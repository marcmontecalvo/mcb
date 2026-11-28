"""Arena plans: block fills plus markers, voxelised in memory and validated.

A plan is the contract between the generators (``designs.py``) and the mod
runtime that places, protects and fills an arena with mobs.  Coordinates are
local; the origin is the minimum corner and marker coordinates are feet cells
(the air block a player or mob stands in).
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass

SCHEMA = 1
MAX_FILL_VOLUME = 32768
MAX_HEIGHT = 64
MAX_FOOTPRINT = 256
MAX_PALETTE = 255
TEAM_NAMES = ("red", "blue", "green", "yellow")
MIN_SPAWN_DISTANCE = 12
MIN_LIGHT = 8
MAX_DROP = 3
AIR = "minecraft:air"

Cell = tuple[int, int, int]


# --- model ------------------------------------------------------------------


@dataclass(frozen=True)
class Fill:
    frm: Cell
    to: Cell
    block: str


@dataclass(frozen=True)
class Team:
    pads: list[Cell]
    entry: Cell


@dataclass(frozen=True)
class Capacity:
    teams: int
    per_team: int
    mobs: int


@dataclass(frozen=True)
class Plan:
    name: str
    size: Cell
    fills: list[Fill]
    teams: dict[str, Team]
    mob_spawns: list[Cell]
    spectator: Cell
    capacity: Capacity
    schema: int = SCHEMA


def _cell(value) -> Cell:
    x, y, z = value
    return (int(x), int(y), int(z))


def to_dict(plan: Plan) -> dict:
    return {
        "schema": plan.schema,
        "name": plan.name,
        "size": list(plan.size),
        "fills": [
            {"from": list(f.frm), "to": list(f.to), "block": f.block}
            for f in plan.fills
        ],
        "markers": {
            "teams": {
                name: {"pads": [list(p) for p in team.pads], "entry": list(team.entry)}
                for name, team in plan.teams.items()
            },
            "mob_spawns": [list(p) for p in plan.mob_spawns],
            "spectator": list(plan.spectator),
        },
        "capacity": {
            "teams": plan.capacity.teams,
            "per_team": plan.capacity.per_team,
            "mobs": plan.capacity.mobs,
        },
    }


def to_json(plan: Plan) -> str:
    """Deterministic JSON with one fill per line so committed diffs stay readable."""
    data = to_dict(plan)
    compact = {"separators": (", ", ": ")}
    fills = ",\n".join("    " + json.dumps(fill, **compact) for fill in data["fills"])
    marks = data["markers"]
    teams = ",\n".join(
        f"      {json.dumps(name)}: {json.dumps(team, **compact)}"
        for name, team in marks["teams"].items()
    )
    markers = (
        "{\n"
        f'    "teams": {{\n{teams}\n    }},\n'
        f'    "mob_spawns": {json.dumps(marks["mob_spawns"], **compact)},\n'
        f'    "spectator": {json.dumps(marks["spectator"], **compact)}\n'
        "  }"
    )
    return (
        "{\n"
        f'  "schema": {data["schema"]},\n'
        f'  "name": {json.dumps(data["name"])},\n'
        f'  "size": {json.dumps(data["size"], **compact)},\n'
        f'  "fills": [\n{fills}\n  ],\n'
        f'  "markers": {markers},\n'
        f'  "capacity": {json.dumps(data["capacity"], **compact)}\n'
        "}\n"
    )


def from_json(text: str) -> Plan:
    try:
        data = json.loads(text)
        markers = data["markers"]
        capacity = data["capacity"]
        return Plan(
            name=data["name"],
            size=_cell(data["size"]),
            fills=[
                Fill(_cell(f["from"]), _cell(f["to"]), f["block"])
                for f in data["fills"]
            ],
            teams={
                name: Team(
                    pads=[_cell(p) for p in team["pads"]], entry=_cell(team["entry"])
                )
                for name, team in markers["teams"].items()
            },
            mob_spawns=[_cell(p) for p in markers["mob_spawns"]],
            spectator=_cell(markers["spectator"]),
            capacity=Capacity(
                int(capacity["teams"]), int(capacity["per_team"]), int(capacity["mobs"])
            ),
            schema=int(data["schema"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"not an arena plan: {error!r}") from error


# --- block classes ----------------------------------------------------------

_BLOCK_RE = re.compile(
    r"^minecraft:[a-z0-9_]+(\[[a-z_]+=[a-z0-9_]+(,[a-z_]+=[a-z0-9_]+)*\])?$"
)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_BANNED_EXACT = frozenset(
    """
    sand red_sand suspicious_sand suspicious_gravel gravel anvil chipped_anvil damaged_anvil
    dragon_egg scaffolding water lava bubble_column fire soul_fire tnt repeater comparator lever
    observer piston sticky_piston piston_head moving_piston target daylight_detector tripwire
    tripwire_hook note_block jukebox chest trapped_chest ender_chest barrel hopper dropper
    dispenser furnace blast_furnace smoker crafter spawner trial_spawner vault ominous_vault
    wheat carrots potatoes beetroots melon_stem pumpkin_stem attached_melon_stem
    attached_pumpkin_stem nether_wart sweet_berry_bush cocoa torchflower_crop pitcher_crop
    bamboo sugar_cane cactus kelp kelp_plant vine ice packed_ice blue_ice frosted_ice snow
    snow_block powder_snow grass_block mycelium dirt_path farmland magma_block campfire
    soul_campfire cobweb sculk sculk_sensor sculk_shrieker sculk_catalyst sculk_vein
    turtle_egg frogspawn sea_pickle cake end_portal end_portal_frame nether_portal end_gateway
    command_block chain_command_block repeating_command_block structure_block jigsaw barrier
    fire_coral bell composter cauldron lava_cauldron water_cauldron brewing_stand lectern
    """.split()
)
_BANNED_SUFFIX = (
    "_concrete_powder",
    "_door",
    "_trapdoor",
    "_button",
    "_pressure_plate",
    "_bed",
    "_carpet",
    "_leaves",
    "_shulker_box",
    "_sapling",
    "_fence_gate",
    "_sign",
    "_hanging_sign",
    "_bush",
    "_roots",
    "_fungus",
)
_BANNED_PREFIX = ("redstone", "potted_")

_EMISSION = {
    "lantern": 15,
    "soul_lantern": 10,
    "sea_lantern": 15,
    "glowstone": 15,
    "shroomlight": 15,
    "ochre_froglight": 15,
    "verdant_froglight": 15,
    "pearlescent_froglight": 15,
    "jack_o_lantern": 15,
    "torch": 14,
    "wall_torch": 14,
    "soul_torch": 10,
    "soul_wall_torch": 10,
    "end_rod": 14,
    "beacon": 15,
    "crying_obsidian": 10,
}


def base_name(block: str) -> str:
    return block.split("[", 1)[0].removeprefix("minecraft:")


def is_banned(base: str) -> bool:
    return (
        base in _BANNED_EXACT
        or base.endswith(_BANNED_SUFFIX)
        or base.startswith(_BANNED_PREFIX)
    )


LOBBY_PLAN = "lobby"


def is_lobby_station_block(base: str) -> bool:
    """Wall signs and buttons: only the lobby room may use them (its menu stations)."""
    return base.endswith(("_wall_sign", "_button"))


def is_glass(base: str) -> bool:
    return base.endswith("glass") or base.endswith("glass_pane")


def is_passable(base: str) -> bool:
    """Blocks a body walks through (air, wall banners, wall signs, buttons; all else solid)."""
    return base in ("air", "cave_air", "void_air") or base.endswith(
        ("wall_banner", "_wall_sign", "_button")
    )


def emission(base: str) -> int:
    return _EMISSION.get(base, 0)


# --- fills ------------------------------------------------------------------


def fill_volume(fill: Fill) -> int:
    (x0, y0, z0), (x1, y1, z1) = fill.frm, fill.to
    return (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)


def split_fill(fill: Fill, limit: int = MAX_FILL_VOLUME) -> list[Fill]:
    """Split a fill into pieces of at most ``limit`` blocks, halving the longest axis."""
    if fill_volume(fill) <= limit:
        return [fill]
    lo, hi = list(fill.frm), list(fill.to)
    axis = max(range(3), key=lambda a: hi[a] - lo[a])
    mid = (lo[axis] + hi[axis]) // 2
    first_hi = hi.copy()
    first_hi[axis] = mid
    second_lo = lo.copy()
    second_lo[axis] = mid + 1
    return split_fill(Fill(fill.frm, tuple(first_hi), fill.block), limit) + split_fill(
        Fill(tuple(second_lo), fill.to, fill.block), limit
    )


# --- voxel grid -------------------------------------------------------------

_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True)
class Walker:
    """A body that walks: footprint width, height, longest step up and drop."""

    width: int = 1
    height: int = 2
    up: int = 1
    drop: int = MAX_DROP


PLAYER = Walker()
RAVAGER = Walker(width=2, height=4, up=1, drop=MAX_DROP)


class Grid:
    """The plan resolved into one palette index per cell (x-major, then y, then z)."""

    def __init__(self, size: Cell, palette: list[str], data: bytearray):
        self.sx, self.sy, self.sz = size
        self.palette = palette
        self.data = data
        bases = [base_name(block) for block in palette]
        self.solid = data.translate(_table(bases, lambda b: 0 if is_passable(b) else 1))
        self.glass = data.translate(_table(bases, lambda b: 1 if is_glass(b) else 0))
        self.trans = data.translate(
            _table(bases, lambda b: 1 if is_passable(b) or is_glass(b) else 0)
        )
        self.emit = data.translate(_table(bases, emission))

    def idx(self, x: int, y: int, z: int) -> int:
        return (x * self.sy + y) * self.sz + z

    def in_bounds(self, x: int, y: int, z: int) -> bool:
        return 0 <= x < self.sx and 0 <= y < self.sy and 0 <= z < self.sz

    def block(self, x: int, y: int, z: int) -> str:
        return self.palette[self.data[self.idx(x, y, z)]]

    def base(self, x: int, y: int, z: int) -> str:
        return base_name(self.block(x, y, z))

    def is_solid(self, x: int, y: int, z: int) -> bool:
        return not self.in_bounds(x, y, z) or bool(self.solid[self.idx(x, y, z)])

    def _free(self, w: Walker, x: int, z: int, y0: int, y1: int) -> bool:
        """Cells y0..y1 over the whole footprint are passable (and inside the grid)."""
        if y1 < y0:
            return True
        if y0 < 0 or y1 >= self.sy:
            return False
        for dx in range(w.width):
            for dz in range(w.width):
                xx, zz = x + dx, z + dz
                if not (0 <= xx < self.sx and 0 <= zz < self.sz):
                    return False
                i = self.idx(xx, y0, zz)
                for k in range(y1 - y0 + 1):
                    if self.solid[i + k * self.sz]:
                        return False
        return True

    def fits(self, w: Walker, x: int, y: int, z: int) -> bool:
        """The body can stand here: solid floor under the footprint, ``height`` air above."""
        if y < 1 or not self._free(w, x, z, y, y + w.height - 1):
            return False
        # A wide body may overhang a step one block lower, but needs a floor under at least
        # one column at its own level (so it can straddle the edge of a stair).
        level = 0
        for dx in range(w.width):
            for dz in range(w.width):
                if self.solid[self.idx(x + dx, y - 1, z + dz)]:
                    level += 1
                elif (
                    w.width == 1
                    or y < 2
                    or not self.solid[self.idx(x + dx, y - 2, z + dz)]
                ):
                    return False
        return level > 0

    def moves(self, w: Walker, cell: Cell) -> list[Cell]:
        x, y, z = cell
        out = []
        for dx, dz in _DIRS:
            nx, nz = x + dx, z + dz
            for ny in range(y + w.up, y - w.drop - 1, -1):
                if not self.fits(w, nx, ny, nz):
                    continue
                if ny > y and not self._free(w, x, z, y + w.height, ny + w.height - 1):
                    continue
                if ny < y and not self._free(
                    w, nx, nz, ny + w.height, y + w.height - 1
                ):
                    continue
                out.append((nx, ny, nz))
        return out

    def standable(self, w: Walker = None) -> list[Cell]:
        w = w or PLAYER
        cells = []
        for x in range(self.sx):
            for z in range(self.sz):
                for y in range(1, self.sy - w.height + 1):
                    if self.fits(w, x, y, z):
                        cells.append((x, y, z))
        return cells


def _table(bases: list[str], fn) -> bytes:
    table = bytearray(256)
    for i, base in enumerate(bases):
        table[i] = fn(base)
    return bytes(table)


def voxelize(plan: Plan) -> Grid:
    """Resolve fills in order (later wins) into a grid. Fills must be in bounds."""
    sx, sy, sz = plan.size
    palette: list[str] = []
    lookup: dict[str, int] = {}
    data = bytearray(sx * sy * sz)
    for fill in plan.fills:
        if fill.block not in lookup:
            lookup[fill.block] = len(palette)
            palette.append(fill.block)
        row = bytes([lookup[fill.block]]) * (fill.to[2] - fill.frm[2] + 1)
        for x in range(fill.frm[0], fill.to[0] + 1):
            for y in range(fill.frm[1], fill.to[1] + 1):
                start = (x * sy + y) * sz + fill.frm[2]
                data[start : start + len(row)] = row
    return Grid(plan.size, palette, data)


def flood(grid: Grid, walker: Walker, starts: Iterable[Cell]) -> set[Cell]:
    return set(reach_graph(grid, walker, starts))


def reach_graph(
    grid: Grid, walker: Walker, starts: Iterable[Cell]
) -> dict[Cell, list[Cell]]:
    """Breadth-first walk; returns every reachable cell with its outgoing moves."""
    graph: dict[Cell, list[Cell]] = {}
    queue = deque()
    for start in starts:
        if start not in graph and grid.fits(walker, *start):
            graph[start] = []
            queue.append(start)
    while queue:
        cell = queue.popleft()
        graph[cell] = grid.moves(walker, cell)
        for nxt in graph[cell]:
            if nxt not in graph:
                graph[nxt] = []
                queue.append(nxt)
    return graph


def light_levels(grid: Grid) -> bytearray:
    """Block light per cell: 1 lost per step through air or glass, stopped by solids."""
    n = len(grid.data)
    light = bytearray(n)
    buckets: list[list[int]] = [[] for _ in range(16)]
    for i in range(n):
        level = grid.emit[i]
        if level:
            light[i] = level
            buckets[level].append(i)
    sy, sz = grid.sy, grid.sz
    ystride, xstride = sz, sy * sz
    for level in range(15, 1, -1):
        for i in buckets[level]:
            if light[i] != level:
                continue
            x, rest = divmod(i, xstride)
            y, z = divmod(rest, sz)
            neighbours = []
            if x > 0:
                neighbours.append(i - xstride)
            if x < grid.sx - 1:
                neighbours.append(i + xstride)
            if y > 0:
                neighbours.append(i - ystride)
            if y < sy - 1:
                neighbours.append(i + ystride)
            if z > 0:
                neighbours.append(i - 1)
            if z < sz - 1:
                neighbours.append(i + 1)
            for j in neighbours:
                if grid.trans[j] and light[j] < level - 1:
                    light[j] = level - 1
                    buckets[level - 1].append(j)
    return light


# --- validation -------------------------------------------------------------


def _sample(cells: Iterable[Cell], count: int = 4) -> str:
    cells = sorted(cells)
    text = ", ".join(str(list(c)) for c in cells[:count])
    return f"{text}{' ...' if len(cells) > count else ''}"


def _clears_bounds(plan: Plan) -> bool:
    """The run of air fills at the start covers every cell (a big clear is split into pieces)."""
    sx, sy, sz = plan.size
    covered = bytearray(sx * sy * sz)
    for fill in plan.fills:
        if base_name(fill.block) != "air":
            break
        if any(
            a > b or a < 0 or b >= s
            for a, b, s in zip(fill.frm, fill.to, plan.size, strict=True)
        ):
            return False
        row = b"" * (fill.to[2] - fill.frm[2] + 1)
        for x in range(fill.frm[0], fill.to[0] + 1):
            for y in range(fill.frm[1], fill.to[1] + 1):
                start = (x * sy + y) * sz + fill.frm[2]
                covered[start : start + len(row)] = row
    return all(covered)


def _structure(plan: Plan) -> list[str]:
    problems = []
    if plan.schema != SCHEMA:
        problems.append(f"schema: expected {SCHEMA}, got {plan.schema}")
    if not _NAME_RE.match(plan.name or ""):
        problems.append(
            f"name: {plan.name!r} must be lowercase letters, digits and underscores"
        )
    sx, sy, sz = plan.size
    if min(plan.size) < 1 or sy > MAX_HEIGHT or max(sx, sz) > MAX_FOOTPRINT:
        problems.append(
            f"size: {list(plan.size)} must be positive, height <= {MAX_HEIGHT}, footprint <= {MAX_FOOTPRINT}"
        )
        return problems
    if not plan.fills:
        problems.append("clear-first: the plan has no fills")
        return problems
    if not _clears_bounds(plan):
        problems.append(
            "clear-first: the leading air fills must clear the whole bounds to minecraft:air"
        )
    blocks = set()
    banned = set()
    for n, fill in enumerate(plan.fills):
        blocks.add(fill.block)
        if not _BLOCK_RE.match(fill.block):
            problems.append(
                f"block-format: fill {n} has a malformed block {fill.block!r}"
            )
            continue
        base = base_name(fill.block)
        if is_banned(base) and not (
            plan.name == LOBBY_PLAN and is_lobby_station_block(base)
        ):
            banned.add(fill.block)
        if any(a > b for a, b in zip(fill.frm, fill.to, strict=True)):
            problems.append(
                f"fill-order: fill {n} {list(fill.frm)}..{list(fill.to)} has from > to"
            )
            continue
        if any(c < 0 for c in fill.frm) or any(
            c >= s for c, s in zip(fill.to, plan.size, strict=True)
        ):
            problems.append(
                f"fill-bounds: fill {n} {list(fill.frm)}..{list(fill.to)} leaves the bounds"
            )
            continue
        if fill_volume(fill) > MAX_FILL_VOLUME:
            problems.append(
                f"fill-volume: fill {n} covers {fill_volume(fill)} blocks (max {MAX_FILL_VOLUME})"
            )
    for block in sorted(banned):
        problems.append(f"banned: {block} may not be used")
    if len(blocks) > MAX_PALETTE:
        problems.append(f"palette: {len(blocks)} distinct blocks (max {MAX_PALETTE})")
    return problems


_FATAL = (
    "schema",
    "name",
    "size",
    "clear-first",
    "block-format",
    "fill-order",
    "fill-bounds",
    "fill-volume",
    "palette",
)


def _marker_problems(plan: Plan) -> tuple[list[str], list[tuple[str, Cell]]]:
    problems = []
    markers: list[tuple[str, Cell]] = []
    if not plan.teams or len(plan.teams) > len(TEAM_NAMES):
        problems.append(
            f"team: need 1 to {len(TEAM_NAMES)} teams, got {len(plan.teams)}"
        )
    seen = set()
    for name, team in plan.teams.items():
        if name not in TEAM_NAMES:
            problems.append(f"team: {name!r} is not one of {', '.join(TEAM_NAMES)}")
        if not team.pads:
            problems.append(f"team: {name} has no pads")
        for pad in team.pads:
            if pad in seen:
                problems.append(f"team: pad {list(pad)} is listed twice")
            seen.add(pad)
            markers.append((f"pad {name}", pad))
        markers.append((f"entry {name}", team.entry))
    markers.extend(("spawn", s) for s in plan.mob_spawns)
    markers.append(("spectator", plan.spectator))
    sx, sy, sz = plan.size
    for label, (x, y, z) in markers:
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            problems.append(f"marker-bounds: {label} {[x, y, z]} is outside the bounds")
    return problems, markers


def _capacity_problems(plan: Plan) -> list[str]:
    cap = plan.capacity
    problems = []
    if cap.teams != len(plan.teams):
        problems.append(
            f"capacity: teams={cap.teams} but {len(plan.teams)} team markers"
        )
    if cap.per_team < 1:
        problems.append(f"capacity: per_team={cap.per_team} must be at least 1")
    for name, team in plan.teams.items():
        if len(team.pads) < cap.per_team:
            problems.append(
                f"capacity: {name} has {len(team.pads)} pads for per_team={cap.per_team}"
            )
    if cap.mobs < 0:
        problems.append(f"capacity: mobs={cap.mobs} must not be negative")
    if cap.mobs > 0 and not plan.mob_spawns:
        problems.append("capacity: mobs are expected but there are no mob spawn points")
    return problems


def _sealed(grid: Grid) -> list[str]:
    sx, sy, sz = grid.sx, grid.sy, grid.sz
    open_cells = []
    top_glass = []
    for x in range(sx):
        for y in range(sy):
            for z in range(sz):
                if x in (0, sx - 1) or y in (0, sy - 1) or z in (0, sz - 1):
                    i = grid.idx(x, y, z)
                    if not grid.solid[i]:
                        open_cells.append((x, y, z))
                    elif y == sy - 1 and grid.glass[i]:
                        top_glass.append((x, y, z))
    problems = []
    if open_cells:
        problems.append(
            f"shell-open: {len(open_cells)} outer cells are not solid, e.g. {_sample(open_cells)}"
        )
    if top_glass:
        problems.append(
            f"shell-open: glass in the top face lets sunlight in, e.g. {_sample(top_glass)}"
        )
    return problems


def _roof_problems(grid: Grid, cells: Iterable[Cell]) -> list[str]:
    glassy, open_ = [], []
    for x, y, z in cells:
        solid_roof = False
        has_glass = False
        for yy in range(y + 1, grid.sy):
            i = grid.idx(x, yy, z)
            if grid.glass[i]:
                has_glass = True
            elif grid.solid[i] and grid.emit[i] == 0:
                solid_roof = True
        if has_glass:
            glassy.append((x, y, z))
        if not solid_roof:
            open_.append((x, y, z))
    problems = []
    if glassy:
        problems.append(
            f"roof-glass: glass above {len(glassy)} floor cells, e.g. {_sample(glassy)}"
        )
    if open_:
        problems.append(
            f"roof-open: no solid roof above {len(open_)} floor cells, e.g. {_sample(open_)}"
        )
    return problems


def _clear_sight(grid: Grid, a: Cell, b: Cell) -> bool:
    """True if the line from an eye above ``a`` to ``b`` passes only air and glass, through glass."""
    start = (a[0] + 0.5, a[1] + 1.62, a[2] + 0.5)
    end = (b[0] + 0.5, b[1] + 1.0, b[2] + 0.5)
    length = math.dist(start, end)
    steps = max(1, int(length / 0.2))
    saw_glass = False
    for k in range(steps + 1):
        t = k / steps
        x, y, z = (math.floor(start[i] + (end[i] - start[i]) * t) for i in range(3))
        if not grid.in_bounds(x, y, z):
            return False
        i = grid.idx(x, y, z)
        if grid.glass[i]:
            saw_glass = True
        elif grid.solid[i]:
            return False
    return saw_glass


def validate(plan: Plan) -> list[str]:
    """Every rule the runtime and the bot rely on; an empty list means the plan is usable."""
    problems = _structure(plan)
    if any(p.startswith(_FATAL) for p in problems):
        return problems
    marker_problems, markers = _marker_problems(plan)
    problems += marker_problems + _capacity_problems(plan)
    grid = voxelize(plan)
    problems += _sealed(grid)
    if any(p.startswith("marker-bounds") for p in marker_problems):
        return problems

    for label, cell in markers:
        if label.startswith("pad") and not grid.fits(PLAYER, *cell):
            problems.append(
                f"pad-standable: {label} {list(cell)} needs a solid floor and 2 air above"
            )
        elif label.startswith("entry") and not grid.fits(PLAYER, *cell):
            problems.append(
                f"entry-standable: {label} {list(cell)} needs a solid floor and 2 air above"
            )
        elif label == "spectator" and not grid.fits(PLAYER, *cell):
            problems.append(
                f"spectator-standable: {list(cell)} needs a solid floor and 2 air above"
            )

    pads = [(name, pad) for name, team in plan.teams.items() for pad in team.pads]
    for spawn in plan.mob_spawns:
        x, y, z = spawn
        around = [(x + dx, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
        if any(grid.is_solid(ax, y - 1, az) is False for ax, az in around) or y < 1:
            problems.append(f"spawn-floor: {list(spawn)} needs a solid 3x3 floor")
        if not all(
            not grid.is_solid(ax, yy, az) and grid.in_bounds(ax, yy, az)
            for ax, az in around
            for yy in range(y, y + 3)
        ):
            problems.append(
                f"spawn-air: {list(spawn)} needs a 3x3 column with 3 air above"
            )
        for _, pad in pads:
            if math.hypot(x - pad[0], z - pad[2]) < MIN_SPAWN_DISTANCE:
                problems.append(
                    f"spawn-distance: {list(spawn)} is within {MIN_SPAWN_DISTANCE} of pad {list(pad)}"
                )
                break

    start = next((pad for _, pad in pads if grid.fits(PLAYER, *pad)), None)
    reach: dict[Cell, list[Cell]] = {}
    if start is not None:
        reach = reach_graph(grid, PLAYER, [start])
        for label, cell in markers:
            if label != "spectator" and grid.fits(PLAYER, *cell) and cell not in reach:
                problems.append(
                    f"marker-unreachable: {label} {list(cell)} cannot be walked to from the pads"
                )
        if reach:
            back = defaultdict(list)
            for cell, outs in reach.items():
                for out in outs:
                    back[out].append(cell)
            seen = {start}
            queue = deque([start])
            while queue:
                for prev in back[queue.popleft()]:
                    if prev not in seen:
                        seen.add(prev)
                        queue.append(prev)
            trapped = set(reach) - seen
            if trapped:
                problems.append(
                    f"floor-trap: {len(trapped)} cells cannot be walked out of, e.g. {_sample(trapped)}"
                )

    gallery: set[Cell] = set()
    if grid.fits(PLAYER, *plan.spectator):
        gallery = flood(grid, PLAYER, [plan.spectator])
        floor_markers = [c for label, c in markers if label != "spectator"]
        if (gallery & set(reach)) or any(c in gallery for c in floor_markers):
            problems.append("spectator-path: the spectator can walk to the fight floor")
        if (
            any(
                _clear_sight(grid, plan.spectator, target)
                for _, target in pads + [("spawn", s) for s in plan.mob_spawns]
            )
            is False
        ):
            problems.append(
                "spectator-view: no clear line of sight through glass from the spectator to the floor"
            )

    if start is not None:
        loose = set(grid.standable()) - set(reach) - gallery
        if loose:
            problems.append(
                f"floor-unreachable: {len(loose)} standable cells cannot be reached on foot "
                f"(perches), e.g. {_sample(loose)}"
            )

    walkable = set(reach) | gallery
    problems += _roof_problems(grid, walkable)
    light = light_levels(grid)
    dark = [c for c in walkable if light[grid.idx(*c)] < MIN_LIGHT]
    if dark:
        problems.append(
            f"light: {len(dark)} floor cells are darker than {MIN_LIGHT}, e.g. {_sample(dark)}"
        )

    problems += _ravager_problems(plan, grid, pads)
    return problems


def _ravager_problems(
    plan: Plan, grid: Grid, pads: list[tuple[str, Cell]]
) -> list[str]:
    problems = []
    starts = []
    for spawn in plan.mob_spawns:
        x, y, z = spawn
        anchors = [
            (x - a, y, z - b)
            for a in (0, 1)
            for b in (0, 1)
            if grid.fits(RAVAGER, x - a, y, z - b)
        ]
        if not anchors:
            problems.append(f"ravager: no 2x2x4 body fits at spawn {list(spawn)}")
        starts += anchors
    if not starts:
        return problems
    reach = flood(grid, RAVAGER, starts)
    for name, pad in pads:
        near = any(
            ax - 2 <= pad[0] <= ax + 3
            and az - 2 <= pad[2] <= az + 3
            and abs(ay - pad[1]) <= 1
            for ax, ay, az in reach
        )
        if not near:
            problems.append(
                f"ravager: no ravager path reaches {name} pad {list(pad)} from the mob spawns"
            )
    return problems
