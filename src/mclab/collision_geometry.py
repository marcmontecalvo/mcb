"""Development slab/stair geometry; unknown cells never provide clearance.

Nodes remain block-column centers with standing heights at observed collision
surfaces. This deliberately excludes fences, doors and other complex machinery.
Legacy full-cube predicates and qualification controllers remain unchanged.
"""

import math

from mclab.grid_cache import HAZARDS
from mclab.navigation import full_cube

EPS = 1e-7
BODY_RADIUS = 0.3


def safe_cell(cell):
    return (
        cell is not None
        and cell.get("fluid", "minecraft:empty") == "minecraft:empty"
        and cell.get("block") not in HAZARDS
    )


def clear_column(cells, x, z, bottom, top):
    """Known body clearance at the central 0.6-wide player footprint."""
    table = getattr(cells, "m_clear", None)  # memoised on a Grid
    if table is None:
        return clear_body(cells, x + 0.5, z + 0.5, bottom, top)
    if (x, z) in cells.ecols:
        table = cells.o_clear
    key = (x, z, bottom, top)
    result = table.get(key)
    if result is None:
        result = table[key] = clear_body(cells, x + 0.5, z + 0.5, bottom, top)
    return result


def clear_body(cells, x, z, bottom, top, *, cell_safe=safe_cell):
    """Check the actual world-space footprint, including partial side collisions."""
    left, right = x - BODY_RADIUS, x + BODY_RADIUS
    near, far = z - BODY_RADIUS, z + BODY_RADIUS
    for cx in range(math.floor(left + EPS), math.ceil(right - EPS)):
        for cz in range(math.floor(near + EPS), math.ceil(far - EPS)):
            for y in range(math.floor(bottom + EPS), math.ceil(top - EPS)):
                cell = cells.get((cx, y, cz))
                if not cell_safe(cell) or "collision_boxes" not in cell:
                    return False
                for box in cell["collision_boxes"]:
                    if (
                        cx + box[0] < right - EPS
                        and cx + box[3] > left + EPS
                        and cz + box[2] < far - EPS
                        and cz + box[5] > near + EPS
                        and y + box[1] < top - EPS
                        and y + box[4] > bottom + EPS
                    ):
                        return False
    return True


def _cell_heights(cells, x, y, z):
    """Standing heights this one cell offers (no window filter)."""
    cell = cells.get((x, y, z))
    if not safe_cell(cell):
        return ()
    block = cell.get("block", "")
    if not (full_cube(cell) or block.endswith(("_slab", "_stairs"))):
        return ()
    heights = []
    for box in cell.get("collision_boxes", []):
        if (
            box[0] < 0.8 - EPS
            and box[3] > 0.2 + EPS
            and box[2] < 0.8 - EPS
            and box[5] > 0.2 + EPS
        ):
            height = y + box[4]
            if clear_column(cells, x, z, height, height + 1.8):
                heights.append(height)
    return tuple(heights)


def surfaces(cells, x, z, low, high):
    table = getattr(cells, "m_csurf", None)  # memoised on a Grid
    if table is None:
        return _surfaces(cells, x, z, low, high, None)
    if (x, z) in cells.ecols:
        table = cells.o_csurf
    key = (x, z, low, high)
    found = table.get(key)
    if found is None:
        found = table[key] = _surfaces(cells, x, z, low, high, table)
    return list(found)


def _surfaces(cells, x, z, low, high, table):
    heights = set()
    for y in range(math.floor(low) - 1, math.floor(high) + 1):
        if table is None:
            found = _cell_heights(cells, x, y, z)
        else:
            found = table.get((x, y, z))
            if found is None:
                found = table[x, y, z] = _cell_heights(cells, x, y, z)
        for height in found:
            if low - EPS <= height <= high + EPS:
                heights.add(height)
    return sorted(heights)


def standable(cells, node):
    x, y, z = node
    table = getattr(cells, "m_stand", None)  # memoised on a Grid
    if table is None:
        return any(abs(height - y) < EPS for height in surfaces(cells, x, z, y, y))
    if (x, z) in cells.ecols:
        table = cells.o_stand
    result = table.get(node)
    if result is None:
        result = table[node] = any(
            abs(height - y) < EPS for height in surfaces(cells, x, z, y, y)
        )
    return result


def step_clear(cells, source, target):
    """Known landing/stance and conservative head clearance over the transition."""
    x, y, z = source
    tx, ty, tz = target
    if not standable(cells, source) or not standable(cells, target):
        return False
    if ty > y:
        # Keep the existing conservative jump envelope on full-block ascents.
        extra = 1.2 if ty - y > 0.6 else 0
        return clear_column(
            cells, x, z, y, max(y + 1.8, ty + 1.8 + extra)
        ) and clear_column(cells, tx, tz, ty, ty + 1.8 + extra)
    return clear_column(cells, tx, tz, ty, y + 1.8)
