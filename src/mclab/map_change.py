"""Did the geometry a plan depends on change between two map snapshots?

Map revisions advance with every admitted observation, and most of them change
nothing a plan in hand depends on: re-observed cells, or a block placed beside
the body. House-large run 20261119T133618892664Z restarted its re-plan on each
revision and cancelled 78% of 2,606 plans. Snapshots are immutable mappings
(``SpatialMemory.planning_snapshot``), so two of them can be compared cell by
cell over the region a plan uses.
"""

import math

# Around each route point: the support below, feet, head, and the jump
# clearance above, one column to each side (the 0.6-wide body sweeps into the
# neighbouring column on diagonals and off-centre steps).
ROUTE_HEIGHTS = (-1, 0, 1, 2)
ROUTE_SIDE = 1


def shape(cell):
    """The geometry facts of a cell record, without observation bookkeeping."""
    if cell is None:
        return None
    boxes = cell.get("collision_boxes")
    properties = cell.get("properties")
    return (
        cell.get("block"),
        cell.get("fluid"),
        None if boxes is None else tuple(tuple(box) for box in boxes),
        tuple(sorted(properties.items())) if properties else None,
    )


def route_cells(route, side=ROUTE_SIDE):
    """Cells a body driving ``route`` stands on, moves through or sweeps beside."""
    cells = set()
    for x, y, z in route:
        floor = math.floor(y)
        for dx in range(-side, side + 1):
            for dz in range(-side, side + 1):
                for dy in ROUTE_HEIGHTS:
                    cells.add((x + dx, floor + dy, z + dz))
    return cells


def box_cells(a, b, *, margin_xz, margin_y):
    """Every cell of the box spanning points ``a`` and ``b``, plus margins."""
    low = [math.floor(min(a[i], b[i])) for i in range(3)]
    high = [math.floor(max(a[i], b[i])) for i in range(3)]
    for x in range(low[0] - margin_xz, high[0] + margin_xz + 1):
        for z in range(low[2] - margin_xz, high[2] + margin_xz + 1):
            for y in range(low[1] - margin_y, high[1] + margin_y + 1):
                yield x, y, z


def changed(old, new, cells):
    """True when any of ``cells`` differs in geometry between two snapshots."""
    if old is new:
        return False
    for p in cells:
        before, after = old.get(p), new.get(p)
        # Grid snapshots share one facts object per block state: identity first.
        if before is not after and shape(before) != shape(after):
            return True
    return False
