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
    if (
        not allow_bridge
        or y != sy - 1
        or abs(dx) + abs(dz) != 1
        or not flags(below) & EMPTY
        or not flags(anchor) & FULL
    ):
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
