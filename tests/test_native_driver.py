from types import SimpleNamespace

import pytest

from mclab import control_loop, native_driver
from mclab.execution import ControllerFailure
from mclab.movement_policy import MovementPolicy
from mclab.terrain_steps import StepPlan, TerrainPolicy


def test_consecutive_native_routes_carry_their_own_door_and_vine_policy():
    sent = []

    class Client:
        def send(self, op, **fields):
            sent.append((op, fields))
            return fields

        def acknowledge(self, request, kind):
            assert kind == "route_accepted"

        def wait(self, predicate, timeout):
            assert predicate({"route_follower": {"route_id": sent[-1][1]["route_id"]}})

    controller = SimpleNamespace(
        route=[],
        movement_policy=MovementPolicy(),
        terrain_policy=TerrainPolicy(use_doors=True, climb_vines=False),
    )
    stats = {"routes_submitted": 0}
    route = [(0, 1, 0), (1, 1, 0)]
    native_driver.submit_route(Client(), controller, stats, route)
    controller.terrain_policy = TerrainPolicy(use_doors=False, climb_vines=True)
    native_driver.submit_route(Client(), controller, stats, route)
    assert [(fields["use_doors"], fields["climb_vines"]) for _, fields in sent] == [
        (True, False),
        (False, True),
    ]


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
@pytest.mark.parametrize(
    "reason", ["route_no_progress", "route_target_unknown_or_changed"]
)
def test_native_driver_hands_off_routes_without_python_tick_controls(
    monkeypatch, finish, reason
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
                    "reason": reason if finish == "needs_replan" else "",
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
    if finish == "needs_replan":
        assert controller.previous_route == ()
        rejected = [
            data for kind, data in records if kind == "native_route_edge_rejected"
        ]
        assert rejected == [
            {
                "source": (0, 1, 0),
                "target": (1, 1, 0),
                "reason": reason,
                "position": [0.5, 1, 0.5],
                "tick": 10,
            }
        ]


@pytest.mark.parametrize(
    "closer,disabled", [(False, False), (True, False), (False, True)]
)
def test_goal_no_progress_ends_attempt_only_when_best_distance_stalls(
    monkeypatch, closer, disabled
):
    ticks = iter(range(0, 4000, 100))
    sent, records = [], []
    client = SimpleNamespace(
        send=lambda op, **kw: sent.append(op) or {"sequence": 1},
        acknowledge=lambda *_: None,
        record=lambda *args: records.append(args),
        events=[],
        wait=lambda *_, **__: None,
    )
    controller = SimpleNamespace(
        goal=(100, 1, 0),
        route=[],
        mode="idle",
        terrain=None,
        gap_jump=None,
        committed_step=None,
        movement_policy=MovementPolicy(),
        decide=lambda _, **kw: None,
        _admit=lambda _: None,
        drain_events=lambda: [],
    )
    if disabled:  # stall_seconds = 0
        controller.goal_progress_ticks = None
    state = {"n": 0}

    def policy(_client):
        tick = next(ticks)
        state["n"] += 1
        # Oscillate 15 blocks wide (R5), or creep closer 1 block per 100 ticks.
        x = 0.5 + (state["n"] if closer else 15 * (state["n"] % 2))
        if state["n"] > 39:
            raise ControllerFailure("test ran out of ticks")
        return {"tick": tick, "body": {"position": [x, 1, 0.5]}}

    monkeypatch.setattr(native_driver, "policy_state", policy)
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda *_: None)
    native_driver.drive_native(client, controller, 10)
    if closer or disabled:
        assert client.last_controller_error == "test ran out of ticks"
    else:
        assert client.last_controller_error.startswith("goal_no_progress")
        assert (
            native_driver.GOAL_PROGRESS_TICKS
            <= state["n"] * 100
            <= native_driver.GOAL_PROGRESS_TICKS + 400
        )


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
    from mclab.route_types import RouteResult

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
    from mclab.route_types import RouteResult

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


def test_splice_that_changes_nothing_is_not_resubmitted():
    # Cohort 19 R5 rep 2: a body stuck at index 1 of a full 32-point route got the
    # identical route every ~6 ticks for 3,900 ticks; each resubmission reset the
    # motor's step deadline, so it never gave up on the edge.
    from types import SimpleNamespace

    from mclab.native_driver import MOTOR_ROUTE_POINTS, splice_route
    from mclab.route_types import RouteResult

    route = [(x, 1, 0) for x in range(MOTOR_ROUTE_POINTS)]
    tail = tuple((x, 1, 0) for x in range(MOTOR_ROUTE_POINTS - 1, 40))
    prefetch = SimpleNamespace(
        start=route[-1], advance=lambda **_: RouteResult("segment", tail, 1, 9, "t")
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
    assert splice_route(c, route, {"next_index": 1}, [0.5, 1, 0.5]) is None
    assert c.prefetch is prefetch  # kept for the ordinary handoff at the end


def _refresh_controller(start, tail):
    from types import SimpleNamespace

    from mclab.route_types import RouteResult

    c = SimpleNamespace(
        prefetch=SimpleNamespace(
            start=start, advance=lambda **_: RouteResult("segment", tail, 1, 9, "t")
        ),
        requires_body_planner=True,
        background_planning=True,
        submit_plan=lambda payload: None,
        committed_step=None,
        terrain=None,
        terrain_queue=[],
        search=None,
        search_nodes=64,
        search_ms=2,
        prefetch_adopted=0,
        last_result=None,
        route=[],
        route_steps={},
        previous_route=(),
        events=[],
        goal_revision=0,
    )
    c._discard_prefetch = lambda: setattr(c, "prefetch", None)
    return c


def test_body_planner_replans_ahead_and_switches_to_a_new_way():
    # Cohort 30 R3: a piece planned ~30 blocks ahead on an old map was never
    # revisited. A re-plan from a few nodes ahead now replaces the rest.
    from mclab.native_driver import splice_route

    route = [(x, 1, 0) for x in range(20)]
    turn = ((6, 1, 0), (6, 1, 1), (6, 1, 2), (6, 1, 3))
    c = _refresh_controller((6, 1, 0), turn)
    joined = splice_route(c, route, {"next_index": 2}, [1.4, 1, 0.5])
    assert joined == [(1, 1, 0), (2, 1, 0), (3, 1, 0), (4, 1, 0), (5, 1, 0), *turn]
    assert c.prefetch is None and c.route == joined
    assert c.previous_route == tuple(joined)  # the next re-plan favours this way


def test_body_planner_drops_replans_that_change_nothing_or_start_behind():
    from mclab.native_driver import splice_route

    route = [(x, 1, 0) for x in range(30)]
    # Same way on, plenty of route left: not resubmitted, dropped for the next one.
    same = tuple((x, 1, 0) for x in range(6, 20))
    c = _refresh_controller((6, 1, 0), same)
    assert splice_route(c, route, {"next_index": 2}, [1.4, 1, 0.5]) is None
    assert c.prefetch is None
    # The motor already passed the re-plan's start.
    c = _refresh_controller((3, 1, 0), ((3, 1, 0), (3, 1, 1)))
    assert splice_route(c, route, {"next_index": 5}, [4.4, 1, 0.5]) is None
    assert c.prefetch is None
    # Same way on but the route is nearly driven: extend it.
    short = [(x, 1, 0) for x in range(8)]
    c = _refresh_controller((7, 1, 0), tuple((x, 1, 0) for x in range(7, 20)))
    joined = splice_route(c, short, {"next_index": 2}, [1.4, 1, 0.5])
    assert joined is not None and joined[-1] == (19, 1, 0)


def test_body_planner_replan_starts_ahead_of_the_motor_once_per_map():
    from mclab.native_driver import REFRESH_AHEAD, prefetch_while_following

    route = [(x, 1, 0) for x in range(30)]
    c = _refresh_controller(None, ())
    c.prefetch = None
    c.goal = (99, 1, 0)
    c.prefetch_started = 0
    started = []
    c._planning_snapshot = lambda: (7, {})
    c._new_search = lambda cells, start, revision: started.append(start) or object()
    prefetch_while_following(c, route, {"next_index": 3})
    assert started == [route[3 + REFRESH_AHEAD - 1]]
    c.prefetch = None
    prefetch_while_following(c, route, {"next_index": 3})
    assert len(started) == 1  # same start, same map revision: nothing new to learn


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


def test_follow_step_carries_only_protection_near_the_plan(monkeypatch):
    # The full house-large protection (4,410 cells) rode every follow_step and
    # crossed the body's 16 KB POST limit (HTTP 413, run 20261106T182735343056Z).
    from mclab.terrain_steps import StepPlan, TerrainPolicy

    far = tuple((x, 5, 4000) for x in range(4000))
    plan = StepPlan((0, 5, 0), (0, 5, 1), ((0, 5, 1),), ((0, 6, 1),), "dig")
    policy = TerrainPolicy(protected_positions=far + ((0, 5, 2), (0, 5, 10)))
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
        terrain=SimpleNamespace(plan=plan, policy=policy),
        gap_jump=None,
        failed_edges=set(),
        movement_policy=MovementPolicy(),
        committed_step=None,
        own_footholds={(1, 5, 0)},
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
    fields = sent[0][1]
    got = set(fields["terrain_policy"]["protected_positions"])
    assert (0, 5, 2) in got  # protected cell beside the plan
    assert (1, 5, 0) in got  # own foothold beside the plan
    # cells nine away are outside the motor's check and not echoed
    assert (0, 5, 10) not in got
    assert not any(cell[2] == 4000 for cell in got)


def test_companion_goto_failure_stops_the_goto_not_the_episode(monkeypatch):
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
        stop_reason=lambda _: None,
    )

    def send(op, **_fields):
        sent.append(op)
        if op == "follow_route":
            raise TimeoutError("route not confirmed")
        return {"sequence": 2}

    client = SimpleNamespace(
        send=send, acknowledge=lambda *_: None, record=lambda *_: None
    )
    monkeypatch.setattr(
        native_driver, "policy_state", lambda _: {"body": {"position": [0.5, 1, 0.5]}}
    )
    monkeypatch.setattr(control_loop, "_running", lambda *_: None)

    end = native_driver.drive_native(client, controller, 10)

    assert end["type"] == "navigation_stopped"
    assert "course_end" not in sent and "release" in sent


