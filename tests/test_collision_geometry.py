import pytest
from test_route_search import floor_map
from test_spatial_memory import identity

from mclab.collision_geometry import clear_body, clear_column, standable
from mclab.movement_policy import MovementPolicy
from mclab.navigation_segmented import SegmentedController
from mclab.route_search import RouteSearch
from mclab.spatial_memory import MapArchive, SpatialMemory


@pytest.mark.parametrize("shapes", [False, True])
@pytest.mark.parametrize("edge_x", [0.94, 1.295])
def test_descending_waypoint_survives_trailing_edge_ground_contact(
    tmp_path, shapes, edge_x
):
    cells = floor_map([(x, z) for x in range(-1, 4) for z in range(-1, 2)])
    for z in range(-1, 2):
        cells[0, 1, z] = dict(cells[0, 0, z], position=[0, 1, z])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (2, 1, 0), memory, radius=0, movement_policy=MovementPolicy(allow_shapes=shapes)
    )
    obs = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(cells.values()),
        "body": {"position": [0.5, 2, 0.5], "yaw": -90, "pitch": 0, "on_ground": True},
    }
    try:
        memory.ingest(obs)
        assert controller._move(obs, [1.5, 1, 0.5]).get("forward", 0) > 0
        assert controller.air_waypoint == [1.5, 1, 0.5]
        obs["body"]["position"][0] = edge_x
        assert controller.decide(obs).get("forward", 0) > 0
    finally:
        controller.close()
        memory.close()


def slab_map():
    cells = floor_map([(x, z) for x in range(-1, 4) for z in range(-1, 2)])
    for z in range(-1, 2):
        cells[1, 0, z].update(
            block="minecraft:stone_slab", collision_boxes=[[0, 0, 0, 1, 0.5, 1]]
        )
    return cells


def test_route_tracks_half_block_heights_and_rejects_missing_headroom():
    cells = slab_map()
    policy = MovementPolicy(allow_shapes=True)
    search = RouteSearch(
        cells, (0, 1, 0), (2, 1, 0), map_revision=1, movement_policy=policy
    )
    result = search.advance(budget_ms=100)
    assert result.status == "reached"
    assert result.route == ((0, 1, 0), (1, 0.5, 0), (2, 1, 0))
    assert standable(cells, (1, 0.5, 0))
    del cells[1, 2, 0]
    assert not standable(cells, (1, 0.5, 0))


def test_off_center_body_checks_partial_side_collision_and_unknown_neighbor():
    cells = floor_map([(x, z) for x in range(4) for z in range(2)])
    cells[1, 1, 0].update(
        block="minecraft:glass_pane", collision_boxes=[[0.9, 0, 0, 1, 1, 1]]
    )
    assert clear_column(cells, 1, 0, 1, 2.8)
    assert not clear_body(cells, 1.75, 0.5, 1, 2.8)
    cells[1, 1, 0].update(block="minecraft:air", collision_boxes=[])
    assert clear_body(cells, 1.75, 0.5, 1, 2.8)
    del cells[2, 2, 0]
    assert not clear_body(cells, 1.75, 0.5, 1, 2.8)


def test_stair_support_and_waterlogged_hazard_rejection():
    cells = slab_map()
    cells[1, 0, 0].update(
        block="minecraft:stone_stairs",
        collision_boxes=[[0, 0, 0, 1, 0.5, 1], [0, 0.5, 0.5, 1, 1, 1]],
    )
    assert standable(cells, (1, 1, 0))
    assert not standable(cells, (1, 0.5, 0))
    cells[1, 0, 0]["fluid"] = "minecraft:water"
    assert not standable(cells, (1, 1, 0))
    cells[1, 0, 0].pop("fluid")
    cells[1, 1, 0]["block"] = "minecraft:fire"
    assert not standable(cells, (1, 1, 0))


def test_landing_on_leading_slab_edge_finishes_the_existing_waypoint(tmp_path):
    cells = slab_map()
    for z in range(-1, 2):
        cells[0, 0, z].update(
            block="minecraft:stone_slab", collision_boxes=[[0, 0, 0, 1, 0.5, 1]]
        )
        cells[1, 0, z].update(
            block="minecraft:stone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
        )
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (2, 1, 0), memory, radius=0, movement_policy=MovementPolicy(allow_shapes=True)
    )
    controller.air_waypoint = [1.5, 1, 0.5]
    obs = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(cells.values()),
        "body": {"position": [0.94, 1, 0.5], "yaw": -90, "pitch": 0, "on_ground": True},
    }
    try:
        assert not standable(cells, (0, 1, 0))
        assert controller.decide(obs).get("forward", 0) > 0
        assert controller.air_waypoint == [1.5, 1, 0.5]
    finally:
        controller.close()
        memory.close()


def test_landing_on_lower_stair_half_keeps_the_upper_waypoint(tmp_path):
    cells = slab_map()
    cells[1, 0, 0].update(
        block="minecraft:stone_stairs",
        collision_boxes=[[0, 0, 0, 1, 0.5, 1], [0, 0.5, 0.5, 1, 1, 1]],
    )
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (2, 1, 0), memory, radius=0, movement_policy=MovementPolicy(allow_shapes=True)
    )
    controller.air_waypoint = [1.5, 1, 0.5]
    obs = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(cells.values()),
        "body": {"position": [1.5, 0.5, 0.1], "yaw": 0, "pitch": 0, "on_ground": True},
    }
    try:
        assert not standable(cells, (1, 0.5, 0))
        command = controller.decide(obs)
        assert command.get("forward", 0) > 0 and command["jump"]
        assert controller.air_waypoint == [1.5, 1, 0.5]
    finally:
        controller.close()
        memory.close()


def test_fresh_shape_motor_follows_slabs_but_rejects_changed_and_stale_geometry(
    tmp_path,
):
    cells = slab_map()
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (2, 1, 0), memory, radius=0, movement_policy=MovementPolicy(allow_shapes=True)
    )
    obs = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(cells.values()),
        "body": {"position": [0.5, 1, 0.5], "yaw": -90, "pitch": 0, "on_ground": True},
    }
    try:
        assert controller.decide(obs).get("forward", 0) > 0
        obs["tick"] = 7
        obs["visible_blocks"] = []
        assert not controller.decide(obs).get("forward")
        cells[1, 0, 0]["fluid"] = "minecraft:water"
        obs["tick"] = 8
        obs["visible_blocks"] = list(cells.values())
        controller.decide(obs)
        assert not controller._move(obs, [1.5, 0.5, 0.5]).get("forward")
    finally:
        controller.close()
        memory.close()
