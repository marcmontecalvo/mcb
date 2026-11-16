import base64
import hashlib
import json
import zlib
from types import SimpleNamespace

import pytest

from mclab.planner_session import attach, detach, submit_plan
from mclab.terrain_steps import TerrainPolicy


class Client:
    def __init__(self, fail_part=False):
        self.calls = []
        self.fail_part = fail_part
        self.run_id = "run"
        self.episode_id = "episode"
        self.body_id = "body"
        self.body_state = "ready"

    def state(self, heartbeat=True):
        return {"state": self.body_state}

    def send(self, operation, **fields):
        self.calls.append((operation, fields))
        if self.fail_part and fields.get("kind") == "part":
            raise TimeoutError("part")
        return {"sequence": len(self.calls)}

    def acknowledge(self, request, kind):
        assert request["sequence"] > 0 and kind in {
            "plan_accepted",
            "planner_request_accepted",
            "route_policy_accepted",
            "plan_cancelled",
        }


def test_large_request_is_framed_without_losing_protected_cells():
    client = Client()
    payload = {"plan_id": "p1", "protected": [[i, i % 7, -i] for i in range(4000)]}
    submit_plan(client, payload)
    assert client.calls[0][1]["kind"] == "begin"
    parts = [f for _, f in client.calls if f.get("kind") == "part"]
    assert all(len(p["data"]) <= 10_000 for p in parts)
    encoded = "".join(p["data"] for p in parts)
    raw = zlib.decompress(base64.b64decode(encoded))
    assert json.loads(raw) == payload
    assert client.calls[0][1]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert client.calls[-1] == ("plan_route", {"plan_id": "p1", "plan_ref": "p1"})


def test_failed_frame_aborts_and_propagates_error():
    client = Client(fail_part=True)
    with pytest.raises(TimeoutError, match="part"):
        submit_plan(
            client, {"plan_id": "p2", "protected": [[i, 0, 0] for i in range(4000)]}
        )
    assert client.calls[-2][1]["kind"] == "abort"
    assert client.calls[-1] == ("cancel_plan", {"plan_id": "p2"})


def test_route_policy_is_scoped_to_each_planner_session():
    client = Client()
    controller = SimpleNamespace(
        terrain_policy=TerrainPolicy(use_doors=True, climb_vines=False)
    )
    attach(client, controller)
    assert client.calls[-1] == (
        "route_policy",
        {"use_doors": True, "climb_vines": False},
    )
    detach(controller)
    assert client.calls[-1] == (
        "route_policy",
        {"use_doors": False, "climb_vines": True},
    )
    client.planner_map_coordinator.close()


def test_detach_skips_route_policy_reset_for_a_dead_body():
    client = Client()
    controller = SimpleNamespace(terrain_policy=None)
    attach(client, controller)
    client.body_state = "disconnected_or_dead"
    client.calls.clear()
    detach(controller)  # the mod would reject route_policy for a non-ready body
    assert client.calls == []
    client.planner_map_coordinator.close()


def test_send_and_ack_retries_on_stale_queued_request():
    class StaleOnceClient:
        def __init__(self):
            self.calls = []
            self.stale_sent = False

        def send(self, operation, **fields):
            self.calls.append((operation, fields))
            return {"sequence": len(self.calls)}

        def acknowledge(self, request, kind):
            if not self.stale_sent:
                self.stale_sent = True
                raise RuntimeError("Bridge rejected request 1: stale_queued_request")
            return {"type": kind, "data": {"generation": 42}}

    client = StaleOnceClient()
    from mclab.planner_session import _send_and_ack

    res = _send_and_ack(client, "planner_map", "planner_map_accepted", kind="batch")
    assert res == {"type": "planner_map_accepted", "data": {"generation": 42}}
    assert len(client.calls) == 2


def test_planner_map_batches_are_pipelined_before_waiting_for_acks():
    # One round trip per 14 KB batch made a 300-section upload ~1.7 s of standing.
    log = []

    class PipeClient(Client):
        def send(self, operation, **fields):
            request = super().send(operation, **fields)
            if operation == "planner_map":
                log.append(("send", fields["kind"]))
            return request

        def acknowledge(self, request, kind):
            if kind == "planner_map_accepted":
                log.append(("ack", self.calls[request["sequence"] - 1][1]["kind"]))
                return {"data": {"generation": 0}}
            return super().acknowledge(request, kind)

    client = PipeClient()
    attach(client, SimpleNamespace(terrain_policy=None))
    frames = [{"kind": "begin", "base": 0, "generation": 1}] + [
        {"kind": "batch", "frames": []}
    ] * 2
    assert client.planner_map_coordinator.transfer.send_frames(frames) == [0, 0, 0]
    assert log == [("send", "begin"), ("send", "batch"), ("send", "batch")] + [
        ("ack", "begin"),
        ("ack", "batch"),
        ("ack", "batch"),
    ]
    client.planner_map_coordinator.close()


def test_queue_full_429_is_retried_because_the_request_was_not_admitted(monkeypatch):
    import urllib.error

    from mclab.planner_session import _send_and_ack

    monkeypatch.setattr("mclab.planner_session.time.sleep", lambda _s: None)

    class FullOnceClient:
        def __init__(self):
            self.attempts = 0

        def send(self, operation, **fields):
            self.attempts += 1
            if self.attempts == 1:
                raise urllib.error.HTTPError("u", 429, "queue_full", {}, None)
            return {"sequence": self.attempts}

        def acknowledge(self, request, kind):
            return {"type": kind}

    client = FullOnceClient()
    assert _send_and_ack(client, "planner_map", "x") == {"type": "x"}
    assert client.attempts == 2
