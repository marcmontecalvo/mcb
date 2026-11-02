"""Gathering planner: cheapest item tree from a knowledge pack to an ordered task list.

Pure symbolic code. `plan()` is stateless, so re-planning after a task finishes or
fails is just another call with the new inventory. `check_order()` replays a task
list against an inventory and reports every step that could not run.

Cost unit is "ticks of effort"; only relative sizes matter.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from typing import Any, NamedTuple

from mclab.gather_check import Replay, check_order, summarize
from mclab.gather_storage import add_storage
from mclab.gather_tasks import (
    DEFAULT_DURABILITY,
    TIER_RANK,
    UNKNOWN_TIER_RANK,
    Craft,
    Deposit,
    Equip,
    Gather,
    Hunt,
    Inventory,
    Missing,
    PlaceContainer,
    PlaceStation,
    PlanResult,
    Shear,
    Smelt,
    Task,
    Transform,
    Withdraw,
    container_contents,
    normalize_inventory,
)
from mclab.knowledge import Drop, Knowledge
from mclab.knowledge import Transform as TransformDef

__all__ = [
    "Craft",
    "Deposit",
    "Equip",
    "Gather",
    "Hunt",
    "Missing",
    "PlaceContainer",
    "PlaceStation",
    "PlanResult",
    "Shear",
    "Smelt",
    "Task",
    "Transform",
    "Withdraw",
    "check_order",
    "food_needed",
    "plan",
    "summarize",
]

# ---- cost model constants -------------------------------------------------
INF = math.inf
HELD_COST = 0.1  # price of an item already in the inventory (any count)
TRAVEL_TICKS_PER_BLOCK = 40.0  # walking/collecting overhead per block mined
SEARCH_TICKS = {"tree": 120.0, "surface": 240.0, "underground": 400.0, "mob": 200.0}
DEFAULT_SEARCH_TICKS = 300.0  # `where` missing or unknown
KNOWN_COST_FACTOR = 0.8  # a block type already seen nearby is cheaper to gather
SEARCH_BATCH = 8  # one search is shared by this many blocks
HUNT_TICKS = 160.0  # chase and kill one animal (bare hand)
SHEAR_TICKS = 80.0  # chase and shear one animal
WEAPON_DISCOUNT_PER_RANK = 0.1  # hunt cost factor drops by this per weapon tier rank
MIN_WEAPON_FACTOR = 0.5
CHANCE_HALF = 0.5  # drops with a lower chance get a bigger margin
MARGIN_BASE = 0.5  # margin for chance p < 0.5 is max(2, ceil(MARGIN_BASE / p))
MAX_MARGIN = 10
FOOD_TICKS_PER_POINT = 80  # about 1 hunger point per 80 ticks of work and sprinting
FOOD_FLOOR = 6  # sprinting stops at 6 food points; plan to stay above it
WEAPON_KINDS = ("sword", "axe")
CHEST = "minecraft:chest"
HUNT_CATEGORIES = frozenset({"creature", "water_creature", "ambient"})
MINE_TICKS_FACTOR = 30.0  # vanilla: ticks = hardness * 30 / tool speed (right tool)
CRAFT_OVERHEAD = 10.0  # per craft/smelt operation
TRANSFORM_OVERHEAD = 60.0  # place the block, use the tool on it, break it again
PLACE_STATION_TICKS = 20.0
STATION_AMORTIZE = 16.0  # a station's cost is shared by this many crafts
SMELT_WAIT_FACTOR = (
    0.25  # fraction of cook_ticks that is not overlapped with other work
)
SMELT_BATCH = 8  # items smelted per fuel purchase when pricing fuel in the model
MAX_SOLVE_ITERATIONS = 200
EPS = 1e-9
REST = "#rest"  # key suffix of the second half of a split gather
MAX_TOOL_PASSES = 8
MAX_POLICY_RANK = 3  # no diamond/netherite tools just to mine faster

DEFAULT_STATIONS = frozenset({"inventory", "crafting_table", "furnace"})
# Oak is the default wood: a slight discount so that ties resolve to it.
DEFAULT_PREFER = {"minecraft:oak_log": 0.9}
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
    margin: int = 0  # spare blocks for chance/range drops (G4)


class _Mob(NamedTuple):
    entity: str
    item: str
    yield_: float  # expected items per animal (killed, or per shear)
    margin: int
    kind: str  # "hunt" | "shear"
    tool_kind: str | None  # shears for "shear"
    search: float
    known: bool


def margin_for(d: Drop) -> int:
    """Spare blocks (or animals) to plan for a drop that is not certain (G4)."""
    if d.chance >= 1 and d.min == d.max:
        return 0
    if d.chance >= CHANCE_HALF:
        return 1
    return min(MAX_MARGIN, max(2, math.ceil(MARGIN_BASE / d.chance - EPS)))


class _Rec(NamedTuple):
    id: str
    ing: tuple[tuple[tuple[str, ...], int], ...]
    result: str
    out: int
    station: str
    cook: int | None  # not None: a smelting-like recipe


# ("gather", _Gat) | ("craft", _Rec) | ("smelt", _Rec, fuel) | ("transform", def, tool)
Option = tuple


class _Model:
    def __init__(
        self,
        k: Knowledge,
        inventory: Mapping[str, int],
        wear: Mapping[str, list[int]],
        stations: Mapping[str, object],
        executable: Collection[str],
        known_blocks: Collection[str],
        prefer: Mapping[str, float],
    ) -> None:
        self.k = k
        self.inv = {i: n for i, n in inventory.items() if n > 0}
        self.wear = wear
        self.policy: dict[str, int] = {}  # tool kind -> tier rank ceiling (G2)
        self._upstream: dict[str, frozenset[str]] = {}
        self.known = {s for s, where in stations.items() if where}
        self.executable = frozenset(executable)
        self.prefer = prefer
        self.known_blocks = frozenset(known_blocks)
        self.rank_of = {
            n: i for i, n in enumerate(t for t in k.drop_tiers if t != "silk_touch")
        }
        self.tools = self._index_tools()
        self.tool_by_item = {t.item: t for ts in self.tools.values() for t in ts}
        sourced = set(k.sources) | self.known_blocks
        self.gathers = self._index_gathers(sourced)
        self.mob_srcs = self._index_mobs(sourced)
        self.weapon, self.weapon_factor = self._find_weapon()
        self.notes: dict[str, list[str]] = {}
        self.recs = self._index_recipes()
        self.transforms = self._index_transforms()
        self.fuels = self._index_fuels()
        self.producible = sorted(
            set(self.gathers)
            | set(self.recs)
            | set(self.transforms)
            | set(self.mob_srcs)
        )
        self.cost, self.best = self._solve(held=True)
        self.cost_n, self.best_n = self._solve(held=False)

    # indexing
    def tier_name(self, rank: int) -> str:
        return next((n for n, r in self.rank_of.items() if r == rank), str(rank))

    def tier_rank(self, tier: str) -> int:
        return TIER_RANK.get(tier, self.rank_of.get(tier, UNKNOWN_TIER_RANK))

    def held_uses(self, tool: _Tool) -> int:
        """Remaining uses of the held pieces of `tool` (wear is known per piece)."""
        if tool.item in self.wear:
            return sum(self.wear[tool.item])
        return self.inv.get(tool.item, 0) * tool.dur

    def _find_weapon(self) -> tuple[str | None, float]:
        best: tuple[int, bool, str] | None = None
        for item, n in self.inv.items():
            tool = self.k.items.get(item, {}).get("tool")
            if n > 0 and tool and tool["kind"] in WEAPON_KINDS:
                if item in self.wear and not any(self.wear[item]):
                    continue
                key = (self.tier_rank(tool.get("tier", "")), tool["kind"] == "sword")
                if best is None or key > best[:2]:
                    best = (*key, item)
        if best is None:
            return None, 1.0
        return best[2], max(MIN_WEAPON_FACTOR, 1 - WEAPON_DISCOUNT_PER_RANK * best[0])

    def _index_mobs(self, sourced: set[str]) -> dict[str, list[_Mob]]:
        out: dict[str, list[_Mob]] = {}
        for entity, info in self.k.mobs.items():
            if entity not in sourced or not info.get("passive"):
                continue
            if info.get("category") not in HUNT_CATEGORIES:
                continue
            search = SEARCH_TICKS["mob"]
            known = entity in self.known_blocks
            for d in self.k.mob_drops(entity):
                if d.expected > 0:
                    mob = _Mob(
                        entity,
                        d.item,
                        d.expected,
                        margin_for(d),
                        "hunt",
                        None,
                        search,
                        known,
                    )
                    out.setdefault(d.item, []).append(mob)
            for inter in info.get("interactions") or ():
                tool = self.k.items.get(inter.get("item"), {}).get("tool")
                gives = inter.get("gives")
                if not gives or not tool or tool["kind"] not in GATHER_TOOL_KINDS:
                    continue
                d = Drop(
                    gives,
                    inter.get("min", 1),
                    inter.get("max", 1),
                    inter.get("chance", 1.0),
                )
                if d.expected > 0:
                    mob = _Mob(
                        entity,
                        gives,
                        d.expected,
                        margin_for(d),
                        "shear",
                        tool["kind"],
                        search,
                        known,
                    )
                    out.setdefault(gives, []).append(mob)
        return out

    def _index_tools(self) -> dict[str, list[_Tool]]:
        out: dict[str, list[_Tool]] = {}
        for item, info in self.k.items.items():
            tool = info.get("tool")
            if not tool or tool["kind"] not in GATHER_TOOL_KINDS:
                continue
            tier = tool.get("tier", "")
            rank = self.tier_rank(tier)
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
                            margin_for(d),
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
    def ceiling_tool(self, kind: str, rank: int) -> _Tool | None:
        tools = [t for t in self.tools.get(kind, ()) if t.rank == rank]
        best = min(tools, key=lambda t: self.cost.get(t.item, INF), default=None)
        return best if best and self.cost.get(best.item, INF) < INF else None

    def policy_ranks(self, kind: str) -> list[int]:
        ranks = {t.rank for t in self.tools.get(kind, ())}
        return sorted(
            r for r in ranks if r <= MAX_POLICY_RANK and self.ceiling_tool(kind, r)
        )

    def upstream(self, tool_item: str) -> frozenset[str]:
        """Items the tool is (transitively) built from, tools for them included."""
        if tool_item not in self._upstream:
            saved, self.policy = self.policy, {}
            try:
                seen: set[str] = set()
                stack = [tool_item]
                while stack:
                    i = stack.pop()
                    if i not in seen:
                        seen.add(i)
                        stack.extend(_children(self, i, self.best))
            finally:
                self.policy = saved
            self._upstream[tool_item] = frozenset(seen)
        return self._upstream[tool_item]

    def policy_tool(self, g: _Gat, item: str | None) -> _Tool | None:
        """The ceiling tool the tool-progression pass fixed for this gather, if any.
        Gathers of items the tool itself is made from keep the lowest-tier logic."""
        if not self.policy or item is None or g.need_tool or not g.kind:
            return None
        ceiling = self.policy.get(g.kind)
        if ceiling is None or g.rank > ceiling:
            return None
        tool = self.ceiling_tool(g.kind, ceiling)
        if tool is None or item in self.upstream(tool.item):
            return None
        return tool

    def candidates(
        self, g: _Gat, cost: Mapping[str, float] | None = None, item: str | None = None
    ) -> list[_Tool | None]:
        """Tools worth considering: bare hand, held tools, and the cheapest tool of
        exactly the tier the drop needs (a higher tier would be built from this very
        tier). Only one craftable tool, so the dependency graph stays acyclic. With a
        tool-progression policy the policy's tool replaces the craftable one."""
        cost = self.cost if cost is None else cost
        pol = self.policy_tool(g, item)
        out: list[_Tool | None] = [] if g.need_tool else ([None] if g.rank == 0 else [])
        made: list[_Tool] = []
        for t in self.tools.get(g.kind, ()) if g.kind else ():
            if self.inv.get(t.item, 0) > 0 and t.rank >= g.rank:
                out.append(t)
            elif pol is None and ((g.rank > 0 and t.rank == g.rank) or g.need_tool):
                made.append(t)
        if pol is not None:
            if pol not in out:
                out.append(pol)
        elif made and cost is not None:
            out.append(min(made, key=lambda t: cost.get(t.item, INF)))
        return out

    def material_of(self, tool: _Tool, item: str) -> int:
        """Units of `item` one craft of `tool` consumes directly."""
        for r in self.recs.get(tool.item, ()):
            if r.cook is None and (mat := sum(n for o, n in r.ing if item in o)):
                return mat
        return 0

    def transform_tool(self, default: str, tr: TransformDef, item: str) -> _Tool:
        """Tool for a transform: the policy's ceiling tool when one is set, the
        output is not part of it and no tool of the kind is held; else `default`."""
        tool = self.tool_by_item[default]
        ceiling = self.policy.get(tr.tool)
        if ceiling is None or self.inv.get(default, 0):
            return tool
        pol = self.ceiling_tool(tr.tool, ceiling)
        if pol is None or item in self.upstream(pol.item):
            return tool
        return pol

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
        for mob in self.mob_srcs.get(item, ()):
            known = KNOWN_COST_FACTOR if mob.known else 1.0
            ticks = (
                HUNT_TICKS * self.weapon_factor if mob.kind == "hunt" else SHEAR_TICKS
            )
            c = ticks + mob.search / SEARCH_BATCH
            if mob.kind == "shear":
                tool = self.pick_tool(mob.tool_kind or "", cost)
                if tool is None:
                    continue
                held = self.inv.get(tool.item, 0)
                c += 0.0 if held else cost.get(tool.item, INF) / tool.dur
                out.append((c * known / mob.yield_, ("shear", mob, tool.item)))
            else:
                out.append((c * known / mob.yield_, ("hunt", mob)))
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
    if opt[0] == "hunt":
        return []
    if opt[0] == "shear":
        return [opt[2]]
    if opt[0] == "gather":
        return [t.item for t in m.candidates(opt[1], item=item) if t]
    if opt[0] == "transform":
        return [opt[1].input, m.transform_tool(opt[2], opt[1], item).item]
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

    def __init__(
        self,
        m: _Model,
        targets: Mapping[str, int],
        tool_make: Mapping[str, int] | None = None,
    ) -> None:
        self.m = m
        self.need: dict[str, int] = {i: n for i, n in targets.items() if n > 0}
        self.tool_make = dict(tool_make or {})
        self.targets = frozenset(self.need)
        for tool, n in self.tool_make.items():
            # a tool that is also a target serves both: one craft, not two
            self.need[tool] = (
                max(self.need[tool], n)
                if tool in self.targets
                else self.need.get(tool, 0) + n
            )
        self.have = dict(m.inv)
        self.best = dict(m.best)
        self.uses: Counter[str] = Counter()
        self.tasks: dict[str, list[Task]] = {}
        self.deps: dict[str, set[str]] = {}
        self.missing: list[Missing] = []
        self.stations: set[str] = set()
        self.surplus: Counter[str] = Counter()
        self.total = 0.0  # estimated ticks of work in the plan

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

    # tools: uses are summed over the whole plan; the number of tools to craft is
    # settled by re-running with `tool_make` until it covers the uses (see _expand)
    def make_for(self, tool: _Tool, uses: int) -> int:
        return max(0, math.ceil((uses - self.m.held_uses(tool)) / tool.dur))

    def required_make(self) -> dict[str, int]:
        return {
            i: n
            for i, u in self.uses.items()
            if (n := self.make_for(self.m.tool_by_item[i], u)) > 0
        }

    def _use_tool(self, tool: _Tool, uses: int, d: set[str]) -> None:
        self.uses[tool.item] += uses
        if tool.item not in self.targets:  # a held target tool still serves as target
            self.have[tool.item] = 0
        d.add(tool.item)

    def _station(self, rec: _Rec, d: set[str]) -> None:
        if rec.station == "inventory" or rec.station in self.m.known:
            return
        station_item = f"minecraft:{rec.station}"
        if rec.station not in self.stations:
            self.stations.add(rec.station)
            self.total += PLACE_STATION_TICKS
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
            before = self.uses[tool.item]
            made = self.make_for(tool, before + blocks) - self.make_for(tool, before)
            total += made * self.m.cost.get(tool.item, INF) if made else 0.0
        return total

    def _choose(self, g: _Gat, item: str, blocks: int) -> _Tool | None | bool:
        options = [
            (self._gather_cost(g, t, blocks), i, t)
            for i, t in enumerate(self.m.candidates(g, item=item))
        ]
        options = [o for o in options if o[0] < INF]
        return min(options)[2] if options else False

    def _bootstrap(self, g: _Gat, item: str, n: int) -> tuple[_Tool, int] | None:
        """For a gather of the very material the policy's tool is crafted from
        (cobblestone for a stone pickaxe): (tool, blocks to mine with the lower tier
        first). The rest is mined with the tool, after it is crafted."""
        m = self.m
        ceiling = m.policy.get(g.kind or "")
        if ceiling is None or g.rank > ceiling or g.need_tool:
            return None
        tool = m.ceiling_tool(g.kind or "", ceiling)
        if tool is None or m.inv.get(tool.item, 0) or item not in m.upstream(tool.item):
            return None
        material = m.material_of(tool, item)
        if not material:
            return None
        return tool, min(n, material * self.tool_make.get(tool.item, 0))

    def _gather(self, item: str, n: int, opt: Option, d: set[str]) -> None:
        g: _Gat = opt[1]
        split = self._bootstrap(g, item, n) if self.m.policy else None
        if split is None or n <= split[1]:
            self._mine(item, n, g, d, item)
            return
        tool, first = split
        if first > 0:
            self._mine(item, first, g, d, item)
        rest = n - first
        key = item + REST
        blocks = math.ceil(rest / g.yield_ - EPS) + g.margin
        self.deps[key] = {tool.item}
        self._use_tool(tool, blocks, self.deps[key])
        self.total += blocks * self.m.block_cost(g, tool)
        self.tasks[key] = [
            Equip(tool.item),
            self._gather_task(g, item, rest, blocks, tool),
        ]

    def _gather_task(
        self, g: _Gat, item: str, n: int, blocks: int, tool: _Tool | None
    ) -> Gather:
        return Gather(
            g.block,
            blocks,
            g.kind if tool else None,
            self.m.tier_name(g.rank),
            item,
            n,
            g.margin,
            g.yield_,
        )

    def _mine(self, item: str, n: int, g: _Gat, d: set[str], key: str) -> None:
        blocks = math.ceil(n / g.yield_ - EPS) + g.margin
        tool = self._choose(g, item, blocks)
        if tool is False:
            self.missing.append(Missing(item, self.m.reason(item)))
            return
        extra = int(blocks * g.yield_ + EPS) - n
        if extra > 0:
            self.surplus[item] += extra
        mine = self._gather_task(g, item, n, blocks, tool)
        self.total += blocks * self.m.block_cost(g, tool)
        if tool is None:
            self.tasks[key] = [mine]
            return
        self._use_tool(tool, blocks, d)
        self.tasks[key] = [Equip(tool.item), mine]

    # mobs
    def _animals(self, mob: _Mob, n: int) -> int:
        animals = math.ceil(n / mob.yield_ - EPS) + mob.margin
        extra = int(animals * mob.yield_ + EPS) - n
        if extra > 0:
            self.surplus[mob.item] += extra
        return animals

    def _hunt(self, item: str, n: int, opt: Option, d: set[str]) -> None:
        mob: _Mob = opt[1]
        animals = self._animals(mob, n)
        weapon = self.m.weapon
        self.total += (
            animals
            * (HUNT_TICKS * self.m.weapon_factor + mob.search / SEARCH_BATCH)
            * (KNOWN_COST_FACTOR if mob.known else 1.0)
        )
        hunt = Hunt(mob.entity, animals, item, n, weapon, mob.yield_, mob.margin)
        self.tasks[item] = [Equip(weapon), hunt] if weapon else [hunt]

    def _shear(self, item: str, n: int, opt: Option, d: set[str]) -> None:
        mob: _Mob = opt[1]
        tool = self.m.tool_by_item[opt[2]]
        animals = self._animals(mob, n)
        self.total += (
            animals
            * (SHEAR_TICKS + mob.search / SEARCH_BATCH)
            * (KNOWN_COST_FACTOR if mob.known else 1.0)
        )
        self._use_tool(tool, animals, d)
        self.tasks[item] = [
            Equip(tool.item),
            Shear(mob.entity, animals, item, n, mob.yield_, mob.margin),
        ]

    # transforming
    def _transform(self, item: str, n: int, opt: Option, d: set[str]) -> None:
        tr: TransformDef = opt[1]
        tool = self.m.transform_tool(opt[2], tr, item)
        times = math.ceil(n / tr.count - EPS)
        if times * tr.count > n:
            self.surplus[item] += times * tr.count - n
        self.need[tr.input] = self.need.get(tr.input, 0) + times
        self.total += times * TRANSFORM_OVERHEAD
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
        self.total += times * CRAFT_OVERHEAD
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
        self.total += times * (CRAFT_OVERHEAD + (rec.cook or 200) * SMELT_WAIT_FACTOR)
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


