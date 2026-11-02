"""outbox 항목을 Supabase 로 올리는 워커 (docs/protocols.md §5).

항목 1건: (PENDING 이면) 로컬 JPEG 확인 → Storage 업로드 → IMAGE_UPLOADED → ppe_events upsert → SYNCED.
IMAGE_UPLOADED 로 남은 항목(재시작·재시도)은 행 upsert 부터 이어간다. event_id 는 outbox 원문 그대로 쓴다.
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Callable, Dict

from edge.event_store.ingest import sha256_file
from edge.event_store.outbox import IMAGE_UPLOADED, PENDING, Outbox, OutboxItem

from .config import SyncConfig
from .record import to_record
from .supabase import PauseError, PermanentError, RetryableError, SupabaseClient

log = logging.getLogger("cloud_sync")


def backoff_s(attempt: int, base: float, cap: float, rand: Callable[[], float] = random.random) -> float:
    """min(SYNC_RETRY_MAX_S, SYNC_RETRY_BASE_S × 2^attempt) × (0.5~1.0 지터). attempt 는 0부터."""
    return min(cap, base * (2 ** attempt)) * (0.5 + 0.5 * rand())


class CloudSync:
    def __init__(
        self,
        cfg: SyncConfig,
        outbox: Outbox,
        client: SupabaseClient,
        clock: Callable[[], float] = time.time,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.cfg = cfg
        self.outbox = outbox
        self.client = client
        self.clock = clock
        self.rand = rand
        self.paused_until = 0.0
        if cfg.concurrency > 1:
            log.warning("SYNC_CONCURRENCY=%d 는 아직 지원하지 않아 1로 동작합니다(측정 후 확장).", cfg.concurrency)

    def sync_once(self) -> Dict[str, int]:
        """업로드할 차례인 항목을 오래된 순으로 처리. 반환: 결과별 건수."""
        stats = {"synced": 0, "retry": 0, "dead_letter": 0, "paused": 0}
        now = self.clock()
        if now < self.paused_until:
            stats["paused"] = 1
            return stats
        for item in self.outbox.due(now, self.cfg.batch_size):
            try:
                self._process(item)
                stats["synced"] += 1
            except PauseError as e:
                # 401/403/설정 오류: 이벤트는 그대로 두고(시도 횟수도 늘리지 않음) 업로드 전체를 멈춘다
                self.paused_until = self.clock() + self.cfg.auth_pause_s
                log.critical(
                    "ALERT cloud-sync paused %.0fs (키/권한/버킷·테이블 설정 확인): event_id=%s %s",
                    self.cfg.auth_pause_s, item.event_id, e,
                )
                stats["paused"] = 1
                break
            except RetryableError as e:
                stats[self._retry(item, e)] += 1
            except PermanentError as e:
                self.outbox.dead_letter(item.event_id, str(e))
                log.error("dead letter event_id=%s %s", item.event_id, e)
                stats["dead_letter"] += 1
        return stats

    def _process(self, item: OutboxItem) -> None:
        record = to_record(item.event, self.cfg.storage_bucket)
        if item.state == PENDING:
            local = os.path.join(self.cfg.data_root, *item.image_path.split("/"))
            if not os.path.isfile(local):
                raise PermanentError(f"로컬 증거 JPEG 없음: {item.image_path}")
            if sha256_file(local) != item.image_sha256:
                raise PermanentError(f"로컬 증거 JPEG sha256 불일치: {item.image_path}")
            result = self.client.upload_image(self.cfg.storage_bucket, record["storage_path"], local, item.image_sha256)
            self.outbox.mark_image_uploaded(item.event_id)
            log.info("image %s event_id=%s path=%s", result, item.event_id, record["storage_path"])
        elif item.state != IMAGE_UPLOADED:
            return
        self.client.upsert_row(self.cfg.events_table, record)
        self.outbox.mark_synced(item.event_id)
        log.info("synced event_id=%s occurred_at=%s", item.event_id, item.occurred_at)

    def _retry(self, item: OutboxItem, e: RetryableError) -> str:
        attempts = item.attempts + 1
        if self.cfg.max_attempts is not None and attempts >= self.cfg.max_attempts:
            self.outbox.dead_letter(item.event_id, f"최대 시도 {attempts}회 초과: {e}", attempts=attempts)
            log.error("dead letter (max attempts) event_id=%s %s", item.event_id, e)
            return "dead_letter"
        delay = e.retry_after if e.retry_after is not None else backoff_s(
            item.attempts, self.cfg.retry_base_s, self.cfg.retry_max_s, self.rand
        )
        self.outbox.schedule_retry(item.event_id, attempts, self.clock() + delay, str(e))
        log.warning("retry in %.1fs attempt=%d event_id=%s %s", delay, attempts, item.event_id, e)
        return "retry"
