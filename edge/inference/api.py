"""추론 진입점: infer(edge_frame: dict, jpeg: bytes) -> inference-result dict.

- edge_frame: contracts/edge-frame.schema.json 인스턴스(검증은 Gateway 책임, 여기서는 필요한 필드만 읽음)
- 반환: contracts/inference-result.schema.json 인스턴스
- 사람 박스 출처(INFERENCE_PERSON_SOURCE):
    model : 모델 Person 클래스
    esp32 : edge_frame["edge_detection"]["persons"] (스키마 1.1.0 제안 필드, 없으면 persons=[])
    auto  : 모델에 Person 클래스가 있으면 model, 없으면 esp32
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import onnxruntime as ort

from .association import AssocParams, PersonBox, PPEBox, associate
from .config import InferenceConfig
from .detector import PPEDetector

log = logging.getLogger(__name__)

MAX_PERSONS = 64  # inference-result.schema.json persons.maxItems


class InvalidImageError(ValueError):
    """JPEG 디코드 실패."""


class InferenceEngine:
    """프로세스 수명 동안 1개만 만들어 재사용한다(InferenceSession 1개)."""

    def __init__(self, cfg: Optional[InferenceConfig] = None, detector: Optional[PPEDetector] = None) -> None:
        self.cfg = cfg or InferenceConfig.from_env()
        self.detector = detector or PPEDetector(
            self.cfg.resolved_model_file(),
            self.cfg.class_roles,
            intra_op_threads=self.cfg.intra_op_threads,
            inter_op_threads=self.cfg.inter_op_threads,
            det_min_confidence=self.cfg.det_min_confidence,
            nms_iou=self.cfg.nms_iou,
            max_det=self.cfg.max_det,
            expected_sha256=self.cfg.model_sha256,
        )
        self.params = AssocParams.from_config(self.cfg)
        src = self.cfg.person_source
        if src == "auto":
            src = "model" if self.detector.has_person_class else "esp32"
        elif src == "model" and not self.detector.has_person_class:
            raise ValueError("INFERENCE_PERSON_SOURCE=model but model has no person class")
        self.person_source = src
        self.model_info = {
            "name": self.cfg.model_name[:64],
            "version": self.cfg.model_version[:32],
            "sha256": self.detector.sha256,
            "input_width": self.detector.input_w,
            "input_height": self.detector.input_h,
            "runtime": "onnxruntime",
            "runtime_version": ort.__version__[:32],
        }
        log.info("inference engine ready: model=%s sha256=%s input=%dx%d person_source=%s threads=%d/%d",
                 self.detector.model_file, self.detector.sha256, self.detector.input_w, self.detector.input_h,
                 self.person_source, self.cfg.intra_op_threads, self.cfg.inter_op_threads)

    # ------------------------------------------------------------------
    def infer(self, edge_frame: Dict[str, Any], jpeg: bytes) -> Dict[str, Any]:
        t0 = time.perf_counter()
        img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        t1 = time.perf_counter()
        if img is None:
            raise InvalidImageError(f"JPEG decode failed (frame_id={edge_frame.get('frame_id')})")
        h, w = img.shape[:2]
        declared = edge_frame.get("image") or {}
        if (declared.get("width"), declared.get("height")) != (w, h):
            log.warning("image size mismatch frame_id=%s declared=%sx%s decoded=%dx%d",
                        edge_frame.get("frame_id"), declared.get("width"), declared.get("height"), w, h)

        dets, t = self.detector.detect(img)
        t2 = time.perf_counter()
        if self.person_source == "model":
            persons = [PersonBox(d.xyxy, d.score) for d in dets if d.role == "person"]
        else:
            persons = esp32_persons(edge_frame, w, h)
        ppe = [PPEBox(d.role, d.xyxy, d.score) for d in dets if d.role in ("helmet", "no_helmet", "vest", "no_vest")]
        if len(persons) > MAX_PERSONS:
            persons = sorted(persons, key=lambda p: -p.score)[:MAX_PERSONS]
        persons.sort(key=lambda p: (p.xyxy[0], p.xyxy[1], -p.score))  # 결정적 순서: 왼쪽→오른쪽
        person_results = associate(persons, ppe, w, h, self.params)
        t3 = time.perf_counter()

        return {
            "schema_version": self.cfg.schema_version,
            "frame_id": edge_frame["frame_id"],
            "device_id": edge_frame["device_id"],
            "image_size": {"width": int(w), "height": int(h)},
            "model": dict(self.model_info),
            "timing": {
                "decode_ms": _ms(t1 - t0),
                "preprocess_ms": round(t["preprocess_ms"], 3),
                "inference_ms": round(t["inference_ms"], 3),
                # 검출 후처리(NMS, 좌표 역변환) + 사람-PPE 연관
                "postprocess_ms": round(t["postprocess_ms"] + (t3 - t2) * 1000.0, 3),
            },
            "persons": person_results,
        }


def esp32_persons(edge_frame: Dict[str, Any], img_w: int, img_h: int) -> List[PersonBox]:
    """edge_detection.persons: [{bbox: bboxNormalized, score}] (스키마 1.1.0 제안, 선택 필드)를 방어적으로 읽는다.

    edge_detection 이 null 이거나 persons 가 없으면 []. 점수가 없으면 edge_detection.person_score,
    그것도 없으면 0.0(→ 판정 불가 UNKNOWN). 퇴화 박스(FOMO 중심점 등, 폭/높이 0)는 버린다.
    """
    ed = edge_frame.get("edge_detection") or {}
    if not isinstance(ed, dict):
        return []
    items = ed.get("persons") or []
    if not isinstance(items, list):
        return []
    default_score = ed.get("person_score")
    out: List[PersonBox] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        bb = it.get("bbox") or {}
        try:
            x1, y1 = float(bb["x_min"]), float(bb["y_min"])
            x2, y2 = float(bb["x_max"]), float(bb["y_max"])
        except (KeyError, TypeError, ValueError):
            continue
        x1, x2 = sorted((min(1.0, max(0.0, x1)), min(1.0, max(0.0, x2))))
        y1, y2 = sorted((min(1.0, max(0.0, y1)), min(1.0, max(0.0, y2))))
        if x2 <= x1 or y2 <= y1:
            continue
        score = it.get("score", default_score)
        try:
            score = min(1.0, max(0.0, float(score))) if score is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
        out.append(PersonBox((x1 * img_w, y1 * img_h, x2 * img_w, y2 * img_h), score))
    return out


def _ms(sec: float) -> float:
    return round(sec * 1000.0, 3)


_engine: Optional[InferenceEngine] = None
_lock = threading.Lock()


def get_engine() -> InferenceEngine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = InferenceEngine()
        return _engine


def infer(edge_frame: Dict[str, Any], jpeg: bytes) -> Dict[str, Any]:
    """모듈 수준 진입점. 첫 호출 시 환경변수로 엔진(세션 1개)을 만들고 이후 재사용한다."""
    return get_engine().infer(edge_frame, jpeg)
