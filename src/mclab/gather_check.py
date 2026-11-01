"""Validation and reporting for gathering plans: `check_order` replays a task list
(with tool wear, storage and the 36-slot inventory); `summarize` prints a plan."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from typing import Any

from mclab.gather_tasks import (
    INVENTORY_SLOTS,
    TIER_RANK,
    UNKNOWN_TIER_RANK,
    Craft,
    Deposit,
    Equip,
    Gather,
    Hunt,
    Inventory,
    PlaceContainer,
    PlaceStation,
    PlanResult,
    Shear,
    Smelt,
    Task,
    Transform,
    Withdraw,
    normalize_inventory,
    slots_used,
)
from mclab.knowledge import Knowledge

HITS_PER_KILL = 3  # weapon uses per animal killed (vanilla sword on a cow: 2 to 4)
PLACED = "placed"  # key of the container placed by the plan


def _pos_key(pos: Any) -> Any:
    return tuple(pos) if pos is not None else PLACED


def credited(task: Gather | Hunt | Shear) -> int:
    """Items check_order credits: the plan's expected yield with the margin spent."""
    got = math.floor(max(0, task.count - task.margin) * task.expected + 1e-9)
    return min(task.item_count, got) if task.item_count else 0


class Replay:
    def __init__(
        self,
        k: Knowledge,
        inventory: Inventory,
        stations: Mapping[str, object] | None,
        storage: Iterable[Mapping[str, Any]] | None,
    ) -> None:
        self.k = k
        counts, wear = normalize_inventory(inventory)
        self.inv: Counter[str] = Counter(counts)
        self.pieces: dict[str, list[int]] = {}
        for item, n in counts.items():
            if dur := self.dur(item):
                known = wear.get(item, [])
                self.pieces[item] = list(known) + [dur] * (n - len(known))
        self.placed = {"inventory"} | {s for s, w in (stations or {}).items() if w}
        self.boxes: dict[Any, Counter[str]] = {}
        for box in storage or ():
            contents, _ = normalize_inventory(box.get("contents"))
            self.boxes[_pos_key(box.get("pos"))] = Counter(contents)
        self.ranks = {
            n: i for i, n in enumerate(t for t in k.drop_tiers if t != "silk_touch")
        }
        self.equipped: str | None = None
        self.problems: list[str] = []

    def snapshot(self) -> list[dict[str, Any]]:
        """The inventory as the body's observe list (damage per tool piece)."""
        out: list[dict[str, Any]] = []
        for item, n in self.inv.items():
            left = self.pieces.get(item)
            if n <= 0:
                continue
            if left and len(left) == n:
                dur = self.dur(item)
                out += [
                    {"item": item, "count": 1, "damage": dur - u, "max_damage": dur}
                    for u in left
                ]
            else:
                out.append({"item": item, "count": n})
        return out

    def dur(self, item: str) -> int:
        return int(self.k.items.get(item, {}).get("durability") or 0)

    def bad(self, step: int, task: Task, msg: str) -> None:
        self.problems.append(f"step {step} {task}: {msg}")

    def add(self, item: str, n: int) -> None:
        self.inv[item] += n
        if d := self.dur(item):
            self.pieces.setdefault(item, []).extend([d] * n)

    def take(self, step: int, task: Task, item: str, n: int) -> None:
        if self.inv[item] < n:
            self.bad(step, task, f"needs {n} {item}, has {self.inv[item]}")
        n = min(n, self.inv[item])
        self.inv[item] -= n
        if n and item in self.pieces:
            del self.pieces[item][len(self.pieces[item]) - n :]

    def use(
        self, step: int, task: Task, item: str, uses: int, strict: bool = True
    ) -> None:
        pieces = self.pieces.setdefault(item, [])
        while uses > 0:
            if not pieces:
                if strict:
                    self.bad(step, task, f"{item} broke")
                return
            d = min(pieces[0], uses)
            pieces[0] -= d
            uses -= d
            if pieces[0] <= 0:
                pieces.pop(0)
                self.inv[item] -= 1

    def tools(self, kind: str, tier: str) -> list[str]:
        out = []
        for item, n in self.inv.items():
            tool = self.k.items.get(item, {}).get("tool")
            if n > 0 and tool and tool["kind"] == kind and self.pieces.get(item):
                rank = TIER_RANK.get(tool.get("tier", ""), UNKNOWN_TIER_RANK)
                if kind == "shears" or rank >= self.ranks.get(tier, 0):
                    out.append((rank, item))
        return [i for _, i in sorted(out)]

    def use_tool(self, step: int, task: Task, kind: str, tier: str, uses: int) -> None:
        held = self.tools(kind, tier)
        if not held:
            self.bad(step, task, f"no {kind} held")
            return
        self.use(step, task, self.equipped if self.equipped in held else held[0], uses)

    def station(self, step: int, task: Task, station: str) -> None:
        if station not in self.placed:
            self.bad(step, task, f"{station} not placed")

    def box(self, step: int, task: Deposit | Withdraw) -> Counter[str] | None:
        key = _pos_key(task.pos)
        if key not in self.boxes:
            self.bad(step, task, "no container there")
            return None
        return self.boxes[key]

    def step(self, i: int, t: Task) -> None:
        if isinstance(t, Equip):
            if self.inv[t.item] > 0:
                self.equipped = t.item
            else:
                self.bad(i, t, "not held")
        elif isinstance(t, PlaceStation):
            self.take(i, t, f"minecraft:{t.kind}", 1)
            self.placed.add(t.kind)
        elif isinstance(t, Gather):
            if t.tool_kind:
                self.use_tool(i, t, t.tool_kind, t.min_tier, t.count)
            self.add(t.item, credited(t))
        elif isinstance(t, Hunt):
            if t.weapon and self.inv[t.weapon] <= 0:
                self.bad(i, t, f"weapon {t.weapon} not held")
            elif t.weapon:
                self.use(i, t, t.weapon, t.count * HITS_PER_KILL, strict=False)
            self.add(t.item, credited(t))
        elif isinstance(t, Shear):
            self.use_tool(i, t, "shears", "hand", t.count)
            self.add(t.item, credited(t))
        elif isinstance(t, Craft):
            self.station(i, t, t.station)
            for item, n in t.ingredients:
                self.take(i, t, item, n)
            self.add(t.result, t.result_count)
        elif isinstance(t, Smelt):
            self.station(i, t, t.station)
            self.take(i, t, t.ingredient, t.count)
            self.take(i, t, t.fuel, t.fuel_count)
            self.add(t.result, t.result_count)
        elif isinstance(t, Transform):
            self.use_tool(i, t, t.tool_kind, "hand", t.count)
            self.take(i, t, t.input, t.count)
            self.add(t.output, t.result_count)
        elif isinstance(t, PlaceContainer):
            self.take(i, t, f"minecraft:{t.kind}", 1)
            self.boxes.setdefault(PLACED, Counter())
        elif isinstance(t, Deposit):
            if (box := self.box(i, t)) is not None:
                for item, n in t.items:
                    self.take(i, t, item, n)
                    box[item] += n
        elif isinstance(t, Withdraw) and (box := self.box(i, t)) is not None:
            for item, n in t.items:
                if box[item] < n:
                    self.bad(i, t, f"container has {box[item]} {item}, needs {n}")
                n = min(n, box[item])
                box[item] -= n
                self.add(item, n)
        used = slots_used(self.inv, self.k.items)
        if used > INVENTORY_SLOTS:
            self.bad(i, t, f"inventory over capacity ({used} slots)")


