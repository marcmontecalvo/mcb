"""Recorded navigation reset qualification and deterministic course attempts.

The navigation profile is separate from the frozen initial C1 profile. Generated
specifications reach only the authenticated reset/evaluator interface; controller
decisions receive the filtered policy stream through the shared motor driver.
"""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab import courses, gates
from mclab.bootstrap import sha256
from mclab.execution import PolicyMapRecorder, RecordedBridge, terminal_event
from mclab.modbuild import source_hash
from mclab.model_execution import drive
from mclab.paths import ROOT, SERVER, config, digest, read_json, write_json
from mclab.recorder import EpisodeRecorder, episode_databases, exclusive_arena


def _validate(course, seeds):
    if course not in {"C2", "C3", "C4", "C5"}:
        raise ValueError("Navigation requires C2, C3, C4 or C5")
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 20:
        raise ValueError("Navigation requires 1..20 explicit seeds")
    if any(type(seed) is not int or not 0 <= seed < 2**31 for seed in seeds):
        raise ValueError("Navigation seeds must be integers in [0,2**31)")


def _new_controller():
    from mclab.navigation import NavigationController

    class Controller:
        def __init__(self):
            self.navigation = NavigationController()

        def decide(self, observation):
            return self.navigation.decide(observation)

        def drain_events(self):
            return []

    return Controller()


