"""Interaction reach and line-of-sight predicate over admitted geometry."""

import math

EYE_HEIGHT = 1.62
RAY_STEP = 0.1


def interacts(cells, node, target, reach):
    return interacts_at_position(
        cells, (node[0] + 0.5, node[1], node[2] + 0.5), target, reach
    )


def _segment_hits_box(start, end, cell_pos, box) -> bool:
    """True when line segment start->end intersects box (relative to cell_pos)."""
    tmin, tmax = 0.0, 1.0
    for i in range(3):
        lo = cell_pos[i] + box[i]
        hi = cell_pos[i] + box[i + 3]
        d = end[i] - start[i]
        if abs(d) < 1e-12:
            if start[i] < lo or start[i] > hi:
                return False
        else:
            t1 = (lo - start[i]) / d
            t2 = (hi - start[i]) / d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return False
    return True


def interacts_at_position(cells, position, target, reach):
    eye = (position[0], position[1] + EYE_HEIGHT, position[2])
    eye_cell = tuple(math.floor(v) for v in eye)
    centre = (target[0] + 0.5, target[1] + 0.5, target[2] + 0.5)
    distance = math.dist(eye, centre)
    if distance > reach:
        return False
    steps = max(1, math.ceil(distance / RAY_STEP))
    for i in range(steps):
        point = tuple(
            math.floor(a + (b - a) * i / steps)
            for a, b in zip(eye, centre, strict=True)
        )
        if point == tuple(target):
            return True
        cell = cells.get(point)
        if cell is None:
            return False
        boxes = cell.get("collision_boxes")
        if (
            point == eye_cell
            and cell.get("block") == "minecraft:scaffolding"
            and boxes
        ):
            if any(_segment_hits_box(eye, centre, point, box) for box in boxes):
                return False
            continue
        if boxes:
            return False
    return True
