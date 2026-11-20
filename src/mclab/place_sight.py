"""Can a body click a support face of a cell from an eye, as place_block does?

The Python twin of ``BodyBuild.hits`` (mcb-body): every neighbour of the target
that has a collision box (and is not replaceable) is a source; on its face toward
the target the server samples 19 points (``PlacementLogic.HIT_OFFSETS``) and
clicks the first one within reach whose ray from the eye meets that face first.
The ray clips block OUTLINES through fluids (``ClipContext.Block.OUTLINE,
Fluid.NONE``): seagrass, lily pads, grass and flowers stop it although they have
no collision box worth standing on.

The server's verdict stays final: this predicts it, so the fill does not click
blind (run 20261119T133618892664Z: 390 of 464 failed dirt clicks had a face in
reach but none in sight) and does not skip cells the server would accept (a
stance-centre, five-eye test skipped so many that the walks rose from 98 to 236
in run 20261119T214327788429Z).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from mclab.interaction_goal import _segment_hits_box

Pos = tuple[int, int, int]

PLACE_REACH = 4.5  # player.blockInteractionRange() for a survival body
EYE_HEIGHT = 1.62
HIT_OFFSETS = (
    (0, 0), (0, 0.25), (0, -0.25), (0.25, 0), (-0.25, 0),
    (0.25, 0.25), (-0.25, 0.25), (0.25, -0.25), (-0.25, -0.25),
    (0, 0.44), (0.25, 0.44), (-0.25, 0.44),
    (0, -0.44), (0.44, 0), (-0.44, 0), (0.44, 0.44), (-0.44, 0.44), (0.44, -0.44),
    (-0.44, -0.44),
)  # fmt: skip
NEIGHBOURS = ((0, -1, 0), (0, 1, 0), (0, 0, -1), (0, 0, 1), (-1, 0, 0), (1, 0, 0))
_CLEAR = frozenset(
    {
        "minecraft:air",
        "minecraft:cave_air",
        "minecraft:void_air",
        "minecraft:water",
        "minecraft:lava",
        "minecraft:bubble_column",
        "minecraft:light",
    }
)
_REPLACEABLE = frozenset(
    "minecraft:" + n
    for n in ("short_grass", "grass", "tall_grass", "snow", "fern", "large_fern",
              "dead_bush", "vine", "seagrass", "tall_seagrass")
)  # fmt: skip
_CELL = (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)


def _boxes(cell) -> list:
    return list((cell or {}).get("collision_boxes") or ())


def outline(cell) -> list:
    """The boxes a clip ray stops at: the collision boxes, or the whole cell for a
    block with none (a plant's outline; the exact shape is smaller, so this errs
    toward hidden). Air, bare fluid and unknown cells stop nothing."""
    if not cell or cell.get("block") in _CLEAR:
        return []
    return _boxes(cell) or [_CELL]


def source(cell) -> bool:
    """A neighbour the server clicks: not replaceable, with a collision shape (or
    scaffolding, a lattice clicked like a block)."""
    if not cell or cell.get("block") in _REPLACEABLE:
        return False
    return bool(_boxes(cell)) or cell.get("block") == "minecraft:scaffolding"


def eye_of(feet) -> tuple[float, float, float]:
    return (feet[0], feet[1] + EYE_HEIGHT, feet[2])


def _ray_clear(cells: Mapping, eye, point, block: Pos) -> bool:
    """No outline between ``eye`` and ``point`` (on ``block``'s face) in any cell the
    segment passes through, by exact grid traversal, ``block`` itself excepted."""
    cell = [math.floor(v) for v in eye]
    d = [point[i] - eye[i] for i in range(3)]
    step = [1 if v > 0 else -1 for v in d]
    t_max, t_delta = [], []
    for i in range(3):
        if d[i] == 0:
            t_max.append(math.inf)
            t_delta.append(math.inf)
            continue
        edge = cell[i] + (1 if d[i] > 0 else 0)
        t_max.append((edge - eye[i]) / d[i])
        t_delta.append(abs(1 / d[i]))
    for _ in range(64):
        c = (cell[0], cell[1], cell[2])
        if c == block:
            return True
        if any(_segment_hits_box(eye, point, c, b) for b in outline(cells.get(c))):
            return False
        i = t_max.index(min(t_max))
        if t_max[i] > 1:
            return True
        cell[i] += step[i]
        t_max[i] += t_delta[i]
    return True


def _face_points(n: Pos, d, boxes):
    """Sample points on the face of block ``n`` toward ``n - d`` (the target), on the
    shape's own plane (a chest top is at 0.875, BodyBuild.hits)."""
    axis = next(i for i in range(3) if d[i])
    others = [i for i in range(3) if i != axis]
    toward = -d[axis]  # +1: the target is on the positive side of n
    if boxes:
        plane = (
            max(b[axis + 3] for b in boxes)
            if toward > 0
            else min(b[axis] for b in boxes)
        )
    else:
        plane = 1.0 if toward > 0 else 0.0
    for u, v in HIT_OFFSETS:
        p = [n[0] + 0.5, n[1] + 0.5, n[2] + 0.5]
        p[axis] = n[axis] + plane
        # facePoint: (u, v) map to (x, z) on up/down, (x|z, y) on the sides
        if axis == 1:
            p[0] += u
            p[2] += v
        else:
            p[others[1] if others[0] == 1 else others[0]] += u
            p[1] += v
        yield tuple(p), n[axis] + plane, toward, axis


def clickable_from(
    cells: Mapping,
    eye,
    target: Pos,
    reach: float = PLACE_REACH,
    sources: Iterable[Pos] | None = None,
) -> bool:
    """True when some source face of ``target`` shows a sample point to ``eye`` within
    ``reach``. ``sources`` limits the neighbours tried."""
    allowed = None if sources is None else {tuple(s) for s in sources}
    for dx, dy, dz in NEIGHBOURS:
        n = (target[0] + dx, target[1] + dy, target[2] + dz)
        if allowed is not None and n not in allowed:
            continue
        cell = cells.get(n)
        if not source(cell):
            continue
        for point, plane, toward, axis in _face_points(n, (dx, dy, dz), _boxes(cell)):
            if (eye[axis] - plane) * toward <= 0:
                break  # the eye is behind this face: no point of it shows
            if math.dist(eye, point) > reach:
                continue
            beyond = _beyond(eye, point)
            if _ray_clear(cells, eye, beyond, n):
                return True
    return False


def _beyond(eye, point):
    """The server clips 0.05 past the point, so the ray ends inside the source block."""
    d = [point[i] - eye[i] for i in range(3)]
    norm = math.sqrt(sum(v * v for v in d)) or 1.0
    return tuple(point[i] + d[i] / norm * 0.05 for i in range(3))
