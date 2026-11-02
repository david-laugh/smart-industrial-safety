import hashlib
import json
import re
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from edge.gateway import schemas
from edge.gateway.app import create_app
from edge.gateway.clock import ClockSyncProbe, utc_now_iso
from edge.gateway.frames import build_edge_frame

from gateway_testlib import FakePipeline, FixedClock, auth, fake_jpeg, files, load_example, make_metadata

URL = "/api/v1/frames"


def assert_response_contract(r, status, code=None):
    assert r.status_code == status, r.text
    body = r.json()
    assert schemas.errors("gateway-response", body) == []
    if code is None:
        assert body["accepted"] is True and body["error"] is None
        assert uuid.UUID(body["frame_id"]).version == 7
    else:
        assert body["accepted"] is False and body["frame_id"] is None
        assert body["error"]["code"] == code
    return body


@pytest.fixture
def client_factory(make_config):
    clients = []

    def _make(pipeline=None, clock=None, **cfg):
        app = create_app(make_config(**cfg), pipeline, clock=clock or FixedClock(True))
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(client_factory):
    return client_factory()


def post(client, metadata, jpeg, device="cam-line-a-01", image_type="image/jpeg", headers=None):
    return client.post(URL, headers=headers if headers is not None else auth(device),
                       files=files(metadata, jpeg, image_type))


def pending(client):
    """워커 없는 앱에서 큐에 남은 프레임을 꺼낸다."""
    sel = client.app.state.gateway.selector
    out = []
    while (item := sel.get(timeout=0)) is not None:
        out.append(item)
    return out


# ---------------------------------------------------------------- 202


@pytest.mark.parametrize(
    "example", ["esp32-frame-metadata.example.json", "esp32-frame-metadata.unsynced.example.json"]
)
def test_contract_examples_accepted(client, example):
    meta = load_example(example)
    jpeg = fake_jpeg(meta["image"]["byte_length"])
    body = assert_response_contract(post(client, meta, jpeg), 202)
    assert body["queue_depth"] == 1 and body["retry_after_ms"] is None

    [(edge_frame, queued_jpeg)] = pending(client)
    assert schemas.errors("edge-frame", edge_frame) == []
    assert queued_jpeg == jpeg
    assert edge_frame["frame_id"] == body["frame_id"]
    assert edge_frame["gateway_received_at_utc"] == body["gateway_received_at_utc"]
    assert edge_frame["capture_ts_utc"] == meta["time_sync"]["capture_ts_utc"]  # 수정 없이 전달
    assert edge_frame["gateway_clock_synced"] is True
    for k in ("device_id", "boot_id", "frame_seq", "mcu_uptime_ms"):
        assert edge_frame[k] == meta[k]
    img = edge_frame["image"]
    assert img["location"] == {"kind": "IN_MEMORY", "path": None}
    assert (img["width"], img["height"], img["byte_length"]) == (640, 480, len(jpeg))
    assert img["sha256"] == hashlib.sha256(jpeg).hexdigest()
    assert edge_frame["edge_detection"] == meta["edge_detection"]
    expected_reason = "EDGE_PERSON_HINT" if meta["edge_detection"]["person_detected"] else "LATEST_FRAME"
    assert edge_frame["selection"]["reason"] == expected_reason


def test_edge_detection_absent_becomes_null_and_unsynced_clock(client_factory):
    c = client_factory(clock=FixedClock(False))
    meta = make_metadata(n=100)
    del meta["edge_detection"]
    meta["trigger"] = "MANUAL"
    assert_response_contract(post(c, meta, fake_jpeg(100)), 202)
    [(ef, _)] = pending(c)
    assert schemas.errors("edge-frame", ef) == []
    assert ef["edge_detection"] is None
    assert ef["gateway_clock_synced"] is False
    assert ef["selection"]["reason"] == "MANUAL"


def test_build_edge_frame_unit_passes_schema():
    meta = load_example("esp32-frame-metadata.example.json")
    ef = build_edge_frame(meta, fake_jpeg(meta["image"]["byte_length"]),
                          gateway_received_at_utc=utc_now_iso(), gateway_clock_synced=True)
    assert schemas.errors("edge-frame", ef) == []
    # 원본 메타데이터를 공유하지 않음(깊은 복사)
    ef["edge_detection"]["person_score"] = 0.0
    assert meta["edge_detection"]["person_score"] == 0.8


# ---------------------------------------------------------------- 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Basic tok-a-0123456789"},
        {"Authorization": "Bearer "},
    ],
)
def test_401_bad_or_missing_token(client, headers):
    r = post(client, make_metadata(n=100), fake_jpeg(100), headers=headers)
    assert_response_contract(r, 401, "UNAUTHORIZED")


def test_401_token_of_other_device(client):
    r = post(client, make_metadata(device_id="cam-line-a-01", n=100), fake_jpeg(100), device="cam-line-b-01")
    assert_response_contract(r, 401, "UNAUTHORIZED")


