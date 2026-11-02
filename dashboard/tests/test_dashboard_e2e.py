"""헤드리스 Chromium으로 대시보드 화면 흐름을 확인한다(샘플 모드)."""

import re


def _meta_total(page):
    m = re.search(r"총 ([\d,]+)건", page.inner_text("#list-meta"))
    return int(m.group(1).replace(",", ""))


def _wait_list_change(page, before):
    page.wait_for_function("(b) => document.getElementById('list-meta').innerText !== b", arg=before)


def test_opens_directly_with_list_kpis_and_chart(page):
    assert page.locator("#events-body tr").count() == 20
    assert page.locator("#kpis .kpi").count() == 5
    assert page.locator("#chart .hit").count() == 7  # 기본 7일 → 일별 7칸
    assert page.locator("input[type=password], input[type=email]").count() == 0
    assert not page.js_errors


def test_no_sample_or_mock_marks_on_screen(page):
    page.locator("#events-body tr").first.click()
    page.wait_for_selector("#evidence-img:not([hidden])")
    text = page.inner_text("body").lower()
    assert not any(w in text for w in ("mock", "목업", "샘플", "sample", "demo"))
    assert "mock" not in page.get_attribute("#evidence-img", "src").lower()


def test_violation_type_filter(page):
    page.click("[data-range='30d']")
    page.wait_for_function("() => document.getElementById('list-meta').innerText.includes('총')")
    before = page.inner_text("#list-meta")
    page.check("#type-chips input[value='VEST_MISSING']")
    _wait_list_change(page, before)
    cells = page.locator("#events-body tr td:nth-child(3)").all_inner_texts()
    assert cells and all("안전조끼 미착용" in c for c in cells)


def test_device_filter(page):
    before = page.inner_text("#list-meta")
    page.click("#device-summary")
    first = page.locator("#device-options input").first
    device = first.get_attribute("value")
    first.check()
    _wait_list_change(page, before)
    assert page.inner_text("#device-summary") == device
    assert set(page.locator("#events-body tr td:nth-child(2)").all_inner_texts()) == {device}


def test_24h_range_uses_hourly_buckets(page):
    page.click("[data-range='24h']")
    page.wait_for_function("() => document.getElementById('chart-title').innerText.startsWith('시간별')")
    assert page.locator("#chart .hit").count() == 24


def test_pagination(page):
    total = _meta_total(page)
    assert page.inner_text("#page-info").startswith("1 /")
    if total > 20:
        before = page.inner_text("#list-meta")
        page.click("#next-page")
        _wait_list_change(page, before)
        assert page.inner_text("#page-info").startswith("2 /")


def test_detail_overlay_and_deep_link(page):
    row = page.locator("#events-body tr").first
    people = int(row.locator("td").nth(3).inner_text())
    row.click()
    page.wait_for_selector("#evidence-img:not([hidden])")
    assert page.locator(".bbox:not(.other)").count() == people
    assert page.locator(".person").count() == people
    assert "Signed URL 만료" in page.inner_text("#signed-meta")

    url = page.url
    assert re.search(r"#event=[0-9a-f-]{36}$", url)
    page.reload()
    page.wait_for_selector("#evidence-img:not([hidden])")
    assert page.url == url

    page.uncheck("#bbox-toggle")
    assert page.locator(".bbox:visible").count() == 0
    page.keyboard.press("Escape")
    assert page.is_hidden("#detail")
    assert not page.js_errors


def test_live_mode_announces_new_events(page):
    page.evaluate("PPE.config.sample.liveIntervalMs = 200")
    before = _meta_total(page)
    page.check("#live-toggle")
    page.wait_for_selector("#new-events:not([hidden])")
    page.uncheck("#live-toggle")
    added = int(page.inner_text("#new-count"))
    page.click("#show-new")
    page.wait_for_function("(n) => /총 ([\\d,]+)건/.exec(document.getElementById('list-meta').innerText)[1] == n", arg=str(before + added))
    assert page.locator("#events-body tr.fresh").count() >= 1


def test_mobile_has_no_horizontal_scroll(browser, base_url):
    ctx = browser.new_context(viewport={"width": 375, "height": 812}, locale="ko-KR")
    page = ctx.new_page()
    page.goto(base_url)
    page.wait_for_selector("#events-body tr")
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    ctx.close()


def test_same_frame_events_are_linked(page):
    pair = page.evaluate(
        """() => {
          const rows = PPE.sampleData.generate(PPE.config.sample);
          const by = {};
          for (const r of rows) (by[r.event_payload.frame_id] ||= []).push(r.event_id);
          return Object.values(by).find((ids) => ids.length > 1);
        }"""
    )
    page.goto(page.url.split("#")[0] + "#event=" + pair[0])
    page.wait_for_selector("#frame-siblings:not([hidden])")
    assert page.locator(".bbox.other").count() == len(pair) - 1
    page.locator(f"#sibling-list a[href='#event={pair[1]}']").click()
    page.wait_for_function("(id) => location.hash === '#event=' + id", arg=pair[1])
    page.wait_for_selector("#frame-siblings:not([hidden])")
    assert not page.js_errors
