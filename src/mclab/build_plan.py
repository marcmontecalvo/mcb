"""Schematic builder planning (milestone M2): pure logic, no I/O.

The world is a callable ``world(x, y, z) -> state string | None`` (None = unknown,
never assumed air or support). Block facts are a dict in blocks.json shape
(``{"minecraft:stone": {"item": ..., "gravity": ..., "needs_support": ...}}``).
Contract: docs/KNOWLEDGE_PACK_FORMAT.md, "Build file" and "blocks.json".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from mclab.schematic import (
    format_state,
    from_build_json,
    parse_state,
    state_materials,
)

Pos = tuple[int, int, int]

AIR = frozenset({"minecraft:air", "minecraft:cave_air", "minecraft:void_air"})
FLUIDS = frozenset({"minecraft:water", "minecraft:lava"})
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
SCAFFOLD_ITEMS = ("minecraft:dirt", "minecraft:cobblestone", "minecraft:netherrack")
MAX_SCAFFOLD_DEPTH = 64

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
    return "place" if cid in REPLACEABLE else "break"


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
    if facts.get("gravity") and not _is_solid(world(*below), blocks):
        return "below"
    kind = _support_kind(ident, props, facts)
    sup = _support_cell(kind, props, pos) if kind else None
    if sup and not _is_solid(world(*sup), blocks):
        return "below" if sup == below else "support"
    return None


def _column_scaffold(x: int, z: int, top: int, world, blocks: dict, reserved):
    """Lowest empty cell of the column up to ``top`` that rests on solid ground;
    None if the column up to ``top`` touches a reserved (template) cell (B3)."""
    cur = top
    for _ in range(MAX_SCAFFOLD_DEPTH):
        cell = world(x, cur, z)
        if cell is None:
            return None
        if _is_solid(cell, blocks):
            low = cur + 1
            if low > top or any((x, y, z) in reserved for y in range(low, top + 1)):
                return None
            return (x, low, z)
        cur -= 1
    return None


def _scaffold_pos(
    pos: Pos, world, blocks: dict, reserved=frozenset(), sideways: bool = False
) -> Pos | None:
    """Scaffold cell for pos: its own column, or (sideways, for a floating cell) a
    horizontal neighbour's column, never inside ``reserved`` template cells."""
    x, y, z = pos
    found = _column_scaffold(x, z, y - 1, world, blocks, reserved)
    if found or not sideways:
        return found
    for dx, _, dz in (_VEC[d] for d in _CW):
        found = _column_scaffold(x + dx, z + dz, y, world, blocks, reserved)
        if found:
            return found
    return None


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


def next_actions(
    template: Template,
    world,
    blocks: dict,
    inventory: dict[str, int],
    limit: int = 8,
    scaffolds: set | None = None,
    ignore_props=DEFAULT_IGNORE,
):
    """Next legal actions, or [] when nothing can be done now.

    Pass the same ``scaffolds`` set on every call: proposed scaffold cells are added
    to it so cleanup_actions can remove them, and they are never broken before then.
    """
    scaffolds = set() if scaffolds is None else scaffolds
    d = diff(template, world, ignore_props)
    breaks = sorted(
        (p for p in d.breaks if p not in scaffolds), key=lambda p: (-p[1], p[0], p[2])
    )
    if breaks:
        return [Break(p) for p in breaks[:limit]]
    if d.uses:
        uses = sorted(d.uses, key=lambda u: (u[0][1], u[0][0], u[0][2]))
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
    todo.sort(key=lambda t: (parse_state(t[1])[0] in FLUIDS, t[0][1], t[0][0], t[0][2]))

    left = dict(inventory)
    out: list = []
    for pos, state, cost, (item, n) in todo:
        if len(out) >= limit:
            break
        if (
            all(left.get(i, 0) >= c for i, c in cost.items())
            and _gate(pos, state, world, blocks) is None
        ):
            for i, c in cost.items():
                left[i] -= c
            out.append(Place(pos, state, item, n))
    if out:
        return out

    remaining = Counter()
    for _, _, cost, _ in todo:
        remaining.update(cost)
    for _, state in d.places:
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
        reason = _gate(pos, state, world, blocks)
        if reason == "below" and _add(pos, _VEC["down"]) in pending:
            continue
        if reason in ("floating", "below"):
            cell = _scaffold_pos(pos, world, blocks, reserved, reason == "floating")
            if cell is None:
                continue
            item = next(
                (i for i in SCAFFOLD_ITEMS if inventory.get(i, 0) > remaining[i]), None
            )
            if item is None:
                return [NeedMaterials({SCAFFOLD_ITEMS[0]: 1})]
            scaffolds.add(cell)
            return [Scaffold(cell, item)]
    return []


def cleanup_actions(template: Template, world, scaffolds, ignore_props=DEFAULT_IGNORE):
    """Breaks for scaffold cells still standing (top first), once the build is done."""
    out = []
    for pos in sorted(scaffolds, key=lambda p: (-p[1], p[0], p[2])):
        cell = world(*pos)
        if cell is None or parse_state(cell)[0] in AIR:
            continue
        target = template.cells.get(pos)
        if target is not None and _same(target, cell, ignore_props):
            continue  # the build itself ended up using this cell
        out.append(Break(pos))
    return out