def test_probe_that_turns_into_the_walk_drives_its_adopted_route(monkeypatch):
    # Run 20261111T135214868983Z: a probe found the route, was cancelled, and the
    # walk planned it again. The probe now unparks itself; the driver must notice.
    rows = iter([{"tick": 1}, {"tick": 2}, {"terminal": {"status": "success"}}])
    sent = []
    client = SimpleNamespace(
        send=lambda op, **kw: sent.append(op) or {"sequence": 1},
        acknowledge=lambda *_: None,
        wait=lambda predicate, **kw: predicate(
            {"route_follower": {"route_id": "native-1"}}
        ),
        record=lambda *args: None,
    )
    controller = SimpleNamespace(
        route=[],
        mode="following",
        terrain=None,
        gap_jump=None,
        failed_edges=set(),
        movement_policy=MovementPolicy(),
        committed_step=None,
        no_drive=True,
        _admit=lambda _: None,
        _discard_prefetch=lambda: None,
        drain_events=lambda: [],
    )

    def decide(observation, *, native=False):
        controller.route = [(0, 1, 0), (1, 1, 0)]

    def stop_reason(observation):
        if observation.get("tick") == 2:
            controller.no_drive = False  # verdict: walk the adopted route

    controller.decide, controller.stop_reason = decide, stop_reason
    monkeypatch.setattr(
        native_driver,
        "policy_state",
        lambda _c: {"body": {"position": [0.5, 1, 0.5]}, **next(rows)},
    )
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))
    assert native_driver.drive_native(client, controller, 10) == {"status": "success"}
    assert sent == ["follow_route", "cancel_route"]


def _no_progress_rows(entities, ticks):
    rows = [{"tick": 1, "route_follower": {"state": "idle"}}]
    for n, tick in enumerate(ticks, 1):
        rows += [
            {
                "tick": tick,
                "route_follower": {"route_id": f"native-{n}", "state": "following"},
            },
            {
                "tick": tick + 1,
                "visible_entities": entities,
                "route_follower": {
                    "route_id": f"native-{n}",
                    "state": "needs_replan",
                    "reason": "route_no_progress",
                    "next_index": 1,
                },
            },
        ]
    return rows


def _drive_no_progress(
    monkeypatch, rows, clear_mob=None, edge_failures=None, after_first=None
):
    rows = iter([*rows, {"terminal": {"status": "success"}}])
    records, routes = [], iter(range(1, 100))
    current = {}

    def send(op, **kw):
        if op == "follow_route":
            current["id"] = f"native-{next(routes)}"
        return {"sequence": 1}

    client = SimpleNamespace(
        send=send,
        acknowledge=lambda *_: None,
        wait=lambda predicate, **kw: predicate(
            {"route_follower": {"route_id": current.get("id")}}
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
        _admit=lambda _: None,
        _discard_prefetch=lambda: None,
        drain_events=lambda: [],
    )
    if clear_mob is not None:
        controller.clear_mob = clear_mob
    if edge_failures is not None:
        controller.edge_failures = edge_failures
    seen = []

    def decide(observation, *, native=False):
        seen.append(set(controller.failed_edges))
        if after_first is not None and len(seen) == 1:
            after_first()
        controller.route = [] if controller.failed_edges else [(0, 1, 0), (1, 1, 0)]

    controller.decide = decide
    monkeypatch.setattr(
        native_driver,
        "policy_state",
        lambda _c: {"body": {"position": [0.5, 1, 0.5]}, **next(rows)},
    )
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))
    native_driver.drive_native(client, controller, 10)
    notes = [d for kind, d in records if kind == "native_route_no_progress"]
    return controller, notes, seen


SHEEP = {"uuid": "s1", "type": "minecraft:sheep", "position": [1.5, 1.0, 0.5]}


