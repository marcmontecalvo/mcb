import threading
import time

import pytest
from test_route_search import floor_map
from test_spatial_memory import identity

from mclab.navigation_segmented import SegmentedController
from mclab.planning_worker import PlanningWorker
from mclab.route_search import RouteSearch
from mclab.spatial_memory import MapArchive, SpatialMemory


def observation(cells, *, x=0.5, tick=1):
    return {
        "sensor_profile": "local-visibility-v1",
        "tick": tick,
        "awareness": {"coverage": {"sample_tick": tick}},
        "visible_blocks": list(cells.values()),
        "body": {"position": [x, 1, 0.5], "yaw": -90, "pitch": 0},
    }


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
