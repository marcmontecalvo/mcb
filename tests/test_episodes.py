import sqlite3

import pytest

from mclab.execution import (
    C1Controller,
    ControllerFailure,
    PolicyMapRecorder,
    qualify_execution,
    run_controller,
    terminal_event,
)
from mclab.recorder import EpisodeRecorder, exclusive_run


def observation(position=(0.5, 80, 0.5), blocks=(), yaw=0, pitch=0, ray=None):
    return {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "body": {"position": position, "yaw": yaw, "pitch": pitch, "ray": ray or {}},
        "visible_blocks": list(blocks),
    }


def test_controller_discovers_target_only_when_sensor_reveals_it():
    controller = C1Controller()
    assert controller.decide(observation())["forward"] == 1
    assert controller.target is None
    block = {"position": [2, 80, 6], "block": "minecraft:oak_log"}
    result = controller.decide(observation(blocks=[block]))
    assert controller.target == [2, 80, 6]
    assert result["yaw_delta"] < 0
    assert result["pitch_delta"] > 0
    assert not result["mine"]


def test_controller_requires_aim_and_ordinary_ray_before_mining():
    controller = C1Controller()
    block = {"position": [0, 80, 3], "block": "minecraft:oak_log"}
    initial = observation(position=(0.5, 80, 0.5), blocks=[block])
    aimed = controller.decide(initial)
    assert not aimed["mine"]
    initial["body"].update(
        pitch=aimed["pitch_delta"], ray={"block": "minecraft:oak_log"}
    )
    assert controller.decide(initial)["mine"]
    initial["visible_blocks"] = [{"position": [0, 80, 3], "block": "minecraft:air"}]
    initial["body"]["ray"] = {}
    pickup = controller.decide(initial)
    assert controller.broken
    assert pickup["forward"] == 1
    assert not pickup["mine"]


def test_new_controller_has_no_previous_target_memory():
    first = C1Controller()
    first.decide(
        observation(blocks=[{"position": [0, 80, 3], "block": "minecraft:oak_log"}])
    )
    assert C1Controller().target is None


def test_terminal_result_must_match_all_attempt_identifiers():
    class Client:
        run_id, episode_id, body_id = "run", "episode", "body"
        events = []

    client = Client()
    event = {
        "type": "course_episode_complete",
        "run_id": "run",
        "episode_id": "old",
        "body_id": "body",
        "data": {"status": "success"},
    }
    client.events = [event]
    assert terminal_event(client) is None
    event["episode_id"] = "episode"
    assert terminal_event(client) is event


def test_terminal_policy_newer_than_event_drain_is_not_runner_failure(monkeypatch):
    from mclab import execution

    class Client:
        run_id, episode_id, body_id = "run", "episode", "body"
        events = []

        def wait(self, predicate, timeout):
            self.events = [
                {
                    "type": "course_episode_complete",
                    "run_id": "run",
                    "episode_id": "episode",
                    "body_id": "body",
                    "data": {"status": "success"},
                }
            ]
            assert predicate({})

    monkeypatch.setattr(
        execution, "policy_state", lambda _: {"state": "episode_terminal"}
    )
    assert execution.run_controller(Client())["data"]["status"] == "success"


def test_controller_error_requests_failed_server_terminal(monkeypatch):
    from mclab import execution

    def fail(_client, _deadline):
        raise ControllerFailure("bad control")

    class Client:
        run_id, episode_id, body_id = "run", "episode", "body"

        def __init__(self):
            self.events = []

        def record(self, *_args):
            pass

        def send(self, op, **_fields):
            assert op == "course_end"
            self.events.append(
                {
                    "type": "course_episode_complete",
                    "run_id": self.run_id,
                    "episode_id": self.episode_id,
                    "body_id": self.body_id,
                    "data": {"status": "failed", "reason": "controller_cancel"},
                }
            )
            return {}

        def acknowledge(self, *_args):
            pass

        def wait(self, predicate):
            assert predicate({})

    monkeypatch.setattr(execution, "_drive_controller", fail)
    assert run_controller(Client())["data"]["status"] == "failed"


def test_recorder_commits_attempt_before_events_and_never_rewrites_terminal(tmp_path):
    recorder = EpisodeRecorder(tmp_path / "episodes.sqlite3")
    try:
        recorder.begin_run("run", {"sensor_profile": "test"})
        recorder.begin("run", 1, "episode", "body", "events.jsonl")
        assert recorder.db.execute("SELECT status FROM episodes").fetchone() == (
            "pending",
        )
        recorder.append("run", 1, "episode", "request", 10, {"op": "course_reset"})
        recorder.finish("episode", "failed", "timeout", {"tick": 600})
        with pytest.raises(ValueError, match="already finalized"):
            recorder.finish("episode", "success", "collected", {})
        assert recorder.db.execute("SELECT status,reason FROM episodes").fetchone() == (
            "failed",
            "timeout",
        )
    finally:
        recorder.close()


def test_interrupted_attempt_is_preserved_as_interrupted_not_reused(tmp_path):
    path = tmp_path / "episodes.sqlite3"
    recorder = EpisodeRecorder(path)
    recorder.begin_run("run", {})
    recorder.begin("run", 1, "episode", "body", "events.jsonl")
    recorder.close()
    recorder = EpisodeRecorder(path)
    try:
        assert recorder.recover_interrupted() == 1
        assert recorder.recover_interrupted() == 0
        assert recorder.db.execute("SELECT status FROM episodes").fetchone() == (
            "interrupted",
        )
    finally:
        recorder.close()


def test_future_schema_rejected(tmp_path):
    path = tmp_path / "episodes.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        EpisodeRecorder(path)


def test_active_runner_lock_cannot_be_stolen_and_releases_on_exit(tmp_path):
    path = tmp_path / "runner.lock"
    with exclusive_run(path):
        with pytest.raises(RuntimeError, match="runner is active"):
            with exclusive_run(path):
                pytest.fail("Concurrent runner admitted")
    with exclusive_run(path):
        pass


def test_execution_and_adversarial_entrypoints_share_arena_lock(tmp_path, monkeypatch):
    from mclab import episodeproof, execution, recorder, resetcheck

    monkeypatch.setattr(recorder, "ROOT", tmp_path)
    with recorder.exclusive_arena():
        for invoke in (
            lambda: execution.qualify_execution(1),
            episodeproof.qualify_episodes,
            lambda: resetcheck.qualify_resets(1),
        ):
            with pytest.raises(RuntimeError, match="runner is active"):
                invoke()


def test_replay_map_keeps_last_seen_tick_and_never_invents_air():
    memory = PolicyMapRecorder()
    seen = {
        "tick": 5,
        "visible_blocks": [{"position": [0, 0, 0], "block": "minecraft:stone"}],
    }
    kind, full = memory.update(seen)
    assert kind == "policy_map_full"
    assert full["cells"][0]["observed_tick"] == 5
    kind, delta = memory.update({"tick": 6, "visible_blocks": []})
    assert kind == "policy_map_delta" and delta["cells"] == []
    assert memory.cells[(0, 0, 0)]["observed_tick"] == 5
    assert not delta["unknown_is_air"]
    _, stale = memory.update(
        {
            "tick": 8,
            "visible_blocks": [
                {"position": [0, 0, 0], "block": "minecraft:stone", "observed_tick": 5}
            ],
        }
    )
    assert stale["cells"] == []


@pytest.mark.parametrize("count", [0, 21])
def test_execution_count_checked_before_runtime_access(count):
    with pytest.raises(ValueError, match="1 to 20"):
        qualify_execution(count)
