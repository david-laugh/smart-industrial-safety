"""개발 도구: ultralytics .pt → ONNX 변환(정적 입력, NMS 미포함) + sha256 기록.

사용:
    python edge/inference/tools/export_onnx.py --model edge/inference/models/best.pt --imgsz 640 416 320

출력: {out_dir}/ppe_yolov8n_{imgsz}.onnx, {out_dir}/onnx_manifest.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from edge.inference.detector import sha256_file  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="edge/inference/models/best.pt")
    ap.add_argument("--imgsz", type=int, nargs="+", default=[640, 416, 320])
    ap.add_argument("--opset", type=int, default=12)
    ap.add_argument("--out-dir", default="edge/inference/models")
    args = ap.parse_args()

    import onnx
    from ultralytics import YOLO

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"source_pt": str(args.model), "source_pt_sha256": sha256_file(args.model), "models": []}
    for sz in args.imgsz:
        m = YOLO(args.model)
        produced = Path(m.export(format="onnx", imgsz=sz, opset=args.opset, simplify=True,
                                 dynamic=False, half=False, nms=False, device="cpu"))
        dst = out_dir / f"ppe_yolov8n_{sz}.onnx"
        shutil.move(str(produced), dst)
        om = onnx.load(str(dst))
        meta = {p.key: p.value for p in om.metadata_props}
        entry = {
            "file": dst.name,
            "imgsz": sz,
            "opset": args.opset,
            "sha256": sha256_file(str(dst)),
            "byte_length": dst.stat().st_size,
            "input": [(i.name, [d.dim_value for d in i.type.tensor_type.shape.dim]) for i in om.graph.input],
            "output": [(o.name, [d.dim_value for d in o.type.tensor_type.shape.dim]) for o in om.graph.output],
            "metadata_names": meta.get("names"),
        }
        manifest["models"].append(entry)
        print(json.dumps(entry, ensure_ascii=False))
    (out_dir / "onnx_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