def test_no_progress_without_mob_bans_edge_only_until_expiry(monkeypatch):
    edge = ((0, 1, 0), (1, 1, 0))
    rows = _no_progress_rows([], [5])
    rows.append({"tick": 6 + native_driver.NO_PROGRESS_BAN_TICKS})
    controller, notes, seen = _drive_no_progress(monkeypatch, rows)
    assert notes[0]["decision"] == "ban"
    assert notes[0]["ban_until"] == 6 + native_driver.NO_PROGRESS_BAN_TICKS
    assert edge in seen[1]  # banned right after
    assert edge not in controller.failed_edges  # expired


def test_an_edge_that_keeps_failing_is_banned_longer_each_time(monkeypatch):
    # Run 20261119T202232979013Z: one vine rise failed 25 times across separate
    # walks, each ban 200 ticks and each walk planning the same rise again.
    memory = native_driver.EdgeFailures()
    base = native_driver.NO_PROGRESS_BAN_TICKS
    _, first, _ = _drive_no_progress(
        monkeypatch, _no_progress_rows([], [5]), edge_failures=memory
    )
    start = 10 + base
    _, second, _ = _drive_no_progress(
        monkeypatch, _no_progress_rows([], [start]), edge_failures=memory
    )
    assert first[0]["ban_until"] == 6 + base
    assert second[0]["ban_until"] == start + 1 + 2 * base
    assert second[0]["failures"] == 2


def test_a_new_walk_starts_with_the_edges_still_banned(monkeypatch):
    edge = ((0, 1, 0), (1, 1, 0))
    memory = native_driver.EdgeFailures()
    memory.fail(edge, 0)
    _, notes, seen = _drive_no_progress(
        monkeypatch,
        [{"tick": 50, "route_follower": {"state": "idle"}}],
        edge_failures=memory,
    )
    assert edge in seen[0]
    assert notes == []


def test_edge_bans_stop_growing_at_the_cap():
    memory = native_driver.EdgeFailures()
    edge = ((0, 1, 0), (0, 2, 0))
    for _ in range(20):
        until = memory.fail(edge, 1000)
    assert until == 1000 + native_driver.MAX_EDGE_BAN_TICKS
    assert memory.active(1000 + native_driver.MAX_EDGE_BAN_TICKS) == {}


def _world(**cells):
    """A mutable block map and the lookup EdgeFailures reads it through."""
    world = {(0, 0, 0): {"block": "minecraft:stone"}}
    world.update(cells)
    return world, lambda: world


def test_a_failed_edge_stays_banned_while_its_blocks_are_unchanged():
    world, cells = _world()
    memory = native_driver.EdgeFailures(cells)
    edge = ((0, 1, 0), (1, 1, 0))
    memory.fail(edge, 0)
    world[(10, 1, 10)] = {"block": "minecraft:dirt"}  # far away: not this move
    assert edge in memory.active(5)
    assert not memory.stale(edge)


@pytest.mark.parametrize(
    "changed",
    [
        (1, 0, 0),  # the floor under the target
        (0, 2, 0),  # head room over the source
        (1, 3, 0),  # one above the target's head (a jump's clearance)
        (2, 1, 1),  # the next column beside the target (leaves at head height)
    ],
)
def test_a_changed_block_around_the_move_lifts_its_ban(changed):
    # The body placed or broke something, or the world changed: the move that
    # failed may work now, and an escalated ban must not keep it off the plan.
    world, cells = _world()
    memory = native_driver.EdgeFailures(cells)
    edge = ((0, 1, 0), (1, 1, 0))
    memory.fail(edge, 0)
    memory.fail(edge, 0)
    world[changed] = {"block": "minecraft:cobblestone"}
    assert memory.stale(edge)
    assert memory.active(5) == {}
    assert memory.failures(edge) == 0  # escalation starts over in the new world
    assert memory.fail(edge, 10) == 10 + native_driver.NO_PROGRESS_BAN_TICKS


def test_a_block_broken_around_the_move_lifts_its_ban():
    world, cells = _world()
    memory = native_driver.EdgeFailures(cells)
    edge = ((0, 1, 0), (0, 2, 0))  # a rise
    memory.fail(edge, 0)
    world[(0, 0, 0)] = {"block": "minecraft:air"}
    assert memory.active(5) == {}


def test_a_cell_out_of_sight_does_not_lift_a_ban():
    # Unknown is not a change: the map unloading a cell proves nothing.
    world, cells = _world()
    memory = native_driver.EdgeFailures(cells)
    edge = ((0, 1, 0), (1, 1, 0))
    memory.fail(edge, 0)
    del world[(0, 0, 0)]
    assert edge in memory.active(5)


def test_a_ban_lifts_mid_walk_when_the_body_changes_its_blocks(monkeypatch):
    world, cells = _world()
    memory = native_driver.EdgeFailures(cells)
    edge = ((0, 1, 0), (1, 1, 0))
    memory.fail(edge, 0)
    rows = [{"tick": 50, "route_follower": {"state": "idle"}}]

    def place(*_):  # the body places a block at the target's feet mid-walk
        world[(1, 1, 0)] = {"block": "minecraft:dirt"}
        return []

    rows.append({"tick": 51, "route_follower": {"state": "idle"}})
    _, _, seen = _drive_no_progress(
        monkeypatch, rows, edge_failures=memory, after_first=place
    )
    assert edge in seen[0]
    assert edge not in seen[1]


def test_companion_retries_through_sheep_then_kills_it(monkeypatch):
    killed = []
    rows = _no_progress_rows([SHEEP], [5, 10, 15])
    controller, notes, _ = _drive_no_progress(
        monkeypatch, rows, clear_mob=lambda uuid: killed.append(uuid) or True
    )
    assert [n["decision"] for n in notes] == ["retry", "retry", "kill"]
    assert notes[0]["entities"] == ["minecraft:sheep"]
    assert killed == ["s1"]
    assert not controller.failed_edges


def test_failed_kill_bans_the_edge(monkeypatch):
    rows = _no_progress_rows([SHEEP], [5, 10, 15])
    controller, notes, _ = _drive_no_progress(
        monkeypatch, rows, clear_mob=lambda uuid: False
    )
    assert notes[-1]["ok"] is False and "ban_until" in notes[-1]
    assert ((0, 1, 0), (1, 1, 0)) in controller.failed_edges


@pytest.mark.parametrize(
    "entity,companion",
    [
        (SHEEP, False),  # scored run: no clear_mob, never kills
        ({**SHEEP, "named": True}, True),
        ({**SHEEP, "type": "minecraft:zombie"}, True),
        ({**SHEEP, "type": "minecraft:villager"}, True),
    ],
)
def test_no_progress_mob_is_banned_after_retries_when_not_killable(
    monkeypatch, entity, companion
):
    killed = []
    rows = _no_progress_rows([entity], [5, 10, 15])
    _, notes, _ = _drive_no_progress(
        monkeypatch, rows, clear_mob=killed.append if companion else None
    )
    assert [n["decision"] for n in notes] == ["retry", "retry", "ban"]
    assert not killed


def test_blocking_entities_ignores_items_and_far_mobs():
    obs = {
        "visible_entities": [
            {"type": "minecraft:item", "item": "x", "position": [1.5, 1, 0.5]},
            {"type": "minecraft:cow", "position": [5.5, 1, 0.5]},
            SHEEP,
        ]
    }
    found = native_driver.blocking_entities(obs, (0, 1, 0), (1, 1, 0))
    assert found == [SHEEP]


