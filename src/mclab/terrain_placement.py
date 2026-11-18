"""Observed placement anchors for footholds and explicitly enabled short bridges."""

from dataclasses import dataclass

from mclab.grid_cache import EMPTY, FULL, classify


@dataclass(frozen=True)
class PlacementTarget:
    support: tuple
    face: str
    aim: tuple
    approach: tuple | None = None


def placement_target(cells, source, point, *, allow_bridge=False):
    x, y, z = point
    sx, sy, sz = source
    dx, dz = x - sx, z - sz
    flags = getattr(cells, "cflags", None) or (lambda p: classify(cells.get(p)))
    below = (x, y - 1, z)
    if flags(below) & FULL:
        approach = None
        if y < sy and abs(dx) + abs(dz) == 1:
            approach = (sx + 0.5 + dx * 0.65, sy, sz + 0.5 + dz * 0.65)
        return PlacementTarget(below, "UP", (x + 0.5, y - 0.001, z + 0.5), approach)
    anchor = (sx, sy - 1, sz)
    if not allow_bridge:
        return None
    if (
        y != sy - 1
        or abs(dx) + abs(dz) != 1
        or not flags(below) & EMPTY
        or not flags(anchor) & FULL
    ):
        # Any full cube beside the cell (a wall, a log), as RoutePlanner.anchored
        # and StepMotor.sideAnchor: north, south, east, west. What is under the
        # cell must be known, as for the floor anchor.
        if (x, y - 1, z) not in cells:
            return None
        for ax, az, face in (
            (0, -1, "SOUTH"),
            (0, 1, "NORTH"),
            (1, 0, "WEST"),
            (-1, 0, "EAST"),
        ):
            side = (x + ax, y, z + az)
            if flags(side) & FULL:
                return PlacementTarget(
                    side,
                    face,
                    (x + ax + 0.5 - ax * 0.499, y + 0.5, z + az + 0.5 - az * 0.499),
                    None,
                )
        return None
    face = (
        "EAST" if dx == 1 else "WEST" if dx == -1 else "SOUTH" if dz == 1 else "NORTH"
    )
    return PlacementTarget(
        anchor,
        face,
        (sx + 0.5 + dx * 0.499, y + 0.5, sz + 0.5 + dz * 0.499),
        (sx + 0.5 + dx * 0.65, sy, sz + 0.5 + dz * 0.65),
    )
