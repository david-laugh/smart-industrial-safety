"""대시보드 조회 API: 서버가 secret(service_role) 키로 Supabase 를 대신 조회한다.

브라우저는 Supabase 키를 갖지 않는다. 이 모듈의 정해진 읽기 질의만 허용한다(쓰기·임의 질의 없음).
secret 키는 RLS 를 우회하므로 ppe_events / ppe-evidence 에 anon·authenticated 정책이 없어도 읽을 수 있다.

| 경로 | Supabase 요청 |
|---|---|
| GET /api/devices | ppe_events select=device_id (최근 1000행) |
| GET /api/events | ppe_events select=* + 필터, occurred_at desc, offset/limit, count=exact |
| GET /api/stats | ppe_events 경량 컬럼(최대 5000행) |
| GET /api/events/{id} | ppe_events event_id=eq |
| GET /api/frames/{frame_id} | ppe_events event_payload->>frame_id=eq (최대 64) |
| GET /api/events/{id}/image | 행의 storage_path 로 Storage sign → 짧게 만료되는 URL |
| GET /api/live?after= | ppe_events ingested_at > after (실시간 대신 폴링) |
"""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UTC_TS = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$")
DEVICE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
TYPES = {"HELMET_MISSING", "VEST_MISSING"}
STATS_COLUMNS = "occurred_at,device_id,violation_types,violation_count,frame_id:event_payload->>frame_id"
MAX_PAGE = 100


def _bad(msg):
    raise HTTPException(status_code=400, detail=msg)


def _filters(from_: Optional[str], to: Optional[str], device: List[str], type_: List[str]):
    params = []
    for op, v in (("gte", from_), ("lt", to)):
        if v:
            if not UTC_TS.match(v):
                _bad(f"시각은 UTC 'Z' 형식이어야 합니다: {v}")
            params.append(("occurred_at", f"{op}.{v}"))
    if device:
        if not all(DEVICE.match(d) for d in device):
            _bad("device 형식이 올바르지 않습니다")
        params.append(("device_id", f"in.({','.join(device)})"))
    if type_:
        if not set(type_) <= TYPES:
            _bad("type 은 HELMET_MISSING / VEST_MISSING 만 허용합니다")
        params.append(("violation_types", "ov.{" + ",".join(sorted(set(type_))) + "}"))
    return params


def _uuid(v):
    if not UUID.match(v):
        _bad("ID 형식이 올바르지 않습니다")
    return v


class Supabase:
    def __init__(self, settings, transport: Optional[httpx.AsyncBaseTransport] = None):
        key = settings.service_key
        self.table = settings.events_table
        self.bucket = settings.storage_bucket
        self.expires_s = settings.signed_url_expires_s
        self.base = settings.supabase_url
        self.http = httpx.AsyncClient(
            base_url=settings.supabase_url,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10,
            transport=transport,
        )

    async def aclose(self):
        await self.http.aclose()

    async def _call(self, method, path, **kw):
        try:
            resp = await self.http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=504, detail=f"Supabase 연결 실패: {type(e).__name__}") from None
        if resp.status_code in (401, 403):
            raise HTTPException(status_code=502, detail="Supabase 인증 실패 — 서버의 secret 키를 확인하세요.")
        if not resp.is_success:
            raise HTTPException(status_code=502, detail=f"Supabase {resp.status_code}: {resp.text[:200]}")
        return resp

    async def select(self, params, count=False):
        headers = {"Prefer": "count=exact"} if count else {}
        resp = await self._call("GET", f"/rest/v1/{self.table}", params=params, headers=headers)
        total = None
        if count:
            total = int(resp.headers.get("content-range", "*/0").rsplit("/", 1)[1] or 0)
        return resp.json(), total

    async def sign(self, bucket, path):
        resp = await self._call("POST", f"/storage/v1/object/sign/{bucket}/{path}", json={"expiresIn": self.expires_s})
        signed = resp.json().get("signedURL") or resp.json().get("signedUrl")
        return f"{self.base}/storage/v1{signed}"


def router(sb: Supabase) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/devices")
    async def devices():
        rows, _ = await sb.select([("select", "device_id"), ("order", "occurred_at.desc"), ("limit", "1000")])
        return sorted({row["device_id"] for row in rows})

    @r.get("/events")
    async def events(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = None,
        device: List[str] = Query(default=[]),
        type_: List[str] = Query(default=[], alias="type"),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=MAX_PAGE),
    ):
        params = [("select", "*"), *_filters(from_, to, device, type_), ("order", "occurred_at.desc"),
                  ("offset", str(offset)), ("limit", str(limit))]
        rows, total = await sb.select(params, count=True)
        return {"rows": rows, "total": total}

    @r.get("/stats")
    async def stats(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = None,
        device: List[str] = Query(default=[]),
        type_: List[str] = Query(default=[], alias="type"),
    ):
        # TODO(C): 행이 많아지면 집계 RPC/뷰로 교체
        params = [("select", STATS_COLUMNS), *_filters(from_, to, device, type_), ("order", "occurred_at.desc"), ("limit", "5000")]
        rows, _ = await sb.select(params)
        return rows

    async def _event(event_id, columns="*"):
        rows, _ = await sb.select([("select", columns), ("event_id", f"eq.{_uuid(event_id)}"), ("limit", "1")])
        if not rows:
            raise HTTPException(status_code=404, detail="이벤트가 없습니다")
        return rows[0]

    @r.get("/events/{event_id}")
    async def event(event_id: str):
        return await _event(event_id)

    @r.get("/events/{event_id}/image")
    async def image(event_id: str):
        row = await _event(event_id, "storage_bucket,storage_path")
        if row["storage_bucket"] != sb.bucket:
            raise HTTPException(status_code=409, detail="허용되지 않은 버킷입니다")
        url = await sb.sign(row["storage_bucket"], row["storage_path"])
        expires = datetime.now(timezone.utc) + timedelta(seconds=sb.expires_s)
        return {"url": url, "expiresAt": expires.isoformat().replace("+00:00", "Z")}

    @r.get("/frames/{frame_id}")
    async def frame(frame_id: str):
        # TODO(C): 행이 많아지면 (event_payload->>'frame_id') 식 인덱스 추가 검토
        rows, _ = await sb.select([("select", "*"), ("event_payload->>frame_id", f"eq.{_uuid(frame_id)}"), ("limit", "64")])
        return rows

    @r.get("/live")
    async def live(after: Optional[str] = None):
        """after 보다 늦게 적재된(ingested_at) 행. after 가 없으면 현재 최신 ingested_at 을 커서로 돌려준다."""
        if after is None:
            rows, _ = await sb.select([("select", "ingested_at"), ("order", "ingested_at.desc"), ("limit", "1")])
            return {"rows": [], "cursor": rows[0]["ingested_at"] if rows else "1970-01-01T00:00:00+00:00"}
        try:
            datetime.fromisoformat(after.replace("Z", "+00:00"))
        except ValueError:
            _bad("after 형식이 올바르지 않습니다")
        rows, _ = await sb.select([("select", "*"), ("ingested_at", f"gt.{after}"), ("order", "ingested_at.asc"), ("limit", "100")])
        return {"rows": rows, "cursor": rows[-1]["ingested_at"] if rows else after}

    return r
