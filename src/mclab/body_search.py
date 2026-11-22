"""Search run by the body's compiled planner core (ROADMAP item 8, stages D and E).

The body plans walking, swimming, gaps, ladders, water landings, unknown-space exits
and the edit edges (dig/place steps, dig down, descend with breaks, pillar) in
RoutePlanner.java. Python sends the facts
that depend on the inventory and policy (edit_payload); the body returns each edit
edge's plan, which the controller executes like a Python-planned one.
"""

import itertools
import math
from collections import ChainMap
from dataclasses import asdict
from functools import cache

from mclab.grid_cache import FALLING
from mclab.pathing_grid import GridView, Section, offset_of
from mclab.planner_values import FALL_SETTLE_TICKS
from mclab.route_types import RouteResult, segment
from mclab.terrain_steps import (
    BREAK_AIM_TICKS,
    BREAK_EXTRA_TICKS,
    BUILD_BREAK_COST,
    TIER_SPEED,
    TOOL_TIERS,
    WIDE_EXCAVATABLE,
    Inventory,
    StepPlan,
    TerrainPolicy,
    _hotbar,
    break_ticks,
    excavatable,
    hand_ticks,
    pick_tool,
    tool_speed,
)

BODY_NODE_LIMIT = 4000  # past the 3,000-node commit floor; ~10 ms of body time per tick
# Dig plans: a broken stone costs ~158 ticks against a walking heuristic, so A* floods
# the walkable area first. Replays of run 20261113T155715976895Z reached 5-block digs
# at 4.9k-9.5k nodes with heuristic_weight 4 (34k+ at weight 1, same route cost).
EDIT_NODE_LIMIT = 10_000
BOXED_IN_NODES = 5  # a dead plan this small with banned exits is boxed in by them
# A forward search that ran out of nodes (``unreachable``) walked the body's whole
# region. One this small is a ledge the house closed around, not a floor (run
# 20261122T024835715542Z: a west eave 8 above the ground, 9 nodes); a closed floor
# of a storey is hundreds.
LEDGE_NODES = 32
# Live-terrain plans read the world within this (horizontal, vertical) radius of the
# body (LiveTerrain.java); None means the session's awareness radius.
LIVE_TERRAIN_RADIUS = None


def live_radius(controller):
    """(horizontal, vertical) for a live-terrain plan, or None to upload the map.

    Live only for unoccluded whole-section awareness ("chunk"/"sections": the body
    already sees every block in range) with remembered geometry; "current" memory
    keeps only fresh cells, so it keeps uploading that filtered snapshot.
    """
    memory = getattr(controller, "memory", None)
    if memory is None:
        return None
    awareness = memory.archive.identity.awareness
    if (
        awareness.preset not in {"chunk", "sections"}
        or memory.policy.memory_mode == "current"
        or not memory.policy.use_historical_geometry
    ):
        return None
    return LIVE_TERRAIN_RADIUS or (
        awareness.horizontal_radius_blocks,
        awareness.vertical_radius_blocks,
    )


SEGMENT = 16  # same segment as BackgroundSearch
_ids = itertools.count(1)
_AIR = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}
_SOLID = {
    "block": "minecraft:cobblestone",
    "fluid": "minecraft:empty",
    "collision_boxes": [[0, 0, 0, 1, 1, 1]],
}
_SCAFFOLD = {
    "block": "minecraft:scaffolding",
    "fluid": "minecraft:empty",
    "collision_boxes": [],
}


def predicted_cells(cells, steps):
    """``cells`` with steps' breaks made air and places made solid or scaffolding.

    A GridView stays one: only the touched sections are copied, so the map encoder
    resends just those and the next real snapshot restores the true cells. Other
    maps become a layered lookup (read-only, like the original)."""
    edits = [(p, _AIR) for s in steps for p in s.breaks]
    for s in steps:
        scaffolds = set(getattr(s, "scaffold", ()))
        for p in s.places:
            edits.append((p, _SCAFFOLD if p in scaffolds else _SOLID))
    if not isinstance(cells, GridView):
        return ChainMap({tuple(p): dict(f) for p, f in edits}, cells)
    grid, sections, copied = cells.grid, dict(cells.sections), set()
    for p, facts in edits:
        x, y, z = p
        key = (x >> 4, y >> 4, z >> 4)
        if key not in copied:
            old = sections.get(key)
            sections[key] = Section(grid.generation, old)
            copied.add(key)
        section, offset = sections[key], offset_of(x, y, z)
        if not section.states[offset]:
            section.known += 1
        section.states[offset] = grid.state_id(facts)
    return GridView(grid, sections, frozen=True)


