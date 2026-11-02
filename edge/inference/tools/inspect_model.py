"""개발 도구: YOLO .pt 모델의 클래스/입력 크기/출력 텐서 형태/메타데이터 확인.

ultralytics 는 개발 도구에서만 사용한다(런타임에서 import 금지).

사용:
    python edge/inference/tools/inspect_model.py --model edge/inference/models/best.pt
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="edge/inference/models/best.pt")
    ap.add_argument("--imgsz", type=int, default=None, help="출력 형태 확인용 입력 크기(기본: 학습 imgsz)")
    args = ap.parse_args()

    import torch
    from ultralytics import YOLO

    path = Path(args.model)
    m = YOLO(str(path))
    ckpt = torch.load(str(path), map_location="cpu", weights_only=False)
    train_args = ckpt.get("train_args", {}) or {}

    names = m.names
    imgsz = args.imgsz or int(train_args.get("imgsz", 640))
    info = {
        "path": str(path),
        "sha256": sha256_file(path),
        "byte_length": path.stat().st_size,
        "task": m.task,
        "nc": len(names),
        "names": names,
        "train_imgsz": train_args.get("imgsz"),
        "train_data": train_args.get("data"),
        "train_epochs": train_args.get("epochs"),
        "base_model": train_args.get("model"),
        "ckpt_version": ckpt.get("version"),
        "ckpt_date": ckpt.get("date"),
        "ckpt_license": ckpt.get("license"),
        "params": sum(p.numel() for p in m.model.parameters()),
    }

    net = m.model.eval().float()
    with torch.no_grad():
        out = net(torch.zeros(1, 3, imgsz, imgsz))
    first = out[0] if isinstance(out, (list, tuple)) else out
    info["raw_output_shape"] = list(first.shape)
    info["raw_output_note"] = "(1, 4+nc, N): cx,cy,w,h(입력 픽셀) + 클래스별 점수(sigmoid), NMS 전"
    print(json.dumps(info, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
