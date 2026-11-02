"""로컬 보존 정책 (docs/protocols.md §5 로컬 보존, EC-06).

- SYNCED 이미지: EVENT_LOCAL_RETENTION_DAYS 지나면 삭제(processed/ JSON 도 함께).
- 로컬 이미지 합계가 EVENT_STORE_MAX_BYTES 를 넘으면 SYNCED 오래된 것부터 삭제.
  그래도 넘으면 경보만 낸다 — 미전송 이벤트는 자동 삭제하지 않는다.
outbox 행은 지우지 않는다(event_id 이력 유지).
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

from .outbox import Outbox

log = logging.getLogger("event_store.retention")


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def prune(data_root: str, outbox: Outbox, now: float, retention_days: float, max_bytes: Optional[int]) -> Dict[str, int]:
    stats = {"deleted": 0, "over_limit": 0}

    def delete(row) -> None:
        _remove(os.path.join(data_root, *row["image_path"].split("/")))
        _remove(os.path.join(data_root, "spool", "processed", f"{row['event_id']}.json"))
        outbox.mark_image_deleted(row["event_id"])
        stats["deleted"] += 1

    for row in outbox.synced_with_local_image(synced_before=now - retention_days * 86400):
        delete(row)

    if max_bytes:
        total = outbox.local_image_bytes()
        if total > max_bytes:
            for row in outbox.synced_with_local_image():
                delete(row)
                total -= row["image_bytes"]
                if total <= max_bytes:
                    break
        if total > max_bytes:
            stats["over_limit"] = 1
            log.error("ALERT event store over limit bytes=%d max=%d (미전송 이벤트는 보존)", total, max_bytes)
    return stats
