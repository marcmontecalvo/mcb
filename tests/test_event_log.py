import gzip
import json
import shutil
import threading

import pytest

from mclab import event_log
from mclab.event_log import EventWriter


def make(sequence, type_="request", payload=None, **extra):
    return {
        "schema_version": 1,
        "run_id": "r",
        "episode_id": "e",
        "body_id": "b",
        "sequence": sequence,
        "monotonic_ns": 1000 + sequence,
        "type": type_,
        "payload": payload if payload is not None else {},
        **extra,
    }


SAMPLE = [
    make(1, "request", {"op": "plan_route", "plan_id": "plan-1"}),
    make(2, "server_event", {"type": "policy_sample", "data": {"tick": 7}}),
    make(3, "server_event", {"type": "plan_finished", "data": {"plan_id": "plan-1"}}),
    make(4, "observation", {"tick": 9, "x": 1.5, "nested": [1, {"a": None}]}),
]


def write_store(run, records):
    writer = EventWriter(run / "events.sqlite3")
    for record in records:
        writer.add(record)
    writer.close()
    return run / "events.sqlite3"


def write_jsonl(path, records):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")


def test_round_trip_is_identical(tmp_path):
    write_store(tmp_path, SAMPLE)
    assert list(event_log.records(tmp_path)) == SAMPLE
    assert [list(r) for r in event_log.records(tmp_path)] == [list(r) for r in SAMPLE]


def test_filters(tmp_path):
    store = write_store(tmp_path, SAMPLE)
    seqs = lambda **kw: [r["sequence"] for r in event_log.records(store, **kw)]  # noqa: E731
    assert seqs(types=["server_event"]) == [2, 3]
    assert seqs(kinds=["policy_sample"]) == [2]
    assert seqs(kinds=["plan_route"]) == [1]
    assert seqs(plan_ids=["plan-1"]) == [1, 3]
    assert seqs(since_seq=2, until_seq=3) == [2, 3]
    assert seqs(limit=2, reverse=True) == [4, 3]
    assert seqs(kind_like=["plan\\_%"]) == [3] or seqs(kind_like=["plan_%"]) == [1, 3]
    with event_log.connect(store) as db:
        assert db.execute("SELECT tick FROM events ORDER BY sequence").fetchall() == [
            (None,),
            (7,),
            (None,),
            (9,),
        ]


@pytest.mark.parametrize("name", ["events.jsonl", "events.jsonl.gz"])
def test_jsonl_fallback_same_filters(tmp_path, name):
    write_jsonl(tmp_path / name, SAMPLE)
    assert event_log.locate(tmp_path) == tmp_path / name
    assert list(event_log.records(tmp_path)) == SAMPLE
    got = event_log.records(
        tmp_path / name, types=["server_event"], plan_ids=["plan-1"]
    )
    assert [r["sequence"] for r in got] == [3]
    found = event_log.records(tmp_path, kinds=["plan_route"])
    assert [r["sequence"] for r in found] == [1]
    assert [r["sequence"] for r in event_log.tail(tmp_path, 2)] == [3, 4]


def test_missing_jsonl_resolves_to_sqlite(tmp_path):
    write_store(tmp_path, SAMPLE)
    assert event_log.locate(tmp_path) == tmp_path / "events.sqlite3"
    assert list(event_log.records(tmp_path / "events.jsonl")) == SAMPLE
    assert event_log.locate(tmp_path / "nowhere") is None


def test_sqlite_wins_over_jsonl(tmp_path):
    write_store(tmp_path, SAMPLE[:1])
    write_jsonl(tmp_path / "events.jsonl", SAMPLE)
    assert event_log.locate(tmp_path) == tmp_path / "events.sqlite3"


def test_tail(tmp_path):
    store = write_store(tmp_path, SAMPLE)
    assert [r["sequence"] for r in event_log.tail(store, 2)] == [3, 4]
    assert [r["sequence"] for r in event_log.tail(store, 9, types=["request"])] == [1]


def test_max_size_skips_bulky_records(tmp_path):
    big = make(5, "server_event", {"type": "policy_sample", "pad": "x" * 5000})
    store = write_store(tmp_path, [*SAMPLE, big])
    small = event_log.records(store, max_size=1000)
    assert [r["sequence"] for r in small] == [1, 2, 3, 4]


def test_convert_and_delete(tmp_path):
    source = tmp_path / "events.jsonl.gz"
    write_jsonl(source, SAMPLE)
    target = event_log.convert(source)
    assert target == tmp_path / "events.sqlite3" and source.exists()
    assert list(event_log.records(target)) == SAMPLE
    other = tmp_path / "other"
    other.mkdir()
    write_jsonl(other / "events.jsonl", SAMPLE)
    event_log.convert(other / "events.jsonl", delete=True)
    assert not (other / "events.jsonl").exists()
    assert list(event_log.records(other)) == SAMPLE


def test_cli_stats_and_show(tmp_path, capsys):
    write_store(tmp_path, SAMPLE)
    assert event_log.main(["stats", str(tmp_path)]) == 0
    assert "policy_sample" in capsys.readouterr().out
    assert event_log.main(["show", str(tmp_path), "--kind", "plan_route"]) == 0
    assert json.loads(capsys.readouterr().out) == SAMPLE[0]


def test_concurrent_writers(tmp_path):
    writer = EventWriter(tmp_path / "events.sqlite3")

    def work(base):
        for i in range(500):
            writer.add(make(base + i))

    threads = [threading.Thread(target=work, args=(b,)) for b in (0, 10_000)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    writer.close()
    assert len(list(event_log.records(tmp_path))) == 1000


def test_crash_without_flush_loses_only_the_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(event_log, "FLUSH_SECONDS", 3600)
    writer = EventWriter(tmp_path / "events.sqlite3")
    for i in range(event_log.FLUSH_ROWS + 10):
        writer.add(make(i))  # one full batch commits, ten stay pending
    copy = tmp_path / "crash"
    copy.mkdir()
    for suffix in ("", "-wal", "-shm"):
        src = tmp_path / f"events.sqlite3{suffix}"
        if src.exists():
            shutil.copy(src, copy / src.name)
    writer.close()
    survived = len(list(event_log.records(copy)))
    assert event_log.FLUSH_ROWS <= survived <= event_log.FLUSH_ROWS + 10


def test_existing_store_is_not_overwritten(tmp_path):
    write_store(tmp_path, SAMPLE)
    with pytest.raises(FileExistsError):
        EventWriter(tmp_path / "events.jsonl")  # maps to the same store