def food_needed(estimated_ticks: float, current_food: float) -> int:
    """Hunger points to buy for a job of `estimated_ticks`: the job uses about one
    point per FOOD_TICKS_PER_POINT ticks (working and sprinting), and the bot should
    stay above FOOD_FLOOR, so the current food above the floor is spent first."""
    use = estimated_ticks / FOOD_TICKS_PER_POINT
    return max(0, math.ceil(use - max(0.0, current_food - FOOD_FLOOR) - EPS))


def _add_food(
    m: _Model, targets: dict[str, int], food_target: int, held: Mapping[str, int]
) -> Missing | None:
    """Add the food item with the lowest cost per hunger point to `targets`."""
    foods = m.k.foods
    if not foods:
        return Missing("food", "no food table")
    need = food_target - sum(
        n * foods[i]["nutrition"] for i, n in held.items() if i in foods
    )
    if need <= 0:
        return None
    options = [
        (m.cost_n[i] / f["nutrition"], i)
        for i, f in foods.items()
        if m.cost_n.get(i, INF) < INF
    ]
    if not options:
        return Missing("food", "no food source")
    item = min(options)[1]
    count = math.ceil(need / foods[item]["nutrition"] - EPS)
    targets[item] = targets.get(item, 0) + held.get(item, 0) + count
    return None


