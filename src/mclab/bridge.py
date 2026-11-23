"""Authenticated transport to the two server mods; never records its token.

mcb-body (bridge_port) spawns and drives Carpet bodies; mcb-lab (lab_port) resets
courses, runs natural attempts and scores any named player. This client keeps the
single-bridge interface the harness was written against: it routes each op to the
right mod, merges both event streams, and overlays lab state (run state, episode,
fixture) onto body state.

Body-only mode (an instance config with ``lab_mod: false`` or no ``lab_port``, e.g.
the play server): no lab request is ever made, and the session state is the body's
own, with a ready body shown as ``running`` (the long-lived companion session is the
"episode"); ``body_state`` keeps the raw body state. The companion host also sets
``budget = None`` (no request budget), calls ``trim`` from its idle loop and sets
``cancel_check``, which every ``state()`` poll runs first.
"""

import http.client
import io
import json
import threading
import time
import urllib.error
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from mclab.event_log import EventWriter
from mclab.paths import ROOT, config, read_json
from mclab.server import STATE
from mclab.silence_watch import SilenceWatchdog

# Ops served by mcb-body. Everything else goes to mcb-lab.
BODY_OPS = frozenset(
    {
        "spawn",
        "despawn",
        "release",
        "heartbeat",
        "plug_keep",
        "controls",
        "follow_route",
        "follow_step",
        "follow_gap",
        "plan_route",
        "cancel_plan",
        "planner_map",
        "planner_request",
        "planner_set",
        "route_policy",
        "cancel_route",
        "sensor_check",
        "awareness_check",
        "command_proof",
        "trace",
        "dump_knowledge",
        "craft",
        "furnace",
        "equip",
        "place_block",
        "use_block",
        "break_block",
        "attack_entity",
        "use_entity",
        "eat",
        "container",
    }
)
# Lab ops that act on the current body by name.
BODY_SUBJECT_OPS = frozenset({"fixture", "death_proof", "knockback_proof", "kit"})
REJECTIONS = frozenset(
    {"rejected", "rejected_at_execution", "course_reset_failed", "course_begin_failed"}
)


def new_body_name() -> str:
    return "Lab" + uuid4().hex[:10]


def block_center(cell: list) -> list:
    return [cell[0] + 0.5, cell[1], cell[2] + 0.5]


