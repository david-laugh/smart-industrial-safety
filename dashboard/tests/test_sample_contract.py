"""샘플 데이터(js/sample-data.js)가 contracts/ 계약을 지키는지 검증한다."""

from datetime import datetime

import pytest
from jsonschema import Draft202012Validator, FormatChecker

GENERATE = "PPE.sampleData.generate(PPE.config.sample)"
SERVER_COLUMNS = {"ingested_at"}  # DB가 생성하는 컬럼 — 업로드 payload 계약에는 없음


@pytest.fixture(scope="module")
def sample_rows(browser, base_url):
    pg = browser.new_page()
    pg.goto(base_url)
    rows = pg.evaluate(GENERATE)
    pg.close()
    return rows


def _validator(schemas, name):
    registry, by_name = schemas
    return Draft202012Validator(by_name[name], registry=registry, format_checker=FormatChecker())


def test_generates_configured_count(sample_rows):
    assert len(sample_rows) == 180


def test_rows_match_supabase_event_record_schema(sample_rows, schemas):
    v = _validator(schemas, "supabase-event-record")
    errors = []
    for row in sample_rows:
        payload = {k: val for k, val in row.items() if k not in SERVER_COLUMNS}
        errors += [f"{row['event_id']}: {e.json_path}: {e.message}" for e in v.iter_errors(payload)]
    assert not errors, "\n".join(errors[:20])


def test_payloads_match_ppe_event_schema(sample_rows, schemas):
    v = _validator(schemas, "ppe-event")
    errors = [f"{r['event_id']}: {e.message}" for r in sample_rows for e in v.iter_errors(r["event_payload"])]
    assert not errors, "\n".join(errors[:20])


def test_denormalized_columns_agree_with_payload(sample_rows):
    for row in sample_rows:
        p = row["event_payload"]
        ts = p["timestamps"]
        assert row["event_id"] == p["event_id"]
        assert row["device_id"] == p["device_id"]
        assert row["occurred_at"] == ts["occurred_at_utc"]
        assert row["occurred_at_source"] == ts["occurred_at_source"]
        assert row["gateway_received_at"] == ts["gateway_received_at_utc"]
        assert row["decided_at"] == ts["decided_at_utc"]
        assert row["violation_count"] == len(p["violations"])
        union = {t for v in p["violations"] for t in v["violation_types"]}
        assert set(row["violation_types"]) == union
        assert row["image_sha256"] == p["image"]["sha256"]
        assert (row["model_name"], row["model_version"]) == (p["model"]["name"], p["model"]["version"])


def test_storage_path_uses_occurred_at_utc_date(sample_rows):
    for row in sample_rows:
        d = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))
        assert row["storage_path"] == f"{row['device_id']}/{d:%Y/%m/%d}/{row['event_id']}.jpg"


def test_time_source_rules(sample_rows):
    for row in sample_rows:
        ts = row["event_payload"]["timestamps"]
        if ts["occurred_at_source"] == "DEVICE_SNTP":
            assert ts["capture_ts_utc"] == ts["occurred_at_utc"]
        else:
            assert ts["capture_ts_utc"] is None
            assert ts["occurred_at_utc"] == ts["gateway_received_at_utc"]
        assert ts["occurred_at_utc"] <= ts["gateway_received_at_utc"] <= ts["decided_at_utc"]


def test_person_status_matches_violation_types(sample_rows):
    for row in sample_rows:
        for v in row["event_payload"]["violations"]:
            assert ("HELMET_MISSING" in v["violation_types"]) == (v["helmet"]["status"] == "MISSING")
            assert ("VEST_MISSING" in v["violation_types"]) == (v["vest"]["status"] == "MISSING")
            b = v["bbox"]
            assert b["x_min"] < b["x_max"] and b["y_min"] < b["y_max"]


def test_sorted_newest_first_and_ids_unique(sample_rows):
    times = [r["occurred_at"] for r in sample_rows]
    assert times == sorted(times, reverse=True)
    assert len({r["event_id"] for r in sample_rows}) == len(sample_rows)


def test_generation_is_deterministic(page):
    assert page.evaluate(f"JSON.stringify({GENERATE}) === JSON.stringify({GENERATE})")
