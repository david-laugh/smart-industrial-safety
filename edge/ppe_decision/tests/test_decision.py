"""PPE Decision: 평활화, UNKNOWN 정책, dedup, 결정적 동작, 시간 규칙, spool 원자 게시, UUIDv7."""
import copy
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path

import pytest

from edge.inference import contract_check as cc
from edge.ppe_decision.config import DecisionConfig, parse_grid
from edge.ppe_decision.decision import PPEDecision, format_utc, jpeg_size, occurred_at
from edge.ppe_decision.spool import SpoolPublisher
from edge.ppe_decision.uuid7 import uuid7, uuid7_unix_ms

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = json.loads((ROOT / "contracts/examples/edge-frame.example.json").read_text(encoding="utf-8"))
MODEL = json.loads((ROOT / "contracts/examples/inference-result.example.json").read_text(encoding="utf-8"))["model"]
T0 = datetime(2026, 11, 2, 1, 0, 0, tzinfo=timezone.utc)


def make_jpeg(w=640, h=480) -> bytes:
    """최소 SOF0 헤더를 가진 JPEG 유사 바이트(디코드 불필요, 크기 파싱용)."""
    sof = b"\xff\xc0\x00\x11\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + b"\xff\xd9"


JPEG = make_jpeg()


def person(idx, helmet, vest, bbox=(0.40, 0.10, 0.60, 0.95), hc=0.8, vc=0.8):
    conf = lambda s, c: None if s == "UNKNOWN" else c  # noqa: E731
    return {"person_index": idx, "track_id": None,
            "bbox": dict(zip(("x_min", "y_min", "x_max", "y_max"), bbox)),
            "person_confidence": 0.9,
            "helmet": {"status": helmet, "confidence": conf(helmet, hc)},
            "vest": {"status": vest, "confidence": conf(vest, vc)}}


def frame(seq, persons, t=None, capture_skew_ms=0, capture_null=False, device="cam-line-a-01"):
    t = t if t is not None else T0 + timedelta(seconds=seq * 0.5)
    ef = copy.deepcopy(TEMPLATE)
    fid = f"019a4a3c-0000-7000-8000-{seq:012x}"
    ef.update({"frame_id": fid, "frame_seq": seq, "device_id": device, "mcu_uptime_ms": 1000 + seq * 500,
               "capture_ts_utc": None if capture_null else format_utc(t),
               "gateway_received_at_utc": format_utc(t + timedelta(milliseconds=capture_skew_ms))})
    ef["image"].update({"byte_length": len(JPEG), "sha256": hashlib.sha256(JPEG).hexdigest()})
    res = {"schema_version": "1.0.0", "frame_id": fid, "device_id": device,
           "image_size": {"width": 640, "height": 480}, "model": dict(MODEL),
           "timing": {"decode_ms": 1.0, "preprocess_ms": 1.0, "inference_ms": 1.0, "postprocess_ms": 1.0},
           "persons": persons}
    return ef, res


def fixed_ids():
    c = count()
    return lambda: f"019a4a3c-8a10-7f00-8b5c-{next(c):012x}"


def decider(tmp_path=None, **kw):
    cfg = DecisionConfig(**kw)
    pub = SpoolPublisher(str(tmp_path)) if tmp_path is not None else None
    return PPEDecision(cfg, publisher=pub, publish=pub is not None,
                       now_fn=lambda: datetime(2026, 11, 2, 2, 0, 0, tzinfo=timezone.utc), id_fn=fixed_ids())


def run_all(d, seq_persons, **kw):
    """프레임별 이벤트 리스트의 리스트."""
    out = []
    for i, ps in enumerate(seq_persons):
        ef, res = frame(i, ps, **kw)
        evs = d.update(ef, res, JPEG)
        assert cc.errors("inference-result", res) == []
        for ev in evs:
            assert cc.errors("ppe-event", ev) == [], cc.errors("ppe-event", ev)
            assert len(ev["violations"]) == 1
        out.append(evs)
    return out


def run(d, seq_persons, **kw):
    """프레임별 첫 이벤트 또는 None (단일 위반자 시나리오용)."""
    return [evs[0] if evs else None for evs in run_all(d, seq_persons, **kw)]


# ---------------------------------------------------------------- 평활화 / UNKNOWN
def test_confirmed_after_min_missing_frames():
    evs = run(decider(), [[person(0, "MISSING", "WORN")]] * 3)
    assert evs[:2] == [None, None] and evs[2] is not None
    v = evs[2]["violations"][0]
    assert v["violation_types"] == ["HELMET_MISSING"]
    assert evs[2]["decision"]["frames_considered"] == 3
    assert evs[2]["decision"]["policy_version"] == "0.0.0"


