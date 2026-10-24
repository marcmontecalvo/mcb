"""Deterministic C1 perception-goal controller and fully recorded live attempts."""

import math
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab import courses, gates
from mclab.bootstrap import sha256
from mclab.bridge import Bridge
from mclab.modbuild import source_hash
from mclab.paths import ROOT, SERVER, digest, read_json, write_json
from mclab.protocolproof import controls
from mclab.recorder import EpisodeRecorder, exclusive_arena


class RecordedBridge(Bridge):
    recorder: EpisodeRecorder | None = None
    policy_memory = None
    last_resource_tick = -20

    def record(self, kind, payload):
        super().record(kind, payload)
        if self.recorder is not None:
            self.recorder.append(
                self.run_id,
                self.record_sequence,
                self.episode_id,
                kind,
                time.monotonic_ns(),
                payload,
            )


class ControllerFailure(RuntimeError):
    """A controller execution error counts as a failed live attempt."""


def wrapped_delta(target: float, current: float) -> float:
    return (target - current + 180) % 360 - 180


class C1Controller:
    """Only consumes the filtered policy snapshot; no manifest or score access."""

    def __init__(self):
        self.target = None
        self.mining = False
        self.broken = False

    def decide(self, observation: dict) -> dict:
        if observation.get("sensor_profile") != "local-visibility-v1":
            raise ValueError("Unsupported policy sensor profile")
        body = observation["body"]
        position = body["position"]
        visible = observation.get("visible_blocks", [])
        logs = [b for b in visible if b["block"] == "minecraft:oak_log"]
        if self.target is None and logs:
            nearest = min(
                logs,
                key=lambda b: sum(
                    (b["position"][i] + 0.5 - position[i]) ** 2 for i in range(3)
                ),
            )
            self.target = list(nearest["position"])
        if self.target is None:
            # Explore the initial facing direction. A target outside the sensor
            # radius must become visible naturally, never through course metadata.
            return {"forward": 1, "pitch_delta": max(-45, min(45, -body["pitch"]))}
        target = [value + 0.5 for value in self.target]
        dx, dz = target[0] - position[0], target[2] - position[2]
        horizontal = math.hypot(dx, dz)
        yaw = math.degrees(math.atan2(-dx, dz))
        pitch = -math.degrees(math.atan2(target[1] - (position[1] + 1.62), horizontal))
        yaw_delta = wrapped_delta(yaw, body["yaw"])
        pitch_delta = pitch - body["pitch"]
        target_observation = next(
            (b for b in visible if list(b["position"]) == self.target), None
        )
        if self.mining and target_observation is not None:
            self.broken = target_observation["block"] != "minecraft:oak_log"
        aimed = abs(yaw_delta) < 2 and abs(pitch_delta) < 2
        mine = (
            not self.broken
            and horizontal <= 3.1
            and aimed
            and body.get("ray", {}).get("block") == "minecraft:oak_log"
        )
        self.mining = self.mining or mine
        move = horizontal > (0.35 if self.broken else 2.8) and abs(yaw_delta) < 10
        return {
            "forward": 1 if move else 0,
            "yaw_delta": max(-45, min(45, yaw_delta)),
            "pitch_delta": max(-45, min(45, pitch_delta)),
            "mine": mine,
        }


def terminal_event(client: Bridge) -> dict | None:
    return next(
        (
            event
            for event in reversed(client.events)
            if event["type"] == "course_episode_complete"
            and event.get("run_id") == client.run_id
            and event.get("episode_id") == client.episode_id
            and event.get("body_id") == client.body_id
        ),
        None,
    )


class PolicyMapRecorder:
    """Replayable observed-cell memory; absent cells remain unknown, never air."""

    def __init__(self):
        self.cells = {}
        self.observations = 0

    def update(self, observation: dict) -> tuple[str, dict]:
        self.observations += 1
        changes = []
        for block in observation.get("visible_blocks", []):
            cell = {
                **block,
                "observed_tick": block.get("observed_tick", observation["tick"]),
            }
            key = tuple(block["position"])
            if self.cells.get(key) != cell:
                self.cells[key] = cell
                changes.append(cell)
        full = self.observations == 1 or self.observations % 20 == 0
        return ("policy_map_full" if full else "policy_map_delta"), {
            "tick": observation["tick"],
            "unknown_is_air": False,
            "cells": list(self.cells.values()) if full else changes,
        }