def check_order(
    tasks: Collection[Task],
    inventory: Inventory,
    knowledge: Knowledge,
    stations: Mapping[str, object] | None = None,
    storage: Iterable[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Replay `tasks` from `inventory`; return one message per step that could not
    run (missing or worn-out tool, ingredient, fuel, station, container, or an
    inventory over 36 slots). Gathers credit their expected yield minus the planned
    margin blocks. `inventory` is `{item: count}` or the body's observe list with
    `damage`/`max_damage`. Empty result means the order is valid."""
    r = Replay(knowledge, inventory, stations, storage)
    for i, t in enumerate(tasks):
        r.step(i, t)
    return r.problems


def summarize(result: PlanResult) -> str:
    """Short human-readable account of a plan: gathers, hunts, crafts, storage, gaps."""
    gathers: Counter[str] = Counter()
    crafts: Counter[str] = Counter()
    hunts: Counter[str] = Counter()
    shears: Counter[str] = Counter()
    tools: list[str] = []
    other: Counter[str] = Counter()
    for t in result.tasks:
        if isinstance(t, Gather):
            gathers[t.block] += t.count
        elif isinstance(t, Hunt):
            hunts[t.entity_type] += t.count
        elif isinstance(t, Shear):
            shears[t.entity_type] += t.count
        elif isinstance(t, Craft | Smelt):
            crafts[t.result] += t.result_count
        elif isinstance(t, Transform):
            crafts[t.output] += t.result_count
        elif isinstance(t, Equip) and t.item not in tools:
            tools.append(t.item)
        elif isinstance(t, PlaceContainer | Deposit | Withdraw):
            other[type(t).__name__] += 1

    def short(i: str) -> str:
        return i.removeprefix("minecraft:")

    def fmt(c: Mapping[str, int]) -> str:
        return ", ".join(f"{short(b)} x{n}" for b, n in c.items())

    lines = [f"{len(result.tasks)} tasks, estimated cost {result.estimated_cost:.0f}"]
    lines.append("gather: " + fmt(gathers))
    lines.append("hunt:   " + fmt(hunts))
    lines.append("shear:  " + fmt(shears))
    lines.append("tools:  " + ", ".join(short(t) for t in tools))
    lines.append("make:   " + fmt(crafts))
    lines.append("storage: " + ", ".join(f"{k} x{n}" for k, n in other.items()))
    if result.stored:
        lines.append("left in containers: " + fmt(result.stored))
    lines.append(
        "missing: " + ", ".join(f"{short(m.item)} ({m.reason})" for m in result.missing)
    )
    return "\n".join(lines)