def test_not_enough_missing_frames_no_event():
    seq = [[person(0, "MISSING", "WORN")], [person(0, "WORN", "WORN")], [person(0, "MISSING", "WORN")],
           [person(0, "WORN", "WORN")], [person(0, "WORN", "WORN")], [person(0, "WORN", "WORN")]]
    assert all(e is None for e in run(decider(), seq))


def test_absent_frames_do_not_count():
    seq = [[person(0, "MISSING", "WORN")], [], [person(0, "MISSING", "WORN")], [], [], [person(0, "MISSING", "WORN")]]
    assert all(e is None for e in run(decider(), seq))


def test_current_frame_must_be_missing():
    seq = [[person(0, "MISSING", "WORN")]] * 3
    seq = [seq[0], seq[1], [person(0, "UNKNOWN", "WORN")], seq[0]]
    evs = run(decider(), seq)
    assert evs[2] is None and evs[3] is not None  # 4번째: 창 안 MISSING 3개 + 현재 MISSING


def test_unknown_only_person_never_creates_event():
    evs = run(decider(), [[person(0, "UNKNOWN", "UNKNOWN")]] * 20)
    assert all(e is None for e in evs)


def test_unknown_is_violation_policy_flag():
    evs = run(decider(unknown_is_violation=True), [[person(0, "UNKNOWN", "WORN")]] * 3)
    assert evs[2] is not None and evs[2]["violations"][0]["violation_types"] == ["HELMET_MISSING"]


def test_both_violation_types_sorted():
    evs = run(decider(), [[person(0, "MISSING", "MISSING")]] * 3)
    assert evs[2]["violations"][0]["violation_types"] == ["HELMET_MISSING", "VEST_MISSING"]


def test_multiple_violators_get_separate_events(tmp_path):
    a = person(0, "MISSING", "WORN", bbox=(0.05, 0.1, 0.20, 0.95))
    b = person(1, "WORN", "MISSING", bbox=(0.70, 0.1, 0.90, 0.95))
    c = person(2, "WORN", "WORN", bbox=(0.40, 0.1, 0.55, 0.95))
    frames_evs = run_all(decider(tmp_path), [[a, b, c]] * 3)
    assert [len(x) for x in frames_evs] == [0, 0, 2]
    e0, e1 = frames_evs[2]
    assert e0["event_id"] != e1["event_id"]
    assert [(v["person_index"], v["violation_types"]) for v in e0["violations"]] == [(0, ["HELMET_MISSING"])]
    assert [(v["person_index"], v["violation_types"]) for v in e1["violations"]] == [(1, ["VEST_MISSING"])]
    assert e0["decision"]["dedup_key"].startswith("cam-line-a-01|pos:r2c0|HELMET_MISSING|")
    assert e1["decision"]["dedup_key"].startswith("cam-line-a-01|pos:r2c3|VEST_MISSING|")
    for e in (e0, e1):  # 증거 JPEG 는 event_id 별로 저장
        assert (tmp_path / e["image"]["image_path"]).read_bytes() == JPEG
        assert (tmp_path / "spool" / "incoming" / f"{e['event_id']}.json").exists()
    assert len(list((tmp_path / "spool" / "incoming").iterdir())) == 2


def test_per_person_dedup_is_independent():
    a = person(0, "MISSING", "WORN", bbox=(0.05, 0.1, 0.20, 0.95))
    b = person(1, "WORN", "MISSING", bbox=(0.70, 0.1, 0.90, 0.95))
    seq = [[a]] * 3 + [[a, b]] * 3   # b 는 나중에 등장 → a 억제 중에도 b 는 별도 발행
    frames_evs = run_all(decider(), seq)
    assert [len(x) for x in frames_evs] == [0, 0, 1, 0, 0, 1]
    assert frames_evs[5][0]["violations"][0]["person_index"] == 1


# ---------------------------------------------------------------- dedup
def test_dedup_window_one_event_for_continuous_violation():
    # 2fps × 59프레임 = 29.5초 동안 연속 미착용 → 1건
    evs = run(decider(), [[person(0, "MISSING", "WORN")]] * 59)
    assert sum(e is not None for e in evs) == 1


def test_dedup_new_event_after_window():
    evs = run(decider(), [[person(0, "MISSING", "WORN")]] * 70)  # 35초
    idx = [i for i, e in enumerate(evs) if e is not None]
    assert idx == [2, 62]  # t=1.0s, t=31.0s
    k1, k2 = (evs[i]["decision"]["dedup_key"] for i in idx)
    assert k1 != k2 and k1.split("|")[:3] == k2.split("|")[:3]


