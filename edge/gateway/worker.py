"""추론 워커 스레드 1개: 선별 큐에서 프레임을 꺼내 PPEPipeline.process(edge_frame, jpeg) 를 호출한다.

어떤 예외가 나도 워커는 죽지 않고 로그만 남긴다(edge/ppe_decision/HANDOFF.md §1).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Protocol

from .selector import FrameSelector

log = logging.getLogger(__name__)


class Pipeline(Protocol):
    def process(self, edge_frame: Dict[str, Any], jpeg: bytes) -> List[Dict[str, Any]]: ...


def _invalid_image_error() -> type:
    try:  # edge.inference.api 는 cv2/onnxruntime 을 import 한다. 없으면 일반 예외로 처리.
        from edge.inference.api import InvalidImageError

        return InvalidImageError
    except ImportError:
        return type("_NoInvalidImageError", (Exception,), {})


class InferenceWorker:
    def __init__(self, selector: FrameSelector, pipeline: Pipeline, poll_s: float = 0.5) -> None:
        self._selector = selector
        self._pipeline = pipeline
        self._poll_s = poll_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gateway-inference", daemon=True)
        self._lock = threading.Lock()
        self._invalid_image = _invalid_image_error()
        self.processed = 0  # process() 정상 반환
        self.failed = 0  # 예외
        self.events = 0  # 반환된 ppe-event 수

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def counters(self) -> Dict[str, int]:
        with self._lock:
            return {"processed": self.processed, "failed": self.failed, "events": self.events}

    def _run(self) -> None:
        log.info("inference worker started")
        while not self._stop.is_set():
            try:
                item = self._selector.get(timeout=self._poll_s)
                if item is not None:
                    self._handle(*item)
            except Exception:  # 워커는 어떤 경우에도 계속 돈다
                log.exception("inference worker loop error")
        log.info("inference worker stopped")

    def _handle(self, edge_frame: Dict[str, Any], jpeg: bytes) -> None:
        frame_id = edge_frame.get("frame_id")
        try:
            events = self._pipeline.process(edge_frame, jpeg)
        except self._invalid_image:
            log.warning("jpeg decode failed, frame dropped: %s", frame_id)
            self._count(failed=1)
        except OSError:
            log.exception("spool write failed: frame %s", frame_id)
            self._count(failed=1)
        except Exception:
            log.exception("pipeline.process failed: frame %s", frame_id)
            self._count(failed=1)
        else:
            n = len(events) if events else 0
            if n:
                log.info("frame %s: %d ppe event(s) published", frame_id, n)
            self._count(processed=1, events=n)

    def _count(self, processed: int = 0, failed: int = 0, events: int = 0) -> None:
        with self._lock:
            self.processed += processed
            self.failed += failed
            self.events += events
