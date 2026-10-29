import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mclab import bridge, gates
from mclab.paths import write_json


@pytest.fixture
def client(tmp_path, monkeypatch):
    state = tmp_path / "supervisor.json"
    write_json(state, {"launch_id": "test-launch", "token": "private-token-1234567890"})
    monkeypatch.setattr(bridge, "STATE", state)
    value = bridge.Bridge(tmp_path / "recording.jsonl")
    yield value
    value.close()


def test_sequences_and_payload_preserve_motor_controls(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        client,
        "http",
        lambda path, payload: sent.append((path, payload)) or {"status": "queued"},
    )
    first = client.send("controls", motor=True, controls={"duration": 5})
    second = client.send("release")
    assert sent[0][0] == "/controls"
    assert sent[0][1]["controls"] == {"duration": 5}
    assert first["sequence"] == 1 and second["sequence"] == 2
    assert first["episode_id"] == second["episode_id"]


@pytest.fixture
def wire(client, monkeypatch):
    sent = []

    def fake(path, payload=None):
        if payload is None:
            return {"fixture": [990.5, 80, 988.5]}
        sent.append((path, payload))
        return {"status": "queued"}

    monkeypatch.setattr(client, "http", fake)
    return sent


def test_ops_route_to_the_mod_that_owns_them(client, wire):
    client.body_name = "LabBody"
    client.send("release")
    client.send("course_begin")
    client.send("controls", motor=True, controls={"duration": 1})
    client.send("course_test", kind="sensor_check")
    client.send("death_proof")
    paths = [(path, payload["op"]) for path, payload in wire]
    assert paths == [
        ("/admin", "release"),
        ("/lab/admin", "course_begin"),
        ("/controls", "controls"),
        ("/admin", "sensor_check"),
        ("/lab/admin", "death_proof"),
    ]
    assert wire[-1][1]["player"] == "LabBody"


def test_native_route_operations_use_body_endpoint(client, wire):
    client.send("follow_route", route_id="r", route=[[0, 1, 0], [1, 1, 0]])
    client.send("cancel_route")
    assert [(path, payload["op"]) for path, payload in wire] == [
        ("/admin", "follow_route"),
        ("/admin", "cancel_route"),
    ]


def test_carpet_course_reset_tells_lab_which_body_to_score(client, wire):
    request = client.send("course_reset", profile_id="initial-c1-survival-v1")
    (lab_path, lab), (body_path, spawn) = wire
    assert (lab_path, lab["op"]) == ("/lab/admin", "course_reset")
    assert (body_path, spawn["op"]) == ("/admin", "spawn")
    assert lab["player"] == spawn["name"] == client.body_name
    assert spawn["replace"] and spawn["stream"] and spawn["limits"] == "course"
    assert spawn["position"] == [990.5, 80, 988.5]
    assert request is lab and lab["watchdog"]
    wire.clear()
    client.send("heartbeat")
    assert [(path, p["op"]) for path, p in wire] == [
        ("/admin", "heartbeat"),
        ("/lab/admin", "heartbeat"),
    ]


def test_named_player_course_reset_spawns_no_body(client, wire):
    client.send("course_reset", player="Steve", profile_id="p")
    client.send("heartbeat")
    # A named client (e.g. Baritone) is not heartbeated, so it gets no lab watchdog.
    assert [(path, p["op"]) for path, p in wire] == [
        ("/lab/admin", "course_reset"),
        ("/admin", "heartbeat"),
    ]
    assert client.body_name is None and "watchdog" not in wire[0][1]


def test_carpet_natural_start_spawns_sensing_body_at_start(client, wire):
    client.send(
        "natural_start",
        subject="carpet_body",
        player="",
        start=[10, 64, -3],
        goal=[20, 64, -3],
        yaw=90,
        awareness={"preset": "chunk"},
        terrain_encoding="sections-v1",
    )
    (_, lab), (_, spawn) = wire
    assert lab["player"] == spawn["name"] and "awareness" not in lab
    assert spawn["position"] == [10.5, 64, -2.5] and spawn["yaw"] == 90
    assert spawn["awareness"] == {"preset": "chunk"}
    assert spawn["terrain_encoding"] == "sections-v1"
    assert spawn["extended_observation"] and spawn["stream"]


