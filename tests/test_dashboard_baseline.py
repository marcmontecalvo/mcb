"""External baseline progress never launches or controls lab work."""

from mclab import dashboard, server
from mclab.paths import write_json


def setup_batch(tmp_path, monkeypatch, alive=True):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(server, "pid_alive", lambda pid: alive)
    write_json(
        tmp_path / "runtime/java-migration-candidate-manifest.json", {"runner_pid": 123}
    )
    return tmp_path / "runtime/java-migration-post.log"


def test_no_external_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    assert dashboard.baseline_progress() is None


def test_running_batch_retains_failure_and_current_check(tmp_path, monkeypatch):
    log = setup_batch(tmp_path, monkeypatch)
    log.write_text(
        "START 2026-11-03T14:00:29-05:00: scripts/check-natural-ops.py\n"
        "END 2026-11-03T14:00:40-05:00: exit=0\n"
        "START 2026-11-03T14:00:42-05:00: runtime/requal-awareness.py\n"
        "Starting initial_resets\n"
    )
    progress = dashboard.baseline_progress()
    assert progress["status"] == "running"
    assert progress["finished"] == 1
    assert progress["jobs"][2]["status"] == "running"
    assert "Starting initial_resets" in progress["log"]


def test_dead_runner_is_not_reported_as_running(tmp_path, monkeypatch):
    log = setup_batch(tmp_path, monkeypatch, alive=False)
    log.write_text("START 2026-11-03T14:00:42-05:00: runtime/requal-awareness.py\n")
    progress = dashboard.baseline_progress()
    assert progress["status"] == "interrupted"
    assert progress["jobs"][2]["status"] == "interrupted"


def test_completed_batch_does_not_hide_failed_gather(tmp_path, monkeypatch):
    log = setup_batch(tmp_path, monkeypatch, alive=False)
    commands = [
        "scripts/check-natural-ops.py",
        "runtime/clean-baseline-chest.py",
        "runtime/requal-awareness.py",
        "scripts/run-gather.py",
        "scripts/run-build.py",
        "-m mclab.cli natural run --condition calm",
        "-m mclab.cli natural run --condition mobs",
    ]
    log.write_text(
        "".join(
            f"START 2026-11-03T14:00:29-05:00: {command}\n"
            f"END 2026-11-03T14:00:40-05:00: exit={int(i == 3)}\n"
            for i, command in enumerate(commands)
        )
    )
    progress = dashboard.baseline_progress()
    assert progress["status"] == "completed with failures"
    assert progress["finished"] == 7
    assert progress["jobs"][3]["status"] == "failed"
