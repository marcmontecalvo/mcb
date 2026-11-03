"""Pure known-cell geometry for terrain execution and the offline oracle."""

import math

from mclab.navigation import empty, standable


def known_step(cells, source, target, *, max_drop=1):
    x, y, z = source
    tx, ty, tz = target
    if abs(tx - x) + abs(tz - z) != 1 or not -max_drop <= ty - y <= 1:
        return False
    if not standable(cells, source) or not standable(cells, target):
        return False
    if ty > y and not all(
        empty(cells.get(p)) for p in ((x, y + 2, z), (x, y + 3, z), (tx, ty + 2, tz))
    ):
        return False
    if ty < y and not all(
        empty(cells.get((tx, cy, tz))) for cy in range(math.floor(ty), math.ceil(y) + 2)
    ):
        return False
    return True
