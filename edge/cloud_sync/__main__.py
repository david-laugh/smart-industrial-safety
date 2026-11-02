"""cloud-sync 프로세스 진입점: spool 수집 → 업로드 → 보존 정책, 반복.

    PYTHONPATH=<저장소 루트> python -m edge.cloud_sync            # 상주 실행
    PYTHONPATH=<저장소 루트> python -m edge.cloud_sync once       # 한 번만(수동 확인용)
    PYTHONPATH=<저장소 루트> python -m edge.cloud_sync status     # outbox 상태별 건수
    PYTHONPATH=<저장소 루트> python -m edge.cloud_sync requeue <event_id>   # DEAD_LETTER 재시도
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

from edge.event_store.ingest import SpoolIngestor
from edge.event_store.outbox import Outbox
from edge.event_store.retention import prune

from .config import ConfigError, SyncConfig
from .supabase import SupabaseClient, urllib_transport
from .worker import CloudSync

log = logging.getLogger("cloud_sync")
RETENTION_EVERY_S = 600


def build(cfg: SyncConfig):
    os.makedirs(os.path.dirname(os.path.abspath(cfg.db_path)), exist_ok=True)
    outbox = Outbox(cfg.db_path, synchronous=cfg.sqlite_synchronous)
    ingestor = SpoolIngestor(cfg.data_root, outbox)
    client = SupabaseClient(cfg.supabase_url, cfg.service_key, urllib_transport(cfg.http_timeout_s))
    return outbox, ingestor, CloudSync(cfg, outbox, client)


def run_once(cfg, outbox, ingestor, sync) -> dict:
    out = {"ingest": ingestor.run_once(), "sync": sync.sync_once()}
    out["retention"] = prune(cfg.data_root, outbox, time.time(), cfg.retention_days, cfg.store_max_bytes)
    out["outbox"] = outbox.counts()
    return out


def run_forever(cfg, outbox, ingestor, sync) -> None:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    log.info("cloud-sync start data_root=%s db=%s bucket=%s table=%s", cfg.data_root, cfg.db_path, cfg.storage_bucket, cfg.events_table)
    last_prune = 0.0
    while not stop.is_set():
        ingested = ingestor.run_once()
        synced = sync.sync_once()
        if time.time() - last_prune >= RETENTION_EVERY_S:
            prune(cfg.data_root, outbox, time.time(), cfg.retention_days, cfg.store_max_bytes)
            last_prune = time.time()
        busy = ingested["ingested"] or (synced["synced"] + synced["retry"] + synced["dead_letter"]) >= cfg.batch_size
        if not busy:
            stop.wait(cfg.poll_interval_s)
    log.info("cloud-sync stop %s", outbox.counts())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m edge.cloud_sync")
    p.add_argument("command", nargs="?", default="run", choices=["run", "once", "status", "requeue"])
    p.add_argument("event_id", nargs="?")
    args = p.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.command in ("status", "requeue"):
        # Supabase 설정 없이도 로컬 outbox 만 본다
        root = os.environ.get("EVENT_DATA_ROOT", "/data")
        outbox = Outbox(os.environ.get("EVENT_STORE_DB_PATH") or os.path.join(root, "outbox.db"))
        if args.command == "status":
            print(json.dumps(outbox.counts()))
            return 0
        if not args.event_id:
            p.error("requeue 에는 event_id 가 필요합니다")
        ok = outbox.requeue(args.event_id)
        print("requeued" if ok else "DEAD_LETTER 상태인 해당 event_id 가 없습니다")
        return 0 if ok else 1

    try:
        cfg = SyncConfig.from_env()
    except ConfigError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2
    parts = build(cfg)
    if args.command == "once":
        print(json.dumps(run_once(cfg, *parts), ensure_ascii=False))
    else:
        run_forever(cfg, *parts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
