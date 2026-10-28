"""Observed still-water surface nodes and fresh buoyancy/shore movement."""

import math

from mclab import collision_geometry
from mclab.navigation import rotation, standable


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


def can_surface(cells, body):
    p = body["position"]
    return collision_geometry.clear_body(
        cells, p[0], p[2], p[1], p[1] + 2.4, cell_safe=water_safe
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
