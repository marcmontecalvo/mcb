"""Search run by the body's compiled planner core (ROADMAP item 8, stages D and E).

The body plans walking, swimming, gaps, ladders, water landings, unknown-space exits
and the edit edges (dig/place steps, dig down, descend with breaks, pillar) in
RoutePlanner.java, parity-checked against route_search.py. Python sends the facts
that depend on the inventory and policy (edit_payload); the body returns each edit
edge's plan, which the controller executes like a Python-planned one.
"""

import itertools

from mclab.grid_cache import FALLING
from mclab.route_search import FALL_SETTLE_TICKS, RouteResult, RouteSearch, _break_cost
from mclab.terrain_steps import (
    BREAK_EXTRA_TICKS,
    WIDE_EXCAVATABLE,
    Inventory,
    StepPlan,
    break_ticks,
    excavatable,
    hotbar,
    stack_supply,
)

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


def edit_payload(policy, inventory, protected):
    """Edit permissions and prices for RoutePlanner.EditPolicy (rules of plan_edit).

    break_costs lists only blocks whose tool is on the hotbar and that are not
    protected (terrain_steps.cut_allowed); place_supply is how many blocks can be
    placed in a row (stack_supply), at most the support stack height.
    """
    inventory = Inventory(dict(r) for r in inventory)
    breaks = {}
    if policy.allow_break:
        for block, tool in excavatable(policy).items():
            if (
                block not in policy.protected_blocks
                and hotbar(inventory, tool) is not None
            ):
                breaks[block] = _break_cost(block)
    supply = 0
    while supply < policy.max_support_stack and stack_supply(
        inventory, supply + 1, policy.reserve_blocks, policy.material_allowlist
    ):
        supply += 1
    cells = set(map(tuple, protected)) | set(policy.protected_positions)
    return {
        "step": bool(
            policy.planner_edits and (policy.allow_break or policy.allow_place)
        ),
        "break_costs": breaks,
        "fall_costs": {
            b: break_ticks(b) + BREAK_EXTRA_TICKS + FALL_SETTLE_TICKS
            for b in sorted(FALLING)
            if b in WIDE_EXCAVATABLE
        },
        "allow_place": policy.allow_place,
        "allow_bridge": policy.allow_bridge,
        "allow_pillar": policy.allow_pillar,
        "max_stack": policy.max_support_stack,
        "break_budget": policy.break_budget,
        "place_budget": policy.place_budget,
        "place_supply": supply,
        "protected": sorted([int(v) for v in p] for p in cells),
        "protected_blocks": list(policy.protected_blocks),
        "material": policy.material_allowlist[0],
    }


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
        "edits": edit_payload(t, controller.inventory, controller.own_footholds),
    }


def step_plan(report):
    """A body-planned edit edge as the StepPlan the controller executes."""
    return StepPlan(
        tuple(report["source"]),
        tuple(report["target"]),
        tuple(tuple(p) for p in report["breaks"]),
        tuple(tuple(p) for p in report["places"]),
        report["kind"],
    )


class BodySearch:
    """Same interface as BackgroundSearch: start, revision, token, advance()."""

    token = -1  # never matches a worker job, so planner_lane.cancel(token) is a no-op

    def __init__(self, controller, start, revision):
        self.controller = controller
        self.start = tuple(start)
        self.revision = revision
        self.expanded = 0
        self.elapsed_ms = 0
        self.plan_id = f"plan-{next(_ids)}"
        controller.submit_plan(plan_payload(controller, self.start, self.plan_id))

    def advance(self, **kwargs):
        report = self.controller.plan_reports.get(self.plan_id)
        if report is None or report.get("state") != "done":
            return RouteResult(
                "searching", (self.start,), self.revision, 0, "body plan pending"
            )
        self.expanded = report["expanded"]
        result = RouteResult(
            report["status"],
            tuple(tuple(p) for p in report["route"]),
            self.revision,
            self.expanded,
            f"body planner {report['status']}",
            tuple(step_plan(s) for s in report.get("steps", ())),
        )
        return RouteSearch._segment(result, SEGMENT)
