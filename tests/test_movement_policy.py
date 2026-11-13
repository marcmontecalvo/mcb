import pytest
from maps import floor_map
from test_spatial_memory import identity

from mclab.movement_policy import MovementPolicy
from mclab.navigation_segmented import SegmentedController
from mclab.spatial_memory import MapArchive, SpatialMemory


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
