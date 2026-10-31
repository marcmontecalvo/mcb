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
        # A stale prefetch leading back over the active route would loop forever.
        controller(tail=((3, 1, 0), (2, 1, 0), (3, 1, 0))),
    ):
        assert splice_route(blocked, route, following, [1.4, 1, 0.5]) is None
        assert blocked.prefetch is not None
    # Too far past the source for the motor's displacement check: retry later.
    assert splice_route(controller(), route, following, [2.9, 1, 0.5]) is None


def test_splice_truncates_to_motor_cap_once_under_planning_lookahead():
    from types import SimpleNamespace

    from mclab.native_driver import (
        MOTOR_ROUTE_POINTS,
        PLANNING_TICK_LOOKAHEAD,
        remaining_ticks,
        splice_route,
    )
    from mclab.route_search import RouteResult

    def run(active_len):
        route = [(x, 1 + x, 0) for x in range(active_len)]
        tail = tuple((x, 1 + x, 0) for x in range(active_len - 1, active_len + 30))
        prefetch = SimpleNamespace(
            start=route[-1],
            advance=lambda **_: RouteResult("segment", tail, 1, 9, "test"),
        )
        c = SimpleNamespace(
            prefetch=prefetch,
            committed_step=None,
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
        return c, route, splice_route(c, route, {"next_index": 1}, [0.5, 1, 0.5])

    # A 32-point staircase is ~172 ticks: too early, the prefetch waits.
    assert (
        remaining_ticks([(x, 1 + x, 0) for x in range(32)]) >= PLANNING_TICK_LOOKAHEAD
    )
    c, _, joined = run(32)
    assert joined is None and c.prefetch is not None
    # 20 stairs ahead is ~106 ticks: splice now, capped at the motor limit.
    c, route, joined = run(20)
    assert len(joined) == MOTOR_ROUTE_POINTS and joined[: len(route)] == route
    assert c.prefetch is None and len(c.route) > MOTOR_ROUTE_POINTS


def test_allow_climb_key_only_in_body_request_when_enabled():
    assert "allow_climb" not in MovementPolicy().payload()
    assert "allow_climb" not in MovementPolicy(allow_swim=True).payload()
    assert MovementPolicy(allow_climb=True).payload()["allow_climb"] is True
    with pytest.raises(ValueError):
        MovementPolicy(allow_climb=1)


def test_swim_surface_drop_and_ladder_edges_are_ordinary_native_routes():
    route = [(0, 1, 0), (1, 1, 0), (2, -5, 0), (2, -4, 0), (2, -3, 0), (3, -3, 0)]
    assert native_driver.ordinary_route(route) == route


def _swim_controller(flowing_at, allow_swim=True):
    cells = {}
    for p in ((1, 1, 0), (2, 1, 0), (3, 1, 0)):
        level = "1" if p == flowing_at else "0"
        cells[p[0], p[1] - 1, p[2]] = {
            "block": "minecraft:water",
            "fluid": "minecraft:water",
            "collision_boxes": [],
            "properties": {"level": level},
        }
    route = [(0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 1, 0)]
    return SimpleNamespace(
        movement_policy=MovementPolicy(allow_swim=allow_swim),
        route=list(route),
        search=object(),
        failed_edges=set(),
        _known_cells=lambda: cells,
        _discard_prefetch=lambda: None,
        last_result=None,
        terrain_policy=None,
    )


def test_flowing_water_swim_nodes_are_never_offered_to_the_motor():
    controller = _swim_controller((3, 1, 0))
    assert native_driver.offered_route(controller) == [(0, 1, 0), (1, 1, 0), (2, 1, 0)]
    assert not controller.failed_edges
    controller = _swim_controller((1, 1, 0))
    assert native_driver.offered_route(controller) == [(0, 1, 0)]
    assert controller.failed_edges == {((0, 1, 0), (1, 1, 0))}
    assert controller.route == [] and controller.search is None
    # Without swimming the check is skipped entirely.
    controller = _swim_controller((1, 1, 0), allow_swim=False)
    assert len(native_driver.offered_route(controller)) == 4


@pytest.mark.parametrize(
    "plan",
    [
        StepPlan((0, 5, 0), (0, 4, 0), breaks=((0, 4, 0),), kind="downward"),
        StepPlan(
            (0, 5, 0),
            (1, 4, 0),
            breaks=((1, 4, 0), (1, 5, 0), (1, 6, 0)),
            kind="descend",
        ),
    ],
)
def test_downward_and_descend_steps_go_to_follow_step_with_plan_shape(
    monkeypatch, plan
):
    from mclab.terrain_steps import TerrainPolicy

    rows = iter(
        [
            {"tick": 1, "route_follower": {"state": "idle"}},
            {"tick": 2, "step_follower": {"step_id": "step-1", "state": "failed"}},
            {"terminal": {"status": "success"}},
        ]
    )
    sent = []
    client = SimpleNamespace(
        send=lambda op, **kw: sent.append((op, kw)) or {"sequence": 1},
        acknowledge=lambda *_: None,
        wait=lambda predicate, **kw: True,
        record=lambda *a: None,
    )
    controller = SimpleNamespace(
        route=[],
        mode="terrain_editing",
        terrain=SimpleNamespace(plan=plan, policy=TerrainPolicy()),
        gap_jump=None,
        failed_edges=set(),
        movement_policy=MovementPolicy(allow_swim=True),
        committed_step=None,
        own_footholds=set(),
        edits_broken=0,
        edits_placed=0,
        search=None,
        _admit=lambda _o: None,
        _clear_terrain_forecast=lambda: None,
        drain_events=lambda: [],
        _discard_prefetch=lambda: None,
        decide=lambda o, *, native=False: None,
    )
    monkeypatch.setattr(
        native_driver,
        "policy_state",
        lambda _c: {"body": {"position": [0.5, 5, 0.5]}, **next(rows)},
    )
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))
    assert native_driver.drive_native(client, controller, 10) == {"status": "success"}
    op, fields = sent[0]
    assert op == "follow_step"
    assert fields["plan"]["kind"] == plan.kind
    assert fields["plan"]["source"] == plan.source
    assert fields["plan"]["target"] == plan.target
    assert fields["plan"]["breaks"] == plan.breaks and fields["plan"]["places"] == ()
    assert controller.failed_edges == {(plan.source, plan.target)}
