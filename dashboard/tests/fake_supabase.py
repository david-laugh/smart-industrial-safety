"""테스트용 Supabase 대역: PostgREST(select) + Storage(sign, 서명된 객체 GET)를 seed 가데이터로 응답한다.

- 대시보드 서버 → Supabase: httpx.MockTransport(fake.handle) 로 붙여, 서버가 보내는 요청(필터·정렬·페이지·count·secret 키 헤더)을 검증한다.
- 브라우저 → Storage(서명된 이미지 URL): Playwright route(fake.serve_image) 로 응답한다.
"""

import json
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

from seed.generate import generate

FAKE_URL = "https://dev.supabase.test"
SERVICE_KEY = "sb_secret_test_only_not_real"
ACCESS_TOKEN = "test-dashboard-token-0123456789"


def _cell(row, col):
    if "->>" in col:  # PostgREST JSON 경로(텍스트)
        base, key = col.split("->>")
        return str(row[base][key])
    return row[col]


def _match(row, col, expr):
    op, _, val = expr.partition(".")
    cell = _cell(row, col)
    if op == "eq":
        return str(cell) == val
    if op == "gte":
        return cell >= val
    if op == "gt":
        return cell > val
    if op == "lt":
        return cell < val
    if op == "in":
        return cell in [v.strip('"') for v in val.strip("()").split(",")]
    if op == "ov":
        return bool(set(cell) & set(val.strip("{}").split(",")))
    raise AssertionError(f"지원하지 않는 필터: {col}={expr}")


def _pick(row, col):
    """select 항목: `col` 또는 `alias:base->>key`."""
    alias, _, expr = col.rpartition(":")
    return alias or expr.split("->>")[-1], _cell(row, expr)


class FakeSupabase:
    def __init__(self, count=45, days=7, seed=11):
        events = generate(count, days, seed)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # ingested_at 은 서버(DB) 생성 컬럼 — 대역에서 채운다
        self.rows = [{**r, "ingested_at": (start + timedelta(seconds=i)).isoformat()} for i, (r, _) in enumerate(events)]
        self.images = {r["storage_path"]: jpeg for r, jpeg in events}
        self.requests = []  # (method, path, query, headers)
        self.lock = threading.Lock()

    def add_row(self, row, jpeg):
        with self.lock:
            last = max(r["ingested_at"] for r in self.rows)
            ingested = (datetime.fromisoformat(last) + timedelta(seconds=1)).isoformat()
            self.rows.append({**row, "ingested_at": ingested})
            self.images[row["storage_path"]] = jpeg

    # ---- 서버 → Supabase (httpx) ----

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        query = parse_qsl(url.query, keep_blank_values=True)
        headers = {k.lower(): v for k, v in request.headers.items()}
        with self.lock:
            self.requests.append((request.method, unquote(url.path), query, headers))
        if headers.get("apikey") != SERVICE_KEY or headers.get("authorization") != f"Bearer {SERVICE_KEY}":
            return httpx.Response(401, json={"message": "Invalid API key"})
        if url.path.startswith("/storage/v1/object/sign/") and request.method == "POST":
            path = unquote(url.path.split("/storage/v1/object/sign/ppe-evidence/", 1)[1])
            if path not in self.images:
                return httpx.Response(400, json={"statusCode": "404", "error": "not_found"})
            assert json.loads(request.content)["expiresIn"] > 0
            return httpx.Response(200, json={"signedURL": f"/object/sign/ppe-evidence/{path}?token=t"})
        if url.path == "/rest/v1/ppe_events" and request.method == "GET":
            return self._select(query, headers)
        return httpx.Response(404, json={"message": url.path})

    def _select(self, query, headers):
        with self.lock:
            rows = list(self.rows)
        cols, order, limit, offset = "*", None, None, 0
        for k, v in query:
            if k == "select":
                cols = v
            elif k == "order":
                order = v
            elif k == "limit":
                limit = int(v)
            elif k == "offset":
                offset = int(v)
            else:
                rows = [r for r in rows if _match(r, k, v)]
        if order:
            col, _, direction = order.partition(".")
            rows = sorted(rows, key=lambda r: r[col], reverse=direction.startswith("desc"))
        total = len(rows)
        page = rows[offset : offset + limit] if limit is not None else rows[offset:]
        if cols != "*":
            page = [dict(_pick(r, c) for c in cols.split(",")) for r in page]
        h = {"content-type": "application/json"}
        if "count=exact" in headers.get("prefer", ""):
            h["content-range"] = f"{offset}-{offset + max(len(page) - 1, 0)}/{total}" if page else f"*/{total}"
        return httpx.Response(200, content=json.dumps(page), headers=h)

    # ---- 브라우저 → Storage 서명 URL (Playwright route) ----

    def serve_image(self, route, request):
        url = urlsplit(request.url)
        with self.lock:
            self.requests.append((request.method, unquote(url.path), parse_qsl(url.query), {"via": "browser"}))
        if url.path.startswith("/storage/v1/object/sign/ppe-evidence/") and "token=" in url.query:
            path = unquote(url.path.split("/storage/v1/object/sign/ppe-evidence/", 1)[1])
            if path in self.images:
                return route.fulfill(status=200, body=self.images[path], headers={"content-type": "image/jpeg"})
        return route.fulfill(status=400, json={"statusCode": "403"})
