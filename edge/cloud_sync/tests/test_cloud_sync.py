"""outbox → Supabase 업로드 (docs/protocols.md §5): 순서·헤더·멱등성·재시도 분류·일시 중지·재시작.

Supabase 는 FakeSupabase(전송 함수 대역)로 대신한다. 실제 dev 프로젝트 연동은 이 테스트 범위 밖.
"""
import hashlib
import io
import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from edge.cloud_sync.config import ConfigError, SyncConfig
from edge.cloud_sync.record import to_record
from edge.cloud_sync.supabase import Response, SupabaseClient, retry_after_s
from edge.cloud_sync.worker import CloudSync, backoff_s
from edge.event_store.contracts import errors as contract_errors
from edge.event_store.ingest import SpoolIngestor
from edge.event_store.outbox import DEAD_LETTER, IMAGE_UPLOADED, PENDING, SYNCED, Outbox
from edge.event_store.tests.factory import EXAMPLE, make_event, publish

BASE = "https://dev.supabase.test"
KEY = "service-key-for-tests"


class FakeSupabase:
    """Storage(객체) + PostgREST(ppe_events) 대역. script 로 다음 응답을 강제할 수 있다."""

    def __init__(self):
        self.objects = {}
        self.rows = {}
        self.calls = []  # (method, path, headers, body_type)
        self.script = []  # [(predicate(method, path), status | Exception, headers, body)]

    def __call__(self, method, url, headers, body):
        u = urlsplit(url)
        path = unquote(u.path)
        data = body.read() if hasattr(body, "read") else body
        self.calls.append((method, path, dict(headers), type(body).__name__, parse_qs(u.query)))
        for i, (pred, status, h, b) in enumerate(self.script):
            if pred(method, path):
                del self.script[i]
                if isinstance(status, Exception):
                    raise status
                return Response(status, h, io.BytesIO(b))
        if path.startswith("/storage/v1/object/"):
            key = path.removeprefix("/storage/v1/object/")
            if method == "GET":
                return Response(200, {}, io.BytesIO(self.objects[key]))
            if key in self.objects:  # x-upsert:false → Supabase 는 400 + statusCode 409
                return Response(400, {}, io.BytesIO(b'{"statusCode":"409","error":"Duplicate","message":"The resource already exists"}'))
            self.objects[key] = data
            return Response(200, {}, io.BytesIO(json.dumps({"Key": key}).encode()))
        if path == "/rest/v1/ppe_events":
            row = json.loads(data)
            self.rows.setdefault(row["event_id"], row)  # ignore-duplicates
            return Response(201, {}, io.BytesIO(b""))
        return Response(404, {}, io.BytesIO(b"{}"))

    def fail_next(self, method, prefix, status, headers=None, body=b"{}"):
        self.script.append((lambda m, p: m == method and p.startswith(prefix), status, headers or {}, body))

    def paths(self):
        return [(m, p) for m, p, *_ in self.calls]


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def _cfg(root, **kw):
    env = {"EVENT_DATA_ROOT": str(root), "SUPABASE_URL": BASE, "SUPABASE_SERVICE_KEY": KEY, **kw}
    return SyncConfig.from_env(env)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    fake = FakeSupabase()
    outbox = Outbox(str(tmp_path / "outbox.db"), clock=clock)

    def make(**cfg_env):
        cfg = _cfg(tmp_path, **cfg_env)
        return CloudSync(cfg, outbox, SupabaseClient(BASE, KEY, fake), clock=clock, rand=lambda: 1.0)

    def add(*events):
        publish(tmp_path, *events)
        SpoolIngestor(str(tmp_path), outbox).run_once()

    yield type("Env", (), dict(root=tmp_path, clock=clock, fake=fake, outbox=outbox, make=staticmethod(make), add=staticmethod(add)))
    outbox.close()


def test_record_from_example_passes_contract():
    rec = to_record(EXAMPLE, "ppe-evidence")
    assert contract_errors("supabase-event-record", rec) == []
    assert rec["storage_path"] == f"cam-line-a-01/2026/11/02/{EXAMPLE['event_id']}.jpg"
    assert rec["event_payload"] is EXAMPLE and "ingested_at" not in rec


def test_record_from_b_replay_output_passes_contract():
    """B HANDOFF.md 의 실제 리플레이 출력 형식(위반자 1명 = 1건)."""
    ev, _ = make_event(7)
    ev["violations"][0]["violation_types"] = ["VEST_MISSING", "HELMET_MISSING"]
    rec = to_record(ev, "ppe-evidence")
    assert contract_errors("supabase-event-record", rec) == []
    assert rec["violation_types"] == ["HELMET_MISSING", "VEST_MISSING"] and rec["violation_count"] == 1


