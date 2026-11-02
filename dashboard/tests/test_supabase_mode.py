"""DASHBOARD_DATA_SOURCE=supabase: 브라우저 → 대시보드 서버(/api, 토큰) → Supabase(secret 키) 경로를 확인한다.

서버 → Supabase 요청은 tests/fake_supabase.py 가 httpx 대역으로 응답하고,
브라우저가 Storage 로 직접 가는 요청은 서명된 이미지 URL 뿐이어야 한다.
"""

import re

import pytest

from seed.generate import generate
from tests.fake_supabase import ACCESS_TOKEN, FAKE_URL, SERVICE_KEY


@pytest.fixture
def fake(fake_supabase):
    fake_supabase.requests.clear()
    return fake_supabase


@pytest.fixture
def sb_page(browser, supabase_base_url, fake):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR", timezone_id="Asia/Seoul")
    pg = ctx.new_page()
    pg.js_errors = []
    pg.browser_urls = []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and pg.js_errors.append(m.text))
    pg.on("request", lambda r: pg.browser_urls.append(r.url))
    pg.route(f"{FAKE_URL}/**", fake.serve_image)
    pg.goto(f"{supabase_base_url}?token={ACCESS_TOKEN}")
    pg.wait_for_selector("#events-body tr", timeout=20000)
    yield pg
    ctx.close()


def _selects(fake):
    return [(dict(q), h) for m, path, q, h in fake.requests if m == "GET" and path == "/rest/v1/ppe_events"]


def _last_list_query(fake):
    return [q for q, _ in _selects(fake) if q.get("select") == "*" and "offset" in q][-1]


def test_server_queries_supabase_with_secret_key(sb_page, fake):
    q, h = next((q, h) for q, h in _selects(fake) if q.get("select") == "*" and "offset" in q)
    assert q["order"] == "occurred_at.desc"
    assert q["occurred_at"].startswith("gte.") and q["occurred_at"].endswith("Z")
    assert (q["offset"], q["limit"]) == ("0", "20")
    assert "count=exact" in h["prefer"]
    assert h["apikey"] == SERVICE_KEY and h["authorization"] == f"Bearer {SERVICE_KEY}"
    assert any(q.get("select") == "occurred_at,device_id,violation_types,violation_count,frame_id:event_payload->>frame_id" for q, _ in _selects(fake))


def test_browser_never_sees_key_or_talks_to_postgrest(sb_page, fake):
    assert sb_page.url.endswith("/") and "token=" not in sb_page.url  # 주소에서 토큰 제거
    cookies = {c["name"]: c for c in sb_page.context.cookies()}
    assert cookies["ppe_dashboard_token"]["httpOnly"]
    assert SERVICE_KEY not in sb_page.content()
    assert SERVICE_KEY not in sb_page.evaluate("() => JSON.stringify(window.PPE.config)")
    assert not [u for u in sb_page.browser_urls if u.startswith(FAKE_URL) and "/storage/v1/object/sign/" not in u]


def test_list_shows_supabase_rows(sb_page, fake):
    since = _last_list_query(fake)["occurred_at"].removeprefix("gte.")
    week = [r for r in fake.rows if r["occurred_at"] >= since]
    m = re.search(r"총 ([\d,]+)건", sb_page.inner_text("#list-meta"))
    assert int(m.group(1)) == len(week)
    newest = max(week, key=lambda r: r["occurred_at"])
    assert sb_page.locator("#events-body tr").first.get_attribute("data-id") == newest["event_id"]
    assert not sb_page.js_errors


def test_filters_become_postgrest_operators(sb_page, fake):
    before = sb_page.inner_text("#list-meta")
    sb_page.check("#type-chips input[value='HELMET_MISSING']")
    sb_page.wait_for_function("(b) => document.getElementById('list-meta').innerText !== b", arg=before)
    assert _last_list_query(fake)["violation_types"] == "ov.{HELMET_MISSING}"

    before = sb_page.inner_text("#list-meta")
    sb_page.click("#device-summary")
    device = sb_page.locator("#device-options input").first.get_attribute("value")
    sb_page.locator("#device-options input").first.check()
    sb_page.wait_for_function("(b) => document.getElementById('list-meta').innerText !== b", arg=before)
    assert _last_list_query(fake)["device_id"] == f"in.({device})"
    assert set(sb_page.locator("#events-body tr td:nth-child(2)").all_inner_texts()) == {device}


def test_detail_image_is_signed_by_server(sb_page, fake):
    row = sb_page.locator("#events-body tr").first
    event_id = row.get_attribute("data-id")
    row.click()
    sb_page.wait_for_selector("#evidence-img:not([hidden])")
    target = next(r for r in fake.rows if r["event_id"] == event_id)
    signs = [p for m, p, _, h in fake.requests if m == "POST" and p.startswith("/storage/v1/object/sign/")]
    assert signs[-1] == f"/storage/v1/object/sign/ppe-evidence/{target['storage_path']}"
    src = sb_page.get_attribute("#evidence-img", "src")
    assert src.startswith(f"{FAKE_URL}/storage/v1/object/sign/ppe-evidence/") and "token=" in src
    assert sb_page.locator(".bbox:not(.other)").count() == target["violation_count"]


def test_deep_link_fetches_single_event(sb_page, fake):
    oldest = min(fake.rows, key=lambda r: r["occurred_at"])  # 첫 페이지에 없는 행
    sb_page.goto(sb_page.url.split("#")[0] + "#event=" + oldest["event_id"])
    sb_page.wait_for_selector("#evidence-img:not([hidden])")
    assert any(dict(q).get("event_id") == f"eq.{oldest['event_id']}" for m, p, q, _ in fake.requests if p == "/rest/v1/ppe_events")


def test_same_frame_query_uses_json_path(sb_page, fake):
    row = sb_page.locator("#events-body tr").first
    event_id = row.get_attribute("data-id")
    row.click()
    sb_page.wait_for_selector("#evidence-img:not([hidden])")
    frame_id = next(r for r in fake.rows if r["event_id"] == event_id)["event_payload"]["frame_id"]
    sent = lambda: any(dict(q).get("event_payload->>frame_id") == f"eq.{frame_id}" for m, p, q, _ in fake.requests if p == "/rest/v1/ppe_events")  # noqa: E731
    for _ in range(50):
        if sent():
            break
        sb_page.wait_for_timeout(100)
    assert sent()


def test_live_polling_shows_new_rows(sb_page, fake):
    sb_page.check("#live-toggle")
    for _ in range(50):  # 첫 폴링으로 커서 확보
        if any(p == "/rest/v1/ppe_events" and dict(q).get("select") == "ingested_at" for m, p, q, _ in fake.requests):
            break
        sb_page.wait_for_timeout(100)
    row, jpeg = generate(1, 0.01, seed=99)[0]
    fake.add_row(row, jpeg)
    sb_page.wait_for_selector("#new-events:not([hidden])", timeout=10000)
    assert any(dict(q).get("ingested_at", "").startswith("gt.") for m, p, q, _ in fake.requests)