def test_bystander_beside_step_is_not_cleared(monkeypatch):
    bystander = {**SHEEP, "position": [1.5, 1, 1.4]}
    killed = []
    rows = _no_progress_rows([bystander], [5])
    _, notes, _ = _drive_no_progress(monkeypatch, rows, clear_mob=killed.append)
    assert notes[0]["decision"] == "ban"
    assert not killed


def test_mixed_blockers_do_not_clear_animal(monkeypatch):
    villager = {**SHEEP, "uuid": "v1", "type": "minecraft:villager"}
    killed = []
    rows = _no_progress_rows([SHEEP, villager], [5, 10, 15])
    _, notes, _ = _drive_no_progress(monkeypatch, rows, clear_mob=killed.append)
    assert [n["decision"] for n in notes] == ["retry", "retry", "ban"]
    assert not killed


@pytest.mark.parametrize("feet_y", [1, -0.2])
def test_named_cow_near_path_guards_against_killing_sheep(monkeypatch, feet_y):
    cow = {
        "uuid": "c1",
        "type": "minecraft:cow",
        "named": True,
        "position": [1.5, feet_y, 1.2],
    }
    killed = []
    rows = _no_progress_rows([SHEEP, cow], [5, 10, 15])
    _, notes, _ = _drive_no_progress(monkeypatch, rows, clear_mob=killed.append)
    assert [n["decision"] for n in notes] == ["retry", "retry", "ban"]
    assert not killed


def test_blocker_uses_reported_bounding_box():
    entity = {
        **SHEEP,
        "position": [1.5, 1, 1.4],
        "bounding_box": [1.1, 1, 0.7, 1.9, 2, 2.1],
    }
    assert native_driver.blocking_entities(
        {"visible_entities": [entity]}, (0, 1, 0), (1, 1, 0)
    ) == [entity]


def _storm_rows(count):
    rows = [{"tick": 1, "route_follower": {"state": "idle"}}]
    for n in range(1, count + 1):
        rows += [
            {
                "tick": 2 * n,
                "route_follower": {"route_id": f"native-{n}", "state": "following"},
            },
            {
                "tick": 2 * n + 1,
                "route_follower": {
                    "route_id": f"native-{n}",
                    "state": "needs_replan",
                    "reason": "route_swept_geometry_unknown_or_unsafe",
                    "next_index": 1,
                },
            },
        ]
    return rows


def _drive_storm(monkeypatch, rows, plan=None, stuck_at=None):
    rows = iter([*rows, {"terminal": {"status": "success"}}])
    records, routes = [], iter(range(1, 100))
    current = {}

    def send(op, **kw):
        if op == "follow_route":
            current["id"] = f"native-{next(routes)}"
        return {"sequence": 1}

    client = SimpleNamespace(
        send=send,
        acknowledge=lambda *_: None,
        wait=lambda predicate, **kw: predicate(
            {"route_follower": {"route_id": current.get("id")}}
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
        own_footholds=set(),
        edits_broken=0,
        edits_placed=0,
        search=None,
        _admit=lambda _: None,
        _discard_prefetch=lambda: None,
        _clear_terrain_forecast=lambda: None,
        drain_events=lambda: [],
    )
    if plan is not None:
        controller.terrain = SimpleNamespace(plan=plan, policy=TerrainPolicy())
    if stuck_at is not None:
        controller.stop_reason = lambda o: (
            "stuck" if (o.get("tick") or 0) >= stuck_at else None
        )

    def decide(observation, *, native=False):
        if plan is None:
            controller.route = [(0, 1, 0), (1, 1, 0)]

    controller.decide = decide
    monkeypatch.setattr(
        native_driver,
        "policy_state",
        lambda _c: {"body": {"position": [0.5, 1, 0.5]}, **next(rows)},
    )
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))
    return native_driver.drive_native(client, controller, 10), controller, records


def test_unknown_geometry_storm_ends_the_goto_as_stuck(monkeypatch):
    # Small house run 20261112T234500320722Z: 29 replans hugged one unverifiable
    # wall and stood still twice for 5.5 s. Six fast kills now escalate like the
    # idle cutoff, so the caller changes stance under the 5 s rule.
    rows = _storm_rows(native_driver.GEOMETRY_STORM_REPLANS)
    end, controller, records = _drive_storm(monkeypatch, rows)
    assert end == {"type": "navigation_stopped", "data": {"reason": "stuck"}}
    assert ((0, 1, 0), (1, 1, 0)) in controller.failed_edges
    storm = [d for kind, d in records if kind == "native_route_storm_stuck"]
    assert storm and storm[0]["replans"] == native_driver.GEOMETRY_STORM_REPLANS


def test_fewer_geometry_kills_replan_on_without_escalating(monkeypatch):
    rows = _storm_rows(native_driver.GEOMETRY_STORM_REPLANS - 1)
    end, _, records = _drive_storm(monkeypatch, rows)
    assert end == {"status": "success"}
    assert not [d for kind, d in records if kind == "native_route_storm_stuck"]


def test_a_stuck_stop_does_not_ban_the_active_step_edge(monkeypatch):
    # The stuck-ban banned a step one tick before it confirmed (iron run
    # 20261113T211211538468Z); a stop now only ends the goto.
    plan = StepPlan((0, 5, 0), (1, 5, 0), (), ((1, 4, 0),), "step")
    rows = [
        {"tick": 1, "route_follower": {"state": "idle"}},
        {"tick": 2, "step_follower": {"step_id": "step-1", "state": "following"}},
        {"tick": 3, "step_follower": {"step_id": "step-1", "state": "following"}},
    ]
    end, controller, records = _drive_storm(monkeypatch, rows, plan=plan, stuck_at=3)
    assert end == {"type": "navigation_stopped", "data": {"reason": "stuck"}}
    assert ((0, 5, 0), (1, 5, 0)) not in controller.failed_edges
    assert "native_step_stuck_banned" not in [k for k, _ in records]


def _edit_controller(**extra):
    steps = (
        StepPlan((0, 1, 0), (1, 1, 0), breaks=((1, 1, 0),), kind="dig"),
        StepPlan((1, 1, 0), (2, 1, 0), places=((2, 0, 0),), kind="bridge"),
    )
    started = []
    controller = SimpleNamespace(
        requires_body_planner=True,
        submit_plan=object(),
        background_planning=True,
        committed_step=None,
        terrain_forecast=steps,
        prefetch=None,
        prefetch_started=0,
        goal=(9, 1, 0),
        goal_revision=0,
        events=[],
        search_nodes=10,
        search_ms=5,
        _planning_snapshot=lambda: (7, {(1, 1, 0): {"block": "minecraft:stone"}}),
        _new_search=lambda cells, node, revision, edits=(): started.append(
            (cells, node, revision, edits)
        )
        or SimpleNamespace(start=node, advance=lambda **kw: started.append(kw)),
        **extra,
    )
    return controller, steps, started