def test_401_checked_before_schema(client):
    meta = make_metadata(device_id="cam-line-a-01", n=100)
    del meta["boot_id"]
    r = post(client, meta, fake_jpeg(100), device="cam-line-b-01")
    assert_response_contract(r, 401, "UNAUTHORIZED")


# ---------------------------------------------------------------- 413


def test_413_request_too_large_by_content_length(client_factory):
    c = client_factory(GATEWAY_MAX_REQUEST_BYTES=2000, GATEWAY_MAX_IMAGE_BYTES=1000)
    r = post(c, make_metadata(n=3000), fake_jpeg(3000))
    assert_response_contract(r, 413, "PAYLOAD_TOO_LARGE")


def test_413_request_too_large_chunked(client_factory):
    c = client_factory(GATEWAY_MAX_REQUEST_BYTES=2000, GATEWAY_MAX_IMAGE_BYTES=1000)

    def gen():
        for _ in range(10):
            yield b"x" * 500

    r = c.post(URL, headers={**auth(), "Content-Type": "multipart/form-data; boundary=abc"}, content=gen())
    assert_response_contract(r, 413, "PAYLOAD_TOO_LARGE")


def test_413_image_too_large(client_factory):
    c = client_factory(GATEWAY_MAX_REQUEST_BYTES=10000, GATEWAY_MAX_IMAGE_BYTES=1000)
    r = post(c, make_metadata(n=1001), fake_jpeg(1001))
    assert_response_contract(r, 413, "PAYLOAD_TOO_LARGE")


def test_image_at_exact_limit_accepted(client_factory):
    c = client_factory(GATEWAY_MAX_REQUEST_BYTES=10000, GATEWAY_MAX_IMAGE_BYTES=1000)
    assert_response_contract(post(c, make_metadata(n=1000), fake_jpeg(1000)), 202)


# ---------------------------------------------------------------- 415


def test_415_image_not_jpeg(client):
    r = post(client, make_metadata(n=100), fake_jpeg(100), image_type="image/png")
    assert_response_contract(r, 415, "UNSUPPORTED_MEDIA_TYPE")


def test_415_request_not_multipart(client):
    r = client.post(URL, headers={**auth(), "Content-Type": "application/json"}, content=json.dumps(make_metadata()))
    assert_response_contract(r, 415, "UNSUPPORTED_MEDIA_TYPE")


# ---------------------------------------------------------------- 400


def test_400_metadata_not_json(client):
    assert_response_contract(post(client, "{not json", fake_jpeg(100)), 400, "INVALID_METADATA")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.pop("boot_id"),
        lambda m: m.update(frame_seq=-1),
        lambda m: m.update(extra_field=1),
        lambda m: m["time_sync"].update(synced=True, capture_ts_utc=None),
        lambda m: m["image"].update(content_type="image/png"),
        lambda m: m.update(image_b64="AAAA"),
    ],
)
def test_400_schema_violation(client, mutate):
    meta = make_metadata(n=100)
    mutate(meta)
    r = post(client, meta, fake_jpeg(100))
    assert_response_contract(r, 400, "INVALID_METADATA")


def test_400_missing_parts(client):
    r = client.post(URL, headers=auth(), files=[("image", ("f.jpg", fake_jpeg(100), "image/jpeg"))])
    assert_response_contract(r, 400, "INVALID_METADATA")
    r = client.post(URL, headers=auth(), files=[("metadata", (None, json.dumps(make_metadata(n=100)), "application/json"))])
    assert_response_contract(r, 400, "INVALID_METADATA")


@pytest.mark.parametrize(
    "content_type, body, needle",
    [
        # 닫는 boundary 없음(잘린 본문)
        ("multipart/form-data; boundary=abc",
         b"--abc\r\nContent-Disposition: form-data; name=\"metadata\"\r\n\r\n{}\r\n--zzz", "incomplete"),
        ("multipart/form-data; boundary=abc", b"garbage", "malformed"),
        ("multipart/form-data", b"", "boundary"),
    ],
)
def test_400_malformed_multipart(client, content_type, body, needle):
    r = client.post(URL, headers={**auth(), "Content-Type": content_type}, content=body)
    b = assert_response_contract(r, 400, "INVALID_METADATA")
    assert needle in b["error"]["message"]


def test_400_duplicate_part(client):
    meta = json.dumps(make_metadata(n=100))
    r = client.post(URL, headers=auth(), files=[
        ("metadata", (None, meta, "application/json")),
        ("metadata", (None, meta, "application/json")),
        ("image", ("f.jpg", fake_jpeg(100), "image/jpeg")),
    ])
    assert_response_contract(r, 400, "INVALID_METADATA")


def test_400_byte_length_mismatch(client):
    r = post(client, make_metadata(n=100), fake_jpeg(101))
    assert_response_contract(r, 400, "IMAGE_METADATA_MISMATCH")


@pytest.mark.parametrize(
    "jpeg",
    [
        b"\x00\xd8" + b"\x00" * 96 + b"\xff\xd9",  # SOI 없음
        b"\xff\xd8" + b"\x00" * 96 + b"\xff\x00",  # EOI 없음
    ],
)
def test_400_jpeg_markers(client, jpeg):
    r = post(client, make_metadata(n=len(jpeg)), jpeg)
    assert_response_contract(r, 400, "IMAGE_METADATA_MISMATCH")


