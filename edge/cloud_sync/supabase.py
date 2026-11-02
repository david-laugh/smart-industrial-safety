"""Supabase Storage / PostgREST 호출과 응답 분류 (docs/protocols.md §5 업로드 순서·재시도 규칙).

| 응답 | 분류 |
|---|---|
| 네트워크 오류, 408/429/5xx | RetryableError (Retry-After 우선) |
| 400, 422 등 그 밖의 4xx | PermanentError → DEAD_LETTER |
| 401, 403 | PauseError → 업로드 전체 일시 중지 + 경보 |
| 404 (버킷/테이블 없음) | PauseError — 설정 오류로 보고 이벤트를 DEAD_LETTER 로 보내지 않음 (잠정) |

Storage 는 오류를 HTTP 400 + {"statusCode": "409"|"403"|"404", ...} 로 주기도 하므로 본문 statusCode 를 우선한다.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, BinaryIO, Callable, Dict, Optional, Union

RETRY_STATUS = {408, 429, 500, 502, 503, 504}
PAUSE_STATUS = {401, 403, 404}


class SyncError(Exception):
    pass


class RetryableError(SyncError):
    def __init__(self, msg: str, retry_after: Optional[float] = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


class PermanentError(SyncError):
    pass


class PauseError(SyncError):
    pass


class Response:
    def __init__(self, status: int, headers: Dict[str, str], body: BinaryIO) -> None:
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def close(self) -> None:
        self._body.close()


Body = Union[bytes, BinaryIO, None]
Transport = Callable[[str, str, Dict[str, str], Body], Response]


def urllib_transport(timeout: float) -> Transport:
    def send(method: str, url: str, headers: Dict[str, str], body: Body) -> Response:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return Response(r.status, dict(r.headers), r)
        except urllib.error.HTTPError as e:
            return Response(e.code, dict(e.headers or {}), e)

    return send


def retry_after_s(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


class SupabaseClient:
    def __init__(self, base_url: str, service_key: str, transport: Transport) -> None:
        self.base = base_url.rstrip("/")
        self._auth = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
        self.transport = transport

    def _send(self, method: str, url: str, headers: Dict[str, str], body: Body = None) -> Response:
        try:
            return self.transport(method, url, {**self._auth, **headers}, body)
        except (OSError, http.client.HTTPException) as e:  # URLError, timeout, 연결 끊김
            raise RetryableError(f"network: {type(e).__name__}: {e}") from None

    @staticmethod
    def _effective_status(resp: Response, text: str) -> int:
        if resp.status == 400:
            try:
                code = json.loads(text).get("statusCode")
                if code is not None and str(code).isdigit():
                    return int(code)
            except (ValueError, AttributeError):
                pass
        return resp.status

    def _raise_for(self, resp: Response, what: str, text: Optional[str] = None) -> None:
        if text is None:
            text = resp.read(2048).decode("utf-8", "replace")
        resp.close()
        status = self._effective_status(resp, text)
        msg = f"{what} {resp.status}" + (f"/{status}" if status != resp.status else "") + f": {text[:300]}"
        if status in PAUSE_STATUS:
            raise PauseError(msg)
        if status in RETRY_STATUS or status >= 500:
            raise RetryableError(msg, retry_after_s(resp.headers.get("retry-after")))
        raise PermanentError(msg)

    # ---- 1단계: Storage ----

    def _object_url(self, bucket: str, path: str) -> str:
        return f"{self.base}/storage/v1/object/{bucket}/{urllib.parse.quote(path)}"

    def upload_image(self, bucket: str, path: str, file_path: str, sha256: str) -> str:
        """'uploaded' 또는 'exists'(409 + 해시 일치). 파일 단위 스트리밍(메모리에 백로그를 올리지 않음)."""
        url = self._object_url(bucket, path)
        size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            resp = self._send(
                "POST", url, {"Content-Type": "image/jpeg", "Content-Length": str(size), "x-upsert": "false"}, f
            )
        if 200 <= resp.status < 300:
            resp.close()
            return "uploaded"
        text = resp.read(2048).decode("utf-8", "replace")
        if self._effective_status(resp, text) == 409:
            resp.close()
            if self._remote_sha256(url) == sha256:
                return "exists"
            raise PermanentError(f"storage 409: 같은 경로에 다른 내용의 객체가 있습니다: {path}")
        self._raise_for(resp, "storage upload", text)
        raise AssertionError("unreachable")

    def _remote_sha256(self, url: str) -> str:
        resp = self._send("GET", url, {})
        if not 200 <= resp.status < 300:
            self._raise_for(resp, "storage get")
        h = hashlib.sha256()
        try:
            for block in iter(lambda: resp.read(64 * 1024), b""):
                h.update(block)
        finally:
            resp.close()
        return h.hexdigest()

    # ---- 2단계: ppe_events ----

    def upsert_row(self, table: str, record: Dict[str, Any]) -> None:
        body = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        resp = self._send(
            "POST",
            f"{self.base}/rest/v1/{table}?on_conflict=event_id",
            {"Content-Type": "application/json", "Prefer": "resolution=ignore-duplicates,return=minimal"},
            body,
        )
        if 200 <= resp.status < 300:
            resp.close()
            return
        self._raise_for(resp, "ppe_events upsert")

