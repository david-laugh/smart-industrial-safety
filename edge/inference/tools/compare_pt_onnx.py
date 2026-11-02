"""개발 도구: PyTorch(ultralytics) vs ONNX Runtime(CPU, 런타임 PPEDetector) 동등성 비교.

1) raw: 같은 입력 텐서(PPEDetector.preprocess)에 대한 PyTorch/ORT 원시 출력 최대 절대 차이
2) e2e: ultralytics predict(pt, rect=False, conf/iou 동일) vs PPEDetector.detect 최종 박스·점수
3) dist: 역할 클래스(Person/Hardhat/NO-Hardhat/Safety Vest/NO-Safety Vest) 점수 분포(min/p50/max)
4) size: 640 대비 416/320 ONNX 검출 차이(클래스별 개수, 미매칭, NO-Hardhat 점수, 작은 사람)
5) small: ESP32 모사 프레임의 사람 박스 높이 분포와 작은 사람(원경) 사례

사용:
    python edge/inference/tools/compare_pt_onnx.py --frames edge/inference/_dev/frames
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from edge.inference.config import DEFAULT_CLASS_ROLES  # noqa: E402
from edge.inference.detector import PPEDetector  # noqa: E402

ROLE_NAMES = list(DEFAULT_CLASS_ROLES.values())


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def match(ref, cand, thr=0.5):
    """ref/cand: [(cls_name, score, xyxy)]. 같은 클래스 내 IoU 탐욕 매칭."""
    pairs, used = [], set()
    for i, (c, s, b) in sorted(enumerate(ref), key=lambda t: -t[1][1]):
        best, bj = thr, None
        for j, (c2, s2, b2) in enumerate(cand):
            if j in used or c2 != c:
                continue
            v = iou(b, b2)
            if v >= best:
                best, bj = v, j
        if bj is not None:
            used.add(bj)
            pairs.append((i, bj, best))
    un_ref = [i for i in range(len(ref)) if i not in {p[0] for p in pairs}]
    un_cand = [j for j in range(len(cand)) if j not in used]
    return pairs, un_ref, un_cand


def stats(xs):
    if not xs:
        return {"n": 0}
    a = np.asarray(xs, dtype=float)
    return {"n": len(xs), "min": round(float(a.min()), 3), "p50": round(float(np.median(a)), 3),
            "max": round(float(a.max()), 3)}


def to_list(dets):
    return [(d.class_name, d.score, d.xyxy) for d in dets]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default="edge/inference/_dev/frames")
    ap.add_argument("--pt", default="edge/inference/models/best.pt")
    ap.add_argument("--models-dir", default="edge/inference/models")
    ap.add_argument("--sizes", type=int, nargs="+", default=[640, 416, 320])
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.7)
    ap.add_argument("--small-px", type=int, default=150, help="작은 사람 사례로 나열할 박스 높이 상한(px)")
    ap.add_argument("--out", default=None, help="결과 JSON 경로(기본: {frames}/compare_report.json)")
    args = ap.parse_args()

    import torch
    from ultralytics import YOLO

    yolo = YOLO(args.pt)
    torch_net = yolo.model.float().eval()
    all_ids = None
    dets_onnx = {}
    for sz in args.sizes:
        dets_onnx[sz] = PPEDetector(str(Path(args.models_dir) / f"ppe_yolov8n_{sz}.onnx"), DEFAULT_CLASS_ROLES,
                                    det_min_confidence=args.conf, nms_iou=args.iou)
    d640 = dets_onnx[640]
    all_ids = list(d640.names)

    report = {}
    for variant in ("orig", "esp32"):
        files = sorted((Path(args.frames) / variant).glob("*.jpg"))
        raw_box_diff, raw_score_diff = [], []
        e2e = {"matched": 0, "pt_only": 0, "onnx_only": 0, "d_score": [], "d_box_px": [], "iou": []}
        dist = defaultdict(list)
        size_cmp = {sz: {"counts": defaultdict(int), "unmatched_vs640": 0, "extra_vs640": 0,
                         "no_hardhat_d_score": [], "person_heights_missed": []} for sz in args.sizes if sz != 640}
        counts640 = defaultdict(int)
        person_h = []
        small = []
        img_hw = None
        for f in files:
            img = cv2.imread(str(f))
            img_hw = img.shape[:2]
            # 1) raw
            x, meta = d640.preprocess(img)
            ort_raw = d640.run(x)
            with torch.no_grad():
                pt_raw = torch_net(torch.from_numpy(x))
                pt_raw = (pt_raw[0] if isinstance(pt_raw, (list, tuple)) else pt_raw).numpy()
            raw_box_diff.append(float(np.abs(pt_raw[0, :4] - ort_raw[0, :4]).max()))
            raw_score_diff.append(float(np.abs(pt_raw[0, 4:] - ort_raw[0, 4:]).max()))
            # 2) e2e (모든 클래스)
            r = yolo.predict(str(f), imgsz=640, conf=args.conf, iou=args.iou, rect=False, device="cpu",
                             verbose=False)[0]
            ref = [(yolo.names[int(c)], float(s), tuple(float(v) for v in b))
                   for c, s, b in zip(r.boxes.cls, r.boxes.conf, r.boxes.xyxy)]
            o_all, _ = d640.detect(img, keep_class_ids=all_ids)
            cand = to_list(o_all)
            pairs, un_r, un_c = match(ref, cand)
            e2e["matched"] += len(pairs)
            e2e["pt_only"] += len(un_r)
            e2e["onnx_only"] += len(un_c)
            for i, j, v in pairs:
                e2e["d_score"].append(abs(ref[i][1] - cand[j][1]))
                e2e["d_box_px"].append(float(np.abs(np.subtract(ref[i][2], cand[j][2])).max()))
                e2e["iou"].append(v)
            # 3) 역할 클래스 분포(640 ONNX, 런타임 필터)
            o640, _ = d640.detect(img)
            for d in o640:
                dist[d.class_name].append(d.score)
                counts640[d.class_name] += 1
                if d.class_name == "Person":
                    h = d.xyxy[3] - d.xyxy[1]
                    person_h.append(h)
                    if h < args.small_px:
                        inside = [(e.class_name, round(e.score, 3)) for e in o640
                                  if e.class_name != "Person"
                                  and d.xyxy[0] <= (e.xyxy[0] + e.xyxy[2]) / 2 <= d.xyxy[2]
                                  and d.xyxy[1] <= (e.xyxy[1] + e.xyxy[3]) / 2 <= d.xyxy[3]]
                        small.append({"frame": f.name, "score": round(d.score, 3),
                                      "w_px": round(d.xyxy[2] - d.xyxy[0], 1), "h_px": round(h, 1),
                                      "y_max_px": round(d.xyxy[3], 1), "ppe_inside": inside})
            # 4) 416/320 vs 640
            ref640 = to_list(o640)
            for sz, sc in size_cmp.items():
                o, _ = dets_onnx[sz].detect(img)
                lst = to_list(o)
                for d in o:
                    sc["counts"][d.class_name] += 1
                pairs, un_r, un_c = match(ref640, lst)
                sc["unmatched_vs640"] += len(un_r)
                sc["extra_vs640"] += len(un_c)
                for i in un_r:
                    if ref640[i][0] == "Person":
                        sc["person_heights_missed"].append(round(ref640[i][2][3] - ref640[i][2][1], 1))
                for i, j, _ in pairs:
                    if ref640[i][0] == "NO-Hardhat":
                        sc["no_hardhat_d_score"].append(lst[j][1] - ref640[i][1])
                nh640 = [t for t in ref640 if t[0] == "NO-Hardhat"]
                nh = [t for t in lst if t[0] == "NO-Hardhat"]
                sc.setdefault("no_hardhat_scores", []).append(
                    (max([t[1] for t in nh640], default=None), max([t[1] for t in nh], default=None)))

        report[variant] = {
            "frames": len(files),
            "image_hw": img_hw,
            "raw_640": {"max_abs_diff_box_px": round(max(raw_box_diff), 5),
                        "max_abs_diff_score": round(max(raw_score_diff), 6)},
            "e2e_640_pt_vs_onnx": {
                "matched": e2e["matched"], "pt_only": e2e["pt_only"], "onnx_only": e2e["onnx_only"],
                "max_d_score": round(max(e2e["d_score"], default=0), 6),
                "max_d_box_px": round(max(e2e["d_box_px"], default=0), 4),
                "min_iou": round(min(e2e["iou"], default=1), 5),
            },
            "score_dist_640": {n: stats(dist[n]) for n in ROLE_NAMES},
            "person_height_px_640": stats(person_h),
            "small_persons_640": small,
            "size_vs_640": {
                str(sz): {
                    "counts": {n: sc["counts"].get(n, 0) for n in ROLE_NAMES},
                    "counts_640": {n: counts640.get(n, 0) for n in ROLE_NAMES},
                    "unmatched_vs640": sc["unmatched_vs640"],
                    "extra_vs640": sc["extra_vs640"],
                    "person_heights_missed_px": sorted(sc["person_heights_missed"]),
                    "no_hardhat_d_score_vs640": stats(sc["no_hardhat_d_score"]),
                    "no_hardhat_frames_detected": sum(1 for a, b in sc["no_hardhat_scores"] if b is not None),
                    "no_hardhat_frames_detected_640": sum(1 for a, b in sc["no_hardhat_scores"] if a is not None),
                    "no_hardhat_max_score": stats([b for a, b in sc["no_hardhat_scores"] if b is not None]),
                } for sz, sc in size_cmp.items()
            },
        }
    out = Path(args.out) if args.out else Path(args.frames) / "compare_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
