"""UUIDv7 (RFC 9562) 생성 — 표준 라이브러리만 사용.

레이아웃: unix_ts_ms(48) | ver=7(4) | rand_a(12) | var=0b10(2) | rand_b(62)
같은 ms 안에서는 rand_a 를 단조 증가 카운터로 써서 생성 순서대로 정렬되게 한다.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Optional

_lock = threading.Lock()
_last_ms = -1
_counter = 0


def uuid7(unix_ms: Optional[int] = None) -> str:
    """소문자 하이픈 UUID 문자열. unix_ms 를 주면 그 시각으로 생성(테스트용)."""
    global _last_ms, _counter
    with _lock:
        ms = int(time.time() * 1000) if unix_ms is None else int(unix_ms)
        if ms == _last_ms:
            _counter += 1
            if _counter > 0xFFF:  # 같은 ms 에 4096개 초과 → 다음 ms 로
                ms = _last_ms + 1
                _counter = int.from_bytes(os.urandom(2), "big") & 0x3FF
        else:
            _counter = int.from_bytes(os.urandom(2), "big") & 0x3FF  # 여유를 두고 시작
        _last_ms = ms
        rand_a = _counter & 0xFFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = ((ms & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def uuid7_unix_ms(u: str) -> int:
    return uuid.UUID(u).int >> 80