Expansion = tuple[_Expander, list[Task]]


def _expand(
    m: _Model,
    targets: Mapping[str, int],
    policy: dict[str, int],
) -> Expansion:
    m.policy = policy
    make: dict[str, int] = {}
    for _ in range(MAX_TOOL_PASSES):
        ex = _Expander(m, targets, make)
        ex.run()
        required = ex.required_make()
        if all(n <= make.get(t, 0) for t, n in required.items()):
            break
        make = {t: max(make.get(t, 0), required.get(t, 0)) for t in make | required}
    return ex, _insert_stations(_schedule(ex.tasks, ex.deps), ex.stations)


def _best_expansion(m: _Model, targets: Mapping[str, int]) -> Expansion:
    """Tool progression (G2/G3): expand once with the per-item tool logic, then try
    a tier ceiling per tool kind (all gathers of that kind that the ceiling tool is
    not itself built from use it) and keep a ceiling when the whole plan is cheaper
    across ALL block types. Coordinate descent over the kinds, most mined first."""
    best = _expand(m, targets, {})
    policy: dict[str, int] = {}
    mined: Counter[str] = Counter()
    for t in best[1]:
        if isinstance(t, Gather) and t.tool_kind and t.tool_kind != "shears":
            mined[t.tool_kind] += t.count
        elif isinstance(t, Transform):
            mined[t.tool_kind] += t.count
    for kind, _ in mined.most_common():
        for rank in m.policy_ranks(kind):
            trial = {**policy, kind: rank}
            cand = _expand(m, targets, trial)
            if (len(cand[0].missing), cand[0].total) < (
                len(best[0].missing),
                best[0].total - 1e-6,
            ):
                best, policy = cand, trial
    return best


