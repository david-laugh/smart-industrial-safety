"""FastAPI 앱: POST /api/v1/frames, GET /healthz (docs/protocols.md §1).

검증 순서(앞에서 실패하면 뒤는 보지 않는다):
  1. Bearer 토큰 → 등록된 장치인지                                   401 UNAUTHORIZED
  2. 요청 크기 > GATEWAY_MAX_REQUEST_BYTES                           413 PAYLOAD_TOO_LARGE
  3. 요청이 multipart/form-data 가 아님                              415 UNSUPPORTED_MEDIA_TYPE
     multipart 형식 오류                                             400 INVALID_METADATA
  4. metadata.device_id ≠ 토큰의 장치                                401 UNAUTHORIZED
  5. image 파트 크기 > GATEWAY_MAX_IMAGE_BYTES                        413 PAYLOAD_TOO_LARGE
  6. image 파트 Content-Type ≠ image/jpeg                            415 UNSUPPORTED_MEDIA_TYPE
  7. metadata 파트 없음 / JSON 아님 / 스키마 실패 / image 파트 없음   400 INVALID_METADATA
  8. image 크기 ≠ byte_length, SOI/EOI 마커 불일치                   400 IMAGE_METADATA_MISMATCH
  9. 선별 큐 상한 초과                                               429 QUEUE_FULL + retry_after_ms
  10. 성공                                                           202
"""
from __future__ import annotations

import hmac
import json
import logging
from collections import Counter
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from . import multipart, schemas
from .clock import ClockSyncProbe, utc_now_iso
from .config import GatewayConfig
from .frames import build_edge_frame, gateway_response, jpeg_markers_ok
from .selector import FrameSelector
from .worker import InferenceWorker, Pipeline

log = logging.getLogger(__name__)


