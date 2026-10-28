import pytest
from test_spatial_memory import identity

from mclab.route_search import RouteSearch
from mclab.spatial_memory import MapArchive, SpatialMemory


def floor_map(positions):
    cells = {}
    for x, z in positions:
        for y in range(5):
            cells[x, y, z] = {
                "position": [x, y, z],
                "block": "minecraft:stone" if y == 0 else "minecraft:air",
                "collision_boxes": [[0, 0, 0, 1, 1, 1]] if y == 0 else [],
            }
    return cells


def test_detour_survives_eviction_restart_and_segmented_search(tmp_path):
    # The only route initially moves AWAY from the goal around an unknown chasm.
    positions = (
        [(0, z) for z in range(9)]
        + [(x, 8) for x in range(1, 9)]
        + [(8, z) for z in reversed(range(8))]
    )
    cells = floor_map(positions)
    archive = MapArchive(tmp_path / "detour.db", identity())
    memory = SpatialMemory(archive, "discovery")
    try:
        memory.ingest(
            {
                "sensor_profile": "local-visibility-v1",
                "tick": 5,
                "visible_blocks": list(cells.values()),
            }
        )
        assert len(memory.cells) == len(cells)
    finally:
        memory.close()
    restarted = SpatialMemory(MapArchive(archive.path, identity()), "recall")
    try:
        restarted.recall((4, 1, 4), 10, 10)
        revision, rows = restarted.snapshot()
        snapshot = {tuple(c["position"]): c for c in rows}
        search = RouteSearch(snapshot, (0, 1, 0), (8, 1, 0), map_revision=revision)
        counts = []
        for _ in range(100):
            result = search.advance(node_limit=2, budget_ms=100, segment_length=100)
            counts.append(result.expanded)
            if result.status != "searching":
                break
        assert result.status == "reached"
        assert result.route == tuple((x, 1, z) for x, z in positions)
        assert all(b - a <= 2 for a, b in zip([0, *counts], counts, strict=False))
        assert search.advance(segment_length=5).status == "segment"
        assert len(search.advance(segment_length=5).route) == 5
        assert restarted.fresh(5) == {}
    finally:
        restarted.close()


def test_unknown_support_and_clearance_cannot_form_route():
    cells = floor_map([(0, 0), (1, 0), (2, 0)])
    del cells[1, 1, 0]
    search = RouteSearch(cells, (0, 1, 0), (2, 1, 0), map_revision=1)
    result = search.advance(budget_ms=100)
    assert result.status == "unreachable"
    assert result.route == ((0, 1, 0),)


def test_horizontal_goal_radius_cannot_accept_the_floor_below_goal():
    cells = floor_map([(x, 0) for x in range(5)])
    search = RouteSearch(cells, (0, 1, 0), (3, 2, 0), map_revision=1, goal_radius=3)
    assert search.advance(budget_ms=100).status == "unreachable"


def test_goal_radius_accepts_horizontal_disk_at_exact_height():
    cells = floor_map([(0, 0)])
    search = RouteSearch(cells, (0, 1, 0), (1, 1, 1), map_revision=1, goal_radius=1.5)
    assert search.advance(budget_ms=100).status == "reached"


def test_confirmed_foothold_is_followed_even_when_away_from_goal(tmp_path):
    from mclab.navigation_segmented import SegmentedController
    from mclab.terrain_steps import StepExecutor, StepPlan, TerrainPolicy

    cells = floor_map([(x, z) for x in range(-1, 2) for z in range(-2, 5)])
    cells[0, 1, -1] = {
        "position": [0, 1, -1],
        "block": "minecraft:cobblestone",
        "collision_boxes": [[0, 0, 0, 1, 1, 1]],
    }
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    try:
        policy = TerrainPolicy()
        controller = SegmentedController(
            (0, 2, 4), memory, radius=0.7, terrain_policy=policy
        )
        controller.terrain = StepExecutor(
            StepPlan((0, 1, 0), (0, 2, -1), places=((0, 1, -1),)),
            policy,
        )
        controller.terrain.index = 1
        controller.terrain.places_confirmed = 1
        obs = {
            "sensor_profile": "local-visibility-v1",
            "tick": 1,
            "visible_blocks": list(cells.values()),
            "body": {"position": [0.5, 1, 0.5], "yaw": 180, "pitch": 0},
        }
        assert controller.decide(obs) == {}
        assert controller.committed_step == (0, 2, -1)
        command = controller.decide(obs)
        assert command.get("forward", 0) > 0 and command["jump"]
        controller.cancel()
        assert controller.committed_step is None
        assert controller.decide(obs) == {}
    finally:
        memory.close()


