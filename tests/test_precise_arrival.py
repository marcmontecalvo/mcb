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
