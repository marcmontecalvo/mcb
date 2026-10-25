import json

import pytest

from mclab import screening
from mclab.backends import BackendError, Decision
from mclab.paths import digest, write_json
from mclab.screen_fixtures import build_fixture
from mclab.tactical import CANDIDATES, INSTRUCTIONS


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(screening, "ROOT", tmp_path)
    fixture = build_fixture(instructions=INSTRUCTIONS)
    fixture["states"] = fixture["states"][:1]
    path = tmp_path / "fixture.json"
    write_json(path, fixture)
    write_json(
        tmp_path / "docs/G4_SCREEN_REVIEW.json",
        {"status": "passed", "fixture_hash": digest(fixture)},
    )
    write_json(
        tmp_path / "config/backends.json",
        {
            "backends": [
                {
                    "name": "test",
                    "enabled": True,
                    "request_limit": 3,
                }
            ]
        },
    )
    for module in ("screening.py", "backends.py", "tactical.py", "screen_fixtures.py"):
        source = tmp_path / "src/mclab" / module
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("test source")
    calls = []

    class Backend:
        local = True

        def __init__(self, spec):
            self.requests = 0
            self.exchanges = []

        def decide(self, request):
            calls.append(request)
            self.requests += 1
            self.exchanges.append({"request": {"observation": request.observation}})
            if len(calls) == 2:
                raise BackendError("Malformed Choice response")
            return Decision("forward", {}, "test", 2, "test")

        def provenance(self):
            return {"requests": self.requests}

        def close(self):
            pass

    monkeypatch.setattr(screening, "HttpBackend", Backend)
    return path, calls


def test_labels_never_on_wire_and_failure_stays_in_denominator(environment):
    path, calls = environment
    result = screening.run("test", path, limit=3)
    assert result["attempted"] == 3
    assert result["correct"] == 2 and result["errors"] == 1
    assert [list(c.candidates) for c in calls][:2] == [
        list(CANDIDATES),
        list(reversed(CANDIDATES)),
    ]
    for request in calls:
        text = json.dumps(request.observation)
        assert not any(
            key in text
            for key in ("valid_actions", "rationale", "geometry_facts", "category")
        )
    group = screening.report()["groups"][0]
    assert group["matrix_complete"] and group["unique_cases"] == 3
    assert group["backend_errors"] == 1


def test_mismatched_review_blocks_requests(environment):
    path, calls = environment
    fixture = json.loads(path.read_text())
    fixture["states"][0]["valid_actions"] = ["stop"]
    write_json(path, fixture)
    with pytest.raises(ValueError, match="independent geometry review"):
        screening.run("test", path, limit=3)
    assert not calls


def test_partial_and_duplicate_matrices_cannot_be_reported_complete(environment):
    path, _ = environment
    screening.run("test", path, limit=1)
    group = screening.report()["groups"][0]
    assert group["missing_cases"] == [1, 2]
    assert not group["matrix_complete"]
    screening.run("test", path, limit=1)
    group = screening.report()["groups"][0]
    assert group["duplicate_cases"] == [0]
    assert group["attempted"] == 2 and group["unique_cases"] == 1


def test_interrupted_paid_request_remains_in_denominator(environment, monkeypatch):
    path, _ = environment
    original = screening.HttpBackend.decide

    def interrupt(self, request):
        original(self, request)
        raise KeyboardInterrupt()

    monkeypatch.setattr(screening.HttpBackend, "decide", interrupt)
    with pytest.raises(KeyboardInterrupt):
        screening.run("test", path, limit=3)
    summaries = list((path.parent / "results/screening").glob("*/summary.json"))
    result = json.loads(summaries[0].read_text())
    assert result["status"] == "interrupted"
    assert result["attempted"] == 1
    assert result["outcomes"][0]["status"] == "interrupted"
    assert result["provenance"]["requests"] == 1


def test_deadline_stops_batch_instead_of_consuming_unadmitted_cases(
    environment, monkeypatch
):
    path, calls = environment
    original = screening.HttpBackend.decide

    def timeout(self, request):
        original(self, request)
        raise BackendError("Decision deadline exceeded")

    monkeypatch.setattr(screening.HttpBackend, "decide", timeout)
    result = screening.run("test", path, limit=3)
    assert result["status"] == "stopped_backend_unready"
    assert result["attempted"] == 1 and len(calls) == 1
    assert result["outcomes"][0]["admitted"]
    assert not screening.report()["groups"][0]["matrix_complete"]


def test_cli_stopped_screen_is_a_failure_exit(monkeypatch):
    from mclab import cli

    monkeypatch.setattr(
        "sys.argv", ["mclab", "experiments", "screen-run", "--backend", "test"]
    )
    monkeypatch.setattr(
        cli.screening,
        "run",
        lambda *_args, **_kwargs: {
            "artifact": "test",
            "status": "stopped_backend_unready",
            "attempted": 1,
            "correct": 0,
            "errors": 1,
        },
    )
    assert cli.main() == 1


@pytest.mark.parametrize("limit,offset", [(41, 0), (0, 0), (1, -1), (True, 0)])
def test_unbounded_screening_rejected_before_provider_calls(limit, offset):
    with pytest.raises(ValueError):
        screening.run("unused", None, limit=limit, offset=offset)
