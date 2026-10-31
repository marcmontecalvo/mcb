"""Development route handoff: Python plans, mcb-body executes every server tick."""

import math
import time
from collections import Counter
from dataclasses import asdict

from mclab import water_movement
from mclab.execution import ControllerFailure, policy_state
from mclab.navigation import empty, full_cube

PROFILE = "dev-native-route-v1"
MOTOR_ROUTE_POINTS = 32
# Baritone Settings.planningTickLookahead: plan the next segment while this many
# ticks of the current one remain, so the motor never brakes at a segment end.
PLANNING_TICK_LOOKAHEAD = 150


def remaining_ticks(route):
    """Estimated ticks to drive `route` at sprint pace (route_search cost constants)."""
    from mclab.route_search import JUMP_PENALTY, SPRINT

    total = 0.0
    for a, b in zip(route, route[1:], strict=False):
        total += SPRINT * math.hypot(b[0] - a[0], b[2] - a[2]) + (
            JUMP_PENALTY if b[1] > a[1] else 0.0
        )
    return total


def ordinary_route(route):
    """Do not silently turn a gap/macro into ordinary walking."""
    selected = list(route[:1])
    for point in route[1:32]:
        previous = selected[-1]
        if max(abs(point[0] - previous[0]), abs(point[2] - previous[2])) > 1:
            break
        selected.append(point)
    return selected


def flowing_cut(controller, route):
    """Index of the first swim stance on flowing water, or None.

    The body refuses flowing water as a swim stance, so such a node must never
    be offered to the motor. Index 0 is the body's own stance and is skipped.
    """
    if not getattr(getattr(controller, "movement_policy", None), "allow_swim", False):
        return None
    cells = controller._known_cells()
    for index, point in enumerate(route[1:], 1):
        if water_movement.flowing_water(cells.get((point[0], point[1] - 1, point[2]))):
            return index
    return None


def offered_route(controller):
    walkable = getattr(controller, "walkable_route", None)
    route = ordinary_route(walkable() if walkable else controller.route)
    cut = flowing_cut(controller, route)
    if cut is not None:
        if cut == 1:
            # Nothing to drive before it: have the planner route around the edge.
            controller.failed_edges.add((tuple(route[0]), tuple(route[1])))
            controller.route.clear()
            controller.search = None
            controller._discard_prefetch()
        route = route[:cut]
    result = getattr(controller, "last_result", None)
    if (
        getattr(controller, "terrain_policy", None) is not None
        and result is not None
        and result.status == "unreachable"
    ):
        # The Python follower stops before a dead-end's final approach so its
        # terrain forecast can build an ascent. A native batch must stop at the
        # same boundary instead of consuming that reserved approach in one go.
        approach = controller._terrain_approach_nodes()
        if len(route) > approach:
            route = route[: len(route) - approach + 1]
    return route


def prefetch_while_following(controller, route):
    """Plan from the native segment's end while the motor drives toward it.

    Waiting for the motor to finish before searching cost a stop plus one full
    search budget at every segment end (run 20261029T190159944470Z: ~7 ticks
    standing per handoff, 15-34 handoffs per route).
    """
    if (
        getattr(controller, "planner_lane", None) is None
        or controller.prefetch is not None
        or controller.committed_step is not None
        or getattr(controller, "route_steps", None)
    ):
        return
    end = route[-1]
    if end == controller.goal:
        return
    revision, cells = controller._planning_snapshot()
    controller.prefetch = controller._new_search(cells, end, revision)
    controller.prefetch_started += 1
    controller.events.append(
        (
            "route_prefetch_started",
            {
                "start": end,
                "map_revision": revision,
                "goal_revision": controller.goal_revision,
            },
        )
    )


