"""대시보드 테스트 공용 fixture: FastAPI 서버, 헤드리스 브라우저, 계약 스키마 레지스트리."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from referencing import Registry, Resource

DASHBOARD_DIR = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = DASHBOARD_DIR.parent / "contracts"
sys.path.insert(0, str(DASHBOARD_DIR))

from server.main import Settings, create_app  # noqa: E402


def _serve(app):
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn 이 시작되지 않았습니다")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, thread, f"http://127.0.0.1:{port}/"


@pytest.fixture(scope="session")
def base_url():
    """샘플 데이터 FastAPI 앱을 임의 포트의 uvicorn으로 띄운다(로컬 .env 영향 없음)."""
    server, thread, url = _serve(create_app(Settings()))
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def fake_supabase():
    from tests.fake_supabase import FakeSupabase

    return FakeSupabase()


@pytest.fixture(scope="session")
def supabase_base_url(fake_supabase):
    """DASHBOARD_DATA_SOURCE=supabase 로 띄운 앱. 서버 → Supabase 요청은 tests/fake_supabase.py 대역이 응답."""
    import httpx

    from tests.fake_supabase import ACCESS_TOKEN, FAKE_URL, SERVICE_KEY

    s = Settings(data_source="supabase", supabase_url=FAKE_URL, service_key=SERVICE_KEY, access_token=ACCESS_TOKEN,
                 live_interval_s=1)
    server, thread, url = _serve(create_app(s, transport=httpx.MockTransport(fake_supabase.handle)))
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    """목록이 그려진 새 페이지. JS 예외와 콘솔 오류(CSP 위반, 404 포함)는 page.js_errors 에 쌓인다."""
    context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR", timezone_id="Asia/Seoul")
    pg = context.new_page()
    pg.js_errors = []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and pg.js_errors.append(m.text))
    pg.goto(base_url)
    pg.wait_for_selector("#events-body tr")
    yield pg
    context.close()


@pytest.fixture(scope="session")
def schemas():
    """contracts/*.schema.json 을 $id 로 등록한 레지스트리와 이름별 스키마."""
    by_name = {}
    resources = []
    for path in CONTRACTS_DIR.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        by_name[path.name.removesuffix(".schema.json")] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources), by_name
