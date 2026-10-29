from types import SimpleNamespace

import pytest

from mclab import control_loop, native_driver
from mclab.execution import ControllerFailure
from mclab.movement_policy import MovementPolicy
from mclab.terrain_steps import StepPlan


def test_ordinary_route_stops_before_gap():
    route = [(0, 1, 0), (1, 1, 0), (4, 1, 0), (5, 1, 0)]
    assert native_driver.ordinary_route(route) == route[:2]
    assert len(native_driver.ordinary_route([(x, 1, 0) for x in range(50)])) == 32


def test_native_batch_preserves_terrain_approach_before_walking_dead_end():
    route = [(x, 1, 0) for x in range(10)]
    controller = SimpleNamespace(
        route=route,
        terrain_policy=object(),
        last_result=SimpleNamespace(status="unreachable"),
        _terrain_approach_nodes=lambda: 4,
    )
    offered = native_driver.offered_route(controller)
    assert offered == route[:7]
    assert len(route[route.index(offered[-1]) :]) == 4
    controller.last_result.status = "segment"
    assert native_driver.offered_route(controller) == route


def test_native_edit_confirmation_requires_geometry_and_pickup_adjusted_debit():
    plan = StepPlan((0, 1, 0), (1, 1, 0), places=((1, 0, 0),))
    item = "minecraft:cobblestone"
    before = {"inventory": [{"item": item, "count": 64}], "building_pickups": {item: 0}}
    after = {
        "body": {
            "inventory": [{"item": item, "count": 64}],
            "building_pickups": {item: 0},
        }
    }
    fresh = {(1, 0, 0): {"block": item, "collision_boxes": [[0, 0, 0, 1, 1, 1]]}}
    report = {
        "placed_positions": [{"position": [1, 0, 0], "item": item}],
        "broken_positions": [],
    }
    assert not native_driver.confirm_step(plan, before, after, fresh, report)
    after["body"]["building_pickups"][item] = 1
    assert native_driver.confirm_step(plan, before, after, fresh, report)
    assert not native_driver.confirm_step(plan, before, after, {}, report)
    fresh[(1, 0, 0)]["block"] = "minecraft:dirt"
    with pytest.raises(ControllerFailure, match="material mismatch"):
        native_driver.confirm_step(plan, before, after, fresh, report)
    with pytest.raises(ControllerFailure, match="outside its plan"):
        native_driver.confirm_step(
            plan, before, after, {}, {**report, "broken_positions": [[9, 9, 9]]}
        )


@pytest.mark.parametrize("finish", ["done", "needs_replan"])
def test_native_driver_hands_off_routes_without_python_tick_controls(
    monkeypatch, finish
):
    rows = iter(
        [
            {"tick": 1, "route_follower": {"state": "idle"}},
            {
                "tick": 5,
                "route_follower": {"route_id": "native-1", "state": "following"},
            },
            {
                "tick": 10,
                "route_follower": {
                    "route_id": "native-1",
                    "state": finish,
                    "reason": "route_no_progress" if finish == "needs_replan" else "",
                    "next_index": 1,
                },
            },
            {"terminal": {"status": "success"}},
        ]
    )
    sent, records, admissions = [], [], []
    client = SimpleNamespace(
        send=lambda op, **kw: sent.append((op, kw)) or {"sequence": 1},
        acknowledge=lambda *_: None,
        wait=lambda predicate, **kw: predicate(
            {"route_follower": {"route_id": "native-1"}}
        ),
        record=lambda *args: records.append(args),
    )
    controller = SimpleNamespace(
        route=[],
        mode="following",
        terrain=None,
        gap_jump=None,
        failed_edges=set(),
        movement_policy=MovementPolicy(),
        committed_step=None,
        _admit=admissions.append,
        _discard_prefetch=lambda: None,
        drain_events=lambda: [],
    )
    decisions = []

    def decide(observation, *, native=False):
        assert native
        controller.route = [(0, 1, 0), (1, 1, 0)] if not decisions else []
        decisions.append(observation)

    controller.decide = decide

    def policy(_client):
        row = next(rows)
        return {"body": {"position": [0.5, 1, 0.5]}, **row}

    monkeypatch.setattr(native_driver, "policy_state", policy)
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))
    assert native_driver.drive_native(client, controller, 10) == {"status": "success"}
    assert [op for op, _ in sent] == ["follow_route", "cancel_route"]
    assert len(admissions) == 2
    assert client.control_loop_summary["route_replans"] == int(finish == "needs_replan")
    assert bool(controller.failed_edges) == (finish == "needs_replan")
    assert any(kind == "native_route_finished" for kind, _ in records)


