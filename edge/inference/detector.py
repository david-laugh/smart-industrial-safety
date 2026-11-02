"""PPEDetector: 레터박스 전처리 → ONNX Runtime(CPU EP) → 클래스별 NMS → 원본 기준 좌표.

- 런타임 의존성: numpy, opencv(cv2), onnxruntime 만.
- InferenceSession 은 인스턴스 수명 동안 1개만 만들어 재사용한다.
- 전처리는 ultralytics LetterBox(auto=False, center=True, pad=114, INTER_LINEAR)와 같다.
- 후처리는 ultralytics non_max_suppression(multi_label=False, agnostic=False)과 같다:
  앵커별 최고 점수 클래스 1개 → 점수 하한 → **클래스별** NMS.
  (Hardhat 과 NO-Hardhat 처럼 같은 위치의 착용/미착용 박스는 서로 지우지 않는다.
   충돌 해결은 association 단계에서 한다.)
"""
from __future__ import annotations

import ast
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import onnxruntime as ort

log = logging.getLogger(__name__)

PAD_VALUE = 114


@dataclass(frozen=True)
class Detection:
    class_id: int
    class_name: str
    role: Optional[str]          # config.class_roles 의 역할(person/helmet/...) 또는 None
    score: float
    xyxy: Tuple[float, float, float, float]   # 원본 이미지 픽셀 좌표(좌상단 원점)


@dataclass(frozen=True)
class LetterboxMeta:
    ratio: float
    pad_left: int
    pad_top: int
    orig_w: int
    orig_h: int


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def letterbox(img_bgr: np.ndarray, new_w: int, new_h: int) -> Tuple[np.ndarray, LetterboxMeta]:
    """ultralytics LetterBox(auto=False, scaleup=True, center=True) 와 동일."""
    h, w = img_bgr.shape[:2]
    r = min(new_h / h, new_w / w)
    unpad_w, unpad_h = max(round(w * r), 1), max(round(h * r), 1)
    dw, dh = (new_w - unpad_w) / 2, (new_h - unpad_h) / 2
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    if (w, h) != (unpad_w, unpad_h):
        img_bgr = cv2.resize(img_bgr, (unpad_w, unpad_h), interpolation=cv2.INTER_LINEAR)
    out = cv2.copyMakeBorder(img_bgr, top, bottom, left, right, cv2.BORDER_CONSTANT,
                             value=(PAD_VALUE, PAD_VALUE, PAD_VALUE))
    return out, LetterboxMeta(r, left, top, w, h)


def nms_per_class(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray, iou: float) -> np.ndarray:
    """클래스별 greedy NMS. 반환: 유지 인덱스(점수 내림차순)."""
    keep: List[int] = []
    for c in np.unique(classes):
        idx = np.where(classes == c)[0]
        idx = idx[np.argsort(-scores[idx], kind="stable")]
        b = boxes[idx]
        areas = (b[:, 2] - b[:, 0]).clip(0) * (b[:, 3] - b[:, 1]).clip(0)
        alive = np.ones(len(idx), dtype=bool)
        for i in range(len(idx)):
            if not alive[i]:
                continue
            keep.append(int(idx[i]))
            rest = np.where(alive[i + 1:])[0] + i + 1
            if rest.size == 0:
                break
            xx1 = np.maximum(b[i, 0], b[rest, 0])
            yy1 = np.maximum(b[i, 1], b[rest, 1])
            xx2 = np.minimum(b[i, 2], b[rest, 2])
            yy2 = np.minimum(b[i, 3], b[rest, 3])
            inter = (xx2 - xx1).clip(0) * (yy2 - yy1).clip(0)
            ious = inter / (areas[i] + areas[rest] - inter + 1e-9)
            alive[rest[ious > iou]] = False
    keep_arr = np.asarray(keep, dtype=np.int64)
    return keep_arr[np.argsort(-scores[keep_arr], kind="stable")] if keep_arr.size else keep_arr


def parse_names(meta: Dict[str, str]) -> Dict[int, str]:
    raw = meta.get("names")
    if not raw:
        return {}
    names = ast.literal_eval(raw)
    if isinstance(names, (list, tuple)):
        names = dict(enumerate(names))
    return {int(k): str(v) for k, v in names.items()}


