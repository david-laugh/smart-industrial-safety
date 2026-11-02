"""시각 유틸과 Pi 시계 NTP 동기화 상태 확인(gateway_clock_synced).

Linux 에서는 adjtimex(2)(modes=0, 읽기 전용)를 호출한다. 반환값 TIME_ERROR(5)는 커널 STA_UNSYNC
상태 = 미동기화. chrony / systemd-timesyncd 모두 동기화되면 커널 상태를 갱신한다.
시스템 호출이 가볍긴 하지만 요청마다 부르지 않도록 결과를 몇 초간 캐시한다.
Linux 가 아니거나 호출이 실패하면 False(동기화 여부를 보장할 수 없음).
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

log = logging.getLogger(__name__)

_TIME_ERROR = 5


def utc_now_iso(now: Optional[datetime] = None) -> str:
    """RFC 3339 UTC, ms 정밀도, 'Z' 접미사 (common.schema.json#/$defs/utcTimestamp)."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _adjtimex_synced() -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    buf = ctypes.create_string_buffer(512)  # struct timex(약 208바이트)보다 넉넉하게. 0 = modes 0(읽기 전용)
    state = libc.adjtimex(buf)
    if state < 0:
        raise OSError(ctypes.get_errno(), "adjtimex failed")
    return state != _TIME_ERROR


class ClockSyncProbe:
    def __init__(self, check: Optional[Callable[[], bool]] = None, cache_s: float = 5.0) -> None:
        if check is None:
            check = _adjtimex_synced if sys.platform.startswith("linux") else (lambda: False)
        self._check = check
        self._cache_s = cache_s
        self._lock = threading.Lock()
        self._value = False
        self._checked_at = float("-inf")
        self._warned = False

    def synced(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._checked_at >= self._cache_s:
                try:
                    self._value = bool(self._check())
                except Exception:
                    if not self._warned:
                        log.warning("clock sync check failed; reporting gateway_clock_synced=false", exc_info=True)
                        self._warned = True
                    self._value = False
                self._checked_at = now
            return self._value
