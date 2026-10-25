import json
import sqlite3

import pytest

from mclab import navigationproof as proof
from mclab.bootstrap import sha256
from mclab.paths import digest, read_json, write_json
from mclab.recorder import EpisodeRecorder


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(proof, "ROOT", tmp_path)

    def build(course="C2", count=2, successes=None, fault=None, name=None):
        directory = tmp_path / "results/navigation" / (name or course)
        snapshot = directory / "sources"
        for filename in (
            "navigation.py",
            "navigation_runner.py",
            "bridge.py",
            "execution.py",
            "model_execution.py",
            "recorder.py",
        ):
            path = snapshot / "src/mclab" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(filename, encoding="utf-8")
        native = snapshot / "mod/src/test.java"
        native.parent.mkdir(parents=True)
        native.write_text("class Test {}", encoding="utf-8")
        profile = {"profile_id": "navigation-c2-c5-survival-v1"}
        profiles = {"version": 1}
        write_json(snapshot / "config/navigation_body_profile.json", profile)
        write_json(snapshot / "courses/profiles.json", profiles)
        run = "run-" + directory.name
        rows, episodes, specs = [], [], []

        def record(kind, payload, episode="initial", body="initial"):
            rows.append(
                {
                    "run_id": run,
                    "episode_id": episode,
                    "body_id": body,
                    "sequence": len(rows) + 1,
                    "monotonic_ns": len(rows) * 100,
                    "type": kind,
                    "payload": payload,
                }
            )

        def event(kind, data, tick, episode, body):
            record(
                "server_event",
                {
                    "type": kind,
                    "data": data,
                    "tick": tick,
                    "run_id": run,
                    "episode_id": episode,
                    "body_id": body,
                },
                episode,
                body,
            )

        record("stream_open", {})
        for index in range(count):
            seed = index + 5
            identity, body = f"{run}-ep-{index}", f"body-{index}"
            start, end = 100 + index * 10, 104 + index * 10
            success = index < (count if successes is None else successes)
            spec = {
                "seed": seed,
                "course_id": course,
                "spec_hash": f"spec-{seed}",
                "origin": [0, 80, 0],
                "spawn": [0.5, 0, 0.5],
                "yaw": 5 if fault == "repeat" else seed,
                "pitch": 0,
                "target": [0, 0, 3],
                "initial_inventory": [],
                "geometry": [{"block": "minecraft:oak_log", "position": [0, 0, 3]}],
            }
            specs.append(spec)
            episode = {
                "attempt": index + 1,
                "episode_id": identity,
                "body_id": body,
                "seed": seed,
                "start_tick": start,
                "end_tick": end,
                "status": "success" if success else "failed",
                "reason": "causal_target_break_drop_pickup"
                if success
                else "tick_limit",
                "drop_uuid": f"drop-{index}" if success else "",
                "picked_up": success,
                "target_broken": success,
            }
            episodes.append(episode)
            event(
                "course_reset_complete",
                {
                    "spec_hash": spec["spec_hash"],
                    "state_digest": "digest",
                    "body_state": {
                        "position": [0.5, 80, 0.5],
                        "yaw": spec["yaw"],
                        "pitch": 0,
                        "inventory": [],
                    },
                },
                start - 1,
                identity,
                body,
            )
            record(
                "request",
                {
                    "op": "controls",
                    "sequence": index + 1,
                    "controls": {
                        "observation_tick": start - 3,
                        "intended_apply_tick": start,
                        "expires_tick": start + 5,
                        "duration": 5,
                    },
                },
                identity,
                body,
            )
            event(
                "applied",
                {
                    "request_sequence": index + 1,
                    "actual_apply_tick": start + (fault == "timing"),
                },
                start,
                identity,
                body,
            )
            for tick in range(start, end):
                if fault == "tick" and tick == start:
                    continue
                event("policy_sample", {}, tick, identity, body)
            if success:
                for milestone in (
                    "causal_drop_created",
                    "target_broken",
                    "target_collected",
                ):
                    data = {"milestone": milestone, "tick": start + 1}
                    if milestone == "target_broken":
                        data["position"] = [0, 80, 3]
                    else:
                        data["drop_uuid"] = (
                            "wrong"
                            if fault == "uuid" and milestone == "target_collected"
                            else episode["drop_uuid"]
                        )
                    event("course_milestone", data, start + 1, identity, body)
            event("course_episode_complete", episode.copy(), end, identity, body)
        cleanup = {
            "episode_id": "cleanup",
            "body_id": "cleanup-body",
            "spec_hash": "C1",
            "status": "restored_initial_C1",
        }
        if fault != "cleanup":
            event(
                "course_reset_complete",
                {"spec_hash": "C1"},
                end + 1,
                cleanup["episode_id"],
                cleanup["body_id"],
            )
        summary = {
            "run_id": run,
            "course_id": course,
            "suite": "navigation_execution_v1",
            "profile_id": profile["profile_id"],
            "status": "completed_navigation_execution",
            "scored": True,
            "seeds": [s["seed"] for s in specs],
            "specs": specs,
            "episodes": episodes,
            "attempted_episodes": count,
            "successes": sum(e["status"] == "success" for e in episodes),
            "cleanup": cleanup,
            "source_commit": "test",
            "timing_profile": "three-tick",
            "controller_sha256": sha256(snapshot / "src/mclab/navigation.py"),
            "runner_sha256": sha256(snapshot / "src/mclab/navigation_runner.py"),
            "profile_hash": digest(profile),
            "course_profiles_hash": digest(profiles),
            "mod_provenance": {"source_hash": proof._native_hash(snapshot)},
        }
        path = directory / "summary.json"
        write_json(path, summary)
        (directory / "events.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
        ledger = EpisodeRecorder(directory / "episodes.sqlite3")
        ledger.begin_run(run, summary)
        for e in episodes:
            ledger.begin(
                run, e["attempt"], e["episode_id"], e["body_id"], "events.jsonl"
            )
            ledger.finish(e["episode_id"], e["status"], e["reason"], e)
        with ledger.batch():
            for row in rows:
                ledger.append(
                    run,
                    row["sequence"],
                    row["episode_id"],
                    row["type"],
                    row["monotonic_ns"],
                    row["payload"],
                )
        ledger.finish_run(run, summary["status"])
        ledger.close()
        if fault == "source":
            (snapshot / "src/mclab/navigation.py").write_text(
                "tampered", encoding="utf-8"
            )
        return path

    return build


def test_smoke_can_be_complete_without_qualifying_twenty_layouts(recorded):
    result = proof.audit(recorded())
    assert result["valid_recording"] and all(result["checks"].values())
    assert result["successes"] == 2 and result["applied_observation_ages"] == [3]
    assert not result["gate_eligible"]


@pytest.mark.parametrize("fault", ["timing", "tick", "uuid", "cleanup", "source"])
def test_incomplete_or_inconsistent_evidence_never_qualifies(recorded, fault):
    result = proof.audit(recorded(count=20, fault=fault))
    assert not result["valid_recording"] and not result["gate_eligible"]


def test_all_four_courses_need_twenty_layouts_and_eighteen_successes(recorded):
    paths = [
        recorded(course, count=20, successes=18) for course in ("C2", "C3", "C4", "C5")
    ]
    output = paths[0].parents[2] / "proof.json"
    result = proof.report(paths, output)
    assert result["G5_passed"] and result["attempted_episodes"] == 80
    assert result["successful_episodes"] == 72
    assert read_json(output)["status"] == "passed_development_navigation"
    missing_course = proof.report(paths[:3], output)
    assert not missing_course["G5_passed"]


def test_fewer_than_eighteen_successes_or_repeated_layouts_does_not_qualify(recorded):
    path = recorded(count=20, successes=17)
    assert proof.audit(path)["valid_recording"]
    assert not proof.audit(path)["gate_eligible"]
    result = proof.audit(recorded(count=20, fault="repeat", name="repeated"))
    assert result["valid_recording"] and result["unique_layouts"] == 1
    assert not result["gate_eligible"]


@pytest.mark.parametrize("fault", ["status", "event"])
def test_sqlite_disagreement_or_missing_critical_record_invalidates(recorded, fault):
    path = recorded(count=20)
    with sqlite3.connect(path.parent / "episodes.sqlite3") as db:
        if fault == "status":
            db.execute("UPDATE episodes SET status='failed'")
        else:
            db.execute(
                "DELETE FROM events WHERE sequence=(SELECT max(sequence) FROM events)"
            )
    result = proof.audit(path)
    assert not result["valid_recording"] and not result["gate_eligible"]


def test_different_execution_stacks_cannot_form_one_frozen_gate(recorded):
    paths = [recorded(course, count=20) for course in ("C2", "C3", "C4", "C5")]
    archive = paths[-1].parent / "sources/src/mclab/recorder.py"
    archive.write_text("different writer", encoding="utf-8")
    result = proof.report(paths, paths[0].parents[2] / "proof.json")
    assert all(r["gate_eligible"] for r in result["courses"].values())
    assert not result["uniform_controller_and_timing"] and not result["G5_passed"]


def test_duplicate_course_batches_are_rejected(recorded):
    path = recorded()
    with pytest.raises(ValueError, match="one development batch"):
        proof.report([path, path], path.parent / "proof.json")
