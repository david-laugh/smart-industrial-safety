"""테스트용 Supabase 대역: PostgREST(select) + Storage(sign, 객체 GET)를 seed 가데이터로 응답한다.

Playwright route 핸들러로 붙여, 대시보드가 실제로 보내는 요청 형식(필터·정렬·페이지·count·토큰 헤더)을 검증한다.
"""

import json
from urllib.parse import parse_qsl, unquote, urlsplit

from seed.generate import generate

FAKE_URL = "https://dev.supabase.test"
ANON_KEY = "sb_publishable_test"


def _match(row, col, expr):
    op, _, val = expr.partition(".")
    cell = row[col]
    if op == "eq":
        return str(cell) == val
    if op == "gte":
        return cell >= val
    if op == "lt":
        return cell < val
    if op == "in":
        return cell in [v.strip('"') for v in val.strip("()").split(",")]
    if op == "ov":
        return bool(set(cell) & set(val.strip("{}").split(",")))
    raise AssertionError(f"지원하지 않는 필터: {col}={expr}")


class FakeSupabase:
    def __init__(self, count=45, days=7, seed=11):
        events = generate(count, days, seed)
        self.rows = [r for r, _ in events]
        self.images = {r["storage_path"]: jpeg for r, jpeg in events}
        self.requests = []  # (method, path, query, headers)

    def handle(self, route, request):
        url = urlsplit(request.url)
        query = parse_qsl(url.query, keep_blank_values=True)
        headers = {k.lower(): v for k, v in request.headers.items()}
        self.requests.append((request.method, url.path, query, headers))
        cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-expose-headers": "*",
                "access-control-allow-methods": "GET,POST,OPTIONS"}
        if request.method == "OPTIONS":
            return route.fulfill(status=204, headers=cors)
        if url.path.startswith("/storage/v1/object/sign/") and request.method == "GET":
            path = unquote(url.path.split("/storage/v1/object/sign/ppe-evidence/", 1)[1])
            return route.fulfill(status=200, body=self.images[path], headers={**cors, "content-type": "image/jpeg"})
        if headers.get("apikey") != ANON_KEY:
            return route.fulfill(status=401, json={"message": "missing token"}, headers=cors)
        if url.path.startswith("/storage/v1/object/sign/"):
            path = unquote(url.path.split("/storage/v1/object/sign/ppe-evidence/", 1)[1])
            if path not in self.images:
                return route.fulfill(status=404, json={"message": "not found"}, headers=cors)
            return route.fulfill(json={"signedURL": f"/object/sign/ppe-evidence/{path}?token=t"}, headers=cors)
        if url.path == "/rest/v1/ppe_events":
            return self._select(route, query, headers, cors)
        return route.fulfill(status=404, json={"message": url.path}, headers=cors)

    def _select(self, route, query, headers, cors):
        rows = self.rows
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
            keep = cols.split(",")
            page = [{c: r[c] for c in keep} for r in page]
        h = {**cors, "content-type": "application/json"}
        if "count=exact" in headers.get("prefer", ""):
            h["content-range"] = f"{offset}-{offset + max(len(page) - 1, 0)}/{total}"
        if "vnd.pgrst.object" in headers.get("accept", ""):  # .single()/.maybeSingle()
            if not page:
                return route.fulfill(status=406, json={"code": "PGRST116", "message": "0 rows"}, headers=h)
            return route.fulfill(status=200, body=json.dumps(page[0]), headers=h)
        return route.fulfill(status=200, body=json.dumps(page), headers=h)
