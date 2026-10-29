"""Deterministic C1 perception-goal controller and fully recorded live attempts."""

import json
import math
import queue
import shutil
import subprocess
import threading
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab import courses, gates
from mclab.bootstrap import sha256
from mclab.bridge import Bridge
from mclab.modbuild import source_hash
from mclab.paths import MOD_SOURCES, ROOT, SERVER, digest, read_json, write_json
from mclab.protocolproof import controls
from mclab.recorder import EpisodeRecorder, exclusive_arena


class RecordedBridge(Bridge):
    recorder: EpisodeRecorder | None = None
    policy_memory = None
    last_resource_tick = -20

    def __init__(self, output):
        super().__init__(output)
        # One native poll can return the entire 2048-event ring. Leave another
        # 256 slots for in-flight writes and local request/observation records.
        self.record_queue = queue.Queue(maxsize=2304)
        self.writer_error = None
        self.writer_stage = "starting"
        self.writer_progress = time.monotonic()
        self.writer = threading.Thread(target=self._write_records, daemon=True)
        self.writer.start()

    def _write_records(self):
        database = None
        try:
            while True:
                value = self.record_queue.get()
                batch = [value]
                try:
                    if value is None:
                        return
                    if self.recorder is not None:
                        if database is None:
                            self.writer_stage = "opening_database"
                            database = EpisodeRecorder(self.recorder.path)
                    # Drain only already queued work; never delay controls to
                    # fill a batch. Shutdown is submitted after a flush.
                    while len(batch) < 128:
                        try:
                            batch.append(self.record_queue.get_nowait())
                        except queue.Empty:
                            break
                    self.writer_stage = "writing_batch"
                    with database.batch() if database is not None else nullcontext():
                        for record in batch:
                            if record is None:
                                raise RuntimeError("Recorder stopped before flush")
                            payload_json = json.dumps(
                                record["payload"],
                                allow_nan=False,
                                separators=(",", ":"),
                            )
                            header = json.dumps(
                                {k: v for k, v in record.items() if k != "payload"},
                                allow_nan=False,
                                separators=(",", ":"),
                            )
                            self.stream.write(
                                header[:-1] + ',"payload":' + payload_json + "}\n"
                            )
                            if database is not None:
                                database._insert_event(
                                    record["run_id"],
                                    record["sequence"],
                                    record["episode_id"],
                                    record["type"],
                                    record["monotonic_ns"],
                                    payload_json,
                                )
                        self.stream.flush()
                        self.writer_stage = "committing_batch"
                    self.writer_progress = time.monotonic()
                    self.writer_stage = "waiting"
                except Exception as error:
                    # Publish failure before task_done makes the queue empty.
                    self.writer_error = error
                    raise
                finally:
                    for _ in batch:
                        self.record_queue.task_done()
        except Exception as error:
            self.writer_error = error
        finally:
            if database is not None:
                database.close()
            try:
                self.stream.close()
            except Exception as error:
                self.writer_error = error

    def record(self, kind, payload):
        if self.writer_error is not None:
            raise RuntimeError("Episode recording failed") from self.writer_error
        self.record_sequence += 1
        # Transport payloads are immutable after enqueue. Assign identities and
        # capture time here; the writer owns its own SQLite connection.
        value = {
            "schema_version": 1,
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "body_id": self.body_id,
            "sequence": self.record_sequence,
            "monotonic_ns": time.monotonic_ns(),
            "type": kind,
            "payload": payload,
        }
        try:
            self.record_queue.put_nowait(value)
        except queue.Full:
            self.writer_error = RuntimeError(
                "Critical recorder queue overflow; "
                f"kind={kind}, writer_stage={self.writer_stage}, "
                f"seconds_since_commit={time.monotonic() - self.writer_progress:.3f}"
            )
            raise RuntimeError(f"{self.writer_error}; attempt invalid") from None

    def flush_recording(self):
        deadline = time.monotonic() + 10
        while self.record_queue.unfinished_tasks:
            if self.writer_error is not None:
                raise RuntimeError("Episode recording failed") from self.writer_error
            if time.monotonic() >= deadline:
                raise RuntimeError("Episode recording flush deadline exceeded")
            time.sleep(0.005)
        if self.writer_error is not None:
            raise RuntimeError("Episode recording failed") from self.writer_error

    def close(self):
        failure = None
        try:
            self.flush_recording()
        except Exception as error:
            failure = error
        if self.writer.is_alive():
            try:
                self.record_queue.put(None, timeout=1)
            except queue.Full:
                failure = RuntimeError("Recorder shutdown queue remained full")
            self.writer.join(timeout=10)
        if self.writer.is_alive():
            raise RuntimeError("Recorder writer did not stop; recording invalid")
        super().close()
        if failure or self.writer_error:
            raise RuntimeError("Episode recording failed during shutdown") from (
                failure or self.writer_error
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
        self.awareness_key = None

    def update(self, observation: dict) -> tuple[str, dict]:
        awareness = observation.get("awareness", {})
        key = (
            observation.get("sensor_profile"),
            awareness.get("revision"),
            json.dumps(awareness.get("config", {}), sort_keys=True),
        )
        if key != self.awareness_key:
            self.cells.clear()
            self.observations = 0
            self.awareness_key = key
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
            "sensor_profile": observation.get("sensor_profile"),
            "awareness": awareness,
            "cells": list(self.cells.values()) if full else changes,
        }