@cache
def _break_cost(block, tool=True, speed=None):
    if tool and speed is not None and speed < 0:  # held, but cannot harvest
        ticks = max(1, math.ceil(WIDE_EXCAVATABLE[block][0] * 100 / -speed))
    else:
        ticks = break_ticks(block, speed) if tool else hand_ticks(block)
    return ticks + BREAK_EXTRA_TICKS + BREAK_AIM_TICKS


def _unsuited_swing(inventory, tool):
    """(tool?, speed) the motor really swings at a block no held tool harvests: the
    best hotbar tool of the kind (StepMotor.breakSlot) at 1/100 damage, because it
    cannot harvest, not the iron speed the table names (a wooden pickaxe on iron ore
    was priced 23 ticks, takes about 150)."""
    kind = tool.removeprefix("minecraft:iron_")
    for tier in TOOL_TIERS:
        if _hotbar(inventory, f"minecraft:{tier}_{kind}") is not None:
            return True, -TIER_SPEED[tier]  # negative speed: cannot harvest
    return False, None


def tool_map(policy, inventory, tool_choice="lowest_sufficient"):
    """block -> hotbar tool item the motor should break it with (StepMotor), only for
    blocks where a sufficient tool is held; the others keep the motor's own search."""
    inventory = Inventory(dict(r) for r in inventory)
    picks = ((b, pick_tool(inventory, b, tool_choice)) for b in excavatable(policy))
    return {b: t for b, t in picks if t}


def edit_payload(policy, inventory, protected, tool_choice="lowest_sufficient"):
    """Edit permissions and prices for RoutePlanner.EditPolicy.

    break_costs lists every excavatable block that is not protected, priced with
    its tool when one is on the hotbar, else at bare-hand speed: dirt over stone
    with no shovel walled the bot in (run 20261113T024449677184Z), and a bot whose
    tools all broke must still dig out to make more. The tool is the one
    ``tool_choice`` picks (edits["tools"]), priced at its tier's speed. place_supply
    is how many blocks can be placed in a row (stack_supply), at most the support
    stack height.
    """
    tools = tool_map(policy, inventory, tool_choice)
    inventory = Inventory(dict(r) for r in inventory)
    breaks = {}
    if policy.allow_break:
        for block, tool in excavatable(policy).items():
            if block in policy.protected_blocks:
                continue
            if block in tools:
                breaks[block] = _break_cost(block, True, tool_speed(tools[block]))
            else:
                breaks[block] = _break_cost(block, *_unsuited_swing(inventory, tool))
    # The true stock of every allowed building block held, in any slot (the control
    # loop brings one to the hotbar when a step uses it). The old count stopped at the
    # support stack height and saw only the first material: a bot holding cobblestone
    # was sent place_supply 0 and could not pillar out of a hole.
    # The allowlist is in preference order (mclab.temporary_blocks: dirt, cobblestone,
    # then spare plain blocks); the motor places the first one it holds.
    held = {}
    for row in inventory:
        if row.get("item") in policy.material_allowlist:
            held[row["item"]] = held.get(row["item"], 0) + row.get("count", 0)
    materials = [m for m in policy.material_allowlist if held.get(m, 0) > 0]
    # Scaffolding is chosen per placed cell by the planner (scaffold_supply), never by
    # order: it needs support from below or a chain of at most 6 from one.
    scaffold_held = sum(
        row.get("count", 0)
        for row in inventory
        if row.get("item") in policy.scaffold_materials
    )
    # The reserve holds back solid blocks (the last ones, kept for an emergency
    # pillar), never scaffolding: Java's solidSupply is place_supply - scaffold_supply.
    supply = max(0, sum(held.values()) - policy.reserve_blocks) + scaffold_held
    cells = set(map(tuple, protected)) | set(policy.protected_positions)
    # Zero supply is a price, not a toggle: with nothing to place the planner must not
    # explore place edges it cannot pay for (502 plans ran out at 4,000 nodes, run
    # 20261116T224011482858Z). Breaks stay as permitted.
    can_place = supply > 0
    return {
        "step": bool(
            policy.planner_edits
            and (policy.allow_break or (policy.allow_place and can_place))
        ),
        "break_costs": breaks,
        **({"tools": tools} if tools else {}),
        "fall_costs": {
            b: break_ticks(b) + BREAK_EXTRA_TICKS + FALL_SETTLE_TICKS
            for b in sorted(FALLING)
            if b in WIDE_EXCAVATABLE
        },
        "allow_place": policy.allow_place and can_place,
        "allow_bridge": policy.allow_bridge and can_place,
        "allow_pillar": policy.allow_pillar and can_place,
        "max_stack": policy.max_support_stack,
        "break_budget": policy.break_budget,
        "place_budget": policy.place_budget,
        "place_supply": supply,
        **({"scaffold_supply": scaffold_held} if scaffold_held else {}),
        "protected": sorted([int(v) for v in p] for p in cells),
        "costly": [[*p, BUILD_BREAK_COST] for p in sorted(policy.costly_positions)],
        "protected_blocks": list(policy.protected_blocks),
        # "materials": the whole list, most held first; "material" stays its head for
        # a body that reads only the single field.
        "materials": materials,
        "material": materials[0]
        if materials
        else policy.material_allowlist[0]
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
        "radius": float(getattr(controller, "planner_radius", controller.radius)),
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
        "node_limit": EDIT_NODE_LIMIT if t.allow_break else BODY_NODE_LIMIT,
        "favored": [list(p) for p in controller.previous_route],
        "excluded": [[list(a), list(b)] for a, b in controller.failed_edges],
        "edits": edit_payload(
            t,
            controller.inventory,
            controller.own_footholds,
            getattr(controller, "tool_choice", "lowest_sufficient"),
        ),
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
        tuple(_point(p) for p in report.get("scaffold", ())),
    )


