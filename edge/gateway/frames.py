"""edge-frame / gateway-response 객체 생성과 JPEG 바이트 검증."""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict, Optional

from edge.ppe_decision.uuid7 import uuid7

EDGE_FRAME_SCHEMA_VERSION = "1.0.0"
GATEWAY_RESPONSE_SCHEMA_VERSION = "1.0.0"

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"


def jpeg_markers_ok(jpeg: bytes) -> bool:
    """JPEG SOI(FFD8)로 시작하고 EOI(FFD9)로 끝나는지. 디코드 검증은 추론 단계에서 한다."""
    return len(jpeg) >= 4 and jpeg.startswith(_SOI) and jpeg.endswith(_EOI)


def selection_reason(metadata: Dict[str, Any]) -> str:
    ed = metadata.get("edge_detection") or {}
    if ed.get("person_detected") is True:
        return "EDGE_PERSON_HINT"
    if metadata.get("trigger") == "MANUAL":
        return "MANUAL"
    return "LATEST_FRAME"


def build_edge_frame(
    metadata: Dict[str, Any],
    jpeg: bytes,
    *,
    gateway_received_at_utc: str,
    gateway_clock_synced: bool,
    frame_id: Optional[str] = None,
) -> Dict[str, Any]:
    """검증된 esp32-frame-metadata + JPEG → edge-frame (location.kind=IN_MEMORY).

    capture_ts_utc 는 ESP32 값을 수정 없이 전달한다. selection.queue_depth_at_enqueue 와
    dropped_since_last_selected 는 선별 큐가 채운다(초기값 0).
    """
    img = metadata["image"]
    ed = metadata.get("edge_detection")
    return {
        "schema_version": EDGE_FRAME_SCHEMA_VERSION,
        "frame_id": frame_id or uuid7(),
        "device_id": metadata["device_id"],
        "boot_id": metadata["boot_id"],
        "frame_seq": metadata["frame_seq"],
        "mcu_uptime_ms": metadata["mcu_uptime_ms"],
        "capture_ts_utc": metadata["time_sync"]["capture_ts_utc"],
        "gateway_received_at_utc": gateway_received_at_utc,
        "gateway_clock_synced": bool(gateway_clock_synced),
        "image": {
            "encoding": "jpeg",
            "width": img["width"],
            "height": img["height"],
            "byte_length": len(jpeg),
            "sha256": hashlib.sha256(jpeg).hexdigest(),
            "location": {"kind": "IN_MEMORY", "path": None},
        },
        "edge_detection": copy.deepcopy(ed) if ed is not None else None,
        "selection": {
            "reason": selection_reason(metadata),
            "queue_depth_at_enqueue": 0,
            "dropped_since_last_selected": 0,
        },
    }


def gateway_response(
    *,
    accepted: bool,
    gateway_received_at_utc: str,
    queue_depth: int,
    frame_id: Optional[str] = None,
    retry_after_ms: Optional[int] = None,
    error_code: Optional[str] = None,
    error_message: str = "",
) -> Dict[str, Any]:
    return {
        "schema_version": GATEWAY_RESPONSE_SCHEMA_VERSION,
        "accepted": accepted,
        "frame_id": frame_id if accepted else None,
        "gateway_received_at_utc": gateway_received_at_utc,
        "queue_depth": queue_depth,
        "retry_after_ms": retry_after_ms,
        "error": None if accepted else {"code": error_code, "message": error_message[:256]},
    }
