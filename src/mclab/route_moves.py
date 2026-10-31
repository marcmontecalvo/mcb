"""Baritone movement kinds added to the planner (flags baritone_moves, planner_swim).

Costs and rules are reimplemented from Baritone v1.20.0 (D12): MovementDownward,
MovementDescend (+ dynamicFallCost water branch), MovementPillar and the ladder
constants in ActionCosts. Edits yield a StepPlan, priced with MCB's measured
edit overheads in ``RouteSearch._edit_cost``. Unknown cells are never support
or air.

``extra_moves`` takes the search object ``s`` for its costs and policy.
"""

import math

from mclab import collision_geometry as geometry
from mclab import water_movement as water
from mclab.grid_cache import CLIMB, CLIMBABLE, EMPTY, FULL, SAFE, classify  # noqa: F401
from mclab.navigation import DIRECTIONS, empty, standable
from mclab.terrain_steps import (
    StepPlan,
    building_supply,
    cut_ok,
    excavatable,
)

LADDER_UP = 20 / 2.35  # ActionCosts.LADDER_UP_ONE_COST
LADDER_DOWN = 20 / 3.0  # ActionCosts.LADDER_DOWN_ONE_COST
LANDING_MAX = 20  # Baritone allows any fall into water; ours is bounded by the scan


def _rs():
    # route_search imports this module, so its constants are read lazily.
    from mclab import route_search

    return route_search


def _flags(cells):
    return getattr(cells, "cflags", None) or (lambda p: classify(cells.get(p)))


def climbable(cell):
    return (
        cell is not None
        and cell.get("block") in CLIMBABLE
        and cell.get("fluid", "minecraft:empty") == "minecraft:empty"
    )


def _passable(cell):
    return empty(cell) or climbable(cell)


def flowing_surface(cells, node):
    x, y, z = node
    return (
        int(y) == y
        and water.flowing_water(cells.get((x, y - 1, z)))
        and geometry.clear_column(cells, x, z, y, y + 2.5)
    )


def is_climb_only(cells, node):
    """Feet in a ladder/vine cell with no floor: reached only by climbing."""
    return bool(_flags(cells)(node) & CLIMB) and not standable(cells, node)


def _cuts(cells, points, policy, inventory, protected, cut_memo=None):
    """Blocks to break among points; None when any needed cut is not allowed."""
    tools = excavatable(policy)
    flags = getattr(cells, "cflags", None) or (lambda p: classify(cells.get(p)))
    cuts = []
    for point in dict.fromkeys(points):
        if flags(point) & EMPTY:
            continue
        if not cut_ok(cells, point, policy, inventory, protected, tools, cut_memo):
            return None
        cuts.append(point)
    return cuts if len(cuts) <= policy.break_budget else None


def prepare_downward(cells, source, policy, inventory, *, protected=(), cut_memo=None):
    """MovementDownward: break the floor, drop 1 onto a known solid y-2."""
    x, y, z = source
    protected = set(protected) | set(policy.protected_positions)
    if not policy.baritone_moves or y != math.floor(y) or not standable(cells, source):
        return None
    support = _flags(cells)((x, y - 2, z))
    if not (support & FULL and support & SAFE):
        return None
    cuts = _cuts(cells, [(x, y - 1, z)], policy, inventory, protected, cut_memo)
    if not cuts:
        return None
    return StepPlan(tuple(source), (x, y - 1, z), tuple(cuts), kind="downward")


def prepare_descend(
    cells, source, target, policy, inventory, *, protected=(), cut_memo=None
):
    """MovementDescend with breaks: clear target feet, head and the cell above."""
    x, y, z = source
    tx, ty, tz = target
    protected = set(protected) | set(policy.protected_positions)
    if (
        not policy.baritone_moves
        or abs(tx - x) + abs(tz - z) != 1
        or ty != y - 1
        or y != math.floor(y)
        or not standable(cells, source)
    ):
        return None
    floor = _flags(cells)((tx, ty - 1, tz))
    if not (floor & FULL and floor & SAFE):
        return None
    points = [(tx, ty, tz), (tx, y, tz), (tx, y + 1, tz)]
    cuts = _cuts(cells, points, policy, inventory, protected, cut_memo)
    if not cuts:
        return None
    return StepPlan(tuple(source), tuple(target), tuple(cuts), kind="descend")


def prepare_pillar_edge(
    cells, source, policy, inventory, *, protected=(), cut_memo=None
):
    """MovementPillar: place under the feet while jumping, rise 1 in place.

    y+2 and y+3 must be known air, as in prepare_pillar, so the jump arc is
    not clipped. ponytail: no head cut; the body's pillar step admits no breaks
    (StepMotor). A cut-then-pillar needs a body change first.
    """
    x, y, z = source
    protected = set(protected) | set(policy.protected_positions)
    if (
        not policy.baritone_moves
        or not policy.allow_pillar
        or not policy.allow_place
        or policy.place_budget < 1
        or y != math.floor(y)
        or not standable(cells, source)
        or tuple(source) in protected
        or not _flags(cells)((x, y + 2, z)) & EMPTY
        or not _flags(cells)((x, y + 3, z)) & EMPTY
        or building_supply(inventory, policy.reserve_blocks, policy.material_allowlist)
        is None
    ):
        return None
    return StepPlan(tuple(source), (x, y + 1, z), (), (tuple(source),), kind="pillar")


