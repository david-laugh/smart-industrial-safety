"""ppe-event → supabase-event-record (인터페이스 5 행) 변환. 값은 원본 이벤트에서만 가져온다(event_id 재생성 없음)."""
from __future__ import annotations

from typing import Any, Dict


def storage_path(event: Dict[str, Any]) -> str:
    """{device_id}/{yyyy}/{mm}/{dd}/{event_id}.jpg — 날짜는 occurred_at UTC 기준."""
    occurred = event["timestamps"]["occurred_at_utc"]  # 'YYYY-MM-DDT…Z' (계약상 UTC)
    return f"{event['device_id']}/{occurred[0:4]}/{occurred[5:7]}/{occurred[8:10]}/{event['event_id']}.jpg"


def to_record(event: Dict[str, Any], bucket: str) -> Dict[str, Any]:
    ts = event["timestamps"]
    types = sorted({t for v in event["violations"] for t in v["violation_types"]})
    return {
        "event_id": event["event_id"],
        "schema_version": event["schema_version"],
        "device_id": event["device_id"],
        "occurred_at": ts["occurred_at_utc"],
        "occurred_at_source": ts["occurred_at_source"],
        "gateway_received_at": ts["gateway_received_at_utc"],
        "decided_at": ts["decided_at_utc"],
        "violation_count": len(event["violations"]),
        "violation_types": types,
        "storage_bucket": bucket,
        "storage_path": storage_path(event),
        "image_sha256": event["image"]["sha256"],
        "model_name": event["model"]["name"],
        "model_version": event["model"]["version"],
        "event_payload": event,
    }
