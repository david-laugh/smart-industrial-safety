"""대시보드 테스트 공용 fixture: 정적 서버, 헤드리스 브라우저, 계약 스키마 레지스트리."""

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from referencing import Registry, Resource

DASHBOARD_DIR = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = DASHBOARD_DIR.parent / "contracts"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def base_url():
    handler = functools.partial(_QuietHandler, directory=str(DASHBOARD_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    """로그인 전 상태의 새 페이지. JS 예외는 page.js_errors 에 쌓인다."""
    context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR", timezone_id="Asia/Seoul")
    pg = context.new_page()
    pg.js_errors = []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    pg.goto(base_url)
    yield pg
    context.close()


@pytest.fixture
def logged_in(page):
    page.fill("#login-email", "demo@example.test")
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#events-body tr")
    return page


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
