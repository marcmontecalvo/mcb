"""Bounded, unscored live C1 tactical experiments; separate from frozen G3."""

import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab import courses, gates
from mclab.backends import BackendError, HttpBackend
from mclab.bootstrap import sha256
from mclab.execution import (
    ControllerFailure,
    PolicyMapRecorder,
    RecordedBridge,
    policy_state,
    terminal_event,
)
from mclab.modbuild import source_hash
from mclab.paths import ROOT, SERVER, digest, read_json, write_json
from mclab.protocolproof import controls
from mclab.recorder import EpisodeRecorder, exclusive_arena
from mclab.tactical import TacticalController


def record_selector(client, controller):
    for kind, payload in controller.drain_events():
        client.record(kind, payload)


def close_selector(client, controller, backend, summary):
    try:
        controller.close()
    finally:
        # A shutdown failure must not erase already admitted provider requests.
        summary["model_requests"] += backend.requests
        try:
            record_selector(client, controller)
        finally:
            client.record("backend_provenance", backend.provenance())


def drive(client, controller, wall_timeout=90):
    """Renew motor commands independently of inference; never retry old commands."""
    deadline = time.monotonic() + wall_timeout
    action_index = discarded = 0
    try:
        while time.monotonic() < deadline:
            observation = policy_state(client)
            terminal = terminal_event(client)
            if terminal is not None:
                return terminal
            if observation["state"] == "episode_terminal":
                client.wait(lambda _: terminal_event(client) is not None, timeout=2)
                return terminal_event(client)
            if observation["state"] != "running":
                raise RuntimeError("Policy admission closed before terminal result")
            try:
                decision = controller.decide(observation)
            finally:
                record_selector(client, controller)
            action_index += 1
            tick = observation["tick"]
            request = client.send(
                "controls",
                motor=True,
                controls=controls(
                    tick,
                    f"model-{action_index}",
                    duration=5,
                    intended_apply_tick=tick + 3,
                    expires_tick=tick + 8,
                    **decision,
                ),
            )
            client.wait(
                lambda _, sequence=request["sequence"]: terminal_event(client)
                is not None
                or any(
                    e["type"] in {"applied", "rejected", "rejected_at_execution"}
                    and int(e["data"].get("request_sequence", -1)) == sequence
                    for e in client.events
                ),
                timeout=5,
            )
            rejection = next(
                (
                    e
                    for e in reversed(client.events)
                    if e["type"] in {"rejected", "rejected_at_execution"}
                    and int(e["data"].get("request_sequence", -1))
                    == request["sequence"]
                ),
                None,
            )
            if rejection and terminal_event(client) is None:
                reason = rejection["data"]["reason"]
                if reason in {"late_or_distant_action", "stale_or_mistimed_action"}:
                    discarded += 1
                    client.record(
                        "discarded_stale_action",
                        {
                            "request_sequence": request["sequence"],
                            "reason": reason,
                            "count": discarded,
                            "limit": 3,
                        },
                    )
                    if discarded <= 3:
                        release = client.send("release")
                        client.acknowledge(release, "received")
                        continue
                raise ControllerFailure("Controller action rejected: " + reason)
        raise ControllerFailure("Controller wall deadline exceeded")
    except (BackendError, ControllerFailure, TimeoutError) as error:
        client.record(
            "controller_error",
            {
                "error": str(error),
                "category": "timing"
                if isinstance(error, TimeoutError) or "deadline" in str(error).lower()
                else "decision",
            },
        )
        if terminal_event(client) is None:
            release = client.send("release")
            client.acknowledge(release, "received")
            end = client.send("course_end", reason="controller_execution_error")
            client.acknowledge(end, "received")
            client.wait(lambda _: terminal_event(client) is not None)
        return terminal_event(client)