# ---------------------------------------------------------------- 429 / 큐


def test_429_queue_full_frames(client_factory):
    c = client_factory(GATEWAY_QUEUE_MAX_FRAMES=2, GATEWAY_RETRY_AFTER_MS=750)
    assert_response_contract(post(c, make_metadata("cam-line-a-01", n=100), fake_jpeg(100), "cam-line-a-01"), 202)
    assert_response_contract(post(c, make_metadata("cam-line-b-01", n=100), fake_jpeg(100), "cam-line-b-01"), 202)
    r = post(c, make_metadata("cam-line-c-01", n=100), fake_jpeg(100), "cam-line-c-01")
    body = assert_response_contract(r, 429, "QUEUE_FULL")
    assert body["retry_after_ms"] == 750 and body["queue_depth"] == 2


def test_429_queue_full_bytes(client_factory):
    c = client_factory(GATEWAY_QUEUE_MAX_BYTES=1500, GATEWAY_MAX_IMAGE_BYTES=1000, GATEWAY_MAX_REQUEST_BYTES=5000)
    assert_response_contract(post(c, make_metadata("cam-line-a-01", n=1000), fake_jpeg(1000), "cam-line-a-01"), 202)
    r = post(c, make_metadata("cam-line-b-01", n=600), fake_jpeg(600), "cam-line-b-01")
    assert_response_contract(r, 429, "QUEUE_FULL")
    assert_response_contract(post(c, make_metadata("cam-line-b-01", n=500), fake_jpeg(500), "cam-line-b-01"), 202)


def test_latest_wins_and_healthz_counts(client):
    for seq in (1, 2, 3):
        body = assert_response_contract(post(client, make_metadata(n=100, seq=seq), fake_jpeg(100)), 202)
        assert body["queue_depth"] == 1
    post(client, make_metadata(n=100), fake_jpeg(100), image_type="image/png")  # 415 하나

    h = client.get("/healthz")
    assert h.status_code == 200
    hb = h.json()
    assert hb["queue"]["frames"] == 1 and hb["queue"]["bytes"] == 100
    assert hb["counters"]["accepted"] == 3
    assert hb["counters"]["dropped"] == 2
    assert hb["counters"]["rejected"] == 1
    assert hb["counters"]["rejected_by_code"] == {"UNSUPPORTED_MEDIA_TYPE": 1}
    assert hb["worker_alive"] is None

    [(ef, _)] = pending(client)
    assert ef["frame_seq"] == 3
    assert ef["selection"]["dropped_since_last_selected"] == 2
    assert schemas.errors("edge-frame", ef) == []


def test_person_frame_dequeued_first(client):
    post(client, make_metadata("cam-line-a-01", n=100, person=False), fake_jpeg(100), "cam-line-a-01")
    post(client, make_metadata("cam-line-b-01", n=100, person=True), fake_jpeg(100), "cam-line-b-01")
    order = [ef["device_id"] for ef, _ in pending(client)]
    assert order == ["cam-line-b-01", "cam-line-a-01"]


# ---------------------------------------------------------------- 워커


def test_worker_calls_pipeline_and_survives_exceptions(client_factory):
    pipe = FakePipeline(raise_on={1}, expected=2)
    c = client_factory(pipeline=pipe)
    assert_response_contract(post(c, make_metadata("cam-line-a-01", n=100, seq=1), fake_jpeg(100), "cam-line-a-01"), 202)
    # 첫 프레임 처리(예외)가 끝난 뒤 두 번째를 보내야 latest-wins 로 합쳐지지 않는다
    deadline = time.monotonic() + 5
    while not pipe.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert_response_contract(post(c, make_metadata("cam-line-a-01", n=100, seq=2), fake_jpeg(100), "cam-line-a-01"), 202)
    assert pipe.done.wait(5)
    assert [ef["frame_seq"] for ef, _ in pipe.calls] == [1, 2]
    for ef, jpeg in pipe.calls:
        assert schemas.errors("edge-frame", ef) == []
        assert jpeg == fake_jpeg(100)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        hb = c.get("/healthz").json()
        if hb["counters"]["processed"] == 1:
            break
        time.sleep(0.01)
    assert hb["worker_alive"] is True
    assert hb["counters"]["failed"] == 1 and hb["counters"]["processed"] == 1


def test_internal_error_returns_500_contract(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(client.app.state.gateway.selector, "offer", boom)
    r = post(client, make_metadata(n=100), fake_jpeg(100))
    assert_response_contract(r, 500, "INTERNAL_ERROR")


# ---------------------------------------------------------------- 시계


def test_utc_now_iso_format():
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", utc_now_iso())


def test_clock_probe_failure_reports_unsynced():
    def bad():
        raise OSError("nope")

    assert ClockSyncProbe(check=bad).synced() is False
    assert ClockSyncProbe(check=lambda: True).synced() is True
