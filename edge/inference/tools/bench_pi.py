"""Raspberry Pi 5 실측용 벤치마크: 저장된 JPEG 폴더 → PPEPipeline N회 → 단계별 p50/p95/p99 + RSS 피크 → JSON.

런타임 의존성(numpy, opencv-python-headless, onnxruntime)만 사용한다. ultralytics/torch 를 import 하지 않는다.
RSS: Linux 는 resource.getrusage(ru_maxrss, KiB) + /proc/self/status(VmHWM/VmRSS), psutil 이 있으면 함께 기록.

사용(Pi):
    ORT_INTRA_OP_THREADS=3 python edge/inference/tools/bench_pi.py \
        --images bench_frames/ --model-path /models/ --iterations 300 --out results/bench_t3.json

ESP32 모사 프레임 준비(개발 PC 에서, 이 스크립트는 영상 디코드에 opencv 만 사용):
    python edge/inference/tools/bench_pi.py --export-frames bench_frames/ \
        --video data/test_videos/ppe_safety_test_fixed.mp4 --count 20
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

FRAME_TEMPLATE = {
    "schema_version": "1.0.0", "frame_id": None, "device_id": "cam-bench-01", "boot_id": "be0c0001",
    "frame_seq": 0, "mcu_uptime_ms": 0, "capture_ts_utc": None, "gateway_received_at_utc": None,
    "gateway_clock_synced": True,
    "image": {"encoding": "jpeg", "width": 0, "height": 0, "byte_length": 0, "sha256": "0" * 64,
              "location": {"kind": "IN_MEMORY", "path": None}},
    "edge_detection": None,
    "selection": {"reason": "LATEST_FRAME", "queue_depth_at_enqueue": 0, "dropped_since_last_selected": 0},
}


def export_frames(video: str, out_dir: str, count: int, fps: float, width: int, height: int, quality: int) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step, idx, next_t, n = src_fps / fps, 0, 0.0, 0
    while n < count:
        ok, img = cap.read()
        if not ok:
            break
        if idx + 1e-6 >= next_t:
            next_t += step
            small = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out / f"frame_{n:04d}.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, quality])
            n += 1
        idx += 1
    cap.release()
    print(f"exported {n} frames ({width}x{height}, JPEG q{quality}) to {out}")


def rss_snapshot() -> dict:
    snap = {}
    try:
        import resource  # Linux/macOS
        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        snap["ru_maxrss_bytes"] = maxrss * (1 if sys.platform == "darwin" else 1024)
    except ImportError:
        pass
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            k, _, v = line.partition(":")
            if k in ("VmHWM", "VmRSS"):
                snap[f"{k}_bytes"] = int(v.split()[0]) * 1024
    try:
        import psutil  # 선택
        mi = psutil.Process().memory_info()
        snap["psutil_rss_bytes"] = mi.rss
        if hasattr(mi, "peak_wset"):  # Windows
            snap["psutil_peak_wset_bytes"] = mi.peak_wset
    except ImportError:
        pass
    return snap


def pct(xs, q):
    return round(float(np.percentile(np.asarray(xs, dtype=float), q)), 3) if xs else None


def pi_info() -> dict:
    info = {}
    for p, k in (("/proc/device-tree/model", "model"), ("/sys/class/thermal/thermal_zone0/temp", "cpu_temp_milli_c")):
        try:
            info[k] = Path(p).read_text().strip("\x00\n ")
        except OSError:
            pass
    if shutil.which("vcgencmd"):
        for cmd in ("measure_temp", "get_throttled", "measure_clock arm"):
            try:
                info[f"vcgencmd {cmd}"] = os.popen(f"vcgencmd {cmd}").read().strip()
            except OSError:
                pass
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", help="JPEG 폴더(ESP32 모사 또는 실제 ESP32 프레임)")
    ap.add_argument("--model-path", default=os.environ.get("INFERENCE_MODEL_PATH", "edge/inference/models/"))
    ap.add_argument("--iterations", type=int, default=200, help="측정 프레임 수(폴더를 순환)")
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--no-publish", action="store_true", help="spool 게시 생략(디스크 쓰기 제외)")
    ap.add_argument("--data-root", default=None, help="spool 위치(기본: 임시 폴더, 종료 시 삭제)")
    ap.add_argument("--out", default=None, help="결과 JSON 경로")
    ap.add_argument("--export-frames", metavar="DIR", help="영상에서 ESP32 모사 프레임을 내보내고 종료")
    ap.add_argument("--video", default="data/test_videos/ppe_safety_test_fixed.mp4")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--jpeg-quality", type=int, default=40)
    args = ap.parse_args()

    if args.export_frames:
        export_frames(args.video, args.export_frames, args.count, args.fps, args.width, args.height, args.jpeg_quality)
        return
    if not args.images:
        raise SystemExit("--images 또는 --export-frames 필요")

    files = sorted(Path(args.images).glob("*.jpg"))
    if not files:
        raise SystemExit(f"no .jpg in {args.images}")
    jpegs = [f.read_bytes() for f in files]
    rss_before = rss_snapshot()

    tmp_root = None
    data_root = args.data_root
    if data_root is None:
        tmp_root = tempfile.mkdtemp(prefix="ppe_bench_")
        data_root = tmp_root

    from edge.ppe_decision.pipeline import PPEPipeline
    t_load0 = time.perf_counter()
    env = {**os.environ, "INFERENCE_MODEL_PATH": args.model_path, "EVENT_DATA_ROOT": data_root}
    pipe = PPEPipeline.from_env(env, publish=not args.no_publish)
    load_ms = (time.perf_counter() - t_load0) * 1000
    rss_loaded = rss_snapshot()

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stages = {k: [] for k in ("decode_ms", "preprocess_ms", "inference_ms", "postprocess_ms", "decision_ms", "total_ms")}
    n_events = 0
    total = args.warmup + args.iterations
    wall0 = time.perf_counter()
    for i in range(total):
        jpeg = jpegs[i % len(jpegs)]
        ef = copy.deepcopy(FRAME_TEMPLATE)
        t = t0 + timedelta(milliseconds=500 * i)
        ts = t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"
        ef.update({"frame_id": f"00000000-0000-7000-8000-{i:012x}", "frame_seq": i, "mcu_uptime_ms": 500 * i,
                   "capture_ts_utc": ts, "gateway_received_at_utc": ts})
        ef["image"].update({"byte_length": len(jpeg), "sha256": hashlib.sha256(jpeg).hexdigest(),
                            "width": args.width, "height": args.height})
        s = time.perf_counter()
        evs = pipe.process(ef, jpeg)
        e = time.perf_counter()
        if i < args.warmup:
            continue
        tm = pipe.last_inference_result["timing"]
        total_ms = (e - s) * 1000
        for k in ("decode_ms", "preprocess_ms", "inference_ms", "postprocess_ms"):
            stages[k].append(tm[k])
        stages["decision_ms"].append(max(0.0, total_ms - sum(tm.values())))
        stages["total_ms"].append(total_ms)
        n_events += len(evs)
    wall = time.perf_counter() - wall0
    rss_after = rss_snapshot()

    import onnxruntime as ort
    result = {
        "note": "Pi 5 에서 실행한 경우에만 Pi 결과로 보고. 그 외 플랫폼은 참고값.",
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": {"platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor(),
                 "python": platform.python_version(), "cpu_count": os.cpu_count(), **pi_info()},
        "runtime": {"onnxruntime": ort.__version__, "opencv": cv2.__version__, "numpy": np.__version__,
                    "providers": pipe.engine.detector.session.get_providers()},
        "config": {"model_file": pipe.engine.detector.model_file, "model_sha256": pipe.engine.detector.sha256,
                   "input": [pipe.engine.detector.input_w, pipe.engine.detector.input_h],
                   "intra_op_threads": pipe.engine.cfg.intra_op_threads,
                   "inter_op_threads": pipe.engine.cfg.inter_op_threads,
                   "publish": not args.no_publish},
        "input": {"images": str(args.images), "n_files": len(files), "iterations": args.iterations,
                  "warmup": args.warmup},
        "model_load_ms": round(load_ms, 1),
        "throughput_fps": round(total / wall, 2),
        "events": n_events,
        "timing_ms": {k: {"p50": pct(v, 50), "p95": pct(v, 95), "p99": pct(v, 99), "max": pct(v, 100)}
                      for k, v in stages.items()},
        "memory": {"before_load": rss_before, "after_load": rss_loaded, "after_run": rss_after},
    }
    leaked = sorted(m for m in ("torch", "ultralytics") if m in sys.modules)
    result["dev_modules_loaded"] = leaked  # 반드시 [] 이어야 함(런타임 의존성만)
    if tmp_root:
        shutil.rmtree(tmp_root, ignore_errors=True)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
