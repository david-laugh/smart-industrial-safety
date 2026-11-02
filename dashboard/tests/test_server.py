"""FastAPI 서버(server/main.py): 서빙 범위, 설정 주입, 키 검증, 보안 헤더."""

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server.main import Settings, create_app

SECRET = "sb_secret_unit_test_value"
TOKEN = "unit-test-access-token-123"
SUPA = dict(data_source="supabase", supabase_url="https://abc.supabase.co", service_key=SECRET, access_token=TOKEN)


def _jwt(role):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'HS256'})}.{enc({'role': role, 'iss': 'supabase'})}.sig"


def _config_from(client):
    body = client.get("/js/config.local.js").text
    assert body.startswith("window.PPE_LOCAL_CONFIG = ")
    return json.loads(body.removeprefix("window.PPE_LOCAL_CONFIG = ").rstrip().rstrip(";"))


@pytest.fixture
def client():
    return TestClient(create_app(Settings()))


def test_serves_index_and_assets(client):
    r = client.get("/")
    assert r.status_code == 200 and "PPE 이벤트 대시보드" in r.text
    assert client.get("/css/style.css").status_code == 200
    assert client.get("/js/app.js").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok", "data_source": "sample"}


@pytest.mark.parametrize(
    "path",
    ["/.env", "/.env.example", "/requirements.txt", "/tests/conftest.py", "/server/main.py", "/README.md", "/js/../server/main.py", "/docs", "/openapi.json"],
)
def test_does_not_expose_other_files(client, path):
    assert client.get(path).status_code == 404


def test_sample_config_has_no_supabase_values(client):
    assert _config_from(client) == {"dataSource": "sample"}


def test_supabase_config_has_no_secrets():
    c = TestClient(create_app(Settings(**SUPA, signed_url_expires_s=60)))
    c.get(f"/?token={TOKEN}")
    body = c.get("/js/config.local.js").text
    assert SECRET not in body and TOKEN not in body
    assert _config_from(c) == {"dataSource": "supabase", "signedUrlExpiresSec": 60, "liveIntervalMs": 15000}
    csp = c.get("/").headers["content-security-policy"]
    assert "img-src 'self' data: blob: https://abc.supabase.co" in csp
    assert "connect-src 'self';" in csp and "jsdelivr" not in csp  # 브라우저는 Supabase API 에 직접 접속하지 않음
    assert SECRET not in repr(Settings(**SUPA))


@pytest.mark.parametrize("key", [_jwt("anon"), "sb_publishable_abc"])
def test_public_keys_are_rejected_as_service_key(key):
    with pytest.raises(ValueError, match="secret"):
        create_app(Settings(**{**SUPA, "service_key": key}))


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(data_source="firebase"),
        dict(data_source="supabase"),
        dict(**{**SUPA, "supabase_url": "http://abc.supabase.co"}),
        dict(**{**SUPA, "access_token": ""}),  # supabase 모드는 토큰 필수
        dict(**{**SUPA, "access_token": "short"}),
        dict(**{**SUPA, "service_key": ""}),
    ],
)
def test_invalid_settings_fail_fast(kwargs):
    with pytest.raises(ValueError):
        create_app(Settings(**kwargs))


def test_token_required_and_exchanged_for_cookie():
    c = TestClient(create_app(Settings(**SUPA)), follow_redirects=False)
    assert c.get("/").status_code == 401
    assert c.get("/api/devices").status_code == 401
    assert c.get("/js/config.local.js").status_code == 401
    assert c.get("/?token=wrong-token-0000000000").status_code == 401
    assert c.get("/healthz").status_code == 200

    r = c.get(f"/?token={TOKEN}")
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/").status_code == 200


def test_bearer_token_for_api_clients():
    c = TestClient(create_app(Settings(**SUPA)))
    assert c.get("/js/config.local.js", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert c.get("/js/config.local.js", headers={"Authorization": "Bearer nope"}).status_code == 401


@pytest.mark.parametrize(
    "path",
    ["/api/events?from=2026-11-02", "/api/events?device=CAM_1", "/api/events?type=GLOVES_MISSING", "/api/events?limit=1000",
     "/api/events/not-a-uuid", "/api/frames/1;drop", "/api/live?after=yesterday"],
)
def test_api_rejects_malformed_input_before_supabase(path):
    calls = []
    transport = httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(200, json=[]))
    c = TestClient(create_app(Settings(**SUPA), transport=transport), headers={"Authorization": f"Bearer {TOKEN}"})
    assert c.get(path).status_code in (400, 422)
    assert calls == []


def test_supabase_auth_failure_is_reported_without_key():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, json={"message": "Invalid API key"}))
    c = TestClient(create_app(Settings(**SUPA), transport=transport), headers={"Authorization": f"Bearer {TOKEN}"})
    r = c.get("/api/devices")
    assert r.status_code == 502 and "secret 키" in r.json()["detail"] and SECRET not in r.text


def test_sample_mode_token_is_optional_but_enforced_when_set():
    assert TestClient(create_app(Settings())).get("/").status_code == 200
    assert TestClient(create_app(Settings(access_token=TOKEN))).get("/").status_code == 401


def test_security_headers(client):
    h = client.get("/").headers
    assert h["cache-control"] == "no-cache"
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_settings_from_env():
    s = Settings.from_env(
        {
            "DASHBOARD_BIND_ADDR": "0.0.0.0",
            "DASHBOARD_PORT": "9000",
            "DASHBOARD_RELOAD": "true",
            "DASHBOARD_DATA_SOURCE": "SUPABASE",
            "SUPABASE_URL": "https://abc.supabase.co/",
            "SUPABASE_SERVICE_KEY": SECRET,
            "DASHBOARD_ACCESS_TOKEN": TOKEN,
        }
    )
    assert (s.bind_addr, s.port, s.reload, s.data_source) == ("0.0.0.0", 9000, True, "supabase")
    assert s.supabase_url == "https://abc.supabase.co"
    assert (s.service_key, s.access_token) == (SECRET, TOKEN)
    assert Settings.from_env({}) == Settings()
