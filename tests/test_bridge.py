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
            for _ in range(40):
                assert client.http("/state")["launch_id"] == "test-launch"
            with pytest.raises(urllib.error.HTTPError) as failure:
                client.http("/denied")
            assert failure.value.code == 403
            assert client.http("/state")["launch_id"] == "test-launch"
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
