"""Development slab/stair geometry; unknown cells never provide clearance.

Nodes remain block-column centers with standing heights at observed collision
surfaces. This deliberately excludes fences, doors and other complex machinery.
Legacy full-cube predicates and qualification controllers remain unchanged.
"""

import math

from mclab.grid_cache import HAZARDS
from mclab.navigation import empty, full_cube
from mclab.navigation import standable as block_standable

EPS = 1e-7
BODY_RADIUS = 0.3


def safe_cell(cell):
    return (
        cell is not None
        and cell.get("fluid", "minecraft:empty") == "minecraft:empty"
        and cell.get("block") not in HAZARDS
    )


WATER_FLUIDS = ("minecraft:water", "minecraft:flowing_water")


def wadeable(cell):
    """Water with nothing solid in it: the body's feet may stand in this cell."""
    return (
        cell is not None
        and cell.get("fluid") in WATER_FLUIDS
        and cell.get("collision_boxes") == []
        and cell.get("block") not in HAZARDS
    )


def flat_floor(cell):
    """A dry floor whose collision top is level with the block top under the stance
    centre: a full cube, a top slab, a stair's high half. A body on an eave slab
    had no edit move off it and every plan was unreachable (large house run
    20261117T075353967859Z)."""
    return safe_cell(cell) and any(
        abs(box[4] - 1) < EPS
        and box[0] < 0.8 - EPS
        and box[3] > 0.2 + EPS
        and box[2] < 0.8 - EPS
        and box[5] > 0.2 + EPS
        for box in cell.get("collision_boxes") or ()
    )


def edit_stance(cells, node):
    """A stance a terrain edit may start from: a dry block stance, or one wading in
    one-deep water on a full block with the head in air. The body dug itself into a
    wet pocket and, with no edit allowed from wet feet, had no move out of it for
    130 s (iron run 20261112T205117541135Z)."""
    if block_standable(cells, node):
        return True
    x, y, z = node
    if (
        flat_floor(cells.get((x, y - 1, z)))
        and empty(cells.get(node))
        and empty(cells.get((x, y + 1, z)))
    ):
        return True
    return (
        full_cube(cells.get((x, y - 1, z)))
        and wadeable(cells.get(node))
        and empty(cells.get((x, y + 1, z)))
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
    feet = math.floor(bottom + EPS)
    for cx in range(math.floor(left + EPS), math.ceil(right - EPS)):
        for cz in range(math.floor(near + EPS), math.ceil(far - EPS)):
            for y in range(feet, math.ceil(top - EPS)):
                cell = cells.get((cx, y, cz))
                # Wading: water in the feet cell only, so the head stays dry. Without
                # it one-deep swamp water had no stance at all and the body could not
                # break a block right beside it (run 20261111T025206013862Z).
                if y == feet and wadeable(cell):
                    continue
                # Vanilla gives scaffolding no collision for a body in or beside it
                # (only one standing above its top): a body on a ledge with its
                # hitbox in a scaffolding column had no stance, and every plan was
                # "unreachable, 0 expanded" (run 20261117T214048223018Z).
                if cell is not None and cell.get("block") == "minecraft:scaffolding":
                    continue
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
    # Any block's real shape, modded ones too (a lily pad, carpet, bed, chest):
    # a box topped within the cell under the centre is a floor. Fences and walls
    # (topped at 1.5) cannot be stepped onto. As RoutePlanner.cellHeights.
    heights = []
    for box in cell.get("collision_boxes", []):
        if (
            box[4] <= 1 + EPS
            and box[0] < 0.8 - EPS
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
        # Jump room above the take-off, as RoutePlanner.stepClear.
        top = max(ty + 1.8, y + 1.8 + (1.2 if ty - y > 0.6 else 0))
        return clear_column(cells, x, z, y, top) and clear_column(
            cells, tx, tz, ty, top
        )
    return clear_column(cells, tx, tz, ty, y + 1.8)