def splice_route(controller, route, native, body):
    """Unfinished native route plus a finished prefetch from its end, or None.

    The body accepts a replacement route in the same tick, so the motor keeps
    its speed instead of braking to a stop at the segment end. Only results the
    ordinary handoff would follow unchanged are spliced: dead ends, terrain
    work, gaps and committed steps keep the existing stop-and-decide path.
    """
    prefetch = getattr(controller, "prefetch", None)
    if (
        prefetch is None
        or tuple(prefetch.start) != tuple(route[-1])
        or controller.committed_step is not None
        or getattr(controller, "terrain", None) is not None
        or getattr(controller, "terrain_queue", None)
    ):
        return None
    result = prefetch.advance(
        node_limit=controller.search_nodes, budget_ms=controller.search_ms
    )
    if result.status not in {"segment", "reached", "incomplete"}:
        return None
    index = native.get("next_index", 1)
    if not 0 < index < len(route) or len(result.route) < 2:
        return None
    source = route[index - 1]
    active = route[index - 1 :]
    if math.hypot(body[0] - source[0] - 0.5, body[2] - source[2] - 0.5) > 1.2:
        return None  # Too far along for the motor's start check; retry next poll.
    joined = [tuple(p) for p in route[index - 1 :]] + [
        tuple(p) for p in result.route[1:]
    ]
    head = joined[:MOTOR_ROUTE_POINTS]
    if ordinary_route(head) != head:
        return None  # A gap: let the ordinary path decide.
    if flowing_cut(controller, head) is not None:
        return None  # offered_route cuts and reroutes at flowing water.
    if len(set(joined)) != len(joined):
        # Prefetches from stale snapshots can lead back over the active route.
        # A looping list never shrinks (route[index(node):]) and is re-offered
        # forever (Step C run 20261030T002908756029Z R1: 200+ A-B-A-B-A routes).
        return None
    # Planned edits stay in the controller's route; the motor stops before them.
    steps = {(s.source, s.target): s for s in result.steps}
    full = joined
    for index, pair in enumerate(zip(full, full[1:], strict=False)):
        if pair in steps:
            joined = full[: index + 1]
            break
    if len(joined) > MOTOR_ROUTE_POINTS:
        # Splice early, truncated to the motor cap, once under the lookahead;
        # the remainder stays in controller.route and is replanned from the new end.
        if remaining_ticks(active) >= PLANNING_TICK_LOOKAHEAD:
            return None
        joined = joined[:MOTOR_ROUTE_POINTS]
    if len(joined) < 2:
        return None
    controller.search, controller.prefetch = prefetch, None
    controller.prefetch_adopted += 1
    controller.last_result = result
    controller.route = list(full)
    controller.route_steps = steps
    controller.events.append(
        (
            "route_prefetch_adopted",
            {"start": tuple(prefetch.start), "goal_revision": controller.goal_revision},
        )
    )
    return joined


def publish_path(client, points):
    """In-game path marker (mcb-lab draws it) when the run asked for markers."""
    if getattr(client, "show_path", False) and len(points) >= 2:
        client.send("natural_path", points=[list(p) for p in points[:64]])


def inventory_totals(body):
    result = Counter()
    for row in body.get("inventory", []):
        result[row["item"]] += row.get("count", 0)
    return result


def confirm_step(plan, before, observation, fresh, report):
    """Native actuation is never enough: require fresh geometry and real stock debit."""
    placed = {tuple(row["position"]): row["item"] for row in report["placed_positions"]}
    broken = {tuple(p) for p in report["broken_positions"]}
    if not broken <= set(plan.breaks) or set(placed) != set(plan.places):
        raise ControllerFailure("Native step reported edits outside its plan")
    if any(not empty(fresh.get(p)) for p in plan.breaks):
        return False
    for point, item in placed.items():
        cell = fresh.get(point)
        if not full_cube(cell):
            return False
        if cell.get("block") != item:
            raise ControllerFailure("Native step placement material mismatch")
    expected = inventory_totals(before)
    actual = inventory_totals(observation["body"])
    initial_pickups = before.get("building_pickups")
    pickups = observation["body"].get("building_pickups")
    if placed and (initial_pickups is None or pickups is None):
        raise ControllerFailure(
            "Native step requires pickup ledger for stock confirmation"
        )
    for item, count in Counter(placed.values()).items():
        acquired = pickups.get(item, 0) - initial_pickups.get(item, 0)
        if acquired < 0:
            raise ControllerFailure("Native step pickup ledger changed generation")
        if actual[item] != expected[item] + acquired - count:
            return False
    return True


def submit_route(client, controller, stats, route):
    route_id = f"native-{stats['routes_submitted'] + 1}"
    stats["routes_submitted"] += 1
    request = client.send(
        "follow_route",
        route_id=route_id,
        route=[list(point) for point in route],
        movement_policy=controller.movement_policy.payload(),
    )
    client.acknowledge(request, "route_accepted")
    client.wait(
        lambda state: state.get("route_follower", {}).get("route_id") == route_id,
        timeout=5,
    )
    # Show the whole planned route, not only the segment handed over.
    publish_path(client, controller.route if len(controller.route) > 2 else route)
    return route_id