def plan(
    targets: Mapping[str, int],
    inventory: Inventory,
    knowledge: Knowledge,
    stations: Mapping[str, object] | None = None,
    *,
    executable_stations: Collection[str] = DEFAULT_STATIONS,
    known_blocks: Collection[str] = (),
    prefer: Mapping[str, float] | None = None,
    food_target: int | None = None,
    storage: Iterable[Mapping[str, Any]] | None = None,
    with_chest: bool = True,
    spare_chest: bool = False,
    starter_tools: bool = False,
) -> PlanResult:
    """Ordered tasks that turn `inventory` into `targets`; unreachable parts are
    reported in `missing` instead of raising.

    `inventory` is `{item: count}` or the body's observe list of
    `{item, count, damage, max_damage}` (remaining durability is used).
    `prefer` maps item ids to cost multipliers (below 1 favours the item); it
    defaults to DEFAULT_PREFER (oak). `known_blocks` are block ids and entity ids
    seen nearby: they are gatherable and cheap to find.
    `food_target` is a number of hunger points to have on top of the targets: the
    cheapest food (hunt, then smelt the meat) is added. `storage` lists known
    containers `{"kind", "pos", "contents"}`; their contents count as available
    behind a Withdraw. (`with_chest` and `spare_chest` are internal: they stop the
    chest-first retry and reserve one carried chest for storage.) When the plan
    would exceed the inventory, gathers are batched with Deposits and a container
    is placed (see gather_storage). `starter_tools` follows the player method for a
    stone pickaxe target: a wooden axe, pickaxe and shovel first (while no stone
    pickaxe is held), and a stone axe and shovel with the stone pickaxe."""
    counts, wear = normalize_inventory(inventory)
    boxes = [dict(b) for b in storage or ()]
    planned = Counter(counts)
    if spare_chest:  # one carried chest is kept back to be placed as storage
        planned[CHEST] -= 1
    reachable = +planned + container_contents(boxes)
    m = _Model(
        knowledge,
        reachable,
        wear,
        stations or {},
        executable_stations,
        known_blocks,
        DEFAULT_PREFER if prefer is None else prefer,
    )
    goal = {i: n for i, n in targets.items() if n > 0}
    if starter_tools and STONE_PICKAXE in goal:
        goal = _with_starter_tools(goal, counts)
    food_gap = (
        _add_food(m, goal, food_target, reachable)
        if food_target and food_target > 0
        else None
    )
    ex, ordered = _best_expansion(m, goal)
    done = add_storage(ordered, counts, boxes, knowledge, goal)
    if done.needs_chest and with_chest:
        return _chest_first(
            targets, inventory, knowledge, stations, boxes, food_target,
            executable_stations, known_blocks, prefer, starter_tools,
        )  # fmt: skip
    missing = (*ex.missing, *([food_gap] if food_gap else ()))
    return PlanResult(done.tasks, missing, ex.total, dict(ex.surplus), done.stored)


