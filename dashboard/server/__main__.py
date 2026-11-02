"""`python -m server` (dashboard/ 에서 실행) — .env/환경변수의 DASHBOARD_* 설정으로 uvicorn 기동.

`python -m server check` — .env 의 SUPABASE_URL + SUPABASE_SERVICE_KEY 로 ppe_events·ppe-evidence 에 실제로 접근되는지만 확인.
"""

import sys

import httpx
import uvicorn

from .main import DASHBOARD_DIR, Settings


def check():
    s = Settings.from_env()
    if not s.supabase_url or not s.service_key:
        print(".env 에 SUPABASE_URL 과 SUPABASE_SERVICE_KEY 를 넣으세요.")
        return 2
    h = {"apikey": s.service_key, "Authorization": f"Bearer {s.service_key}"}
    ok = True
    with httpx.Client(base_url=s.supabase_url, headers=h, timeout=15) as c:
        for name, method, path, kw in [
            (f"테이블 {s.events_table}", "GET", f"/rest/v1/{s.events_table}", {"params": {"select": "event_id", "limit": "1"}, "headers": {"Prefer": "count=exact"}}),
            (f"버킷 {s.storage_bucket}", "GET", f"/storage/v1/bucket/{s.storage_bucket}", {}),
        ]:
            try:
                r = c.request(method, path, **kw)
            except httpx.HTTPError as e:
                print(f"[실패] {name}: 연결 오류 {type(e).__name__}")
                ok = False
                continue
            if r.is_success:
                extra = f" (행 {r.headers.get('content-range', '?').split('/')[-1]}건)" if "rest" in path else ""
                print(f"[성공] {name}: 접근 가능{extra}")
            else:
                ok = False
                code = r.status_code
                try:  # Storage 는 400 + {"statusCode": "403"} 형식으로 준다
                    code = int(r.json().get("statusCode", code))
                except (ValueError, AttributeError):
                    pass
                hint = {401: "키가 틀림", 403: "키가 틀리거나 secret 키가 아님", 404: "schema.sql 실행 여부 확인"}.get(code, "")
                print(f"[실패] {name}: {r.status_code} {r.text[:150]} {hint}")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ["check"]:
        sys.exit(check())
    s = Settings.from_env()
    s.validate()
    print(f"PPE dashboard ({s.data_source}) → http://{s.bind_addr}:{s.port}/")
    uvicorn.run(
        "server.main:create_app",
        factory=True,
        host=s.bind_addr,
        port=s.port,
        reload=s.reload,
        reload_dirs=[str(DASHBOARD_DIR)] if s.reload else None,
        log_level=s.log_level,
        app_dir=str(DASHBOARD_DIR),
    )


if __name__ == "__main__":
    main()
