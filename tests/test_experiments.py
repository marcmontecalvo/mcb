import json

import pytest

from mclab.experiments import export_screening, freeze, layout_hash


def test_seed_metadata_does_not_manufacture_unseen_layouts():
    spec = {
        "course_id": "C2",
        "geometry": [],
        "spawn": [0, 0, 0],
        "yaw": 0,
        "pitch": 0,
        "target": [0, 0, 3],
        "initial_inventory": [],
        "seed": 1,
    }
    assert layout_hash(spec) == layout_hash({**spec, "seed": 2})
    assert layout_hash(spec) != layout_hash({**spec, "yaw": 30})


def test_freeze_refuses_redefinition(tmp_path):
    path = tmp_path / "frozen.json"
    freeze(path, {"value": 1})
    freeze(path, {"value": 1})
    with pytest.raises(ValueError, match="different"):
        freeze(path, {"value": 2})


def test_screening_excludes_evaluator_and_preserves_candidate_identity(tmp_path):
    recording = tmp_path / "events.jsonl"
    rows = [
        {"type": "server_event", "payload": {"target": [1, 2, 3]}},
        {
            "type": "policy_observation",
            "payload": {
                "schema_version": 1,
                "sensor_profile": "local-visibility-v1",
                "tick": 1,
                "body": {},
                "visible_blocks": [],
                "target": [1, 2, 3],
                "episode": {"expected": "mine"},
            },
        },
    ]
    recording.write_text("\n".join(json.dumps(row) for row in rows))
    destination = tmp_path / "screen.json"
    result = export_screening(recording, destination)
    artifact = json.loads(destination.read_text())
    assert result["states"] == 1
    state = artifact["states"][0]
    assert "target" not in state["observation"]
    assert "episode" not in state["observation"]
    assert state["valid_actions"] is None
    assert all(
        set(order) == set(artifact["candidates"]) for order in state["candidate_orders"]
    )
    assert artifact["training_ready"] is False


def test_no_policy_states_cannot_be_screening_evidence(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    with pytest.raises(ValueError, match="no policy"):
        export_screening(path, tmp_path / "out.json")
