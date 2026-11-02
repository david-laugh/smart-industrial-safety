"""프레임 선별 큐 (메모리 전용, docs/protocols.md §2).

- 장치별 대기 프레임 최대 1장(latest-wins). 새 프레임이 오면 대기 중인 이전 프레임을 드롭하고
  장치의 dropped_since_last_selected 를 1 증가시킨다. 교체된 프레임은 이전 프레임의 대기 순서를 물려받는다
  (계속 보내는 장치가 뒤로 밀려 굶지 않도록).
- 전체 상한: 프레임 수(max_frames), JPEG 바이트 합(max_bytes). 넘으면 거부 → 호출자가 429.
- 꺼내는 순서: edge_detection.person_detected=true 프레임 우선, 같은 우선순위 안에서는 먼저 들어온 순.
- dropped_since_last_selected 는 꺼낼 때 확정해 edge_frame.selection 에 기록하고 0 으로 되돌린다.
"""
from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class _Pending:
    edge_frame: Dict[str, Any]
    jpeg: bytes
    priority: bool
    order: int


@dataclass(frozen=True)
class OfferResult:
    accepted: bool
    replaced: bool  # 같은 장치의 대기 프레임을 드롭하고 교체했는지
    queue_depth: int  # offer 처리 후 큐 길이


@dataclass(frozen=True)
class SelectorStats:
    frames: int
    bytes: int
    dropped_total: int


class FrameSelector:
    def __init__(self, max_frames: int, max_bytes: int) -> None:
        if max_frames < 1 or max_bytes < 1:
            raise ValueError("max_frames and max_bytes must be >= 1")
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self._pending: Dict[str, _Pending] = {}
        self._dropped: Dict[str, int] = {}
        self._bytes = 0
        self._dropped_total = 0
        self._order = itertools.count()
        self._cond = threading.Condition()

    def offer(self, edge_frame: Dict[str, Any], jpeg: bytes) -> OfferResult:
        """프레임을 넣는다. 상한 초과면 accepted=False(큐 변화 없음).

        edge_frame["selection"]["queue_depth_at_enqueue"] 는 여기서 채운다(교체 대상 제외, 자신 제외).
        """
        device_id = edge_frame["device_id"]
        size = len(jpeg)
        ed = edge_frame.get("edge_detection") or {}
        priority = ed.get("person_detected") is True
        with self._cond:
            old = self._pending.get(device_id)
            depth_without_old = len(self._pending) - (1 if old else 0)
            bytes_without_old = self._bytes - (len(old.jpeg) if old else 0)
            if depth_without_old + 1 > self.max_frames or bytes_without_old + size > self.max_bytes:
                return OfferResult(accepted=False, replaced=False, queue_depth=len(self._pending))
            edge_frame["selection"]["queue_depth_at_enqueue"] = depth_without_old
            order = old.order if old else next(self._order)
            if old:
                self._dropped[device_id] = self._dropped.get(device_id, 0) + 1
                self._dropped_total += 1
            self._pending[device_id] = _Pending(edge_frame, jpeg, priority, order)
            self._bytes = bytes_without_old + size
            self._cond.notify()
            return OfferResult(accepted=True, replaced=old is not None, queue_depth=len(self._pending))

    def get(self, timeout: Optional[float] = None) -> Optional[Tuple[Dict[str, Any], bytes]]:
        """다음 프레임을 꺼낸다. timeout 안에 없으면 None."""
        with self._cond:
            if not self._pending and not self._cond.wait_for(lambda: bool(self._pending), timeout):
                return None
            device_id, p = min(self._pending.items(), key=lambda kv: (not kv[1].priority, kv[1].order))
            del self._pending[device_id]
            self._bytes -= len(p.jpeg)
            p.edge_frame["selection"]["dropped_since_last_selected"] = self._dropped.pop(device_id, 0)
            return p.edge_frame, p.jpeg

    def __len__(self) -> int:
        with self._cond:
            return len(self._pending)

    def stats(self) -> SelectorStats:
        with self._cond:
            return SelectorStats(frames=len(self._pending), bytes=self._bytes, dropped_total=self._dropped_total)