class BodySearch:
    """Same interface as BackgroundSearch: start, revision, token, advance()."""

    token = -1  # never matches a worker job, so planner_lane.cancel(token) is a no-op

    def __init__(self, controller, start, revision, cells=None, edits=()):
        self.controller = controller
        self.start = tuple(start)
        self.revision = revision
        self.expanded = 0
        self.elapsed_ms = 0
        self.plan_id = f"plan-{next(_ids)}"
        self.superseded = False
        self.submitted = False
        # Planned edits not yet made: this plan starts past them, on a map where they
        # are done (predicted_cells). The body's live world lacks them, so it uploads.
        self.edits = tuple(edits)
        live = None if self.edits else live_radius(controller)
        # A live plan reads the world in the body; nothing is uploaded for it.
        self.map_transfer = None if live else getattr(controller, "planner_map", None)
        if self.map_transfer is not None:
            if cells is None:
                raise ValueError("body search requires a planning snapshot")
            self.map_transfer.begin(self, cells)
        else:
            payload = self.payload()
            if live:
                payload.update(terrain="live", terrain_radius=list(live))
            controller.submit_plan(payload)
            self.submitted = True

    def payload(self):
        payload = plan_payload(self.controller, self.start, self.plan_id)
        if self.edits:  # the blocks the pending steps place are not to be broken
            places = [list(p) for s in self.edits for p in s.places]
            protected = payload["edits"]["protected"]
            payload["edits"]["protected"] = protected + [
                p for p in places if p not in protected
            ]
        return payload

    def _unban_start(self, status):
        """A plan that died after a few nodes with banned exits from the start cell is
        boxed in by its own bans: lift them once so the replan can try (run
        20261113T172130173999Z: 25 'stuck' gotos in a pit, expanded=2)."""
        c = self.controller
        edges = getattr(c, "failed_edges", ())
        lifted = [e for e in edges if tuple(e[0]) == self.start]
        dead = status in ("unreachable", "incomplete")
        dead = dead and self.expanded <= BOXED_IN_NODES
        if dead and lifted:
            c.failed_edges.difference_update(lifted)
            event = {"start": self.start, "edges": len(lifted)}
            c.events.append(("failed_edges_cleared", event))

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
        self._unban_start(report["status"])
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
            "radius": float(getattr(c, "planner_radius", c.radius)),
            "max_drop": c.movement_policy.max_drop,
            "horizon": 4,
            "beam_width": 24,
            "node_limit": c.search_nodes,
            "policy": asdict(self.terrain_policy),
            "inventory": list(self.terrain_inventory),
            "protected": [list(p) for p in c.own_footholds],
            "excluded": [[list(a), list(b)] for a, b in c.failed_edges],
        }
