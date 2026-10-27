import copy
import json
from dataclasses import replace

import pytest

from mclab.awareness import Awareness
from mclab.execution import PolicyMapRecorder
from mclab.navigation_goal import GoalController


@pytest.mark.parametrize("preset", ["legacy", "visible", "chunk"])
def test_preset_roundtrip(preset):
    selected = Awareness.resolve(preset)
    assert (
        Awareness.from_payload(json.loads(json.dumps(selected.payload()))) == selected
    )
    assert selected.sensor_profile == (
        "local-visibility-v1" if preset == "legacy" else "awareness-v1"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("horizontal_radius_blocks", 0),
        ("horizontal_radius_blocks", 65),
        ("vertical_radius_blocks", 0),
        ("vertical_radius_blocks", -2),
        ("vertical_radius_blocks", 65),
        ("entity_radius_blocks", 0),
        ("entity_radius_blocks", 65),
        ("entity_vertical_radius_blocks", 0),
        ("entity_vertical_radius_blocks", 65),
        ("update_interval_ticks", 0),
        ("update_interval_ticks", 21),
        ("extra_scan_cells_per_update", 0),
        ("extra_scan_cells_per_update", 8193),
        ("extra_scan_budget_ms", 0),
        ("extra_scan_budget_ms", 11),
        ("schema_version", 2),
        ("preset", "client_equivalent"),
        ("preset", []),
        ("entity_visibility", "all"),
        ("entity_visibility", []),
    ],
)
def test_reject_bad_profiles(key, value):
    with pytest.raises(ValueError):
        replace(Awareness.resolve("chunk"), **{key: value})


@pytest.mark.parametrize("value", [True, "16", 16.0, None])
def test_reject_coercion(value):
    with pytest.raises(ValueError):
        Awareness.resolve("visible", horizontal_radius_blocks=value)


def test_legacy_cannot_be_silently_modified():
    with pytest.raises(ValueError, match="frozen"):
        Awareness.resolve("legacy", entity_visibility="unoccluded")
    with pytest.raises(ValueError, match="Full height"):
        Awareness.resolve("visible", vertical_radius_blocks=-1)
    assert Awareness.resolve("chunk").entity_visibility == "visible"


@pytest.mark.parametrize("change", ["extra", "missing", "not_object"])
def test_exact_profile_schema(change):
    value = Awareness().payload()
    if change == "extra":
        value["full_server_access"] = True
    elif change == "missing":
        del value["entity_visibility"]
    else:
        value = []
    with pytest.raises(ValueError, match="schema fields"):
        Awareness.from_payload(value)


def observation(selected, *, revision="episode-one", blocks=None):
    return {
        "sensor_profile": selected.sensor_profile,
        "awareness": {"config": selected.payload(), "revision": revision},
        "tick": 100,
        "body": {
            "position": [0.5, 80, 0.5],
            "yaw": 0,
            "pitch": 0,
            "inventory": [],
            "ray": {},
            "on_ground": True,
        },
        "visible_blocks": blocks or [],
        "visible_entities": [],
    }


def test_goal_controller_requires_explicit_sensor_configuration():
    chunk = Awareness.resolve("chunk")
    broad = observation(chunk)
    with pytest.raises(ValueError, match="sensor profile"):
        GoalController([0, 80, 0]).decide(broad)
    controller = GoalController([0, 80, 0], awareness=chunk)
    assert controller.decide(broad) == {}
    wrong = copy.deepcopy(broad)
    wrong["awareness"]["config"]["horizontal_radius_blocks"] = 16
    with pytest.raises(ValueError, match="configuration"):
        controller.decide(wrong)
    with pytest.raises(ValueError, match="fresh controller"):
        controller.decide(observation(chunk, revision="different-episode"))


def test_map_recording_isolates_broader_knowledge():
    memory = PolicyMapRecorder()
    secret = {"position": [0, 79, 0], "block": "minecraft:diamond_ore"}
    kind, payload = memory.update(
        observation(Awareness.resolve("chunk"), blocks=[secret])
    )
    assert kind == "policy_map_full" and len(payload["cells"]) == 1
    kind, payload = memory.update(observation(Awareness.resolve("visible")))
    assert kind == "policy_map_full" and payload["cells"] == []
    assert payload["awareness"]["config"]["preset"] == "visible"
    memory.update(observation(Awareness.resolve("visible"), blocks=[secret]))
    _, payload = memory.update(
        observation(Awareness.resolve("visible"), revision="new")
    )
    assert payload["cells"] == []


def test_cli_exports_resolved_config_without_touching_server(
    monkeypatch, tmp_path, capsys
):
    from mclab.cli import main

    target = tmp_path / "chunk.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "mclab",
            "awareness",
            "show",
            "--preset",
            "chunk",
            "--horizontal-radius-blocks",
            "24",
            "--entity-radius-blocks",
            "12",
            "--output",
            str(target),
        ],
    )
    assert main() == 0
    value = json.loads(target.read_text())
    assert value["horizontal_radius_blocks"] == 24
    assert value["entity_radius_blocks"] == 12
    assert value["entity_visibility"] == "visible"
    assert json.loads(capsys.readouterr().out)["config"] == value


def test_live_check_cleanup_failure_cannot_be_reported_as_pass(monkeypatch, tmp_path):
    from contextlib import nullcontext

    from mclab import awareness, bridge, modbuild, paths, recorder

    class FakeBridge:
        closed = False

        def __init__(self, output):
            pass

        def synchronize(self):
            pass

        def send(self, op, **kwargs):
            return op

        def acknowledge(self, request, kind, **kwargs):
            return {"data": {"verification": {"passed": True, "checks": {}}}}

        def wait(self, predicate, **kwargs):
            if predicate({"state": "idle"}):
                raise RuntimeError("Cleanup did not finish")

        def close(self):
            FakeBridge.closed = True

    monkeypatch.setattr(bridge, "Bridge", FakeBridge)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "read_json", lambda _: {"source_hash": "current"})
    monkeypatch.setattr(modbuild, "source_hash", lambda: "current")
    monkeypatch.setattr(recorder, "exclusive_arena", nullcontext)
    with pytest.raises(RuntimeError, match="Cleanup did not finish"):
        awareness.check()
    artifact = next(tmp_path.glob("results/awareness/*/summary.json"))
    value = json.loads(artifact.read_text())
    assert value["status"] == "error"
    assert value["cleanup_error"] == "Cleanup did not finish"
    assert FakeBridge.closed
