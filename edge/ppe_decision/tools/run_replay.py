"""PC 단독 E2E 리플레이: 테스트 영상 → edge-frame(템플릿 기반) → infer() → PPEDecision → spool.

- contracts/examples/edge-frame.example.json 을 템플릿으로 frame_id(UUIDv7), frame_seq, mcu_uptime_ms,
  capture_ts_utc, gateway_received_at_utc, image.{width,height,byte_length,sha256} 를 실제 값으로 채운다.
- 기본 입력은 ESP32 모사(640x480 리사이즈 + JPEG 품질 40). --no-esp32 로 원본 해상도 사용.
- 각 edge-frame / inference-result / ppe-event 를 contracts 스키마로 100% 검증(실패 시 중단).
- 단계별 timing 통계(p50/p95) 출력. 개발 PC 측정값은 **x86 참고값**이며 Pi 5 결과가 아니다.

사용:
    python edge/ppe_decision/tools/run_replay.py --video data/test_videos/ppe_safety_test_fixed.mp4 \
        --model-path edge/inference/models/ --data-root edge/ppe_decision/_data --loops 4
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from edge.inference import contract_check as cc  # noqa: E402
from edge.inference.api import InferenceEngine  # noqa: E402
from edge.inference.config import InferenceConfig  # noqa: E402
from edge.ppe_decision.config import DecisionConfig  # noqa: E402
from edge.ppe_decision.decision import PPEDecision, format_utc  # noqa: E402
from edge.ppe_decision.spool import SpoolPublisher  # noqa: E402
from edge.ppe_decision.uuid7 import uuid7  # noqa: E402


def frames(video: str, fps: float, loops: int, esp32: bool, quality: int, size):
    """(t_offset_s, jpeg_bytes) 생성. loops 회 반복 시 시간은 이어진다."""
    for loop in range(loops):
        cap = cv2.VideoCapture(video)
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        dur = total / src_fps
        step = src_fps / fps
        idx, next_t = 0, 0.0
        while True:
            ok, img = cap.read()
            if not ok:
                break
            if idx + 1e-6 >= next_t:
                next_t += step
                if esp32:
                    img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality if esp32 else 90])
                yield loop * dur + idx / src_fps, buf.tobytes()
            idx += 1
        cap.release()


def pct(xs, q):
    return float(np.percentile(np.asarray(xs, dtype=float), q)) if xs else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="data/test_videos/ppe_safety_test_fixed.mp4")
    ap.add_argument("--template", default=str(ROOT / "contracts/examples/edge-frame.example.json"))
    ap.add_argument("--model-path", default="edge/inference/models/")
    ap.add_argument("--data-root", default="edge/ppe_decision/_data")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--loops", type=int, default=1, help="영상 반복 횟수(시간 연속). dedup 창 검증용")
    ap.add_argument("--no-esp32", action="store_true")
    ap.add_argument("--jpeg-quality", type=int, default=40)
    ap.add_argument("--edge-detection-null", action="store_true", help="edge_detection=null 로 입력")
    ap.add_argument("--start", default="2026-11-02T01:00:00.000Z", help="첫 프레임 capture 시각(UTC)")
    ap.add_argument("--clock-unsynced", action="store_true", help="capture_ts_utc=null (GATEWAY_RECEIVED 경로)")
    ap.add_argument("--report", default=None, help="timing/결과 요약 JSON 경로")
    args = ap.parse_args()

    tmpl = json.loads(Path(args.template).read_text(encoding="utf-8"))
    icfg = InferenceConfig.from_env({**dict(__import__("os").environ), "INFERENCE_MODEL_PATH": args.model_path})
    engine = InferenceEngine(icfg)
    dcfg = DecisionConfig.from_env({**dict(__import__("os").environ), "EVENT_DATA_ROOT": args.data_root})
    decision = PPEDecision(dcfg, publisher=SpoolPublisher(dcfg.data_root))

    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    stages = {k: [] for k in ("decode_ms", "preprocess_ms", "inference_ms", "postprocess_ms",
                              "infer_total_ms", "decision_ms", "validate_ms")}
    events = []
    n = 0
    status_counts = {}
    for seq, (t_off, jpeg) in enumerate(frames(args.video, args.fps, args.loops, not args.no_esp32,
                                               args.jpeg_quality, (640, 480))):
        cap_t = start + timedelta(seconds=t_off)
        gw_t = cap_t + timedelta(milliseconds=60)
        ef = copy.deepcopy(tmpl)
        ef.update({
            "frame_id": uuid7(int(gw_t.timestamp() * 1000)),
            "frame_seq": seq,
            "mcu_uptime_ms": 3_600_000 + int(t_off * 1000),
            "capture_ts_utc": None if args.clock_unsynced else format_utc(cap_t),
            "gateway_received_at_utc": format_utc(gw_t),
        })
        img_full = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        ef["image"].update({"width": img_full.shape[1], "height": img_full.shape[0],
                            "byte_length": len(jpeg), "sha256": hashlib.sha256(jpeg).hexdigest()})
        if args.edge_detection_null:
            ef["edge_detection"] = None
        cc.validate("edge-frame", ef)

        t0 = time.perf_counter()
        res = engine.infer(ef, jpeg)
        t1 = time.perf_counter()
        ev = decision.update(ef, res, jpeg)
        t2 = time.perf_counter()
        cc.validate("inference-result", res)
        if ev is not None:
            cc.validate("ppe-event", ev)
            events.append(ev)
        t3 = time.perf_counter()

        for k in ("decode_ms", "preprocess_ms", "inference_ms", "postprocess_ms"):
            stages[k].append(res["timing"][k])
        stages["infer_total_ms"].append((t1 - t0) * 1000)
        stages["decision_ms"].append((t2 - t1) * 1000)  # spool 게시(fsync) 포함
        stages["validate_ms"].append((t3 - t2) * 1000)
        for p in res["persons"]:
            key = f"{p['helmet']['status']}/{p['vest']['status']}"
            status_counts[key] = status_counts.get(key, 0) + 1
        n += 1
        if ev is not None:
            print(f"[frame {seq:3d} t={t_off:6.2f}s] EVENT {ev['event_id']} "
                  f"{[(v['person_index'], v['violation_types']) for v in ev['violations']]} key={ev['decision']['dedup_key']}")

    warm = 2  # 처음 2프레임(워밍업)은 통계에서 제외
    timing = {k: {"p50": round(pct(v[warm:], 50), 2), "p95": round(pct(v[warm:], 95), 2), "n": len(v[warm:])}
              for k, v in stages.items()}
    summary = {
        "note": "x86 참고값(개발 PC). Raspberry Pi 5 측정 결과가 아님.",
        "host": {"platform": platform.platform(), "processor": platform.processor(),
                 "python": platform.python_version(), "ort_intra_op_threads": icfg.intra_op_threads},
        "input": {"video": args.video, "fps": args.fps, "loops": args.loops, "esp32_sim": not args.no_esp32,
                  "jpeg_quality": args.jpeg_quality if not args.no_esp32 else 90},
        "frames": n, "events": len(events),
        "event_ids": [e["event_id"] for e in events],
        "person_status_counts": status_counts,
        "timing_ms": timing,
        "data_root": str(Path(args.data_root).resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.report:
        Path(args.report).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