def test_prefetch_plans_past_the_forecast_edits_on_the_predicted_map():
    controller, steps, started = _edit_controller()
    native_driver.prefetch_past_edits(controller)
    cells, node, revision, edits = started[0]
    assert (node, revision, edits) == ((2, 1, 0), 7, steps)
    assert cells[(1, 1, 0)]["block"] == "minecraft:air"  # planned break
    assert cells[(2, 0, 0)]["collision_boxes"] == [[0, 0, 0, 1, 1, 1]]  # planned place
    assert controller.prefetch_started == 1
    native_driver.prefetch_past_edits(controller)  # next poll: the search advances
    assert started[1]["moving"] and controller.prefetch_started == 1


def test_prefetch_past_edits_waits_for_a_forecast():
    controller, _, started = _edit_controller()
    controller.terrain_forecast = ()
    native_driver.prefetch_past_edits(controller)
    assert started == [] and controller.prefetch is None


def test_failed_edit_discards_the_prefetch_planned_past_it(monkeypatch):
    plan = StepPlan((0, 5, 0), (0, 5, 1), ((0, 5, 1),), (), "dig")
    rows = iter(
        [
            {"tick": 1, "route_follower": {"state": "idle"}},
            {"tick": 2, "step_follower": {"step_id": "step-1", "state": "failed"}},
            {"terminal": {"status": "success"}},
        ]
    )
    discarded = []
    client = SimpleNamespace(
        send=lambda op, **kw: {"sequence": 1},
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
        movement_policy=MovementPolicy(),
        committed_step=None,
        own_footholds=set(),
        edits_broken=0,
        edits_placed=0,
        search=None,
        _admit=lambda _o: None,
        _clear_terrain_forecast=lambda: None,
        drain_events=lambda: [],
        _discard_prefetch=lambda: discarded.append(1),
        decide=lambda o, *, native=False: None,
    )
    monkeypatch.setattr(
        native_driver,
        "policy_state",
        lambda _c: {"body": {"position": [0.5, 5, 0.5]}, **next(rows)},
    )
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))
    native_driver.drive_native(client, controller, 10)
    assert discarded == [1]


def test_a_motor_failed_step_edge_is_banned_only_briefly(monkeypatch):
    plan = StepPlan((0, 5, 0), (0, 5, 1), ((0, 5, 1),), (), "dig")
    rows = iter(
        [
            {"tick": 1, "route_follower": {"state": "idle"}},
            {"tick": 2, "step_follower": {"step_id": "step-1", "state": "failed"}},
            {"tick": 2 + native_driver.STEP_FAIL_BAN_TICKS, "route_follower": {}},
            {"terminal": {"status": "success"}},
        ]
    )
    discarded = []
    client = SimpleNamespace(
        send=lambda op, **kw: {"sequence": 1},
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
        movement_policy=MovementPolicy(),
        committed_step=None,
        own_footholds=set(),
        edits_broken=0,
        edits_placed=0,
        search=None,
        _admit=lambda _o: None,
        _clear_terrain_forecast=lambda: None,
        drain_events=lambda: [],
        _discard_prefetch=lambda: discarded.append(set(controller.failed_edges)),
        decide=lambda o, *, native=False: None,
    )
    monkeypatch.setattr(
        native_driver,
        "policy_state",
        lambda _c: {"body": {"position": [0.5, 5, 0.5]}, **next(rows)},
    )
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))
    native_driver.drive_native(client, controller, 10)
    # banned while the replan reads fresh cells, free again after 2 s
    assert discarded == [{((0, 5, 0), (0, 5, 1))}]
    assert controller.failed_edges == set()


def test_drive_calls_on_pass_only_while_a_route_is_followed(monkeypatch):
    seen = []
    rows = iter(
        [
            {"tick": 1, "route_follower": {"state": "idle"}},
            {"tick": 2, "route_follower": {"state": "following"}},
            {"terminal": {"status": "success"}},
        ]
    )
    client = SimpleNamespace(
        send=lambda *a, **k: {"sequence": 1},
        acknowledge=lambda *_: None,
        wait=lambda *a, **k: True,
        record=lambda *a: None,
    )
    controller = SimpleNamespace(
        route=[],
        on_pass=lambda position, route=None: seen.append(position),
        gap_jump=None,
        failed_edges=set(),
        movement_policy=MovementPolicy(),
        committed_step=None,
        search=None,
        terrain=None,
        _admit=lambda _o: None,
        _discard_prefetch=lambda: None,
        drain_events=lambda: [],
        decide=lambda o, *, native=False: None,
    )
    monkeypatch.setattr(
        native_driver,
        "policy_state",
        lambda _c: {"body": {"position": [1.5, 5, 0.5]}, **next(rows)},
    )
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))
    try:
        native_driver.drive_native(client, controller, 10)
    except Exception:  # the fake route state is not driven further than the hook
        pass
    assert seen == [[1.5, 5, 0.5]]


def test_the_route_ahead_is_what_the_motor_still_has_to_drive():
    route = [[0, 5, 0], [1, 5, 0], [2, 5, 0], [3, 6, 0]]
    follower = {"route_id": "native-1", "state": "following", "next_index": 2}
    # from the source of the edge being driven on
    assert native_driver.route_ahead(route, follower, "native-1") == route[1:]
    # another route's status says nothing about this one: all of it is ahead
    assert native_driver.route_ahead(route, follower, "native-2") == route
    assert native_driver.route_ahead([], follower, None) == []


