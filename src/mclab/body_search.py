"""Search run by the body's compiled planner core (ROADMAP item 8, stage E).

The body plans walking, swimming, gaps, ladders, water landings and unknown-space
exits (RoutePlanner.java, parity-checked against route_search.py). It has no edit
edges yet, so a search the body finds unreachable falls back to the Python search,
which can dig or build out.
"""

import itertools

from mclab.route_search import RouteResult, RouteSearch

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
            # Known graph exhausted: only an edit (dig, build) gets out, so Python
            # plans it. ponytail: no edit edges in Java yet (ROADMAP item 8, stage D).
            self.fallback = self._fallback_factory()
            self.token = getattr(self.fallback, "token", -1)
            return self.fallback.advance(**kwargs)
        result = RouteResult(
            report["status"],
            route,
            self.revision,
            self.expanded,
            f"body planner {report['status']}",
        )
        return RouteSearch._segment(result, SEGMENT)
