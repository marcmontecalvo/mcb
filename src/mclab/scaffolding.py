"""How scaffolding behaves, from the game's own rules (ScaffoldingBlock and
ScaffoldingBlockItem, 26.x). The knowledge pack lists only its properties
(``distance`` 0-7, ``bottom``, ``waterlogged``), hardness 0, the ``climbable`` tag and
that it drops itself; the rules below are what make it unlike any other block.

Stability. Every scaffolding block stores ``distance`` (0-7):
- 0 when the block under it is sturdy (a solid top face);
- the ``distance`` of a scaffolding block directly below it, else
- 1 + the smallest ``distance`` among its four horizontal scaffolding neighbours;
- 7 when none of those holds. A block that reaches 7 is destroyed and drops itself
  (a tick later it would fall as an entity). So a tower stands on the ground, a
  bridge may reach 6 blocks sideways from a supported column (distance 6), and
  breaking the supported block (the *root*) brings down everything that hung from it.

Placing (ScaffoldingBlockItem.updatePlacementContext). Clicked on a scaffolding block:
- sneaking: the new block goes next to the clicked face, but jumping over the
  scaffolding already in that direction (up to 7 blocks sideways, no limit upwards);
- not sneaking: the top face extends sideways along the player's heading, any other
  face goes to the top of the column.
The body places sneaking, so a click on a face means "the cell beyond the run of
scaffolding on that face", which for a tower is the cell above its top.
A cell where the new block would have distance 7 is refused.

Walking. Scaffolding has no collision from the side or from inside (the body passes
through it and climbs it); it is stood on from above unless sneaking.
"""

from __future__ import annotations

from collections.abc import Callable, Collection

Pos = tuple[int, int, int]

BLOCK = "minecraft:scaffolding"
UNSTABLE = 7
_HORIZONTAL = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1))


def _add(p: Pos, v: Pos) -> Pos:
    return (p[0] + v[0], p[1] + v[1], p[2] + v[2])


def distances(
    scaffolds: Collection[Pos], sturdy_below: Callable[[Pos], bool]
) -> dict[Pos, int]:
    """The ``distance`` every block of ``scaffolds`` settles at. ``sturdy_below(p)``:
    is the cell under ``p`` a solid block (not one of the scaffolds)."""
    cells = set(scaffolds)
    dist = {p: 0 if sturdy_below(p) else UNSTABLE for p in cells}
    changed = True
    while changed:
        changed = False
        for p in cells:
            if dist[p] == 0:
                continue
            below = _add(p, (0, -1, 0))
            best = dist[below] if below in cells else UNSTABLE
            for v in _HORIZONTAL:
                n = _add(p, v)
                if n in cells:
                    best = min(best, dist[n] + 1)
            if best < dist[p]:
                dist[p], changed = best, True
    return dist


def survivors(
    scaffolds: Collection[Pos],
    sturdy_below: Callable[[Pos], bool],
    removed: Collection[Pos] = (),
) -> set[Pos]:
    """What still stands after ``removed`` are broken (everything unstable drops)."""
    gone = set(removed)
    left = [p for p in scaffolds if p not in gone]
    return {p for p, d in distances(left, sturdy_below).items() if d < UNSTABLE}


def roots(scaffolds: Collection[Pos], sturdy_below: Callable[[Pos], bool]) -> list[Pos]:
    """The blocks standing on solid ground. Breaking only these takes down the lot,
    whatever hangs from them, in one hit each."""
    return sorted(p for p in scaffolds if sturdy_below(p))


def can_place(
    pos: Pos, scaffolds: Collection[Pos], sturdy_below: Callable[[Pos], bool]
) -> bool:
    """Would a scaffolding block at ``pos`` stay (distance below 7)?"""
    cells = {*scaffolds, pos}
    return distances(cells, sturdy_below)[pos] < UNSTABLE
