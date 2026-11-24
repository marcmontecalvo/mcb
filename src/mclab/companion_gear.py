"""What the bot wears: armor and a shield it carries are put on (the mod's ``wear``
op picks the best piece per slot; docs/COMPANION_COMBAT_PLAN_2026-11-23.md s15).

Pure: reads the body's inventory rows (``slot``, ``item``, ``count``). Slots 0-35 are
the main inventory; 36-39 the armor (feet to head) and 40 the off hand.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

M = "minecraft:"
MAIN_SLOTS = 36
ARMOR_SUFFIXES = ("_helmet", "_chestplate", "_leggings", "_boots")
SHIELD = M + "shield"


def is_gear(item: str) -> bool:
    return item == SHIELD or item.endswith(ARMOR_SUFFIXES)


def unworn(rows: Iterable[Mapping] | None) -> tuple[tuple[int, str], ...]:
    """Gear carried in the main inventory, (slot, item) by slot: what ``wear``
    might put on."""
    return tuple(
        sorted(
            (r["slot"], r["item"])
            for r in rows or ()
            if 0 <= r.get("slot", -1) < MAIN_SLOTS and is_gear(r.get("item", ""))
        )
    )


def signature(rows: Iterable[Mapping] | None) -> tuple[tuple[int, str], ...]:
    """Every gear row, carried or worn: a new one is a reason to dress again."""
    return tuple(
        sorted((r["slot"], r["item"]) for r in rows or () if is_gear(r.get("item", "")))
    )