def policy_state(client: Bridge, *, record=True) -> dict:
    client.state()  # Heartbeat and evaluator event recording never reach decide().
    started = time.perf_counter_ns()
    observation = client.http("/policy")
    latency_ms = (time.perf_counter_ns() - started) / 1_000_000
    for key in ("run_id", "episode_id", "body_id"):
        if observation.get(key) != getattr(client, key):
            raise RuntimeError("Policy observation belongs to another attempt")
    from mclab.terrain_transport import expand_terrain

    client.pending_policy_wire = observation
    decode_started = time.perf_counter_ns()
    observation = expand_terrain(observation)
    client.terrain_decode_ms = (time.perf_counter_ns() - decode_started) / 1_000_000
    client.pending_policy = (observation, latency_ms, time.monotonic_ns())
    if record:
        record_pending_policy(client)
    return observation


def record_pending_policy(client: Bridge):
    pending = getattr(client, "pending_policy", None)
    if pending is None:
        return
    observation, latency_ms, captured_ns = pending
    client.pending_policy = None
    wire = getattr(client, "pending_policy_wire", None)
    client.pending_policy_wire = None
    client.record(
        "policy_observation",
        {
            **(wire if wire is not None else observation),
            "captured_monotonic_ns": captured_ns,
            "terrain_decode_ms": getattr(client, "terrain_decode_ms", 0.0),
            "terrain_decode_clock": "perf_counter_ns",
        },
    )
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
                "policy_wire_bytes": getattr(client, "last_policy_wire_bytes", None),
                "terrain_decode_ms": getattr(client, "terrain_decode_ms", None),
                "timing_clock": "perf_counter_ns",
                "server": observation.get("resources", {"status": "unavailable"}),
            },
        )
        client.last_resource_tick = observation["tick"]


def run_controller(client: Bridge, wall_timeout: float = 90) -> dict:
    try:
        return _drive_controller(client, wall_timeout)
    except (ControllerFailure, TimeoutError) as error:
        client.record("controller_error", {"error": str(error)})
        if terminal_event(client) is not None:
            return terminal_event(client)
        request = client.send("course_end", reason="controller_execution_error")
        client.acknowledge(request, "received")
        client.wait(lambda _state: terminal_event(client) is not None)
        return terminal_event(client)


def _drive_controller(client: Bridge, wall_timeout: float) -> dict:
    controller = C1Controller()  # No memory survives an episode boundary.
    deadline = time.monotonic() + wall_timeout
    action_index = 0
    discarded_stale = 0
    while time.monotonic() < deadline:
        observation = policy_state(client)
        terminal = terminal_event(client)
        if terminal is not None:
            record_pending_policy(client)
            return terminal
        if observation["state"] != "running":
            if observation["state"] == "episode_terminal":
                record_pending_policy(client)
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
                intended_apply_tick=tick + 3,
                expires_tick=tick + 8,
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
            reason = rejected["data"]["reason"]
            if reason in {"late_or_distant_action", "stale_or_mistimed_action"}:
                discarded_stale += 1
                client.record(
                    "discarded_stale_action",
                    {
                        "request_sequence": request["sequence"],
                        "reason": reason,
                        "count": discarded_stale,
                        "limit": 3,
                    },
                )
                if discarded_stale <= 3:
                    # Discard this command permanently. Release, then the next
                    # loop senses and decides afresh under the same tick budget.
                    release = client.send("release")
                    client.acknowledge(release, "received")
                    continue
            raise ControllerFailure("Controller action rejected: " + reason)
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
        "timing_profile": "c1-three-tick-bounded-resense-v4",
        "maximum_discarded_stale_actions": 3,
        "intended_apply_offset_ticks": 3,
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
        for relative in ("src/mclab", *MOD_SOURCES, "config/initial_body_profile.json"):
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
            # The event is emitted before END(t) publishes /policy and /state.
            # Observe publication of the running snapshot before the first input.
            client.wait(
                lambda state: state["state"] == "running"
                or terminal_event(client) is not None
            )
            terminal = run_controller(client)
            outcome = terminal["data"]
            client.flush_recording()
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
        try:
            client.record("runner_error", {"error": str(error)})
        except Exception as recording_error:
            summary["recording_error"] = str(recording_error)
        if active_episode is not None:
            terminal = terminal_event(client)
            outcome = {
                "status": "invalid",
                "reason": "runner_error",
                "error": str(error),
                "observed_native_terminal": terminal,
            }
            recorder.finish(
                active_episode,
                outcome["status"],
                outcome["reason"],
                outcome,
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
        try:
            client.close()  # Flush all critical records before finalizing run status.
        except Exception as error:
            summary.update(status="invalid", recording_error=str(error))
        finally:
            recorder.finish_run(client.run_id, summary["status"])
            recorder.close()
            write_json(destination / "summary.json", summary)
    return summary
