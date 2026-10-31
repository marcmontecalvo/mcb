"""Observed still-water surface nodes and fresh buoyancy/shore movement."""

import math

from mclab import collision_geometry
from mclab.navigation import rotation, standable

# Baritone ActionCosts.WALK_ONE_IN_WATER_COST (20 / 2.2 ticks per block) and
# Settings.walkOnWaterOnePenalty.
SWIM_TICKS = 20 / 2.2
WALK_ON_WATER_PENALTY = 3.0
FLOWING_WATER_MULTIPLIER = 1.5
EYE = 1.62
MAX_SURFACE_SCAN = 40


def still_water(cell):
    return (
        cell is not None
        and cell.get("fluid") == "minecraft:water"
        and cell.get("collision_boxes") == []
        and cell.get("properties", {}).get("level", "0") == "0"
        and cell.get("block") not in collision_geometry.HAZARDS
    )


def water_safe(cell):
    return collision_geometry.safe_cell(cell) or still_water(cell)


def surface_node(cells, node):
    x, y, z = node
    return (
        type(y) in (int, float)
        and math.isfinite(y)
        and int(y) == y
        and still_water(cells.get((x, y - 1, z)))
        and collision_geometry.clear_column(cells, x, z, y, y + 2.5)
    )


def surface_height(cells, position):
    x, y, z = math.floor(position[0]), math.floor(position[1]), math.floor(position[2])
    return next(
        (h for h in range(y - 1, y + 9) if surface_node(cells, (x, h, z))), None
    )


def supported_node(cells, node, *, shapes=False):
    if surface_node(cells, node):
        return True
    predicate = collision_geometry.standable if shapes else standable
    return predicate(cells, node) and collision_geometry.safe_cell(
        cells.get((node[0], math.ceil(node[1]) - 1, node[2]))
    )


def water_edge(cells, source, target, *, shapes=False):
    return (
        source[1] == target[1]
        and abs(source[0] - target[0]) + abs(source[2] - target[2]) == 1
        and (surface_node(cells, source) or surface_node(cells, target))
        and supported_node(cells, source, shapes=shapes)
        and supported_node(cells, target, shapes=shapes)
    )


def _passable(cell):
    """Known, collision-free air or non-hazard water (still or flowing)."""
    return (
        cell is not None
        and cell.get("collision_boxes") == []
        and cell.get("block") not in collision_geometry.HAZARDS
        and cell.get("fluid", "minecraft:empty")
        in ("minecraft:empty", "minecraft:water")
    )


def _breathable(cell):
    return _passable(cell) and cell.get("fluid", "minecraft:empty") == "minecraft:empty"


def flowing_water(cell):
    return (
        cell is not None
        and cell.get("fluid") == "minecraft:water"
        and cell.get("collision_boxes") == []
        and cell.get("properties", {}).get("level", "0") != "0"
        and cell.get("block") not in collision_geometry.HAZARDS
    )


def water_cost_multiplier(cell):
    """Swim-cost multiplier: 1 still, 1.5 flowing, None when not swimmable."""
    # ponytail: no push direction yet; model current vector when flow matters.
    if still_water(cell):
        return 1.0
    return FLOWING_WATER_MULTIPLIER if flowing_water(cell) else None


def head_out_of_water(survival):
    """True when the eyes are above water (eye_in_water, else in_water)."""
    flag = survival.get("eye_in_water")
    if type(flag) is not bool:
        flag = survival.get("in_water")
    return flag is False


def _air_above(cells, x, z, y):
    """Cell y of the first breathable cell straight up from cell y, or None."""
    for h in range(y, y + MAX_SURFACE_SCAN):
        cell = cells.get((x, h, z))
        if _breathable(cell):
            return h
        if not _passable(cell):
            return None
    return None


def _column_open(cells, px, pz, py):
    air = _air_above(cells, math.floor(px), math.floor(pz), math.floor(py + EYE))
    return air is not None and collision_geometry.clear_body(
        cells, px, pz, py, air, cell_safe=_passable
    )


def can_surface(cells, body):
    """Straight up to known open air (not a fixed 2.4 blocks)."""
    p = body["position"]
    return _column_open(cells, p[0], p[2], p[1])


def _air_route(cells, body, max_steps):
    """Shortest feet-cell path to a cell whose eye cell is breathable (BFS)."""
    p = body["position"]
    start = (math.floor(p[0]), math.floor(p[1]), math.floor(p[2]))
    seen, frontier = {start: None}, [start]
    for _ in range(max_steps + 1):
        nxt = []
        for node in frontier:
            x, y, z = node
            if _breathable(cells.get((x, y + 1, z))):
                path = []
                while node is not None:
                    path.append(node)
                    node = seen[node]
                return path[::-1]
            for d in (
                (1, 0, 0),
                (-1, 0, 0),
                (0, 0, 1),
                (0, 0, -1),
                (0, 1, 0),
                (0, -1, 0),
            ):
                n = (x + d[0], y + d[1], z + d[2])
                if (
                    n not in seen
                    and _passable(cells.get(n))
                    and _passable(cells.get((n[0], n[1] + 1, n[2])))
                ):
                    seen[n] = node
                    nxt.append(n)
        frontier = nxt
    return None


