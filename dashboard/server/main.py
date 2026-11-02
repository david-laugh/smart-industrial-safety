"""대시보드 서버.

- `/`            index.html
- `/css`, `/js`  정적 파일 (dashboard/ 의 다른 파일 — .env, tests, .venv 등 — 은 노출하지 않음)
- `/js/config.local.js`  환경변수로 만든 런타임 설정(window.PPE_LOCAL_CONFIG, 비밀 값 없음)
- `/api/*`       DASHBOARD_DATA_SOURCE=supabase 일 때 조회 API (server/supabase_api.py)
- `/healthz`     상태 확인(토큰 불필요)

접근 제어(SYS-SEC-01): DASHBOARD_ACCESS_TOKEN. 처음 `/?token=…` 으로 열면 httpOnly 쿠키로 바꾸고 주소에서 토큰을 지운다.
API 는 쿠키 또는 `Authorization: Bearer <token>`. 토큰이 없거나 틀리면 401.
Supabase secret 키는 서버에만 있고 브라우저로 내려가지 않는다.

uvicorn 직접 실행: `uvicorn server.main:create_app --factory` (설정은 기동 시점의 환경변수/.env 에서 읽음)
"""

import base64
import hmac
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

DASHBOARD_DIR = Path(__file__).resolve().parents[1]
COOKIE = "ppe_dashboard_token"
COOKIE_MAX_AGE_S = 12 * 3600
MIN_TOKEN_LEN = 16


@dataclass(frozen=True)
class Settings:
    bind_addr: str = "127.0.0.1"
    port: int = 8765
    reload: bool = False
    log_level: str = "info"
    data_source: str = "sample"
    supabase_url: str = ""
    service_key: str = field(default="", repr=False)  # 서버 전용. 로그·repr·브라우저에 나가지 않음
    access_token: str = field(default="", repr=False)
    events_table: str = "ppe_events"
    storage_bucket: str = "ppe-evidence"
    signed_url_expires_s: int = 120
    live_interval_s: int = 15

    @classmethod
    def from_env(cls, env=None):
        if env is None:
            # 셸 환경변수가 dashboard/.env 보다 우선
            load_dotenv(DASHBOARD_DIR / ".env", override=False)
            env = os.environ
        g = lambda k, d="": (env.get(k) or d).strip()  # noqa: E731
        return cls(
            bind_addr=g("DASHBOARD_BIND_ADDR", "127.0.0.1"),
            port=int(g("DASHBOARD_PORT", "8765")),
            reload=g("DASHBOARD_RELOAD", "false").lower() in ("1", "true", "yes"),
            log_level=g("LOG_LEVEL", "info").lower(),
            data_source=g("DASHBOARD_DATA_SOURCE", "sample").lower(),
            supabase_url=g("SUPABASE_URL").rstrip("/"),
            service_key=read_service_key(env),
            access_token=g("DASHBOARD_ACCESS_TOKEN"),
            events_table=g("SUPABASE_EVENTS_TABLE", "ppe_events"),
            storage_bucket=g("SUPABASE_STORAGE_BUCKET", "ppe-evidence"),
            signed_url_expires_s=int(g("DASHBOARD_SIGNED_URL_EXPIRES_S", "120")),
            live_interval_s=int(g("DASHBOARD_LIVE_INTERVAL_S", "15")),
        )

    def validate(self):
        if self.data_source not in ("sample", "supabase"):
            raise ValueError(f"DASHBOARD_DATA_SOURCE 는 sample 또는 supabase 여야 합니다: {self.data_source!r}")
        if self.access_token and len(self.access_token) < MIN_TOKEN_LEN:
            raise ValueError(f"DASHBOARD_ACCESS_TOKEN 은 {MIN_TOKEN_LEN}자 이상이어야 합니다.")
        if self.data_source != "supabase":
            return
        if not self.supabase_url or not self.service_key:
            raise ValueError("DASHBOARD_DATA_SOURCE=supabase 에는 SUPABASE_URL 과 SUPABASE_SERVICE_KEY(secret 키)가 필요합니다.")
        if urlsplit(self.supabase_url).scheme != "https":
            raise ValueError("SUPABASE_URL 은 https:// 여야 합니다.")
        if _is_public_key(self.service_key):
            raise ValueError("SUPABASE_SERVICE_KEY 에 publishable/anon 키가 들어 있습니다. secret(service_role) 키가 필요합니다.")
        if not self.access_token:
            raise ValueError("DASHBOARD_DATA_SOURCE=supabase 에는 DASHBOARD_ACCESS_TOKEN 이 필요합니다(비인증 접근 차단).")