def test_dedup_key_format_and_window_start():
    d = decider()
    start = datetime(2026, 11, 2, 1, 23, 44, tzinfo=timezone.utc)  # 예시 파일과 같은 버킷(…1:23:30)
    evs = []
    for i in range(3):
        ef, res = frame(i, [person(0, "MISSING", "WORN", bbox=(0.21, 0.10, 0.45, 0.95))],
                        t=start + timedelta(seconds=i * 0.5))
        evs.append((d.update(ef, res, JPEG) or [None])[0])
    assert evs[2]["decision"]["dedup_key"] == "cam-line-a-01|pos:r2c1|HELMET_MISSING|1793582610"


def test_dedup_suppresses_across_bucket_boundary():
    d = decider()
    start = datetime(2026, 11, 2, 1, 0, 28, tzinfo=timezone.utc)  # 1:00:29 에 확정, 1:00:30 버킷 경계 통과
    n = 0
    for i in range(20):
        ef, res = frame(i, [person(0, "MISSING", "WORN")], t=start + timedelta(seconds=i * 0.5))
        n += len(d.update(ef, res, JPEG))
    assert n == 1


def test_different_violation_types_are_separate_keys():
    seq = [[person(0, "MISSING", "WORN")]] * 3 + [[person(0, "MISSING", "MISSING")]] * 3
    evs = run(decider(), seq)
    assert [i for i, e in enumerate(evs) if e is not None] == [2, 5]


def test_sticky_cell_across_grid_boundary():
    """bbox 중심이 격자 경계(x=0.5)에서 흔들려도 같은 사람으로 본다 → 이벤트 1건."""
    left = person(0, "MISSING", "WORN", bbox=(0.37, 0.1, 0.62, 0.95))   # 중심 0.495 → c1
    right = person(0, "MISSING", "WORN", bbox=(0.39, 0.1, 0.62, 0.95))  # 중심 0.505 → c2
    evs = run(decider(), [[left], [right], [left], [right], [left], [right]] * 5)
    assert sum(e is not None for e in evs) == 1
    assert evs[2] is not None


def test_unknown_person_in_same_cell_does_not_split_violator():
    """걸어오는 작업자 영상 회귀: 뒤쪽 원경 인물(UNKNOWN)이 같은 셀에 있어도 위반자는 이벤트 1건."""
    seq = []
    for k in range(40):
        y0 = 0.11 + k * 0.012                       # 주 작업자: 아래로 이동하며 커짐
        main = person(0, "MISSING", "WORN", bbox=(0.45, y0, 0.57, min(1.0, y0 + 0.25 + k * 0.01)))
        back = person(1, "UNKNOWN", "UNKNOWN", bbox=(0.51, 0.04, 0.55, 0.13))
        dup = [person(2, "UNKNOWN", "UNKNOWN", bbox=(0.51, 0.05, 0.53, 0.09))] if k % 7 == 3 else []
        seq.append([main, back] + dup)
    evs = run_all(decider(), seq)
    assert sum(len(x) for x in evs) == 1


def test_unknown_persons_tracked_when_unknown_is_violation():
    evs = run(decider(unknown_is_violation=True), [[person(0, "UNKNOWN", "UNKNOWN")]] * 3)
    assert evs[2] is not None


def test_devices_are_independent():
    d = decider()
    n = 0
    for i in range(3):
        for dev in ("cam-a-01", "cam-b-01"):
            ef, res = frame(i, [person(0, "MISSING", "WORN")], device=dev)
            n += len(d.update(ef, res, JPEG))
    assert n == 2


# ---------------------------------------------------------------- 결정적 동작
def _strip(ev):
    ev = copy.deepcopy(ev)
    ev.pop("event_id")
    ev["timestamps"].pop("decided_at_utc")
    ev["image"].pop("image_path")
    return ev


def test_deterministic_same_sequence_same_decisions():
    a = person(0, "MISSING", "WORN", bbox=(0.05, 0.1, 0.20, 0.95))
    b = person(1, "WORN", "MISSING", bbox=(0.70, 0.1, 0.90, 0.95))
    seq = ([[a, b]] * 4 + [[a]] * 3 + [[]] * 2 + [[b]] * 4) * 6
    e1 = run_all(decider(), seq)
    e2 = run_all(decider(), seq)
    assert e1 == e2  # id/시각 주입 시 완전 동일
    d3 = PPEDecision(DecisionConfig(), publish=False)  # 실제 uuid7/현재 시각
    e3 = run_all(d3, seq)
    assert [len(x) for x in e1] == [len(x) for x in e3]
    assert [_strip(x) for f in e1 for x in f] == [_strip(x) for f in e3 for x in f]
    assert sum(len(x) for x in e1) >= 4


