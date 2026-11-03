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