class PPEDetector:
    def __init__(
        self,
        model_file: str,
        class_roles: Dict[str, str],
        intra_op_threads: int = 3,
        inter_op_threads: int = 1,
        det_min_confidence: float = 0.25,
        nms_iou: float = 0.7,
        max_det: int = 300,
        expected_sha256: Optional[str] = None,
        class_names: Optional[Dict[int, str]] = None,
    ) -> None:
        self.model_file = model_file
        self.sha256 = sha256_file(model_file)
        if expected_sha256 and expected_sha256.lower() != self.sha256:
            raise ValueError(f"model sha256 mismatch: expected {expected_sha256}, got {self.sha256}")

        so = ort.SessionOptions()
        so.intra_op_num_threads = int(intra_op_threads)
        so.inter_op_num_threads = int(inter_op_threads)
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(model_file, sess_options=so, providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        _, _, ih, iw = inp.shape
        if not (isinstance(ih, int) and isinstance(iw, int)):
            raise ValueError(f"dynamic input shape not supported: {inp.shape}")
        self.input_h, self.input_w = ih, iw

        meta = self.session.get_modelmeta().custom_metadata_map
        self.names: Dict[int, str] = class_names or parse_names(meta)
        if not self.names:
            raise ValueError("class names not found in model metadata; pass class_names")
        by_name = {v.lower(): k for k, v in self.names.items()}
        self.role_to_id: Dict[str, int] = {}
        for role, name in class_roles.items():
            cid = by_name.get(name.lower())
            if cid is None:
                log.warning("class %r (role %s) not in model names; role disabled", name, role)
                continue
            self.role_to_id[role] = cid
        self.id_to_role: Dict[int, str] = {v: k for k, v in self.role_to_id.items()}
        self.det_min_confidence = det_min_confidence
        self.nms_iou = nms_iou
        self.max_det = max_det

    @property
    def has_person_class(self) -> bool:
        return "person" in self.role_to_id

    # ---- 단계 ----
    def preprocess(self, img_bgr: np.ndarray) -> Tuple[np.ndarray, LetterboxMeta]:
        lb, meta = letterbox(img_bgr, self.input_w, self.input_h)
        x = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32)
        x *= 1.0 / 255.0
        return np.ascontiguousarray(x), meta

    def run(self, x: np.ndarray) -> np.ndarray:
        return self.session.run(None, {self.input_name: x})[0]

    def postprocess(self, raw: np.ndarray, meta: LetterboxMeta,
                    keep_class_ids: Optional[Iterable[int]] = None) -> List[Detection]:
        """raw: (1, 4+nc, N). keep_class_ids=None 이면 역할이 지정된 클래스만 남긴다."""
        p = raw[0].T  # (N, 4+nc)
        cls_scores = p[:, 4:]
        cls = cls_scores.argmax(1)
        conf = cls_scores[np.arange(len(cls)), cls]
        keep_ids = set(self.id_to_role) if keep_class_ids is None else set(keep_class_ids)
        m = (conf > self.det_min_confidence) & np.isin(cls, list(keep_ids))
        if not m.any():
            return []
        xywh, conf, cls = p[m, :4], conf[m], cls[m]
        boxes = np.empty_like(xywh)
        boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
        boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
        boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
        boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
        keep = nms_per_class(boxes, conf, cls, self.nms_iou)[: self.max_det]
        boxes, conf, cls = boxes[keep], conf[keep], cls[keep]
        # 레터박스 역변환 → 원본 픽셀, 클립
        boxes[:, [0, 2]] = ((boxes[:, [0, 2]] - meta.pad_left) / meta.ratio).clip(0, meta.orig_w)
        boxes[:, [1, 3]] = ((boxes[:, [1, 3]] - meta.pad_top) / meta.ratio).clip(0, meta.orig_h)
        return [
            Detection(int(c), self.names.get(int(c), str(int(c))), self.id_to_role.get(int(c)), float(s),
                      (float(b[0]), float(b[1]), float(b[2]), float(b[3])))
            for b, s, c in zip(boxes, conf, cls)
        ]

    def detect(self, img_bgr: np.ndarray, keep_class_ids: Optional[Sequence[int]] = None
               ) -> Tuple[List[Detection], Dict[str, float]]:
        """전처리→추론→후처리. timing(ms)은 time.perf_counter() 실측."""
        t0 = time.perf_counter()
        x, meta = self.preprocess(img_bgr)
        t1 = time.perf_counter()
        raw = self.run(x)
        t2 = time.perf_counter()
        dets = self.postprocess(raw, meta, keep_class_ids)
        t3 = time.perf_counter()
        return dets, {
            "preprocess_ms": (t1 - t0) * 1000.0,
            "inference_ms": (t2 - t1) * 1000.0,
            "postprocess_ms": (t3 - t2) * 1000.0,
        }
