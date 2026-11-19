"""The ledger keeps small payloads only, launch records stay out of the real
results/ under pytest, and an ending arena session runs the automatic prune."""

import json

from mclab import recorder, run_launch
from mclab.recorder import EpisodeRecorder


def payloads(ledger):
    return [
        json.loads(p)
        for (p,) in ledger.db.execute("SELECT payload FROM events ORDER BY sequence")
    ]


def test_big_ledger_payloads_are_elided_small_ones_kept(tmp_path, monkeypatch):
    monkeypatch.delenv("MCLAB_LEDGER_FULL", raising=False)
    ledger = EpisodeRecorder(tmp_path / "episodes.sqlite3")
    ledger.begin_run("r", {})
    ledger.append("r", 1, "e", "request", 1, {"op": "controls"})
    ledger.append(
        "r", 2, "e", "server_event", 2, {"type": "policy_sample", "x": "y" * 9000}
    )

    small, big = payloads(ledger)

    assert small == {"op": "controls"}
    assert big == {"type": "policy_sample", "elided_bytes": big["elided_bytes"]}
    assert big["elided_bytes"] > recorder.LEDGER_PAYLOAD_MAX


def test_full_ledger_payloads_on_request(tmp_path, monkeypatch):
    monkeypatch.setenv("MCLAB_LEDGER_FULL", "1")
    ledger = EpisodeRecorder(tmp_path / "episodes.sqlite3")
    ledger.begin_run("r", {})
    ledger.append(
        "r", 1, "e", "server_event", 1, {"type": "policy_sample", "x": "y" * 9000}
    )

    [full] = payloads(ledger)

    assert len(full["x"]) == 9000


def test_launch_records_under_pytest_land_in_the_test_folder(tmp_path):
    assert tmp_path in run_launch.LAUNCHES.parents
    with run_launch.launch_record({"run": {"test": "x"}}) as state:
        pass
    assert run_launch.LAUNCHES in state["folder"].parents


def test_an_ending_arena_session_runs_the_automatic_prune(monkeypatch):
    from mclab import prune

    calls = []
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.delenv("MCLAB_NO_PRUNE", raising=False)
    monkeypatch.setattr(prune, "auto", lambda: calls.append(1) or {"ok": 1})
    with recorder.exclusive_arena():
        pass
    assert calls == [1]
