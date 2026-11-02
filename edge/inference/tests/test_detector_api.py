"""detector 후처리/설정/ESP32 사람 박스 + (모델 파일이 있으면) infer() 계약 검증."""
import copy
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from edge.inference import contract_check as cc
from edge.inference.api import InferenceEngine, InvalidImageError, esp32_persons
from edge.inference.config import InferenceConfig
from edge.inference.detector import LetterboxMeta, PPEDetector, letterbox, nms_per_class

ROOT = Path(__file__).resolve().parents[3]
MODEL = ROOT / "edge/inference/models/ppe_yolov8n_640.onnx"
def _dev_frames_match(frames):
    """dev 프레임이 B 원본(tests/dev_frames.sha256)과 같은지. 다르면 (False, 이유)."""
    manifest = {}
    for line in (ROOT / "edge/inference/tests/dev_frames.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, name = line.split(maxsplit=1)
            manifest[name.strip()] = digest
    for f in frames:
        if manifest.get(f.name) != hashlib.sha256(f.read_bytes()).hexdigest():
            return False, f"dev frame {f.name} differs from B's original (dev_frames.sha256)"
    return True, ""


NAMES = {0: "Hardhat", 1: "Mask", 2: "NO-Hardhat", 3: "NO-Mask", 4: "NO-Safety Vest", 5: "Person",
         6: "Safety Cone", 7: "Safety Vest", 8: "machinery", 9: "vehicle"}


def fake_detector(names=NAMES):
    d = object.__new__(PPEDetector)
    d.names = names
    roles = {"person": "Person", "helmet": "Hardhat", "no_helmet": "NO-Hardhat", "vest": "Safety Vest",
             "no_vest": "NO-Safety Vest"}
    by = {v.lower(): k for k, v in names.items()}
    d.role_to_id = {r: by[n.lower()] for r, n in roles.items() if n.lower() in by}
    d.id_to_role = {v: k for k, v in d.role_to_id.items()}
    d.det_min_confidence, d.nms_iou, d.max_det = 0.25, 0.7, 300
    d.input_w = d.input_h = 640
    return d


def raw_from(boxes):
    """boxes: [(cx, cy, w, h, cls, score)] → (1, 4+nc, N)"""
    nc = len(NAMES)
    raw = np.zeros((1, 4 + nc, len(boxes)), dtype=np.float32)
    for i, (cx, cy, w, h, c, s) in enumerate(boxes):
        raw[0, :4, i] = (cx, cy, w, h)
        raw[0, 4 + c, i] = s
    return raw


def test_letterbox_matches_ultralytics_geometry():
    img = np.zeros((480, 640, 3), np.uint8)
    out, meta = letterbox(img, 640, 640)
    assert out.shape == (640, 640, 3) and (meta.pad_top, meta.pad_left, meta.ratio) == (80, 0, 1.0)
    assert out[0, 0, 0] == 114 and out[100, 0, 0] == 0
    out, meta = letterbox(np.zeros((720, 1280, 3), np.uint8), 640, 640)
    assert (meta.pad_top, meta.pad_left, meta.ratio) == (140, 0, 0.5)


def test_per_class_nms_keeps_overlapping_worn_and_missing_boxes():
    d = fake_detector()
    raw = raw_from([(320, 200, 60, 60, 0, 0.8),   # Hardhat
                    (320, 200, 60, 60, 2, 0.7),   # NO-Hardhat — 같은 위치, 다른 클래스 → 유지
                    (322, 201, 60, 60, 0, 0.6)])  # Hardhat 중복 → NMS 제거
    dets = d.postprocess(raw, LetterboxMeta(1.0, 0, 80, 640, 480))
    names = sorted(x.class_name for x in dets)
    assert names == ["Hardhat", "NO-Hardhat"]
    hh = [x for x in dets if x.class_name == "Hardhat"][0]
    assert hh.xyxy == pytest.approx((290, 90, 350, 150))  # 레터박스 역변환(pad_top 80)


def test_unused_classes_are_dropped_by_name_mapping():
    d = fake_detector()
    raw = raw_from([(100, 300, 40, 40, 1, 0.9), (200, 300, 40, 40, 6, 0.9), (300, 300, 40, 40, 9, 0.9),
                    (400, 300, 40, 80, 5, 0.9)])
    dets = d.postprocess(raw, LetterboxMeta(1.0, 0, 80, 640, 480))
    assert [x.class_name for x in dets] == ["Person"] and dets[0].role == "person"


def test_class_ids_come_from_names_not_hardcoded():
    shuffled = {0: "Person", 1: "Safety Vest", 2: "Hardhat", 3: "NO-Hardhat", 4: "NO-Safety Vest"}
    d = fake_detector(shuffled)
    assert d.role_to_id == {"person": 0, "vest": 1, "helmet": 2, "no_helmet": 3, "no_vest": 4}


def test_nms_basic():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [0, 0, 10, 10]], float)
    keep = nms_per_class(boxes, np.array([0.9, 0.8, 0.7]), np.array([0, 0, 1]), 0.5)
    assert sorted(keep.tolist()) == [0, 2]


