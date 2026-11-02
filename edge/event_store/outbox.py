"""SQLite outbox (docs/protocols.md §5 Outbox 상태 머신).

PENDING ─▶ IMAGE_UPLOADED ─▶ SYNCED,  재시도 불가/최대 시도 초과 ─▶ DEAD_LETTER.
cloud-sync 프로세스만 이 DB를 연다(단일 작성자, WAL).

outbox 레코드 = cloud_sync 의 입력(계약 범위 밖, C 내부 규격 — README "outbox 레코드" 표).
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

PENDING = "PENDING"
IMAGE_UPLOADED = "IMAGE_UPLOADED"
SYNCED = "SYNCED"
DEAD_LETTER = "DEAD_LETTER"
STATES = (PENDING, IMAGE_UPLOADED, SYNCED, DEAD_LETTER)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS outbox (
    event_id         TEXT PRIMARY KEY,                -- ppe-event event_id (재생성 금지)
    device_id        TEXT NOT NULL,
    occurred_at      TEXT NOT NULL,                   -- timestamps.occurred_at_utc 원문
    occurred_at_ms   INTEGER NOT NULL,                -- 정렬용 epoch ms (소수 자릿수가 달라도 바르게 정렬)
    image_path       TEXT NOT NULL,                   -- EVENT_DATA_ROOT 기준 상대 경로
    image_sha256     TEXT NOT NULL,
    image_bytes      INTEGER NOT NULL,
    event_json       TEXT NOT NULL,                   -- spool 의 ppe-event JSON 원문
    state            TEXT NOT NULL DEFAULT '{PENDING}' CHECK (state IN {STATES}),
    attempts         INTEGER NOT NULL DEFAULT 0,      -- 재시도 가능 오류 횟수
    next_attempt_at  REAL NOT NULL DEFAULT 0,         -- epoch s
    last_error       TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    synced_at        REAL,
    image_deleted_at REAL                              -- 보존 정책으로 로컬 JPEG 삭제한 시각
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox (state, next_attempt_at, occurred_at_ms);
CREATE INDEX IF NOT EXISTS outbox_synced ON outbox (state, synced_at) WHERE image_deleted_at IS NULL;
"""


def parse_utc_ms(ts: str) -> int:
    return round(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)


@dataclass(frozen=True)
class OutboxItem:
    event_id: str
    device_id: str
    occurred_at: str
    image_path: str
    image_sha256: str
    image_bytes: int
    state: str
    attempts: int
    event: Dict[str, Any]


class Outbox:
    def __init__(self, path: str, synchronous: str = "FULL", clock=time.time) -> None:
        self.clock = clock
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        # 전원 차단 대비(SYS-REL-04). NORMAL 로 낮출지는 측정 후 결정.
        self.db.execute(f"PRAGMA synchronous={synchronous}")
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---- 수집 ----

    def add(self, event: Dict[str, Any], event_json: str) -> bool:
        """INSERT OR IGNORE (PK=event_id). 새로 넣었으면 True, 이미 있으면 False."""
        now = self.clock()
        occurred = event["timestamps"]["occurred_at_utc"]
        cur = self.db.execute(
            "INSERT OR IGNORE INTO outbox (event_id, device_id, occurred_at, occurred_at_ms, image_path, image_sha256,"
            " image_bytes, event_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                event["event_id"], event["device_id"], occurred, parse_utc_ms(occurred), event["image"]["image_path"],
                event["image"]["sha256"], event["image"]["byte_length"], event_json, now, now,
            ),
        )
        return cur.rowcount == 1

    # ---- 업로드 ----

    def due(self, now: float, limit: int) -> List[OutboxItem]:
        """업로드할 차례인 항목. occurred_at 오래된 순."""
        rows = self.db.execute(
            "SELECT * FROM outbox WHERE state IN (?, ?) AND next_attempt_at <= ?"
            " ORDER BY occurred_at_ms, event_id LIMIT ?",
            (PENDING, IMAGE_UPLOADED, now, limit),
        ).fetchall()
        return [self._item(r) for r in rows]

    def get(self, event_id: str) -> Optional[OutboxItem]:
        r = self.db.execute("SELECT * FROM outbox WHERE event_id = ?", (event_id,)).fetchone()
        return self._item(r) if r else None

    def row(self, event_id: str) -> Optional[Dict[str, Any]]:
        r = self.db.execute("SELECT * FROM outbox WHERE event_id = ?", (event_id,)).fetchone()
        return dict(r) if r else None

    def mark_image_uploaded(self, event_id: str) -> None:
        self._set(event_id, state=IMAGE_UPLOADED, last_error=None)

    def mark_synced(self, event_id: str) -> None:
        self._set(event_id, state=SYNCED, synced_at=self.clock(), last_error=None)

    def schedule_retry(self, event_id: str, attempts: int, next_attempt_at: float, error: str) -> None:
        # 상태는 그대로(PENDING 또는 IMAGE_UPLOADED) — IMAGE_UPLOADED 는 다음에 행 upsert 부터 재개
        self._set(event_id, attempts=attempts, next_attempt_at=next_attempt_at, last_error=error[:500])

    def dead_letter(self, event_id: str, error: str, attempts: Optional[int] = None) -> None:
        fields = {"state": DEAD_LETTER, "last_error": error[:500]}
        if attempts is not None:
            fields["attempts"] = attempts
        self._set(event_id, **fields)

    def requeue(self, event_id: str) -> bool:
        """DEAD_LETTER 수동 확인 후 재시도. 이미지 단계부터 다시(409+해시 일치면 성공 처리되므로 안전)."""
        cur = self.db.execute(
            "UPDATE outbox SET state=?, attempts=0, next_attempt_at=0, updated_at=? WHERE event_id=? AND state=?",
            (PENDING, self.clock(), event_id, DEAD_LETTER),
        )
        return cur.rowcount == 1

    # ---- 상태/보존 ----

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in STATES}
        for r in self.db.execute("SELECT state, COUNT(*) n FROM outbox GROUP BY state"):
            out[r["state"]] = r["n"]
        return out

    def local_image_bytes(self) -> int:
        r = self.db.execute("SELECT COALESCE(SUM(image_bytes), 0) FROM outbox WHERE image_deleted_at IS NULL").fetchone()
        return int(r[0])

    def synced_with_local_image(self, synced_before: Optional[float] = None) -> Iterator[sqlite3.Row]:
        """로컬 JPEG 가 남은 SYNCED 항목, 동기화 오래된 순."""
        sql = "SELECT event_id, image_path, image_bytes FROM outbox WHERE state=? AND image_deleted_at IS NULL"
        args: list = [SYNCED]
        if synced_before is not None:
            sql += " AND synced_at < ?"
            args.append(synced_before)
        return iter(self.db.execute(sql + " ORDER BY synced_at, occurred_at_ms", args).fetchall())

    def mark_image_deleted(self, event_id: str) -> None:
        self._set(event_id, image_deleted_at=self.clock())

    # ----

    def _set(self, event_id: str, **fields: Any) -> None:
        fields["updated_at"] = self.clock()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE outbox SET {cols} WHERE event_id=?", (*fields.values(), event_id))

    @staticmethod
    def _item(r: sqlite3.Row) -> OutboxItem:
        return OutboxItem(
            event_id=r["event_id"], device_id=r["device_id"], occurred_at=r["occurred_at"], image_path=r["image_path"],
            image_sha256=r["image_sha256"], image_bytes=r["image_bytes"], state=r["state"], attempts=r["attempts"],
            event=json.loads(r["event_json"]),
        )
