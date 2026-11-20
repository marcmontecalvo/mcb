"""Schematic builder planning (milestone M2): pure logic, no I/O.

The world is a callable ``world(x, y, z) -> state string | None`` (None = unknown,
never assumed air or support). Block facts are a dict in blocks.json shape
(``{"minecraft:stone": {"item": ..., "gravity": ..., "needs_support": ...}}``).
Contract: docs/KNOWLEDGE_PACK_FORMAT.md, "Build file" and "blocks.json".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from mclab import scaffolding as sc
from mclab.schematic import (
    format_state,
    from_build_json,
    parse_state,
    state_materials,
)

Pos = tuple[int, int, int]

AIR = frozenset({"minecraft:air", "minecraft:cave_air", "minecraft:void_air"})
FLUIDS = frozenset({"minecraft:water", "minecraft:lava"})
# Always water, yet carry no ``waterlogged`` property.
WATER_PLANTS = frozenset(
    "minecraft:" + n
    for n in ("seagrass", "tall_seagrass", "kelp", "kelp_plant", "bubble_column")
)


def is_wet(state: str | None) -> bool:
    """A fluid, water plant or waterlogged state: breaking beside it floods."""
    if not state:
        return False
    ident, props = parse_state(state)
    return (
        ident in FLUIDS or ident in WATER_PLANTS or props.get("waterlogged") == "true"
    )


REPLACEABLE = (
    AIR
    | FLUIDS
    | frozenset(
        "minecraft:" + n
        for n in (
            "short_grass",
            "grass",
            "tall_grass",
            "snow",
            "fern",
            "large_fern",
            "dead_bush",
            "vine",
        )
    )
)
POT = "minecraft:flower_pot"
# Cells with no item: place dirt, then Use a tool of this kind ("#kind" is resolved
# against the inventory in next_actions) on it (B2).
TOOL_USES = {"minecraft:farmland": "#hoe", "minecraft:dirt_path": "#shovel"}
TOOL_BASES = frozenset({"minecraft:dirt", "minecraft:grass_block"})
SCAFFOLD_ITEMS = (
    sc.BLOCK,
    "minecraft:dirt",
    "minecraft:cobblestone",
    "minecraft:netherrack",
)
MAX_SCAFFOLD_DEPTH = 64
FILL_ITEM = "minecraft:dirt"  # fills water under a block; stays in the build

_SIDES = frozenset({"north", "east", "south", "west"})
# (id suffix, ignored properties); suffix "" applies to every block.
DEFAULT_IGNORE = (
    (
        "",
        frozenset(
            {
                "distance",
                "persistent",
                "power",
                "powered",
                "waterlogged",
                "snowy",
                # books in a chiseled bookshelf (PlacementLogic.IGNORED has the same)
                *(f"slot_{i}_occupied" for i in range(6)),
            }
        ),
    ),
    ("farmland", frozenset({"moisture"})),
    ("_stairs", frozenset({"shape"})),
    ("_fence", _SIDES),
    ("_wall", _SIDES | {"up"}),
    ("_pane", _SIDES),
    ("iron_bars", _SIDES),
)

_CW = ("north", "east", "south", "west")
_VEC = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
    "up": (0, 1, 0),
    "down": (0, -1, 0),
}
_NEIGHBOURS = tuple(_VEC.values())
_RAIL_RANK = {"north": 0, "south": 1, "east": 2, "west": 3}


# --- positions ------------------------------------------------------------------


def _add(pos: Pos, vec: tuple[int, int, int]) -> Pos:
    return (pos[0] + vec[0], pos[1] + vec[1], pos[2] + vec[2])


# --- rotation / mirror ----------------------------------------------------------


def transform_state(state: str, quarter_turns: int, mirror: bool) -> str:
    """Mirror across X (east<->west), then rotate clockwise seen from above."""
    ident, props = parse_state(state)

    def face(d: str) -> str:
        if mirror and d in ("east", "west"):
            d = "west" if d == "east" else "east"
        if d in _CW:
            d = _CW[(_CW.index(d) + quarter_turns) % 4]
        return d

    swap_lr = {"left": "right", "right": "left"}
    out: dict[str, str] = {}
    for key, val in props.items():
        if key == "facing":
            out[key] = face(val)
        elif key == "axis":
            odd = quarter_turns % 2 == 1
            out[key] = {"x": "z", "z": "x"}.get(val, val) if odd else val
        elif key == "rotation":
            r = (-int(val)) % 16 if mirror else int(val)
            out[key] = str((r + 4 * quarter_turns) % 16)
        elif key in _SIDES:
            out[face(key)] = val
        elif key == "shape" and ident.endswith("_stairs"):
            if mirror:
                for a, b in (("left", "right"), ("right", "left")):
                    if val.endswith(a):
                        val = val[: -len(a)] + b
                        break
            out[key] = val
        elif key == "shape" and "rail" in ident:
            words = (
                [face(w) for w in val.split("_")] if "ascending" not in val else None
            )
            if words is None:
                head, d = val.split("_", 1)
                out[key] = f"{head}_{face(d)}"
            else:
                out[key] = "_".join(sorted(words, key=_RAIL_RANK.get))
        elif key == "hinge" and mirror:
            out[key] = swap_lr.get(val, val)
        elif key == "type" and mirror and "chest" in ident:
            out[key] = swap_lr.get(val, val)
        else:
            out[key] = val
    return format_state(ident, out)


# --- template -------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    cells: dict[Pos, str]  # world position -> target state (air included)
    size: tuple[int, int, int]  # footprint after rotation
    origin: Pos
    name: str = ""

    @classmethod
    def from_build(
        cls, build: dict, origin: Pos, rotation: int = 0, mirror: bool = False
    ) -> Template:
        if rotation not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180 or 270")
        turns = rotation // 90
        schem = from_build_json(build)
        sx, sy, sz = schem.size
        cache: dict[str, str] = {}
        cells: dict[Pos, str] = {}
        for y in range(sy):
            for z in range(sz):
                for x in range(sx):
                    state = schem.state_at(x, y, z)
                    if state not in cache:
                        cache[state] = transform_state(state, turns, mirror)
                    px, pz, w, d = (sx - 1 - x) if mirror else x, z, sx, sz  # noqa: E741
                    for _ in range(turns):  # clockwise: (x, z) -> (d-1-z, x)
                        px, pz, w, d = d - 1 - pz, px, d, w
                    cells[(origin[0] + px, origin[1] + y, origin[2] + pz)] = cache[
                        state
                    ]
        size = (sz, sy, sx) if turns % 2 else (sx, sy, sz)
        return cls(cells, size, tuple(origin), build.get("name", ""))


# --- diff -----------------------------------------------------------------------


def _ignored(ident: str, ignore) -> frozenset[str]:
    out: set[str] = set()
    for suffix, props in ignore:
        if ident.endswith(suffix):
            out |= props
    return frozenset(out)


def _same(target: str, current: str, ignore) -> bool:
    tid, tp = parse_state(target)
    cid, cp = parse_state(current)
    if tid in AIR and cid in AIR:
        return True
    if tid != cid:
        return False
    if tid in FLUIDS:
        return True
    skip = _ignored(tid, ignore)
    if tp.get("half") == "upper":
        skip |= {"open"}  # follows the lower half, which is the one that is used
    return all(cp.get(k) == v for k, v in tp.items() if k not in skip)


def _use_item(target: str, current: str, ignore) -> tuple[bool, str | None]:
    """(a Use fixes it, item to hold): a door to toggle, a pot waiting for its plant."""
    tid, tp = parse_state(target)
    cid, cp = parse_state(current)
    if tid in TOOL_USES and cid in TOOL_BASES:
        return True, TOOL_USES[tid]
    if tid.startswith("minecraft:potted_") and cid == POT:
        plant = [i for i in state_materials(target)[0] if i != POT]
        return bool(plant), (plant[0] if plant else None)
    if (
        tid == cid
        and tp.get("open") in ("true", "false")
        and cp.get("open") != tp["open"]
        and tp.get("half") != "upper"  # using the lower half moves both halves
        and _same(target, format_state(cid, {**cp, "open": tp["open"]}), ignore)
    ):
        return True, None
    return False, None


def _classify(target: str, current: str | None, ignore, keep_existing: bool) -> str:
    if current is None:
        return "unknown"
    tid = parse_state(target)[0]
    cid = parse_state(current)[0]
    if tid in AIR:
        # A fluid cannot be broken; leave it. keep_existing leaves anything.
        return "ok" if cid in AIR or cid in FLUIDS or keep_existing else "break"
    if _same(target, current, ignore):
        return "ok"
    if _use_item(target, current, ignore)[0]:
        return "use"
    if _half_of_double(target, current):
        return "place"  # the second slab goes on the half already there
    return "place" if cid in REPLACEABLE else "break"


def _half_of_double(target: str, current: str) -> bool:
    (tid, tp), (cid, cp) = parse_state(target), parse_state(current)
    return (
        tid == cid
        and tp.get("type") == "double"
        and cp.get("type") in ("bottom", "top")
        and cp.get("waterlogged", "false") == "false"
    )


@dataclass(frozen=True)
class Diff:
    breaks: list[Pos]
    places: list[tuple[Pos, str]]
    unknown: list[Pos]
    uses: list[tuple[Pos, str | None]] = field(default_factory=list)


def diff(
    template: Template, world, ignore_props=DEFAULT_IGNORE, keep_existing=False
) -> Diff:
    breaks: list[Pos] = []
    places: list[tuple[Pos, str]] = []
    unknown: list[Pos] = []
    uses: list[tuple[Pos, str | None]] = []
    for pos, target in template.cells.items():
        verdict = _classify(target, world(*pos), ignore_props, keep_existing)
        if verdict == "break":
            breaks.append(pos)
        elif verdict == "place":
            places.append((pos, target))
        elif verdict == "unknown":
            unknown.append(pos)
        elif verdict == "use":
            uses.append((pos, _use_item(target, world(*pos), ignore_props)[1]))
    return Diff(breaks, places, unknown, uses)


def progress(
    template: Template, world, ignore_props=DEFAULT_IGNORE, keep_existing=False
) -> dict:
    """Counts for reporting. ``correct`` counts non-air template cells only."""
    counts = Counter()
    for pos, target in template.cells.items():
        verdict = _classify(target, world(*pos), ignore_props, keep_existing)
        if verdict == "ok":
            counts["correct"] += parse_state(target)[0] not in AIR
        else:
            counts[
                {
                    "break": "wrong",
                    "use": "wrong",
                    "place": "missing",
                    "unknown": "unknown",
                }[verdict]
            ] += 1
    return {k: counts[k] for k in ("correct", "wrong", "missing", "unknown")}


# --- materials ------------------------------------------------------------------


def item_for_state(state: str, blocks: dict) -> tuple[str, int] | None:
    """(item, count) to place this state; None for air, upper halves, no item."""
    ident = parse_state(state)[0]
    if ident in FLUIDS:
        return ident + "_bucket", 1
    if ident == "minecraft:piston_head":
        return None
    items, _ = state_materials(state, blocks)
    if not items:
        return None
    # A potted plant places the pot first; the plant is used on it afterwards.
    item = POT if POT in items else next(iter(items))
    return item, items[item]


def _cost(state: str, blocks: dict) -> dict[str, int]:
    """Everything the inventory must hold to finish this cell."""
    if not item_for_state(state, blocks):
        return {}
    if parse_state(state)[0] in FLUIDS:
        return dict([item_for_state(state, blocks)])
    return dict(state_materials(state, blocks)[0])


# --- actions --------------------------------------------------------------------


@dataclass(frozen=True)
class Break:
    pos: Pos


@dataclass(frozen=True)
class Place:
    pos: Pos
    state: str
    item: str
    count: int = 1


@dataclass(frozen=True)
class Use:
    """Use the block (toggle a door, put a plant in a pot), optionally holding item."""

    pos: Pos
    item: str | None = None


@dataclass(frozen=True)
class Scaffold:
    pos: Pos
    item: str


@dataclass(frozen=True)
class NeedMaterials:
    items: dict[str, int] = field(default_factory=dict)
    scaffold: bool = False  # for a scaffold: standing ones can be taken back first


def _is_solid(state: str | None, blocks: dict) -> bool:
    if state is None:
        return False
    ident = parse_state(state)[0]
    return (
        ident not in REPLACEABLE and blocks.get(ident, {}).get("needs_support") is None
    )


def _support_cell(kind: str, props: dict[str, str], pos: Pos) -> Pos | None:
    facing = _VEC.get(props.get("facing", ""))
    if kind == "below":
        return _add(pos, _VEC["down"])
    if kind == "above":
        return _add(pos, _VEC["up"])
    if kind == "any_face":
        face = props.get("face")
        if face == "floor":
            return _add(pos, _VEC["down"])
        if face == "ceiling":
            return _add(pos, _VEC["up"])
    if kind in ("facing_opposite", "any_face") and facing:
        return _add(pos, tuple(-c for c in facing))
    return None


def _support_kind(ident: str, props: dict[str, str], facts: dict) -> str | None:
    # The pack lists lanterns as unsupported; they hang from above or stand on a block.
    if ident.endswith("lantern"):
        return "above" if props.get("hanging") == "true" else "below"
    return facts.get("needs_support")


def _gate(pos: Pos, state: str, world, blocks: dict) -> str | None:
    """None if placeable now, else why not: unknown, floating, below or support."""
    around = [world(*_add(pos, v)) for v in _NEIGHBOURS]
    if any(s is None for s in around):
        return "unknown"
    if not any(_is_solid(s, blocks) for s in around):
        return "floating"
    ident, props = parse_state(state)
    facts = blocks.get(ident, {})
    below = _add(pos, _VEC["down"])
    if ident not in FLUIDS and parse_state(world(*below))[0] in FLUIDS:
        return "unfilled"  # over water: fill the column with dirt first, never float
    if facts.get("gravity") and not _is_solid(world(*below), blocks):
        return "below"
    axis = props.get("axis")
    if axis == "z" and not any(
        _is_solid(world(*_add(pos, v)), blocks) for v in (_VEC["north"], _VEC["south"])
    ):
        return "floating"
    if axis == "x" and not any(
        _is_solid(world(*_add(pos, v)), blocks) for v in (_VEC["east"], _VEC["west"])
    ):
        return "floating"
    if axis == "y" and not any(
        _is_solid(world(*_add(pos, v)), blocks) for v in (_VEC["down"], _VEC["up"])
    ):
        return "floating"
    kind = _support_kind(ident, props, facts)
    sup = _support_cell(kind, props, pos) if kind else None
    if sup and not _is_solid(world(*sup), blocks):
        return "below" if sup == below else "support"
    return None


def _sturdy_up(state: str | None, blocks: dict) -> bool:
    """True if scaffolding can stand on the top face of ``state``."""
    if not _is_solid(state, blocks):
        return False
    ident, props = parse_state(state)
    if ident == sc.BLOCK:
        return True
    facts = blocks.get(ident, {})
    if facts.get("full_cube", True):
        return True
    if ident.endswith("_slab") and props.get("type") in ("top", "double"):
        return True
    if ident.endswith("_stairs") and props.get("half") == "top":
        return True
    return False


def _column_scaffold(
    x: int, z: int, top: int, world, blocks: dict, reserved, *, scaffolding: bool = True
):
    """Lowest empty cell of the column up to ``top`` that rests on solid ground;
    None if the column up to ``top`` touches a reserved (template) cell (B3)."""
    cur = top
    for _ in range(MAX_SCAFFOLD_DEPTH):
        cell = world(x, cur, z)
        if cell is None:
            return None
        if _is_solid(cell, blocks):
            if scaffolding and not _sturdy_up(cell, blocks):
                return None
            low = cur + 1
            if low > top or any((x, y, z) in reserved for y in range(low, top + 1)):
                return None
            return (x, low, z)
        cur -= 1
    return None


def _scaffold_pos(
    pos: Pos,
    world,
    blocks: dict,
    reserved=frozenset(),
    sideways: bool = False,
    level: bool = False,
    scaffolding: bool = True,
) -> Pos | None:
    """Scaffold cell for pos: its own column, or (sideways, for a floating cell) a
    horizontal neighbour's column, never inside ``reserved`` template cells. ``level``:
    only a neighbour's column, built up to pos's own height."""
    x, y, z = pos
    found = (
        None
        if level
        else _column_scaffold(
            x, z, y - 1, world, blocks, reserved, scaffolding=scaffolding
        )
    )
    if found or not sideways:
        return found
    for dx, _, dz in (_VEC[d] for d in _CW):
        found = _column_scaffold(
            x + dx, z + dz, y, world, blocks, reserved, scaffolding=scaffolding
        )
        if found:
            return found
    return None


# A tower from the ground to one below the cell. 8 left the gable-end lanterns and
# the gable's top stair of the large house (10-13 up) without a stance (run
# 20261118T070135016043Z); a scaffolding column may be any height, a stack is 64.
MAX_TOWER = 16


_AXIS_VEC = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def _given_up(todo, skipped, blocks: dict, world=None) -> set:
    """Skipped cells, and the cells that hang or depend on them, transitively."""
    dead = set(skipped or ())
    todo_pos = {t[0] for t in todo}
    hangs = {}
    axis_deps = {}
    for t in todo:
        pos = t[0]
        ident, props = parse_state(t[1])
        kind = _support_kind(ident, props, blocks.get(ident, {}))
        if kind:
            sup = _support_cell(kind, props, pos)
            if sup is not None:
                hangs[pos] = sup
                continue
        axis = props.get("axis")
        if axis in _AXIS_VEC:
            dx, dy, dz = _AXIS_VEC[axis]
            axis_deps[pos] = (
                (pos[0] - dx, pos[1] - dy, pos[2] - dz),
                (pos[0] + dx, pos[1] + dy, pos[2] + dz),
            )

    def _anchored(n: Pos) -> bool:
        if n in todo_pos or n in dead:
            return False
        return world is None or _is_solid(world(*n), blocks)

    live: set[Pos] = set()
    candidates = todo_pos - dead
    grew = True
    while grew:
        grew = False
        for pos in candidates:
            if pos in live:
                continue
            if pos in hangs:
                sup = hangs[pos]
                ok = sup in live or (sup not in todo_pos and sup not in dead)
            elif pos in axis_deps:
                ok = any(n in live or _anchored(n) for n in axis_deps[pos])
            else:
                ok = True
            if ok:
                live.add(pos)
                grew = True
    return dead | (candidates - live)


def tower_cells(
    pos: Pos,
    world,
    blocks: dict,
    reserved=frozenset(),
    *,
    roof: bool = False,
    scaffolding: bool = True,
) -> list[Pos]:
    """Scaffolding column, bottom up, to stand on beside ``pos``: in a horizontal
    neighbour's column, from the ground to one below pos's height (the eye is then
    level with pos), headroom above it free. Template cells (``reserved``) are never
    used; the margin and still-empty cells of the house are fine. With ``roof=True``,
    when all four immediate neighbours are blocked (an interior ceiling cell), raises a
    tower up to ``pos[1]`` in a nearby open column so the body can step onto roof level.
    [] if none fits."""
    x, y, z = pos
    for dx, _, dz in (_VEC[d] for d in _CW):
        low = _column_scaffold(
            x + dx, z + dz, y - 1, world, blocks, reserved, scaffolding=scaffolding
        )
        head = (x + dx, y, z + dz)
        if low is None or head in reserved or y - low[1] > MAX_TOWER:
            continue
        cells = [(x + dx, h, z + dz) for h in range(low[1], y)]
        # The body stands on the top with its feet in ``head``'s cell and its head
        # one over: a tower under a ceiling it cannot get its head into is never
        # climbed (run 20261120T141244368734Z, a hopper under the first floor).
        # A dirt column is pillared by jumping (pillar_to): the last jump starts a
        # cell lower and the planner's pillar edge wants feet+2 and feet+3 empty
        # (RoutePlanner.pillar), one more cell over the crown. Scaffolding is
        # climbed, not jumped.
        crown = (x + dx, y + 1, z + dz)
        over = () if scaffolding else ((x + dx, y + 2, z + dz),)
        states = [world(*c) for c in (*cells, head, *over, crown)]
        if any(s is None or parse_state(s)[0] in FLUIDS for s in states):
            continue
        base_state = world(x + dx, low[1] - 1, z + dz)
        base_ok = (
            _sturdy_up(base_state, blocks)
            if scaffolding
            else _is_solid(base_state, blocks)
        )
        if not base_ok or any(_is_solid(s, blocks) for s in states[len(cells) :]):
            continue
        return cells
    if not roof:
        return []
    # Every immediate horizontal neighbour is reserved/blocked (e.g. an interior ceiling
    # cell): raise a tower up to y in a nearby open column to reach roof level.
    candidates = sorted(
        (
            (dx, dz)
            for dx in range(-3, 4)
            for dz in range(-3, 4)
            if (dx or dz) and dx * dx + dz * dz <= 9
        ),
        key=lambda d: (abs(d[0]) + abs(d[1]), d[0] * d[0] + d[1] * d[1], d[0], d[1]),
    )
    for dx, dz in candidates:
        nx, nz = x + dx, z + dz
        low = _column_scaffold(
            nx, nz, y, world, blocks, reserved, scaffolding=scaffolding
        )
        foot, head = (nx, y + 1, nz), (nx, y + 2, nz)
        if (
            low is None
            or foot in reserved
            or head in reserved
            or y + 1 - low[1] > MAX_TOWER
        ):
            continue
        cells = [(nx, h, nz) for h in range(low[1], y + 1)]
        over = () if scaffolding else ((nx, y + 3, nz),)  # the last jump (above)
        states = [world(*c) for c in (*cells, foot, head, *over)]
        if any(s is None or parse_state(s)[0] in FLUIDS for s in states):
            continue
        base_state = world(nx, low[1] - 1, nz)
        base_ok = (
            _sturdy_up(base_state, blocks)
            if scaffolding
            else _is_solid(base_state, blocks)
        )
        if not base_ok or any(_is_solid(s, blocks) for s in states[len(cells) :]):
            continue
        return cells
    return []


_FLAT = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1))
_AROUND = (*_FLAT, (0, 1, 0), (0, -1, 0))
# How much standing scaffolding a root's distance is worked out from.
_DISTANCE_FLOOD = 512


def _scaffolding(state: str | None) -> bool:
    return state is not None and parse_state(state)[0] == sc.BLOCK


def _scaffold_distance(root: Pos, world, blocks: dict) -> int:
    """The ``distance`` the standing scaffolding block ``root`` holds (the game's
    rule, mclab.scaffolding), from the scaffolding connected to it."""
    seen, todo = {root}, [root]
    while todo and len(seen) < _DISTANCE_FLOOD:
        p = todo.pop()
        for v in _AROUND:
            n = _add(p, v)
            if n not in seen and _scaffolding(world(*n)):
                seen.add(n)
                todo.append(n)

    def sturdy_below(p: Pos) -> bool:
        under = (p[0], p[1] - 1, p[2])
        return under not in seen and _sturdy_up(world(*under), blocks)

    return sc.distances(seen, sturdy_below)[root]


def bridge_cells(
    floor: Pos,
    world,
    blocks: dict,
    reserved=frozenset(),
    max_len: int = sc.UNSTABLE - 1,
) -> list[Pos]:
    """Scaffolding to put down, the tower's side first, so that ``floor`` holds a
    block to stand on: a horizontal run at ``floor``'s level off the top of a
    standing scaffolding tower (or the end of an earlier bridge). Scaffolding hangs
    up to six blocks sideways from a supported block, so a tower serves every stance
    within 13 blocks end to end; one block off its top replaces a second tower beside
    it (house-large run 20261120T141244368734Z). Every cell is empty, outside the
    template (``reserved``), with room above to walk out on it; the top it leaves
    from has the same room. The shortest that stays up; [] if none does."""
    y = floor[1]

    def clear(p: Pos) -> bool:
        state = world(*p)
        return (
            state is not None
            and not _is_solid(state, blocks)
            and parse_state(state)[0] not in FLUIDS
        )

    def walk_room(p: Pos) -> bool:
        return clear((p[0], y + 1, p[2])) and clear((p[0], y + 2, p[2]))

    def open_cell(p: Pos) -> bool:
        state = world(*p)
        return (
            p not in reserved
            and state is not None
            and parse_state(state)[0] in REPLACEABLE
            and parse_state(state)[0] not in FLUIDS
            and walk_room(p)
        )

    def root(p: Pos) -> bool:
        return _scaffolding(world(*p)) and walk_room(p)

    def holds(root_cell: Pos, length: int) -> bool:
        """A run ``length`` long out of ``root_cell`` stays under the fall distance."""
        return _scaffold_distance(root_cell, world, blocks) + length < sc.UNSTABLE

    if not open_cell(floor):
        return []
    parent: dict[Pos, Pos | None] = {floor: None}
    frontier = [floor]
    for length in range(1, max_len + 1):
        later = []
        for cell in frontier:
            for v in _FLAT:
                n = _add(cell, v)
                if root(n) and holds(n, length):
                    path, at = [], cell
                    while at is not None:
                        path.append(at)
                        at = parent[at]
                    return path
                if length < max_len and n not in parent and open_cell(n):
                    parent[n] = cell
                    later.append(n)
        frontier = later
    return []


def _held_tool(inventory: dict[str, int], kind: str) -> str | None:
    return next(
        (i for i, n in sorted(inventory.items()) if n > 0 and i.endswith("_" + kind)),
        None,
    )


def tool_needs(template: Template, inventory: dict[str, int]) -> dict[str, int]:
    """Tools the template's no-item cells need (a hoe for farmland, a shovel for
    paths) that the inventory lacks; the cheapest (wooden) tool stands for any."""
    kinds = {
        TOOL_USES[parse_state(s)[0]][1:]
        for s in template.cells.values()
        if parse_state(s)[0] in TOOL_USES
    }
    return {
        f"minecraft:wooden_{k}": 1
        for k in sorted(kinds)
        if not _held_tool(inventory, k)
    }


def _place_state(state: str) -> str:
    """What the body places: closed (a Use opens it), a pot without its plant."""
    ident, props = parse_state(state)
    if ident.startswith("minecraft:potted_"):
        return POT
    if ident in TOOL_USES:
        return "minecraft:dirt"
    if props.get("open") == "true" and props.get("half") != "upper":
        return format_state(ident, {**props, "open": "false"})
    return state


# Details go on once the structure (walls, floors, stairs, roof) is up, the way a
# person builds: windows, lights, pots and furniture last. Placed early, glass and
# flower pots closed the body in on an eave slab twice (runs 20261117T144843447702Z,
# 20261117T155135309814Z). Words in the block name; anything else is structure.
DETAIL_WORDS = (
    "glass",
    "potted_",
    "flower_pot",
    "lantern",
    "torch",
    "chain",
    "trapdoor",
    "_sign",
    "banner",
    "carpet",
    "candle",
    "button",
    "pressure_plate",
    "rail",
    "_bed",
    "barrel",
    "chest",
    "bookshelf",
    "furnace",
    "smoker",
    "hopper",
    "crafting_table",
    "campfire",
    "anvil",
    "stonecutter",
    "lectern",
    "loom",
    "composter",
    "cauldron",
    "bell",
    "leaves",
    "flower",
    "item_frame",
)


def is_detail(state: str) -> bool:
    ident = parse_state(state)[0]
    return any(word in ident for word in DETAIL_WORDS)


def _glazing(state: str) -> bool:
    """Glass, panes and bars: what fills a window gap."""
    ident = parse_state(state)[0]
    return "glass" in ident or ident == "minecraft:iron_bars"


GLAZING_RISE = 2  # details this many layers off a pane, and in work reach, go first


def _near_glazing(pane: Pos, detail: Pos) -> bool:
    dx, dy, dz = (a - b for a, b in zip(pane, detail, strict=True))
    return abs(dy) <= GLAZING_RISE and dx * dx + dz * dz <= WORK_REACH**2


EXIT_DROP = 3  # the longest drop that is a way off a spot (no damage)


def _exit_open(world, p: Pos, filled: Pos | None) -> bool:
    if p == filled:
        return False
    s = world(*p)
    return s is None or parse_state(s)[0] in REPLACEABLE


def has_exit(world, stance: Pos, filled: Pos | None = None) -> bool:
    """The body standing at ``stance`` can still leave it with ``filled`` solid: a
    walk to a side cell with ground at most EXIT_DROP below, or a one-block step up,
    or a climb down the scaffolding it stands on. Unknown cells count as open."""
    x, y, z = stance
    under = world(x, y - 1, z)
    if under is not None and parse_state(under)[0] == sc.BLOCK:
        return True
    head = _exit_open(world, (x, y + 2, z), filled)
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, nz = x + dx, z + dz
        feet = _exit_open(world, (nx, y, nz), filled)
        if feet and _exit_open(world, (nx, y + 1, nz), filled):
            for d in range(1, EXIT_DROP + 2):
                if not _exit_open(world, (nx, y - d, nz), None) or (
                    (nx, y - d, nz) == filled
                ):
                    return True
                g = world(nx, y - d, nz)
                if g is not None and parse_state(g)[0] in FLUIDS:
                    return True
        elif (
            not feet
            and head
            and _exit_open(world, (nx, y + 1, nz), filled)
            and _exit_open(world, (nx, y + 2, nz), filled)
        ):
            return True
    return False


def traps(world, stance: Pos, pos: Pos) -> bool:
    """Placing ``pos`` would take the last way off ``stance`` (a spot already shut
    in is not made worse by it)."""
    return has_exit(world, stance) and not has_exit(world, stance, pos)


def _is_door(state: str) -> bool:
    return parse_state(state)[0].endswith("_door")


def inside_corner(template: Template, pos: Pos) -> bool:
    """Two template blocks beside ``pos`` at right angles. Placed after them it is a
    pocket whose faces show only from inside it (run 20261102T223417173633Z), so it
    goes first in its layer."""
    x, y, z = pos
    solid = [
        (dx, dz)
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
        if parse_state(template.cells.get((x + dx, y, z + dz), "minecraft:air"))[0]
        not in AIR
    ]
    return any(a[0] * b[0] + a[1] * b[1] == 0 for a in solid for b in solid if a != b)


WORK_REACH = 4.5  # the body's block interaction range
# Work zones: square tiles of the footprint, ZONE columns a side, about what the
# body covers from two or three stances. With a body position the build is done one
# zone at a time, bottom up inside the zone, the way a person finishes one wing
# before walking to the next (run 20261119T133618892664Z built by whole layers: 824
# walks, 47 of them 12-24 blocks across the house between two placements).
ZONE = 8


def zone_of(template: Template, pos: Pos) -> tuple[int, int]:
    """The work zone of a cell (cells outside the footprint: the nearest zone)."""
    hx = max(0, (template.size[0] - 1) // ZONE)
    hz = max(0, (template.size[2] - 1) // ZONE)
    zx = (pos[0] - template.origin[0]) // ZONE
    zz = (pos[2] - template.origin[2]) // ZONE
    return (min(max(zx, 0), hx), min(max(zz, 0), hz))


def next_actions(
    template: Template,
    world,
    blocks: dict,
    inventory: dict[str, int],
    limit: int = 8,
    scaffolds: set | None = None,
    ignore_props=DEFAULT_IGNORE,
    skipped: set | None = None,
    near: Pos | None = None,
    fetch_first: bool = False,
    focus: Pos | None = None,
):
    """Next legal actions, or [] when nothing can be done now.

    ``near`` also scopes the placements to one work zone (``zone_of``): the zone of
    ``focus`` (a cell of the zone worked last) while it has placeable work, else the
    zone the body stands in, else the one with the placeable cell nearest the body.
    Inside a zone the layer rule holds per zone; the column rule (nothing over a
    missing cell in its column) holds everywhere.

    ``fetch_first``: ask for materials (NeedMaterials) while a placeable cell below
    the working layer is only short of its item, instead of building on above it.

    Pass the same ``scaffolds`` set on every call: proposed scaffold cells are added
    to it so cleanup_actions can remove them, and they are never broken before then.
    ``near``: where the body stands. Within a layer the ``limit`` acts offered are then
    the ones closest to it (not the first in template order, which is scattered over
    the house), and the global inside-corner-first rule gives way to the runner's
    local one (a wall waits for the corner it would close in).
    """
    scaffolds = set() if scaffolds is None else scaffolds
    d = diff(template, world, ignore_props)

    def far(p):  # squared distance to the body; 0 (template order) without ``near``
        if near is None:
            return 0
        return sum((a - b) ** 2 for a, b in zip(p, near, strict=False))

    def _in_reach(p):  # from where the body stands, without walking
        return near is not None and far(p) <= WORK_REACH**2

    def is_canopy_leaf(p):
        return (
            (s := world(*p))
            and "_leaves" in s
            and template.cells.get(p, "minecraft:air") in AIR
        )

    place_positions = {pos for pos, _ in d.places}
    sealed_footholds = {
        p
        for p in d.breaks
        if p in scaffolds
        and (skipped is None or p not in skipped)
        and parse_state(world(*p) or "")[0] != sc.BLOCK
        and _is_solid(world(p[0], p[1] + 1, p[2]), blocks)
        and (p[0], p[1] - 1, p[2]) in place_positions
    }
    breaks = sorted(
        (
            p
            for p in d.breaks
            if (p not in scaffolds or p in sealed_footholds)
            and (skipped is None or p not in skipped)
        ),
        key=lambda p: (is_canopy_leaf(p), p[1], far(p), p[0], p[2]),
    )
    if breaks and (near is None or any(_in_reach(p) for p in breaks)):
        return [Break(p) for p in breaks[:limit]]
    uses = [u for u in d.uses if skipped is None or u[0] not in skipped]
    if uses and not breaks:
        uses = sorted(uses, key=lambda u: (u[0][1], far(u[0]), u[0][0], u[0][2]))
        acts: list = []
        missing: dict[str, int] = {}
        for p, item in uses[:limit]:
            if item and item.startswith("#"):
                held = _held_tool(inventory, item[1:])
                if held is None:
                    missing[f"minecraft:wooden_{item[1:]}"] = 1
                    continue
                item = held
            acts.append(Use(p, item))
        if acts:
            return acts
        return [NeedMaterials(missing)]

    todo = []
    for pos, state in d.places:
        cost = _cost(state, blocks)
        if cost:
            todo.append((pos, _place_state(state), cost, item_for_state(state, blocks)))
    if not todo:
        return []

    def _ground_supported(pos):
        below = world(pos[0], pos[1] - 1, pos[2])
        return _is_solid(below, blocks)

    todo.sort(
        key=lambda t: (
            parse_state(t[1])[0] in FLUIDS,
            t[0][1],
            not _ground_supported(t[0]),
            not inside_corner(template, t[0]) if near is None else far(t[0]),
            t[0][0],
            t[0][2],
        )
    )

    # Bottom up: nothing above the lowest layer that still has a block to place. A cell
    # that keeps failing then holds the build up instead of being walled in (live run
    # 20261102T212736219778Z: a missed floor corner got its wall on top, leaving a
    # pocket no stance could see into). Cells waiting on another cell ("support":
    # a torch on a wall, a lantern under a ceiling; "floating": hung from a block
    # placed later), and cells short of materials (asked for once the rest is
    # placed) do not hold their layer.
    # Details wait while a structure cell is placeable now (blocks held, support in
    # place), and do not hold the structure's layer. With no structure to place, the
    # details it carries go in: held back they filled the inventory, so no refill had
    # room for the planks and stairs, and the build stopped (run
    # 20261117T184243178083Z: 587 cells left, 27 kinds of detail held).
    def ready_now(t) -> bool:  # placeable this tick, the layer and reach rules aside
        pos, state, cost = t[0], t[1], t[2]
        return (
            (skipped is None or pos not in skipped)
            and parse_state(state)[0] not in FLUIDS
            and (pos[0], pos[1] + 1, pos[2]) not in sealed_footholds
            and all(inventory.get(i, 0) >= c for i, c in cost.items())
            and _gate(pos, state, world, blocks) is None
        )

    # Glazing goes in after the details round it, the way a person finishes a
    # window: the balcony's low panes went in first and its flower pots, out of
    # sight from the room, were then reached by way of a tree (run
    # 20261120T141244368734Z). Only details placeable now hold a pane (one skipped,
    # short or waiting on another never keeps the window open), and a held pane
    # holds no layer, column or structure rule: the details it waits for may be a
    # layer up, and would otherwise wait on the pane's layer in turn.
    decor = [
        t[0]
        for t in todo
        if is_detail(t[1])
        and not _glazing(t[1])
        and not _is_door(t[1])
        and ready_now(t)
    ]
    glazing_held = {
        t[0]
        for t in todo
        if _glazing(t[1]) and any(_near_glazing(t[0], q) for q in decor)
    }
    todo_open = [t for t in todo if t[0] not in glazing_held]

    structure_left = any(
        not is_detail(t[1]) and not _is_door(t[1]) and ready_now(t) for t in todo_open
    )

    def held_back(state):
        return structure_left and is_detail(state)

    waiting = [
        t[0]
        for t in todo_open
        if parse_state(t[1])[0] not in FLUIDS
        and not held_back(t[1])
        and all(inventory.get(i, 0) >= c for i, c in t[2].items())
        and not _is_door(t[1])
        and (skipped is None or t[0] not in skipped)
        and _gate(t[0], t[1], world, blocks)
        not in ("support", "below", "floating", "unknown", "unfilled")
    ]
    lowest = min((p[1] for p in waiting), default=None)

    def zone(p):
        return zone_of(template, p)

    zone_lowest: dict = {}  # the layer rule per work zone (with a body position)
    for p in waiting:
        zone_lowest[zone(p)] = min(zone_lowest.get(zone(p), p[1]), p[1])
    # A cell given up on (skipped) does not keep the doors out: run
    # 20261118T070135016043Z left both doors off for five unplaceable cells. Nor
    # does one hanging from it (a lantern under a skipped chain, run
    # 20261118T094938114426Z): it can never go in either.
    given_up = _given_up(todo, skipped, blocks, world)
    others_left = any(not _is_door(t[1]) and t[0] not in given_up for t in todo)
    # Work from where the body stands: a cell above the lowest layer that is in reach
    # now and has nothing left to place under it in its own column is offered too, so
    # a body that climbed to the roof finishes the roof there instead of being sent
    # back down for one low cell and coming up again (run 20261117T130121185147Z).
    # The column rule keeps what the layer rule was for: nothing is built over a
    # missing cell (a wall over a missed floor corner, run 20261102T212736219778Z).
    below_todo: dict = {}
    for t in todo_open:
        if parse_state(t[1])[0] in FLUIDS or (skipped is not None and t[0] in skipped):
            continue
        if held_back(t[1]):  # a window gap does not hold up the wall above it
            continue
        col = (t[0][0], t[0][2])
        below_todo[col] = min(below_todo.get(col, t[0][1]), t[0][1])

    def _pending_below(pos):
        return below_todo.get((pos[0], pos[2]), pos[1]) < pos[1]

    left = dict(inventory)
    out: list = []
    # ponytail: run 20261110T150634125260Z built walls to y 69 with the staircase and
    # upper floor (birch, still in the chests) unplaced, then found no way up.
    starved = fetch_first and any(
        (lowest is None or t[0][1] < lowest)
        and parse_state(t[1])[0] not in FLUIDS
        and not _is_door(t[1])
        and not held_back(t[1])
        and (skipped is None or t[0] not in skipped)
        and not all(inventory.get(i, 0) >= c for i, c in t[2].items())
        and _gate(t[0], t[1], world, blocks) is None
        for t in todo_open  # a held pane short in hand is fetched once it is due
    )

    def offerable(pos, state, cost, held) -> bool:
        if pos in glazing_held or (skipped is not None and pos in skipped):
            return False
        low = lowest if near is None else zone_lowest.get(zone(pos))
        if (
            low is not None
            and pos[1] > low
            and not (_in_reach(pos) and not _pending_below(pos))
        ):
            return False
        # The doorway is the body's way in and out: a door placed while other cells are
        # still missing shut the body inside (it cannot open doors; run
        # 20261102T225803126737Z: every walk "stuck" once the walls closed).
        if _is_door(state) and others_left:
            return False
        if held_back(state):
            return False
        if (pos[0], pos[1] + 1, pos[2]) in sealed_footholds:
            return False
        return (
            all(held.get(i, 0) >= c for i, c in cost.items())
            and _gate(pos, state, world, blocks) is None
        )

    active = None  # the work zone, with a body position
    if near is not None and not starved:
        ready = [t[0] for t in todo if offerable(t[0], t[1], t[2], inventory)]
        zones = {zone(p) for p in ready}
        if focus is not None and zone(focus) in zones:
            active = zone(focus)
        elif zone(near) in zones:
            active = zone(near)
        elif ready:
            active = zone(min(ready, key=lambda p: (far(p), p)))
    for pos, state, cost, (item, n) in () if starved else todo:
        if len(out) >= limit:
            break
        if active is not None and zone(pos) != active:
            continue
        if offerable(pos, state, cost, left):
            for i, c in cost.items():
                left[i] -= c
            out.append(Place(pos, state, item, n))
    if breaks:  # none in reach: place what is in reach here, then walk to the breaks
        out = [a for a in out if _in_reach(a.pos)]
        return out or [Break(p) for p in breaks[:limit]]
    if out:
        return out

    remaining = Counter()
    for pos, _, cost, _ in todo:
        if pos not in given_up:
            remaining.update(cost)
    for pos, state in d.places:
        if pos in given_up:
            continue
        tool = TOOL_USES.get(parse_state(state)[0])
        if tool and not _held_tool(inventory, tool[1:]):
            remaining[f"minecraft:wooden_{tool[1:]}"] = 1
    reserved = frozenset(
        p for p, s in template.cells.items() if parse_state(s)[0] not in AIR
    )
    if any(n > inventory.get(item, 0) for item, n in remaining.items()):
        return [NeedMaterials(dict(remaining))]

    pending = {t[0] for t in todo}
    for pos, state, _, _ in todo:
        if pos in given_up:
            continue
        reason = _gate(pos, state, world, blocks)
        if reason == "below" and _add(pos, _VEC["down"]) in pending:
            continue
        if reason == "unfilled":
            # water under the block: dirt fills the column up from the bed. Permanent,
            # so it is never added to the scaffolds that cleanup takes back.
            if inventory.get(FILL_ITEM, 0) <= remaining[FILL_ITEM]:
                return [NeedMaterials({FILL_ITEM: 1})]
            cell = _scaffold_pos(pos, world, blocks, reserved, scaffolding=False)
            if cell is None:
                continue
            return [Scaffold(cell, FILL_ITEM)]
        if reason in ("floating", "below"):
            item = next(
                (i for i in SCAFFOLD_ITEMS if inventory.get(i, 0) > remaining[i]), None
            )
            if item is None:
                # the caller takes standing scaffolds back first; only then is
                # dirt (gatherable, unlike scaffolding) asked for
                return [NeedMaterials({SCAFFOLD_ITEMS[1]: 1}, scaffold=True)]
            # A floating block is placed against the side of a column beside it, as high
            # as it: scaffolding has no solid top to click from the ground (the eye is
            # below it), and its side is clicked to extend a column upwards.
            cell = _scaffold_pos(
                pos,
                world,
                blocks,
                reserved,
                reason == "floating",
                level=item == sc.BLOCK and reason == "floating",
                scaffolding=item == sc.BLOCK,
            )
            if cell is None:
                continue
            scaffolds.add(cell)
            return [Scaffold(cell, item)]
    return []


def cleanup_actions(template: Template, world, scaffolds, ignore_props=DEFAULT_IGNORE):
    """Breaks for scaffold cells still standing (top first), once the build is done.

    Scaffolding blocks hang from the ones standing on the ground: breaking those roots
    drops the rest with them, so only the roots are broken (see mclab.scaffolding)."""
    out = []
    ropes = {p for p in scaffolds if (c := world(*p)) and parse_state(c)[0] == sc.BLOCK}

    def sturdy(p: Pos) -> bool:
        below = world(p[0], p[1] - 1, p[2])
        return (
            below is not None
            and parse_state(below)[0] not in REPLACEABLE
            and parse_state(below)[0] != sc.BLOCK
        )

    falls = ropes - set(sc.roots(ropes, sturdy))
    for pos in sorted(scaffolds, key=lambda p: (-p[1], p[0], p[2])):
        cell = world(*pos)
        if cell is None or parse_state(cell)[0] in AIR or pos in falls:
            continue
        target = template.cells.get(pos)
        if target is not None and _same(target, cell, ignore_props):
            continue  # the build itself ended up using this cell
        out.append(Break(pos))
    return out
