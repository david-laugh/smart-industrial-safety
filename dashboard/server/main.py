"""대시보드 서버.

- `/`            index.html
- `/css`, `/js`  정적 파일 (dashboard/ 의 다른 파일 — .env, tests, .venv 등 — 은 노출하지 않음)
- `/js/config.local.js`  환경변수로 만든 런타임 설정(window.PPE_LOCAL_CONFIG)
- `/healthz`     상태 확인

대시보드는 읽기 전용이며 서버는 데이터 API를 갖지 않는다. 브라우저가 anon key로 Supabase를 직접 조회한다.
uvicorn 직접 실행: `uvicorn server.main:create_app --factory` (설정은 기동 시점의 환경변수/.env 에서 읽음)
"""

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

DASHBOARD_DIR = Path(__file__).resolve().parents[1]
SUPABASE_JS_ORIGIN = "https://cdn.jsdelivr.net"


@dataclass(frozen=True)
class Settings:
    bind_addr: str = "127.0.0.1"
    port: int = 8765
    reload: bool = False
    log_level: str = "info"
    data_source: str = "mock"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    events_table: str = "ppe_events"
    storage_bucket: str = "ppe-evidence"
    signed_url_expires_s: int = 120

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
            data_source=g("DASHBOARD_DATA_SOURCE", "mock").lower(),
            supabase_url=g("SUPABASE_URL").rstrip("/"),
            supabase_anon_key=g("SUPABASE_ANON_KEY"),
            events_table=g("SUPABASE_EVENTS_TABLE", "ppe_events"),
            storage_bucket=g("SUPABASE_STORAGE_BUCKET", "ppe-evidence"),
            signed_url_expires_s=int(g("DASHBOARD_SIGNED_URL_EXPIRES_S", "120")),
        )

    def validate(self):
        if self.data_source not in ("mock", "supabase"):
            raise ValueError(f"DASHBOARD_DATA_SOURCE 는 mock 또는 supabase 여야 합니다: {self.data_source!r}")
        if self.data_source != "supabase":
            return
        if not self.supabase_url or not self.supabase_anon_key:
            raise ValueError("DASHBOARD_DATA_SOURCE=supabase 에는 SUPABASE_URL, SUPABASE_ANON_KEY 가 필요합니다.")
        if urlsplit(self.supabase_url).scheme != "https":
            raise ValueError("SUPABASE_URL 은 https:// 여야 합니다.")
        if _is_privileged_key(self.supabase_anon_key):
            raise ValueError("SUPABASE_ANON_KEY 에 service_role/secret 키가 들어 있습니다. 대시보드에는 anon 키만 사용하세요.")


def _is_privileged_key(key):
    """service_role(JWT) 또는 sb_secret_ 키면 True. 브라우저로 내려가므로 반드시 막는다."""
    if key.startswith("sb_secret_"):
        return True
    parts = key.split(".")
    if len(parts) != 3:
        return False
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return False
    return payload.get("role") not in (None, "anon")


def _client_config(s):
    cfg = {"dataSource": s.data_source}
    if s.data_source == "supabase":
        cfg.update(
            supabaseUrl=s.supabase_url,
            supabaseAnonKey=s.supabase_anon_key,
            table=s.events_table,
            storageBucket=s.storage_bucket,
            signedUrlExpiresSec=s.signed_url_expires_s,
        )
    return cfg


def _csp(s):
    supabase = ""
    if s.data_source == "supabase":
        host = urlsplit(s.supabase_url).netloc
        supabase = f" https://{host} wss://{host}"
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' {SUPABASE_JS_ORIGIN}",
            "style-src 'self' 'unsafe-inline'",  # bbox/차트 위치용 style 속성
            f"img-src 'self' data: blob:{supabase}",
            f"connect-src 'self'{supabase}",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'self'",
        ]
    )


def create_app(settings=None):
    s = settings or Settings.from_env()
    s.validate()
    app = FastAPI(title="PPE Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    csp = _csp(s)
    config_js = "window.PPE_LOCAL_CONFIG = " + json.dumps(_client_config(s), ensure_ascii=False) + ";\n"

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"  # Signed URL 이 Referer 로 새지 않게
        resp.headers["Content-Security-Policy"] = csp
        return resp

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

    app.mount("/css", StaticFiles(directory=DASHBOARD_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=DASHBOARD_DIR / "js"), name="js")
    app.state.settings = s
    return app
