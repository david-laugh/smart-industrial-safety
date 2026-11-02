"""FastAPI 서버(server/main.py): 서빙 범위, 설정 주입, 키 검증, 보안 헤더."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from server.main import Settings, create_app

SUPA = dict(data_source="supabase", supabase_url="https://abc.supabase.co")


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


def test_supabase_config_is_injected():
    key = _jwt("anon")
    c = TestClient(create_app(Settings(**SUPA, supabase_anon_key=key, signed_url_expires_s=60)))
    cfg = _config_from(c)
    assert cfg["dataSource"] == "supabase"
    assert cfg["supabaseUrl"] == "https://abc.supabase.co"
    assert cfg["supabaseAnonKey"] == key
    assert cfg["signedUrlExpiresSec"] == 60
    assert "https://abc.supabase.co" in c.get("/").headers["content-security-policy"]


def test_publishable_key_is_allowed():
    create_app(Settings(**SUPA, supabase_anon_key="sb_publishable_abc"))


@pytest.mark.parametrize("key", [_jwt("service_role"), "sb_secret_abc"])
def test_privileged_keys_are_rejected(key):
    with pytest.raises(ValueError, match="anon 키만"):
        create_app(Settings(**SUPA, supabase_anon_key=key))


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(data_source="firebase"),
        dict(data_source="supabase"),
        dict(data_source="supabase", supabase_url="http://abc.supabase.co", supabase_anon_key="sb_publishable_x"),
    ],
)
def test_invalid_settings_fail_fast(kwargs):
    with pytest.raises(ValueError):
        create_app(Settings(**kwargs))


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
            "SUPABASE_ANON_KEY": "sb_publishable_x",
        }
    )
    assert (s.bind_addr, s.port, s.reload, s.data_source) == ("0.0.0.0", 9000, True, "supabase")
    assert s.supabase_url == "https://abc.supabase.co"
    assert Settings.from_env({}) == Settings()