def test_image_first_then_row_with_protocol_headers(env):
    events = [make_event(i, minutes=5 - i) for i in range(3)]
    env.add(*events)
    stats = env.make().sync_once()
    assert stats["synced"] == 3
    oldest_first = [e for e, _ in reversed(events)]
    expected = []
    for ev in oldest_first:
        rec = to_record(ev, "ppe-evidence")
        expected += [("POST", f"/storage/v1/object/ppe-evidence/{rec['storage_path']}"), ("POST", "/rest/v1/ppe_events")]
    assert env.fake.paths() == expected

    img_call, row_call = env.fake.calls[0], env.fake.calls[1]
    assert img_call[2]["x-upsert"] == "false" and img_call[2]["Content-Type"] == "image/jpeg"
    assert img_call[3] == "BufferedReader"  # 파일 스트리밍(bytes 로 메모리에 올리지 않음)
    assert row_call[4] == {"on_conflict": ["event_id"]}
    assert row_call[2]["Prefer"] == "resolution=ignore-duplicates,return=minimal"
    assert img_call[2]["apikey"] == KEY and img_call[2]["Authorization"] == f"Bearer {KEY}"

    for ev, jpeg in events:
        rec = env.fake.rows[ev["event_id"]]
        assert contract_errors("supabase-event-record", rec) == []
        assert rec["event_payload"] == ev  # event_id 포함 원본 그대로
        assert env.fake.objects[f"ppe-evidence/{rec['storage_path']}"] == jpeg
        assert env.outbox.get(ev["event_id"]).state == SYNCED


def test_same_frame_events_upload_separate_objects(env):
    """위반자 1명 = 이벤트 1건: 같은 JPEG 내용이 event_id 별 객체로 각각 올라간다."""
    jpeg = b"\xff\xd8same-frame\xff\xd9"
    a, b = make_event(1, jpeg=jpeg), make_event(2, jpeg=jpeg)
    env.add(a, b)
    env.make().sync_once()
    assert len(env.fake.objects) == 2 and set(env.fake.objects.values()) == {jpeg}
    assert len(env.fake.rows) == 2


def test_existing_object_with_same_hash_counts_as_uploaded(env):
    ev, jpeg = make_event(1)
    env.add((ev, jpeg))
    env.fake.objects["ppe-evidence/" + to_record(ev, "ppe-evidence")["storage_path"]] = jpeg  # 이전 시도에서 올라감
    assert env.make().sync_once()["synced"] == 1
    assert [m for m, _ in env.fake.paths()] == ["POST", "GET", "POST"]


def test_existing_object_with_other_hash_is_dead_letter(env):
    ev, jpeg = make_event(1)
    env.add((ev, jpeg))
    env.fake.objects["ppe-evidence/" + to_record(ev, "ppe-evidence")["storage_path"]] = b"other"
    assert env.make().sync_once()["dead_letter"] == 1
    assert env.outbox.get(ev["event_id"]).state == DEAD_LETTER
    assert env.fake.rows == {}


def test_retryable_error_keeps_state_and_backs_off(env):
    ev, jpeg = make_event(1)
    env.add((ev, jpeg))
    env.fake.fail_next("POST", "/storage", 503)
    sync = env.make(SYNC_RETRY_BASE_S="2", SYNC_RETRY_MAX_S="300")
    assert sync.sync_once()["retry"] == 1
    row = env.outbox.row(ev["event_id"])
    assert (row["state"], row["attempts"]) == (PENDING, 1)
    assert row["next_attempt_at"] == env.clock.t + 2.0  # base × 2^0 × 지터(1.0)
    assert sync.sync_once()["synced"] == 0  # 아직 차례 아님
    env.clock.t += 2.0
    assert sync.sync_once()["synced"] == 1


def test_retry_after_header_wins(env):
    ev, jpeg = make_event(1)
    env.add((ev, jpeg))
    env.fake.fail_next("POST", "/rest", 429, {"Retry-After": "17"})
    env.make().sync_once()
    row = env.outbox.row(ev["event_id"])
    assert row["state"] == IMAGE_UPLOADED and row["next_attempt_at"] == env.clock.t + 17


def test_network_error_is_retryable(env):
    ev, jpeg = make_event(1)
    env.add((ev, jpeg))
    env.fake.fail_next("POST", "/storage", TimeoutError("timed out"))
    assert env.make().sync_once()["retry"] == 1
    assert "network" in env.outbox.row(ev["event_id"])["last_error"]


def test_image_uploaded_resumes_at_row_after_restart(env):
    """Storage 성공 후 행 upsert 전에 죽어도 재시작하면 행만 올린다(객체 중복 없음, SYS-REL-02)."""
    ev, jpeg = make_event(1)
    env.add((ev, jpeg))
    env.fake.fail_next("POST", "/rest", 500)
    env.make().sync_once()
    assert env.outbox.get(ev["event_id"]).state == IMAGE_UPLOADED
    env.clock.t += 1000
    env.fake.calls.clear()
    assert env.make().sync_once()["synced"] == 1  # 새 워커 인스턴스 = 재시작
    assert env.fake.paths() == [("POST", "/rest/v1/ppe_events")]


