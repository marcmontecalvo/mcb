"""Authenticated transport to the two server mods; never records its token.

mcb-body (bridge_port) spawns and drives Carpet bodies; mcb-lab (lab_port) resets
courses, runs natural attempts and scores any named player. This client keeps the
single-bridge interface the harness was written against: it routes each op to the
right mod, merges both event streams, and overlays lab state (run state, episode,
fixture) onto body state.
"""

import http.client
import io
import json
import time
import urllib.error
from pathlib import Path
from uuid import uuid4

from mclab.paths import ROOT, config, read_json
from mclab.server import STATE

# Ops served by mcb-body. Everything else goes to mcb-lab.
BODY_OPS = frozenset(
    {
        "spawn",
        "despawn",
        "release",
        "heartbeat",
        "controls",
        "follow_route",
        "follow_step",
        "follow_gap",
        "cancel_route",
        "sensor_check",
        "awareness_check",
        "command_proof",
        "trace",
    }
)
# Lab ops that act on the current body by name.
BODY_SUBJECT_OPS = frozenset({"fixture", "death_proof", "knockback_proof"})
REJECTIONS = frozenset(
    {"rejected", "rejected_at_execution", "course_reset_failed", "course_begin_failed"}
)


def new_body_name() -> str:
    return "Lab" + uuid4().hex[:10]


def block_center(cell: list) -> list:
    return [cell[0] + 0.5, cell[1], cell[2] + 0.5]


class Bridge:
    def __init__(self, output: Path):
        state = read_json(STATE)
        settings = config()
        self.launch_id = state["launch_id"]
        self._token = state["token"]
        self.root = f"http://127.0.0.1:{settings['bridge_port']}"
        self.lab_root = f"http://127.0.0.1:{settings['lab_port']}"
        self._connection = http.client.HTTPConnection(
            "127.0.0.1", settings["bridge_port"], timeout=4
        )
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
        self.budget = read_json(ROOT / "config/qualification.json")["request_budget"]
        output.parent.mkdir(parents=True, exist_ok=True)
        self.stream = output.open("x", encoding="utf-8")

    def record(self, kind: str, payload: dict):
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
        self.stream.write(json.dumps(value, allow_nan=False) + "\n")
        self.stream.flush()

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
                lab = self.http("/lab/events?after=0")
                break
            except urllib.error.HTTPError:
                raise
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        self.cursor = body["latest_sequence"]
        self.lab_cursor = lab["latest_sequence"]
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
        try:
            # Fully consume each response so the next poll can reuse its socket.
            # A failed request is never retried: its admission may be ambiguous.
            connection.request(
                "GET" if payload is None else "POST",
                path,
                body=body,
                headers={
                    "Authorization": "Bearer " + self._token,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            with response:
                data = response.read()
        except (OSError, http.client.HTTPException) as error:
            connection.close()
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
            return self._raw(True, path[4:], payload)
        if path in {"/state", "/policy"} and payload is None:
            body = self._raw(False, path, None)
            return merge(
                body, self._raw(True, "/state", None), policy=path == "/policy"
            )
        return self._raw(False, path, payload)

    def envelope(self, op: str, **fields) -> dict:
        self.sequence += 1
        if self.sequence > self.budget:
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
            self._send("stop", reason="despawn_cancelled")
            self.body_name = None
            self.lab_watchdog = False
            return request
        if op == "course_test" and fields.get("kind") == "sensor_check":
            return self._send("sensor_check")
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
        return self._send("spawn", name=self.body_name, position=position, **fields)

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
        if self.submitted >= self.budget:
            raise RuntimeError("Qualification request budget exhausted")
        self.submitted += 1
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
        lab = self.http(f"/lab/events?after={self.lab_cursor}")
        for batch in (body, lab):
            if batch["gap"]:
                self.record("event_gap", batch)
                raise RuntimeError("Critical qualification event gap; attempt invalid")
        # Stable sort keeps each mod's own order within a tick.
        merged = sorted(body["events"] + lab["events"], key=lambda e: e["tick"])
        for event in merged:
            self.record("server_event", event)
            self.events.append(event)
        self.cursor = body["latest_sequence"]
        self.lab_cursor = lab["latest_sequence"]
        return merged

    def state(self, *, heartbeat=True) -> dict:
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

    def acknowledge(self, payload: dict, kind: str, timeout=5):
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
        self._connection.close()
        self._lab_connection.close()
        self.stream.close()


def _legacy_awareness() -> dict:
    from mclab.awareness import Awareness

    return Awareness().payload()


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
