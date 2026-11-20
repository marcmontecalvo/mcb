from maps import floor_map, observation
from test_spatial_memory import identity

from mclab.navigation_segmented import SegmentedController
from mclab.spatial_memory import MapArchive, SpatialMemory


def test_off_center_goal_in_same_cell_requests_centering_before_arrival(tmp_path):
    cells = floor_map([(x, z) for x in range(-1, 3) for z in (-1, 0, 1)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((0, 1, 0), memory, radius=0.1)
    try:
        assert controller.decide(observation(cells, x=0.95), native=True) == {}
        assert controller.mode == "centering"
        assert controller.route == [(0, 1, 0), (0, 1, 0)]
        assert controller.center_radius == 0.1
        assert controller.decide(observation(cells, x=0.57, tick=2), native=True) == {}
        assert controller.mode == "arrived"
    finally:
        controller.close()
        memory.close()


def test_interaction_checks_actual_eye_position_before_arrival(tmp_path):
    cells = floor_map([(x, z) for x in range(-1, 6) for z in (-1, 0, 1)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((4, 1, 0), memory, radius=0, interact_reach=4.3)
    try:
        assert controller.decide(observation(cells, x=0.01), native=True) == {}
        assert controller.mode == "centering" and controller.center_radius == 0.05
        assert controller.decide(observation(cells, x=0.5, tick=2), native=True) == {}
        assert controller.mode == "arrived"
    finally:
        controller.close()
        memory.close()


def _planner(controller, status, start):
    from mclab.route_types import RouteResult

    class Search:
        def __init__(self):
            self.start = start

        def advance(self, **_):
            return RouteResult(status, (start,), 0, 0, "stub")

    controller._new_search = lambda *_a, **_k: Search()


def _half_low(cells, *, x, tick=1):
    """Feet half a block under the stance (a stair's low step under its top)."""
    obs = observation(cells, x=x, tick=tick)
    obs["body"]["position"][1] = 0.5
    return obs


def test_a_stance_the_feet_are_not_level_with_is_not_centred_on(tmp_path):
    # House-large run 20261119T133618892664Z: feet on a stair's low step (70.5),
    # stance planned from the stair top (71.0). RouteMotor refuses a centre-only
    # route 0.2 off the feet, and the same centring was sent 64 times in 16.8 s.
    cells = floor_map([(x, z) for x in range(-1, 6) for z in (-1, 0, 1)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((4, 1, 0), memory, radius=0, interact_reach=4.3)
    controller._stance_node = lambda p, height: (0, 1, 0)
    _planner(controller, "unreachable", (0, 1, 0))
    try:
        controller.decide(_half_low(cells, x=0.01), native=True)
        assert controller.mode != "centering"
        assert (0, 1, 0) in controller.avoid_goal_stances  # interact from elsewhere
    finally:
        controller.close()
        memory.close()


def test_a_position_goal_is_not_centred_on_when_the_motor_refused_it(tmp_path):
    cells = floor_map([(x, z) for x in range(-1, 3) for z in (-1, 0, 1)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((0, 1, 0), memory, radius=0.1)
    _planner(controller, "reached", (0, 1, 0))
    try:
        controller.uncentered.add((0, 1, 0))  # native_driver: route_center_not_grounded
        controller.decide(observation(cells, x=0.95), native=True)
        assert controller.mode == "arrived"
    finally:
        controller.close()
        memory.close()


def test_a_dry_terrain_verdict_holds_until_the_way_to_the_goal_changes(tmp_path):
    # House-large run 20261119T133618892664Z: an unreachable goal was searched
    # again (walking, then terrain) on every map revision, 45 s in terrain_planning.
    air = {"block": "minecraft:air", "collision_boxes": []}
    stone = {"block": "minecraft:stone", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    cells = {(x, y, z): air for x in range(-20, 40) for y in range(0, 5) for z in (0,)}
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((10, 1, 0), memory, radius=0)
    snapshot = {"value": (3, cells)}
    controller._planning_snapshot = lambda: snapshot["value"]
    controller._revision_now = lambda: snapshot["value"][0]
    try:
        controller._mark_terrain_dry((0, 1, 0))
        assert controller._terrain_dry_holds((0, 1, 0))
        assert not controller._terrain_dry_holds((1, 1, 0))  # another start
        # Re-observed, and a block placed far behind the body: same verdict.
        snapshot["value"] = (4, {**cells, (-20, 1, 0): stone})
        assert controller._terrain_dry_holds((0, 1, 0))
        # A block placed between the body and the goal: search again.
        snapshot["value"] = (5, {**cells, (-20, 1, 0): stone, (5, 1, 0): stone})
        assert not controller._terrain_dry_holds((0, 1, 0))
    finally:
        controller.close()
        memory.close()


def test_planner_reached_with_a_failing_arrival_check_ends_the_goto_not_idles(tmp_path):
    # iron-mine run 20261116T145041448719Z: the body planner said "reached" but the
    # controller's snapshot ray check failed (a just-broken cell still solid), so it
    # sat in mode "reached" with no route and no search until the StuckGuard fired.
    from mclab.route_types import RouteResult

    cells = floor_map([(x, z) for x in range(-1, 4) for z in (-1, 0, 1)])
    cells[1, 2, 0] = {**cells[1, 2, 0], "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((2, 1, 0), memory, radius=0, interact_reach=4.5)

    class Search:
        start = (0, 1, 0)

        def advance(self, **_):
            return RouteResult("reached", ((0, 1, 0),), 0, 0, "body planner reached")

    try:
        controller.search = Search()
        assert controller.decide(observation(cells, x=0.5), native=True) == {}
        assert controller.mode == "arrived"
    finally:
        controller.close()
        memory.close()


def test_a_body_on_a_pond_rim_plans_from_the_rim_not_the_water_surface(tmp_path):
    # run 20261117T040459442702Z: the body stood on a pond's dirt rim with its
    # centre over the water. The swim fallback took the water surface (a block
    # lower) before the rim's support was checked, so every plan started inside
    # the rim block and the motor refused each edge for 100 s.
    from mclab.movement_policy import MovementPolicy
    from mclab.route_types import RouteResult

    cells = floor_map([(x, z) for x in range(-1, 4) for z in (-1, 0)])
    for x in range(-1, 4):
        for y in (-2, -1):
            water = {"block": "minecraft:water", "fluid": "minecraft:water"}
            cells[x, y, 1] = {
                "position": [x, y, 1],
                **water,
                "properties": {"level": "0"},
                "collision_boxes": [],
            }
        cells[x, -3, 1] = {
            "position": [x, -3, 1],
            "block": "minecraft:stone",
            "collision_boxes": [[0, 0, 0, 1, 1, 1]],
        }
        for y in range(0, 5):
            cells[x, y, 1] = {
                "position": [x, y, 1],
                "block": "minecraft:air",
                "collision_boxes": [],
            }
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (1, 1, 0), memory, radius=0, movement_policy=MovementPolicy(allow_swim=True)
    )

    class Search:
        start = (1, 1, 0)

        def advance(self, **_):
            return RouteResult("reached", ((1, 1, 0),), 0, 0, "body planner reached")

    try:
        controller.search = Search()
        seen = observation(cells, x=1.5)
        seen["body"]["position"] = [1.5, 1, 1.05]  # hitbox 0.75..1.35 rests on z=0
        controller.decide(seen, native=True)
        assert controller.search is not None and controller.search.start == (1, 1, 0)
    finally:
        controller.close()
        memory.close()


def test_a_body_on_a_stair_edge_plans_from_the_step_half_a_block_below(tmp_path):
    # run 20261117T071227173616Z: the body's feet were on a stair's low step
    # (y+0.5) with its centre over the next column, whose own stance is the lower
    # stair's high half, half a block down. Planning from the centre column at
    # y+0.5 was "unreachable, 0 expanded" for 4 minutes; from y it took 23 nodes.
    from mclab.movement_policy import MovementPolicy
    from mclab.route_types import RouteResult

    cells = floor_map([(x, z) for x in range(-1, 4) for z in range(-1, 3)])
    stair = {
        "block": "minecraft:birch_stairs",
        "properties": {"facing": "north", "half": "bottom", "shape": "straight"},
        "collision_boxes": [[0, 0, 0, 1, 0.5, 1], [0, 0.5, 0, 1, 1, 0.5]],
    }
    cells[1, 0, 1] = {"position": [1, 0, 1], **stair}  # stance (1,1,1)
    cells[1, 1, 0] = {"position": [1, 1, 0], **stair}  # low step top at 1.5
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (1, 1, 3), memory, radius=0, movement_policy=MovementPolicy(allow_shapes=True)
    )

    class Search:
        start = (1, 1, 1)

        def advance(self, **_):
            return RouteResult("reached", ((1, 1, 1),), 0, 0, "body planner reached")

    try:
        controller.search = Search()
        seen = observation(cells, x=1.5)
        seen["body"]["position"] = [1.5, 1.5, 1.2]  # hitbox 0.9..1.5 on the step
        controller.decide(seen, native=True)
        assert controller.search is not None and controller.search.start == (1, 1, 1)
    finally:
        controller.close()
        memory.close()


def test_a_body_on_a_stair_sliver_over_a_drop_plans_from_the_stair_top(tmp_path):
    # run 20261118T060652726798Z: the hitbox rested 0.12 on a roof stair's low step,
    # centre over open air; the stair's high half fills its own column, so no
    # stance was found and every plan from the air was "unreachable, 0 expanded".
    from mclab.movement_policy import MovementPolicy
    from mclab.route_types import RouteResult

    cells = floor_map([(x, z) for x in range(-1, 4) for z in range(-1, 3)])
    for x in range(-1, 4):
        cells[x, 0, 0] = {"position": [x, 0, 0], "block": "minecraft:air"}
        cells[x, -1, 0] = {"position": [x, -1, 0], "block": "minecraft:air"}
    stair = {
        "block": "minecraft:oak_stairs",
        "properties": {"facing": "south", "half": "bottom", "shape": "straight"},
        "collision_boxes": [[0, 0, 0, 1, 0.5, 1], [0, 0.5, 0.5, 1, 1, 1]],
    }
    cells[1, 1, 1] = {"position": [1, 1, 1], **stair}  # on the floor; top at 2
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (1, 2, 2), memory, radius=0, movement_policy=MovementPolicy(allow_shapes=True)
    )

    class Search:
        start = (1, 2, 1)

        def advance(self, **_):
            return RouteResult("reached", ((1, 2, 1),), 0, 0, "body planner reached")

    try:
        controller.search = Search()
        seen = observation(cells, x=1.5)
        seen["body"]["position"] = [1.5, 1.5, 0.82]  # hitbox to z 1.12, the low step
        controller.decide(seen, native=True)
        assert controller.search is not None and controller.search.start == (1, 2, 1)
    finally:
        controller.close()
        memory.close()


def test_a_body_on_a_lily_pad_plans_from_the_pads_own_height(tmp_path):
    # run 20261117T221236234752Z: feet at y+0.094 on a lily pad were rounded to
    # the block, which had no floor, and every plan was refused for a minute.
    from mclab.movement_policy import MovementPolicy
    from mclab.route_types import RouteResult

    cells = floor_map([(x, z) for x in range(-1, 4) for z in range(-1, 3)])
    cells[1, 1, 1] = {
        "position": [1, 1, 1],
        "block": "minecraft:lily_pad",
        "collision_boxes": [[0, 0, 0, 1, 0.09375, 1]],
    }
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (1, 1, 3), memory, radius=0, movement_policy=MovementPolicy(allow_shapes=True)
    )

    class Search:
        start = (1, 1.09375, 1)

        def advance(self, **_):
            return RouteResult("reached", ((1, 1.09375, 1),), 0, 0, "reached")

    try:
        controller.search = Search()
        seen = observation(cells, x=1.5)
        seen["body"]["position"] = [1.5, 1.09375, 1.5]
        controller.decide(seen, native=True)
        assert controller.search is not None
        assert controller.search.start == (1, 1.09375, 1)
    finally:
        controller.close()
        memory.close()


def test_a_body_inside_a_scaffolding_bridge_cell_plans_from_that_cell(tmp_path):
    # run 20261118T080749937951Z: feet at y+0.125 on the bottom plate of a
    # scaffolding bridge cell over a pond; the water surface a block lower was
    # taken as the start and every plan was unreachable until the run stopped.
    from mclab.movement_policy import MovementPolicy
    from mclab.route_types import RouteResult

    cells = floor_map([(x, z) for x in range(-1, 4) for z in range(-1, 3)])
    cells[1, 0, 1] = {
        "position": [1, 0, 1],
        "block": "minecraft:water",
        "fluid": "minecraft:water",
        "collision_boxes": [],
    }
    plate = [[0, 0.875, 0, 1, 1, 1], [0, 0, 0, 0.125, 1, 0.125]]
    cells[1, 2, 1] = {
        "position": [1, 2, 1],
        "block": "minecraft:scaffolding",
        "properties": {"bottom": "true", "distance": "2"},
        "collision_boxes": plate,
    }
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    policy = MovementPolicy(allow_shapes=True, allow_swim=True)
    controller = SegmentedController(
        (1, 1, 3), memory, radius=0, movement_policy=policy
    )

    class Search:
        start = (1, 2.0, 1)

        def advance(self, **_):
            return RouteResult("reached", ((1, 2.0, 1),), 0, 0, "reached")

    try:
        controller.search = Search()
        seen = observation(cells, x=1.5)
        seen["body"]["position"] = [1.5, 2.125, 1.5]
        controller.decide(seen, native=True)
        assert controller.search is not None
        assert controller.search.start == (1, 2.0, 1)
    finally:
        controller.close()
        memory.close()