def test_bad_request_goes_to_dead_letter(env):
    ev, jpeg = make_event(1)
    env.add((ev, jpeg))
    env.fake.fail_next("POST", "/rest", 400, body=b'{"code":"22P02","message":"invalid input"}')
    assert env.make().sync_once()["dead_letter"] == 1
    row = env.outbox.row(ev["event_id"])
    assert row["state"] == DEAD_LETTER and "22P02" in row["last_error"]


@pytest.mark.parametrize("status, body", [(401, b'{"message":"Invalid API key"}'), (403, b"{}"), (400, b'{"statusCode":"403","error":"Unauthorized"}'), (400, b'{"statusCode":"404","error":"Bucket not found"}')])
def test_auth_or_config_error_pauses_everything(env, caplog, status, body):
    events = [make_event(i, minutes=i) for i in range(3)]
    env.add(*events)
    env.fake.fail_next("POST", "/storage", status, body=body)
    sync = env.make(SYNC_AUTH_PAUSE_S="300")
    assert sync.sync_once()["paused"] == 1
    assert len(env.fake.calls) == 1  # 나머지 항목은 시도하지 않음
    assert all(env.outbox.row(e["event_id"])["state"] == PENDING and env.outbox.row(e["event_id"])["attempts"] == 0 for e, _ in events)
    assert "ALERT" in caplog.text and KEY not in caplog.text
    assert sync.sync_once()["paused"] == 1 and len(env.fake.calls) == 1
    env.clock.t += 300
    assert sync.sync_once()["synced"] == 3


def test_max_attempts_goes_to_dead_letter(env):
    ev, jpeg = make_event(1)
    env.add((ev, jpeg))
    sync = env.make(SYNC_MAX_ATTEMPTS="2")
    for _ in range(2):
        env.fake.fail_next("POST", "/storage", 502)
        sync.sync_once()
        env.clock.t += 1000
    row = env.outbox.row(ev["event_id"])
    assert (row["state"], row["attempts"]) == (DEAD_LETTER, 2)


def test_local_image_missing_is_dead_letter(env):
    ev, jpeg = make_event(1)
    env.add((ev, jpeg))
    (env.root / ev["image"]["image_path"]).unlink()
    assert env.make().sync_once()["dead_letter"] == 1
    assert env.fake.calls == []


def test_backoff_formula():
    assert backoff_s(0, 2, 300, lambda: 0.0) == 1.0
    assert backoff_s(3, 2, 300, lambda: 1.0) == 16.0
    assert backoff_s(20, 2, 300, lambda: 1.0) == 300.0


def test_retry_after_parsing():
    from datetime import datetime, timezone

    assert retry_after_s("5") == 5.0
    now = datetime(2026, 11, 2, 0, 0, 0, tzinfo=timezone.utc)
    assert retry_after_s("Mon, 02 Nov 2026 00:00:30 GMT", now) == 30.0
    assert retry_after_s("garbage") is None and retry_after_s(None) is None


def test_config_rejects_missing_key_and_plain_http(tmp_path):
    with pytest.raises(ConfigError):
        SyncConfig.from_env({"SUPABASE_URL": BASE})
    with pytest.raises(ConfigError):
        SyncConfig.from_env({"SUPABASE_URL": "http://example.com", "SUPABASE_SERVICE_KEY": KEY})
    key_file = tmp_path / "key"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    cfg = SyncConfig.from_env({"SUPABASE_URL": BASE, "SUPABASE_SERVICE_KEY_FILE": str(key_file), "EVENT_DATA_ROOT": str(tmp_path)})
    assert cfg.service_key == KEY and KEY not in repr(cfg)
    assert cfg.db_path.endswith("outbox.db") and cfg.max_attempts is None


def test_end_to_end_from_b_spool(env):
    """B 의 SpoolPublisher 게시 → 수집 → 업로드. 업로드된 행 100% 계약 통과(CON-01), 이미지 sha 일치."""
    events = [make_event(i, minutes=i, device_id=d) for i, d in enumerate(["cam-line-a-01", "cam-line-b-02"] * 3)]
    env.add(*events)
    while env.make().sync_once()["synced"]:
        pass
    assert env.outbox.counts()[SYNCED] == 6
    for rec in env.fake.rows.values():
        assert contract_errors("supabase-event-record", rec) == []
        obj = env.fake.objects[f"{rec['storage_bucket']}/{rec['storage_path']}"]
        assert hashlib.sha256(obj).hexdigest() == rec["image_sha256"]
