"""seed 가데이터·업로더: 계약 통과, 실제 JPEG, 프로토콜 규칙(§5 업로드 순서·헤더, 시간/ID/중복 규칙)."""

import hashlib
import io
import json
import re
from datetime import datetime

import httpx
import pytest
from PIL import Image

from seed.__main__ import SeedError, main, upload
from seed.generate import TIME_MAX_DEVICE_SKEW_MS, generate, validator

UUID7 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
DEDUP = re.compile(r"^[a-z0-9-]+\|(trk-[0-9a-f]{6}|pos:r[0-3]c[0-3])\|(HELMET_MISSING|HELMET_MISSING,VEST_MISSING|VEST_MISSING)\|\d+$")


@pytest.fixture(scope="module")
def events():
    return generate(80, 7, seed=3)


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_rows_pass_contract(events):
    v = validator()
    errors = [f"{r['event_id']}: {e.message}" for r, _ in events for e in v.iter_errors(r)]
    assert not errors, "\n".join(errors[:10])


def test_jpeg_matches_metadata(events):
    for row, jpeg in events:
        img = row["event_payload"]["image"]
        assert jpeg[:2] == b"\xff\xd8" and len(jpeg) <= 1_048_576
        assert hashlib.sha256(jpeg).hexdigest() == row["image_sha256"] == img["sha256"]
        assert len(jpeg) == img["byte_length"]
        assert Image.open(io.BytesIO(jpeg)).size == (img["width"], img["height"])
        assert row["storage_path"] == f"{row['device_id']}/{_ts(row['occurred_at']):%Y/%m/%d}/{row['event_id']}.jpg"


def test_time_rule(events):
    sources = set()
    for row, _ in events:
        ts = row["event_payload"]["timestamps"]
        sources.add(ts["occurred_at_source"])
        gw = _ts(ts["gateway_received_at_utc"])
        cap = ts["capture_ts_utc"]
        within = cap is not None and abs((_ts(cap) - gw).total_seconds() * 1000) <= TIME_MAX_DEVICE_SKEW_MS
        if within:
            assert ts["occurred_at_source"] == "DEVICE_SNTP" and ts["occurred_at_utc"] == cap
        else:
            assert ts["occurred_at_source"] == "GATEWAY_RECEIVED" and ts["occurred_at_utc"] == ts["gateway_received_at_utc"]
    assert sources == {"DEVICE_SNTP", "GATEWAY_RECEIVED"}


def test_ids_and_dedup_key(events):
    for row, _ in events:
        p = row["event_payload"]
        assert UUID7.match(row["event_id"]) and UUID7.match(p["frame_id"])
        assert int(row["event_id"].replace("-", "")[:12], 16) == int(_ts(row["decided_at"]).timestamp() * 1000)
        assert DEDUP.match(p["decision"]["dedup_key"]), p["decision"]["dedup_key"]
        assert int(p["decision"]["dedup_key"].rsplit("|", 1)[1]) % p["decision"]["dedup_window_s"] == 0


def test_upload_order_is_oldest_first(events):
    times = [r["occurred_at"] for r, _ in events]
    assert times == sorted(times)


def _recorder(conflict_paths=(), existing=None):
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path.startswith("/storage/"):
            path = req.url.path.split("/ppe-evidence/", 1)[1]
            if req.method == "GET":
                return httpx.Response(200, content=existing[path])
            if path in conflict_paths:
                return httpx.Response(400, json={"statusCode": "409", "error": "Duplicate"})
            return httpx.Response(200, json={"Key": path})
        return httpx.Response(201)

    return calls, httpx.MockTransport(handler)


def test_upload_image_then_row_with_protocol_headers(events):
    sample = events[:3]
    calls, transport = _recorder()
    upload(sample, "https://dev.supabase.test", "service-key", "ppe_events", transport=transport)
    assert [c.method for c in calls] == ["POST", "POST"] * 3
    for (row, jpeg), (img, db) in zip(sample, zip(calls[::2], calls[1::2])):
        assert img.url.path == f"/storage/v1/object/ppe-evidence/{row['storage_path']}"
        assert img.headers["x-upsert"] == "false" and img.headers["content-type"] == "image/jpeg"
        assert img.content == jpeg
        assert db.url.path == "/rest/v1/ppe_events" and db.url.params["on_conflict"] == "event_id"
        assert db.headers["prefer"] == "resolution=ignore-duplicates,return=minimal"
        assert json.loads(db.content) == row and "ingested_at" not in json.loads(db.content)
        assert img.headers["apikey"] == "service-key" and img.headers["authorization"] == "Bearer service-key"


def test_existing_object_with_same_hash_is_success(events):
    row, jpeg = events[0]
    calls, transport = _recorder({row["storage_path"]}, {row["storage_path"]: jpeg})
    upload([events[0]], "https://dev.supabase.test", "k", "ppe_events", transport=transport)
    assert [c.method for c in calls] == ["POST", "GET", "POST"]


def test_existing_object_with_other_hash_fails(events):
    row, _ = events[0]
    _, transport = _recorder({row["storage_path"]}, {row["storage_path"]: b"other"})
    with pytest.raises(SeedError, match="해시가 다릅니다"):
        upload([events[0]], "https://dev.supabase.test", "k", "ppe_events", transport=transport)


def test_auth_error_stops_immediately(events):
    calls = []
    transport = httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(401, json={"message": "bad jwt"}))
    with pytest.raises(SeedError, match="401"):
        upload(events[:5], "https://dev.supabase.test", "k", "ppe_events", transport=transport)
    assert len(calls) == 1


def test_dry_run_writes_files(tmp_path):
    main(["--count", "4", "--days", "2", "--seed", "1", "--out", str(tmp_path)])
    rows = json.loads((tmp_path / "ppe_events.json").read_text(encoding="utf-8"))
    assert len(rows) == 4
    for r in rows:
        assert (tmp_path / "storage" / "ppe-evidence" / r["storage_path"]).is_file()