@pytest.mark.parametrize("obstructed", [False, True])
def test_forecast_walk_is_followed_by_its_planned_placement(tmp_path, obstructed):
    from mclab.navigation_segmented import SegmentedController
    from mclab.terrain_steps import StepPlan, TerrainPolicy

    cells = floor_map([(x, z) for x in range(-1, 2) for z in range(-2, 4)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    try:
        controller = SegmentedController(
            (0, 3, 3), memory, terrain_policy=TerrainPolicy()
        )
        walk = StepPlan((0, 1, 0), (0, 1, -1))
        place = StepPlan((0, 1, -1), (0, 2, 0), places=((0, 1, 0),))
        controller.terrain_forecast = (walk, place)
        controller.terrain_queue = [place]
        controller.terrain_queue_policy = TerrainPolicy()
        controller.places_reserved = 1
        controller.committed_step = walk.target
        controller.commit_started = 1
        if obstructed:
            cells[0, 2, 0] = {
                "position": [0, 2, 0],
                "block": "minecraft:bedrock",
                "collision_boxes": [[0, 0, 0, 1, 1, 1]],
            }
        obs = {
            "sensor_profile": "local-visibility-v1",
            "tick": 2,
            "visible_blocks": list(cells.values()),
            "body": {
                "position": [0.5, 1, -0.5],
                "yaw": 0,
                "pitch": 0,
                "inventory": [{"slot": 3, "item": "minecraft:cobblestone", "count": 1}],
            },
        }
        assert controller.decide(obs) == {}
        if obstructed:
            assert controller.terrain is None
            assert not controller.terrain_queue
            assert controller.places_reserved == 0
            assert controller.mode == "terrain_replanning"
        else:
            assert controller.terrain.plan == place
            assert controller.terrain.operations == [("place", (0, 1, 0))]
            assert controller.places_reserved == 1
    finally:
        memory.close()


def test_cancelled_terrain_plan_cannot_reuse_reserved_material_budget(tmp_path):
    from mclab.navigation_segmented import SegmentedController
    from mclab.terrain_steps import TerrainPolicy

    cells = floor_map([(x, z) for x in range(-2, 3) for z in range(-2, 3)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    try:
        controller = SegmentedController(
            (0, 2, 0),
            memory,
            radius=1,
            search_nodes=1000,
            search_ms=100,
            terrain_policy=TerrainPolicy(allow_break=False, place_budget=1),
        )
        obs = {
            "sensor_profile": "local-visibility-v1",
            "tick": 1,
            "visible_blocks": list(cells.values()),
            "body": {
                "position": [0.5, 1, 0.5],
                "yaw": 0,
                "pitch": 0,
                "inventory": [
                    {"slot": 3, "item": "minecraft:cobblestone", "count": 64}
                ],
            },
        }
        controller.decide(obs)
        assert controller.mode != "arrived"
        assert controller.terrain is not None
        assert controller.status()["place_budget_reserved"] == 1
        controller.cancel()
        controller.resume()
        controller.decide(obs)
        assert controller.terrain is None
        assert controller.mode == "terrain_incomplete"
        assert controller.status()["place_budget_reserved"] == 1
    finally:
        memory.close()


def test_search_snapshot_immutable_and_motor_rejects_stale_recall(tmp_path):
    from mclab.navigation_segmented import SegmentedController

    cells = floor_map([(0, 0), (1, 0), (2, 0), (3, 0)])
    search = RouteSearch(cells, (0, 1, 0), (3, 1, 0), map_revision=1)
    cells.clear()
    assert search.advance(budget_ms=100).status == "reached"
    archive = MapArchive(tmp_path / "map.db", identity())
    history = list(
        floor_map([(x, z) for x in range(-1, 5) for z in (-1, 0, 1)]).values()
    )
    old = SpatialMemory(archive, "old")
    old.ingest(
        {"sensor_profile": "local-visibility-v1", "tick": 1, "visible_blocks": history}
    )
    old.close()
    memory = SpatialMemory(archive, "new")
    try:
        memory.recall((0, 1, 0), 10, 10)
        controller = SegmentedController((3, 1, 0), memory, radius=0)
        obs = {
            "sensor_profile": "local-visibility-v1",
            "tick": 1,
            "visible_blocks": [],
            "body": {"position": [0.5, 1, 0.5], "yaw": -90, "pitch": 0},
        }
        assert not controller.decide(obs).get("forward")
        assert controller.mode == "awaiting_fresh_geometry"
        obs["visible_blocks"] = history
        assert controller.decide(obs).get("forward", 0) > 0
        controller.cancel()
        assert controller.decide(obs) == {}
        assert controller.mode == "cancelled"
        controller.resume()
        assert controller.decide(obs).get("forward", 0) > 0
        controller.replace_goal((0, 1, 0))
        assert controller.decide(obs) == {}
        assert controller.mode == "arrived"
    finally:
        memory.close()