def air_recovery_command(cells, body, policy):
    """Survival reflex to get the head into air; {} when no way is known.

    Decision (DECISIONS.md, swimming reflex): runs even when allow_swim is
    false, but then only surfaces straight up or steps to one adjacent column.
    With allow_swim it may swim to the nearest breathable cell within the
    remaining air (SWIM_TICKS per block).
    """
    survival = body.get("survival", {})
    air, maximum = survival.get("air"), survival.get("max_air")
    if type(air) is not int or type(maximum) is not int or not 0 <= air <= maximum:
        return {}
    if head_out_of_water(survival) or can_surface(cells, body):
        return {} if head_out_of_water(survival) else {"jump": True}
    p = body["position"]
    if policy.allow_swim:
        path = _air_route(cells, body, int(air / SWIM_TICKS) + 1)
        steps = path[1:2] if path else []
    else:
        steps = [
            (math.floor(p[0]) + dx, math.floor(p[1]), math.floor(p[2]) + dz)
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
            if _column_open(
                cells, math.floor(p[0]) + dx + 0.5, math.floor(p[2]) + dz + 0.5, p[1]
            )
        ][:1]
    if not steps:
        return {}
    nx, ny, nz = steps[0]
    waypoint = [nx + 0.5, ny, nz + 0.5]
    if (nx, nz) == (math.floor(p[0]), math.floor(p[2])):
        return {"jump": True}
    turn = rotation(waypoint, body, ground=True)
    if abs(turn["yaw_delta"]) > 8:
        return {**turn, "jump": True}
    return {**turn, "forward": 1.0, "jump": ny >= math.floor(p[1])}


def water_exit_up(cells, source, target):
    """Water surface node -> adjacent land node one block higher (jump out)."""
    return (
        target[1] == source[1] + 1
        and abs(source[0] - target[0]) + abs(source[2] - target[2]) == 1
        and surface_node(cells, source)
        and not surface_node(cells, target)
        and standable(cells, target)
        and collision_geometry.safe_cell(
            cells.get((target[0], target[1] - 1, target[2]))
        )
        and collision_geometry.clear_column(
            cells, source[0], source[2], source[1], target[1] + 1.8
        )
    )


def water_landing(cells, source, target, max_height):
    """Land node -> adjacent still-water surface node below (Baritone
    MovementDescend.dynamicFallCost: water cancels fall damage at any height).

    Needs a known solid safe floor under an all-still water column.
    """
    drop = source[1] - target[1]
    if (
        not 1 <= drop <= max_height
        or abs(source[0] - target[0]) + abs(source[2] - target[2]) != 1
        or not standable(cells, source)
        or not surface_node(cells, target)
        or not collision_geometry.clear_column(
            cells, target[0], target[2], target[1], source[1] + 1.8
        )
    ):
        return False
    y = target[1] - 1
    while still_water(cells.get((target[0], y, target[2]))):
        y -= 1
        if y < target[1] - MAX_SURFACE_SCAN:
            return False
    floor = cells.get((target[0], y, target[2]))
    return (
        y < target[1] - 1
        and collision_geometry.safe_cell(floor)
        and bool(floor.get("collision_boxes"))
    )


def swim_command(cells, body, waypoint, policy):
    if not policy.allow_swim:
        return {}
    survival = body.get("survival", {})
    if type(survival.get("in_water")) is not bool:
        return {}
    if (
        type(survival.get("air")) is not int
        or type(survival.get("max_air")) is not int
        or not 0 <= survival["air"] <= survival["max_air"]
        or survival["max_air"] < 1
    ):
        return {}
    p = body["position"]
    height = surface_height(cells, p)
    target = (math.floor(waypoint[0]), waypoint[1], math.floor(waypoint[2]))
    if height is None:
        height = p[1]
    if not supported_node(cells, target, shapes=policy.allow_shapes):
        return {}
    if survival["in_water"] and p[1] < height - 0.8:
        return {"jump": True} if can_surface(cells, body) else {}
    turn = rotation(waypoint, body, ground=True)
    if abs(turn["yaw_delta"]) > 8:
        return {**turn, "jump": survival["in_water"]}
    distance = math.hypot(waypoint[0] - p[0], waypoint[2] - p[2])
    forward = min(1.0, max(0.12, distance / 1.8))
    yaw = math.radians(body["yaw"])
    horizon = min(distance + 0.05, 1.8 * forward)
    radius = collision_geometry.BODY_RADIUS - collision_geometry.EPS
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        x, z = (
            p[0] - math.sin(yaw) * horizon * fraction,
            p[2] + math.cos(yaw) * horizon * fraction,
        )
        dry = False
        for ox in (-radius, radius):
            for oz in (-radius, radius):
                node = (math.floor(x + ox), height, math.floor(z + oz))
                if not supported_node(cells, node, shapes=policy.allow_shapes):
                    return {}
                dry |= not surface_node(cells, node)
        bottom = max(p[1], height) if dry else p[1]
        if not collision_geometry.clear_body(
            cells, x, z, bottom, bottom + 2.4, cell_safe=water_safe
        ):
            return {}
    return {**turn, "forward": forward, "jump": survival["in_water"]}
