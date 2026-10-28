from dataclasses import replace

import pytest
from test_route_search import floor_map
from test_spatial_memory import identity

from mclab.movement_policy import MovementPolicy
from mclab.navigation_segmented import SegmentedController
from mclab.route_search import RouteSearch
from mclab.spatial_memory import MapArchive, SpatialMemory


def test_diagonals_require_both_swept_side_columns_and_permission():
    cells = floor_map([(0, 0), (1, 0), (0, 1), (1, 1)])

    def result(policy, excluded=()):
        return RouteSearch(
            cells,
            (0, 1, 0),
            (1, 1, 1),
            map_revision=1,
            movement_policy=policy,
            excluded=excluded,
        ).advance(budget_ms=100)

    enabled = MovementPolicy(allow_diagonals=True)
    assert len(result(MovementPolicy()).route) == 3
    assert len(result(enabled).route) == 2
    assert len(result(enabled, (((0, 1, 0), (1, 1, 1)),)).route) == 3
    del cells[1, 0, 0]
    assert len(result(enabled).route) == 3
    del cells[0, 1, 1]
    assert result(enabled).status == "unreachable"


def test_drops_require_known_clear_shaft_solid_landing_and_permission():
    cells = floor_map([(0, 0), (1, 0)])
    cells[0, 2, 0] = dict(cells[0, 0, 0], position=[0, 2, 0])
    policy = MovementPolicy(max_drop=3)

    def edges():
        return dict(
            RouteSearch(
                cells, (0, 3, 0), (1, 1, 0), map_revision=1, movement_policy=policy
            )._edges((0, 3, 0))
        )

    assert (1, 1, 0) in edges()
    policy = replace(policy, max_drop=1)
    assert (1, 1, 0) not in edges()
    policy = replace(policy, max_drop=3)
    del cells[1, 2, 0]
    assert (1, 1, 0) not in edges()


def test_sprint_requires_permission_food_and_fresh_swept_geometry(tmp_path):
    cells = floor_map([(x, z) for x in range(-1, 2) for z in range(-1, 6)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (0, 1, 5), memory, movement_policy=MovementPolicy(allow_sprint=True)
    )
    obs = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(cells.values()),
        "body": {
            "position": [0.5, 1, 0.5],
            "yaw": 0,
            "pitch": 0,
            "on_ground": True,
            "food": 20,
        },
    }
    try:
        memory.ingest(obs)
        assert controller._move(obs, [0.5, 1, 3.5])["sprint"]
        obs["body"]["food"] = 6
        assert not controller._move(obs, [0.5, 1, 3.5]).get("sprint")
        obs["body"]["food"] = 20
        controller.movement_policy = MovementPolicy()
        assert not controller._move(obs, [0.5, 1, 3.5]).get("sprint")
        controller.movement_policy = MovementPolicy(allow_sprint=True)
        obs["tick"] = 7
        assert not controller._move(obs, [0.5, 1, 3.5]).get("forward")
    finally:
        controller.close()
        memory.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"allow_sprint": 1},
        {"max_drop": True},
        {"max_drop": 4},
        {"allow_diagonals": "yes"},
    ],
)
def test_invalid_movement_policy(kwargs):
    with pytest.raises(ValueError):
        MovementPolicy(**kwargs)


@pytest.mark.parametrize("shapes", [False, True])
def test_disabled_drop_rejects_changed_pit_beneath_a_level_waypoint(tmp_path, shapes):
    cells = floor_map([(x, z) for x in range(-1, 2) for z in range(-1, 5)])
    cells[0, 0, 1].update(block="minecraft:air", collision_boxes=[])
    cells[0, -1, 1] = dict(cells[0, 0, 0], position=[0, -1, 1])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (0, 1, 3),
        memory,
        movement_policy=MovementPolicy(max_drop=0, allow_shapes=shapes),
    )
    obs = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(cells.values()),
        "body": {"position": [0.5, 1, 0.5], "yaw": 0, "pitch": 0, "on_ground": True},
    }
    try:
        memory.ingest(obs)
        assert not controller._move(obs, [0.5, 1, 3.5]).get("forward")
    finally:
        controller.close()
        memory.close()


@pytest.mark.parametrize("shapes", [False, True])
def test_zero_drop_policy_preserves_the_support_of_an_admitted_ascent(tmp_path, shapes):
    cells = floor_map([(x, z) for x in range(-1, 2) for z in range(-1, 4)])
    cells[0, 1, 1] = dict(cells[0, 0, 1], position=[0, 1, 1])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (0, 2, 1),
        memory,
        movement_policy=MovementPolicy(max_drop=0, allow_shapes=shapes),
    )
    controller.motion_origin_height = 1
    controller.air_waypoint = [0.5, 2, 1.5]
    obs = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(cells.values()),
        "body": {
            "position": [0.5, 2.17, 0.9],
            "yaw": 0,
            "pitch": 0,
            "on_ground": False,
        },
    }
    try:
        memory.ingest(obs)
        assert controller._move(obs, [0.5, 2, 1.5]).get("forward", 0) > 0
        controller.cancel()
        assert controller.motion_origin_height is None
    finally:
        controller.close()
        memory.close()


def test_disabled_drops_cannot_reappear_in_terrain_search_or_queued_walk(tmp_path):
    from mclab.terrain_route import TerrainRouteSearch
    from mclab.terrain_steps import StepPlan, TerrainPolicy

    cells = floor_map([(0, 0), (1, 0), (2, 0)])
    cells[0, 1, 0] = dict(cells[0, 0, 0], position=[0, 1, 0])
    policy = MovementPolicy(max_drop=0)
    search = TerrainRouteSearch(
        cells,
        (0, 2, 0),
        (2, 1, 0),
        map_revision=1,
        policy=TerrainPolicy(allow_break=False, allow_place=False),
        inventory=(),
        movement_policy=policy,
    )
    assert not list(search._children(search.layer[0]))
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((2, 1, 0), memory, movement_policy=policy)
    obs = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(cells.values()),
        "body": {"position": [0.5, 2, 0.5], "yaw": -90, "pitch": 0},
    }
    try:
        memory.ingest(obs)
        controller.terrain_queue = [StepPlan((0, 2, 0), (1, 1, 0))]
        controller.terrain_queue_policy = TerrainPolicy()
        controller._begin_queued_step((0, 2, 0), obs)
        assert controller.terrain is None
        assert not controller.terrain_queue
    finally:
        controller.close()
        memory.close()
