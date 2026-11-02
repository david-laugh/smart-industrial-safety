"""DASHBOARD_DATA_SOURCE=supabase: 대시보드가 Supabase 에 보내는 실제 요청 형식과 화면 표시를 확인한다.

supabase-js(CDN)는 실제로 내려받고, https://dev.supabase.test 로 가는 요청만 tests/fake_supabase.py 가 응답한다.
"""

import re
from urllib.parse import unquote

import pytest

from tests.fake_supabase import ANON_KEY, FAKE_URL, FakeSupabase


@pytest.fixture
def fake():
    return FakeSupabase()


@pytest.fixture
def sb_page(browser, supabase_base_url, fake):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR", timezone_id="Asia/Seoul")
    pg = ctx.new_page()
    pg.js_errors = []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and pg.js_errors.append(m.text))
    pg.route(f"{FAKE_URL}/**", fake.handle)
    pg.goto(supabase_base_url)
    pg.wait_for_selector("#events-body tr", timeout=20000)
    yield pg
    ctx.close()


def _selects(fake):
    return [(dict(q), h) for m, path, q, h in fake.requests if m == "GET" and path == "/rest/v1/ppe_events"]


def test_list_query_follows_contract(sb_page, fake):
    selects = _selects(fake)
    assert selects, "ppe_events 조회가 없었습니다"
    page_q, page_h = next((q, h) for q, h in selects if q.get("select") == "*")
    assert page_q["order"] == "occurred_at.desc"
    assert page_q["occurred_at"].startswith("gte.") and page_q["occurred_at"].endswith("Z")
    assert (page_q["offset"], page_q["limit"]) == ("0", "20")
    assert "count=exact" in page_h["prefer"]
    # 토큰으로 요청(로그인 세션 없음): apikey 와 Bearer 모두 anon 토큰
    assert page_h["apikey"] == ANON_KEY and page_h["authorization"] == f"Bearer {ANON_KEY}"
    # 요약/차트는 경량 컬럼만
    assert any(q.get("select") == "occurred_at,device_id,violation_types,violation_count" for q, _ in selects)


def test_list_shows_supabase_rows(sb_page, fake):
    week = [r for r in fake.rows if r["occurred_at"] >= _query_from(fake)]
    m = re.search(r"총 ([\d,]+)건", sb_page.inner_text("#list-meta"))
    assert int(m.group(1)) == len(week)
    newest = max(week, key=lambda r: r["occurred_at"])
    assert sb_page.locator("#events-body tr").first.get_attribute("data-id") == newest["event_id"]
    assert not sb_page.js_errors


def _query_from(fake):
    q, _ = next((q, h) for q, h in _selects(fake) if q.get("select") == "*")
    return q["occurred_at"].removeprefix("gte.")


def test_filters_become_postgrest_operators(sb_page, fake):
    before = sb_page.inner_text("#list-meta")
    sb_page.check("#type-chips input[value='HELMET_MISSING']")
    sb_page.wait_for_function("(b) => document.getElementById('list-meta').innerText !== b", arg=before)
    q = [s for s, _ in _selects(fake) if s.get("select") == "*"][-1]
    assert unquote(q["violation_types"]) == "ov.{HELMET_MISSING}"

    before = sb_page.inner_text("#list-meta")
    sb_page.click("#device-summary")
    device = sb_page.locator("#device-options input").first.get_attribute("value")
    sb_page.locator("#device-options input").first.check()
    sb_page.wait_for_function("(b) => document.getElementById('list-meta').innerText !== b", arg=before)
    q = [s for s, _ in _selects(fake) if s.get("select") == "*"][-1]
    assert unquote(q["device_id"]).strip('"') in (f"in.({device})", f'in.("{device}")')
    assert set(sb_page.locator("#events-body tr td:nth-child(2)").all_inner_texts()) == {device}


def test_detail_uses_signed_url_from_storage(sb_page, fake):
    row = sb_page.locator("#events-body tr").first
    event_id = row.get_attribute("data-id")
    row.click()
    sb_page.wait_for_selector("#evidence-img:not([hidden])")
    target = next(r for r in fake.rows if r["event_id"] == event_id)
    signs = [p for m, p, _, _ in fake.requests if m == "POST" and p.startswith("/storage/v1/object/sign/")]
    assert signs[-1] == f"/storage/v1/object/sign/ppe-evidence/{target['storage_path']}"
    src = sb_page.get_attribute("#evidence-img", "src")
    assert src.startswith(f"{FAKE_URL}/storage/v1/object/sign/ppe-evidence/") and "token=" in src
    assert sb_page.locator(".bbox").count() == target["violation_count"]


def test_deep_link_fetches_single_event(sb_page, fake):
    oldest = min(fake.rows, key=lambda r: r["occurred_at"])  # 첫 페이지에 없는 행
    sb_page.goto(sb_page.url.split("#")[0] + "#event=" + oldest["event_id"])
    sb_page.wait_for_selector("#evidence-img:not([hidden])")
    assert any(dict(q).get("event_id") == f"eq.{oldest['event_id']}" for m, p, q, _ in fake.requests if p == "/rest/v1/ppe_events")
