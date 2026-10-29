import threading
import time

import pytest
from test_route_search import floor_map
from test_spatial_memory import identity

from mclab.navigation_segmented import SegmentedController
from mclab.planning_worker import PlanningWorker
from mclab.route_search import RouteSearch
from mclab.spatial_memory import MapArchive, SpatialMemory


def test_incomplete_search_retries_after_new_map_snapshot(tmp_path):
    from types import SimpleNamespace

    from mclab.route_search import RouteResult

    cells = floor_map([(x, z) for x in range(-1, 8) for z in (-1, 0, 1)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((6, 1, 0), memory, radius=0)
    try:
        memory.ingest(observation(cells))
        revision = memory.revision
        exhausted = SimpleNamespace(
            start=(0, 1, 0),
            revision=revision,
            advance=lambda **_: RouteResult(
                "incomplete", ((0, 1, 0),), revision, 128, "background search budget"
            ),
        )
        controller.search = exhausted
        # Keep the same snapshot for both polls: do not spin up duplicate jobs.
        controller._admit = lambda _: None
        assert controller.decide(observation(cells)) == {}
        assert controller.decide(observation(cells)) == {}
        assert controller.search is exhausted
        # A fresh map snapshot must allow planning from this same standing cell.
        memory.ingest(observation(cells, tick=6))
        assert memory.revision != revision
        wait_until(lambda: controller.decide(observation(cells, tick=6)).get("forward"))
        assert controller.search is not exhausted
    finally:
        controller.close()
        memory.close()


def observation(cells, *, x=0.5, tick=1):
    return {
        "sensor_profile": "local-visibility-v1",
        "tick": tick,
        "awareness": {"coverage": {"sample_tick": tick}},
        "visible_blocks": list(cells.values()),
        "body": {"position": [x, 1, 0.5], "yaw": -90, "pitch": 0},
    }


@pytest.mark.parametrize("native", [False, True])
def test_native_handoff_keeps_route_when_python_motor_would_stop(tmp_path, native):
    cells = floor_map([(x, z) for x in range(-1, 8) for z in (-1, 0, 1)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((6, 1, 0), memory, radius=0)
    try:
        # Reproduce the native run's loop: a known route exists, but the Python
        # motor returns no forward input and clears it before native handoff.
        controller._follow_route = lambda _: {}
        assert controller.decide(observation(cells), native=native) == {}
        if native:
            assert controller.mode == "following"
            assert len(controller.route) > 1
        else:
            assert controller.mode == "awaiting_fresh_geometry"
            assert not controller.route
    finally:
        controller.close()
        memory.close()


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.002)
    raise AssertionError("Background navigation did not reach expected state")


def test_prefetch_is_created_while_moving_and_adopted_at_segment_boundary(tmp_path):
    cells = floor_map([(x, z) for x in range(-1, 43) for z in (-1, 0, 1)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (40, 1, 0), memory, radius=0, background_planning=True
    )
    try:
        obs = observation(cells)
        command = wait_until(lambda: controller.decide(obs).get("forward", 0))
        assert command > 0
        assert controller.prefetch is not None
        assert controller.prefetch.start == (15, 1, 0)
        wait_until(lambda: controller.prefetch.advance().status != "searching")
        obs = observation(cells, x=15.5, tick=6)
        assert controller.decide(obs).get("forward", 0) > 0
        events = controller.drain_events()
        assert any(kind == "route_prefetch_started" for kind, _ in events)
        assert any(
            kind == "route_prefetch_adopted" and e["start"] == (15, 1, 0)
            for kind, e in events
        )
        assert controller.route[0] == (15, 1, 0)
    finally:
        controller.close()
        memory.close()
    assert not controller.planner_lane.thread.is_alive()
    assert not controller.observation_lane.thread.is_alive()
    with pytest.raises(RuntimeError, match="closed"):
        controller.resume()


def test_blocked_worker_does_not_block_controls_or_leak_a_cancelled_goal(
    tmp_path, monkeypatch
):
    from mclab import navigation_segmented

    entered, release = threading.Event(), threading.Event()

    class GatedSearch(RouteSearch):
        def advance(self, **kwargs):
            entered.set()
            assert release.wait(3)
            return super().advance(**kwargs)

    class GatedWorker(PlanningWorker):
        def submit(self, *args, **kwargs):
            kwargs["search_factory"] = GatedSearch
            return super().submit(*args, **kwargs)

    monkeypatch.setattr(navigation_segmented, "PlanningWorker", GatedWorker)
    cells = floor_map([(x, z) for x in range(-1, 8) for z in (-1, 0, 1)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (6, 1, 0), memory, radius=0, background_planning=True
    )
    try:
        obs = observation(cells)
        assert not controller.decide(obs).get("forward")
        assert entered.wait(3)
        # Calling decide again must return while the worker is still blocked.
        assert not controller.decide(obs).get("forward")
        controller.cancel()
        assert controller.decide(obs) == {}
        controller.replace_goal((0, 1, 0))
        release.set()
        assert controller.decide(obs) == {}
        assert controller.mode == "arrived"
        assert not controller.route
    finally:
        release.set()
        controller.close()
        memory.close()


@pytest.mark.parametrize(
    "route,expected",
    [
        ([(0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 1, 0), (4, 1, 0)], 3),
        ([(0, 1, 0), (1, 1, 0), (2, 1, 0), (2, 1, 1)], 2),
        ([(0, 1, 0), (1, 1, 0), (2, 2, 0)], 1),
    ],
)
def test_straight_lookahead_stops_at_turn_or_height_change(route, expected):
    controller = object.__new__(SegmentedController)
    controller.route = route
    targets = []

    def move(observation, target):
        targets.append(target)
        return {"forward": 1}

    controller._move = move
    assert controller._follow_route({}) == {"forward": 1}
    assert targets == [[expected + 0.5, 1, 0.5]]


def test_straight_lookahead_falls_back_when_farther_move_fails_fresh_safety():
    controller = object.__new__(SegmentedController)
    controller.route = [(0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 1, 0)]
    targets = []

    def move(observation, target):
        targets.append(target)
        return {"forward": int(target[0] == 1.5)}

    controller._move = move
    assert controller._follow_route({}) == {"forward": 1}
    assert [p[0] for p in targets] == [3.5, 2.5, 1.5]


def test_unsafe_lookahead_cannot_turn_body_away_from_safe_near_waypoint(tmp_path):
    from mclab.movement_policy import MovementPolicy

    cells = floor_map([(x, z) for x in range(-1, 3) for z in range(-1, 5)])
    for z in range(1, 5):
        cells[1, 0, z].update(block="minecraft:air", collision_boxes=[])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController(
        (0, 1, 4), memory, movement_policy=MovementPolicy(allow_shapes=True)
    )
    controller.route = [(0, 1, z) for z in range(5)]
    obs = observation(cells)
    obs["body"].update(position=[0.80150609438, 1, 0.28864596651], yaw=5.363637)
    try:
        memory.ingest(obs)
        # Aim safely toward the next cell, rather than alternating between its
        # heading and an unsafe shortcut farther along the same straight route.
        turn = controller._follow_route(obs)
        assert turn["yaw_delta"] > 8
        obs["body"]["yaw"] += turn["yaw_delta"]
        command = controller._follow_route(obs)
        assert command.get("forward", 0) > 0
        assert abs(command["yaw_delta"]) <= 8
        # The rejected shortcut remains unsafe; fixing steering must not admit it.
        aligned_far = {**obs, "body": {**obs["body"], "yaw": 5.363637}}
        assert not controller._move(aligned_far, [0.5, 1, 3.5]).get("forward")
    finally:
        controller.close()
        memory.close()


def test_blocked_map_admission_keeps_controls_responsive_and_uses_fresh_safety(
    tmp_path,
):
    cells = floor_map([(x, z) for x in range(-1, 8) for z in (-1, 0, 1)])
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    entered, release = threading.Event(), threading.Event()
    original_ingest = memory.ingest

    def gated_ingest(obs, **kwargs):
        entered.set()
        assert release.wait(3)
        original_ingest(obs, **kwargs)

    memory.ingest = gated_ingest
    controller = SegmentedController((6, 1, 0), memory, background_planning=True)
    try:
        obs = observation(cells)
        assert not controller.decide(obs).get("forward")
        assert entered.wait(3)
        # This returns even though admission is still blocked.
        assert not controller.decide(obs).get("forward")
        release.set()
        wait_until(lambda: controller.decide(obs).get("forward"))
        # A historical route never authorizes walking onto newly observed air.
        missing = {
            p: {**c, "block": "minecraft:air", "collision_boxes": []}
            for p, c in cells.items()
        }
        assert not controller.decide(observation(missing, tick=6)).get("forward")
    finally:
        release.set()
        controller.close()
        memory.close()


def test_ledge_edge_stance_uses_the_supporting_cell(tmp_path):
    # Floor at x 0..6; x = -1 is a hole. The body center is over the hole
    # (x = -0.05) but its hitbox still rests on x = 0, like run
    # 20261029T122717114577Z R4/R5, which froze for ~4,900 ticks.
    cells = floor_map([(x, z) for x in range(0, 7) for z in (-1, 0, 1)])
    for y in range(5):
        cells[-1, y, 0] = {
            "position": [-1, y, 0],
            "block": "minecraft:air",
            "collision_boxes": [],
        }
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    controller = SegmentedController((6, 1, 0), memory, radius=0)
    try:
        memory.ingest(observation(cells, x=-0.05))
        assert controller._stance_node([-0.05, 1, 0.5], 1) == (0, 1, 0)
        # A body fully over a hole keeps its own (unsupported) cell.
        assert controller._stance_node([-0.5, 1, 0.5], 1) == (-1, 1, 0)
        # The ordinary case is unchanged.
        assert controller._stance_node([2.5, 1, 0.5], 1) == (2, 1, 0)
    finally:
        controller.close()
        memory.close()


def test_wall_cut_dead_end_search_resumes_until_unreachable():
    # Natural run 20261029T190159944470Z R5: a dead-end pocket needed more than
    # one 250 ms wall budget. Each cut search restarted from zero and reported
    # "incomplete" forever, so the terrain fallback (which needs "unreachable")
    # never ran. A slow search with a tiny wall budget reproduces that here.
    from mclab.planning_worker import BackgroundSearch

    class SlowSearch(RouteSearch):
        def advance(self, **kwargs):
            time.sleep(0.01)
            return super().advance(**{**kwargs, "node_limit": 1})

    class TinyWallWorker(PlanningWorker):
        def submit(self, *args, **kwargs):
            return super().submit(*args, **{**kwargs, "wall_budget_ms": 5})

    pocket = floor_map([(x, z) for x in range(3) for z in range(3)])
    worker = TinyWallWorker(search_factory=SlowSearch)
    try:
        search = BackgroundSearch(
            worker, pocket, (2, 1, 2), (40, 1, 40), map_revision=1, goal_revision=1
        )
        result = wait_until(
            lambda: (r := search.advance()).status not in ("searching",) and r
        )
        assert result.status == "unreachable"
        assert result.expanded == 9
    finally:
        worker.close()
