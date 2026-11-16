import json
from collections import deque

import pytest

from mclab import companion_service as svc
from mclab.companion_status import explain

OWNER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def calls(monkeypatch, tmp_path):
    config = tmp_path / "companion.json"
    config.write_text(json.dumps({"owners": [OWNER], "allowlist": []}))
    monkeypatch.setattr(svc, "CONFIG", config)
    log = {"start": [], "kill": 0}
    monkeypatch.setattr(svc, "_start", lambda request: log["start"].append(request))

    def kill():
        log["kill"] += 1
        return {"status": "No active session"}  # no dashboard session at all

    monkeypatch.setattr(svc, "_kill", kill)
    monkeypatch.setattr(svc, "_status", lambda tail: "status line")
    return log


def event(sequence, command, uuid=OWNER, **args):
    data = {"player_uuid": uuid, "player_name": "p", "command": command, **args}
    return {"sequence": sequence, "type": "player_command", "data": data}


def test_unauthorized_is_refused(calls):
    assert svc.handle(event(1, "build", uuid="x", name="house")) == "Not allowed."
    assert svc.handle(event(1, "stop", uuid="x")) == "Not allowed."
    assert calls == {"start": [], "kill": 0}


def test_empty_config_refuses_everyone(calls):
    svc.CONFIG.write_text(json.dumps({"owners": [], "allowlist": []}))
    assert svc.handle(event(1, "status")) == "Not allowed."


@pytest.mark.parametrize(
    "bad",
    [
        event(1, "gather", item="Iron Ore", count=5),
        event(1, "gather", item="iron", count=0),
        event(1, "gather", item="iron", count=2305),
        event(1, "gather", item="iron", count="5"),
        event(1, "build", name="../x"),
        event(1, "goto", x=1, y="2", z=3),
        event(1, "teleport"),
    ],
)
def test_malformed_args_refused(calls, bad):
    svc.handle(bad)
    assert calls["start"] == [] and calls["kill"] == 0


def test_stop_without_session(calls):
    assert svc.handle(event(1, "stop")) == "Nothing to stop."
    assert calls["kill"] == 1


def test_build_and_goto(calls):
    assert svc.handle(event(1, "build", name="house")) == "Build started."
    assert calls["start"] == [{"kind": "companion", "job": "build", "build": "builds/house.build.json"}]
    assert svc.handle(event(2, "goto", x=1, y=2, z=3)) == "goto not supported yet."


def test_gap_and_restart_do_not_crash(calls):
    sent = []
    batches = [
        {"launch_id": "a", "gap": True, "latest_sequence": 90, "events": [event(80, "stop")]},
        {"launch_id": "b", "gap": False, "latest_sequence": 1, "events": []},
    ]
    state = {"cursor": 5, "launch_id": "a", "tail": deque(maxlen=50)}
    tell = lambda uuid, text: sent.append(text)
    svc.poll(state, fetch=lambda path, payload=None: batches.pop(0), tell=tell)
    assert state["cursor"] == 90 and sent == ["Nothing to stop."]
    svc.poll(state, fetch=lambda path, payload=None: batches.pop(0), tell=tell)
    assert state["cursor"] == 1  # new launch: sequences restarted


def test_explain():
    session = {"status": "running"}
    fresh = {"job": "build", "stage": "placing", "done": 3, "total": 10, "updated_at": 100}
    assert explain(None, None, [], 0) == "Idle."
    assert explain(None, {"status": "failed"}, [], 0) == "Finished: failed."
    assert explain(None, session, [], 0) == "Starting up."
    assert explain(fresh, session, [], 105) == "Running build: placing 3/10."
    tail = [{"type": "route_stopped", "data": {"reason": "cancelled:watchdog"}}]
    text = explain(fresh, session, tail, 200)
    assert "Stalled" in text and "route_stopped: cancelled:watchdog" in text
