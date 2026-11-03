"""Interaction reach and line-of-sight predicate over admitted geometry."""

import math

EYE_HEIGHT = 1.62
RAY_STEP = 0.1


def interacts(cells, node, target, reach):
    return interacts_at_position(
        cells, (node[0] + 0.5, node[1], node[2] + 0.5), target, reach
    )


def interacts_at_position(cells, position, target, reach):
    eye = (position[0], position[1] + EYE_HEIGHT, position[2])
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
        if cell is None or cell.get("collision_boxes"):
            return False
    return True
