import threading
import time

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

    monkeypatch.setattr(EpisodeRecorder, "append", fail_append)
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
    original = EpisodeRecorder.append

    def append(self, *args):
        entered.set()
        assert release.wait(2)
        original(self, *args)

    monkeypatch.setattr(EpisodeRecorder, "append", append)
    client.record("sample", {})
    assert entered.wait(1)
    start = time.monotonic()
    client.record("request", {"op": "controls"})
    assert time.monotonic() - start < 0.05
    release.set()
    client.close()
    assert ledger.db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
