"""개발 도구: 테스트 영상에서 프레임을 추출하고 ultralytics predict 로 박스를 그린 이미지를 저장.

출력 (기본 out_dir=_dev/frames, .gitignore 대상 아님 → 커밋하지 말 것):
  {out}/orig/      원본 해상도 프레임(JPEG q95)
  {out}/esp32/     640x480 리사이즈 + 저품질 JPEG 재압축(ESP32 카메라 입력 모사)
  {out}/pred_orig/ 원본 프레임 predict 결과(박스)
  {out}/pred_esp32/ ESP32 모사 프레임 predict 결과
  {out}/detections.json  프레임별 검출 요약

사용:
    python edge/inference/tools/extract_and_predict.py --video <mp4> --fps 2 --max-frames 20
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import cv2


def extract(video: Path, fps: float, max_frames: int, out: Path, esp_size, esp_quality: int):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = src_fps / fps  # 시간 기준 샘플링(소수 step 허용)
    (out / "orig").mkdir(parents=True, exist_ok=True)
    (out / "esp32").mkdir(parents=True, exist_ok=True)
    stem = video.stem
    saved = []
    idx = 0
    next_t = 0.0
    size = "?"
    while len(saved) < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        size = f"{frame.shape[1]}x{frame.shape[0]}"
        if idx + 1e-6 >= next_t:
            next_t += step
            name = f"{stem}_f{idx:06d}.jpg"
            cv2.imwrite(str(out / "orig" / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            small = cv2.resize(frame, esp_size, interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out / "esp32" / name), small, [cv2.IMWRITE_JPEG_QUALITY, esp_quality])
            saved.append(name)
        idx += 1
    cap.release()
    print(f"{video.name}: src_fps={src_fps:.2f} total={total} step={step:.2f} saved={len(saved)} size={size}")
    return saved


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", nargs="+", required=True)
    ap.add_argument("--model", default="edge/inference/models/best.pt")
    ap.add_argument("--out", default="_dev/frames")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--max-frames", type=int, default=20, help="영상당 최대 프레임 수")
    ap.add_argument("--esp-width", type=int, default=640)
    ap.add_argument("--esp-height", type=int, default=480)
    ap.add_argument("--esp-quality", type=int, default=40, help="OpenCV JPEG 품질(0~100, 낮을수록 저화질)")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--device", default=None, help="ultralytics device (예: 0, cpu). 개발 도구만 GPU 허용")
    args = ap.parse_args()

    from ultralytics import YOLO

    out = Path(args.out)
    names = []
    for v in args.video:
        names += extract(Path(v), args.fps, args.max_frames, out,
                         (args.esp_width, args.esp_height), args.esp_quality)

    m = YOLO(args.model)
    summary = {}
    for variant in ("orig", "esp32"):
        pred_dir = out / f"pred_{variant}"
        pred_dir.mkdir(parents=True, exist_ok=True)
        counts = Counter()
        per_frame = {}
        for name in names:
            r = m.predict(str(out / variant / name), conf=args.conf, verbose=False, device=args.device)[0]
            cv2.imwrite(str(pred_dir / name), r.plot())
            dets = [{"cls": m.names[int(c)], "conf": round(float(s), 3),
                     "xyxy": [round(float(x), 1) for x in b]}
                    for c, s, b in zip(r.boxes.cls, r.boxes.conf, r.boxes.xyxy)]
            counts.update(d["cls"] for d in dets)
            per_frame[name] = dets
        summary[variant] = {"class_counts": dict(counts), "frames": per_frame}
        print(f"[{variant}] class counts over {len(names)} frames: {dict(counts)}")
    (out / "detections.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
