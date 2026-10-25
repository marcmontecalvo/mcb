import json
import sqlite3

import pytest

from mclab import navigation_runner as runner
from mclab.paths import digest, write_json


def spec(course="C2", seed=0):
    return {
        "course_id": course,
        "seed": seed,
        "origin": [1000, 80, 1000],
        "spawn": [0.5, 0, 0.5],
        "yaw": 45 if course != "C1" else 0,
        "pitch": 0,
        "spec_hash": f"{course}-{seed}",
        "tick_limit": 900,
    }


def reset_data(value, generation=1):
    return {
        "spec_hash": value["spec_hash"],
        "state_digest": f"digest-{value['spec_hash']}",
        "generation": generation,
        "body_state": {
            "position": [1000.5, 80, 1000.5],
            "yaw": value["yaw"],
            "pitch": value["pitch"],
            "inventory": [],
        },
    }


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "SERVER", tmp_path / "server")
    monkeypatch.setattr(runner, "source_hash", lambda: "mod-current")
    monkeypatch.setattr(runner, "_source_commit", lambda: "test-commit")
    monkeypatch.setattr(runner, "config", lambda: {"origin": [1000, 80, 1000]})
    monkeypatch.setattr(runner.courses, "verify_generated", lambda: {})
    monkeypatch.setattr(
        runner.courses, "generate", lambda course, seed, _: spec(course, seed)
    )
    profile = {
        "profile_id": "navigation-c2-c5-survival-v1",
        "sensor_profile": "local-visibility-v1",
    }
    monkeypatch.setattr(
        runner.gates,
        "require_navigation_profile",
        lambda *_args, **_kwargs: profile,
        raising=False,
    )
    monkeypatch.setattr(
        runner.gates,
        "require_initial_profile",
        lambda _: {"profile_id": "initial-c1-survival-v1"},
    )
    write_json(tmp_path / "runtime/mod-build.json", {"source_hash": "mod-current"})
    write_json(tmp_path / "courses/profiles.json", {"test": True})
    write_json(tmp_path / "config/navigation_body_profile.json", profile)
    write_json(
        tmp_path / "server/world/datapacks/mclab/evaluator/active.json", spec("C1")
    )
    for name in ("src/mclab/navigation.py", "mod/src/test.java"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test source")
    state = {"clients": [], "wrong_hash": False, "controllers": []}

    class Client:
        def __init__(self, output):
            self.run_id, self.launch_id = "run-test", "launch-test"
            self.events, self.requests = [], []
            self.submitted = self.generation = 0
            state["clients"].append(self)

        def synchronize(self):
            pass

        def record(self, *_args):
            pass

        def flush_recording(self):
            pass

        def close(self):
            pass

        def send(self, op, **fields):
            self.submitted += 1
            request = {
                "op": op,
                "episode_id": self.episode_id,
                "body_id": self.body_id,
                **fields,
            }
            self.requests.append(request)
            return request

        def acknowledge(self, request, kind, **_kwargs):
            if kind == "course_reset_complete":
                self.generation += 1
                value = request.get("course_spec", spec("C1"))
                data = reset_data(value, self.generation)
                if state["wrong_hash"] and value["course_id"] != "C1":
                    data["spec_hash"] = "wrong"
                return {"data": data}
            return {}

        def wait(self, predicate):
            op = self.requests[-1]["op"]
            assert predicate({"state": "idle" if op == "despawn" else "running"})

    monkeypatch.setattr(runner, "RecordedBridge", Client)
    monkeypatch.setattr(runner, "_new_controller", lambda: object())

    def drive(client, controller, wall_timeout):
        state["controllers"].append(controller)
        assert wall_timeout == 75
        return {
            "data": {"status": "success", "reason": "causal_target_break_drop_pickup"}
        }

    monkeypatch.setattr(runner, "drive", drive)
    return tmp_path, state, profile


@pytest.mark.parametrize(
    "course,seeds",
    [("C1", [0]), ("C2", []), ("C2", [True]), ("C2", [-1]), ("C2", [1] * 21)],
)
def test_invalid_navigation_requests_fail_before_world_access(course, seeds):
    with pytest.raises(ValueError):
        runner.execute(course, seeds)


def test_reset_count_and_execution_seed_duplicates_rejected():
    with pytest.raises(ValueError):
        runner.qualify_resets("C2", count=True)
    with pytest.raises(ValueError):
        runner.execute("C2", [1, 1])


def test_wrong_reset_hash_never_starts_controller(environment):
    root, state, _ = environment
    state["wrong_hash"] = True
    result = runner.execute("C2", [0])
    assert result["status"] == "invalid"
    assert result["episodes"][0]["status"] == "invalid"
    assert not state["controllers"]
    assert result["cleanup"]["status"] == "restored_initial_C1"
    with sqlite3.connect(root / "results/episodes.sqlite3") as db:
        assert db.execute("SELECT status FROM episodes").fetchall() == [("invalid",)]


def test_fresh_controller_and_identities_for_each_episode_and_restore(environment):
    root, state, _ = environment
    result = runner.execute("C2", [0, 1])
    assert result["status"] == "completed_navigation_execution"
    assert result["successes"] == 2 and result["scored"] is False
    assert state["controllers"][0] is not state["controllers"][1]
    resets = [r for r in state["clients"][0].requests if r["op"] == "course_reset"]
    assert len({r["episode_id"] for r in resets}) == 3
    assert len({r["body_id"] for r in resets}) == 3
    assert [r["course_spec"]["seed"] for r in resets[:2]] == [0, 1]
    assert "course_spec" not in resets[2]
    assert resets[2]["profile_id"] == "initial-c1-survival-v1"
    with sqlite3.connect(root / "results/episodes.sqlite3") as db:
        assert db.execute("SELECT status FROM episodes").fetchall() == [
            ("success",),
            ("success",),
        ]


def test_twenty_matching_resets_do_not_begin_gameplay(environment):
    _, state, _ = environment
    result = runner.qualify_resets("C3", seed=4, count=20)
    assert result["status"] == "passed_development_reset"
    assert len(result["resets"]) == 20 and result["scored"] is False
    assert not any(r["op"] == "course_begin" for r in state["clients"][0].requests)
    assert not state["controllers"]


def test_interrupted_attempt_is_persisted_then_restored(environment, monkeypatch):
    root, state, _ = environment

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(runner, "drive", interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.execute("C2", [0])
    with sqlite3.connect(root / "results/episodes.sqlite3") as db:
        assert db.execute("SELECT status FROM episodes").fetchone()[0] == "interrupted"
    summary = json.loads(
        next((root / "results/navigation").glob("*/summary.json")).read_text()
    )
    assert summary["status"] == "interrupted"
    assert summary["cleanup"]["status"] == "restored_initial_C1"
    assert state["clients"][0].requests[-1]["op"] == "despawn"


def test_scored_execution_rejects_stale_qualification_before_transport(environment):
    root, state, profile = environment
    write_json(
        root / "docs/NAVIGATION_QUALIFICATION.json",
        {
            "courses": {
                "C2": {
                    "status": "passed",
                    "profile_hash": digest(profile),
                    "mod_source_hash": "old-mod",
                }
            }
        },
    )
    with pytest.raises(ValueError, match="reset qualification"):
        runner.execute("C2", [0], scored=True)
    assert not state["clients"]


def test_reset_position_and_heading_are_checked_without_trusting_hash():
    value = spec()
    data = reset_data(value)
    data["body_state"]["yaw"] = 0
    with pytest.raises(RuntimeError, match="heading"):
        runner._verify_reset(data, value)