def policy_state(client: Bridge) -> dict:
    client.state()  # Heartbeat and evaluator event recording never reach decide().
    started = time.monotonic_ns()
    observation = client.http("/policy")
    latency_ms = (time.monotonic_ns() - started) / 1_000_000
    for key in ("run_id", "episode_id", "body_id"):
        if observation.get(key) != getattr(client, key):
            raise RuntimeError("Policy observation belongs to another attempt")
    client.record("policy_observation", observation)
    memory = getattr(client, "policy_memory", None)
    if memory is not None:
        kind, payload = memory.update(observation)
        client.record(kind, payload)
    if observation["tick"] - getattr(client, "last_resource_tick", -20) >= 20:
        client.record(
            "resource_sample",
            {
                "tick": observation["tick"],
                "policy_http_latency_ms": latency_ms,
                "server": observation.get("resources", {"status": "unavailable"}),
            },
        )
        client.last_resource_tick = observation["tick"]
    return observation


def run_controller(client: Bridge, wall_timeout: float = 90) -> dict:
    try:
        return _drive_controller(client, wall_timeout)
    except ControllerFailure as error:
        client.record("controller_error", {"error": str(error)})
        request = client.send("course_end", reason="controller_execution_error")
        client.acknowledge(request, "received")
        client.wait(lambda _state: terminal_event(client) is not None)
        return terminal_event(client)


def _drive_controller(client: Bridge, wall_timeout: float) -> dict:
    controller = C1Controller()  # No memory survives an episode boundary.
    deadline = time.monotonic() + wall_timeout
    action_index = 0
    while time.monotonic() < deadline:
        observation = policy_state(client)
        terminal = terminal_event(client)
        if terminal is not None:
            return terminal
        if observation["state"] != "running":
            if observation["state"] == "episode_terminal":
                # /policy can observe a newer tick than the preceding event
                # drain. Obtain the authoritative event before classifying it.
                client.wait(lambda _: terminal_event(client) is not None, timeout=2)
                return terminal_event(client)
            raise RuntimeError("Policy admission closed before terminal result")
        decision = controller.decide(observation)
        action_index += 1
        tick = observation["tick"]
        request = client.send(
            "controls",
            motor=True,
            controls=controls(
                tick,
                f"c1-{action_index}",
                duration=5,
                intended_apply_tick=tick + 4,
                expires_tick=tick + 9,
                **decision,
            ),
        )
        # Keep renewing mining before expiry; gameplay continues during polling.
        # Terminal success can arrive before the final action acknowledgement.
        client.wait(
            lambda _state, sequence=request["sequence"]: terminal_event(client)
            is not None
            or any(
                e["type"] in {"applied", "rejected", "rejected_at_execution"}
                and int(e["data"].get("request_sequence", -1)) == sequence
                for e in client.events
            ),
            timeout=5,
        )
        rejected = next(
            (
                e
                for e in reversed(client.events)
                if e["type"] in {"rejected", "rejected_at_execution"}
                and int(e["data"].get("request_sequence", -1)) == request["sequence"]
            ),
            None,
        )
        if rejected is not None and terminal_event(client) is None:
            raise ControllerFailure(
                "Controller action rejected: " + rejected["data"]["reason"]
            )
    request = client.send("course_end", reason="controller_wall_deadline")
    client.acknowledge(request, "received")
    client.wait(lambda _state: terminal_event(client) is not None)
    return terminal_event(client)


def qualify_execution(count: int = 10, *, development: bool = False) -> dict:
    if not 1 <= count <= 20:
        raise ValueError("Execution count must be 1 to 20")
    with exclusive_arena():
        return _qualify_execution(count, development=development)


