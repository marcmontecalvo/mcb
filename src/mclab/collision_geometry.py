"""Development slab/stair geometry; unknown cells never provide clearance.

Nodes remain block-column centers with standing heights at observed collision
surfaces. This deliberately excludes fences, doors and other complex machinery.
Legacy full-cube predicates and qualification controllers remain unchanged.
"""

import math

from mclab.navigation import full_cube

EPS = 1e-7
BODY_RADIUS = 0.3
HAZARDS = {
    "minecraft:fire",
    "minecraft:soul_fire",
    "minecraft:cactus",
    "minecraft:magma_block",
    "minecraft:campfire",
    "minecraft:soul_campfire",
    "minecraft:powder_snow",
    "minecraft:sweet_berry_bush",
    "minecraft:wither_rose",
}


def safe_cell(cell):
    return (
        cell is not None
        and cell.get("fluid", "minecraft:empty") == "minecraft:empty"
        and cell.get("block") not in HAZARDS
    )


def clear_column(cells, x, z, bottom, top):
    """Known body clearance at the central 0.6-wide player footprint."""
    return clear_body(cells, x + 0.5, z + 0.5, bottom, top)


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


def surfaces(cells, x, z, low, high):
    heights = set()
    for y in range(math.floor(low) - 1, math.floor(high) + 1):
        cell = cells.get((x, y, z))
        if not safe_cell(cell):
            continue
        block = cell.get("block", "")
        if not (full_cube(cell) or block.endswith(("_slab", "_stairs"))):
            continue
        for box in cell.get("collision_boxes", []):
            height = y + box[4]
            if (
                low - EPS <= height <= high + EPS
                and box[0] < 0.8 - EPS
                and box[3] > 0.2 + EPS
                and box[2] < 0.8 - EPS
                and box[5] > 0.2 + EPS
                and clear_column(cells, x, z, height, height + 1.8)
            ):
                heights.add(height)
    return sorted(heights)


def standable(cells, node):
    x, y, z = node
    return any(abs(height - y) < EPS for height in surfaces(cells, x, z, y, y))


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
