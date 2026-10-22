import pytest

from mclab import evidence
from mclab.paths import write_json


@pytest.fixture
def fixture_world(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "SERVER", tmp_path)
    state = tmp_path / "supervisor.json"
    monkeypatch.setattr(evidence.server, "STATE", state)
    monkeypatch.setattr(evidence.server, "status", lambda: {"state": "running"})
    write_json(state, {"launch_id": "new-launch"})
    write_json(
        tmp_path / "world/datapacks/mclab/evaluator/active.json",
        {"spec_hash": "current-spec"},
    )
    return tmp_path


def record(path, **overrides):
    value = {
        "launch_id": "new-launch",
        "spec_hash": "current-spec",
        "checked_blocks": 49 * 41 * 49,
        "mismatches": 0,
    }
    value.update(overrides)
    write_json(path / "mclab-bootstrap-evidence.json", value)


def test_matching_geometry_does_not_claim_human_inspection(fixture_world):
    record(fixture_world)
    assert not evidence.verify_bootstrap()["human_inspection_confirmed"]


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"launch_id": "old-launch"}, "Stale"),
        ({"spec_hash": "old-spec"}, "active specification"),
        ({"checked_blocks": 100}, "incomplete"),
        ({"mismatches": 1}, "mismatch"),
    ],
)
def test_stale_or_incomplete_evidence_is_rejected(fixture_world, overrides, reason):
    record(fixture_world, **overrides)
    with pytest.raises(ValueError, match=reason):
        evidence.verify_bootstrap()
