import json
import threading
import time
from contextlib import contextmanager

import pytest

from mclab import bridge, execution
from mclab.paths import write_json
from mclab.recorder import EpisodeRecorder


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    write_json(state, {"launch_id": "launch", "token": "secret"})
    monkeypatch.setattr(bridge, "STATE", state)
    client = execution.RecordedBridge(tmp_path / "events.jsonl")
    ledger = EpisodeRecorder(tmp_path / "episodes.sqlite3")
    ledger.begin_run(client.run_id, {})
    client.recorder = ledger
    yield client, ledger
    if client.writer.is_alive():
        client.close()
    ledger.close()


def test_last_record_failure_is_published_before_flush_can_pass(recorded, monkeypatch):
    client, _ledger = recorded

    def fail_append(*_args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(EpisodeRecorder, "_insert_event", fail_append)
    client.record("critical_terminal", {"status": "success"})
    with pytest.raises(RuntimeError, match="recording failed"):
        client.flush_recording()
    assert isinstance(client.writer_error, OSError)
    with pytest.raises(RuntimeError, match="recording failed"):
        client.close()
    assert not client.writer.is_alive()


def test_blocked_disk_writer_does_not_block_motor_submission(recorded, monkeypatch):
    client, ledger = recorded
    entered, release = threading.Event(), threading.Event()
    original = EpisodeRecorder._insert_event

    def append(self, *args):
        entered.set()
        assert release.wait(2)
        original(self, *args)

    monkeypatch.setattr(EpisodeRecorder, "_insert_event", append)
    client.record("sample", {})
    assert entered.wait(1)
    start = time.monotonic()
    client.record("request", {"op": "controls"})
    assert time.monotonic() - start < 0.05
    release.set()
    client.close()
    assert ledger.db.execute("SELECT count(*) FROM events").fetchone()[0] == 2


def test_queued_burst_uses_bounded_durable_batches(recorded, monkeypatch):
    client, ledger = recorded
    entered, release = threading.Event(), threading.Event()
    original = EpisodeRecorder.batch
    sizes = []

    @contextmanager
    def batch(self):
        entered.set()
        assert release.wait(2)
        before = self.db.total_changes
        with original(self):
            yield
        sizes.append(self.db.total_changes - before)

    monkeypatch.setattr(EpisodeRecorder, "batch", batch)
    client.record("first", {})
    assert entered.wait(1)
    for index in range(200):
        client.record("burst", {"index": index})
    assert client.record_queue.unfinished_tasks == 201
    release.set()
    client.flush_recording()
    assert ledger.db.execute("SELECT count(*) FROM events").fetchone()[0] == 201
    assert all(1 <= size <= 128 for size in sizes)
    assert len(sizes) == 3


def test_batch_failure_rolls_back_partial_events(recorded, monkeypatch):
    client, ledger = recorded
    original = EpisodeRecorder.batch

    @contextmanager
    def batch(self):
        with original(self):
            yield
            raise OSError("simulated commit failure")

    monkeypatch.setattr(EpisodeRecorder, "batch", batch)
    client.record("critical_terminal", {"status": "success"})
    with pytest.raises(RuntimeError, match="recording failed"):
        client.flush_recording()
    assert ledger.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    with pytest.raises(RuntimeError, match="recording failed"):
        client.close()


def test_burst_capacity_remains_bounded_and_overflow_invalidates(recorded, monkeypatch):
    client, _ledger = recorded
    entered, release = threading.Event(), threading.Event()
    original = EpisodeRecorder._insert_event

    def append(self, *args):
        entered.set()
        assert release.wait(2)
        original(self, *args)

    monkeypatch.setattr(EpisodeRecorder, "_insert_event", append)
    client.record("first", {})
    assert entered.wait(1)
    try:
        for _ in range(client.record_queue.maxsize):
            client.record("burst", {})
        with pytest.raises(RuntimeError, match="queue overflow.*writer_stage="):
            client.record("overflow", {})
        assert client.record_queue.qsize() == client.record_queue.maxsize
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="recording failed"):
            client.close()


def test_large_payload_is_encoded_once_and_roundtrips_in_both_artifacts(
    recorded, monkeypatch
):
    client, ledger = recorded
    payload = {"text": 'quoted "value"\nwith Unicode: café', "nested": [None, True, 1.5]}
    original = json.dumps
    encoded = []

    def dumps(value, *args, **kwargs):
        if value is payload:
            encoded.append(True)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(execution.json, "dumps", dumps)
    client.record("roundtrip", payload)
    client.flush_recording()
    stored = ledger.db.execute(
        "SELECT payload FROM events WHERE kind='roundtrip'"
    ).fetchone()[0]
    with open(client.stream.name, encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream]
    assert records[-1]["payload"] == json.loads(stored) == payload
    assert encoded == [True]
