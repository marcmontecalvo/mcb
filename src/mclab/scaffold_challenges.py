"""Scaffold challenges, basic to advanced, that the bot or a person with a normal
client attempts (scripts/check-scaffold-challenges.py). Pure geometry and verdicts, so
tests cover them; no server.

Offsets are from the start feet cell on a flat lab spot: the ground top is dy=-1, so
everything below it is stone and everything from dy=0 up is air, except ``cells``.
Every challenge needs scaffolding the way a player uses it (docs/SCAFFOLDING.md):
climb, come down a drop too deep to fall, stand out over a pit, bridge straight, round
one corner (L) and round a wall (U) within scaffold distance 6, and, the last one, take
the stairs to an upper floor rather than tower into a solid ceiling (run
20261120T141244368734Z).

A course must force what it claims (review of 5bb4812): the bridge courses are closed
under a glass roof, so no tower reaches the top of a fence to walk along it, and their
pit is deeper than the scaffolding handed out, so no column down to the pit floor and up
again at the island stands in for the bridge. The kit is scaffolding alone (no pickaxe
to dig out of a pit), and a changed course cell fails the attempt."""

from __future__ import annotations

import heapq
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

AIR = "minecraft:air"
STONE = "minecraft:stone"
GLASS = "minecraft:glass"
SCAFFOLDING = "minecraft:scaffolding"
STAIR = "minecraft:stone_stairs[facing=east]"
SCAFFOLD_DISTANCE = 6  # a seventh cell out from support falls
MAX_DROP = 3  # a fall of more hurts
KIT_BATCH = 60  # the lab kit places at most 64 cells a request
LEVELS = ("basic", "advanced")
MOAT_DEPTH = 10  # deeper than any bridge course's scaffolding
# The bridge courses' glass roof, on the fences (y 0..ROOF_Y-1, so nothing walks over
# them). Not 3: a rise needs the launch cell clear to feet+3, and the step up from the
# pad onto the bridge root is one (live run 20261120T173256582204Z never left the pad).
ROOF_Y = 4

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
    scaffold_free: bool = False  # the way is there already (stairs): place nothing
    bridge: bool = False  # passed only with a scaffold bridge standing to the goal

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

    def built(self) -> list[Offset]:
        """The laid cells that are not air: the subject must leave them as they are."""
        return sorted(p for p, s in self.cells.items() if s != AIR)


def _box(xs: Iterable[int], ys: Iterable[int], zs: Iterable[int], state: str) -> dict:
    ys, zs = list(ys), list(zs)
    return {(x, y, z): state for x in xs for y in ys for z in zs}


def _pit(xs: Iterable[int], zs: Iterable[int], depth: int, *, keep=()) -> dict:
    """Air ``depth`` deep under the ground top in laid stone: a floor under it and a
    wall round it (the lab platform may be a slab with nothing under it, and the course
    must not open into whatever is there). ``keep``: (x, z) columns left standing,
    laid in stone too."""
    xs, zs = list(xs), list(zs)
    holes = {(x, z) for x in xs for z in zs} - set(keep)
    out = {}
    for x, z in holes:
        out |= {(x, y, z): AIR for y in range(-depth, 0)}
        out[(x, -depth - 1, z)] = STONE
    ring = {
        (x + sx, z + sz) for x, z in holes for sx in (-1, 0, 1) for sz in (-1, 0, 1)
    } - holes
    for x, z in ring:
        out |= {(x, y, z): STONE for y in range(-depth - 1, 0)}
    return out