def test_ambiguous_native_submission_is_cancelled_without_retry(monkeypatch):
    sent = []
    controller = SimpleNamespace(
        route=[(0, 1, 0), (1, 1, 0)],
        mode="following",
        terrain=None,
        gap_jump=None,
        committed_step=None,
        movement_policy=MovementPolicy(),
        decide=lambda _, **kw: {},
        drain_events=lambda: [],
    )

    def send(op, **_fields):
        sent.append(op)
        if op == "follow_route":
            raise OSError("response lost after admission")
        return {"sequence": 2}

    client = SimpleNamespace(
        send=send, acknowledge=lambda *_: None, record=lambda *_: None
    )
    monkeypatch.setattr(
        native_driver, "policy_state", lambda _: {"body": {"position": [0.5, 1, 0.5]}}
    )
    monkeypatch.setattr(control_loop, "_running", lambda *_: None)
    with pytest.raises(OSError, match="response lost"):
        native_driver.drive_native(client, controller, 10)
    assert sent == ["follow_route", "cancel_route"]


def test_planned_path_is_published_only_when_markers_are_on():
    from types import SimpleNamespace

    from mclab.native_driver import publish_path

    sent = []
    client = SimpleNamespace(send=lambda op, **kw: sent.append((op, kw)))
    publish_path(client, [(0, 1, 0), (1, 1, 0)])
    assert sent == []  # markers off by default
    client.show_path = True
    publish_path(client, [(0, 1, 0)])
    assert sent == []  # nothing to draw
    publish_path(client, [(0, 1, 0), (1, 1.5, 0)])
    assert sent == [("natural_path", {"points": [[0, 1, 0], [1, 1.5, 0]]})]


def test_splice_joins_prefetch_onto_unfinished_route_only_when_safe_to_hand_over():
    from types import SimpleNamespace

    from mclab.native_driver import splice_route
    from mclab.route_search import RouteResult

    route = [(0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 1, 0)]

    def controller(status="segment", tail=((3, 1, 0), (4, 1, 0), (5, 1, 0)), step=None):
        prefetch = SimpleNamespace(
            start=(3, 1, 0),
            advance=lambda **_: RouteResult(status, tail, 1, 9, "test"),
        )
        return SimpleNamespace(
            prefetch=prefetch,
            committed_step=step,
            terrain=None,
            terrain_queue=[],
            search=None,
            search_nodes=64,
            search_ms=2,
            prefetch_adopted=0,
            last_result=None,
            route=[],
            events=[],
            goal_revision=0,
        )

    following = {"next_index": 2}
    c = controller()
    joined = splice_route(c, route, following, [1.4, 1, 0.5])
    # Starts at the motor's current source, so the body passes the start check.
    assert joined == [(1, 1, 0), (2, 1, 0), (3, 1, 0), (4, 1, 0), (5, 1, 0)]
    assert c.prefetch is None and c.prefetch_adopted == 1 and c.route == joined
    # Dead ends, unfinished searches, terrain work and gaps keep the ordinary path.
    for blocked in (
        controller(status="unreachable"),
        controller(status="searching"),
        controller(step=(3, 1, 0)),
        controller(tail=((3, 1, 0), (5, 1, 0))),
    ):
        assert splice_route(blocked, route, following, [1.4, 1, 0.5]) is None
        assert blocked.prefetch is not None
    # Too far past the source for the motor's displacement check: retry later.
    assert splice_route(controller(), route, following, [2.9, 1, 0.5]) is None