# ---------------------------------------------------------------- 시간 규칙
def test_occurred_at_device_sntp_within_skew():
    ef, _ = frame(0, [], capture_skew_ms=2000)
    occ, src = occurred_at(ef, 2000)
    assert src == "DEVICE_SNTP" and occ == ef["capture_ts_utc"]


def test_occurred_at_gateway_when_skew_exceeded_or_null():
    ef, _ = frame(0, [], capture_skew_ms=2001)
    assert occurred_at(ef, 2000) == (ef["gateway_received_at_utc"], "GATEWAY_RECEIVED")
    ef, _ = frame(0, [], capture_null=True)
    assert occurred_at(ef, 2000) == (ef["gateway_received_at_utc"], "GATEWAY_RECEIVED")


def test_event_with_unsynced_clock_passes_contract():
    evs = run(decider(), [[person(0, "MISSING", "WORN")]] * 3, capture_null=True)
    ts = evs[2]["timestamps"]
    assert ts["occurred_at_source"] == "GATEWAY_RECEIVED" and ts["capture_ts_utc"] is None
    assert ts["occurred_at_utc"].endswith("Z") and ts["decided_at_utc"].endswith("Z")


# ---------------------------------------------------------------- spool
def test_spool_publish_order_and_files(tmp_path):
    steps = []
    d = decider(tmp_path)
    d.publisher.on_step = lambda s, p: steps.append((s, os.path.exists(os.path.join(
        tmp_path, "spool", "incoming")) and len(os.listdir(os.path.join(tmp_path, "spool", "incoming")))))
    evs = run(d, [[person(0, "MISSING", "WORN")]] * 3)
    ev = evs[2]
    assert [s for s, _ in steps] == ["jpeg_tmp_written", "jpeg_published", "json_tmp_written", "json_published"]
    assert [n for _, n in steps][:3] == [0, 0, 0]  # JSON 은 JPEG 게시 후에만 보인다
    img = tmp_path / ev["image"]["image_path"]
    js = tmp_path / "spool" / "incoming" / f"{ev['event_id']}.json"
    assert img.read_bytes() == JPEG
    assert re.fullmatch(r"images/2026/11/02/[0-9a-f-]{36}\.jpg", ev["image"]["image_path"])
    assert ev["image"]["sha256"] == hashlib.sha256(JPEG).hexdigest()
    assert (ev["image"]["width"], ev["image"]["height"], ev["image"]["byte_length"]) == (640, 480, len(JPEG))
    assert json.loads(js.read_text(encoding="utf-8")) == ev
    assert os.listdir(tmp_path / "spool" / "tmp") == []


def test_spool_failure_does_not_record_dedup(tmp_path):
    d = decider(tmp_path)

    def boom(step, path):
        if step == "json_tmp_written":
            raise OSError("disk full")
    d.publisher.on_step = boom
    seq = [[person(0, "MISSING", "WORN")]] * 3
    with pytest.raises(OSError):
        run(d, seq)
    assert list((tmp_path / "spool" / "incoming").iterdir()) == []
    d.publisher.on_step = None
    for f in (tmp_path / "spool" / "tmp").iterdir():
        f.unlink()
    ef, res = frame(3, [person(0, "MISSING", "WORN")])
    assert len(d.update(ef, res, JPEG)) == 1  # 실패한 이벤트는 억제되지 않고 다음 프레임에서 발행


# ---------------------------------------------------------------- 기타
def test_uuid7_format_version_and_order():
    pat = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    ids = [uuid7() for _ in range(2000)]
    assert all(pat.match(u) for u in ids)
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert uuid7_unix_ms(uuid7(1793582625120)) == 1793582625120


def test_jpeg_size_and_grid_parse():
    assert jpeg_size(make_jpeg(320, 240)) == (320, 240)
    assert jpeg_size(b"garbage") is None
    assert parse_grid("6x4") == (4, 6)
    cfg = DecisionConfig.from_env({"PPE_DECISION_WINDOW_FRAMES": "", "PPE_DEDUP_GRID": "4x4",
                                   "PPE_UNKNOWN_IS_VIOLATION": "false", "EVENT_DATA_ROOT": "./_data"})
    assert (cfg.window_frames, cfg.min_missing_frames, cfg.unknown_is_violation, cfg.data_root) == (5, 3, False, "./_data")
    with pytest.raises(ValueError):
        DecisionConfig(window_frames=2, min_missing_frames=3)