def _source_commit():
    return subprocess.run(
        ["rtk", "git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _new_identity(client, prefix="navigation"):
    # Historical payloads already live in JSONL/SQLite. Retaining their objects
    # across episodes grows the heap and makes event searches/GC progressively
    # slower. Flush before releasing them, while policy admission is closed.
    client.flush_recording()
    client.events.clear()
    client.samples.clear()
    client.episode_id = prefix + "-" + uuid4().hex[:12]
    client.body_id = "body-" + uuid4().hex[:12]
    client.policy_memory = PolicyMapRecorder()
    client.last_resource_tick = -20


def _verify_reset(data, spec):
    if data.get("spec_hash") != spec["spec_hash"]:
        raise RuntimeError(
            "Server reset specification differs from generated navigation"
        )
    body = data.get("body_state", {})
    position = [a + b for a, b in zip(spec["origin"], spec["spawn"], strict=True)]
    if body.get("position") != position:
        raise RuntimeError("Navigation reset position differs from specification")
    if body.get("yaw") != spec["yaw"] or body.get("pitch") != spec["pitch"]:
        raise RuntimeError("Navigation reset heading differs from specification")
    if body.get("inventory") != [] or not data.get("state_digest"):
        raise RuntimeError("Navigation reset inventory/digest is invalid")


def _reset(client, profile, spec, profiles_hash):
    if digest(read_json(ROOT / "courses/profiles.json")) != profiles_hash:
        raise RuntimeError("Course profiles changed during navigation run")
    request = client.send(
        "course_reset", profile_id=profile["profile_id"], course_spec=spec
    )
    client.acknowledge(request, "course_reset_started", timeout=10)
    event = client.acknowledge(request, "course_reset_complete", timeout=90)
    _verify_reset(event["data"], spec)
    return event


def _restore_initial(client, summary, destination):
    """Restore C1 under a fresh identity, separately from scored attempts."""
    _new_identity(client, "restore-c1")
    summary["cleanup"] = {
        "status": "pending",
        "episode_id": client.episode_id,
        "body_id": client.body_id,
    }
    write_json(destination / "summary.json", summary)
    client.record("navigation_cleanup_started", summary["cleanup"])
    client.flush_recording()
    profile = gates.require_initial_profile("C1")
    request = client.send("course_reset", profile_id=profile["profile_id"])
    client.acknowledge(request, "course_reset_started", timeout=10)
    event = client.acknowledge(request, "course_reset_complete", timeout=90)
    initial = read_json(SERVER / "world/datapacks/mclab/evaluator/active.json")
    _verify_reset(event["data"], initial)
    request = client.send("despawn")
    client.acknowledge(request, "received")
    client.wait(lambda state: state["state"] == "idle")
    summary["cleanup"].update(
        status="restored_initial_C1", spec_hash=initial["spec_hash"]
    )


def qualify_resets(course, seed=0, count=20):
    if type(count) is not int or not 1 <= count <= 20:
        raise ValueError("Navigation reset count must be 1..20")
    seeds = [seed] * count
    _validate(course, seeds)
    with exclusive_arena():
        return _run(course, seeds, reset_only=True, scored=False)


def execute(course, seeds, *, scored=False):
    _validate(course, seeds)
    if type(scored) is not bool:
        raise ValueError("scored must be an explicit boolean")
    if len(set(seeds)) != len(seeds):
        raise ValueError("Execution seeds must be distinct within a batch")
    with exclusive_arena():
        return _run(course, seeds, reset_only=False, scored=scored)


def _run(course, seeds, *, reset_only, scored):
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError("Installed navigation mod differs from source")
    profile = gates.require_navigation_profile(
        course, ("forward", "look", "jump", "mine", "pickup"), scored=scored
    )
    if scored:
        qualification = read_json(ROOT / "docs/NAVIGATION_QUALIFICATION.json")[
            "courses"
        ][course]
        if (
            qualification.get("status") != "passed"
            or qualification.get("profile_hash") != digest(profile)
            or qualification.get("mod_source_hash") != provenance["source_hash"]
        ):
            raise ValueError(
                "Scored navigation requires current course reset qualification"
            )
    active = read_json(SERVER / "world/datapacks/mclab/evaluator/active.json")
    if active["course_id"] != "C1":
        raise ValueError("Navigation runner requires initial C1 startup specification")
    courses.verify_generated()
    profiles_hash = digest(read_json(ROOT / "courses/profiles.json"))
    specs = [courses.generate(course, seed, config()["origin"]) for seed in seeds]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/navigation" / stamp
    request_budget = len(seeds) * (2 * max(s["tick_limit"] for s in specs) + 500) + 500
    configuration = {
        "schema_version": 1,
        "controller": "navigation-reset-verifier"
        if reset_only
        else "deterministic-navigation-v2",
        "suite": "navigation_reset_v1" if reset_only else "navigation_execution_v1",
        "course_id": course,
        "seeds": seeds,
        "scored": scored,
        "timing_profile": "navigation-three-tick-bounded-resense-v1",
        "intended_apply_offset_ticks": 3,
        "action_duration_ticks": 5,
        "maximum_discarded_stale_actions": 3,
        "profile_id": profile["profile_id"],
        "profile_hash": digest(profile),
        "sensor_profile": profile["sensor_profile"],
        "mod_provenance": provenance,
        "course_profiles_hash": profiles_hash,
        "specs": specs,
        "runner_sha256": sha256(Path(__file__)),
        "controller_sha256": sha256(ROOT / "src/mclab/navigation.py"),
        "source_commit": _source_commit(),
        "episode_database": str((destination / "episodes.sqlite3").relative_to(ROOT)),
    }
    client = RecordedBridge(destination / "events.jsonl")
    client.budget = request_budget
    try:
        recovered = 0
        for database_path in episode_databases(ROOT):
            previous = EpisodeRecorder(database_path)
            try:
                recovered += previous.recover_interrupted()
            finally:
                previous.close()
        recorder = EpisodeRecorder(destination / "episodes.sqlite3")
    except BaseException:
        client.close()
        raise
    try:
        configuration["recovered_interrupted"] = recovered
        recorder.begin_run(client.run_id, configuration)
    except BaseException:
        try:
            client.close()
        finally:
            recorder.close()
        raise
    client.recorder = recorder
    summary = {
        **configuration,
        "run_id": client.run_id,
        "launch_id": client.launch_id,
        "request_budget": client.budget,
        "episodes": [],
        "resets": [],
        "recording": str(destination / "events.jsonl"),
        "status": "running",
    }
    active_episode = None
    try:
        for relative in (
            "src/mclab",
            "mod/src",
            "config/navigation_body_profile.json",
            "courses/profiles.json",
        ):
            source, target = ROOT / relative, destination / "sources" / relative
            if source.is_dir():
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        client.synchronize()
        first_digest = None
        previous_generation = None
        for attempt, spec in enumerate(specs, 1):
            _new_identity(client)
            recorder.begin(
                client.run_id,
                attempt,
                client.episode_id,
                client.body_id,
                destination / "events.jsonl",
            )
            active_episode = client.episode_id
            reset = _reset(client, profile, spec, profiles_hash)
            data = reset["data"]
            if reset_only:
                first_digest = first_digest or data["state_digest"]
                if data["state_digest"] != first_digest or (
                    previous_generation is not None
                    and data["generation"] != previous_generation + 1
                ):
                    raise RuntimeError("Navigation reset digest/generation mismatch")
                previous_generation = data["generation"]
                summary["resets"].append(
                    {"attempt": attempt, "seed": spec["seed"], **data}
                )
                outcome = {
                    "status": "success",
                    "reason": "matching_authoritative_reset",
                }
                terminal = {"reset": reset, **outcome}
            else:
                request = client.send("course_begin")
                client.acknowledge(request, "course_started")
                client.wait(
                    lambda state: state["state"] == "running"
                    or terminal_event(client) is not None
                )
                terminal = drive(
                    client, _new_controller(), wall_timeout=spec["tick_limit"] / 20 + 30
                )
                outcome = terminal["data"]
            client.flush_recording()
            recorder.finish(
                active_episode, outcome["status"], outcome["reason"], terminal
            )
            summary["episodes"].append(
                {
                    "attempt": attempt,
                    "episode_id": active_episode,
                    "body_id": client.body_id,
                    "seed": spec["seed"],
                    "spec_hash": spec["spec_hash"],
                    **outcome,
                }
            )
            active_episode = None
            write_json(destination / "summary.json", summary)
        summary["status"] = (
            "passed_development_reset"
            if reset_only
            else "completed_navigation_execution"
        )
    except BaseException as error:
        interrupted = not isinstance(error, Exception)
        summary.update(
            status="interrupted" if interrupted else "invalid", error=str(error)
        )
        try:
            client.record(
                "navigation_runner_error",
                {"error": str(error), "type": type(error).__name__},
            )
        except Exception as recording_error:
            summary["recording_error"] = str(recording_error)
        if active_episode is not None:
            outcome = {
                "status": "interrupted" if interrupted else "invalid",
                "reason": "runner_interrupted" if interrupted else "runner_error",
                "error": str(error),
                "observed_native_terminal": terminal_event(client),
            }
            recorder.finish(
                active_episode, outcome["status"], outcome["reason"], outcome
            )
            summary["episodes"].append({"episode_id": active_episode, **outcome})
        if interrupted:
            raise
    finally:
        try:
            _restore_initial(client, summary, destination)
        except Exception as error:
            summary.update(status="invalid", cleanup_error=str(error))
        try:
            client.close()
        except Exception as error:
            summary.update(status="invalid", recording_error=str(error))
        finally:
            summary["attempted_episodes"] = len(summary["episodes"])
            summary["successes"] = sum(
                e["status"] == "success" for e in summary["episodes"]
            )
            summary["requests"] = client.submitted
            recorder.finish_run(client.run_id, summary["status"])
            recorder.close()
            write_json(destination / "summary.json", summary)
    return summary
