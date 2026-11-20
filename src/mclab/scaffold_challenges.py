"""Scaffold challenges, basic to advanced, that the bot or a person with a normal
client attempts (scripts/check-scaffold-challenges.py). Pure geometry and verdicts, so
tests cover them; no server.

Offsets are from the start feet cell on a flat lab spot: the ground top is dy=-1, so
everything below it is stone and everything from dy=0 up is air, except ``cells``.
Every challenge needs scaffolding the way a player uses it (docs/SCAFFOLDING.md):
climb, come down a drop too deep to fall, stand out over a pit, bridge straight, round
one corner (L) and round a wall (U) within scaffold distance 6, span 13 with a middle
tower, and, the last one, take the stairs to an upper floor rather than tower into a
solid ceiling (run 20261120T141244368734Z)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

AIR = "minecraft:air"
STONE = "minecraft:stone"
GLASS = "minecraft:glass"
SCAFFOLDING = "minecraft:scaffolding"
STAIR = "minecraft:stone_stairs[facing=east]"
PICKAXE = "minecraft:iron_pickaxe"
SCAFFOLD_DISTANCE = 6  # a seventh cell out from support falls
MAX_DROP = 3  # a fall of more hurts
KIT_BATCH = 60  # the lab kit places at most 64 cells a request
LEVELS = ("basic", "advanced")

Offset = tuple[int, int, int]
Pos = tuple[int, int, int]
STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True)
class Challenge:
    name: str
    level: str
    task: str  # what the player is asked to do
    cells: Mapping[Offset, str]  # every cell that differs from the flat spot
    goal: Offset  # feet cell to reach
    goal_radius: float = 1.0
    scaffolding: int = 32
    no_damage: bool = True  # a fall or a drop into the pit is not a scaffold solution
    keep_built: bool = False  # the subject must not change the built cells (a house)

    def state(self, p: Offset) -> str:
        return self.cells.get(p, STONE if p[1] < 0 else AIR)

    def at(self, origin: Pos, offset: Offset) -> Pos:
        return (origin[0] + offset[0], origin[1] + offset[1], origin[2] + offset[2])

    def clear_extent(self) -> list[int]:
        """The lab kit's ``clear`` box (+-dx, 0..dy, +-dz round the start) that
        covers the course and the room to build up over it."""
        points = [*self.cells, self.goal]
        dx = max(abs(p[0]) for p in points) + 1
        dz = max(abs(p[2]) for p in points) + 1
        dy = max(4, max(p[1] for p in points) + 2, self.goal[1] + 3)
        return [min(dx, 24), min(dy, 16), min(dz, 24)]

    def place_batches(self, size: int = KIT_BATCH) -> list[list[dict]]:
        """Kit ``place`` entries, bottom up (a column's base before its top)."""
        entries = [
            {"offset": list(p), "state": s}
            for p, s in sorted(
                self.cells.items(), key=lambda i: (i[0][1], i[0][0], i[0][2])
            )
        ]
        return [entries[i : i + size] for i in range(0, len(entries), size)]

    def restore_cells(self) -> dict[Offset, str]:
        """Back to the flat spot: stone below the ground top, air above."""
        return {p: STONE if p[1] < 0 else AIR for p in self.cells}


def _box(xs: Iterable[int], ys: Iterable[int], zs: Iterable[int], state: str) -> dict:
    ys, zs = list(ys), list(zs)
    return {(x, y, z): state for x in xs for y in ys for z in zs}


def _pit(xs: Iterable[int], zs: Iterable[int], depth: int, *, keep=()) -> dict:
    """Air ``depth`` deep under the ground top, on a laid stone floor (the lab platform
    may be a slab with nothing under it). ``keep``: (x, z) columns left standing."""
    out = {}
    for x in xs:
        for z in zs:
            if (x, z) in keep:
                continue
            out |= {(x, y, z): AIR for y in range(-depth, 0)}
            out[(x, -depth - 1, z)] = STONE
    return out


def _moat(island: tuple[int, int], walls=()) -> dict:
    """The bridge courses: a 3x3 start pad (x -1..1) closed by glass on three sides,
    a pit 6 deep east of it (x 2..9, z -4..4) fenced in glass so its far rims cannot
    be reached, and a one-cell stone island to stand on. ``walls``: glass (x, z)
    columns rising from the pit floor."""
    cells = _pit(range(2, 10), range(-4, 5), 6, keep={island, *walls})
    for x, z in walls:
        cells |= _box([x], range(-6, 4), [z], GLASS)
    fence = {(x, z) for x in range(1, 11) for z in (-5, 5)}
    fence |= {(10, z) for z in range(-5, 6)}
    fence |= {(1, z) for z in (*range(-5, -1), *range(2, 6))}
    fence |= {(x, z) for x in range(-2, 2) for z in (-2, 2)}
    fence |= {(-2, z) for z in range(-2, 3)}
    for x, z in fence:
        cells |= _box([x], range(0, 3), [z], GLASS)
    return cells


def _corridor() -> dict:
    """The span: a corridor 5 wide closed in glass, a pit 13 long and 4 deep across it,
    so a bridge from one side reaches 6 and a tower on the pit floor carries the
    rest."""
    cells = _pit(range(2, 15), range(-2, 3), 4)
    for x in range(-2, 19):
        cells |= _box([x], range(0, 3), [-3, 3], GLASS)
    for x in (-2, 18):
        cells |= _box([x], range(0, 3), range(-2, 3), GLASS)
    return cells


def _house() -> dict:
    """Two storeys, x 1..9, z -4..4: a door at (1, 0..1, 0), a solid ceiling at y=3 but
    for the stairwell over three stone stairs at z=2, and a roof at y=7."""
    cells = _box([1, 9], range(0, 8), range(-4, 5), STONE)
    cells |= _box(range(2, 9), range(0, 8), [-4, 4], STONE)
    cells |= _box(range(2, 9), [3, 7], range(-3, 4), STONE)
    for p in ((1, 0, 0), (1, 1, 0), (3, 3, 2), (4, 3, 2), (5, 3, 2)):
        del cells[p]
    for x, y in ((3, 0), (4, 1), (5, 2)):
        cells[(x, y, 2)] = STAIR
    return cells


CHALLENGES: tuple[Challenge, ...] = (
    Challenge(
        "climb",
        "basic",
        "Climb onto the stone ledge, five blocks up, with scaffolding.",
        _box(range(2, 5), range(0, 5), range(-1, 2), STONE),
        goal=(3, 5, 0),
        scaffolding=16,
    ),
    Challenge(
        "descend",
        "basic",
        "Get to the bottom of the shaft, ten deep, without taking damage.",
        _pit(range(2, 5), range(-1, 2), 10),
        goal=(3, -10, 0),
        scaffolding=16,
    ),
    Challenge(
        "reach-out",
        "basic",
        "Stand on the green marker: two up, four out over the pit.",
        _pit(range(2, 9), range(-3, 4), 8),
        goal=(5, 2, 0),
        scaffolding=16,
    ),
    Challenge(
        "bridge",
        "advanced",
        "Bridge straight across the pit to the island, without falling in.",
        _moat((7, 0)),
        goal=(7, 0, 0),
    ),
    Challenge(
        "bridge-l",
        "advanced",
        "Bridge to the island round one corner (it is off your lines), no falling in.",
        _moat((4, -3)),
        goal=(4, 0, -3),
    ),
    Challenge(
        "bridge-u",
        "advanced",
        "Bridge round the glass wall to the island behind it, without falling in.",
        _moat((4, 0), walls=[(3, -1), (3, 0), (3, 1)]),
        goal=(4, 0, 0),
    ),
    Challenge(
        "span",
        "advanced",
        "Cross the 13-wide pit: a bridge reaches 6, so build a tower in the middle.",
        _corridor(),
        goal=(16, 0, 0),
        scaffolding=48,
    ),
    Challenge(
        "stairs",
        "advanced",
        "Get to the green marker upstairs: take the stairs, not scaffolding.",
        _house(),
        goal=(7, 4, -2),
        scaffolding=16,
        keep_built=True,
    ),
)


def by_name(name: str) -> Challenge:
    for c in CHALLENGES:
        if c.name == name:
            return c
    raise KeyError(name)


def select(
    *, names: Iterable[str] | None = None, level: str | None = None
) -> list[Challenge]:
    """By name in the order given, else every challenge of a level (all when None)."""
    if names:
        return [by_name(n) for n in names]
    return [c for c in CHALLENGES if level in (None, "all", c.level)]


def kit(c: Challenge) -> list[dict]:
    """The subject's inventory: scaffolding, and a pickaxe to take it down again."""
    return [
        {"item": SCAFFOLDING, "count": c.scaffolding},
        {"item": PICKAXE, "count": 1},
    ]


def _solid(c: Challenge, p: Offset, extra: Mapping[Offset, str]) -> bool:
    return extra.get(p, c.state(p)) != AIR


def walkable(c: Challenge, target: Offset) -> bool:
    """Can a player walk and jump from the start to ``target`` without placing anything
    or falling more than MAX_DROP? (Stairs and slabs count as full blocks: a step up is
    a jump.)"""
    dx, dy, dz = c.clear_extent()
    no: dict = {}

    def open_(p):
        return not _solid(c, p, no)

    def inside(p):
        return abs(p[0]) <= dx and abs(p[2]) <= dz and -32 <= p[1] <= dy + 2

    seen, todo = {(0, 0, 0)}, deque([(0, 0, 0)])
    while todo:
        x, y, z = todo.popleft()
        if (x, y, z) == target:
            return True
        for sx, sz in STEPS:
            nx, nz = x + sx, z + sz
            nxt = None
            if open_((nx, y, nz)) and open_((nx, y + 1, nz)):
                ny = y
                while open_((nx, ny - 1, nz)) and y - ny <= MAX_DROP:
                    ny -= 1
                if y - ny <= MAX_DROP:
                    nxt = (nx, ny, nz)
            elif (
                _solid(c, (nx, y, nz), no)
                and open_((x, y + 2, z))
                and open_((nx, y + 1, nz))
                and open_((nx, y + 2, nz))
            ):
                nxt = (nx, y + 1, nz)
            if nxt and nxt not in seen and inside(nxt):
                seen.add(nxt)
                todo.append(nxt)
    return False


def bridge_turns(
    c: Challenge,
    *,
    limit: int = SCAFFOLD_DISTANCE,
    extra: Mapping[Offset, str] | None = None,
) -> int | None:
    """Fewest turns of a scaffold bridge at the ground's height (dy=0) from the start to
    the goal column, or None when no bridge within scaffold distance ``limit`` gets
    there. A cell over ground has distance 0 (walk on, turns start again); each cell out
    over air is one more than the cell before it. A turn is a change of direction
    between two bridge cells; stepping off onto ground is free. ``extra``: cells that
    are built (a tower on the pit floor)."""
    extra = extra or {}
    dx, _dy, dz = c.clear_extent()

    def open_(x, z):
        state = extra.get((x, 0, z), c.state((x, 0, z)))
        return state in (AIR, SCAFFOLDING) and abs(x) <= dx and abs(z) <= dz

    def grounded(x, z):
        return _solid(c, (x, -1, z), extra)

    goal = (c.goal[0], c.goal[2])
    start = (0, 0, None, 0)  # x, z, last bridge direction, distance
    best = {start: 0}
    todo: deque = deque([(0, start)])
    while todo:
        turns, state = todo.popleft()
        if best.get(state, turns) < turns:
            continue
        x, z, heading, dist = state
        if (x, z) == goal and grounded(x, z):
            return turns
        for step in STEPS:
            nx, nz = x + step[0], z + step[1]
            if not open_(nx, nz):
                continue
            if grounded(nx, nz):
                nxt, cost = (nx, nz, None, 0), 0
            elif dist + 1 > limit:
                continue
            elif dist == 0:  # the first cell out: the next one sets the line
                nxt, cost = (nx, nz, None, 1), 0
            else:
                nxt, cost = (nx, nz, step, dist + 1), int(heading not in (None, step))
            if turns + cost < best.get(nxt, 1 << 30):
                best[nxt] = turns + cost
                (todo.appendleft if cost == 0 else todo.append)((turns + cost, nxt))
    return None


def verdict(
    c: Challenge,
    *,
    reached: bool,
    damage: float,
    scaffolding_used: int,
    built_changed: int,
) -> dict:
    problems = []
    if not reached:
        problems.append("goal not reached")
    if c.no_damage and damage > 0:
        problems.append(f"took {damage} damage")
    if c.keep_built and built_changed:
        problems.append(f"changed {built_changed} house cells")
    if c.keep_built and scaffolding_used:
        problems.append(f"placed {scaffolding_used} scaffolding (stairs were there)")
    return {
        "passed": not problems,
        "problems": problems,
        "reached": reached,
        "damage": damage,
        "scaffolding_used": scaffolding_used,
        "built_changed": built_changed,
    }
