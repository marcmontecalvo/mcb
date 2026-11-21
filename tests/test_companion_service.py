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


def test_an_empty_config_lets_every_player_command_the_bot(calls):
    # Nobody listed: everyone in the game may talk to the bot and give it orders.
    svc.CONFIG.write_text(json.dumps({"owners": [], "allowlist": []}))
    assert svc.handle(event(1, "status", uuid="x")) == "status line"
    svc.CONFIG.write_text("{}")
    assert svc.handle(event(1, "status", uuid="x")) == "status line"


def test_a_missing_config_lets_every_player_command_the_bot(calls, tmp_path):
    svc.CONFIG = tmp_path / "absent.json"
    assert svc.handle(event(1, "status", uuid="x")) == "status line"


def test_a_broken_config_refuses_everyone(calls):
    svc.CONFIG.write_text("{not json")
    assert svc.handle(event(1, "status")) == "Not allowed."


def test_a_listed_player_name_is_allowed_like_a_uuid(calls):
    svc.CONFIG.write_text(json.dumps({"owners": [], "allowlist": ["Steve"]}))
    allowed = event(1, "status", uuid="x")
    allowed["data"]["player_name"] = "steve"
    assert svc.handle(allowed) == "status line"
    assert svc.handle(event(1, "status", uuid="x")) == "Not allowed."


def test_the_shipped_config_names_the_owner():
    shipped = json.loads((svc.ROOT / "config/companion.json").read_text())
    assert "243def0a-1a75-4518-8358-b4cb18a1cc54" in shipped["owners"]


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
    assert calls["start"] == [
        {"kind": "companion", "job": "build", "build": "builds/house.build.json"}
    ]
    assert svc.handle(event(2, "goto", x=1, y=2, z=3)) == "goto not supported yet."


def test_gap_and_restart_do_not_crash(calls):
    sent = []
    batches = [
        {
            "launch_id": "a",
            "gap": True,
            "latest_sequence": 90,
            "events": [event(80, "stop")],
        },
        {"launch_id": "b", "gap": False, "latest_sequence": 1, "events": []},
    ]
    state = {"cursor": 5, "launch_id": "a", "tail": deque(maxlen=50)}
    def tell(uuid, text):
        sent.append(text)
    svc.poll(state, fetch=lambda path, payload=None: batches.pop(0), tell=tell)
    assert state["cursor"] == 90 and sent == ["Nothing to stop."]
    svc.poll(state, fetch=lambda path, payload=None: batches.pop(0), tell=tell)
    assert state["cursor"] == 1  # new launch: sequences restarted


def test_explain():
    session = {"status": "running"}
    fresh = {
        "job": "build",
        "stage": "placing",
        "done": 3,
        "total": 10,
        "updated_at": 100,
    }
    assert explain(None, None, [], 0) == "Idle."
    assert explain(None, {"status": "failed"}, [], 0) == "Finished: failed."
    assert explain(None, session, [], 0) == "Starting up."
    assert explain(fresh, session, [], 105) == "Running build: placing 3/10."
    tail = [{"type": "route_stopped", "data": {"reason": "cancelled:watchdog"}}]
    text = explain(fresh, session, tail, 200)
    assert "Stalled" in text and "route_stopped: cancelled:watchdog" in text


def test_explain_counts_recent_done_ops_as_activity():
    session = {"status": "running"}
    stale = {"job": "build", "stage": "starting", "updated_at": 0}
    acting = [{"type": "break_block_done", "at": 195}]
    assert explain(stale, session, acting, 200).startswith("Running build: starting")
    old = [{"type": "break_block_done", "at": 100}]
    assert "Stalled" in explain(stale, session, old, 200)
    assert "Stalled" in explain(stale, session, [], 200)