class Bridge:
    def __init__(self, output: Path):
        from mclab.run_launch import attach_run

        attach_run(output.parent)
        if not STATE.is_file():
            raise RuntimeError("The lab server is not running; use Start lab first")
        state = read_json(STATE)
        settings = config()
        self.launch_id = state["launch_id"]
        self._token = state["token"]
        self.root = f"http://127.0.0.1:{settings['bridge_port']}"
        self._connection = http.client.HTTPConnection(
            "127.0.0.1", settings["bridge_port"], timeout=4
        )
        # Body-only: an instance without the mcb-lab mod (the play server).
        self.lab = bool(settings.get("lab_mod", True)) and "lab_port" in settings
        self.lab_root = None
        self._lab_connection = None
        if self.lab:
            self.lab_root = f"http://127.0.0.1:{settings['lab_port']}"
            self._lab_connection = http.client.HTTPConnection(
                "127.0.0.1", settings["lab_port"], timeout=4
            )
        self.run_id = "qualification-" + uuid4().hex[:12]
        self.episode_id = "proof-" + uuid4().hex[:12]
        self.body_id = "body-" + uuid4().hex[:12]
        self.body_name: str | None = None
        self.lab_watchdog = False
        self.sequence = 0
        self.submitted = 0
        self.cursor = 0
        self.lab_cursor = 0
        self.record_sequence = 0
        self.events: list[dict] = []
        self.samples: list[dict] = []
        self.last_tick = -1
        self.last_heartbeat = 0.0
        # None: no budget (the long-lived companion host)
        self.budget: int | None = read_json(ROOT / "config/qualification.json")[
            "request_budget"
        ]
        # Run first by every state() poll, so any blocking wait can be cancelled
        # (the companion host raises its JobCancelled from it).
        self.cancel_check: Callable[[], None] | None = None
        output.parent.mkdir(parents=True, exist_ok=True)
        # The log is a SQLite store; callers still pass <run>/events.jsonl.
        self.stream = EventWriter(output)
        self._record_lock = threading.Lock()  # the silence watchdog writes too
        self.silence = SilenceWatchdog(self.record)

    def record(self, kind: str, payload: dict):
        with self._record_lock:
            self._write_record(kind, payload)

    def _write_record(self, kind: str, payload: dict):
        self.record_sequence += 1
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
        self.stream.add(value)

    def synchronize(self):
        # Old, already-evicted events from another run do not invalidate a new attempt.
        # Establish both cursors before sending this run's first command.
        # Startup polls are separate from execution. Commands are never retried.
        deadline = time.monotonic() + 30
        while True:
            try:
                # HTTP/event endpoints exist before the first server tick publishes
                # both snapshots. Do not admit commands until both mods are ready.
                self.http("/state")
                body = self.http("/events?after=0")
                lab = self.http("/lab/events?after=0") if self.lab else None
                break
            except urllib.error.HTTPError:
                raise
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        self.cursor = body["latest_sequence"]
        self.lab_cursor = lab["latest_sequence"] if lab is not None else 0
        self.record(
            "stream_open",
            {
                "launch_id": self.launch_id,
                "after_sequence": self.cursor,
                "lab_after_sequence": self.lab_cursor,
            },
        )

    def _raw(self, lab: bool, path: str, payload: dict | None) -> dict:
        connection = self._lab_connection if lab else self._connection
        root = self.lab_root if lab else self.root
        body = (
            None if payload is None else json.dumps(payload, allow_nan=False).encode()
        )
        is_idempotent = payload is None or (
            isinstance(payload, dict) and payload.get("op") == "heartbeat"
        )
        for attempt in range(2):
            sent = False
            try:
                # Fully consume each response so the next poll can reuse its socket.
                # A failed request is never retried: its admission may be ambiguous,
                # except for idempotent ops or stale sockets that dropped before send.
                connection.request(
                    "GET" if payload is None else "POST",
                    path,
                    body=body,
                    headers={
                        "Authorization": "Bearer " + self._token,
                        "Content-Type": "application/json",
                    },
                )
                sent = True
                response = connection.getresponse()
                with response:
                    data = response.read()
                break
            except (OSError, http.client.HTTPException) as error:
                connection.close()
                if attempt == 0 and (
                    is_idempotent
                    or (
                        not sent
                        and isinstance(
                            error, (ConnectionResetError, ConnectionAbortedError)
                        )
                    )
                ):
                    continue
                raise urllib.error.URLError(error) from error
        if not 200 <= response.status < 300:
            raise urllib.error.HTTPError(
                root + path,
                response.status,
                response.reason,
                response.headers,
                io.BytesIO(data),
            )
        value = json.loads(data)
        if value in ({}, {"state": "idle"}) and path in {"/state", "/policy"}:
            raise urllib.error.URLError("Bridge snapshot not ready")
        if path == "/policy":
            self.last_policy_wire_bytes = len(data)
        if value.get("launch_id") != self.launch_id:
            raise RuntimeError(
                "Bridge belongs to a different launch "
                f"({'lab' if lab else 'body'} {path}; "
                f"expected {self.launch_id}, received {value.get('launch_id')}; "
                f"fields {sorted(value)})"
            )
        return value

    def http(self, path: str, payload: dict | None = None) -> dict:
        """'/lab/...' goes to mcb-lab; /state and /policy merge both; else mcb-body."""
        if path.startswith("/lab/"):
            if not self.lab:
                raise RuntimeError(
                    f"{path} needs the mcb-lab mod; this server has none"
                )
            return self._raw(True, path[4:], payload)
        if path in {"/state", "/policy"} and payload is None:
            body = self._raw(False, path, None)
            if not self.lab:
                return session_view(body)
            return merge(
                body, self._raw(True, "/state", None), policy=path == "/policy"
            )
        return self._raw(False, path, payload)

    def envelope(self, op: str, **fields) -> dict:
        self.sequence += 1
        if self.budget is not None and self.sequence > self.budget:
            raise RuntimeError("Qualification request budget exhausted")
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "body_id": self.body_id,
            "sequence": self.sequence,
            "op": op,
            **fields,
        }

    def send(self, op: str, *, motor: bool = False, **fields) -> dict:
        """Sends one logical op. Some ops fan out to both mods; the returned payload is
        the one whose acknowledgement callers wait for."""
        if op == "spawn":
            return self._spawn(**fields)
        if op == "course_reset" and "player" not in fields:
            # A qualified Carpet course: the lab waits for the named body spawned now.
            # The lab fails the episode if this controller stops heartbeating.
            name = new_body_name()
            request = self._send("course_reset", player=name, watchdog=True, **fields)
            self.lab_watchdog = True
            self._spawn(name=name, replace=True, stream=True, limits="course")
            return request
        if op == "course_reset":
            self.lab_watchdog = False
        if op == "heartbeat":
            request = self._send("heartbeat")
            if self.lab_watchdog:
                self._send("heartbeat", lab=True)
            return request
        if op == "natural_start" and fields.get("subject") == "carpet_body":
            return self._natural_carpet(**fields)
        if op == "natural_start":
            if fields.pop("awareness", None) not in (None, _legacy_awareness()):
                raise ValueError("awareness_requires_carpet_body")
            fields.pop("terrain_encoding", None)
            return self._send(op, **fields)
        if op == "natural_stop":
            request = self._send("stop", reason="stopped")
            if self.body_name:
                self._send("despawn")
                self.body_name = None
            return request
        if op == "despawn":
            request = self._send("despawn")
            if self.lab:
                self._send("stop", reason="despawn_cancelled")
            self.body_name = None
            self.lab_watchdog = False
            return request
        if op == "course_test" and fields.get("kind") == "sensor_check":
            return self._send("sensor_check")
        if op == "kit" and fields.get("player"):
            return self._send(op, **fields)  # a named person, not this client's body
        if op in BODY_SUBJECT_OPS:
            if op == "fixture":
                self._send("release")
            return self._send(op, player=self._require_body(), **fields)
        return self._send(op, motor=motor, **fields)

    def _send(
        self, op: str, *, motor: bool = False, lab: bool | None = None, **fields
    ) -> dict:
        payload = self.envelope(op, **fields)
        self.send_raw(payload, motor=motor, lab=lab)
        return payload

    def _require_body(self) -> str:
        if not self.body_name:
            raise RuntimeError("No body has been spawned by this client")
        return self.body_name

    def _spawn(self, *, position=None, name=None, **fields) -> dict:
        """Spawn a Carpet body; by default at the lab's mechanics fixture."""
        if position is None:
            position = self.http("/lab/state")["fixture"]
        self.body_name = name or new_body_name()
        request = self._send("spawn", name=self.body_name, position=position, **fields)
        self.planner_map_epoch = getattr(self, "planner_map_epoch", 0) + 1
        return request

    def _natural_carpet(self, **fields) -> dict:
        awareness = fields.pop("awareness", None)
        encoding = fields.pop("terrain_encoding", "cells")
        fields.pop("player", None)
        name = new_body_name()
        request = self._send("natural_start", player=name, **fields)
        sensing = {"terrain_encoding": encoding}
        if awareness is not None:
            sensing["awareness"] = awareness
        self._spawn(
            name=name,
            position=block_center(fields["start"]),
            yaw=fields["yaw"],
            replace=True,
            stream=True,
            extended_observation=True,
            **sensing,
        )
        return request

    def send_raw(self, payload: dict, *, motor: bool = False, lab: bool | None = None):
        """Submits one envelope; the op picks the target mod unless `lab` is given."""
        if self.budget is not None and self.submitted >= self.budget:
            raise RuntimeError("Qualification request budget exhausted")
        self.submitted += 1
        self.silence.touch()
        self.record("request", payload)
        if lab is None:
            lab = not motor and payload["op"] not in BODY_OPS
        if motor:
            path = "/controls"
        else:
            path = "/lab/admin" if lab else "/admin"
        receipt = self.http(path, payload)
        self.record("receipt", receipt)

    def drain(self) -> list[dict]:
        body = self.http(f"/events?after={self.cursor}")
        batches = [body]
        if self.lab:
            batches.append(self.http(f"/lab/events?after={self.lab_cursor}"))
        for batch in batches:
            if batch["gap"]:
                self.record("event_gap", batch)
                raise RuntimeError("Critical qualification event gap; attempt invalid")
        # Stable sort keeps each mod's own order within a tick.
        merged = sorted(
            (e for batch in batches for e in batch["events"]), key=lambda e: e["tick"]
        )
        for event in merged:
            self.record("server_event", event)
            self.events.append(event)
        self.cursor = body["latest_sequence"]
        if self.lab:
            self.lab_cursor = batches[1]["latest_sequence"]
        return merged

    def state(self, *, heartbeat=True) -> dict:
        if self.cancel_check is not None:
            self.cancel_check()
        if heartbeat and time.monotonic() - self.last_heartbeat >= 0.5:
            self.send("heartbeat")
            self.last_heartbeat = time.monotonic()
        self.drain()
        value = self.http("/state")
        if value["tick"] != self.last_tick:
            self.samples.append(value)
            self.record("observation", value)
            self.last_tick = value["tick"]
        return value

    def wait(self, predicate, timeout=10, *, heartbeat=True) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.state(heartbeat=heartbeat)
            if predicate(value):
                return value
            time.sleep(0.015)
        raise TimeoutError("Qualification wait exceeded its wall-clock deadline")

    def ticks(self, duration: int, *, heartbeat=True) -> dict:
        current = self.state(heartbeat=heartbeat)["tick"]
        return self.wait(
            lambda value: value["tick"] >= current + duration,
            timeout=duration / 10 + 5,
            heartbeat=heartbeat,
        )

    def trim(self, keep: int = 2000) -> None:
        """Bound the in-memory event and observation lists of a long-lived session
        (the recording keeps everything). Call it between waits, never inside one:
        an acknowledgement looks its event up in ``events``."""
        for kept in (self.events, self.samples):
            if len(kept) > keep:
                del kept[:-keep]

    # 20 s: a 5.5 s server pause outlived the old 5 s and ended a healthy build
    # at 423 blocks (run 20261110T035112317338Z, cancel_plan ack)
    def acknowledge(self, payload: dict, kind: str, timeout=20):
        sequence = payload["sequence"]

        def matches(_state):
            for event in self.events:
                if (
                    event["type"] in REJECTIONS
                    and kind not in REJECTIONS
                    and int(event["data"].get("request_sequence", -1)) == sequence
                ):
                    raise RuntimeError(
                        f"Bridge rejected request {sequence}: {event['data']['reason']}"
                    )
            return any(
                e["type"] == kind
                and int(e["data"].get("request_sequence", -1)) == sequence
                for e in self.events
            )

        self.wait(matches, timeout=timeout)
        return next(
            e
            for e in reversed(self.events)
            if e["type"] == kind
            and int(e["data"].get("request_sequence", -1)) == sequence
        )

    def close(self):
        coordinator = getattr(self, "planner_map_coordinator", None)
        self.planner_map_coordinator = None
        try:
            if coordinator is not None:
                coordinator.close()
        finally:
            self.silence.close()
            self._connection.close()
            if self._lab_connection is not None:
                self._lab_connection.close()
            self.stream.close()


