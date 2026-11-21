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


@pytest.fixture
def runs(monkeypatch, tmp_path):
    from mclab import dashboard

    monkeypatch.setattr(dashboard, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(svc, "RUNS", tmp_path / "companion")

    def run(name, **progress):
        folder = tmp_path / "companion" / name
        folder.mkdir(parents=True)
        (folder / "progress.json").write_text(json.dumps(progress))
        return folder

    return run


def test_status_describes_a_run_build_run_without_a_dashboard_session(runs):
    # /mcb status during run-build.py answered "Idle.": it read dashboard sessions only.
    now = 1_795_284_500.0
    runs(
        "20261121T180806599463Z",
        job="build",
        stage="placing",
        done=40,
        total=1242,
        updated_at=now - 2,
    )
    assert svc._status([], now=now) == "Running build: placing 40/1242."


def test_status_reports_an_inspection_hold_as_finished_not_stalled(runs):
    now = 1_795_284_500.0
    runs(
        "20261121T180806599463Z", job="build", stage="inspection", updated_at=now - 600
    )
    assert svc._status([], now=now) == (
        "Finished build; holding the world for inspection."
    )


def test_status_is_idle_when_the_last_run_ended(runs):
    now = 1_795_284_500.0
    folder = runs(
        "20261121T170000000000Z", job="build", stage="done", updated_at=now - 60
    )
    (folder / "summary.json").write_text("{}")
    assert svc._status([], now=now) == "Idle."


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


def test_the_service_replies_until_told_to_stop_and_waits_out_a_server_not_up(
    calls, capsys
):
    # The supervisor runs this loop for the server's whole life: the bridge is down
    # until Minecraft is up, so a refused call is retried and logged once, not per poll.
    import threading

    stop = threading.Event()
    replies = []
    script = [
        OSError("refused"),
        OSError("refused"),
        {"launch_id": "a", "latest_sequence": 4, "events": []},
        {
            "launch_id": "a",
            "latest_sequence": 5,
            "events": [event(5, "status")],
        },
    ]

    def call(path, payload=None):
        if path == "/tell":
            replies.append(payload["text"])
            stop.set()
            return {}
        step = (
            script.pop(0)
            if script
            else {"launch_id": "a", "latest_sequence": 5, "events": []}
        )
        if isinstance(step, Exception):
            raise step
        return step

    svc.run(stop=stop, call=call, pause=lambda s: None)

    assert replies == ["status line"]
    assert capsys.readouterr().out.count("refused") == 1


def test_the_supervisor_runs_the_companion_service():
    import inspect

    from mclab import supervisor

    assert "companion_service.run" in inspect.getsource(supervisor.main)


def test_events_are_stamped_when_read_so_recent_work_counts_as_progress(calls):
    # Bridge events carry a tick, not a time: unstamped, every break_block_done was
    # ignored and a busy site prep read "Stalled ... no progress for 30s".
    done = {"sequence": 7, "type": "break_block_done", "data": {}}
    batch = {"launch_id": "a", "latest_sequence": 7, "events": [done]}
    state = {"cursor": 6, "launch_id": "a", "tail": deque(maxlen=50)}
    svc.poll(state, fetch=lambda path, payload=None: batch, now=lambda: 1234.5)
    assert state["tail"][-1]["at"] == 1234.5
    stale = {"job": "build", "stage": "site prep", "updated_at": 1200.0}
    text = explain(stale, {"status": "running"}, list(state["tail"]), 1240.0)
    assert text.startswith("Running build: site prep")


def test_sample_noise_does_not_push_recent_work_out_of_the_status_tail(calls):
    # Run 20261121T195634605213Z: half the body's events are policy_sample and a
    # quarter received, so a place_block_done left the 50-event tail within seconds
    # and a busy site prep read "Stalled ... no progress for 21s".
    work = {"sequence": 1, "type": "place_block_done", "data": {}}
    noise = [
        {"sequence": n, "type": kind, "data": {}}
        for n in range(2, 300)
        for kind in ("policy_sample", "received")
    ]
    batch = {"launch_id": "a", "latest_sequence": 300, "events": [work, *noise]}
    state = {"cursor": 0, "launch_id": "a", "tail": deque(maxlen=50)}
    svc.poll(state, fetch=lambda path, payload=None: batch, now=lambda: 1234.5)
    assert [e["type"] for e in state["tail"]] == ["place_block_done"]
    assert state["cursor"] == 300
    stale = {"job": "build", "stage": "site prep", "updated_at": 1200.0}
    text = explain(stale, {"status": "running"}, list(state["tail"]), 1240.0)
    assert text.startswith("Running build: site prep")


@pytest.mark.parametrize("kind", ["route_progress", "block_broken", "leak_plugged"])
def test_explain_counts_walking_and_world_edits_as_activity(kind):
    # Walking between site-prep cells finishes no op for a while: still working.
    stale = {"job": "build", "stage": "site prep", "updated_at": 0}
    acting = [{"type": kind, "at": 195}]
    assert explain(stale, {"status": "running"}, acting, 200).startswith(
        "Running build: site prep"
    )


def test_the_status_tail_keeps_failures_and_commands():
    assert svc.notable("break_block_failed")
    assert svc.notable("route_stopped")
    assert svc.notable("player_command")
    assert svc.notable("place_block_done")
    assert not svc.notable("policy_sample")
    assert not svc.notable("received")
