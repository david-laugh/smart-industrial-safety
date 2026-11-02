"""spool 수집 → outbox, 보존 정책 (docs/protocols.md §4 spool, §5 로컬 보존, EC-06)."""
import json
import os

import pytest

from edge.event_store.ingest import SpoolIngestor
from edge.event_store.outbox import DEAD_LETTER, PENDING, SYNCED, Outbox
from edge.event_store.retention import prune
from edge.event_store.tests.factory import make_event, publish


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def outbox(tmp_path, clock):
    ob = Outbox(str(tmp_path / "outbox.db"), clock=clock)
    yield ob
    ob.close()


def _spool(root, sub):
    return sorted(os.listdir(root / "spool" / sub))


def test_ingest_moves_valid_events_to_outbox(tmp_path, outbox):
    events = [make_event(i, minutes=10 - i) for i in range(3)]
    publish(tmp_path, *events)
    stats = SpoolIngestor(str(tmp_path), outbox).run_once()
    assert stats == {"ingested": 3, "duplicate": 0, "rejected": 0}
    assert _spool(tmp_path, "incoming") == []
    assert _spool(tmp_path, "processed") == sorted(f"{e['event_id']}.json" for e, _ in events)
    assert outbox.counts()[PENDING] == 3
    item = outbox.get(events[0][0]["event_id"])
    assert item.event == events[0][0] and item.state == PENDING and item.attempts == 0
    # 오래된 순
    assert [i.event_id for i in outbox.due(1e12, 10)] == [e["event_id"] for e, _ in reversed(events)]


def test_reingest_is_ignored_and_event_id_unchanged(tmp_path, outbox):
    ev, jpeg = make_event(1)
    publish(tmp_path, (ev, jpeg))
    SpoolIngestor(str(tmp_path), outbox).run_once()
    # 커밋 후 이동 전에 죽은 상황: 같은 JSON 이 다시 incoming 에 있음
    (tmp_path / "spool/incoming" / f"{ev['event_id']}.json").write_text(json.dumps(ev), encoding="utf-8")
    stats = SpoolIngestor(str(tmp_path), outbox).run_once()
    assert stats == {"ingested": 0, "duplicate": 1, "rejected": 0}
    assert sum(outbox.counts().values()) == 1


@pytest.mark.parametrize(
    "breakage, reason",
    [
        (lambda ev, root: ev["violations"][0]["helmet"].update(status="ABSENT"), "schema"),
        (lambda ev, root: ev["image"].update(sha256="f" * 64), "sha"),
        (lambda ev, root: os.remove(root / ev["image"]["image_path"]), "missing"),
    ],
)
def test_bad_spool_file_is_rejected(tmp_path, outbox, breakage, reason):
    ev, jpeg = make_event(1)
    publish(tmp_path, (ev, jpeg))
    path = tmp_path / "spool/incoming" / f"{ev['event_id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    breakage(data, tmp_path)
    path.write_text(json.dumps(data), encoding="utf-8")
    stats = SpoolIngestor(str(tmp_path), outbox).run_once()
    assert stats["rejected"] == 1, reason
    assert _spool(tmp_path, "rejected") == [path.name]
    assert sum(outbox.counts().values()) == 0


def test_part_files_and_non_json_are_ignored(tmp_path, outbox):
    (tmp_path / "spool/incoming").mkdir(parents=True)
    (tmp_path / "spool/incoming/x.json.part").write_bytes(b"{")
    assert SpoolIngestor(str(tmp_path), outbox).run_once() == {"ingested": 0, "duplicate": 0, "rejected": 0}
    assert _spool(tmp_path, "incoming") == ["x.json.part"]


def test_outbox_survives_reopen(tmp_path, clock):
    ev, jpeg = make_event(1)
    publish(tmp_path, (ev, jpeg))
    ob = Outbox(str(tmp_path / "outbox.db"), clock=clock)
    SpoolIngestor(str(tmp_path), ob).run_once()
    ob.mark_image_uploaded(ev["event_id"])
    ob.close()
    ob2 = Outbox(str(tmp_path / "outbox.db"), clock=clock)
    assert ob2.get(ev["event_id"]).state == "IMAGE_UPLOADED"
    assert ob2.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    ob2.close()


def test_requeue_only_dead_letters(tmp_path, outbox):
    ev, jpeg = make_event(1)
    publish(tmp_path, (ev, jpeg))
    SpoolIngestor(str(tmp_path), outbox).run_once()
    assert not outbox.requeue(ev["event_id"])
    outbox.dead_letter(ev["event_id"], "400 bad", attempts=3)
    assert outbox.get(ev["event_id"]).state == DEAD_LETTER
    assert outbox.requeue(ev["event_id"])
    item = outbox.get(ev["event_id"])
    assert (item.state, item.attempts) == (PENDING, 0)


def test_retention_deletes_only_synced(tmp_path, outbox, clock):
    events = [make_event(i, minutes=i) for i in range(4)]
    publish(tmp_path, *events)
    SpoolIngestor(str(tmp_path), outbox).run_once()
    old, recent = events[0][0]["event_id"], events[1][0]["event_id"]
    outbox.mark_synced(old)
    clock.t += 8 * 86400
    outbox.mark_synced(recent)

    stats = prune(str(tmp_path), outbox, clock.t, retention_days=7, max_bytes=None)
    assert stats == {"deleted": 1, "over_limit": 0}
    assert not (tmp_path / events[0][0]["image"]["image_path"]).exists()
    assert not (tmp_path / "spool/processed" / f"{old}.json").exists()
    assert (tmp_path / events[1][0]["image"]["image_path"]).exists()
    assert outbox.row(old)["image_deleted_at"] == clock.t and outbox.get(old).state == SYNCED  # 행은 남김


def test_disk_limit_deletes_synced_first_and_keeps_unsent(tmp_path, outbox, clock, caplog):
    events = [make_event(i, minutes=i) for i in range(4)]
    publish(tmp_path, *events)
    SpoolIngestor(str(tmp_path), outbox).run_once()
    size = events[0][0]["image"]["byte_length"]
    outbox.mark_synced(events[0][0]["event_id"])

    stats = prune(str(tmp_path), outbox, clock.t, retention_days=7, max_bytes=size * 2)
    assert stats == {"deleted": 1, "over_limit": 1}  # 미전송 3건은 지우지 않고 경보
    assert "ALERT" in caplog.text
    for ev, _ in events[1:]:
        assert (tmp_path / ev["image"]["image_path"]).exists()
