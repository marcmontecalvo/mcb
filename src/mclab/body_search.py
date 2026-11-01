"""Search run by the body's compiled planner core (ROADMAP item 8, stage E).

The body plans walking, swimming, gaps, ladders, water landings, unknown-space
exits and edit edges (RoutePlanner.java, parity-checked against route_search.py).
A search the body finds unreachable still falls back to the Python search.
"""

import itertools

from mclab.route_search import RouteResult, RouteSearch
from mclab.terrain_steps import StepPlan

BODY_NODE_LIMIT = 4000  # past the 3,000-node commit floor; ~10 ms of body time per tick
SEGMENT = 16  # same segment as BackgroundSearch
_ids = itertools.count(1)


def supported(terrain_policy, movement_policy, interact_reach=None):
    """True when the Java core implements this policy (else plan in Python)."""
    t, m = terrain_policy, movement_policy
    return bool(
        t is not None
        and t.planner_tick_costs
        and t.baritone_search
        and t.baritone_diagonals
        and t.heuristic_weight == 1
        and t.mob_coefficient == 1
        and not t.hazard_penalty
        and m.allow_shapes
        and m.allow_diagonals
        and interact_reach is None
    )


def plan_payload(controller, start, plan_id):
    t, m = controller._remaining_policy(), controller.movement_policy
    return {
        "plan_id": plan_id,
        "start": list(start),
        "goal": list(controller.goal),
        "radius": float(controller.radius),
        "max_drop": m.max_drop,
        "sprint": m.allow_sprint,
        "swim": bool(m.allow_swim and t.planner_swim),
        "extra": bool(t.baritone_moves),
        "max_gap": m.max_gap,
        "unknown_penalty": float(t.unknown_penalty) if t.planner_unknown else 0.0,
        "node_limit": BODY_NODE_LIMIT,
        "favored": [list(p) for p in controller.previous_route][:4096],
        "excluded": [[list(a), list(b)] for a, b in controller.failed_edges][:4096],
        **(
            {"edits": edits_payload(t, controller.inventory, controller.own_footholds)}
            if t.planner_edits and (t.allow_break or t.allow_place)
            else {}
        ),
    }


class BodySearch:
    """Same interface as BackgroundSearch: start, revision, token, advance()."""

    token = -1  # never matches a worker job, so planner_lane.cancel(token) is a no-op

    def __init__(self, controller, start, revision, fallback):
        self.controller = controller
        self.start = tuple(start)
        self.revision = revision
        self.expanded = 0
        self.elapsed_ms = 0
        self.plan_id = f"plan-{next(_ids)}"
        self._fallback_factory = fallback
        self.fallback = None
        controller.submit_plan(plan_payload(controller, self.start, self.plan_id))

    def advance(self, **kwargs):
        if self.fallback is not None:
            return self.fallback.advance(**kwargs)
        report = self.controller.plan_reports.get(self.plan_id)
        if report is None or report.get("state") != "done":
            return RouteResult(
                "searching", (self.start,), self.revision, 0, "body plan pending"
            )
        route = tuple(tuple(p) for p in report["route"])
        self.expanded = report["expanded"]
        if report["status"] == "unreachable" or len(route) < 2:
            # Known graph exhausted in the body: let the Python search confirm it.
            self.fallback = self._fallback_factory()
            self.token = getattr(self.fallback, "token", -1)
            return self.fallback.advance(**kwargs)
        steps = tuple(
            StepPlan(
                tuple(p["source"]),
                tuple(p["target"]),
                tuple(map(tuple, p["breaks"])),
                tuple(map(tuple, p["places"])),
                kind=p["kind"],
            )
            for p in report.get("steps", ())
        )
        result = RouteResult(
            report["status"],
            route,
            self.revision,
            self.expanded,
            f"body planner {report['status']}",
            steps,
        )
        return RouteSearch._segment(result, SEGMENT)


def edits_payload(policy, inventory, protected=()):
    """What the Java core needs for edit edges, from the policy and hotbar.

    Python keeps tool and supply rules (terrain_steps); the body gets the result:
    breakable blocks (tool in the hotbar), break ticks, placeable stock above the
    reserve, and every protected cell.
    """
    from mclab.terrain_steps import break_ticks, excavatable, hotbar

    rows = [dict(r) for r in inventory]
    tools = excavatable(policy)
    stock = sum(
        r.get("count", 0)
        for r in rows
        if r.get("item") in policy.material_allowlist and 0 <= r.get("slot", -1) < 9
    )
    return {
        "allow_break": policy.allow_break,
        "allow_place": policy.allow_place,
        "allow_bridge": policy.allow_bridge,
        "allow_pillar": policy.allow_pillar,
        "max_support_stack": policy.max_support_stack,
        "break_budget": policy.break_budget,
        "place_budget": policy.place_budget,
        "place_supply": max(0, stock - policy.reserve_blocks),
        "break_ticks": {block: break_ticks(block) for block in tools},
        "breakable": sorted(
            b for b, tool in tools.items() if hotbar(rows, tool) is not None
        ),
        "protected": sorted(
            {tuple(p) for p in protected}
            | {tuple(p) for p in policy.protected_positions}
        ),
        "protected_blocks": list(policy.protected_blocks),
        "material": policy.material_allowlist[0],
    }