def test_config_env_blank_and_split_thresholds():
    cfg = InferenceConfig.from_env({"INFERENCE_PERSON_MIN_CONFIDENCE": "", "INFERENCE_PPE_MIN_CONFIDENCE": "0.4"})
    assert cfg.person_min_confidence == 0.5
    assert cfg.ppe_worn_min_confidence == 0.4 and cfg.ppe_missing_min_confidence == 0.4
    cfg = InferenceConfig.from_env({"INFERENCE_PPE_MIN_CONFIDENCE": "0.4",
                                    "INFERENCE_PPE_MISSING_MIN_CONFIDENCE": "0.6",
                                    "INFERENCE_PERSON_SOURCE": "esp32"})
    assert cfg.ppe_worn_min_confidence == 0.4 and cfg.ppe_missing_min_confidence == 0.6
    assert cfg.person_source == "esp32"
    with pytest.raises(ValueError):
        InferenceConfig.from_env({"INFERENCE_PERSON_SOURCE": "camera"})


def test_esp32_persons_defensive_reads():
    assert esp32_persons({"edge_detection": None}, 640, 480) == []
    assert esp32_persons({}, 640, 480) == []
    assert esp32_persons({"edge_detection": {"enabled": True, "person_detected": True}}, 640, 480) == []
    ed = {"person_score": 0.7, "persons": [
        {"bbox": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.3, "y_max": 0.9}, "score": 0.8},
        {"bbox": {"x_min": 0.5, "y_min": 0.1, "x_max": 0.6, "y_max": 0.9}},           # score 없음 → person_score
        {"bbox": {"x_min": 0.5, "y_min": 0.5, "x_max": 0.5, "y_max": 0.5}, "score": 0.9},  # 퇴화(FOMO 중심점)
        {"bbox": {"x_min": "bad"}}, "junk"]}
    ps = esp32_persons({"edge_detection": ed}, 640, 480)
    assert len(ps) == 2
    assert ps[0].xyxy == pytest.approx((64, 48, 192, 432)) and ps[0].score == 0.8
    assert ps[1].score == 0.7


# ---------------------------------------------------------------- 모델 필요
needs_model = pytest.mark.skipif(not MODEL.exists(), reason="ONNX model not present (not committed)")


@pytest.fixture(scope="module")
def engine():
    return InferenceEngine(InferenceConfig.from_env({"INFERENCE_MODEL_PATH": str(MODEL)}))


def _frame(engine_img=None):
    tmpl = json.loads((ROOT / "contracts/examples/edge-frame.example.json").read_text(encoding="utf-8"))
    img = engine_img if engine_img is not None else np.full((480, 640, 3), 127, np.uint8)
    cv2.rectangle(img, (200, 100), (300, 400), (40, 40, 40), -1)
    jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 40])[1].tobytes()
    ef = copy.deepcopy(tmpl)
    ef["image"].update({"width": 640, "height": 480, "byte_length": len(jpeg),
                        "sha256": hashlib.sha256(jpeg).hexdigest()})
    return ef, jpeg


@needs_model
def test_infer_with_edge_detection_null_passes_contract(engine):
    ef, jpeg = _frame()
    ef["edge_detection"] = None
    assert cc.errors("edge-frame", ef) == []
    r = engine.infer(ef, jpeg)
    assert cc.errors("inference-result", r) == []
    assert r["image_size"] == {"width": 640, "height": 480}
    assert all(r["timing"][k] > 0 for k in ("decode_ms", "preprocess_ms", "inference_ms", "postprocess_ms"))
    assert r["model"]["runtime"] == "onnxruntime" and len(r["model"]["sha256"]) == 64


@needs_model
def test_infer_on_real_frame_if_available(engine):
    frames = sorted((ROOT / "edge/inference/_dev/frames/esp32").glob("*.jpg"))
    if not frames:
        pytest.skip("dev frames not extracted")
    ok, why = _dev_frames_match(frames[:1])
    if not ok:
        pytest.skip(why)
    jpeg = frames[0].read_bytes()
    ef, _ = _frame()
    ef["image"].update({"byte_length": len(jpeg), "sha256": hashlib.sha256(jpeg).hexdigest()})
    r = engine.infer(ef, jpeg)
    assert cc.errors("inference-result", r) == []
    statuses = sorted((p["helmet"]["status"], p["vest"]["status"]) for p in r["persons"]
                      if p["helmet"]["status"] != "UNKNOWN")
    assert statuses == [("MISSING", "WORN"), ("WORN", "MISSING"), ("WORN", "WORN")]


@needs_model
def test_invalid_jpeg_raises(engine):
    ef, _ = _frame()
    with pytest.raises(InvalidImageError):
        engine.infer(ef, b"not a jpeg")


@needs_model
def test_size_mismatch_logs_warning(engine, caplog):
    ef, jpeg = _frame()
    ef["image"]["width"] = 800
    r = engine.infer(ef, jpeg)
    assert r["image_size"]["width"] == 640
    assert any("size mismatch" in m for m in caplog.messages)