def test_replan_on_a_new_map_revision_waits_for_a_change_along_the_route():
    # House-large run 20261119T133618892664Z: every map revision (most of them a
    # block placed beside the body) restarted the re-plan ahead; 78% of plans were
    # cancelled. A revision that changes nothing along the route ahead is no news.
    from mclab.native_driver import REFRESH_AHEAD, prefetch_while_following

    route = [(x, 1, 0) for x in range(30)]
    air = {"block": "minecraft:air", "collision_boxes": []}
    stone = {"block": "minecraft:stone", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    floor = {
        (x, y, z): dict(air)
        for x in range(-2, 32)
        for y in range(0, 4)
        for z in range(-3, 3)
    }
    c = _refresh_controller(None, ())
    c.route = list(route)
    c.prefetch = None
    c.goal = (99, 1, 0)
    c.prefetch_started = 0
    started = []
    snapshot = {"value": (7, floor)}
    c._planning_snapshot = lambda: snapshot["value"]
    c._new_search = lambda cells, start, revision: started.append(start) or object()
    prefetch_while_following(c, route, {"next_index": 3})
    assert started == [route[3 + REFRESH_AHEAD - 1]]
    # A block placed off the route: same start, newer map, no re-plan.
    far = {**floor, (5, 1, -3): stone}
    snapshot["value"] = (8, far)
    c.prefetch = None
    prefetch_while_following(c, route, {"next_index": 3})
    assert len(started) == 1
    # Re-observed cells (new records, same geometry) are no change either.
    again = {p: dict(cell, observed_tick=99) for p, cell in far.items()}
    snapshot["value"] = (9, again)
    prefetch_while_following(c, route, {"next_index": 3})
    assert len(started) == 1
    # A block placed on the route ahead: re-plan.
    snapshot["value"] = (10, {**again, (20, 1, 0): stone})
    prefetch_while_following(c, route, {"next_index": 3})
    assert len(started) == 2


def test_complete_route_does_not_replan_on_each_step_when_map_unchanged():
    # Run 20261120T175636584118Z fired 20 plans in 6.8 s along a complete, unchanged
    # route because next_index advanced ``end`` by 1 each step.
    from mclab.native_driver import prefetch_while_following

    route = [(x, 1, 0) for x in range(25)]
    air = {"block": "minecraft:air", "collision_boxes": []}
    stone = {"block": "minecraft:stone", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    floor = {
        (x, y, z): dict(air)
        for x in range(-2, 28)
        for y in range(0, 4)
        for z in range(-3, 3)
    }
    c = _refresh_controller(None, ())
    c.route = list(route)
    c.goal = route[-1]  # complete route: already reaches the goal
    c.prefetch = None
    c.prefetch_started = 0
    started = []
    snapshot = {"value": (7, floor)}
    c._planning_snapshot = lambda: snapshot["value"]
    c._new_search = lambda cells, start, revision: started.append(start) or object()
    # Advancing next_index along the complete route on unchanged geometry fires 0 plans.
    for idx in (1, 2, 3, 4, 5):
        prefetch_while_following(c, route, {"next_index": idx})
    assert started == []
    # Even a newer map revision with an off-route change fires 0 plans.
    snapshot["value"] = (8, {**floor, (5, 1, -3): stone})
    prefetch_while_following(c, route, {"next_index": 6})
    assert started == []
    # Only a real change along the route ahead triggers a refresh.
    snapshot["value"] = (9, {**floor, (18, 1, 0): stone})
    prefetch_while_following(c, route, {"next_index": 7})
    assert len(started) == 1


def test_route_ending_within_the_goal_radius_is_complete_and_not_replanned():
    # Companion host run 20261123T211058112992Z: a goto with radius 1.8 adopted a
    # route ending one cell off the goal; ``route[-1] == goal`` never held, so every
    # map revision (and every step of the advancing start) fired a full re-plan that
    # finished "reached", was dropped as nothing new, and was cancelled.
    from types import SimpleNamespace

    from mclab.native_driver import prefetch_while_following

    route = [(x, 1, 0) for x in range(25)]
    air = {"block": "minecraft:air", "collision_boxes": []}
    stone = {"block": "minecraft:stone", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    floor = {
        (x, y, z): dict(air)
        for x in range(-2, 28)
        for y in range(0, 4)
        for z in range(-3, 3)
    }
    c = _refresh_controller(None, ())
    c.route = list(route)
    c.goal = (25, 1, 1)  # the route stops short: within the reach radius
    c.radius = 1.8
    c.interact_reach = None
    c.prefetch = None
    c.prefetch_started = 0
    started = []
    snapshot = {"value": (7, floor)}
    c._planning_snapshot = lambda: snapshot["value"]
    c._new_search = lambda cells, start, revision: started.append(start) or object()
    for rev, idx in enumerate((1, 2, 3, 4, 5, 6), start=8):
        snapshot["value"] = (rev, floor)
        prefetch_while_following(c, route, {"next_index": idx})
    assert started == []
    # A real change on the way ahead still re-plans.
    snapshot["value"] = (20, {**floor, (18, 1, 0): stone})
    prefetch_while_following(c, route, {"next_index": 7})
    assert len(started) == 1
    # A route that stops outside the radius is not complete: it keeps extending.
    far = SimpleNamespace(**{**vars(c), "goal": (40, 1, 0), "refreshed": None})
    far.prefetch = None
    started.clear()
    prefetch_while_following(far, route, {"next_index": 3})
    assert len(started) == 1


def _single_route_client(sent, records, current, reject=lambda n: None):
    count = {"n": 0}

    def send(op, **kw):
        if op == "follow_route":
            count["n"] += 1
            current["id"] = kw["route_id"]
        sent.append((op, kw))
        return {"sequence": len(sent), "op": op, "n": count["n"]}

    def acknowledge(request, kind):
        reason = reject(request["n"]) if kind == "route_accepted" else None
        if reason:
            raise RuntimeError(
                f"Bridge rejected request {request['sequence']}: {reason}"
            )

    return SimpleNamespace(
        send=send,
        acknowledge=acknowledge,
        wait=lambda predicate, **kw: predicate(
            {"route_follower": {"route_id": current.get("id")}}
        ),
        record=lambda *args: records.append(args),
    )


def _plain_controller(**fields):
    return SimpleNamespace(
        **{
            "route": [],
            "mode": "following",
            "terrain": None,
            "gap_jump": None,
            "failed_edges": set(),
            "movement_policy": MovementPolicy(allow_shapes=True),
            "committed_step": None,
            "own_footholds": set(),
            "search": None,
            "_admit": lambda _: None,
            "_discard_prefetch": lambda: None,
            "_clear_terrain_forecast": lambda: None,
            "drain_events": lambda: [],
            **fields,
        }
    )


def _rows(monkeypatch, rows, position=(0.5, 1, 0.5)):
    rows = iter(rows)
    monkeypatch.setattr(
        native_driver,
        "policy_state",
        lambda _c: {"body": {"position": list(position)}, **next(rows)},
    )
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))


def test_a_route_the_body_refuses_is_replanned_around_not_resubmitted(monkeypatch):
    # House-large run 20261119T133618892664Z: a route over a closed trapdoor
    # (y 69.1875) was refused "route_point" 35 times in a row. The refusal ended the
    # goto with no memory, and the next goto planned the same route again.
    sent, records, current = [], [], {}
    client = _single_route_client(
        sent, records, current, reject=lambda n: "route_point" if n == 1 else None
    )
    over = [(0, 1, 0), (1, 1.1875, 0), (2, 1, 0)]
    around = [(0, 1, 0), (0, 1, 1)]
    controller = _plain_controller()
    plans = []

    def decide(observation, *, native=False):
        if not controller.route and len(plans) < 2:
            controller.route = list(over if not plans else around)
            plans.append(list(controller.route))

    controller.decide = decide
    _rows(
        monkeypatch,
        [
            {"tick": 1, "route_follower": {"state": "idle"}},
            {"tick": 2, "route_follower": {"state": "idle"}},
            {"tick": 5, "route_follower": {"route_id": "native-2", "state": "done"}},
            {"terminal": {"status": "success"}},
        ],
    )
    assert native_driver.drive_native(client, controller, 10) == {"status": "success"}
    assert plans == [over, around]
    assert ((0, 1, 0), (1, 1.1875, 0)) in controller.failed_edges
    assert ((1, 1.1875, 0), (2, 1, 0)) in controller.failed_edges
    refused = [d for kind, d in records if kind == "native_route_refused"]
    assert refused and refused[0]["reason"] == "route_point"
    assert refused[0]["route"] == over


def test_repeated_route_refusals_end_the_goto_as_stuck(monkeypatch):
    sent, records, current = [], [], {}
    client = _single_route_client(
        sent, records, current, reject=lambda n: "route_edge_permission"
    )
    controller = _plain_controller(stop_reason=lambda _o: None)
    targets = iter((dx, 1, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz)

    def decide(observation, *, native=False):
        if not controller.route:
            controller.route = [(0, 1, 0), next(targets)]

    controller.decide = decide
    _rows(
        monkeypatch,
        [{"tick": t, "route_follower": {"state": "idle"}} for t in range(1, 50)],
    )
    end = native_driver.drive_native(client, controller, 10)
    assert end == {"type": "navigation_stopped", "data": {"reason": "stuck"}}
    submitted = [op for op, _ in sent if op == "follow_route"]
    assert len(submitted) == native_driver.GEOMETRY_STORM_REPLANS


def test_a_centring_the_motor_refuses_is_not_requested_again(monkeypatch):
    # House-large run 20261119T133618892664Z: feet on a stair's low step (y 70.5),
    # stance planned from the stair top (71.0). RouteMotor refuses a centre-only
    # route more than 0.2 off the feet ("route_center_not_grounded") and Python
    # resubmitted the same centring 64 times in 16.8 s.
    sent, records, current = [], [], {}
    client = _single_route_client(sent, records, current)
    controller = _plain_controller(
        mode="centering", center_radius=0.05, uncentered=set()
    )
    node = (0, 71.0, 0)

    def decide(observation, *, native=False):
        if node in controller.uncentered:
            controller.route = []
            controller.mode = "arrived"
            return
        controller.mode = "centering"
        controller.route = [node, node]

    controller.decide = decide
    _rows(
        monkeypatch,
        [
            {"tick": 1, "route_follower": {"state": "idle"}},
            {
                "tick": 3,
                "route_follower": {
                    "route_id": "native-1",
                    "state": "needs_replan",
                    "reason": "route_center_not_grounded",
                    "next_index": 1,
                },
            },
            {"tick": 4, "route_follower": {"state": "idle"}},
            {"terminal": {"status": "success"}},
        ],
        position=(0.8, 70.5, 0.5),
    )
    native_driver.drive_native(client, controller, 10)
    assert node in controller.uncentered
    assert [op for op, _ in sent].count("follow_route") == 1
    assert any(kind == "native_center_refused" for kind, _ in records)


def test_confirm_step_accepts_scaffold_drop_column():
    from collections import ChainMap

    crane = ((0, 0, 0), (0, 1, 0), (0, 2, 0), (1, 2, 0), (2, 2, 0))
    places = crane + tuple((2, y, 0) for y in range(-10, 0))
    plan = StepPlan(
        (1, 0, 0),
        (2, -1, 0),
        places=places,
        kind="scaffold_drop",
        scaffold=places,
    )
    before = {
        "inventory": [{"slot": 0, "item": "minecraft:scaffolding", "count": 16}],
        "building_pickups": {},
    }
    after = {
        "body": {
            "inventory": [{"slot": 0, "item": "minecraft:scaffolding", "count": 1}],
            "building_pickups": {},
        }
    }
    scaff = {
        "block": "minecraft:scaffolding",
        "fluid": "minecraft:empty",
        "collision_boxes": [],
    }
    # Lower shaft cells (below +-4 near-body refresh) live in known cells, not fresh.
    fresh = {p: scaff for p in places if p[1] >= -4}
    known = {p: scaff for p in places}
    report = {
        "broken_positions": [],
        "placed_positions": [
            {"position": list(p), "item": "minecraft:scaffolding"} for p in places
        ],
    }
    assert not native_driver.confirm_step(plan, before, after, fresh, report)
    assert native_driver.confirm_step(
        plan, before, after, ChainMap(fresh, known), report
    )
    # Missing one block in known geometry is not yet confirmed.
    incomplete = dict(known)
    incomplete[(2, -10, 0)] = {"block": "minecraft:air", "collision_boxes": []}
    assert not native_driver.confirm_step(
        plan, before, after, ChainMap(fresh, incomplete), report
    )


def test_route_detour_away_from_goal_is_not_cut_off_while_advancing(monkeypatch):
    """A valid detour moving away from the goal is not cut off while advancing."""
    route = [(-i, 1, 0) for i in range(20)]  # walks away from goal (5, 1, 0)
    sent, records = [], []
    client = SimpleNamespace(
        send=lambda op, **kw: sent.append((op, kw)) or {"sequence": 1},
        acknowledge=lambda *_: None,
        record=lambda *args: records.append(args),
        events=[],
        wait=lambda *_, **__: None,
    )

    def decide(obs, *, native=False):
        if obs["tick"] >= 390:
            controller.route = []
            controller.mode = "idle"

    controller = SimpleNamespace(
        goal=(5, 1, 0),
        goal_progress_ticks=180,
        route=list(route),
        mode="following",
        terrain=None,
        gap_jump=None,
        committed_step=None,
        movement_policy=MovementPolicy(),
        decide=decide,
        _admit=lambda _: None,
        drain_events=lambda: [],
        stop_reason=lambda obs: "arrived" if obs["tick"] >= 400 else None,
    )
    rows = [{"tick": 0, "body": {"position": [0.5, 1, 0.5]}, "route_follower": {}}]
    # 19 steps x 20 ticks = 380 ticks (> 180 limit) moving away from (5, 1, 0).
    for step in range(1, 20):
        rows.append(
            {
                "tick": step * 20,
                "body": {"position": [0.5 - step, 1, 0.5]},
                "route_follower": {
                    "route_id": "native-1",
                    "state": "following",
                    "next_index": step,
                },
            }
        )
    # Route completes at tick 390, then one idle poll at tick 400 still far from goal.
    rows.append(
        {
            "tick": 390,
            "body": {"position": [-18.5, 1, 0.5]},
            "route_follower": {
                "route_id": "native-1",
                "state": "done",
                "next_index": 20,
            },
        }
    )
    rows.append(
        {
            "tick": 400,
            "body": {"position": [-18.5, 1, 0.5]},
            "route_follower": {"state": "idle"},
        }
    )
    it = iter(rows)
    monkeypatch.setattr(native_driver, "policy_state", lambda _: next(it))
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda *_: None)
    end = native_driver.drive_native(client, controller, 10)
    assert end == {"type": "navigation_stopped", "data": {"reason": "arrived"}}
    assert client.last_controller_error is None


def test_standing_still_on_a_route_still_triggers_goal_no_progress(monkeypatch):
    """A body whose route_follower stops advancing still trips goal_no_progress."""
    route = [(-i, 1, 0) for i in range(10)]
    sent, records = [], []
    client = SimpleNamespace(
        send=lambda op, **kw: sent.append((op, kw)) or {"sequence": 1},
        acknowledge=lambda *_: None,
        record=lambda *args: records.append(args),
        events=[],
        wait=lambda *_, **__: None,
    )
    controller = SimpleNamespace(
        goal=(5, 1, 0),
        goal_progress_ticks=180,
        route=list(route),
        mode="following",
        terrain=None,
        gap_jump=None,
        committed_step=None,
        movement_policy=MovementPolicy(),
        decide=lambda _, **kw: None,
        _admit=lambda _: None,
        drain_events=lambda: [],
    )
    rows = [
        {"tick": 0, "body": {"position": [0.5, 1, 0.5]}, "route_follower": {}},
        {
            "tick": 20,
            "body": {"position": [-0.5, 1, 0.5]},
            "route_follower": {
                "route_id": "native-1",
                "state": "following",
                "next_index": 2,
            },
        },
        {
            "tick": 40,
            "body": {"position": [-1.5, 1, 0.5]},
            "route_follower": {
                "route_id": "native-1",
                "state": "following",
                "next_index": 3,
            },
        },
    ]
    # Stuck at next_index=3 from tick 40 onwards.
    for tick in range(60, 400, 20):
        rows.append(
            {
                "tick": tick,
                "body": {"position": [-1.5, 1, 0.5]},
                "route_follower": {
                    "route_id": "native-1",
                    "state": "following",
                    "next_index": 3,
                },
            }
        )
    seen = []
    it = iter(rows)

    def policy(_client):
        row = next(it)
        seen.append(row["tick"])
        return row

    monkeypatch.setattr(native_driver, "policy_state", policy)
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda *_: None)
    native_driver.drive_native(client, controller, 10)
    assert client.last_controller_error.startswith("goal_no_progress")
    assert seen[-1] == 220  # 40 (last advance) + 180 (limit)