def _qualify_execution(count: int, *, development: bool) -> dict:
    profile = gates.require_initial_profile("C1", ("forward", "look", "mine", "pickup"))
    if not development and gates.statuses()["G2"] != "passed":
        raise ValueError(
            "G2 must pass before scored execution; use development explicitly"
        )
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError(
            "Installed mod differs from source; rebuild and install first"
        )
    if not development:
        gates.require_scored_execution(provenance["source_hash"], "local-visibility-v1")
    generated = courses.verify_generated()
    active_spec = read_json(SERVER / "world/datapacks/mclab/evaluator/active.json")
    if active_spec["course_id"] != "C1" or active_spec["tick_limit"] != 600:
        raise ValueError("Execution requires the frozen 600-tick C1 course")
    if (
        not development
        and generated["spec_hash"]
        != read_json(ROOT / "docs/gates.json")["G2"]["spec_hash"]
    ):
        raise ValueError("G2 course specification changed; requalification required")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/execution" / stamp
    client = RecordedBridge(destination / "events.jsonl")
    client.budget = 12000
    recorder = EpisodeRecorder(ROOT / "results/episodes.sqlite3")
    recovered = recorder.recover_interrupted()
    configuration = {
        "schema_version": 1,
        "controller": "deterministic-c1-v1",
        "timing_profile": "c1-next-four-ticks-v1",
        "intended_apply_offset_ticks": 4,
        "action_duration_ticks": 5,
        "wall_watchdog_seconds": 90,
        "course_spec_hash": generated["spec_hash"],
        "course_tick_limit": 600,
        "track": "perception_goal",
        "sensor_profile": "local-visibility-v1",
        "profile_id": profile["profile_id"],
        "profile_hash": digest(profile),
        "mod_provenance": provenance,
        "requested_episodes": count,
        "scored": not development,
        "recovered_interrupted": recovered,
        "controller_sha256": sha256(Path(__file__)),
        "source_commit": subprocess.run(
            ["rtk", "git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
    }
    recorder.begin_run(client.run_id, configuration)
    client.recorder = recorder
    summary = {
        **configuration,
        "run_id": client.run_id,
        "launch_id": client.launch_id,
        "episodes": [],
        "recording": str(destination / "events.jsonl"),
        "request_budget": client.budget,
    }
    active_episode = None
    try:
        for relative in ("src/mclab", "mod/src", "config/initial_body_profile.json"):
            source, target = ROOT / relative, destination / "sources" / relative
            if source.is_dir():
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        client.synchronize()
        for attempt in range(1, count + 1):
            client.episode_id = "episode-" + uuid4().hex[:12]
            client.body_id = "body-" + uuid4().hex[:12]
            client.policy_memory = PolicyMapRecorder()
            recorder.begin(
                client.run_id,
                attempt,
                client.episode_id,
                client.body_id,
                destination / "events.jsonl",
            )
            active_episode = client.episode_id
            request = client.send("course_reset", profile_id=profile["profile_id"])
            client.acknowledge(request, "course_reset_started", timeout=10)
            reset = client.acknowledge(request, "course_reset_complete", timeout=90)
            if reset["data"]["spec_hash"] != generated["spec_hash"]:
                raise RuntimeError(
                    "Server reset specification differs from verified files"
                )
            request = client.send("course_begin")
            client.acknowledge(request, "course_started")
            terminal = run_controller(client)
            outcome = terminal["data"]
            if outcome["status"] not in {"success", "failed", "invalid"}:
                raise ValueError("Unknown authoritative episode status")
            recorder.finish(
                active_episode, outcome["status"], outcome["reason"], terminal
            )
            summary["episodes"].append(
                {
                    "attempt": attempt,
                    "episode_id": active_episode,
                    "body_id": client.body_id,
                    **outcome,
                }
            )
            active_episode = None
            write_json(destination / "summary.json", {**summary, "status": "running"})
        successes = sum(e["status"] == "success" for e in summary["episodes"])
        summary["successes"] = successes
        summary["attempted_episodes"] = len(summary["episodes"])
        summary["status"] = (
            "passed"
            if count >= 10
            and successes / count >= 0.9
            and all(e["status"] != "invalid" for e in summary["episodes"])
            else "failed"
        )
    except Exception as error:
        summary.update(status="invalid", error=str(error))
        client.record("runner_error", {"error": str(error)})
        if active_episode is not None:
            terminal = terminal_event(client)
            outcome = (
                terminal["data"]
                if terminal is not None
                else {
                    "status": "invalid",
                    "reason": "runner_error",
                    "error": str(error),
                }
            )
            recorder.finish(
                active_episode,
                outcome["status"],
                outcome["reason"],
                terminal or outcome,
            )
            summary["episodes"].append({"episode_id": active_episode, **outcome})
    finally:
        try:
            request = client.send("despawn")
            client.acknowledge(request, "received")
            client.wait(lambda state: state["state"] == "idle")
        except Exception as error:
            summary.update(status="invalid", cleanup_error=str(error))
        summary["attempted_episodes"] = len(summary["episodes"])
        summary["requests"] = client.submitted
        summary["successes"] = sum(
            e["status"] == "success" for e in summary["episodes"]
        )
        recorder.finish_run(client.run_id, summary["status"])
        client.close()
        recorder.close()
        write_json(destination / "summary.json", summary)
    return summary