def test_despawn_retires_body_and_stops_lab_run(client, wire):
    client.body_name = "LabBody"
    client.send("despawn")
    assert [(path, p["op"]) for path, p in wire] == [
        ("/admin", "despawn"),
        ("/lab/admin", "stop"),
    ]
    assert client.body_name is None


def test_lab_state_wins_while_lab_is_busy():
    body = {"tick": 5, "state": "ready", "body": {"registered": True, "health": 20}}
    lab = {
        "state": "running",
        "episode": {"status": "running"},
        "fixture_state": {"qualification_target_block": "minecraft:oak_log"},
        "reference_setup": {},
        "course_reset": {"generation": 2},
        "building_pickups": {"minecraft:dirt": 1},
    }
    state = bridge.merge(body, lab, policy=False)
    assert state["state"] == "running" and state["tick"] == 5
    assert state["body"]["qualification_target_block"] == "minecraft:oak_log"
    assert state["course_reset"] == {"generation": 2}
    policy = bridge.merge(body, lab, policy=True)
    assert policy["episode"] == {"status": "running"}
    assert policy["body"]["building_pickups"] == {"minecraft:dirt": 1}
    assert "qualification_target_block" not in policy["body"]
    idle = bridge.merge(body, {"state": "idle"}, policy=False)
    assert idle["state"] == "ready"


def test_request_budget_is_bounded(client):
    client.budget = 1
    client.envelope("heartbeat")
    with pytest.raises(RuntimeError, match="budget"):
        client.envelope("heartbeat")


def test_recording_contains_no_transport_token(client):
    client.record("check", {"observed": 42})
    client.stream.flush()
    text = client.stream.name
    with open(text, encoding="utf-8") as stream:
        value = stream.read()
    assert client._token not in value
    assert json.loads(value)["sequence"] == 1


def test_event_gap_invalidates_qualification(client, monkeypatch):
    monkeypatch.setattr(client, "http", lambda _: {"gap": True, "events": []})
    with pytest.raises(RuntimeError, match="event gap"):
        client.drain()


def test_changed_server_launch_is_rejected(client, monkeypatch):
    response = io.BytesIO(b'{"launch_id":"other-launch"}')
    response.status, response.reason, response.headers = 200, "OK", {}
    monkeypatch.setattr(client._connection, "request", lambda *a, **k: None)
    monkeypatch.setattr(client._connection, "getresponse", lambda: response)
    with pytest.raises(RuntimeError, match="different launch"):
        client.http("/state")


@pytest.mark.parametrize("waiting_mod", ["body", "lab"])
@pytest.mark.parametrize("initial_snapshot", [{}, {"state": "idle"}])
def test_synchronize_waits_for_initial_snapshots_before_commands(
    client, monkeypatch, waiting_mod, initial_snapshot
):
    attempts = {"body": 0, "lab": 0}
    paths = []

    def response(mod):
        def receive():
            attempts[mod] += 1
            value = (
                initial_snapshot
                if mod == waiting_mod and attempts[mod] == 1
                else {"launch_id": "test-launch", "latest_sequence": 7}
            )
            result = io.BytesIO(json.dumps(value).encode())
            result.status, result.reason, result.headers = 200, "OK", {}
            return result

        return receive

    for mod, connection in (
        ("body", client._connection),
        ("lab", client._lab_connection),
    ):
        monkeypatch.setattr(
            connection,
            "request",
            lambda method, path, **kw: paths.append((method, path)),
        )
        monkeypatch.setattr(connection, "getresponse", response(mod))
    monkeypatch.setattr(bridge.time, "sleep", lambda _: None)
    client.synchronize()
    assert client.cursor == client.lab_cursor == 7
    assert attempts[waiting_mod] >= 3
    assert all(method == "GET" for method, _ in paths)
    assert client.submitted == 0


