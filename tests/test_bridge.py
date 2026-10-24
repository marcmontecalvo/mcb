import io
import json

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
    monkeypatch.setattr(bridge.urllib.request, "urlopen", lambda *a, **k: response)
    with pytest.raises(RuntimeError, match="different launch"):
        client.http("/state")


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
