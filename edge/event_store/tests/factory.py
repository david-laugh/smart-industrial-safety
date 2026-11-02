"""테스트용 ppe-event + JPEG. contracts/examples/ppe-event.example.json 을 바탕으로 event_id·시각·이미지만 바꾼다.

spool 게시는 B 의 실제 생산자(edge.ppe_decision.spool.SpoolPublisher)를 쓴다.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from edge.ppe_decision.spool import SpoolPublisher

EXAMPLE = json.loads((Path(__file__).resolve().parents[3] / "contracts/examples/ppe-event.example.json").read_text(encoding="utf-8"))
BASE = datetime(2026, 11, 2, 1, 0, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def make_event(n: int, *, minutes: float = 0, device_id: str = "cam-line-a-01", jpeg: bytes | None = None):
    """n 번째 이벤트(시각 = BASE + minutes). (event, jpeg) 반환."""
    jpeg = jpeg if jpeg is not None else b"\xff\xd8\xff\xe0" + f"jpeg-{n}".encode() * 50 + b"\xff\xd9"
    ev = copy.deepcopy(EXAMPLE)
    occurred = BASE + timedelta(minutes=minutes)
    ev["event_id"] = f"019a4a3c-8a10-7f00-8b5c-{n:012x}"
    ev["device_id"] = device_id
    ev["timestamps"].update(
        occurred_at_utc=iso(occurred),
        capture_ts_utc=iso(occurred),
        gateway_received_at_utc=iso(occurred + timedelta(milliseconds=60)),
        decided_at_utc=iso(occurred + timedelta(milliseconds=900)),
    )
    ev["image"].update(
        image_path=f"images/{occurred:%Y/%m/%d}/{ev['event_id']}.jpg",
        byte_length=len(jpeg),
        sha256=hashlib.sha256(jpeg).hexdigest(),
    )
    ev["decision"]["dedup_key"] = f"{device_id}|pos:r2c1|HELMET_MISSING|{int(occurred.timestamp()) // 30 * 30}"
    return ev, jpeg


def publish(data_root, *events) -> None:
    pub = SpoolPublisher(str(data_root))
    for ev, jpeg in events:
        pub.publish(ev, jpeg)