def run(backend_name, *, count=1, request_limit=40):
    if type(count) is not int or not 1 <= count <= 5:
        raise ValueError("Live model smoke count must be 1..5")
    if type(request_limit) is not int or not 1 <= request_limit <= 100:
        raise ValueError("Total model request budget must be 1..100")
    if request_limit < count:
        raise ValueError("Require at least one request per episode")
    specs = read_json(ROOT / "config/backends.json")["backends"]
    matches = [s for s in specs if s["name"] == backend_name and s.get("enabled")]
    if len(matches) != 1:
        raise ValueError("Select exactly one enabled configured backend")
    selected = matches[0]
    # Remote budgets must be explicitly supplied in configuration; never increase
    # a remote request cap just because a CLI option asks for a larger batch.
    probe = HttpBackend({**selected, "request_limit": request_limit})
    try:
        if not probe.local and request_limit > selected["request_limit"]:
            raise ValueError("Live request cap exceeds configured remote batch limit")
        if probe.deadline != 1.0:
            raise ValueError("Initial live profile requires the frozen 1s deadline")
    finally:
        probe.close()
    spec = {**selected, "request_limit": request_limit // count}
    with exclusive_arena():
        return _run(spec, count, request_limit)


def _run(spec, count, request_limit):
    profile = gates.require_initial_profile("C1", ("forward", "look", "mine", "pickup"))
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError("Installed mod differs from source")
    gates.require_scored_execution(provenance["source_hash"], "local-visibility-v1")
    generated = courses.verify_generated()
    active = read_json(SERVER / "world/datapacks/mclab/evaluator/active.json")
    if (
        active["course_id"] != "C1"
        or active["tick_limit"] != 600
        or generated["spec_hash"]
        != read_json(ROOT / "docs/gates.json")["G2"]["spec_hash"]
    ):
        raise ValueError("Live smoke requires frozen G2 C1 specification")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/model_execution" / stamp
    client = RecordedBridge(destination / "events.jsonl")
    client.budget = 12000
    recorder = EpisodeRecorder(ROOT / "results/episodes.sqlite3")
    configuration = {
        "schema_version": 1,
        "controller": "bounded-visible-target-tactical-v1",
        "timing_profile": "async-tactical-1s-motor-three-tick-v1",
        "scored": False,
        "track": "perception_goal",
        "requested_episodes": count,
        "model_request_limit_total": request_limit,
        "backend": spec,
        "course_spec_hash": generated["spec_hash"],
        "course_tick_limit": 600,
        "sensor_profile": "local-visibility-v1",
        "profile_hash": digest(profile),
        "mod_provenance": provenance,
        "controller_sha256": sha256(Path(__file__)),
        "tactical_sha256": sha256(ROOT / "src/mclab/tactical.py"),
        "backend_sha256": sha256(ROOT / "src/mclab/backends.py"),
        "source_commit": subprocess.run(
            ["rtk", "git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "recovered_interrupted": recorder.recover_interrupted(),
    }
    summary = {
        **configuration,
        "run_id": client.run_id,
        "episodes": [],
        "recording": str(destination / "events.jsonl"),
        "model_requests": 0,
    }
    recorder.begin_run(client.run_id, configuration)
    client.recorder = recorder
    active_episode = None
    try:
        # Exact executable sources are retained independently of later commits.
        import shutil

        shutil.copytree(
            ROOT / "src/mclab",
            destination / "sources/mclab",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        client.synchronize()
        for attempt in range(1, count + 1):
            client.episode_id = "episode-" + uuid4().hex[:12]
            client.body_id = "body-" + uuid4().hex[:12]
            client.policy_memory = PolicyMapRecorder()
            active_episode = client.episode_id
            recorder.begin(
                client.run_id,
                attempt,
                active_episode,
                client.body_id,
                destination / "events.jsonl",
            )
            request = client.send("course_reset", profile_id=profile["profile_id"])
            client.acknowledge(request, "course_reset_started", timeout=10)
            reset = client.acknowledge(request, "course_reset_complete", timeout=90)
            if reset["data"]["spec_hash"] != generated["spec_hash"]:
                raise RuntimeError("Server reset specification mismatch")
            request = client.send("course_begin")
            client.acknowledge(request, "course_started")
            client.wait(
                lambda s: s["state"] == "running" or terminal_event(client) is not None
            )
            backend = HttpBackend(spec)
            controller = TacticalController(backend)
            try:
                terminal = drive(client, controller)
            finally:
                close_selector(client, controller, backend, summary)
            client.flush_recording()
            outcome = terminal["data"]
            recorder.finish(
                active_episode, outcome["status"], outcome["reason"], terminal
            )
            summary["episodes"].append({"episode_id": active_episode, **outcome})
            active_episode = None
            write_json(destination / "summary.json", {**summary, "status": "running"})
        summary["status"] = "completed_development_smoke"
    except Exception as error:
        summary.update(status="invalid", error=str(error))
        if active_episode is not None:
            outcome = {
                "status": "invalid",
                "reason": "runner_error",
                "error": str(error),
            }
            recorder.finish(active_episode, "invalid", "runner_error", outcome)
            summary["episodes"].append({"episode_id": active_episode, **outcome})
    finally:
        try:
            request = client.send("despawn")
            client.acknowledge(request, "received")
            client.wait(lambda s: s["state"] == "idle")
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
            recorder.finish_run(client.run_id, summary["status"])
            recorder.close()
            write_json(destination / "summary.json", summary)
    return summary
