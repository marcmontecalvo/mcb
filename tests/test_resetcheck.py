import sqlite3

import pytest

from mclab.resetcheck import ResetRecorder, qualify_resets


def test_reset_records_pending_before_success_and_keeps_identifiers_as_data(tmp_path):
    path = tmp_path / "resets.sqlite3"
    recorder = ResetRecorder(path)
    run = "run'; DROP TABLE reset_attempts; --"
    recorder.begin(run, 1, "episode", "body", "events.jsonl")
    assert recorder.db.execute("SELECT status FROM reset_attempts").fetchone() == (
        "pending",
    )
    recorder.finish(run, 1, "passed", generation=7, state_digest="abc")
    recorder.close()
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT status,generation,state_digest FROM reset_attempts"
        ).fetchone() == ("passed", 7, "abc")
        assert db.execute("PRAGMA user_version").fetchone() == (1,)


def test_finalized_attempt_cannot_be_rewritten(tmp_path):
    recorder = ResetRecorder(tmp_path / "resets.sqlite3")
    try:
        recorder.begin("run", 1, "episode", "body", "events.jsonl")
        recorder.finish("run", 1, "invalid", error="state mismatch")
        with pytest.raises(ValueError, match="already finalized"):
            recorder.finish("run", 1, "passed")
        assert recorder.db.execute(
            "SELECT status,error FROM reset_attempts"
        ).fetchone() == ("invalid", "state mismatch")
    finally:
        recorder.close()


def test_unfinished_attempt_is_not_reclassified_as_success(tmp_path):
    path = tmp_path / "resets.sqlite3"
    recorder = ResetRecorder(path)
    recorder.begin("run", 1, "episode", "body", "events.jsonl")
    recorder.close()
    recorder = ResetRecorder(path)
    try:
        assert recorder.db.execute("SELECT status FROM reset_attempts").fetchone() == (
            "pending",
        )
    finally:
        recorder.close()


def test_future_database_schema_is_rejected(tmp_path):
    path = tmp_path / "resets.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        ResetRecorder(path)


@pytest.mark.parametrize("count", [0, 21])
def test_reset_count_is_bounded_before_accessing_runtime(count):
    with pytest.raises(ValueError, match="1 to 20"):
        qualify_resets(count)