def test_resubmitting_routes_at_index_one_still_triggers_goal_no_progress(monkeypatch):
    """Repeated replans that never advance past next_index=1 do not reset the clock."""
    sent, records = [], []
    client = SimpleNamespace(
        send=lambda op, **kw: sent.append((op, kw)) or {"sequence": 1},
        acknowledge=lambda *_: None,
        record=lambda *args: records.append(args),
        events=[],
        wait=lambda *_, **__: None,
    )
    controller = SimpleNamespace(
        goal=(10, 1, 0),
        goal_progress_ticks=180,
        route=[(0, 1, 0), (-1, 1, 0)],
        mode="following",
        terrain=None,
        gap_jump=None,
        committed_step=None,
        failed_edges=set(),
        previous_route=(),
        search=None,
        _discard_prefetch=lambda: None,
        movement_policy=MovementPolicy(),
        decide=lambda _, **kw: setattr(controller, "route", [(0, 1, 0), (-1, 1, 0)]),
        _admit=lambda _: None,
        drain_events=lambda: [],
    )
    state = {"tick": -20, "route_n": 0}

    def policy(_client):
        state["tick"] += 20
        if state["tick"] == 0:
            return {
                "tick": 0,
                "body": {"position": [0.5, 1, 0.5]},
                "route_follower": {},
            }
        state["route_n"] += 1
        return {
            "tick": state["tick"],
            "body": {"position": [0.5, 1, 0.5]},
            "route_follower": {
                "route_id": f"native-{state['route_n']}",
                "state": "needs_replan",
                "reason": "route_step_deadline",
                "next_index": 1,
            },
        }

    monkeypatch.setattr(native_driver, "policy_state", policy)
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda *_: None)
    native_driver.drive_native(client, controller, 10)
    assert client.last_controller_error.startswith("goal_no_progress")
    assert state["tick"] == 180