def drive_native(client, controller, wall_timeout):
    from mclab.control_loop import _fail, _running, record_selector

    deadline = time.monotonic() + wall_timeout
    active_id = None
    active_route = []
    active_step = None
    active_gap = None
    step_before = None
    confirmation_deadline = None
    goal_revision = getattr(controller, "goal_revision", 0)
    stats = {
        "profile": PROFILE,
        "routes_submitted": 0,
        "route_replans": 0,
        "steps_submitted": 0,
        "gaps_submitted": 0,
        "routes_spliced": 0,
    }
    client.last_controller_error = None
    try:
        while time.monotonic() < deadline:
            if getattr(controller, "cancelled", False):
                raise ControllerFailure("Native objective cancelled")
            revision = getattr(controller, "goal_revision", 0)
            if revision != goal_revision:
                request = client.send("cancel_route")
                client.acknowledge(request, "route_cancelled")
                client.record(
                    "native_goal_replaced",
                    {"old_revision": goal_revision, "new_revision": revision},
                )
                active_id = active_step = active_gap = None
                active_route = []
                confirmation_deadline = None
                goal_revision = revision
            observation = policy_state(client)
            terminal = _running(client, observation)
            if terminal is not None:
                return terminal
            if observation.get("reflex_follower", {}).get("state") == "following":
                controller._admit(observation)
                time.sleep(0.05)
                continue
            if active_gap is not None:
                controller._admit(observation)
                report = observation.get("gap_follower", {})
                if report.get("gap_id") != active_gap:
                    raise ControllerFailure(
                        "Native gap status lost its accepted identity"
                    )
                if report.get("state") == "following":
                    time.sleep(0.05)
                    continue
                if report.get("state") != "done":
                    raise ControllerFailure(
                        "Native gap failed: " + report.get("reason", "unknown")
                    )
                controller.gap_jump = None
                controller.gap_jumps_completed += 1
                controller.route.clear()
                controller.search = None
                controller.air_waypoint = None
                controller.motion_origin_height = None
                client.record("native_gap_finished", report)
                active_gap = None
            if active_step is not None:
                controller._admit(observation)
                report = observation.get("step_follower", {})
                if report.get("step_id") != active_step[0]:
                    raise ControllerFailure(
                        "Native step status lost its accepted identity"
                    )
                if report.get("state") == "following":
                    time.sleep(0.05)
                    continue
                if report.get("state") != "edits_ready":
                    # A refused or interrupted edit is a failed edge, not a failed
                    # attempt: never retry it, release the forecast, replan from
                    # fresh geometry (as the Python StepExecutor path does). Live:
                    # native_edit_stance_changed invalidated a whole attempt
                    # (run 20261030T012618920529Z R1).
                    plan = active_step[1]
                    controller.edits_broken += len(report.get("broken_positions", []))
                    controller.edits_placed += len(report.get("placed_positions", []))
                    controller.failed_edges.add((plan.source, plan.target))
                    controller.terrain = None
                    controller._clear_terrain_forecast()
                    controller.route.clear()
                    controller.search = None
                    client.record("native_step_failed", report)
                    active_step = None
                    confirmation_deadline = None
                    continue
                confirmation_deadline = confirmation_deadline or time.monotonic() + 2
                plan = active_step[1]
                if not confirm_step(
                    plan,
                    step_before,
                    observation,
                    controller._fresh(observation["tick"]),
                    report,
                ):
                    if time.monotonic() > confirmation_deadline:
                        raise ControllerFailure(
                            "Native step geometry/stock confirmation deadline"
                        )
                    time.sleep(0.05)
                    continue
                controller.edits_broken += len(report["broken_positions"])
                controller.edits_placed += len(report["placed_positions"])
                controller.own_footholds.update(plan.places)
                if plan.kind not in {"pillar", "downward"}:
                    # A pillar's (or dig-down's) StepMotor already moved the body:
                    # a vertical [source, target] route is refused by RouteMotor
                    # (Step C run 20261030T004308126362Z R4: route_edge_permission).
                    controller.committed_source = plan.source
                    controller.committed_step = plan.target
                    controller.commit_started = observation["tick"]
                controller.terrain = None
                controller.route.clear()
                controller.search = None
                client.record("native_step_confirmed", report)
                active_step = None
                confirmation_deadline = None
            native = observation.get("route_follower", {})
            if active_id is not None:
                if native.get("route_id") != active_id:
                    raise ControllerFailure(
                        "Native route status lost its accepted identity"
                    )
                # Acquisition continues while the native motor runs independently.
                controller._admit(observation)
                if native.get("state") == "following":
                    prefetch_while_following(controller, active_route)
                    joined = splice_route(
                        controller,
                        active_route,
                        native,
                        observation["body"]["position"],
                    )
                    if joined is None:
                        time.sleep(0.1)
                        continue
                    stats["routes_spliced"] += 1
                    active_id = submit_route(client, controller, stats, joined)
                    active_route = joined
                    continue
                client.record("native_route_finished", native)
                if native.get("state") != "done":
                    stats["route_replans"] += 1
                    # Edges the motor cannot drive: replan around them. Without
                    # the geometry case, run 20261030T194952532182Z resubmitted
                    # one rejected diagonal until the 5 s stall cutoff.
                    if native.get("reason") in {
                        "route_no_progress",
                        "route_swept_geometry_unknown_or_unsafe",
                        "route_drop_requires_water",
                    }:
                        index = native.get("next_index", 1)
                        route = active_route
                        if 0 < index < len(route):
                            controller.failed_edges.add(
                                (route[index - 1], route[index])
                            )
                    controller.route.clear()
                    controller.search = None
                    controller._discard_prefetch()
                controller.air_waypoint = None
                controller.motion_origin_height = None
                if controller.committed_step is not None:
                    controller.committed_step = None
                    if native.get("state") != "done" or not controller.terrain_queue:
                        controller._clear_terrain_forecast()
                active_id = None
            try:
                controller.decide(observation, native=True)
            finally:
                record_selector(client, controller)
            if controller.gap_jump is not None:
                gap_id = f"gap-{stats['gaps_submitted'] + 1}"
                stats["gaps_submitted"] += 1
                request = client.send(
                    "follow_gap",
                    gap_id=gap_id,
                    plan=asdict(controller.gap_jump.plan),
                    movement_policy=controller.movement_policy.payload(),
                )
                client.acknowledge(request, "gap_accepted")
                client.wait(
                    lambda state, accepted=gap_id: state.get("gap_follower", {}).get(
                        "gap_id"
                    )
                    == accepted
                )
                active_gap = gap_id
                continue
            if controller.terrain is not None and (
                controller.terrain.plan.breaks
                or controller.terrain.plan.places
                or controller.terrain.plan.kind != "step"
            ):
                plan, policy = controller.terrain.plan, controller.terrain.policy
                step_id = f"step-{stats['steps_submitted'] + 1}"
                stats["steps_submitted"] += 1
                step_before = observation["body"]
                terrain_policy = asdict(policy)
                terrain_policy["protected_positions"] = sorted(
                    set(policy.protected_positions) | controller.own_footholds
                )
                controller.own_footholds.update(plan.places)
                request = client.send(
                    "follow_step",
                    step_id=step_id,
                    plan=asdict(plan),
                    terrain_policy=terrain_policy,
                )
                client.acknowledge(request, "step_accepted")
                client.wait(
                    lambda state, accepted=step_id: state.get("step_follower", {}).get(
                        "step_id"
                    )
                    == accepted
                )
                active_step = (step_id, plan)
                publish_path(client, [plan.source, plan.target])
                continue
            route = (
                [controller.committed_source, controller.committed_step]
                if controller.committed_step is not None
                else offered_route(controller)
            )
            if len(route) >= 2 and controller.mode in {
                "following",
                "terrain_step_motion",
            }:
                body = observation["body"]["position"]
                if (
                    math.hypot(body[0] - route[0][0] - 0.5, body[2] - route[0][2] - 0.5)
                    > 1.5
                ):
                    raise ControllerFailure("Native route start displaced")
                active_id = submit_route(client, controller, stats, route)
                active_route = route
            time.sleep(0.05)
        raise ControllerFailure("Native route wall deadline exceeded")
    except (ControllerFailure, TimeoutError) as error:
        return _fail(client, error)
    finally:
        if (
            stats["routes_submitted"]
            + stats["steps_submitted"]
            + stats["gaps_submitted"]
        ):
            request = client.send("cancel_route")
            client.acknowledge(request, "route_cancelled")
        stats["timing_rejections"] = sum(
            event.get("type") in {"rejected", "rejected_at_execution"}
            and event.get("data", {}).get("reason")
            in {"late_or_distant_action", "stale_or_mistimed_action"}
            for event in getattr(client, "events", [])
        )
        client.control_loop_summary = stats
        client.record("control_loop_summary", stats)
