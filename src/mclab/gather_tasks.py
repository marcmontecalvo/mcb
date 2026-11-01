"""Task and result types of the gathering planner, plus inventory helpers shared by
the planner, `check_order` and the storage pass. No planning logic lives here."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

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
DEFAULT_DURABILITY = 1000
INVENTORY_SLOTS = 36
RESERVED_FREE_SLOTS = 4  # storage is added when more than 36 - 4 slots would be used
DEFAULT_STACK = 64


# ---- tasks ----------------------------------------------------------------
@dataclass(frozen=True)
class Gather:
    block: str  # block id to mine
    count: int  # blocks to mine (includes `margin` spare blocks for chance drops)
    tool_kind: str | None  # None: bare hand
    min_tier: str  # lowest drop tier that yields `item` ("hand", "wood", ...)
    item: str  # item wanted from it
    item_count: int
    margin: int = 0  # spare blocks planned for chance/range drops (G4)
    expected: float = 1.0  # expected items per block; check_order credits
    # (count - margin) * expected, i.e. the plan with the spare blocks lost


@dataclass(frozen=True)
class Hunt:
    """Kill `count` passive animals of `entity_type` (body op attack_entity)."""

    entity_type: str
    count: int
    item: str = ""  # what the hunt is for
    item_count: int = 0
    weapon: str | None = None  # best held sword/axe, else bare hand
    expected: float = 1.0
    margin: int = 0


@dataclass(frozen=True)
class Shear:
    """Shear `count` animals (body op use_entity with shears held)."""

    entity_type: str
    count: int
    item: str = ""
    item_count: int = 0
    expected: float = 1.0
    margin: int = 0


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


@dataclass(frozen=True)
class PlaceContainer:
    """Place a held chest or shulker box. `near` is a hint for the executor."""

    kind: str  # "chest" | "shulker_box"
    near: str = "build_site"


@dataclass(frozen=True)
class Deposit:
    items: tuple[tuple[str, int], ...]
    pos: tuple[int, int, int] | None = None  # None: the container placed by the plan


@dataclass(frozen=True)
class Withdraw:
    items: tuple[tuple[str, int], ...]
    pos: tuple[int, int, int] | None = None


Task = (
    Gather
    | Hunt
    | Shear
    | Craft
    | Smelt
    | Transform
    | PlaceStation
    | Equip
    | PlaceContainer
    | Deposit
    | Withdraw
)


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
    stored: Mapping[str, int] = field(default_factory=dict)  # left in containers

    @property
    def ok(self) -> bool:
        return not self.missing


# ---- inventory helpers ------------------------------------------------------
Inventory = Mapping[str, int] | Sequence[Mapping[str, Any]]


def normalize_inventory(
    inventory: Inventory | None,
) -> tuple[dict[str, int], dict[str, list[int]]]:
    """Counts per item and, for damaged tools, the remaining uses of each piece.
    Accepts `{item: count}` or the body's observe list of
    `{item, count, damage, max_damage}`."""
    if not inventory:
        return {}, {}
    if isinstance(inventory, Mapping):
        return {i: int(n) for i, n in inventory.items() if n > 0}, {}
    counts: Counter[str] = Counter()
    wear: dict[str, list[int]] = {}
    for st in inventory:
        item, n = st["item"], int(st.get("count", 1))
        if n <= 0:
            continue
        counts[item] += n
        mx = int(st.get("max_damage") or 0)
        if mx > 0:
            left = max(1, mx - int(st.get("damage") or 0))
            wear.setdefault(item, []).extend([left] * n)
    return dict(counts), wear


def slots_used(
    counts: Mapping[str, int], items: Mapping[str, Mapping[str, Any]]
) -> int:
    return sum(
        -(-n // int(items.get(i, {}).get("stack", DEFAULT_STACK)))
        for i, n in counts.items()
        if n > 0
    )


def item_stack(items: Mapping[str, Mapping[str, Any]], item: str) -> int:
    return int(items.get(item, {}).get("stack", DEFAULT_STACK))


def container_contents(storage: Iterable[Mapping[str, Any]] | None) -> Counter[str]:
    """Total items over the known containers (`contents`: {item: count} or a list)."""
    total: Counter[str] = Counter()
    for box in storage or ():
        counts, _ = normalize_inventory(box.get("contents"))
        total.update(counts)
    return total
