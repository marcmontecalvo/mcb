"""Gathering planner: cheapest item tree from a knowledge pack to an ordered task list.

Pure symbolic code. `plan()` is stateless, so re-planning after a task finishes or
fails is just another call with the new inventory.

Cost unit is "ticks of effort"; only relative sizes matter.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from mclab.knowledge import Knowledge

# ---- cost model constants -------------------------------------------------
INF = math.inf
HELD_COST = 0.1  # price of an item already in the inventory (any count)
TRAVEL_TICKS_PER_BLOCK = 40.0  # walking/collecting overhead per block mined
SEARCH_TICKS = {"tree": 120.0, "surface": 240.0, "underground": 400.0}
DEFAULT_SEARCH_TICKS = 300.0  # `where` missing or unknown
SEARCH_BATCH = 8  # one search is shared by this many blocks
MINE_TICKS_FACTOR = 30.0  # vanilla: ticks = hardness * 30 / tool speed (right tool)
CRAFT_OVERHEAD = 10.0  # per craft/smelt operation
PLACE_STATION_TICKS = 20.0
STATION_AMORTIZE = 16.0  # a station's cost is shared by this many crafts
SMELT_WAIT_FACTOR = (
    0.25  # fraction of cook_ticks that is not overlapped with other work
)
DEFAULT_DURABILITY = 1000
MAX_SOLVE_ITERATIONS = 200
EPS = 1e-9

DEFAULT_STATIONS = frozenset({"inventory", "crafting_table", "furnace"})
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
GATHER_TOOL_KINDS = frozenset({"pickaxe", "axe", "shovel", "hoe"})
GROUP_RANK = {"gather": 0, "inventory": 1, "crafting_table": 2, "furnace": 3}


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


@dataclass(frozen=True)
class Smelt:
    recipe_id: str
    count: int  # inputs to smelt
    fuel: str
    fuel_count: int
    station: str
    result: str
    ingredient: str


@dataclass(frozen=True)
class PlaceStation:
    kind: str


@dataclass(frozen=True)
class Equip:
    item: str


Task = Gather | Craft | Smelt | PlaceStation | Equip


@dataclass(frozen=True)
class Missing:
    item: str
    reason: str


@dataclass(frozen=True)
class PlanResult:
    tasks: tuple[Task, ...]
    missing: tuple[Missing, ...]
    estimated_cost: float

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


class _Rec(NamedTuple):
    id: str
    ing: tuple[tuple[tuple[str, ...], int], ...]
    result: str
    out: int
    station: str
    cook: int | None  # not None: a smelting-like recipe


Option = tuple  # ("gather", _Gat) | ("craft", _Rec) | ("smelt", _Rec, fuel_item)


class _Model:
    def __init__(
        self,
        k: Knowledge,
        inventory: Mapping[str, int],
        stations: Mapping[str, object],
        executable: Collection[str],
        known_blocks: Collection[str],
    ) -> None:
        self.k = k
        self.inv = {i: n for i, n in inventory.items() if n > 0}
        self.known = {s for s, where in stations.items() if where}
        self.executable = frozenset(executable)
        self.rank_of = {
            n: i for i, n in enumerate(t for t in k.drop_tiers if t != "silk_touch")
        }
        self.tools = self._index_tools()
        self.gathers = self._index_gathers(set(k.sources) | set(known_blocks))
        self.notes: dict[str, list[str]] = {}
        self.recs = self._index_recipes()
        self.fuels = [
            (i, int(v["fuel_ticks"])) for i, v in k.items.items() if v.get("fuel_ticks")
        ]
        self.producible = sorted(set(self.gathers) | set(self.recs))
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

    def _index_gathers(self, sourced: set[str]) -> dict[str, list[_Gat]]:
        out: dict[str, list[_Gat]] = {}
        for block in self.k.drops:
            info = self.k.blocks.get(block)
            if info is None or block not in sourced:
                continue
            mineable = info.get("mineable") or ()
            where = (self.k.sources.get(block) or {}).get("where")
            search = SEARCH_TICKS.get(where, DEFAULT_SEARCH_TICKS)
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

    # costs
    def candidates(self, g: _Gat) -> list[_Tool | None]:
        """Tools worth considering: bare hand, held tools, or the tool of exactly the
        tier the drop needs (a higher tier would be built from this very tier)."""
        out: list[_Tool | None] = [None] if g.rank == 0 else []
        for t in self.tools.get(g.kind, ()) if g.kind else ():
            if (g.rank > 0 and t.rank == g.rank) or (
                self.inv.get(t.item, 0) > 0 and t.rank >= g.rank
            ):
                out.append(t)
        return out

    @staticmethod
    def block_cost(g: _Gat, tool: _Tool | None) -> float:
        speed = tool.speed if tool and tool.kind == g.kind else 1.0
        ticks = max(1.0, g.hardness * MINE_TICKS_FACTOR / speed)
        return TRAVEL_TICKS_PER_BLOCK + g.search / SEARCH_BATCH + ticks

    def _station_cost(self, station: str, cost: Mapping[str, float]) -> float:
        if station == "inventory" or station in self.known:
            return 0.0
        return cost.get(f"minecraft:{station}", INF) + PLACE_STATION_TICKS

    def fuel_for(
        self, rec: _Rec, cost: Mapping[str, float]
    ) -> tuple[str | None, float]:
        """Cheapest fuel and its cost per item smelted."""
        best: tuple[str | None, float] = (None, INF)
        for item, ticks in self.fuels:
            if item == rec.result:
                continue
            c = cost.get(item, INF) * (rec.cook or 200) / ticks
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
            for t in self.candidates(g):
                tool_c = 0.0
                if t is not None and self.inv.get(t.item, 0) == 0:
                    tool_c = cost.get(t.item, INF) / t.dur
                out.append(((self.block_cost(g, t) + tool_c) / g.yield_, ("gather", g)))
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
        return [
            (c, o) for c, o in out if c < INF and (o[0] != "smelt" or o[2] is not None)
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
        order = ("needs tier", "needs unhandled", "needs station")
        return min(
            found or ["no source"],
            key=lambda s: next(
                (i for i, p in enumerate(order) if s.startswith(p)), len(order)
            ),
        )


# ---- expansion ------------------------------------------------------------
def _children(m: _Model, item: str, best: Mapping[str, Option]) -> list[str]:
    opt = best.get(item)
    if opt is None:
        return []
    if opt[0] == "gather":
        return [t.item for t in m.candidates(opt[1]) if t]
    rec: _Rec = opt[1]
    out = [m.pick(opts, m.cost) for opts, _ in rec.ing]
    if opt[0] == "smelt":
        out.append(opt[2])
    if rec.station != "inventory" and rec.station not in m.known:
        out.append(f"minecraft:{rec.station}")
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


def _choose_tool(m, g, blocks, have, tool_left):
    best = None
    for t in m.candidates(g):
        make = 0
        total = blocks * m.block_cost(g, t)
        if t is not None:
            left = tool_left.get(t.item, have.get(t.item, 0) * t.dur)
            make = max(0, math.ceil((blocks - left) / t.dur))
            total += make * m.cost.get(t.item, INF) if make else 0.0
        if total < INF and (best is None or total < best[0]):
            best = (total, t, make)
    return best


def _group(task: Task) -> str:
    if isinstance(task, Gather):
        return "gather"
    return task.station if isinstance(task, Craft | Smelt) else "gather"


def plan(
    targets: Mapping[str, int],
    inventory: Mapping[str, int],
    knowledge: Knowledge,
    stations: Mapping[str, object] | None = None,
    *,
    executable_stations: Collection[str] = DEFAULT_STATIONS,
    known_blocks: Collection[str] = (),
) -> PlanResult:
    """Ordered tasks that turn `inventory` into `targets`; unreachable parts are
    reported in `missing` instead of raising."""
    m = _Model(knowledge, inventory, stations or {}, executable_stations, known_blocks)
    need: dict[str, int] = {i: n for i, n in targets.items() if n > 0}
    have = dict(m.inv)
    best = dict(m.best)
    tool_left: dict[str, int] = {}
    tasks: dict[str, list[Task]] = {}
    deps: dict[str, set[str]] = {}
    missing: list[Missing] = []
    station_wanted: set[str] = set()

    for item in _consumers_first(m, list(need), best):
        n = need.get(item, 0)
        take = min(have.get(item, 0), n)
        have[item] = have.get(item, 0) - take
        n -= take
        if n <= 0:
            continue
        opt = best.get(item)
        if opt is None:
            missing.append(Missing(item, m.reason(item)))
            continue
        d = deps.setdefault(item, set())
        if opt[0] == "gather":
            g: _Gat = opt[1]
            blocks = math.ceil(n / g.yield_ - EPS)
            choice = _choose_tool(m, g, blocks, have, tool_left)
            if choice is None:
                missing.append(Missing(item, m.reason(item)))
                continue
            _, tool, make = choice
            mine = Gather(
                g.block, blocks, g.kind if tool else None, m.tier_name(g.rank), item, n
            )
            if tool is None:
                tasks[item] = [mine]
                continue
            if tool.item not in tool_left:
                tool_left[tool.item] = have.get(tool.item, 0) * tool.dur
                have[tool.item] = 0
            tool_left[tool.item] += make * tool.dur - blocks
            if make:
                need[tool.item] = need.get(tool.item, 0) + make
                d.add(tool.item)
            tasks[item] = [Equip(tool.item), mine]
            continue

        rec: _Rec = opt[1]
        times = math.ceil(n / rec.out)
        used: dict[str, int] = {}
        for opts, count in rec.ing:
            chosen = m.pick(opts, m.cost)
            used[chosen] = used.get(chosen, 0) + count * times
            need[chosen] = need.get(chosen, 0) + count * times
            d.add(chosen)
        if rec.station != "inventory" and rec.station not in m.known:
            station_item = f"minecraft:{rec.station}"
            if rec.station not in station_wanted:
                station_wanted.add(rec.station)
                need[station_item] = need.get(station_item, 0) + 1
            d.add(station_item)
        if opt[0] == "smelt":
            fuel = opt[2]
            ticks = dict(m.fuels)[fuel]
            fuel_count = math.ceil(times * (rec.cook or 200) / ticks - EPS)
            need[fuel] = need.get(fuel, 0) + fuel_count
            d.add(fuel)
            ingredient = next(iter(used))
            tasks[item] = [
                Smelt(rec.id, times, fuel, fuel_count, rec.station, item, ingredient)
            ]
        else:
            tasks[item] = [Craft(rec.id, times, rec.station, item, tuple(used.items()))]

    ordered = _schedule(tasks, deps)
    ordered = _insert_stations(ordered, station_wanted)
    cost = sum(
        m.cost.get(i, INF) * n for i, n in targets.items() if m.cost.get(i, INF) < INF
    )
    return PlanResult(tuple(ordered), tuple(missing), cost)


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