def test_short_two_node_detour_routes_keep_progress_alive(monkeypatch):
    """Two-node routes finishing state=done between polls count as progress."""
    sent, records = [], []
    client = SimpleNamespace(
        send=lambda op, **kw: sent.append((op, kw)) or {"sequence": 1},
        acknowledge=lambda *_: None,
        record=lambda *args: records.append(args),
        events=[],
        wait=lambda *_, **__: None,
    )
    state = {"step": 0}

    def decide(_obs, *, native=False):
        s = state["step"]
        controller.route = [(-s, 1, 0), (-(s + 1), 1, 0)]
        controller.mode = "following"

    controller = SimpleNamespace(
        goal=(5, 1, 0),
        goal_progress_ticks=100,
        route=[(0, 1, 0), (-1, 1, 0)],
        mode="following",
        terrain=None,
        gap_jump=None,
        committed_step=None,
        movement_policy=MovementPolicy(),
        decide=decide,
        _admit=lambda _: None,
        drain_events=lambda: [],
        stop_reason=lambda obs: "arrived" if obs["tick"] >= 200 else None,
    )
    rows = [{"tick": 0, "body": {"position": [0.5, 1, 0.5]}, "route_follower": {}}]
    for i in range(1, 11):
        rows.append(
            {
                "tick": i * 20,
                "body": {"position": [0.5 - i, 1, 0.5]},
                "route_follower": {
                    "route_id": f"native-{i}",
                    "state": "done",
                    "next_index": 2,
                },
            }
        )
    it = iter(rows)

    def policy(_client):
        row = next(it)
        state["step"] = row["tick"] // 20
        return row

    monkeypatch.setattr(native_driver, "policy_state", policy)
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda *_: None)
    end = native_driver.drive_native(client, controller, 10)
    assert end == {"type": "navigation_stopped", "data": {"reason": "arrived"}}
    assert client.last_controller_error is None


@pytest.mark.parametrize("confirmed", [True, False])
def test_confirmed_step_keeps_detour_alive_but_failed_step_does_not(
    monkeypatch, confirmed
):
    """Confirmed terrain steps along a detour reset the clock; failed steps do not."""
    from mclab.terrain_steps import TerrainPolicy

    sent, records = [], []
    client = SimpleNamespace(
        send=lambda op, **kw: sent.append((op, kw)) or {"sequence": 1},
        acknowledge=lambda *_: None,
        record=lambda *args: records.append(args),
        events=[],
        wait=lambda *_, **__: None,
    )
    step_num = {"n": 0}
    policy_obj = TerrainPolicy()

    def decide(obs, *, native=False):
        if obs["tick"] < 240:
            step_num["n"] += 1
            plan = StepPlan(
                (0, 1, 0),
                (0, 2, 0),
                breaks=((0, 3, 0),),
                kind="pillar",
            )
            controller.terrain = SimpleNamespace(plan=plan, policy=policy_obj)
        else:
            controller.terrain = None

    controller = SimpleNamespace(
        goal=(10, 1, 0),
        goal_progress_ticks=120,
        route=[],
        mode="terrain_editing",
        terrain=None,
        gap_jump=None,
        committed_step=None,
        failed_edges=set(),
        own_footholds=set(),
        edits_broken=0,
        edits_placed=0,
        search=None,
        _clear_terrain_forecast=lambda: None,
        _discard_prefetch=lambda: None,
        _fresh=lambda _: {(0, 3, 0): {"block": "minecraft:air", "collision_boxes": []}},
        movement_policy=MovementPolicy(),
        decide=decide,
        _admit=lambda _: None,
        drain_events=lambda: [],
        stop_reason=lambda obs: "arrived" if obs["tick"] >= 240 else None,
    )
    rows = [{"tick": 0, "body": {"position": [0.5, 1, 0.5]}, "step_follower": {}}]
    for i in range(1, 5):
        rows.append(
            {
                "tick": i * 60,
                "body": {"position": [0.5, 1, 0.5], "inventory": []},
                "step_follower": {
                    "step_id": f"step-{i}",
                    "state": "edits_ready" if confirmed else "needs_replan",
                    "reason": "" if confirmed else "native_step_no_progress",
                    "broken_positions": [[0, 3, 0]] if confirmed else [],
                    "placed_positions": [],
                },
            }
        )
    it = iter(rows)
    monkeypatch.setattr(native_driver, "policy_state", lambda _: next(it))
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda *_: None)
    end = native_driver.drive_native(client, controller, 10)
    if confirmed:
        assert end == {"type": "navigation_stopped", "data": {"reason": "arrived"}}
        assert client.last_controller_error is None
    else:
        assert client.last_controller_error.startswith("goal_no_progress")
