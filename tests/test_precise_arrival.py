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