class _Reject(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class GatewayState:
    def __init__(self, config: GatewayConfig, pipeline: Optional[Pipeline], clock: ClockSyncProbe) -> None:
        self.config = config
        self.clock = clock
        self.selector = FrameSelector(config.queue_max_frames, config.queue_max_bytes)
        self.worker = InferenceWorker(self.selector, pipeline) if pipeline is not None else None
        self.accepted = 0
        self.rejected: Counter[str] = Counter()
        # 토큰 → device_id 조회용 목록
        self._tokens = [(t.encode("utf-8"), d) for d, t in config.device_tokens.items()]

    def device_for_authorization(self, header: Optional[str]) -> Optional[str]:
        if not header:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        given = token.strip().encode("utf-8")
        found = None
        for t, device_id in self._tokens:  # 모든 항목과 비교(조기 종료 없음)
            if hmac.compare_digest(given, t):
                found = device_id
        return found


def create_app(
    config: GatewayConfig,
    pipeline: Optional[Pipeline],
    *,
    clock: Optional[ClockSyncProbe] = None,
) -> FastAPI:
    """pipeline=None 이면 추론 워커를 띄우지 않는다(큐에 쌓이기만 함, 테스트용)."""
    schemas.load()
    state = GatewayState(config, pipeline, clock or ClockSyncProbe())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if state.worker is not None:
            state.worker.start()
        try:
            yield
        finally:
            if state.worker is not None:
                state.worker.stop()

    app = FastAPI(title="ppe-gateway", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.gateway = state

    def reply(status: int, received: str, **kw: Any) -> JSONResponse:
        body = gateway_response(gateway_received_at_utc=received, queue_depth=len(state.selector), **kw)
        return JSONResponse(body, status_code=status)

    @app.post("/api/v1/frames")
    async def post_frame(request: Request) -> JSONResponse:
        received = utc_now_iso()
        try:
            device_id = state.device_for_authorization(request.headers.get("authorization"))
            if device_id is None:
                raise _Reject(401, "UNAUTHORIZED", "missing or unknown bearer token")
            body = await _read_body(request, config.max_request_bytes)
            received = utc_now_iso()  # multipart 수신 완료 시각
            clock_synced = state.clock.synced()
            metadata, jpeg = _parse_and_validate(request, body, device_id, config)
            edge_frame = build_edge_frame(
                metadata, jpeg, gateway_received_at_utc=received, gateway_clock_synced=clock_synced
            )
            offer = state.selector.offer(edge_frame, jpeg)
            if not offer.accepted:
                state.rejected["QUEUE_FULL"] += 1
                return reply(
                    429,
                    received,
                    accepted=False,
                    retry_after_ms=config.retry_after_ms,
                    error_code="QUEUE_FULL",
                    error_message="inference queue is full",
                )
            state.accepted += 1
            return reply(202, received, accepted=True, frame_id=edge_frame["frame_id"])
        except _Reject as r:
            state.rejected[r.code] += 1
            log.info("frame rejected %d %s: %s", r.status, r.code, r.message)
            return reply(r.status, received, accepted=False, error_code=r.code, error_message=r.message)
        except ClientDisconnect:
            state.rejected["CLIENT_DISCONNECT"] += 1
            return reply(400, received, accepted=False, error_code="INVALID_METADATA", error_message="client disconnected")
        except Exception:
            log.exception("unexpected error handling frame upload")
            state.rejected["INTERNAL_ERROR"] += 1
            return reply(500, received, accepted=False, error_code="INTERNAL_ERROR", error_message="internal error")

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        st = state.selector.stats()
        worker_alive = state.worker.is_alive() if state.worker is not None else None
        ok = worker_alive is not False
        body: Dict[str, Any] = {
            "status": "ok" if ok else "worker_dead",
            "queue": {
                "frames": st.frames,
                "bytes": st.bytes,
                "max_frames": config.queue_max_frames,
                "max_bytes": config.queue_max_bytes,
            },
            "counters": {
                "accepted": state.accepted,
                "rejected": sum(state.rejected.values()),
                "rejected_by_code": dict(sorted(state.rejected.items())),
                "dropped": st.dropped_total,
                **(state.worker.counters() if state.worker is not None else {}),
            },
            "worker_alive": worker_alive,
            "clock_synced": state.clock.synced(),
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    return app


async def _read_body(request: Request, limit: int) -> bytes:
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > limit:
                raise _Reject(413, "PAYLOAD_TOO_LARGE", f"request exceeds {limit} bytes")
        except ValueError:
            raise _Reject(400, "INVALID_METADATA", "invalid Content-Length") from None
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > limit:
            raise _Reject(413, "PAYLOAD_TOO_LARGE", f"request exceeds {limit} bytes")
    return bytes(buf)


def _parse_and_validate(
    request: Request, body: bytes, token_device: str, config: GatewayConfig
) -> tuple[Dict[str, Any], bytes]:
    try:
        boundary = multipart.boundary_from_content_type(request.headers.get("content-type", ""))
    except multipart.MultipartError as e:
        raise _Reject(400, "INVALID_METADATA", str(e)) from None
    if boundary is None:
        raise _Reject(415, "UNSUPPORTED_MEDIA_TYPE", "request must be multipart/form-data")
    try:
        parts = multipart.parse(body, boundary)
    except multipart.MultipartError as e:
        raise _Reject(400, "INVALID_METADATA", str(e)) from None

    meta_part = parts.get("metadata")
    image_part = parts.get("image")

    metadata: Any = None
    meta_error: Optional[str] = None
    if meta_part is None:
        meta_error = "missing 'metadata' part"
    else:
        try:
            metadata = json.loads(bytes(meta_part.data).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            meta_error = f"metadata is not valid JSON: {e}"

    # 4. 토큰 ↔ device_id (device_id 를 읽을 수 있을 때만. 없으면 7 에서 스키마 오류)
    if isinstance(metadata, dict) and isinstance(metadata.get("device_id"), str):
        if metadata["device_id"] != token_device:
            raise _Reject(401, "UNAUTHORIZED", "bearer token does not match device_id")

    if image_part is not None:
        if len(image_part.data) > config.max_image_bytes:
            raise _Reject(413, "PAYLOAD_TOO_LARGE", f"image exceeds {config.max_image_bytes} bytes")
        if image_part.content_type != "image/jpeg":
            raise _Reject(415, "UNSUPPORTED_MEDIA_TYPE", "image part must be image/jpeg")

    if meta_error is not None:
        raise _Reject(400, "INVALID_METADATA", meta_error)
    errs = schemas.errors("esp32-frame-metadata", metadata)
    if errs:
        raise _Reject(400, "INVALID_METADATA", "; ".join(errs))
    if image_part is None:
        raise _Reject(400, "INVALID_METADATA", "missing 'image' part")

    jpeg = bytes(image_part.data)
    declared = metadata["image"]["byte_length"]
    if len(jpeg) != declared:
        raise _Reject(400, "IMAGE_METADATA_MISMATCH", f"image is {len(jpeg)} bytes, metadata says {declared}")
    if not jpeg_markers_ok(jpeg):
        raise _Reject(400, "IMAGE_METADATA_MISMATCH", "image is missing JPEG SOI/EOI markers")
    return metadata, jpeg