STONE_PICKAXE = "minecraft:stone_pickaxe"
# The wooden pickaxe is not listed: mining stone makes the planner craft it already.
STARTER_WOODEN = ("minecraft:wooden_axe", "minecraft:wooden_shovel")
STARTER_STONE = ("minecraft:stone_axe", "minecraft:stone_shovel")
ADVANCED_PICKAXES = tuple(
    f"minecraft:{t}_pickaxe" for t in ("iron", "diamond", "netherite")
)


def _with_starter_tools(
    goal: dict[str, int], held: Mapping[str, int]
) -> dict[str, int]:
    extra = list(STARTER_STONE)
    # stays in the plan while a stone pickaxe is held: the schedule may craft it first
    if not any(held.get(p, 0) > 0 for p in ADVANCED_PICKAXES):
        extra += STARTER_WOODEN
    # held tools stay out of the goal: the planner's tool logic re-crafts a held tool
    # that is also a target
    return {**{i: 1 for i in extra if not held.get(i, 0)}, **goal}


def _chest_first(
    targets,
    inventory,
    knowledge,
    stations,
    boxes,
    food_target,
    executable,
    known,
    prefer,
    starter_tools=False,
) -> PlanResult:
    """No container to deposit into: plan a chest on its own first, replay it to get
    the inventory and stations it leaves behind, and plan the job from there with
    the chest in hand. (A chest inside the main plan would be crafted only after
    the bulk crafts that need the space.)"""
    kw: dict[str, Any] = {
        "executable_stations": executable,
        "known_blocks": known,
        "prefer": prefer,
        "storage": boxes,
        "with_chest": False,
        "starter_tools": starter_tools,
    }
    pre = plan({CHEST: 1}, inventory, knowledge, stations, **kw)
    replay = Replay(knowledge, inventory, stations, boxes)
    for i, t in enumerate(pre.tasks):
        replay.step(i, t)
    placed = {s: True for s in replay.placed if s != "inventory"}
    after = [
        {**b, "contents": dict(replay.boxes.get(_box_key(b), b.get("contents") or {}))}
        for b in boxes
    ]
    main = plan(
        targets,
        replay.snapshot(),
        knowledge,
        placed,
        **{**kw, "storage": after, "spare_chest": True},
        food_target=food_target,
    )
    return PlanResult(
        (*pre.tasks, *main.tasks),
        (*pre.missing, *main.missing),
        pre.estimated_cost + main.estimated_cost,
        main.surplus,
        main.stored,
    )


def _box_key(box: Mapping[str, Any]) -> Any:
    return tuple(box["pos"]) if box.get("pos") is not None else "placed"


def _schedule(tasks: dict[str, list[Task]], deps: dict[str, set[str]]) -> list[Task]:
    """Dependencies first; among ready items keep the current group (gather, one
    station) together, otherwise take the cheapest group first."""

    def needs(i: str) -> set[str]:  # transitive dependencies of i
        seen: set[str] = set()
        todo = list(deps.get(i, ()))
        while todo:
            c = todo.pop()
            if c not in seen:
                seen.add(c)
                todo.extend(deps.get(c, ()))
        return seen

    def waiting(i: str, c: str) -> bool:
        """Is `i` still waiting for `c`? A split gather `c` is only complete when
        its second half is done, except for the tool crafted in between."""
        if c in tasks and c not in done:
            return True
        r = c + REST
        return r in tasks and r not in done and i not in needs(r)

    remaining = list(reversed(tasks))
    done: set[str] = set()
    out: list[Task] = []
    last = None
    while remaining:
        ready = [
            i for i in remaining if not any(waiting(i, c) for c in deps.get(i, ()))
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
