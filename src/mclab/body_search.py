"""Search run by the body's compiled planner core (ROADMAP item 8, stages D and E).

The body plans walking, swimming, gaps, ladders, water landings, unknown-space exits
and the edit edges (dig/place steps, dig down, descend with breaks, pillar) in
RoutePlanner.java, parity-checked against route_search.py. Python sends the facts
that depend on the inventory and policy (edit_payload); the body returns each edit
edge's plan, which the controller executes like a Python-planned one.
"""

import itertools
from dataclasses import asdict
from functools import cache

from mclab.grid_cache import FALLING
from mclab.planner_values import FALL_SETTLE_TICKS
from mclab.route_types import RouteResult, segment
from mclab.terrain_steps import (
    BREAK_AIM_TICKS,
    BREAK_EXTRA_TICKS,
    WIDE_EXCAVATABLE,
    Inventory,
    StepPlan,
    TerrainPolicy,
    break_ticks,
    excavatable,
    hotbar,
    stack_supply,
)

BODY_NODE_LIMIT = 4000  # past the 3,000-node commit floor; ~10 ms of body time per tick
SEGMENT = 16  # same segment as BackgroundSearch
_ids = itertools.count(1)


@cache
def _break_cost(block):
    return break_ticks(block) + BREAK_EXTRA_TICKS + BREAK_AIM_TICKS


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
        "material": policy.material_allowlist[0]
        if policy.material_allowlist
        else "minecraft:cobblestone",
        **(
            {}  # absent: the body's default (allowed), so old requests are unchanged
            if policy.allow_falling_neighbours
            else {"falling_neighbours": False}
        ),
        **({"doors": True} if policy.use_doors else {}),
        **({} if policy.climb_vines else {"vines": False}),
    }


def plan_payload(controller, start, plan_id):
    t = (
        controller._remaining_policy()
        if controller.terrain_policy is not None
        else TerrainPolicy(allow_break=False, allow_place=False)
    )
    m = controller.movement_policy
    return {
        "plan_id": plan_id,
        "start": list(start),
        "goal": list(controller.goal),
        "radius": float(controller.radius),
        **(
            {"interact_reach": controller.interact_reach}
            if controller.interact_reach is not None
            else {}
        ),
        "avoid_stances": [
            list(p) for p in getattr(controller, "avoid_goal_stances", ())
        ],
        "max_drop": m.max_drop,
        "sprint": m.allow_sprint,
        "swim": m.allow_swim,
        "planner_swim": t.planner_swim,
        "extra": bool(t.baritone_moves),
        "allow_climb": m.allow_climb,
        "tick_costs": t.planner_tick_costs,
        "baritone_search": t.baritone_search,
        "baritone_diagonals": t.baritone_diagonals,
        "allow_diagonals": m.allow_diagonals,
        "allow_shapes": m.allow_shapes,
        "heuristic_weight": t.heuristic_weight,
        "break_penalty": t.break_penalty,
        "placement_penalty": t.placement_penalty,
        "tool_wear_penalty": t.tool_wear_penalty,
        "hazard_penalty": t.hazard_penalty,
        "mob_coefficient": t.mob_coefficient,
        "mob_radius": t.mob_radius,
        "threats": [list(p) for p in controller.threats],
        "max_gap": m.max_gap,
        "unknown_penalty": float(t.unknown_penalty) if t.planner_unknown else 0.0,
        "node_limit": BODY_NODE_LIMIT,
        "favored": [list(p) for p in controller.previous_route],
        "excluded": [[list(a), list(b)] for a, b in controller.failed_edges],
        "edits": edit_payload(t, controller.inventory, controller.own_footholds),
    }


def _point(values):
    return tuple(
        int(value) if isinstance(value, float) and value.is_integer() else value
        for value in values
    )


def step_plan(report):
    """A body-planned edit edge as the StepPlan the controller executes."""
    return StepPlan(
        _point(report["source"]),
        _point(report["target"]),
        tuple(_point(p) for p in report["breaks"]),
        tuple(_point(p) for p in report["places"]),
        report["kind"],
        _point(report["exit"]) if report.get("exit") is not None else None,
    )


class BodySearch:
    """Same interface as BackgroundSearch: start, revision, token, advance()."""

    token = -1  # never matches a worker job, so planner_lane.cancel(token) is a no-op

    def __init__(self, controller, start, revision, cells=None):
        self.controller = controller
        self.start = tuple(start)
        self.revision = revision
        self.expanded = 0
        self.elapsed_ms = 0
        self.plan_id = f"plan-{next(_ids)}"
        self.superseded = False
        self.submitted = False
        self.map_transfer = getattr(controller, "planner_map", None)
        if self.map_transfer is not None:
            if cells is None:
                raise ValueError("body search requires a planning snapshot")
            self.map_transfer.begin(self, cells)
        else:
            controller.submit_plan(self.payload())
            self.submitted = True

    def payload(self):
        return plan_payload(self.controller, self.start, self.plan_id)

    def cancel(self):
        if self.superseded:
            return
        try:
            if self.map_transfer is not None:
                self.map_transfer.cancel(self)
        finally:
            self.superseded = True
            if (
                self.submitted
                and getattr(self.controller, "cancel_plan", None) is not None
            ):
                self.controller.cancel_plan(self.plan_id)

    def advance(self, **kwargs):
        if self.superseded:
            raise RuntimeError("body search was superseded")
        if not self.submitted:
            generation = self.map_transfer.advance(self)
            if generation is None:
                return RouteResult(
                    "searching", (self.start,), self.revision, 0, "planner map transfer"
                )
            transfer = getattr(self.map_transfer, "transfer", None)
            if getattr(transfer, "recovered", False):
                self.controller.events.append(
                    ("planner_map_ack_recovered", {"generation": generation})
                )
            payload = self.payload()
            payload["map_generation"] = generation
            self.controller.submit_plan(payload)
            self.submitted = True
        report = self.controller.plan_reports.get(self.plan_id)
        if report is None or report.get("state") != "done":
            return RouteResult(
                "searching", (self.start,), self.revision, 0, "body plan pending"
            )
        self.expanded = report["expanded"]
        result = RouteResult(
            report["status"],
            tuple(_point(p) for p in report["route"]),
            self.revision,
            self.expanded,
            report.get("reason", f"body planner {report['status']}"),
            tuple(step_plan(s) for s in report.get("steps", ())),
        )
        return segment(result, SEGMENT)


class BodyTerrainSearch(BodySearch):
    """The legacy bounded terrain forecast, executed by the body."""

    def __init__(self, controller, start, revision, cells, policy, inventory):
        self.terrain_policy = policy
        self.terrain_inventory = tuple(dict(row) for row in inventory)
        super().__init__(controller, start, revision, cells)

    def payload(self):
        c = self.controller
        return {
            "mode": "terrain",
            "plan_id": self.plan_id,
            "start": list(self.start),
            "goal": list(c.goal),
            "radius": float(c.radius),
            "max_drop": c.movement_policy.max_drop,
            "horizon": 4,
            "beam_width": 24,
            "node_limit": c.search_nodes,
            "policy": asdict(self.terrain_policy),
            "inventory": list(self.terrain_inventory),
            "protected": [list(p) for p in c.own_footholds],
            "excluded": [[list(a), list(b)] for a, b in c.failed_edges],
        }
