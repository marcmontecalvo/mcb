"""Authenticated qualification transport; never exposes its token in recordings."""

import http.client
import io
import json
import time
import urllib.error
from pathlib import Path
from uuid import uuid4

from mclab.paths import ROOT, config, read_json
from mclab.server import STATE


class Bridge:
    def __init__(self, output: Path):
        state = read_json(STATE)
        self.launch_id = state["launch_id"]
        self._token = state["token"]
        self.root = f"http://127.0.0.1:{config()['bridge_port']}"
        self._connection = http.client.HTTPConnection(
            "127.0.0.1", config()["bridge_port"], timeout=4
        )
        self.run_id = "qualification-" + uuid4().hex[:12]
        self.episode_id = "proof-" + uuid4().hex[:12]
        self.body_id = "body-" + uuid4().hex[:12]
        self.sequence = 0
        self.submitted = 0
        self.cursor = 0
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
        # Establish the cursor before sending this run's first command.
        # Startup polls are separate from execution. Commands are never retried.
        deadline = time.monotonic() + 30
        while True:
            try:
                batch = self.http("/events?after=0")
                break
            except urllib.error.HTTPError:
                raise
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        self.cursor = batch["latest_sequence"]
        self.record(
            "stream_open", {"launch_id": self.launch_id, "after_sequence": self.cursor}
        )

    def http(self, path: str, payload: dict | None = None) -> dict:
        body = (
            None if payload is None else json.dumps(payload, allow_nan=False).encode()
        )
        try:
            # Fully consume each response so the next poll can reuse its socket.
            # A failed request is never retried: its admission may be ambiguous.
            self._connection.request(
                "GET" if payload is None else "POST",
                path,
                body=body,
                headers={
                    "Authorization": "Bearer " + self._token,
                    "Content-Type": "application/json",
                },
            )
            response = self._connection.getresponse()
            with response:
                data = response.read()
        except (OSError, http.client.HTTPException) as error:
            self._connection.close()
            raise urllib.error.URLError(error) from error
        if not 200 <= response.status < 300:
            raise urllib.error.HTTPError(
                self.root + path,
                response.status,
                response.reason,
                response.headers,
                io.BytesIO(data),
            )
        value = json.loads(data)
        if path == "/policy":
            self.last_policy_wire_bytes = len(data)
        if value.get("launch_id") != self.launch_id:
            raise RuntimeError("Bridge belongs to a different launch")
        return value

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
        payload = self.envelope(op, **fields)
        self.send_raw(payload, motor=motor)
        return payload

    def send_raw(self, payload: dict, *, motor: bool = False):
        if self.submitted >= self.budget:
            raise RuntimeError("Qualification request budget exhausted")
        self.submitted += 1
        self.record("request", payload)
        receipt = self.http("/controls" if motor else "/admin", payload)
        self.record("receipt", receipt)

    def drain(self) -> list[dict]:
        batch = self.http(f"/events?after={self.cursor}")
        if batch["gap"]:
            self.record("event_gap", batch)
            raise RuntimeError("Critical qualification event gap; attempt invalid")
        for event in batch["events"]:
            self.record("server_event", event)
            self.events.append(event)
        self.cursor = batch["latest_sequence"]
        return batch["events"]

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
                    event["type"]
                    in {
                        "rejected",
                        "rejected_at_execution",
                        "course_reset_failed",
                        "course_begin_failed",
                    }
                    and kind
                    not in {
                        "rejected",
                        "rejected_at_execution",
                        "course_reset_failed",
                        "course_begin_failed",
                    }
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
        self.stream.close()