def _legacy_awareness() -> dict:
    from mclab.awareness import Awareness

    return Awareness().payload()


def session_view(body: dict) -> dict:
    """Body-only state: a ready body is a running session; any other body state
    (preparing, settling, dead, disconnected_or_dead, ...) shows as it is."""
    value = dict(body)
    value["body_state"] = body.get("state", "idle")
    if value["body_state"] == "ready":
        value["state"] = "running"
    return value


def merge(body: dict, lab: dict, *, policy: bool) -> dict:
    """Body state with the lab's run state, episode and fixture view laid over it.

    The lab's state wins whenever it is doing something (resetting, running, recording);
    when the lab is idle the body's own state (preparing, ready, dead) shows through.
    """
    value = dict(body)
    lab_state = lab.get("state", "idle")
    value["state"] = lab_state if lab_state != "idle" else body.get("state", "idle")
    value["episode"] = lab.get("episode", {})
    observed = dict(value.get("body") or {})
    if policy:
        if "building_pickups" in lab:
            observed["building_pickups"] = lab["building_pickups"]
    else:
        value["course_reset"] = lab.get("course_reset", {})
        if observed.get("registered"):
            observed.update(lab.get("fixture_state", {}))
            observed["reference_setup"] = lab.get("reference_setup", {})
    value["body"] = observed
    return value
