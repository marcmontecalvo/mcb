import threading
import time

import pytest
from test_route_search import floor_map

from mclab.planning_worker import PlanningWorker
from mclab.route_search import RouteSearch


def await_result(worker, token):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = worker.poll(token)
        if result is not None and result.result.status != "searching":
            return result
        time.sleep(0.001)
    raise AssertionError("Planner did not publish a terminal result")


def test_background_search_keeps_immutable_revision_and_bounds_output():
    cells = floor_map([(x, 0) for x in range(40)])
    worker = PlanningWorker()
    try:
        token = worker.submit(
            cells,
            (0, 1, 0),
            (39, 1, 0),
            map_revision=12,
            goal_revision=4,
            segment_length=8,
        )
        cells.clear()
        result = await_result(worker, token)
        assert result.goal_revision == 4 and result.result.map_revision == 12
        assert result.result.status == "segment"
        assert result.result.route == tuple((x, 1, 0) for x in range(8))
        assert worker.status()["completed"] == 1
    finally:
        worker.close()
    assert not worker.thread.is_alive()


def test_obsolete_blocked_search_cannot_publish_after_goal_replacement():
    entered, release = threading.Event(), threading.Event()

    class GatedSearch(RouteSearch):
        def advance(self, **kwargs):
            entered.set()
            assert release.wait(3)
            return super().advance(**kwargs)

    cells = floor_map([(x, 0) for x in range(5)])
    worker = PlanningWorker(search_factory=GatedSearch)
    try:
        old = worker.submit(
            cells, (0, 1, 0), (4, 1, 0), map_revision=1, goal_revision=1
        )
        assert entered.wait(3)
        new = worker.submit(
            cells, (0, 1, 0), (1, 1, 0), map_revision=2, goal_revision=2
        )
        assert worker.poll(old) is None
        assert worker.poll(new) is None
        release.set()
        result = await_result(worker, new)
        assert result.token == new and result.goal_revision == 2
        assert result.result.route[-1] == (1, 1, 0)
        worker.cancel()
        assert worker.poll(new) is None
    finally:
        release.set()
        worker.close()


def test_node_exhaustion_is_incomplete_not_a_false_unreachable():
    cells = floor_map([(x, 0) for x in range(100)])
    worker = PlanningWorker()
    try:
        token = worker.submit(
            cells,
            (0, 1, 0),
            (99, 1, 0),
            map_revision=1,
            goal_revision=1,
            node_limit=2,
            max_expansions=3,
        )
        result = await_result(worker, token).result
        assert result.status == "incomplete"
        assert result.expanded == 3
        assert all(
            b[0] - a[0] == 1
            for a, b in zip(result.route, result.route[1:], strict=False)
        )
    finally:
        worker.close()


def test_worker_failures_propagate_to_the_motor():
    class BrokenSearch:
        expanded = 0

        def __init__(self, *args, **kwargs):
            pass

        def advance(self, **kwargs):
            raise OSError("deliberate worker failure")

    worker = PlanningWorker(search_factory=BrokenSearch)
    try:
        token = worker.submit({}, (0, 1, 0), (1, 1, 0), map_revision=1, goal_revision=1)
        with pytest.raises(RuntimeError, match="Background route search failed"):
            await_result(worker, token)
    finally:
        worker.close()


def test_budgets_rejected_before_worker_admission():
    worker = PlanningWorker()
    try:
        with pytest.raises(ValueError, match="budgets"):
            worker.submit(
                {},
                (0, 1, 0),
                (1, 1, 0),
                map_revision=1,
                goal_revision=1,
                node_limit=128,
                max_expansions=2,
            )
    finally:
        worker.close()
