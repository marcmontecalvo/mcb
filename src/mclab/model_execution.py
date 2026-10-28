"""Bounded, unscored live C1 tactical experiments; separate from frozen G3.

The motor loop lives in `control_loop`; it is re-exported here for old callers.
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab import courses, gates
from mclab.backends import HttpBackend
from mclab.bootstrap import sha256
from mclab.control_loop import drive, record_selector
from mclab.execution import (
    ControllerFailure,
    PolicyMapRecorder,
    RecordedBridge,
    terminal_event,
)
from mclab.modbuild import source_hash
from mclab.paths import ROOT, SERVER, digest, read_json, write_json
from mclab.recorder import EpisodeRecorder, exclusive_arena
from mclab.tactical import TacticalController

__all__ = ["ControllerFailure", "close_selector", "drive", "record_selector", "run"]


def close_selector(client, controller, backend, summary):
    try:
        controller.close()
    finally:
        # A shutdown failure must not erase already admitted provider requests.
        summary["model_requests"] += backend.requests
        planner = getattr(controller, "planner_backend", None)
        if planner is not None:
            summary["model_requests"] += planner.requests
            summary["planner_model_requests"] = (
                summary.get("planner_model_requests", 0) + planner.requests
            )
            summary["tactical_model_requests"] = (
                summary.get("tactical_model_requests", 0) + backend.requests
            )
            summary["planner_failures"] = summary.get("planner_failures", 0) + getattr(
                controller, "planner_failures", 0
            )
            summary["degraded"] = summary.get("degraded", False) or bool(
                getattr(controller, "degraded_events", 0)
            )
        try:
            record_selector(client, controller)
        finally:
            try:
                client.record("backend_provenance", backend.provenance())
            finally:
                if planner is not None:
                    client.record("planner_backend_provenance", planner.provenance())


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


def run_tandem(
    tactical_backend_name,
    planner_backend_name,
    *,
    planner_profile,
    count=1,
    request_limit=40,
    planner_request_limit=4,
):
    """Optional C1 tandem experiment; validate both roles before arena access."""
    from mclab.tandem import TandemController

    if (
        type(count) is not int
        or not 1 <= count <= 5
        or any(
            type(cap) is not int or not count <= cap <= 100
            for cap in (request_limit, planner_request_limit)
        )
    ):
        raise ValueError("Require 1..5 episodes and bounded per-role request caps")
    optional = {"planner_interval_s", "plan_ttl_ticks", "planner_failure"}
    if (
        not isinstance(planner_profile, dict)
        or not {"instructions", "candidates"} <= planner_profile.keys()
        or not planner_profile.keys() <= optional | {"instructions", "candidates"}
    ):
        raise ValueError("Planner profile requires instructions and candidates")
    kwargs = {
        "planner_instructions": planner_profile["instructions"],
        "planner_candidates": planner_profile["candidates"],
        **{key: planner_profile[key] for key in optional if key in planner_profile},
    }
    specs = read_json(ROOT / "config/backends.json")["backends"]
    selected, probes = [], []
    try:
        for name, cap in (
            (tactical_backend_name, request_limit),
            (planner_backend_name, planner_request_limit),
        ):
            matches = [s for s in specs if s["name"] == name and s.get("enabled")]
            if len(matches) != 1:
                raise ValueError("Select one configured backend for each tandem role")
            original = matches[0]
            spec = {**original, "request_limit": cap}
            probe = HttpBackend(spec)
            probes.append(probe)
            if not probe.local and cap > original["request_limit"]:
                raise ValueError("Tandem role exceeds configured remote request cap")
            selected.append(spec)
        remote = [s for s, p in zip(selected, probes, strict=True) if not p.local]
        if remote and sum(
            s["request_limit"] * s["max_request_cost_usd"] for s in remote
        ) > min(s["cost_ceiling_usd"] for s in remote):
            raise ValueError("Tandem combined conservative cost budget exceeded")
        if probes[0].deadline != 1.0:
            raise ValueError("Initial tandem tactical profile requires 1s deadline")
        # Validate all controller options without provider calls or world access.
        TandemController(probes[1], probes[0], **kwargs).close()
    finally:
        for probe in probes:
            probe.close()
    tactical, planner = selected
    tactical = {**tactical, "request_limit": request_limit // count}
    planner = {**planner, "request_limit": planner_request_limit // count}
    with exclusive_arena():
        return _run(
            tactical,
            count,
            request_limit,
            planner_spec=planner,
            planner_profile=kwargs,
        )


def _run(spec, count, request_limit, *, planner_spec=None, planner_profile=None):
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
        "control_loop_sha256": sha256(ROOT / "src/mclab/control_loop.py"),
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
    if planner_spec is not None:
        from mclab.tandem import PROFILE

        configuration.update(
            controller=PROFILE,
            experiment_mode="tandem",
            timing_profile="async-tandem-1s-tactical-independent-planner-v1",
            tandem_sha256=sha256(ROOT / "src/mclab/tandem.py"),
            planner_backend=planner_spec,
            planner_profile=planner_profile,
            tactical_request_limit_total=request_limit,
            planner_request_limit_total=planner_spec["request_limit"] * count,
            model_request_limit_total=request_limit
            + planner_spec["request_limit"] * count,
        )
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
            if planner_spec is None:
                controller = TacticalController(backend)
            else:
                from mclab.tandem import TandemController

                planner = None
                try:
                    planner = HttpBackend(planner_spec)
                    controller = TandemController(planner, backend, **planner_profile)
                except BaseException:
                    backend.close()
                    if planner is not None:
                        planner.close()
                    raise
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
