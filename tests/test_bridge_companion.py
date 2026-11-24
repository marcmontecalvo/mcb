"""Bridge as the companion host uses it: body-only mode, no budget, trim, cancel."""

import pytest

from mclab import bridge
from mclab.paths import write_json

BODY_ONLY = {"bridge_port": 18775, "supervisor_port": 18776, "lab_mod": False}


@pytest.fixture
def make(tmp_path, monkeypatch):
    state = tmp_path / "supervisor.json"
    write_json(state, {"launch_id": "play-launch", "token": "private-token-1234567890"})
    monkeypatch.setattr(bridge, "STATE", state)
    made = []

    def factory(settings):
        monkeypatch.setattr(bridge, "config", lambda: dict(settings))
        value = bridge.Bridge(tmp_path / f"events-{len(made)}.jsonl")
        made.append(value)
        return value

    yield factory
    for value in made:
        value.close()


@pytest.fixture
def body_only(make):
    return make(BODY_ONLY)


def fake_raw(client, monkeypatch, responses):
    """Answer _raw(lab, path, payload) from ``responses[path]``; record every call."""
    calls = []

    def raw(lab, path, payload):
        calls.append((lab, path, payload))
        value = responses.get(path, {"status": "queued"})
        return value(payload) if callable(value) else value

    monkeypatch.setattr(client, "_raw", raw)
    return calls


def test_lab_mode_is_the_default_and_needs_a_lab_port(make):
    assert make({"bridge_port": 1, "lab_port": 2}).lab is True
    assert make({"bridge_port": 1, "lab_port": 2, "lab_mod": False}).lab is False
    assert make({"bridge_port": 1}).lab is False


def test_body_only_synchronize_reads_no_lab_events(body_only, monkeypatch):
    calls = fake_raw(
        body_only,
        monkeypatch,
        {
            "/state": {"state": "idle", "tick": 3},
            "/events?after=0": {"latest_sequence": 7},
        },
    )
    body_only.synchronize()
    assert all(lab is False for lab, _, _ in calls)
    assert body_only.cursor == 7 and body_only.lab_cursor == 0


def test_body_only_state_reports_a_ready_body_as_a_running_session(
    body_only, monkeypatch
):
    body = {"state": "ready", "tick": 9, "body": {"position": [0.5, 64, 0.5]}}
    calls = fake_raw(body_only, monkeypatch, {"/state": body, "/policy": body})
    value = body_only.http("/state")
    assert value["state"] == "running" and value["body_state"] == "ready"
    assert value["body"]["position"] == [0.5, 64, 0.5]
    assert body_only.http("/policy")["state"] == "running"
    assert [lab for lab, _, _ in calls] == [False, False]


@pytest.mark.parametrize("raw_state", ["dead", "disconnected_or_dead", "settling"])
def test_body_only_state_passes_any_other_body_state_through(
    body_only, monkeypatch, raw_state
):
    fake_raw(body_only, monkeypatch, {"/state": {"state": raw_state, "tick": 1}})
    value = body_only.http("/state")
    assert value["state"] == raw_state and value["body_state"] == raw_state


def test_body_only_refuses_lab_requests(body_only, monkeypatch):
    calls = fake_raw(body_only, monkeypatch, {})
    with pytest.raises(RuntimeError, match="mcb-lab"):
        body_only.http("/lab/state")
    with pytest.raises(RuntimeError, match="mcb-lab"):
        body_only.send("course_begin")
    assert calls == []


def test_body_only_despawn_stops_no_lab_run(body_only, monkeypatch):
    calls = fake_raw(body_only, monkeypatch, {})
    body_only.body_name = "MCBot"
    body_only.send("despawn")
    assert [(lab, path, p["op"]) for lab, path, p in calls] == [
        (False, "/admin", "despawn")
    ]
    assert body_only.body_name is None


def test_body_only_drain_reads_the_body_stream_only(body_only, monkeypatch):
    events = {"gap": False, "latest_sequence": 4, "events": [{"tick": 2, "type": "x"}]}
    calls = fake_raw(body_only, monkeypatch, {"/events?after=0": events})
    assert body_only.drain() == [{"tick": 2, "type": "x"}]
    assert [path for _, path, _ in calls] == ["/events?after=0"]
    assert body_only.cursor == 4