def swim_cost(s, cells, source, target):
    """Cost of a same-height water-surface edge, or None.

    Without planner_swim: the legacy edge (still water only, legacy constant).
    With it: SWIM_TICKS x water_cost_multiplier of the water cell, flowing
    water allowed.
    """
    shapes = s.movement_policy.allow_shapes
    base = _rs().SWIM if s.ticks else 1.8
    if not s.pswim:
        return base if water.water_edge(cells, source, target, shapes=shapes) else None
    flat = source[1] == target[1]
    if not flat or abs(source[0] - target[0]) + abs(source[2] - target[2]) != 1:
        return None

    def surface(node):
        if water.surface_node(cells, node) or flowing_surface(cells, node):
            return cells.get((node[0], node[1] - 1, node[2]))
        return None

    wet_source, wet_target = surface(source), surface(target)
    if wet_source is None and wet_target is None:
        return None
    for node in (source, target):
        if not (
            water.supported_node(cells, node, shapes=shapes)
            or flowing_surface(cells, node)
        ):
            return None
    return base * water.water_cost_multiplier(wet_target or wet_source)


def water_exit(s, cells, node, dx, dz):
    """Surface -> land +1 (planner_swim): jump out, priced like an ascend."""
    target = (node[0] + dx, node[1] + 1, node[2] + dz)
    if (
        s.pswim
        and (node, target) not in s.excluded
        and water.water_exit_up(cells, node, target)
    ):
        yield target, s._height(1), None


def extra_moves(s, cells, node):
    """(target, cost, StepPlan or None) for the baritone_moves edge kinds."""
    rs = _rs()
    yield from _ladder_moves(s, cells, node)
    x, y, z = node
    if y != math.floor(y) or not standable(cells, node):
        return
    kw = {
        "protected": s.protected,
        "cut_memo": getattr(s, "cut_memo", None),
    }
    plan = prepare_downward(cells, node, s.policy, s.inventory, **kw)
    if plan and (node, plan.target) not in s.excluded:
        # MovementDownward: fall 1 plus the break, no walk-off.
        base = rs.fall_ticks(1)
        yield plan.target, s._priced(plan, -1, cells, node, base=base), plan
    plan = prepare_pillar_edge(cells, node, s.policy, s.inventory, **kw)
    if plan and (node, plan.target) not in s.excluded:
        base = rs.JUMP + s._jump_penalty()
        yield plan.target, s._priced(plan, 1, cells, node, base=base), plan
    swim = s.movement_policy.allow_swim and (s.moves or s.pswim)
    for dx, dz in DIRECTIONS:
        target = (x + dx, y - 1, z + dz)
        if (node, target) not in s.excluded:
            plan = prepare_descend(cells, node, target, s.policy, s.inventory, **kw)
            if plan:
                yield target, s._priced(plan, -1, cells, node), plan
        if swim:
            yield from _landings(s, cells, node, dx, dz)


def _landings(s, cells, node, dx, dz):
    """MovementDescend.dynamicFallCost water branch: any drop into still water."""
    rs = _rs()
    x, y, z = node
    for height in range(1, LANDING_MAX + 1):
        target = (x + dx, y - height, z + dz)
        if (node, target) not in s.excluded and water.water_landing(
            cells, node, target, LANDING_MAX
        ):
            cost = rs.WALK_OFF + rs.fall_ticks(height) if s.ticks else 1 + 0.4 * height
            yield target, cost, None
        cell = cells.get(target)
        if (
            cell is None
            or cell.get("collision_boxes")
            or cell.get("fluid", "minecraft:empty") != "minecraft:empty"
        ):
            return


def _ladder_moves(s, cells, node):
    """Vertical edges in a climbable column, plus entries and exits."""
    x, y, z = node
    if y != math.floor(y):
        return
    fl = _flags(cells)
    open_bits = EMPTY | CLIMB
    if fl(node) & CLIMB:
        up, down = (x, y + 1, z), (x, y - 1, z)
        if (
            fl(up) & CLIMB
            and fl((x, y + 2, z)) & open_bits
            and (node, up) not in s.excluded
        ):
            yield up, LADDER_UP if s.ticks else 1.4, None
        if fl(down) & CLIMB and (node, down) not in s.excluded:
            yield down, LADDER_DOWN if s.ticks else 1.4, None
        for dx, dz in DIRECTIONS:
            for dy in (0, 1):
                target = (x + dx, y + dy, z + dz)
                if (node, target) not in s.excluded and standable(cells, target):
                    yield target, s._height(dy), None
    if not standable(cells, node):
        return
    for dx, dz in DIRECTIONS:
        for dy in (0, -1):
            target = (x + dx, y + dy, z + dz)
            if (
                not fl(target) & CLIMB
                or (node, target) in s.excluded
                or not fl((target[0], target[1] + 1, target[2])) & open_bits
                or not fl((target[0], y, target[2])) & open_bits
            ):
                continue
            yield target, s._height(dy), None
