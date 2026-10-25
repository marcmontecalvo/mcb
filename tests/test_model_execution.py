import pytest

from mclab import model_execution
from mclab.backends import BackendError


@pytest.mark.parametrize(
    "error_type,message,category",
    [
        (BackendError, "deadline", "timing"),
        (
            model_execution.ControllerFailure,
            "Controller action rejected: late_or_distant_action",
            "timing",
        ),
        (
            model_execution.ControllerFailure,
            "Controller action rejected: stale_or_mistimed_action",
            "timing",
        ),
        (BackendError, "invalid model choice", "decision"),
    ],
)
def test_model_failure_releases_and_ends_without_fallback(
    monkeypatch, error_type, message, category
):
    class Client:
        run_id, episode_id, body_id = "r", "e", "b"

        def __init__(self):
            self.events = []
            self.operations = []
            self.records = []

        def record(self, kind, payload):
            self.records.append((kind, payload))

        def send(self, op, **fields):
            self.operations.append(op)
            assert op != "controls"
            if op == "course_end":
                self.events.append(
                    {
                        "type": "course_episode_complete",
                        "run_id": "r",
                        "episode_id": "e",
                        "body_id": "b",
                        "data": {"status": "failed", "reason": fields["reason"]},
                    }
                )
            return {}

        def acknowledge(self, *_args):
            pass

        def wait(self, predicate):
            assert predicate({})

    class Controller:
        def decide(self, observation):
            raise error_type(message)

        def drain_events(self):
            return [("tactical_result", {"status": "error", "error": message})]

    monkeypatch.setattr(model_execution, "policy_state", lambda _: {"state": "running"})
    client = Client()
    terminal = model_execution.drive(client, Controller())
    assert terminal["data"]["status"] == "failed"
    assert client.operations == ["release", "course_end"]
    assert client.records[0] == (
        "tactical_result",
        {"status": "error", "error": message},
    )
    assert client.records[-1][1]["category"] == category


def test_live_rejects_unbounded_batches_before_world_access():
    for count, budget in [(0, 40), (6, 40), (1, 101), (2, 1), (True, 40)]:
        with pytest.raises(ValueError):
            model_execution.run("unused", count=count, request_limit=budget)


def test_remote_live_batch_preserves_configured_total_budget(monkeypatch):
    spec = {
        "name": "remote",
        "enabled": True,
        "kind": "systemone",
        "root": "https://example.com",
        "model": "test",
        "deadline_s": 1,
        "request_limit": 10,
        "cost_ceiling_usd": 1,
        "max_request_cost_usd": 0.02,
    }
    monkeypatch.setattr(model_execution, "read_json", lambda _: {"backends": [spec]})
    monkeypatch.setattr(model_execution, "_run", lambda s, n, cap: (s, n, cap))
    with pytest.raises(ValueError, match="configured remote"):
        model_execution.run("remote", count=2, request_limit=12)
    selected, count, total = model_execution.run("remote", count=2, request_limit=10)
    assert selected["request_limit"] == 5
    assert count == 2 and total == 10
    spec.update(request_limit=100, max_request_cost_usd=0.03)
    with pytest.raises(ValueError, match="cost budget"):
        model_execution.run("remote", count=5, request_limit=50)


def test_terminal_snapshot_drains_event_before_deciding(monkeypatch):
    class Client:
        run_id, episode_id, body_id = "r", "e", "b"
        events = []

        def wait(self, predicate, timeout):
            self.events = [
                {
                    "type": "course_episode_complete",
                    "run_id": "r",
                    "episode_id": "e",
                    "body_id": "b",
                    "data": {"status": "success"},
                }
            ]
            assert predicate({})

    monkeypatch.setattr(
        model_execution, "policy_state", lambda _: {"state": "episode_terminal"}
    )
    assert model_execution.drive(Client(), object())["data"]["status"] == "success"


def test_close_failure_still_counts_requests_and_records_provenance():
    class Controller:
        def close(self):
            raise BackendError("shutdown failed")

        def drain_events(self):
            return [("final_result", {})]

    class Backend:
        requests = 3

        def provenance(self):
            return {"requests": 3}

    class Client:
        def __init__(self):
            self.records = []

        def record(self, kind, payload):
            self.records.append((kind, payload))

    summary = {"model_requests": 0}
    client = Client()
    with pytest.raises(BackendError, match="shutdown"):
        model_execution.close_selector(client, Controller(), Backend(), summary)
    assert summary["model_requests"] == 3
    assert client.records[-1] == ("backend_provenance", {"requests": 3})
