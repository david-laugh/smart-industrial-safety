"""spool 수집 (docs/protocols.md §4 spool 게시 절차의 소비 측).

incoming/*.json → ppe-event 스키마 검증 + 증거 JPEG 확인 → outbox INSERT OR IGNORE → processed/
실패 → rejected/ (로그 남김). tmp/*.part 는 보지 않는다(JSON 이 보이면 JPEG 는 이미 완성).
커밋 후 이동이므로, 이동 전에 죽어도 다음 수집에서 중복으로 무시되고 이동만 다시 한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Callable, Dict, List, Optional

from .contracts import errors as contract_errors
from .outbox import Outbox

log = logging.getLogger("event_store.ingest")


def sha256_file(path: str, chunk: int = 64 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


class SpoolIngestor:
    def __init__(
        self,
        data_root: str,
        outbox: Outbox,
        validate: Optional[Callable[[dict], List[str]]] = None,
        batch: int = 200,
    ) -> None:
        self.root = data_root
        self.outbox = outbox
        self.validate = validate or (lambda e: contract_errors("ppe-event", e))
        self.batch = batch
        self.incoming = os.path.join(data_root, "spool", "incoming")
        self.processed = os.path.join(data_root, "spool", "processed")
        self.rejected = os.path.join(data_root, "spool", "rejected")
        for d in (self.incoming, self.processed, self.rejected):
            os.makedirs(d, exist_ok=True)

    def run_once(self) -> Dict[str, int]:
        stats = {"ingested": 0, "duplicate": 0, "rejected": 0}
        names = sorted(n for n in os.listdir(self.incoming) if n.endswith(".json"))[: self.batch]
        for name in names:
            src = os.path.join(self.incoming, name)
            reason = None
            try:
                with open(src, "rb") as f:
                    raw = f.read()
                event = json.loads(raw)
                reason = self._check(event, name)
            except (OSError, ValueError) as e:
                reason = f"읽기/JSON 오류: {e}"
            if reason:
                log.warning("spool rejected file=%s reason=%s", name, reason)
                os.replace(src, os.path.join(self.rejected, name))
                stats["rejected"] += 1
                continue
            new = self.outbox.add(event, raw.decode("utf-8"))  # autocommit
            stats["ingested" if new else "duplicate"] += 1
            os.replace(src, os.path.join(self.processed, name))
        return stats

    def _check(self, event: dict, name: str) -> Optional[str]:
        errs = self.validate(event)
        if errs:
            return "ppe-event 스키마: " + "; ".join(errs[:5])
        if name != f"{event['event_id']}.json":
            return f"파일명과 event_id 불일치: {event['event_id']}"
        img = os.path.join(self.root, *event["image"]["image_path"].split("/"))
        if not os.path.isfile(img):
            return f"증거 JPEG 없음: {event['image']['image_path']}"
        size = os.path.getsize(img)
        if size != event["image"]["byte_length"]:
            return f"JPEG 크기 불일치: {size} != {event['image']['byte_length']}"
        if sha256_file(img) != event["image"]["sha256"]:
            return "JPEG sha256 불일치"
        return None
