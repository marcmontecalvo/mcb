import math
from contextlib import nullcontext

import pytest

from mclab import natural


@pytest.mark.parametrize("repetitions", [True, 0, 4])
def test_repetition_bounds_rejected_before_world_changes(monkeypatch, repetitions):
    monkeypatch.setattr(
        natural, "natural_world", lambda *_: pytest.fail("World changed")
    )
    route = {"route_id": "R1", "start": [0, 80, 0], "goal": [0, 80, 100], "yaw": 0}
    with pytest.raises(ValueError, match="repetitions"):
        natural.run(1337, [route], repetitions=repetitions)


@pytest.mark.parametrize("view_distance", [True, 1, 33, 4.0])
def test_view_distance_bounds_rejected_before_world_changes(monkeypatch, view_distance):
    monkeypatch.setattr(
        natural, "natural_world", lambda *_: pytest.fail("World changed")
    )
    route = {"route_id": "R1", "start": [0, 80, 0], "goal": [0, 80, 100], "yaw": 0}
    with pytest.raises(ValueError, match="view distance"):
        natural.run(1337, [route], view_distance=view_distance)


def test_view_distance_edit_and_restore(monkeypatch, tmp_path):
    properties = tmp_path / "server.properties"
    original = "level-name=world\nview-distance=6\n"
    properties.write_text(original)
    monkeypatch.setattr(natural, "PROPERTIES", properties)
    monkeypatch.setattr(natural, "_stop", lambda: None)
    with natural.natural_world(1337, 4):
        assert "view-distance=4" in properties.read_text()
        assert natural._server_view_distance() == 4
    assert properties.read_text() == original


def test_three_entrant_repetitions_keep_failures_and_unique_paths(
    monkeypatch, tmp_path
):
    from mclab.movement_policy import MovementPolicy

    snapshot = tmp_path / "natural-1337"
    snapshot.mkdir()
    (snapshot / "level.dat").touch()
    monkeypatch.setattr(natural, "SNAPSHOTS", tmp_path)
    monkeypatch.setattr(natural, "RESULTS", tmp_path / "results")
    worlds = []
    monkeypatch.setattr(
        natural, "natural_world", lambda *a: worlds.append(a) or nullcontext()
    )
    monkeypatch.setattr(natural, "read_json", lambda _: {"source_hash": "current"})
    monkeypatch.setattr(natural, "source_hash", lambda: "current")
    monkeypatch.setattr(natural, "sha256", lambda _: "test-hash")
    monkeypatch.setattr(natural, "_source_commit", lambda: "test-commit")
    paths = []

    def attempt(seed, route, entrant, mobs, destination, **kwargs):
        paths.append(destination)
        if entrant == "segmented_goal":
            raise TimeoutError("Retained timeout")
        return {"status": "success", "ticks": 100}

    monkeypatch.setattr(natural, "attempt", attempt)
    route = {"route_id": "R1", "start": [0, 80, 0], "goal": [0, 80, 100], "yaw": 0}
    summary = natural.run(
        1337,
        [route],
        entrants=("segmented_goal", "baritone", "v8_goal"),
        conditions=(False,),
        repetitions=3,
        movement_policy=MovementPolicy(),
        view_distance=4,
    )
    assert worlds == [(1337, 4)]
    assert summary["server_view_distance_chunks"] == 4
    assert len(paths) == len(set(paths)) == 9
    assert len(summary["attempts"]) == 9
    assert [r["repetition"] for r in summary["attempts"]] == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    assert sum(r["status"] == "invalid" for r in summary["attempts"]) == 3


def _cells():
    # A dry ridge field: height varies along x, with a pond in one corner.
    cells = []
    for x in range(-160, 161, 4):
        for z in range(-160, 161, 4):
            water = x > 120 and z > 120
            height = 70 + int(12 * math.sin(x / 20))
            cells.append([x, z, height, water, "water" if water else "grass_block"])
    return cells


def test_routes_are_dry_spread_and_in_length_band():
    routes = natural.pick_routes(_cells(), count=5)
    assert len(routes) == 5
    for route in routes:
        assert natural.ROUTE_MIN <= route["length"] <= natural.ROUTE_MAX
        assert route["water_fraction"] <= 0.1
        assert -180 <= route["yaw"] < 180
    for a in routes:
        for b in routes:
            if a is not b:
                assert math.dist(a["start"][::2], b["start"][::2]) >= 48


def test_property_edit_only_touches_named_keys():
    text = "level-name=world\nlevel-type=minecraft\\:flat\nserver-port=25575\n"
    edited = natural._set_properties(text, {"level-name": "natural-1"})
    assert edited == text.replace("level-name=world", "level-name=natural-1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("route_id", "../escape"),
        ("start", [0, 80]),
        ("goal", [0, 80, "0"]),
        ("yaw", float("nan")),
        ("goal", [1000, 80, 0]),
    ],
)
def test_malformed_route_rejected_before_world_mutation(monkeypatch, field, value):
    def fail_if_started(*args, **kwargs):
        pytest.fail("Invalid routes must be rejected before world changes")

    monkeypatch.setattr(natural, "natural_world", fail_if_started)
    route = {"route_id": "R1", "start": [0, 80, 0], "goal": [0, 80, 100], "yaw": 0}
    route[field] = value
    with pytest.raises(ValueError):
        natural.run(1337, [route])


def test_stale_installed_mod_rejected_before_world_mutation(monkeypatch):
    def fail_if_started(*args, **kwargs):
        pytest.fail("Stale mods must be rejected before world changes")

    monkeypatch.setattr(natural, "natural_world", fail_if_started)
    monkeypatch.setattr(natural, "read_json", lambda _: {"source_hash": "old"})
    monkeypatch.setattr(natural, "source_hash", lambda: "new")
    route = {"route_id": "R1", "start": [0, 80, 0], "goal": [0, 80, 100], "yaw": 0}
    with pytest.raises(RuntimeError, match="Rebuild and install"):
        natural.run(1337, [route])


def test_missing_snapshot_rejected_before_world_mutation(monkeypatch, tmp_path):
    def fail_if_started(*args, **kwargs):
        pytest.fail("Missing snapshots must be rejected before world changes")

    monkeypatch.setattr(natural, "natural_world", fail_if_started)
    monkeypatch.setattr(natural, "read_json", lambda _: {"source_hash": "current"})
    monkeypatch.setattr(natural, "source_hash", lambda: "current")
    monkeypatch.setattr(natural, "SNAPSHOTS", tmp_path)
    route = {"route_id": "R1", "start": [0, 80, 0], "goal": [0, 80, 100], "yaw": 0}
    with pytest.raises(ValueError, match="snapshot missing"):
        natural.run(1337, [route])


def test_terrain_override_rejected_for_frozen_entrant_before_world_mutation(
    monkeypatch,
):
    from mclab.terrain_steps import TerrainPolicy

    def fail_if_started(*args, **kwargs):
        pytest.fail("Terrain policy must be validated before world changes")

    monkeypatch.setattr(natural, "natural_world", fail_if_started)
    route = {"route_id": "R1", "start": [0, 80, 0], "goal": [0, 80, 100], "yaw": 0}
    with pytest.raises(ValueError, match="segmented_goal"):
        natural.run(1337, [route], terrain_policy=TerrainPolicy())
