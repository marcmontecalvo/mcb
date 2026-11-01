"""Gathering planner: cheapest item tree from a knowledge pack to an ordered task list.

Pure symbolic code. `plan()` is stateless, so re-planning after a task finishes or
fails is just another call with the new inventory. `check_order()` replays a task
list against an inventory and reports every step that could not run.

Cost unit is "ticks of effort"; only relative sizes matter.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import NamedTuple

from mclab.knowledge import Knowledge
from mclab.knowledge import Transform as TransformDef

# ---- cost model constants -------------------------------------------------
INF = math.inf
HELD_COST = 0.1  # price of an item already in the inventory (any count)
TRAVEL_TICKS_PER_BLOCK = 40.0  # walking/collecting overhead per block mined
SEARCH_TICKS = {"tree": 120.0, "surface": 240.0, "underground": 400.0}
DEFAULT_SEARCH_TICKS = 300.0  # `where` missing or unknown
KNOWN_COST_FACTOR = 0.8  # a block type already seen nearby is cheaper to gather
SEARCH_BATCH = 8  # one search is shared by this many blocks
MINE_TICKS_FACTOR = 30.0  # vanilla: ticks = hardness * 30 / tool speed (right tool)
CRAFT_OVERHEAD = 10.0  # per craft/smelt operation
TRANSFORM_OVERHEAD = 60.0  # place the block, use the tool on it, break it again
PLACE_STATION_TICKS = 20.0
STATION_AMORTIZE = 16.0  # a station's cost is shared by this many crafts
SMELT_WAIT_FACTOR = (
    0.25  # fraction of cook_ticks that is not overlapped with other work
)
SMELT_BATCH = 8  # items smelted per fuel purchase when pricing fuel in the model
DEFAULT_DURABILITY = 1000
MAX_SOLVE_ITERATIONS = 200
EPS = 1e-9

DEFAULT_STATIONS = frozenset({"inventory", "crafting_table", "furnace"})
# Oak is the default wood: a slight discount so that ties resolve to it.
DEFAULT_PREFER = {"minecraft:oak_log": 0.9}
# Tool tier name -> rank on the drop-tier ladder (hand 0, wood 1, stone 2, iron 3 ...).
TIER_RANK = {
    "wood": 1,
    "gold": 1,
    "stone": 2,
    "copper": 2,
    "iron": 3,
    "diamond": 4,
    "netherite": 5,
}
UNKNOWN_TIER_RANK = 3
GATHER_TOOL_KINDS = frozenset({"pickaxe", "axe", "shovel", "hoe", "shears"})
GROUP_RANK = {"gather": 0, "inventory": 1, "crafting_table": 2, "furnace": 3}
# Fuels the planner will buy; anything else (tools, chests) is never burnt.
FUEL_TAGS = ("#minecraft:planks", "#minecraft:logs_that_burn")
FUEL_ITEMS = frozenset({"minecraft:coal", "minecraft:stick"})
HELD_FUEL_ITEMS = FUEL_ITEMS | {
    "minecraft:charcoal",
    "minecraft:coal_block",
    "minecraft:blaze_rod",
}
MOB_DROPS = frozenset(
    "minecraft:" + n
    for n in (
        "leather string feather bone gunpowder slime_ball ender_pearl blaze_rod "
        "spider_eye rotten_flesh ink_sac rabbit_hide phantom_membrane"
    ).split()
)


# ---- public result types --------------------------------------------------
@dataclass(frozen=True)
class Gather:
    block: str  # block id to mine
    count: int  # blocks to mine
    tool_kind: str | None  # None: bare hand
    min_tier: str  # lowest drop tier that yields `item` ("hand", "wood", ...)
    item: str  # item wanted from it
    item_count: int


@dataclass(frozen=True)
class Craft:
    recipe_id: str
    times: int
    station: str
    result: str
    ingredients: tuple[tuple[str, int], ...]  # concrete (item, total count) consumed
    result_count: int = 1  # total items produced


@dataclass(frozen=True)
class Smelt:
    recipe_id: str
    count: int  # inputs to smelt
    fuel: str
    fuel_count: int
    station: str
    result: str
    ingredient: str
    result_count: int = 1  # total items produced


@dataclass(frozen=True)
class Transform:
    """Place `input`, use a tool of `tool_kind` on it, break it: `count` times."""

    transform_id: str
    count: int
    input: str
    output: str
    tool_kind: str
    result_count: int = 1


@dataclass(frozen=True)
class PlaceStation:
    kind: str


@dataclass(frozen=True)
class Equip:
    item: str


Task = Gather | Craft | Smelt | Transform | PlaceStation | Equip


@dataclass(frozen=True)
class Missing:
    item: str
    reason: str


@dataclass(frozen=True)
class PlanResult:
    tasks: tuple[Task, ...]
    missing: tuple[Missing, ...]
    estimated_cost: float
    surplus: Mapping[str, int] = field(default_factory=dict)  # made beyond the need

    @property
    def ok(self) -> bool:
        return not self.missing


# ---- model ----------------------------------------------------------------
class _Tool(NamedTuple):
    item: str
    kind: str
    rank: int
    speed: float
    dur: int


class _Gat(NamedTuple):
    block: str
    rank: int
    yield_: float
    hardness: float
    kind: str | None
    search: float
    need_tool: bool = False  # the drop appears only with a tool (shears on vines)
    known: bool = False  # seen nearby (known_blocks)


class _Rec(NamedTuple):
    id: str
    ing: tuple[tuple[tuple[str, ...], int], ...]
    result: str
    out: int
    station: str
    cook: int | None  # not None: a smelting-like recipe


class _Upgrade(NamedTuple):
    tool: _Tool
    rec: _Rec
    material: int  # units of the gathered item the tool's recipe consumes
    others: tuple[tuple[tuple[str, ...], int], ...]  # the recipe's other ingredients


# ("gather", _Gat) | ("craft", _Rec) | ("smelt", _Rec, fuel) | ("transform", def, tool)
Option = tuple


class _Model:
    def __init__(
        self,
        k: Knowledge,
        inventory: Mapping[str, int],
        stations: Mapping[str, object],
        executable: Collection[str],
        known_blocks: Collection[str],
        prefer: Mapping[str, float],
    ) -> None:
        self.k = k
        self.inv = {i: n for i, n in inventory.items() if n > 0}
        self.known = {s for s, where in stations.items() if where}
        self.executable = frozenset(executable)
        self.prefer = prefer
        self.known_blocks = frozenset(known_blocks)
        self.rank_of = {
            n: i for i, n in enumerate(t for t in k.drop_tiers if t != "silk_touch")
        }
        self.tools = self._index_tools()
        self.tool_by_item = {t.item: t for ts in self.tools.values() for t in ts}
        self.gathers = self._index_gathers(set(k.sources) | self.known_blocks)
        self.notes: dict[str, list[str]] = {}
        self.recs = self._index_recipes()
        self.transforms = self._index_transforms()
        self.fuels = self._index_fuels()
        self.producible = sorted(
            set(self.gathers) | set(self.recs) | set(self.transforms)
        )
        self.cost, self.best = self._solve(held=True)
        self.cost_n, self.best_n = self._solve(held=False)

    # indexing
    def tier_name(self, rank: int) -> str:
        return next((n for n, r in self.rank_of.items() if r == rank), str(rank))

    def _index_tools(self) -> dict[str, list[_Tool]]:
        out: dict[str, list[_Tool]] = {}
        for item, info in self.k.items.items():
            tool = info.get("tool")
            if not tool or tool["kind"] not in GATHER_TOOL_KINDS:
                continue
            tier = tool.get("tier", "")
            rank = TIER_RANK.get(tier, self.rank_of.get(tier, UNKNOWN_TIER_RANK))
            dur = int(info.get("durability") or DEFAULT_DURABILITY)
            out.setdefault(tool["kind"], []).append(
                _Tool(item, tool["kind"], rank, float(tool.get("speed", 1.0)), dur)
            )
        return out

    def _search_ticks(self, block: str) -> float:
        where = (self.k.sources.get(block) or {}).get("where")
        return SEARCH_TICKS.get(where, DEFAULT_SEARCH_TICKS)

    def _index_gathers(self, sourced: set[str]) -> dict[str, list[_Gat]]:
        out: dict[str, list[_Gat]] = {}
        for block in sorted(sourced):  # drops that appear only with a tool
            required = (self.k.sources.get(block) or {}).get("requires_tool")
            info = self.k.blocks.get(block)
            if required and info and info.get("item"):
                out.setdefault(info["item"], []).append(
                    _Gat(
                        block,
                        0,
                        1.0,
                        float(info["hardness"]),
                        required,
                        self._search_ticks(block),
                        True,
                        block in self.known_blocks,
                    )
                )
        for block in self.k.drops:
            info = self.k.blocks.get(block)
            if info is None or block not in sourced:
                continue
            if (self.k.sources.get(block) or {}).get("requires_tool"):
                continue
            mineable = info.get("mineable") or ()
            search = self._search_ticks(block)
            seen: set[str] = set()
            for tier, rank in self.rank_of.items():
                for d in self.k.drops_for(block, tier):
                    if d.item in seen or d.expected <= 0:
                        continue
                    seen.add(d.item)
                    out.setdefault(d.item, []).append(
                        _Gat(
                            block,
                            rank,
                            d.expected,
                            float(info["hardness"]),
                            mineable[0] if mineable else None,
                            search,
                            False,
                            block in self.known_blocks,
                        )
                    )
        return out

    def _index_recipes(self) -> dict[str, list[_Rec]]:
        out: dict[str, list[_Rec]] = {}
        for r in self.k.recipes:
            if r.result_item is None:
                continue
            if r.station is None:
                self.notes.setdefault(r.result_item, []).append(
                    f"needs unhandled recipe type {r.type}"
                )
                continue
            if r.station not in self.executable:
                self.notes.setdefault(r.result_item, []).append(
                    f"needs station {r.station}"
                )
                continue
            ing = tuple(
                (
                    tuple(
                        dict.fromkeys(x for o in i.options for x in self.k.expand(o))
                    ),
                    i.count,
                )
                for i in r.ingredients
            )
            out.setdefault(r.result_item, []).append(
                _Rec(r.id, ing, r.result_item, r.result_count, r.station, r.cook_ticks)
            )
        return out

    def _index_transforms(self) -> dict[str, list[TransformDef]]:
        out: dict[str, list[TransformDef]] = {}
        for t in self.k.transforms:
            if not t.in_place and t.tool in self.tools:
                out.setdefault(t.output, []).append(t)
        return out

    def _index_fuels(self) -> list[tuple[str, int]]:
        burnable = {
            i: int(v["fuel_ticks"])
            for i, v in self.k.items.items()
            if v.get("fuel_ticks") and not v.get("durability")
        }
        allowed = set(FUEL_ITEMS)
        for tag in FUEL_TAGS:
            allowed.update(self.k.item_tags.get(tag, ()))
        allowed.update(i for i in self.inv if i in HELD_FUEL_ITEMS)
        return sorted((i, t) for i, t in burnable.items() if i in allowed)

    def fuel_choices(self) -> list[tuple[str, int]]:
        """Fuels the expansion may pick: coal, sticks, held fuels and the cheapest
        member of each fuel tag (few, so the dependency graph stays acyclic)."""
        keep = {i for i, _ in self.fuels if i in FUEL_ITEMS or self.inv.get(i, 0) > 0}
        for tag in FUEL_TAGS:
            members = [i for i, _ in self.fuels if i in self.k.item_tags.get(tag, ())]
            if members:
                keep.add(min(members, key=lambda i: self.cost_n.get(i, INF)))
        return [(i, t) for i, t in self.fuels if i in keep]

    # costs
    def candidates(
        self, g: _Gat, cost: Mapping[str, float] | None = None
    ) -> list[_Tool | None]:
        """Tools worth considering: bare hand, held tools, and the cheapest tool of
        exactly the tier the drop needs (a higher tier would be built from this very
        tier). Only one craftable tool, so the dependency graph stays acyclic."""
        cost = self.cost if cost is None else cost
        out: list[_Tool | None] = [] if g.need_tool else ([None] if g.rank == 0 else [])
        made: list[_Tool] = []
        for t in self.tools.get(g.kind, ()) if g.kind else ():
            if self.inv.get(t.item, 0) > 0 and t.rank >= g.rank:
                out.append(t)
            elif (g.rank > 0 and t.rank == g.rank) or g.need_tool:
                made.append(t)
        if made and cost is not None:
            out.append(min(made, key=lambda t: cost.get(t.item, INF)))
        return out

    def upgrades(self, g: _Gat, item: str) -> list[_Upgrade]:
        """Better tools of the same kind that are crafted from `item` itself."""
        out: list[_Upgrade] = []
        for tool in self.tools.get(g.kind, ()) if g.kind and not g.need_tool else ():
            if tool.rank <= g.rank or self.inv.get(tool.item, 0):
                continue
            for r in self.recs.get(tool.item, ()):
                mat = [n for opts, n in r.ing if item in opts]
                if r.cook is None and mat:
                    others = tuple((o, n) for o, n in r.ing if item not in o)
                    out.append(_Upgrade(tool, r, sum(mat), others))
                    break
        return out

    def pick_tool(self, kind: str, cost: Mapping[str, float]) -> _Tool | None:
        """Tool for a transform: a held one if any, else the cheapest to make."""
        best, best_c = None, INF
        for t in self.tools.get(kind, ()):
            c = HELD_COST if self.inv.get(t.item, 0) else cost.get(t.item, INF)
            if c < best_c:
                best, best_c = t, c
        return best

    @staticmethod
    def block_cost(g: _Gat, tool: _Tool | None) -> float:
        speed = tool.speed if tool and tool.kind == g.kind else 1.0
        ticks = max(1.0, g.hardness * MINE_TICKS_FACTOR / speed)
        cost = TRAVEL_TICKS_PER_BLOCK + g.search / SEARCH_BATCH + ticks
        return cost * (KNOWN_COST_FACTOR if g.known else 1.0)

    def _station_cost(self, station: str, cost: Mapping[str, float]) -> float:
        if station == "inventory" or station in self.known:
            return 0.0
        return cost.get(f"minecraft:{station}", INF) + PLACE_STATION_TICKS

    def fuel_for(
        self, rec: _Rec, cost: Mapping[str, float]
    ) -> tuple[str | None, float]:
        """Cheapest fuel and its cost per item smelted, buying whole fuel items for a
        batch of SMELT_BATCH smelts."""
        best: tuple[str | None, float] = (None, INF)
        cook = rec.cook or 200
        for item, ticks in self.fuels:
            if item == rec.result:
                continue
            units = math.ceil(cook * SMELT_BATCH / ticks - EPS)
            c = cost.get(item, INF) * units / SMELT_BATCH
            if c < best[1]:
                best = (item, c)
        return best

    def pick(self, options: tuple[str, ...], cost: Mapping[str, float]) -> str:
        return min(options, key=lambda o: cost.get(o, INF))

    def _options(
        self, item: str, cost: Mapping[str, float]
    ) -> list[tuple[float, Option]]:
        out: list[tuple[float, Option]] = []
        for g in self.gathers.get(item, ()):
            for t in self.candidates(g, cost):
                tool_c = 0.0
                if t is not None and self.inv.get(t.item, 0) == 0:
                    tool_c = cost.get(t.item, INF) / t.dur
                out.append(((self.block_cost(g, t) + tool_c) / g.yield_, ("gather", g)))
        for tr in self.transforms.get(item, ()):
            tool = self.pick_tool(tr.tool, cost)
            if tool is not None:
                tool_c = 0.0 if self.inv.get(tool.item, 0) else cost.get(tool.item, INF)
                c = cost.get(tr.input, INF) + TRANSFORM_OVERHEAD + tool_c / tool.dur
                out.append((c / tr.count, ("transform", tr, tool.item)))
        for r in self.recs.get(item, ()):
            total = sum(
                (min((cost.get(o, INF) for o in opts), default=INF)) * n
                for opts, n in r.ing
            )
            fuel: str | None = None
            if r.cook is not None:
                fuel, fuel_c = self.fuel_for(r, cost)
                total += fuel_c + r.cook * SMELT_WAIT_FACTOR
            total += CRAFT_OVERHEAD
            c = total / r.out + self._station_cost(r.station, cost) / STATION_AMORTIZE
            out.append((c, ("smelt", r, fuel) if r.cook is not None else ("craft", r)))
        scale = self.prefer.get(item, 1.0)
        return [
            (c * scale, o)
            for c, o in out
            if c < INF and (o[0] != "smelt" or o[2] is not None)
        ]

    def _solve(self, *, held: bool) -> tuple[dict[str, float], dict[str, Option]]:
        cost: dict[str, float] = {i: HELD_COST for i in self.inv} if held else {}
        for _ in range(MAX_SOLVE_ITERATIONS):  # Bellman-Ford style relaxation
            changed = False
            for item in self.producible:
                opts = self._options(item, cost)
                if (
                    opts
                    and (c := min(opts, key=lambda x: x[0])[0])
                    < cost.get(item, INF) - EPS
                ):
                    cost[item] = c
                    changed = True
            if not changed:
                break
        best: dict[str, Option] = {}
        for item in self.producible:
            opts = self._options(item, cost)
            if opts:
                best[item] = min(opts, key=lambda x: x[0])[1]
        return cost, best

    # explanation of failures
    @staticmethod
    def _is_mob_drop(item: str) -> bool:
        return item in MOB_DROPS or item.endswith("_wool")

    def reason(self, item: str, seen: frozenset[str] = frozenset()) -> str:
        if item not in self.k.items:
            return "unknown item"
        if item in seen:
            return "no source"
        seen = seen | {item}
        found = [
            f"needs tier {self.tier_name(g.rank)}" for g in self.gathers.get(item, ())
        ]
        found += self.notes.get(item, ())
        for r in self.recs.get(item, ()):
            for opts, _ in r.ing:
                if all(self.cost.get(o, INF) == INF for o in opts):
                    found.append(self.reason(opts[0], seen) if opts else "no source")
            if (
                r.station not in ("inventory",)
                and self.cost.get(f"minecraft:{r.station}", INF) == INF
            ):
                found.append(self.reason(f"minecraft:{r.station}", seen))
        if not found and self._is_mob_drop(item):
            return "needs mob drop"
        order = ("needs mob drop", "needs tier", "needs unhandled", "needs station")
        return min(
            found or ["no source"],
            key=lambda s: next(
                (i for i, p in enumerate(order) if s.startswith(p)), len(order)
            ),
        )


# ---- expansion ------------------------------------------------------------
def _recipe_children(m: _Model, rec: _Rec) -> list[str]:
    out = [m.pick(opts, m.cost) for opts, _ in rec.ing]
    if rec.station != "inventory" and rec.station not in m.known:
        out.append(f"minecraft:{rec.station}")
    return out


def _children(m: _Model, item: str, best: Mapping[str, Option]) -> list[str]:
    """Items whose demand `item` may add to (so they must be expanded after it)."""
    opt = best.get(item)
    if opt is None:
        return []
    if opt[0] == "gather":
        out = [t.item for t in m.candidates(opt[1]) if t]
        for up in m.upgrades(opt[1], item):  # upgrade craft needs these as well
            out += _recipe_children(m, up.rec._replace(ing=up.others))
        return out
    if opt[0] == "transform":
        return [opt[1].input, opt[2]]
    out = _recipe_children(m, opt[1])
    if opt[0] == "smelt":
        out += [f for f, _ in m.fuel_choices()]
    return out


def _visit(m, i, best, state, order, cycle) -> None:
    state[i] = 1
    for c in _children(m, i, best):
        if state.get(c) == 1:
            cycle.append(c)
        elif c not in state:
            _visit(m, c, best, state, order, cycle)
    state[i] = 2
    order.append(i)


def _consumers_first(m: _Model, roots: list[str], best: dict[str, Option]) -> list[str]:
    """Topological order of the chosen-option graph, consumers before ingredients.
    A cycle (e.g. ingot <-> block when one is held) is broken by falling back to the
    held-agnostic choice for the item the cycle re-enters."""
    order: list[str] = []
    for _ in range(len(best) + 1):
        order, state, cycle = [], {}, []
        for r in roots:
            if r not in state:
                _visit(m, r, best, state, order, cycle)
        if not cycle:
            break
        alt = m.best_n.get(cycle[0])
        if alt is None or alt == best.get(cycle[0]):
            break
        best[cycle[0]] = alt
    return order[::-1]


def _group(task: Task) -> str:
    if isinstance(task, Craft | Smelt):
        return task.station
    return "gather"


class _Expander:
    """Walks the items consumers-first, summing demand and emitting each item's tasks.
    Items are expanded once, so a shared ingredient is crafted once for all users."""

    def __init__(self, m: _Model, targets: Mapping[str, int]) -> None:
        self.m = m
        self.need: dict[str, int] = {i: n for i, n in targets.items() if n > 0}
        self.have = dict(m.inv)
        self.best = dict(m.best)
        self.tool_left: dict[str, int] = {}
        self.tasks: dict[str, list[Task]] = {}
        self.deps: dict[str, set[str]] = {}
        self.missing: list[Missing] = []
        self.stations: set[str] = set()
        self.surplus: Counter[str] = Counter()

    def run(self) -> None:
        for item in _consumers_first(self.m, list(self.need), self.best):
            n = self.need.get(item, 0)
            take = min(self.have.get(item, 0), n)
            self.have[item] = self.have.get(item, 0) - take
            n -= take
            opt = self.best.get(item)
            if n <= 0:
                continue
            if opt is None:
                self.missing.append(Missing(item, self.m.reason(item)))
                continue
            d = self.deps.setdefault(item, set())
            getattr(self, "_" + opt[0])(item, n, opt, d)

    # tools
    def _make_needed(self, tool: _Tool, uses: int) -> int:
        left = self.tool_left.get(tool.item, self.have.get(tool.item, 0) * tool.dur)
        return max(0, math.ceil((uses - left) / tool.dur))

    def _use_tool(self, tool: _Tool, uses: int, d: set[str]) -> None:
        make = self._make_needed(tool, uses)
        if tool.item not in self.tool_left:
            self.tool_left[tool.item] = self.have.get(tool.item, 0) * tool.dur
            self.have[tool.item] = 0
        self.tool_left[tool.item] += make * tool.dur - uses
        if make:
            self.need[tool.item] = self.need.get(tool.item, 0) + make
        d.add(tool.item)

    def _station(self, rec: _Rec, d: set[str]) -> None:
        if rec.station == "inventory" or rec.station in self.m.known:
            return
        station_item = f"minecraft:{rec.station}"
        if rec.station not in self.stations:
            self.stations.add(rec.station)
            self.need[station_item] = self.need.get(station_item, 0) + 1
        d.add(station_item)

    def _ingredients(
        self, rec: _Rec, times: int, d: set[str], skip: str | None = None
    ) -> dict[str, int]:
        used: dict[str, int] = {}
        for opts, count in rec.ing:
            if skip is not None and skip in opts:
                continue
            chosen = self.m.pick(opts, self.m.cost)
            used[chosen] = used.get(chosen, 0) + count * times
            self.need[chosen] = self.need.get(chosen, 0) + count * times
            d.add(chosen)
        return used

    # gathering
    def _gather_cost(self, g: _Gat, tool: _Tool | None, blocks: int) -> float:
        total = blocks * self.m.block_cost(g, tool)
        if tool is not None:
            make = self._make_needed(tool, blocks)
            total += make * self.m.cost.get(tool.item, INF) if make else 0.0
        return total

    def _upgrade_option(self, g, item, n, t0, up):
        m, y = self.m, g.yield_
        blocks0 = math.ceil(up.material / y - EPS)
        count = 1
        while True:
            rest = math.ceil((n + (count - 1) * up.material) / y - EPS)
            if count * up.tool.dur >= rest:
                break
            count += 1
        own = max(
            0.0, m.cost.get(up.tool.item, INF) - up.material * m.cost.get(item, 0.0)
        )
        total = (
            self._gather_cost(g, t0, blocks0)
            + rest * m.block_cost(g, up.tool)
            + count * own
        )
        return total, t0, (up, blocks0, rest, count)

    def _choose(self, g: _Gat, item: str, n: int, blocks: int):
        options = [
            (self._gather_cost(g, t, blocks), t, None) for t in self.m.candidates(g)
        ]
        for up in self.m.upgrades(g, item):
            blocked = up.tool.item in self.need or up.tool.item in self.tasks
            for t0 in self.m.candidates(g):
                if t0 is not None and up.tool.rank > t0.rank and not blocked:
                    options.append(self._upgrade_option(g, item, n, t0, up))
        options = [o for o in options if o[0] < INF]
        return min(options, key=lambda o: o[0]) if options else None

    def _gather(self, item: str, n: int, opt: Option, d: set[str]) -> None:
        g: _Gat = opt[1]
        blocks = math.ceil(n / g.yield_ - EPS)
        choice = self._choose(g, item, n, blocks)
        if choice is None:
            self.missing.append(Missing(item, self.m.reason(item)))
            return
        _, tool, upgrade = choice
        if upgrade is not None:
            self._upgraded(item, n, g, tool, upgrade, d)
            return
        extra = int(blocks * g.yield_ + EPS) - n
        if extra > 0:
            self.surplus[item] += extra
        mine = Gather(
            g.block,
            blocks,
            g.kind if tool else None,
            self.m.tier_name(g.rank),
            item,
            n,
        )
        if tool is None:
            self.tasks[item] = [mine]
            return
        self._use_tool(tool, blocks, d)
        self.tasks[item] = [Equip(tool.item), mine]

    def _upgraded(self, item, n, g, t0, upgrade, d) -> None:
        """Mine with `t0` until the better tool can be crafted from the mined
        material, then mine the rest with it (crafting replacements as needed)."""
        up, blocks0, rest, count = upgrade
        self._use_tool(t0, blocks0, d)
        others = self._ingredients(up.rec, count, d, skip=item)
        self._station(up.rec, d)
        self.tool_left[up.tool.item] = count * up.tool.dur - rest
        total = n + count * up.material
        craft = Craft(
            up.rec.id,
            1,
            up.rec.station,
            up.tool.item,
            ((item, up.material), *((i, c // count) for i, c in others.items())),
            up.rec.out,
        )

        def mine(blocks: int, items: int) -> Gather:
            return Gather(
                g.block, blocks, g.kind, self.m.tier_name(g.rank), item, items
            )

        got = int(blocks0 * g.yield_ + EPS)
        seq: list[Task] = [Equip(t0.item), mine(blocks0, got)]
        left = rest
        for _ in range(count):
            seg = min(up.tool.dur, left)
            left -= seg
            items = total - got if left == 0 else int(seg * g.yield_ + EPS)
            got += items
            seq += [craft, Equip(up.tool.item), mine(seg, items)]
        self.tasks[item] = seq

    # transforming
    def _transform(self, item: str, n: int, opt: Option, d: set[str]) -> None:
        tr: TransformDef = opt[1]
        tool = self.m.tool_by_item[opt[2]]
        times = math.ceil(n / tr.count - EPS)
        if times * tr.count > n:
            self.surplus[item] += times * tr.count - n
        self.need[tr.input] = self.need.get(tr.input, 0) + times
        d.add(tr.input)
        self._use_tool(tool, times, d)
        self.tasks[item] = [
            Equip(tool.item),
            Transform(tr.id, times, tr.input, item, tool.kind, times * tr.count),
        ]

    # crafting and smelting
    def _fuel(self, rec: _Rec, times: int, default: str) -> tuple[str, int]:
        """Cheapest whole-item fuel for `times` smelts; held items count nearly free."""
        total = times * (rec.cook or 200)
        best = (INF, default, math.ceil(total / dict(self.m.fuels)[default] - EPS))
        for item, ticks in self.m.fuel_choices():
            if item == rec.result:
                continue
            units = math.ceil(total / ticks - EPS)
            held = min(self.have.get(item, 0), units)
            c = held * HELD_COST + (units - held) * self.m.cost_n.get(item, INF)
            if c < best[0]:
                best = (c, item, units)
        return best[1], best[2]

    def _craft(self, item: str, n: int, opt: Option, d: set[str]) -> None:
        rec: _Rec = opt[1]
        times = math.ceil(n / rec.out - EPS)
        if times * rec.out > n:
            self.surplus[item] += times * rec.out - n
        used = self._ingredients(rec, times, d)
        self._station(rec, d)
        self.tasks[item] = [
            Craft(
                rec.id, times, rec.station, item, tuple(used.items()), times * rec.out
            )
        ]

    def _smelt(self, item: str, n: int, opt: Option, d: set[str]) -> None:
        rec: _Rec = opt[1]
        times = math.ceil(n / rec.out - EPS)
        if times * rec.out > n:
            self.surplus[item] += times * rec.out - n
        used = self._ingredients(rec, times, d)
        self._station(rec, d)
        fuel, fuel_count = self._fuel(rec, times, opt[2])
        self.need[fuel] = self.need.get(fuel, 0) + fuel_count
        d.add(fuel)
        self.tasks[item] = [
            Smelt(
                rec.id,
                times,
                fuel,
                fuel_count,
                rec.station,
                item,
                next(iter(used)),
                times * rec.out,
            )
        ]


def plan(
    targets: Mapping[str, int],
    inventory: Mapping[str, int],
    knowledge: Knowledge,
    stations: Mapping[str, object] | None = None,
    *,
    executable_stations: Collection[str] = DEFAULT_STATIONS,
    known_blocks: Collection[str] = (),
    prefer: Mapping[str, float] | None = None,
) -> PlanResult:
    """Ordered tasks that turn `inventory` into `targets`; unreachable parts are
    reported in `missing` instead of raising.

    `prefer` maps item ids to cost multipliers (below 1 favours the item); it
    defaults to DEFAULT_PREFER (oak). `known_blocks` are block ids seen nearby:
    they are gatherable and cheap to find."""
    m = _Model(
        knowledge,
        inventory,
        stations or {},
        executable_stations,
        known_blocks,
        DEFAULT_PREFER if prefer is None else prefer,
    )
    ex = _Expander(m, targets)
    ex.run()
    ordered = _insert_stations(_schedule(ex.tasks, ex.deps), ex.stations)
    cost = sum(
        m.cost.get(i, INF) * n for i, n in targets.items() if m.cost.get(i, INF) < INF
    )
    return PlanResult(tuple(ordered), tuple(ex.missing), cost, dict(ex.surplus))


def _schedule(tasks: dict[str, list[Task]], deps: dict[str, set[str]]) -> list[Task]:
    """Dependencies first; among ready items keep the current group (gather, one
    station) together, otherwise take the cheapest group first."""
    remaining = list(reversed(tasks))
    done: set[str] = set()
    out: list[Task] = []
    last = None
    while remaining:
        ready = [
            i
            for i in remaining
            if all(c in done or c not in tasks for c in deps.get(i, ()))
        ]
        ready = (
            ready or remaining[:1]
        )  # unreachable unless the choice graph had a cycle
        pick = min(
            ready,
            key=lambda i: (
                -1
                if _group(tasks[i][-1]) == last
                else GROUP_RANK.get(_group(tasks[i][-1]), 9),
                remaining.index(i),
            ),
        )
        remaining.remove(pick)
        done.add(pick)
        out.extend(tasks[pick])
        last = _group(tasks[pick][-1])
    return out


def _insert_stations(ordered: list[Task], wanted: set[str]) -> list[Task]:
    out: list[Task] = []
    placed: set[str] = set()
    for task in ordered:
        station = task.station if isinstance(task, Craft | Smelt) else None
        if station in wanted and station not in placed:
            placed.add(station)
            out.append(PlaceStation(station))
        out.append(task)
    return out


# ---- validation and reporting ---------------------------------------------
def check_order(
    tasks: Collection[Task],
    inventory: Mapping[str, int],
    knowledge: Knowledge,
    stations: Mapping[str, object] | None = None,
) -> list[str]:
    """Replay `tasks` from `inventory`; return one message per step that could not
    run (missing tool, ingredient, fuel or station). Empty means the order is valid."""
    inv = Counter({i: n for i, n in inventory.items() if n > 0})
    placed = {"inventory"} | {s for s, where in (stations or {}).items() if where}
    ranks = {
        n: i for i, n in enumerate(t for t in knowledge.drop_tiers if t != "silk_touch")
    }
    problems: list[str] = []

    def holds_tool(kind: str, tier: str) -> bool:
        for item, n in inv.items():
            tool = knowledge.items.get(item, {}).get("tool")
            if n > 0 and tool and tool["kind"] == kind:
                rank = TIER_RANK.get(tool.get("tier", ""), UNKNOWN_TIER_RANK)
                if rank >= ranks.get(tier, 0):
                    return True
        return False

    def take(step: int, task: Task, item: str, n: int) -> None:
        if inv[item] < n:
            problems.append(f"step {step} {task}: needs {n} {item}, has {inv[item]}")
        inv[item] = max(0, inv[item] - n)

    def need_station(step: int, task: Task, station: str) -> None:
        if station not in placed:
            problems.append(f"step {step} {task}: {station} not placed")

    for step, task in enumerate(tasks):
        if isinstance(task, Equip):
            take(step, task, task.item, 0) if inv[task.item] else problems.append(
                f"step {step} {task}: not held"
            )
        elif isinstance(task, PlaceStation):
            take(step, task, f"minecraft:{task.kind}", 1)
            placed.add(task.kind)
        elif isinstance(task, Gather):
            if task.tool_kind and not holds_tool(task.tool_kind, task.min_tier):
                problems.append(f"step {step} {task}: no {task.tool_kind} held")
            inv[task.item] += task.item_count
        elif isinstance(task, Craft):
            need_station(step, task, task.station)
            for item, n in task.ingredients:
                take(step, task, item, n)
            inv[task.result] += task.result_count
        elif isinstance(task, Smelt):
            need_station(step, task, task.station)
            take(step, task, task.ingredient, task.count)
            take(step, task, task.fuel, task.fuel_count)
            inv[task.result] += task.result_count
        elif isinstance(task, Transform):
            if not holds_tool(task.tool_kind, "hand"):
                problems.append(f"step {step} {task}: no {task.tool_kind} held")
            take(step, task, task.input, task.count)
            inv[task.output] += task.result_count
    return problems


def summarize(result: PlanResult) -> str:
    """Short human-readable account of a plan: gathers, crafts, smelts, gaps."""
    gathers: Counter[str] = Counter()
    crafts: Counter[str] = Counter()
    for t in result.tasks:
        if isinstance(t, Gather):
            gathers[t.block] += t.count
        elif isinstance(t, Craft | Smelt):
            crafts[t.result] += t.result_count
        elif isinstance(t, Transform):
            crafts[t.output] += t.result_count
    short = lambda i: i.removeprefix("minecraft:")  # noqa: E731
    lines = [f"{len(result.tasks)} tasks, estimated cost {result.estimated_cost:.0f}"]
    lines.append("gather: " + ", ".join(f"{short(b)} x{n}" for b, n in gathers.items()))
    lines.append("make:   " + ", ".join(f"{short(i)} x{n}" for i, n in crafts.items()))
    lines.append(
        "missing: " + ", ".join(f"{short(m.item)} ({m.reason})" for m in result.missing)
    )
    return "\n".join(lines)
