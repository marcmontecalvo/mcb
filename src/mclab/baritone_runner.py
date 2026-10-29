"""Server-scored Baritone reference attempts on the navigation courses.

The Baritone client player is the scored subject: the mod resets the course with the
player parked above it, places the player at spawn, and scores the same causal
break/drop/pickup chain as for Carpet bodies. Baritone sees the full client world;
it is a reference, not a filtered-observation policy.
"""

import time
from datetime import UTC, datetime
from pathlib import Path

from mclab import courses, gates
from mclab.baritone_client import BaritoneClient
from mclab.bootstrap import sha256
from mclab.execution import RecordedBridge, terminal_event
from mclab.modbuild import source_hash
from mclab.navigation_runner import (
    _new_identity,
    _restore_initial,
    _source_commit,
    _validate,
    _verify_reset,
)
from mclab.paths import ROOT, SERVER, config, digest, read_json, write_json
from mclab.recorder import EpisodeRecorder, exclusive_arena

POLL_SECONDS = 0.25


def _wait_terminal(client, wall_timeout):
    deadline = time.monotonic() + wall_timeout
    while time.monotonic() < deadline:
        client.state(heartbeat=True)
        terminal = terminal_event(client)
        if terminal is not None:
            return terminal
        time.sleep(POLL_SECONDS)
    raise TimeoutError("Baritone attempt exceeded its wall-clock deadline")


def _attempt(client, baritone, player, profile, spec):
    # Naming the player makes this a lab-only reset of that client; no body is spawned.
    request = client.send(
        "course_reset",
        player=player,
        profile_id=profile["profile_id"],
        course_spec=spec,
    )
    client.acknowledge(request, "course_reset_started", timeout=10)
    reset = client.acknowledge(request, "course_reset_complete", timeout=90)
    _verify_reset(reset["data"], spec)
    request = client.send("course_begin")
    client.acknowledge(request, "course_started")
    started = time.monotonic()
    baritone.mine("minecraft:oak_log", 1)
    try:
        terminal = _wait_terminal(client, spec["tick_limit"] / 20 + 30)
    finally:
        baritone.cancel()
    data = terminal["data"]
    return {
        **data,
        "ticks": data["end_tick"] - data["start_tick"],
        "wall_seconds": round(time.monotonic() - started, 3),
    }


def execute(course, seeds):
    _validate(course, seeds)
    with exclusive_arena():
        return _run(course, seeds)


def _run(course, seeds):
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError("Installed mod differs from source")
    profile = gates.require_navigation_profile(
        course, ("forward", "look", "jump", "mine", "pickup"), scored=False
    )
    active = read_json(SERVER / "world/datapacks/mclab/evaluator/active.json")
    if active["course_id"] != "C1":
        raise ValueError("Baritone runner requires initial C1 startup specification")
    baritone = BaritoneClient()
    status = baritone.status()
    if not status.get("in_world"):
        raise RuntimeError("Baritone client is not in the lab world")
    player = status["player"]
    baritone.policy(allowPlace=False, allowInventory=False)
    specs = [courses.generate(course, seed, config()["origin"]) for seed in seeds]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/baritone" / stamp
    summary = {
        "schema_version": 1,
        "controller": "baritone-reference-v1.20.0",
        "suite": "baritone_reference_v1",
        "subject": "human_client",
        "player": player,
        "course_id": course,
        "seeds": seeds,
        "scored": False,
        "baritone_sha256": read_json(ROOT / "config/baselines.lock.json")["baritone"][
            "sha256"
        ],
        "profile_hash": digest(profile),
        "mod_provenance": provenance,
        "runner_sha256": sha256(Path(__file__)),
        "source_commit": _source_commit(),
        "specs": specs,
        "episodes": [],
        "status": "running",
    }
    client = RecordedBridge(destination / "events.jsonl")
    client.budget = len(seeds) * 4000 + 500
    recorder = EpisodeRecorder(destination / "episodes.sqlite3")
    recorder.begin_run(client.run_id, summary)
    client.recorder = recorder
    summary["run_id"] = client.run_id
    try:
        client.synchronize()
        for attempt, spec in enumerate(specs, 1):
            _new_identity(client, "baritone")
            recorder.begin(
                client.run_id,
                attempt,
                client.episode_id,
                client.body_id,
                destination / "events.jsonl",
            )
            outcome = _attempt(client, baritone, player, profile, spec)
            client.flush_recording()
            recorder.finish(
                client.episode_id, outcome["status"], outcome["reason"], outcome
            )
            summary["episodes"].append(
                {"attempt": attempt, "seed": spec["seed"], **outcome}
            )
            write_json(destination / "summary.json", summary)
        summary["status"] = "completed_baritone_reference"
    except Exception as error:
        summary.update(status="invalid", error=str(error))
    finally:
        try:
            baritone.cancel()
            request = client.send("stop", reason="human_course_stopped")
            client.acknowledge(request, "stopped")
        except Exception as error:
            summary["stop_error"] = str(error)
        try:
            _restore_initial(client, summary, destination)
        except Exception as error:
            summary.update(status="invalid", cleanup_error=str(error))
        client.close()
        summary["successes"] = sum(
            e["status"] == "success" for e in summary["episodes"]
        )
        recorder.finish_run(client.run_id, summary["status"])
        recorder.close()
        write_json(destination / "summary.json", summary)
    return summary