def test_synchronize_does_not_retry_a_different_launch(client, monkeypatch):
    calls = []

    def wrong_launch(*args):
        calls.append(args)
        raise RuntimeError("Bridge belongs to a different launch")

    monkeypatch.setattr(client, "http", wrong_launch)
    with pytest.raises(RuntimeError, match="different launch"):
        client.synchronize()
    assert calls == [("/state",)]


def test_synchronize_times_out_without_submitting_commands(client, monkeypatch):
    clock = iter((0, 31))
    monkeypatch.setattr(bridge.time, "monotonic", lambda: next(clock))

    def not_ready(*_args):
        raise urllib.error.URLError("Bridge snapshot not ready")

    monkeypatch.setattr(client, "http", not_ready)
    with pytest.raises(urllib.error.URLError, match="snapshot not ready"):
        client.synchronize()
    assert client.submitted == 0


def test_transport_reuses_socket_and_preserves_http_errors(client):
    peers, requests = [], []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            peers.append(self.client_address)
            requests.append((self.path, self.headers.get("Authorization")))
            body = b'{"launch_id":"test-launch"}'
            self.send_response(403 if self.path == "/denied" else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client._connection.close()
        client._connection = bridge.http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=2
        )
        try:
            # /state and /policy merge both mods; a raw body path exercises one socket.
            for _ in range(40):
                assert client.http("/events?after=0")["launch_id"] == "test-launch"
            with pytest.raises(urllib.error.HTTPError) as failure:
                client.http("/denied")
            assert failure.value.code == 403
            assert client.http("/events?after=0")["launch_id"] == "test-launch"
            assert len(peers) == 42 and len(set(peers)) == 1
            assert all(auth == "Bearer " + client._token for _, auth in requests)
        finally:
            client._connection.close()
            server.shutdown()
            thread.join(timeout=2)


def test_ambiguous_motor_submission_is_not_retried(client, monkeypatch):
    sent = []

    def request(*args, **kwargs):
        sent.append((args, kwargs))

    def disconnected():
        raise bridge.http.client.RemoteDisconnected("lost response after admission")

    monkeypatch.setattr(client._connection, "request", request)
    monkeypatch.setattr(client._connection, "getresponse", disconnected)
    with pytest.raises(urllib.error.URLError):
        client.send("controls", motor=True, controls={"duration": 5})
    assert len(sent) == 1 and client.submitted == 1 and client.sequence == 1


def test_human_inspection_applies_only_to_reviewed_specification():
    assert gates.inspected(
        "8632e6132b76947bc406ac6c5bbee311d1fe569c9b07cd1fdaa8afae93ea9bdf"
    )
    assert not gates.inspected("unreviewed-layout")


def test_reset_failure_is_propagated_without_waiting_for_timeout(client, monkeypatch):
    client.events.append(
        {
            "type": "course_reset_failed",
            "data": {"request_sequence": 7, "reason": "state_changed"},
        }
    )
    monkeypatch.setattr(client, "wait", lambda predicate, timeout: predicate({}))
    with pytest.raises(RuntimeError, match="state_changed"):
        client.acknowledge({"sequence": 7}, "course_reset_complete")


def test_begin_verification_failure_does_not_wait_for_start_timeout(
    client, monkeypatch
):
    client.events.append(
        {
            "type": "course_begin_failed",
            "data": {"request_sequence": 9, "reason": "geometry_mismatch"},
        }
    )
    monkeypatch.setattr(client, "wait", lambda predicate, timeout: predicate({}))
    with pytest.raises(RuntimeError, match="geometry_mismatch"):
        client.acknowledge({"sequence": 9}, "course_started")
