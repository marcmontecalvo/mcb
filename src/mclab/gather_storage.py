"""Storage pass of the gathering planner (G5): keeps the plan inside the 36-slot
inventory by gathering in batches and depositing into a container.

Greedy and simple, on purpose. Limits:
- one container is used for deposits (a known chest, else a placed shulker box or
  chest); capacity of the container is not modelled;
- on every deposit it empties everything not needed before the next gathering task
  (it may re-withdraw later; that costs trips, not correctness);
- a stack of one task is never split below one item, and tools are never deposited;
- planned gross yields beyond `item_count` (spare drops) are not tracked.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from mclab.gather_tasks import (
    INVENTORY_SLOTS,
    RESERVED_FREE_SLOTS,
    Craft,
    Deposit,
    Gather,
    Hunt,
    PlaceContainer,
    PlaceStation,
    Shear,
    Smelt,
    Task,
    Transform,
    Withdraw,
    item_stack,
    normalize_inventory,
    slots_used,
)
from mclab.knowledge import Knowledge

SLOT_LIMIT = INVENTORY_SLOTS - RESERVED_FREE_SLOTS
EPS = 1e-9
CHEST = "minecraft:chest"
SHULKER = "minecraft:shulker_box"


@dataclass(frozen=True)
class StorageResult:
    tasks: tuple[Task, ...]
    stored: Mapping[str, int]  # contents of all containers at the end of the plan
    needs_chest: bool  # a deposit was needed but no container could be had


def _needs(t: Task) -> Counter[str]:
    c: Counter[str] = Counter()
    if isinstance(t, Craft):
        c.update(dict(t.ingredients))
    elif isinstance(t, Smelt):
        c[t.ingredient] += t.count
        c[t.fuel] += t.fuel_count
    elif isinstance(t, Transform):
        c[t.input] += t.count
    elif isinstance(t, PlaceStation):
        c[f"minecraft:{t.kind}"] += 1
    return c


def _produces(t: Task) -> Counter[str]:
    if isinstance(t, Gather | Hunt | Shear):
        return Counter({t.item: t.item_count}) if t.item_count else Counter()
    if isinstance(t, Craft | Smelt):
        return Counter({t.result: t.result_count})
    if isinstance(t, Transform):
        return Counter({t.output: t.result_count})
    return Counter()


def _slice(total: int, done: int, n: int, whole: int) -> int:
    """Share of `total` for items done+1..done+n of `whole` (shares sum to total)."""
    return total * (done + n) // whole - total * done // whole


def _times(t: Task) -> int:
    if isinstance(t, Craft):
        return t.times
    return t.count if isinstance(t, Smelt | Transform) else 1


def _part(t: Task, done: int, n: int) -> Task:
    """The batch of `n` crafts/smelts/transforms after `done` of them."""
    whole = _times(t)
    if isinstance(t, Craft):
        ing = tuple((i, _slice(c, done, n, whole)) for i, c in t.ingredients)
        return replace(
            t,
            times=n,
            ingredients=ing,
            result_count=_slice(t.result_count, done, n, whole),
        )
    if isinstance(t, Smelt):
        return replace(
            t,
            count=n,
            fuel_count=_slice(t.fuel_count, done, n, whole),
            result_count=_slice(t.result_count, done, n, whole),
        )
    assert isinstance(t, Transform)
    return replace(t, count=n, result_count=_slice(t.result_count, done, n, whole))


class _Box:
    def __init__(self, pos: tuple[int, int, int] | None, contents: Mapping[str, int]):
        self.pos = pos
        self.contents: Counter[str] = Counter(contents)
        self.initial: Counter[str] = Counter(contents)


class _Storage:
    def __init__(
        self,
        tasks: Sequence[Task],
        held: Mapping[str, int],
        boxes: Iterable[Mapping[str, Any]],
        k: Knowledge,
        targets: Mapping[str, int],
    ) -> None:
        self.targets = targets
        self.tasks = tasks
        self.k = k
        self.inv: Counter[str] = Counter(held)
        self.boxes: list[_Box] = []
        for b in boxes:
            pos = tuple(b["pos"]) if b.get("pos") is not None else None
            contents, _ = normalize_inventory(b.get("contents"))
            self.boxes.append(_Box(pos, contents))
        self.placed: _Box | None = None
        self.target: _Box | None = None
        self.out: list[Task] = []
        self.needs_chest = False
        self.at = 0  # index of the task being handled

    # ---- measures
    def slots(self, extra: Mapping[str, int] | None = None) -> int:
        total = Counter(self.inv)
        total.update(extra or {})
        return slots_used(total, self.k.items)

    def capacity(self, item: str) -> int:
        """Items of `item` that still fit while staying within the slot limit."""
        have = self.inv[item]
        stack = item_stack(self.k.items, item)
        others = self.slots() - math.ceil(have / stack)
        return max(0, (SLOT_LIMIT - others) * stack - have)

    def soon(self, i: int) -> Counter[str]:
        """Items consumed from task i up to (not including) the next gatherer."""
        need: Counter[str] = Counter()
        for j in range(i, len(self.tasks)):
            if j > i and isinstance(self.tasks[j], Gather | Hunt | Shear):
                break
            need.update(_needs(self.tasks[j]))
        return need

    def later_need(self, item: str) -> int:
        """Units of `item` that must be on hand at some point from the current task
        on, counting what later tasks produce before they consume it."""
        running = reserve = 0
        for t in self.tasks[self.at :]:
            reserve = max(reserve, running + _needs(t)[item])
            running += _needs(t)[item] - _produces(t)[item]
        return reserve

    def is_depositable(self, item: str) -> bool:
        info = self.k.items.get(item, {})
        return not (info.get("tool") or info.get("durability"))

    # ---- container
    def container(self) -> _Box | None:
        """The box deposits go to; sets it up (withdraw/place) on first use."""
        if self.target is not None:
            return self.target
        if self.inv[SHULKER] > 0:
            self.place(SHULKER)
        elif src := next((b for b in self.boxes if b.contents[SHULKER] > 0), None):
            self.withdraw_from(src, Counter({SHULKER: 1}))
            self.place(SHULKER)
        elif self.boxes:
            self.target = self.boxes[0]
        elif self.inv[CHEST] > self.later_need(CHEST):
            self.place(CHEST)
        else:
            self.needs_chest = True
            self.place_missing()
        return self.target

    def place(self, item: str) -> None:
        self.out.append(PlaceContainer(item.removeprefix("minecraft:")))
        self.inv[item] -= 1
        self.placed = self.target = _Box(None, {})

    def place_missing(self) -> None:
        self.target = self.placed = _Box(None, {})  # a deposit with nothing to place

    def withdraw_from(self, box: _Box, items: Counter[str]) -> None:
        items = Counter({i: min(n, box.contents[i]) for i, n in items.items()})
        items = +items
        if not items:
            return
        self.out.append(Withdraw(tuple(sorted(items.items())), box.pos))
        box.contents.subtract(items)
        self.inv.update(items)

    def all_boxes(self) -> list[_Box]:
        return self.boxes + ([self.placed] if self.placed else [])

    # ---- moves
    def deposit(self, keep: Mapping[str, int]) -> bool:
        if not any(
            n > keep.get(i, 0) and self.is_depositable(i) for i, n in self.inv.items()
        ):
            return False
        box = self.container()  # may place a container, which changes the inventory
        moving: Counter[str] = Counter()
        for item, n in self.inv.items():
            spare = n - keep.get(item, 0)
            if spare > 0 and self.is_depositable(item):
                moving[item] = spare
        if box is None or not moving:
            return False
        self.out.append(Deposit(tuple(sorted(moving.items())), box.pos))
        self.inv.subtract(moving)
        box.contents.update(moving)
        return True

    def fetch(self, i: int, t: Task) -> None:
        """Withdraw what task `t` consumes and is not carried."""
        short = Counter({x: n - self.inv[x] for x, n in _needs(t).items()})
        short = +short
        wanted: dict[int, Counter[str]] = {}
        for item, n in short.items():
            for bi, box in enumerate(self.all_boxes()):
                take = min(n, box.contents[item])
                if take > 0:
                    wanted.setdefault(bi, Counter())[item] += take
                    n -= take
        total = Counter()
        for c in wanted.values():
            total.update(c)
        if not total:
            return
        if self.slots(total) > SLOT_LIMIT:
            self.deposit({x: n for x, n in self.soon(i).items()} | dict(total))
        boxes = self.all_boxes()
        for bi, c in wanted.items():
            self.withdraw_from(boxes[bi], c)

    def apply(self, t: Task) -> None:
        self.inv.subtract(_needs(t))
        self.inv.update(_produces(t))

    def fits(self, t: Task) -> bool:
        """Does task `t` keep the inventory within the limit (fetching what it
        needs first, then consuming and producing)?"""
        need = _needs(t)
        inv = Counter(self.inv)
        for item, n in need.items():
            inv[item] = max(inv[item], n)
        if slots_used(inv, self.k.items) > SLOT_LIMIT:
            return False
        inv.subtract(need)
        inv.update(_produces(t))
        return slots_used(inv, self.k.items) <= SLOT_LIMIT

    def batched(self, i: int, t: Task) -> None:
        """Run a craft/smelt/transform in the largest batches that fit."""
        whole, done = _times(t), 0
        while done < whole:
            lo, hi = 1, whole - done
            while lo < hi:  # largest batch that fits (1 if even that does not)
                mid = (lo + hi + 1) // 2
                if self.fits(_part(t, done, mid)):
                    lo = mid
                else:
                    hi = mid - 1
            part = t if lo == whole else _part(t, done, lo)
            self.fetch(i, part)
            self.out.append(part)
            self.apply(part)
            done += lo
            keep = self.soon(i + 1)
            if done < whole:
                keep.update(_needs(_part(t, done, whole - done)))
            if done < whole or self.slots() > SLOT_LIMIT:
                self.deposit(keep)

    def gatherer(self, i: int, t: Gather | Hunt | Shear) -> None:
        remaining, first = t.item_count, True
        while remaining > 0:
            cap = self.capacity(t.item)
            if cap <= 0 and self.deposit(self.soon(i)):
                cap = self.capacity(t.item)
            take = min(remaining, cap) if cap > 0 else remaining
            if take == remaining and first:
                self.out.append(t)
            else:
                blocks = math.ceil(take / t.expected - EPS) + t.margin
                self.out.append(replace(t, count=blocks, item_count=take))
            self.inv[t.item] += take
            remaining -= take
            first = False
            if remaining > 0:
                self.deposit(self.soon(i))

    def hand_over(self) -> None:
        """Withdraw targets that sat in known containers from the start, as far as
        the inventory has room (the rest stays reported in `stored`)."""
        for item, n in self.targets.items():
            short = n - self.inv[item]
            for box in self.boxes:
                take = min(
                    short, box.initial[item], box.contents[item], self.capacity(item)
                )
                if take > 0:
                    self.withdraw_from(box, Counter({item: take}))
                    short -= take

    def run(self) -> StorageResult:
        for i, t in enumerate(self.tasks):
            self.at = i
            if isinstance(t, Gather | Hunt | Shear) and t.item_count > 0:
                self.gatherer(i, t)
            elif isinstance(t, Craft | Smelt | Transform) and _times(t) > 1:
                self.batched(i, t)
            else:
                self.fetch(i, t)
                self.out.append(t)
                self.apply(t)
            if self.slots() > SLOT_LIMIT:
                self.deposit(self.soon(i + 1))
        self.hand_over()
        stored: Counter[str] = Counter()
        for box in self.all_boxes():
            stored.update(+box.contents)
        return StorageResult(tuple(self.out), dict(stored), self.needs_chest)


def add_storage(
    tasks: Sequence[Task],
    held: Mapping[str, int],
    boxes: Iterable[Mapping[str, Any]],
    knowledge: Knowledge,
    targets: Mapping[str, int] | None = None,
) -> StorageResult:
    """Insert withdrawals, deposits, batch splits and container placement.

    Container choice at the first deposit: a shulker box carried, then one stored in
    a known container (withdrawn first), then a known container, then a carried or
    already-crafted chest; else `needs_chest` (the caller adds a chest and re-plans)."""
    return _Storage(tasks, held, boxes, knowledge, targets or {}).run()
