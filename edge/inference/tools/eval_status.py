"""상태 단위 평가: 사람이 라벨링한 CSV(frame, 사람 위치, helmet/vest 정답) vs 파이프라인 출력.

CSV 형식 (UTF-8, 헤더 필수):
    frame,x_center,y_center,helmet,vest,note
    ppe_safety_test_fixed_f000000.jpg,0.20,0.55,WORN,WORN,
  - x_center, y_center: 사람의 대략 중심(0~1, 원본 기준 좌상단 원점)
  - helmet, vest: WORN | MISSING | UNKNOWN (사람이 봐도 판단 불가면 UNKNOWN)

매칭: 예측 사람 중 bbox 가 정답 중심을 포함하는 사람(여러 명이면 bbox 중심이 가장 가까운 사람).
지표(항목별 helmet/vest, 상태별): precision/recall, 혼동행렬. 매칭 실패 정답은 'NO_PERSON' 예측으로 집계.

사용:
    # 1) 템플릿 생성(모델이 찾은 판정 가능 사람 위치로 행을 채우고 라벨 칸은 비워 둠)
    python edge/inference/tools/eval_status.py --images edge/inference/_dev/frames/esp32 --make-template labels.csv
    # 2) helmet/vest 칸을 직접 채운 뒤 평가
    python edge/inference/tools/eval_status.py --images edge/inference/_dev/frames/esp32 --labels labels.csv
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from edge.inference.api import InferenceEngine  # noqa: E402
from edge.inference.config import InferenceConfig  # noqa: E402

STATUSES = ("WORN", "MISSING", "UNKNOWN")


def run_engine(engine, images_dir: Path):
    tmpl = json.loads((ROOT / "contracts/examples/edge-frame.example.json").read_text(encoding="utf-8"))
    out = {}
    for f in sorted(images_dir.glob("*.jpg")):
        jpeg = f.read_bytes()
        ef = copy.deepcopy(tmpl)
        ef["image"].update({"byte_length": len(jpeg), "sha256": hashlib.sha256(jpeg).hexdigest()})
        ef["edge_detection"] = None
        out[f.name] = engine.infer(ef, jpeg)["persons"]
    return out


def match(persons, x, y):
    cands = [p for p in persons
             if p["bbox"]["x_min"] <= x <= p["bbox"]["x_max"] and p["bbox"]["y_min"] <= y <= p["bbox"]["y_max"]]
    if not cands:
        return None
    return min(cands, key=lambda p: ((p["bbox"]["x_min"] + p["bbox"]["x_max"]) / 2 - x) ** 2
               + ((p["bbox"]["y_min"] + p["bbox"]["y_max"]) / 2 - y) ** 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True)
    ap.add_argument("--labels", help="라벨 CSV")
    ap.add_argument("--make-template", help="템플릿 CSV 출력 경로")
    ap.add_argument("--model-path", default="edge/inference/models/")
    ap.add_argument("--out", help="결과 JSON 경로")
    args = ap.parse_args()

    engine = InferenceEngine(InferenceConfig.from_env({**__import__("os").environ,
                                                       "INFERENCE_MODEL_PATH": args.model_path}))
    preds = run_engine(engine, Path(args.images))

    if args.make_template:
        with open(args.make_template, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["frame", "x_center", "y_center", "helmet", "vest", "note"])
            for name, persons in preds.items():
                for p in persons:
                    b = p["bbox"]
                    note = "" if p["helmet"]["confidence"] is not None or p["vest"]["confidence"] is not None \
                        or p["helmet"]["status"] != "UNKNOWN" else "model:UNKNOWN(작음/저신뢰)"
                    w.writerow([name, round((b["x_min"] + b["x_max"]) / 2, 3), round((b["y_min"] + b["y_max"]) / 2, 3),
                                "", "", note])
        print(f"template written: {args.make_template} (helmet/vest 칸을 WORN/MISSING/UNKNOWN 으로 채울 것)")
        return

    if not args.labels:
        raise SystemExit("--labels 또는 --make-template 필요")
    conf = {"helmet": defaultdict(Counter), "vest": defaultdict(Counter)}
    n_rows = skipped = 0
    with open(args.labels, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["frame"] not in preds:
                skipped += 1
                continue
            gt = {k: (row.get(k) or "").strip().upper() for k in ("helmet", "vest")}
            if not all(v in STATUSES for v in gt.values()):
                skipped += 1
                continue
            n_rows += 1
            p = match(preds[row["frame"]], float(row["x_center"]), float(row["y_center"]))
            for item in ("helmet", "vest"):
                conf[item][gt[item]][p[item]["status"] if p else "NO_PERSON"] += 1

    result = {"rows": n_rows, "skipped_rows": skipped, "items": {}}
    for item, cm in conf.items():
        per = {}
        for s in STATUSES:
            tp = cm[s][s]
            pred_s = sum(cm[g][s] for g in cm)
            gt_s = sum(cm[s].values())
            per[s] = {"precision": round(tp / pred_s, 4) if pred_s else None,
                      "recall": round(tp / gt_s, 4) if gt_s else None, "support": gt_s}
        result["items"][item] = {"per_status": per, "confusion(gt→pred)": {g: dict(c) for g, c in cm.items()}}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