def test_no_budget_means_unbounded_requests(body_only, monkeypatch):
    fake_raw(body_only, monkeypatch, {})
    body_only.budget = None
    for _ in range(5):
        body_only.send("heartbeat")
    assert body_only.sequence == 5 and body_only.submitted == 5


def test_trim_bounds_the_kept_events_and_samples(body_only):
    body_only.events = [{"n": i} for i in range(10)]
    body_only.samples = [{"n": i} for i in range(10)]
    body_only.trim(keep=3)
    assert [e["n"] for e in body_only.events] == [7, 8, 9]
    assert [s["n"] for s in body_only.samples] == [7, 8, 9]
    body_only.trim(keep=50)  # nothing to drop
    assert len(body_only.events) == 3


def test_cancel_check_runs_before_any_request(body_only, monkeypatch):
    calls = fake_raw(body_only, monkeypatch, {})

    class Cancelled(BaseException):
        pass

    def check():
        raise Cancelled

    body_only.cancel_check = check
    with pytest.raises(Cancelled):
        body_only.state()
    with pytest.raises(Cancelled):
        body_only.wait(lambda _: True)
    assert calls == []


def test_state_runs_the_cancel_check_every_poll(body_only, monkeypatch):
    seen = []
    body_only.cancel_check = lambda: seen.append(1)
    events = {"gap": False, "latest_sequence": 0, "events": []}
    fake_raw(
        body_only,
        monkeypatch,
        {"/events?after=0": events, "/state": {"state": "ready", "tick": 5}},
    )
    body_only.last_heartbeat = float("inf")  # no heartbeat in this test
    body_only.state(heartbeat=False)
    body_only.state(heartbeat=False)
    assert len(seen) == 2 and len(body_only.samples) == 1


# --- one body among several (the arena server) ---------------------------------------


def test_a_bridge_reads_every_body_unless_it_is_scoped(body_only, monkeypatch):
    calls = fake_raw(
        body_only,
        monkeypatch,
        {
            "/state": {"state": "idle", "tick": 3},
            "/events?after=0": {"latest_sequence": 1},
        },
    )
    body_only.synchronize()
    assert [path for _, path, _ in calls] == ["/state", "/events?after=0"]


def test_a_scoped_bridge_asks_for_its_own_body_only(body_only, monkeypatch):
    body_only.scoped = True
    mine = "body=" + body_only.body_id
    state = {"state": "ready", "tick": 4, "body": {"position": [1, 64, 1]}}
    calls = fake_raw(
        body_only,
        monkeypatch,
        {
            f"/state?{mine}": state,
            f"/policy?{mine}": state,
            f"/events?after=0&{mine}": {
                "latest_sequence": 5,
                "gap": False,
                "events": [],
            },
            f"/events?after=5&{mine}": {
                "latest_sequence": 5,
                "gap": False,
                "events": [],
            },
        },
    )
    body_only.synchronize()
    assert body_only.cursor == 5
    assert body_only.http("/state")["body_state"] == "ready"
    assert body_only.http("/policy")["body_state"] == "ready"
    body_only.last_heartbeat = float("inf")
    body_only.state(heartbeat=False)
    assert [path for _, path, _ in calls] == [
        f"/state?{mine}",
        f"/events?after=0&{mine}",
        f"/state?{mine}",
        f"/policy?{mine}",
        f"/events?after=5&{mine}",
        f"/state?{mine}",
    ]


def test_a_scoped_bridge_still_refuses_a_snapshot_that_is_not_ready(body_only):
    body_only.scoped = True
    from mclab.bridge import Bridge

    class Response:
        status, reason, headers = 200, "OK", {}

        def read(self):
            return b'{"state": "idle"}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Connection:
        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    body_only._connection = Connection()
    with pytest.raises(bridge.urllib.error.URLError, match="not ready"):
        Bridge._raw(body_only, False, f"/state?body={body_only.body_id}", None)
