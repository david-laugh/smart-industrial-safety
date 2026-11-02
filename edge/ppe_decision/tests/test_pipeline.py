"""PPEPipeline 통합(모델 파일과 추출 프레임이 있을 때만)."""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from edge.inference import contract_check as cc
from edge.ppe_decision.decision import format_utc
from edge.ppe_decision.pipeline import PPEPipeline

ROOT = Path(__file__).resolve().parents[3]
MODEL = ROOT / "edge/inference/models/ppe_yolov8n_640.onnx"
FRAMES = sorted((ROOT / "edge/inference/_dev/frames/esp32").glob("*.jpg"))

pytestmark = pytest.mark.skipif(not MODEL.exists() or not FRAMES, reason="model or dev frames not present")


def test_pipeline_process_publishes_one_event_per_violator(tmp_path):
    pipe = PPEPipeline.from_env({"INFERENCE_MODEL_PATH": str(MODEL), "EVENT_DATA_ROOT": str(tmp_path)})
    tmpl = json.loads((ROOT / "contracts/examples/edge-frame.example.json").read_text(encoding="utf-8"))
    t0 = datetime(2026, 11, 2, 1, 0, 0, tzinfo=timezone.utc)
    all_events = []
    for i, f in enumerate(FRAMES[:4]):
        jpeg = f.read_bytes()
        ef = copy.deepcopy(tmpl)
        t = t0 + timedelta(seconds=i * 0.5)
        ef.update({"frame_id": f"019a4a3c-0000-7000-8000-{i:012x}", "frame_seq": i,
                   "capture_ts_utc": format_utc(t), "gateway_received_at_utc": format_utc(t),
                   "edge_detection": None})
        ef["image"].update({"byte_length": len(jpeg), "sha256": hashlib.sha256(jpeg).hexdigest()})
        evs = pipe.process(ef, jpeg)
        assert cc.errors("inference-result", pipe.last_inference_result) == []
        for e in evs:
            assert cc.errors("ppe-event", e) == []
        all_events += evs
    types = sorted(e["violations"][0]["violation_types"][0] for e in all_events)
    assert types == ["HELMET_MISSING", "VEST_MISSING"]
    assert len(list((tmp_path / "spool" / "incoming").glob("*.json"))) == 2