def _moat(island: tuple[int, int], walls=()) -> dict:
    """The bridge courses: a 3x3 start pad (x -1..1) closed by glass on three sides,
    a pit MOAT_DEPTH deep east of it (x 2..9, z -4..4) fenced in glass so its far rims
    cannot be reached, a one-cell stone island to stand on, and a glass roof over it
    all at ROOF_Y. ``walls``: glass (x, z) columns rising from the pit floor."""
    cells = _pit(range(2, 10), range(-4, 5), MOAT_DEPTH, keep={island, *walls})
    for x, z in walls:
        cells |= _box([x], range(-MOAT_DEPTH, ROOF_Y), [z], GLASS)
    fence = {(x, z) for x in range(1, 11) for z in (-5, 5)}
    fence |= {(10, z) for z in range(-5, 6)}
    fence |= {(1, z) for z in (*range(-5, -1), *range(2, 6))}
    fence |= {(x, z) for x in range(-2, 2) for z in (-2, 2)}
    fence |= {(-2, z) for z in range(-2, 3)}
    for x, z in fence:
        cells |= _box([x], range(0, ROOF_Y), [z], GLASS)
    cells |= _box(range(-2, 11), [ROOF_Y], range(-5, 6), GLASS)
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
    # The bridge courses hand out the bridge's scaffolding and two spare: never enough
    # for a column down to the pit floor (MOAT_DEPTH) and up again.
    Challenge(
        "bridge",
        "advanced",
        "Bridge straight across the pit to the island, without falling in. Leave the"
        " bridge standing.",
        _moat((7, 0)),
        goal=(7, 0, 0),
        scaffolding=8,
        bridge=True,
    ),
    Challenge(
        "bridge-l",
        "advanced",
        "Bridge to the island round one corner (it is off your lines), no falling in."
        " Leave the bridge standing.",
        _moat((4, -3)),
        goal=(4, 0, -3),
        scaffolding=7,
        bridge=True,
    ),
    Challenge(
        "bridge-u",
        "advanced",
        "Bridge round the glass wall to the island behind it, without falling in."
        " Leave the bridge standing.",
        _moat((4, 0), walls=[(3, -1), (3, 0), (3, 1)]),
        goal=(4, 0, 0),
        scaffolding=8,
        bridge=True,
    ),
    Challenge(
        "stairs",
        "advanced",
        "Get to the green marker upstairs: take the stairs, not scaffolding.",
        _house(),
        goal=(7, 4, -2),
        scaffolding=16,
        scaffold_free=True,
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
    """The subject's inventory: scaffolding only (it breaks by hand; a pickaxe would
    dig a way out of a pit or a supply of stone to pillar with)."""
    return [{"item": SCAFFOLDING, "count": c.scaffolding}]


def open_space(c: Challenge) -> tuple[set[Offset], bool]:
    """The air the start is in (6-neighbour flood through non-solid cells), and
    whether it leaves the course box: True means the course is open to the world
    round it, so a subject could go round it."""
    dx, dy, dz = c.clear_extent()
    floor = min([p[1] for p in c.cells] + [0]) - 1
    seen, todo, leaks = {(0, 0, 0)}, deque([(0, 0, 0)]), False
    while todo:
        x, y, z = todo.popleft()
        for n in (
            (x + 1, y, z),
            (x - 1, y, z),
            (x, y + 1, z),
            (x, y - 1, z),
            (x, y, z + 1),
            (x, y, z - 1),
        ):
            if n in seen or c.state(n) != AIR:
                continue
            if abs(n[0]) >= dx or abs(n[2]) >= dz or n[1] >= dy or n[1] <= floor:
                leaks = True
                continue
            seen.add(n)
            todo.append(n)
    return seen, leaks


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


def _bridge(
    c: Challenge,
    limit: int,
    extra: Mapping[Offset, str] | None,
    rank: Callable[[int, int], tuple[int, int]],
) -> tuple[int, int] | None:
    """The best scaffold bridge at the ground's height (dy=0) from the start to the
    goal column by ``rank(turns, cells)``, as that rank, or None when no bridge within
    scaffold distance ``limit`` gets there. A cell over ground has distance 0 (walk
    on); each cell out over air is one more than the cell before it. A run out costs
    its air cells and the grounded cell it stands on (scaffolding over air falls
    unless a column on the ground holds it). A turn is a change of direction between
    two cells out; stepping off onto ground is free. ``extra``: cells that are
    built."""
    extra = extra or {}
    dx, _dy, dz = c.clear_extent()

    def open_(x, z):
        state = extra.get((x, 0, z), c.state((x, 0, z)))
        return state in (AIR, SCAFFOLDING) and abs(x) <= dx and abs(z) <= dz

    def grounded(x, z):
        return _solid(c, (x, -1, z), extra)

    goal = (c.goal[0], c.goal[2])
    start = (0, 0, None, 0)  # x, z, last bridge direction, distance
    best = {start: rank(0, 0)}
    todo = [(rank(0, 0), 0, 0, start)]
    while todo:
        key, turns, cells, state = heapq.heappop(todo)
        if best.get(state, key) < key:
            continue
        x, z, heading, dist = state
        if (x, z) == goal and grounded(x, z):
            return key
        for step in STEPS:
            nx, nz = x + step[0], z + step[1]
            if not open_(nx, nz):
                continue
            if grounded(nx, nz):
                nxt, turn, more = (nx, nz, None, 0), 0, 0
            elif dist + 1 > limit:
                continue
            elif dist == 0:  # the first cell out (and its root); the next sets the line
                nxt, turn, more = (nx, nz, None, 1), 0, 2
            else:
                nxt = (nx, nz, step, dist + 1)
                turn, more = int(heading not in (None, step)), 1
            new = rank(turns + turn, cells + more)
            if new < best.get(nxt, (1 << 30, 1 << 30)):
                best[nxt] = new
                heapq.heappush(todo, (new, turns + turn, cells + more, nxt))
    return None


def bridge_turns(
    c: Challenge,
    *,
    limit: int = SCAFFOLD_DISTANCE,
    extra: Mapping[Offset, str] | None = None,
) -> int | None:
    """Fewest turns of a scaffold bridge to the goal (``_bridge``), or None."""
    best = _bridge(c, limit, extra, lambda turns, cells: (turns, cells))
    return None if best is None else best[0]


def bridge_length(
    c: Challenge,
    *,
    limit: int = SCAFFOLD_DISTANCE,
    extra: Mapping[Offset, str] | None = None,
) -> int | None:
    """Fewest scaffolding a bridge to the goal places (``_bridge``), or None."""
    best = _bridge(c, limit, extra, lambda turns, cells: (cells, turns))
    return None if best is None else best[0]


def bridge_area(c: Challenge) -> list[Offset]:
    """The open cells a bridge can stand in (the start's air from the ground top up),
    read back after an attempt (one lab kit probe)."""
    space, _ = open_space(c)
    return sorted(p for p in space if p[1] >= 0)


def bridge_stands(c: Challenge, scaffolding: set[Offset]) -> bool:
    """Does scaffolding stand, joined face to face, from a root on ground the start
    walks to as far as the goal column or a cell beside it? A jump across the pit
    leaves none, or a bridge with a gap (review of e029b74: a sprint jump from the
    pad's corner reached the bridge-l island with no scaffolding)."""
    gx, gz = c.goal[0], c.goal[2]
    roots = [
        p
        for p in scaffolding
        if p[1] == 0
        and _solid(c, (p[0], -1, p[2]), {})
        and abs(p[0] - gx) + abs(p[2] - gz) > 1
        and walkable(c, p)
    ]
    seen, todo = set(roots), deque(roots)
    while todo:
        x, y, z = todo.popleft()
        if abs(x - gx) + abs(z - gz) <= 1:
            return True
        for n in (
            (x + 1, y, z),
            (x - 1, y, z),
            (x, y + 1, z),
            (x, y - 1, z),
            (x, y, z + 1),
            (x, y, z - 1),
        ):
            if n in scaffolding and n not in seen:
                seen.add(n)
                todo.append(n)
    return False


def verdict(
    c: Challenge,
    *,
    reached: bool,
    damage: float,
    scaffolding_used: int,
    built_changed: int,
    bridge_stands: bool | None = None,
) -> dict:
    problems = []
    if not reached:
        problems.append("goal not reached")
    if c.no_damage and damage > 0:
        problems.append(f"took {damage} damage")
    if built_changed:
        problems.append(f"changed {built_changed} course cells")
    if c.scaffold_free and scaffolding_used:
        problems.append(f"placed {scaffolding_used} scaffolding (stairs were there)")
    if c.bridge and not bridge_stands:
        problems.append("no scaffolding bridge stands from the pad to the island")
    return {
        "passed": not problems,
        "problems": problems,
        "reached": reached,
        "damage": damage,
        "scaffolding_used": scaffolding_used,
        "built_changed": built_changed,
        **({"bridge_stands": bridge_stands} if c.bridge else {}),
    }