def read_service_key(env):
    """.env/환경변수의 SUPABASE_SERVICE_KEY (seed 와 같은 이름)."""
    return (env.get("SUPABASE_SERVICE_KEY") or "").strip()


def _is_public_key(key):
    """publishable 키 또는 role=anon JWT 면 True — RLS 에 막혀 아무것도 읽지 못한다."""
    if key.startswith("sb_publishable_"):
        return True
    parts = key.split(".")
    if len(parts) != 3:
        return False
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return False
    return payload.get("role") == "anon"


def _client_config(s):
    cfg = {"dataSource": s.data_source}
    if s.data_source == "supabase":
        cfg.update(signedUrlExpiresSec=s.signed_url_expires_s, liveIntervalMs=s.live_interval_s * 1000)
    return cfg


def _csp(s):
    images = f" https://{urlsplit(s.supabase_url).netloc}" if s.data_source == "supabase" else ""
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",  # bbox/차트 위치용 style 속성
            f"img-src 'self' data: blob:{images}",  # 증거 이미지는 Signed URL 로 Storage 에서 직접
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'self'",
        ]
    )


_DENIED_HTML = """<!doctype html><meta charset="utf-8"><title>PPE 대시보드</title>
<p style="font:16px system-ui;padding:24px">접근 토큰이 필요합니다. 관리자에게 받은 주소(<code>/?token=…</code>)로 여세요.</p>"""


def create_app(settings=None, transport=None):
    """transport: 테스트에서 Supabase 대역(httpx.MockTransport)을 넣을 때만 사용."""
    s = settings or Settings.from_env()
    s.validate()
    sb = None
    if s.data_source == "supabase":
        from .supabase_api import Supabase, router

        sb = Supabase(s, transport)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if sb:
            await sb.aclose()

    app = FastAPI(title="PPE Dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    csp = _csp(s)
    config_js = "window.PPE_LOCAL_CONFIG = " + json.dumps(_client_config(s), ensure_ascii=False) + ";\n"
    token = s.access_token.encode()

    def _valid(value):
        return bool(value) and hmac.compare_digest(value.encode(), token)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        resp = await _authorize(request, call_next)
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"  # 토큰·Signed URL 이 Referer 로 새지 않게
        resp.headers["Content-Security-Policy"] = csp
        return resp

    async def _authorize(request, call_next):
        if not token or request.url.path == "/healthz":
            return await call_next(request)
        if request.url.path == "/" and "token" in request.query_params:
            if not _valid(request.query_params["token"]):
                return HTMLResponse(_DENIED_HTML, status_code=401)
            resp = RedirectResponse("/", status_code=303)  # 주소창·기록에서 토큰 제거
            resp.set_cookie(COOKIE, s.access_token, max_age=COOKIE_MAX_AGE_S, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")
            return resp
        bearer = request.headers.get("authorization", "")
        if _valid(request.cookies.get(COOKIE, "")) or (bearer.startswith("Bearer ") and _valid(bearer[7:])):
            return await call_next(request)
        if request.url.path == "/":
            return HTMLResponse(_DENIED_HTML, status_code=401)
        return JSONResponse({"detail": "접근 토큰이 필요합니다"}, status_code=401)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(DASHBOARD_DIR / "index.html")

    # StaticFiles 마운트보다 먼저 등록해야 디스크 파일 대신 이 응답이 나간다
    @app.get("/js/config.local.js", include_in_schema=False)
    def config_local():
        return Response(config_js, media_type="text/javascript; charset=utf-8")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "data_source": s.data_source}

    if sb:
        app.include_router(router(sb))
    app.mount("/css", StaticFiles(directory=DASHBOARD_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=DASHBOARD_DIR / "js"), name="js")
    app.state.settings = s
    return app
