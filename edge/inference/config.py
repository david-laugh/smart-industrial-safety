"""추론 설정: 환경변수(infra/docker/.env.example 의 INFERENCE_*, ORT_*) + 기본값.

빈 문자열 환경변수(.env.example 의 미기입 값)는 "미설정"으로 보고 기본값을 쓴다.
기본값은 측정 전 초기값이며 Pi 5 실측/평가 후 조정한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

# 역할(role) → 모델 클래스 이름. 클래스 ID 는 하드코딩하지 않고 모델 메타데이터의 names 에서 찾는다.
DEFAULT_CLASS_ROLES: Dict[str, str] = {
    "person": "Person",
    "helmet": "Hardhat",
    "no_helmet": "NO-Hardhat",
    "vest": "Safety Vest",
    "no_vest": "NO-Safety Vest",
}
ROLES = tuple(DEFAULT_CLASS_ROLES)

PERSON_SOURCES = ("auto", "model", "esp32")


def _get(env: Mapping[str, str], name: str) -> Optional[str]:
    v = env.get(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _float(env, name, default: float) -> float:
    v = _get(env, name)
    return float(v) if v is not None else default


def _int(env, name, default: int) -> int:
    v = _get(env, name)
    return int(v) if v is not None else default


def _range(env, name, default: Tuple[float, float]) -> Tuple[float, float]:
    v = _get(env, name)
    if v is None:
        return default
    lo, hi = (float(x) for x in v.split(","))
    if not 0.0 <= lo < hi <= 1.0:
        raise ValueError(f"{name}: expected 'lo,hi' with 0<=lo<hi<=1, got {v!r}")
    return (lo, hi)


def _class_roles(env) -> Dict[str, str]:
    """INFERENCE_CLASS_MAP="person=Person,helmet=Hardhat,..." 로 일부/전체 덮어쓰기."""
    roles = dict(DEFAULT_CLASS_ROLES)
    v = _get(env, "INFERENCE_CLASS_MAP")
    if v:
        for item in v.split(","):
            k, _, name = item.partition("=")
            k = k.strip()
            if k not in roles:
                raise ValueError(f"INFERENCE_CLASS_MAP: unknown role {k!r} (roles: {', '.join(ROLES)})")
            roles[k] = name.strip()
    return roles


@dataclass(frozen=True)
class InferenceConfig:
    # 모델
    model_path: str = "/models/"
    model_filename: str = "ppe_yolov8n_640.onnx"   # model_path 가 디렉터리일 때 사용
    model_sha256: Optional[str] = None             # 설정 시 로드 시점에 검증
    model_name: str = "yolov8n-ppe-hansung-cho"
    model_version: str = "hf-ac0027b-onnx-op12"
    class_roles: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CLASS_ROLES))

    # ONNX Runtime (CPU EP 고정)
    intra_op_threads: int = 3
    inter_op_threads: int = 1

    # 검출 후처리
    det_min_confidence: float = 0.25   # NMS 전 후보 하한(ultralytics predict 기본과 동일)
    nms_iou: float = 0.7               # 클래스별 NMS IoU(ultralytics predict 기본과 동일)
    max_det: int = 300

    # 판정
    person_min_confidence: float = 0.5
    ppe_worn_min_confidence: float = 0.5
    ppe_missing_min_confidence: float = 0.5
    # 판정 가능한 최소 사람 박스 높이. 기본은 이미지 높이 대비 비율(테스트 영상 1개 기준, ESP32 실프레임으로 재조정 필요).
    # ESP32 640x480 에서 0.25 = 120px. 근거: 3인 영상 원경 인물 0.10~0.275(MISSING 판정 0건),
    # 걸어오는 작업자 영상 최소 0.255 → 0.33 대비 helmet/vest 재현율 0.50/0.72 → 0.76/0.98.
    # INFERENCE_MIN_PERSON_BOX_PX 가 설정되면 px 값이 우선한다.
    min_person_box_ratio: float = 0.25
    min_person_box_px: Optional[int] = None
    edge_margin_px: float = 2.0        # 사람 박스가 이미지 경계에서 이 거리 이내면 '잘림'으로 본다
    person_source: str = "auto"        # auto | model | esp32

    # 연관 영역(사람 박스 높이 대비 비율, 위에서부터)
    helmet_region: Tuple[float, float] = (0.0, 0.25)
    vest_region: Tuple[float, float] = (0.2, 0.7)
    assoc_min_ioa: float = 0.5

    schema_version: str = "1.0.0"

    def resolved_model_file(self) -> str:
        p = self.model_path
        if os.path.isdir(p) or p.endswith(("/", "\\")):
            return os.path.join(p, self.model_filename)
        return p

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "InferenceConfig":
        env = os.environ if env is None else env
        d = cls()
        ppe_common = _float(env, "INFERENCE_PPE_MIN_CONFIDENCE", d.ppe_worn_min_confidence)
        ppe_common_missing = _float(env, "INFERENCE_PPE_MIN_CONFIDENCE", d.ppe_missing_min_confidence)
        person_source = (_get(env, "INFERENCE_PERSON_SOURCE") or _get(env, "PERSON_SOURCE") or d.person_source).lower()
        if person_source not in PERSON_SOURCES:
            raise ValueError(f"INFERENCE_PERSON_SOURCE must be one of {PERSON_SOURCES}, got {person_source!r}")
        sha = _get(env, "INFERENCE_MODEL_SHA256")
        return cls(
            model_path=_get(env, "INFERENCE_MODEL_PATH") or d.model_path,
            model_filename=_get(env, "INFERENCE_MODEL_FILENAME") or d.model_filename,
            model_sha256=sha.lower() if sha else None,
            model_name=_get(env, "INFERENCE_MODEL_NAME") or d.model_name,
            model_version=_get(env, "INFERENCE_MODEL_VERSION") or d.model_version,
            class_roles=_class_roles(env),
            intra_op_threads=_int(env, "ORT_INTRA_OP_THREADS", d.intra_op_threads),
            inter_op_threads=_int(env, "ORT_INTER_OP_THREADS", d.inter_op_threads),
            det_min_confidence=_float(env, "INFERENCE_DET_MIN_CONFIDENCE", d.det_min_confidence),
            nms_iou=_float(env, "INFERENCE_NMS_IOU", d.nms_iou),
            max_det=_int(env, "INFERENCE_MAX_DET", d.max_det),
            person_min_confidence=_float(env, "INFERENCE_PERSON_MIN_CONFIDENCE", d.person_min_confidence),
            ppe_worn_min_confidence=_float(env, "INFERENCE_PPE_WORN_MIN_CONFIDENCE", ppe_common),
            ppe_missing_min_confidence=_float(env, "INFERENCE_PPE_MISSING_MIN_CONFIDENCE", ppe_common_missing),
            min_person_box_ratio=_float(env, "INFERENCE_MIN_PERSON_BOX_RATIO", d.min_person_box_ratio),
            min_person_box_px=(int(_get(env, "INFERENCE_MIN_PERSON_BOX_PX"))
                               if _get(env, "INFERENCE_MIN_PERSON_BOX_PX") else None),
            edge_margin_px=_float(env, "INFERENCE_EDGE_MARGIN_PX", d.edge_margin_px),
            person_source=person_source,
            helmet_region=_range(env, "INFERENCE_HELMET_REGION", d.helmet_region),
            vest_region=_range(env, "INFERENCE_VEST_REGION", d.vest_region),
            assoc_min_ioa=_float(env, "INFERENCE_ASSOC_MIN_IOA", d.assoc_min_ioa),
            schema_version=_get(env, "INFERENCE_SCHEMA_VERSION") or d.schema_version,
        )
