"""Persistent, survival-legal arena kit library, validator, scorer, and resolver.

Delivery Item 7 of ``docs/ARENA_PLAY_PLAN.md`` (Python layer):
- Built-in read-only starter classes (``fighter``, ``archer``, ``tank``,
  ``skirmisher``).
- Strict survival-legal validation: curated combat item allowlist, exact vanilla
  enchantment applicability / max levels / conflict groups, consumable caps, and
  explicit rejection of raw NBT, custom components, attribute modifiers, and commands.
- Power tier & bot-readiness scoring (``score_kit``).
- Wire compiler (``compile_kit``) compatible with ``TrialLogic`` / ``arena_client.kit``.
- Atomic JSON store (``KitLibrary`` backed by ``runtime/arena-kits.json``) plus host
  clone/tweak helper (``clone_and_tweak``) and UI catalog metadata
  (``catalog_metadata``).
- Deterministic lobby kit resolution (``resolve_match_kits``) across all five kit modes
  plus deterministic respawn re-rolling (``roll_respawn_kit``).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import re
import secrets
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mclab.paths import RUNTIME

SCHEMA = 1
KITS_FILE = "arena-kits.json"
DEFAULT_KITS_PATH = RUNTIME / KITS_FILE
SAVE_RETRIES = 40

MAX_GIVE = 36
MAX_ENCHANTMENTS_PER_ITEM = 4
MAX_WEAPONS = 4
MAX_ARROWS = 128
MAX_FOOD = 64
MAX_GOLDEN_APPLES = 8
MAX_POTIONS = 6
MAX_OFFHAND_PER_ITEM = 1
MAX_NAME_LEN = 40
MAX_DESCRIPTION_LEN = 200

KIT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
KIT_MODES = ("uniform", "per_team", "class_picker", "everyone_random", "random_shared")
RANDOM_SENTINEL = "random"

_TOP_LEVEL_FIELDS = frozenset({"id", "name", "description", "items", "slots"})
_ITEM_FIELDS = frozenset({"item", "count", "enchantments", "potion"})
_STORE_META_FIELDS = frozenset(
    {"builtin", "created_at", "updated_at", "power", "compiled"}
)
_VALID_SLOT_KEYS = frozenset(
    {
        "primary",
        "secondary",
        "weapons",
        "helmet",
        "chestplate",
        "leggings",
        "boots",
        "offhand",
        "ammo",
        "food",
        "potions",
    }
)
_ARMOR_CATEGORIES = frozenset({"helmet", "chestplate", "leggings", "boots"})
_DAMAGEABLE_CATEGORIES = frozenset(
    {
        "sword",
        "axe",
        "spear",
        "mace",
        "trident",
        "bow",
        "crossbow",
        "helmet",
        "chestplate",
        "leggings",
        "boots",
        "shield",
    }
)


class KitError(ValueError):
    """Domain error for kit store operations and match kit resolution."""

    def __init__(self, message: str, *, code: str = "kit_error"):
        super().__init__(message)
        self.code = code


class KitValidationError(KitError):
    """Raised when a kit spec violates survival-legal validation rules."""

    def __init__(self, errors: Sequence[str] | str):
        if isinstance(errors, str):
            items = (errors,)
        else:
            items = tuple(errors)
        self.errors: tuple[str, ...] = items
        super().__init__("; ".join(items), code="invalid_kit")


# ---------------------------------------------------------------------------
# Curated survival-legal combat catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemMeta:
    id: str
    label: str
    category: str
    group: str
    slot: str
    max_stack: int
    damageable: bool
    requires_potion: bool
    power_points: int


@dataclass(frozen=True)
class EnchantMeta:
    id: str
    label: str
    max_level: int
    categories: frozenset[str]
    conflicts: frozenset[str]
    power_per_level: int


def _title_id(full_id: str) -> str:
    bare = full_id.split(":", 1)[-1]
    return " ".join(part.capitalize() for part in bare.split("_"))


def _build_items() -> dict[str, ItemMeta]:
    items: list[ItemMeta] = []

    melee_tiers = (
        ("wooden", 6),
        ("stone", 9),
        ("copper", 10),
        ("golden", 8),
        ("iron", 12),
        ("diamond", 16),
        ("netherite", 18),
    )
    for tier, pts in melee_tiers:
        for weapon in ("sword", "axe", "spear"):
            item_id = f"minecraft:{tier}_{weapon}"
            items.append(
                ItemMeta(
                    id=item_id,
                    label=_title_id(item_id),
                    category=weapon,
                    group="melee",
                    slot="weapon",
                    max_stack=1,
                    damageable=True,
                    requires_potion=False,
                    power_points=pts,
                )
            )

    items.extend(
        [
            ItemMeta(
                "minecraft:mace", "Mace", "mace", "melee", "weapon", 1, True, False, 16
            ),
            ItemMeta(
                "minecraft:trident",
                "Trident",
                "trident",
                "melee",
                "weapon",
                1,
                True,
                False,
                14,
            ),
            ItemMeta(
                "minecraft:bow", "Bow", "bow", "ranged", "weapon", 1, True, False, 11
            ),
            ItemMeta(
                "minecraft:crossbow",
                "Crossbow",
                "crossbow",
                "ranged",
                "weapon",
                1,
                True,
                False,
                11,
            ),
        ]
    )

    armor_table = (
        ("leather", {"helmet": 2, "chestplate": 4, "leggings": 3, "boots": 2}),
        ("chainmail", {"helmet": 3, "chestplate": 6, "leggings": 5, "boots": 2}),
        ("copper", {"helmet": 3, "chestplate": 7, "leggings": 5, "boots": 3}),
        ("golden", {"helmet": 3, "chestplate": 6, "leggings": 4, "boots": 2}),
        ("iron", {"helmet": 4, "chestplate": 8, "leggings": 6, "boots": 4}),
        ("diamond", {"helmet": 6, "chestplate": 11, "leggings": 9, "boots": 6}),
        ("netherite", {"helmet": 7, "chestplate": 13, "leggings": 10, "boots": 7}),
    )
    for tier, piece_pts in armor_table:
        for piece, pts in piece_pts.items():
            item_id = f"minecraft:{tier}_{piece}"
            items.append(
                ItemMeta(
                    id=item_id,
                    label=_title_id(item_id),
                    category=piece,
                    group="armor",
                    slot=piece,
                    max_stack=1,
                    damageable=True,
                    requires_potion=False,
                    power_points=pts,
                )
            )

    items.append(
        ItemMeta(
            "minecraft:turtle_helmet",
            "Turtle Shell",
            "helmet",
            "armor",
            "helmet",
            1,
            True,
            False,
            5,
        )
    )

    items.extend(
        [
            ItemMeta(
                "minecraft:shield",
                "Shield",
                "shield",
                "offhand",
                "offhand",
                1,
                True,
                False,
                6,
            ),
            ItemMeta(
                "minecraft:totem_of_undying",
                "Totem of Undying",
                "totem",
                "offhand",
                "offhand",
                1,
                False,
                False,
                16,
            ),
        ]
    )

    items.extend(
        [
            ItemMeta(
                "minecraft:arrow", "Arrow", "ammo", "ammo", "ammo", 64, False, False, 0
            ),
            ItemMeta(
                "minecraft:spectral_arrow",
                "Spectral Arrow",
                "ammo",
                "ammo",
                "ammo",
                64,
                False,
                False,
                0,
            ),
            ItemMeta(
                "minecraft:tipped_arrow",
                "Tipped Arrow",
                "ammo",
                "ammo",
                "ammo",
                64,
                False,
                True,
                0,
            ),
        ]
    )

    for food_id in (
        "minecraft:cooked_beef",
        "minecraft:cooked_porkchop",
        "minecraft:cooked_chicken",
        "minecraft:cooked_mutton",
        "minecraft:bread",
        "minecraft:golden_carrot",
    ):
        items.append(
            ItemMeta(
                id=food_id,
                label=_title_id(food_id),
                category="food",
                group="food",
                slot="food",
                max_stack=64,
                damageable=False,
                requires_potion=False,
                power_points=0,
            )
        )

    items.append(
        ItemMeta(
            "minecraft:golden_apple",
            "Golden Apple",
            "golden_apple",
            "food",
            "food",
            64,
            False,
            False,
            3,
        )
    )

    items.extend(
        [
            ItemMeta(
                "minecraft:potion",
                "Potion",
                "potion",
                "potion",
                "potion",
                1,
                False,
                True,
                3,
            ),
            ItemMeta(
                "minecraft:splash_potion",
                "Splash Potion",
                "potion",
                "potion",
                "potion",
                1,
                False,
                True,
                3,
            ),
            ItemMeta(
                "minecraft:lingering_potion",
                "Lingering Potion",
                "potion",
                "potion",
                "potion",
                1,
                False,
                True,
                3,
            ),
        ]
    )

    return {meta.id: meta for meta in items}


ITEMS: dict[str, ItemMeta] = _build_items()

COMBAT_POTIONS: tuple[str, ...] = (
    "minecraft:healing",
    "minecraft:strong_healing",
    "minecraft:regeneration",
    "minecraft:long_regeneration",
    "minecraft:strong_regeneration",
    "minecraft:swiftness",
    "minecraft:long_swiftness",
    "minecraft:strong_swiftness",
    "minecraft:strength",
    "minecraft:long_strength",
    "minecraft:strong_strength",
    "minecraft:fire_resistance",
    "minecraft:long_fire_resistance",
    "minecraft:leaping",
    "minecraft:long_leaping",
    "minecraft:strong_leaping",
    "minecraft:harming",
    "minecraft:strong_harming",
    "minecraft:poison",
    "minecraft:long_poison",
    "minecraft:strong_poison",
    "minecraft:weakness",
    "minecraft:long_weakness",
    "minecraft:slowness",
    "minecraft:long_slowness",
    "minecraft:strong_slowness",
    "minecraft:turtle_master",
    "minecraft:long_turtle_master",
    "minecraft:strong_turtle_master",
    "minecraft:slow_falling",
    "minecraft:long_slow_falling",
)
_COMBAT_POTION_SET = frozenset(COMBAT_POTIONS)

CONFLICT_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {"minecraft:sharpness", "minecraft:smite", "minecraft:bane_of_arthropods"}
    ),
    frozenset(
        {
            "minecraft:density",
            "minecraft:breach",
            "minecraft:smite",
            "minecraft:bane_of_arthropods",
        }
    ),
    frozenset(
        {
            "minecraft:protection",
            "minecraft:fire_protection",
            "minecraft:blast_protection",
            "minecraft:projectile_protection",
        }
    ),
    frozenset({"minecraft:multishot", "minecraft:piercing"}),
    frozenset({"minecraft:infinity", "minecraft:mending"}),
    frozenset({"minecraft:loyalty", "minecraft:riptide"}),
    frozenset({"minecraft:channeling", "minecraft:riptide"}),
    frozenset({"minecraft:depth_strider", "minecraft:frost_walker"}),
)


def _build_enchantments() -> dict[str, EnchantMeta]:
    raw: list[tuple[str, int, frozenset[str], int]] = [
        ("minecraft:sharpness", 5, frozenset({"sword", "axe"}), 3),
        ("minecraft:smite", 5, frozenset({"sword", "axe", "mace"}), 2),
        ("minecraft:bane_of_arthropods", 5, frozenset({"sword", "axe", "mace"}), 1),
        ("minecraft:knockback", 2, frozenset({"sword"}), 2),
        ("minecraft:fire_aspect", 2, frozenset({"sword"}), 2),
        ("minecraft:sweeping_edge", 3, frozenset({"sword"}), 2),
        ("minecraft:power", 5, frozenset({"bow"}), 3),
        ("minecraft:punch", 2, frozenset({"bow"}), 2),
        ("minecraft:flame", 1, frozenset({"bow"}), 3),
        ("minecraft:infinity", 1, frozenset({"bow"}), 3),
        ("minecraft:quick_charge", 3, frozenset({"crossbow"}), 2),
        ("minecraft:multishot", 1, frozenset({"crossbow"}), 3),
        ("minecraft:piercing", 4, frozenset({"crossbow"}), 2),
        ("minecraft:loyalty", 3, frozenset({"trident"}), 2),
        ("minecraft:impaling", 5, frozenset({"trident"}), 2),
        ("minecraft:riptide", 3, frozenset({"trident"}), 2),
        ("minecraft:channeling", 1, frozenset({"trident"}), 2),
        ("minecraft:density", 5, frozenset({"mace"}), 3),
        ("minecraft:breach", 4, frozenset({"mace"}), 3),
        ("minecraft:wind_burst", 3, frozenset({"mace"}), 2),
        ("minecraft:protection", 4, _ARMOR_CATEGORIES, 2),
        ("minecraft:fire_protection", 4, _ARMOR_CATEGORIES, 2),
        ("minecraft:blast_protection", 4, _ARMOR_CATEGORIES, 2),
        ("minecraft:projectile_protection", 4, _ARMOR_CATEGORIES, 2),
        ("minecraft:thorns", 3, _ARMOR_CATEGORIES, 2),
        ("minecraft:respiration", 3, frozenset({"helmet"}), 1),
        ("minecraft:aqua_affinity", 1, frozenset({"helmet"}), 1),
        ("minecraft:swift_sneak", 3, frozenset({"leggings"}), 1),
        ("minecraft:feather_falling", 4, frozenset({"boots"}), 2),
        ("minecraft:depth_strider", 3, frozenset({"boots"}), 1),
        ("minecraft:soul_speed", 3, frozenset({"boots"}), 1),
        ("minecraft:frost_walker", 2, frozenset({"boots"}), 1),
        ("minecraft:unbreaking", 3, _DAMAGEABLE_CATEGORIES, 1),
        ("minecraft:mending", 1, _DAMAGEABLE_CATEGORIES, 1),
    ]
    out: dict[str, EnchantMeta] = {}
    for ench_id, max_lvl, cats, power in raw:
        conflicts: set[str] = set()
        for group in CONFLICT_GROUPS:
            if ench_id in group:
                conflicts.update(group - {ench_id})
        out[ench_id] = EnchantMeta(
            id=ench_id,
            label=_title_id(ench_id),
            max_level=max_lvl,
            categories=cats,
            conflicts=frozenset(conflicts),
            power_per_level=power,
        )
    return out


ENCHANTMENTS: dict[str, EnchantMeta] = _build_enchantments()


# ---------------------------------------------------------------------------
# Built-in starter classes (read-only, always available, duplicatable)
# ---------------------------------------------------------------------------


BUILTIN_KITS: dict[str, dict[str, Any]] = {
    "fighter": {
        "id": "fighter",
        "name": "Fighter",
        "description": (
            "Balanced melee frontline with Sharpness II sword, iron armor, "
            "shield, and healing."
        ),
        "items": [
            {
                "item": "minecraft:iron_sword",
                "count": 1,
                "enchantments": {"minecraft:sharpness": 2},
            },
            {
                "item": "minecraft:iron_helmet",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {
                "item": "minecraft:iron_chestplate",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {
                "item": "minecraft:iron_leggings",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {
                "item": "minecraft:iron_boots",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {"item": "minecraft:shield", "count": 1},
            {"item": "minecraft:cooked_beef", "count": 16},
            {"item": "minecraft:golden_apple", "count": 2},
            {
                "item": "minecraft:splash_potion",
                "count": 2,
                "potion": "minecraft:strong_healing",
            },
        ],
    },
    "archer": {
        "id": "archer",
        "name": "Archer",
        "description": (
            "Mobile ranged specialist with Power II Punch I bow, chainmail "
            "armor, and swiftness."
        ),
        "items": [
            {
                "item": "minecraft:bow",
                "count": 1,
                "enchantments": {"minecraft:power": 2, "minecraft:punch": 1},
            },
            {"item": "minecraft:arrow", "count": 64},
            {"item": "minecraft:stone_sword", "count": 1},
            {
                "item": "minecraft:chainmail_helmet",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {
                "item": "minecraft:chainmail_chestplate",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {
                "item": "minecraft:chainmail_leggings",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {
                "item": "minecraft:chainmail_boots",
                "count": 1,
                "enchantments": {
                    "minecraft:protection": 1,
                    "minecraft:feather_falling": 1,
                },
            },
            {"item": "minecraft:cooked_beef", "count": 16},
            {"item": "minecraft:potion", "count": 1, "potion": "minecraft:swiftness"},
        ],
    },
    "tank": {
        "id": "tank",
        "name": "Tank",
        "description": (
            "Heavy anchor with Protection II diamond/iron armor, Unbreaking "
            "shield, axe, and regen."
        ),
        "items": [
            {"item": "minecraft:iron_axe", "count": 1},
            {
                "item": "minecraft:iron_helmet",
                "count": 1,
                "enchantments": {"minecraft:protection": 2},
            },
            {
                "item": "minecraft:diamond_chestplate",
                "count": 1,
                "enchantments": {"minecraft:protection": 2},
            },
            {
                "item": "minecraft:iron_leggings",
                "count": 1,
                "enchantments": {"minecraft:protection": 2},
            },
            {
                "item": "minecraft:iron_boots",
                "count": 1,
                "enchantments": {"minecraft:protection": 2},
            },
            {
                "item": "minecraft:shield",
                "count": 1,
                "enchantments": {"minecraft:unbreaking": 2},
            },
            {"item": "minecraft:cooked_beef", "count": 16},
            {"item": "minecraft:golden_apple", "count": 3},
            {
                "item": "minecraft:potion",
                "count": 1,
                "potion": "minecraft:regeneration",
            },
        ],
    },
    "skirmisher": {
        "id": "skirmisher",
        "name": "Skirmisher",
        "description": (
            "Fast hybrid duelist with Knockback sword, Quick Charge crossbow, "
            "Loyalty trident, and speed."
        ),
        "items": [
            {
                "item": "minecraft:iron_sword",
                "count": 1,
                "enchantments": {"minecraft:knockback": 1},
            },
            {
                "item": "minecraft:crossbow",
                "count": 1,
                "enchantments": {"minecraft:quick_charge": 1},
            },
            {"item": "minecraft:arrow", "count": 32},
            {
                "item": "minecraft:trident",
                "count": 1,
                "enchantments": {"minecraft:loyalty": 2},
            },
            {
                "item": "minecraft:chainmail_helmet",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {
                "item": "minecraft:chainmail_chestplate",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {
                "item": "minecraft:chainmail_leggings",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {
                "item": "minecraft:chainmail_boots",
                "count": 1,
                "enchantments": {"minecraft:protection": 1},
            },
            {"item": "minecraft:cooked_beef", "count": 16},
            {"item": "minecraft:potion", "count": 2, "potion": "minecraft:swiftness"},
            {
                "item": "minecraft:splash_potion",
                "count": 2,
                "potion": "minecraft:strong_healing",
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Strict survival-legal validation
# ---------------------------------------------------------------------------


def _is_whole_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def _flatten_slots(slots: Any, errors: list[str]) -> list[Any]:
    if not isinstance(slots, dict):
        errors.append("slots must be an object")
        return []
    unknown = sorted(set(slots) - _VALID_SLOT_KEYS)
    if unknown:
        errors.append(f"Unknown slot key(s): {', '.join(unknown)}")
        return []
    ordered_keys = (
        "primary",
        "secondary",
        "weapons",
        "helmet",
        "chestplate",
        "leggings",
        "boots",
        "offhand",
        "ammo",
        "food",
        "potions",
    )
    out: list[Any] = []
    for key in ordered_keys:
        val = slots.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            out.extend(val)
        elif isinstance(val, dict):
            out.append(val)
        else:
            errors.append(f"slots.{key} must be an item object or list of item objects")
    return out


def validate_kit(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a declarative kit spec under survival-legal rules.

    Accepts either ``items: [...]`` or slot-organized ``slots: {...}``.
    Explicitly rejects unknown fields (raw NBT, components, attribute modifiers,
    command strings), non-allowlisted items, illegal enchantment levels/targets/
    conflicts, and consumable/inventory caps.
    """
    if not isinstance(spec, dict):
        raise KitValidationError("Kit spec must be a JSON object")

    errors: list[str] = []
    extra = sorted(set(spec) - _TOP_LEVEL_FIELDS)
    if extra:
        errors.append(
            f"Unsupported top-level field(s): {', '.join(extra)} "
            "(raw NBT, components, attribute modifiers, and commands are forbidden)"
        )

    raw_id = spec.get("id")
    norm_id: str | None = None
    if raw_id is not None:
        if not isinstance(raw_id, str) or not KIT_ID_RE.fullmatch(raw_id):
            errors.append(
                f"Invalid kit id {raw_id!r} "
                "(expected lowercase slug [a-z0-9][a-z0-9_-]{0,47})"
            )
        else:
            norm_id = raw_id

    raw_name = spec.get("name")
    norm_name = ""
    if not isinstance(raw_name, str) or not raw_name.strip():
        errors.append("Kit name is required")
    else:
        norm_name = raw_name.strip()
        if len(norm_name) > MAX_NAME_LEN:
            errors.append(f"Kit name must be at most {MAX_NAME_LEN} characters")
        if _has_control_chars(norm_name) or norm_name.startswith("/"):
            errors.append(
                "Kit name must not start with '/' or contain control characters"
            )

    raw_desc = spec.get("description", "")
    norm_desc = ""
    if raw_desc is not None:
        if not isinstance(raw_desc, str):
            errors.append("Kit description must be a string")
        else:
            norm_desc = raw_desc.strip()
            if len(norm_desc) > MAX_DESCRIPTION_LEN:
                errors.append(
                    f"Kit description must be at most {MAX_DESCRIPTION_LEN} characters"
                )
            if _has_control_chars(norm_desc):
                errors.append("Kit description must not contain control characters")

    has_items = "items" in spec
    has_slots = "slots" in spec
    if has_items and has_slots:
        errors.append("Provide either 'items' or 'slots', not both")
        raw_items: list[Any] = []
    elif has_items:
        if not isinstance(spec["items"], list):
            errors.append("'items' must be a list")
            raw_items = []
        else:
            raw_items = spec["items"]
    elif has_slots:
        raw_items = _flatten_slots(spec["slots"], errors)
    else:
        errors.append("Kit must include 'items' or 'slots'")
        raw_items = []

    if (has_items or has_slots) and not raw_items and not errors:
        errors.append("Kit must contain at least one item")
    if len(raw_items) > MAX_GIVE:
        errors.append(
            f"Kit may have at most {MAX_GIVE} item entries (got {len(raw_items)})"
        )

    normalized_items: list[dict[str, Any]] = []
    weapon_count = 0
    armor_slots_seen: set[str] = set()
    offhand_counts: dict[str, int] = {}
    arrow_total = 0
    food_total = 0
    gapple_total = 0
    potion_total = 0
    compiled_slots = 0

    for idx, entry in enumerate(raw_items):
        where = f"items[{idx}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        extra_item = sorted(set(entry) - _ITEM_FIELDS)
        if extra_item:
            errors.append(f"{where} has unsupported field(s): {', '.join(extra_item)}")
            continue

        item_id = entry.get("item")
        if not isinstance(item_id, str) or item_id not in ITEMS:
            errors.append(
                f"{where}.item {item_id!r} is not in the survival-legal combat catalog"
            )
            continue
        meta = ITEMS[item_id]

        count = entry.get("count")
        if not _is_whole_int(count) or count < 1:
            errors.append(f"{where}.count must be a positive integer")
            continue

        if meta.group in ("melee", "ranged", "armor", "offhand") and count != 1:
            errors.append(f"{where} ({item_id}) must have count 1 (got {count})")
            continue
        if meta.group == "ammo" and count > MAX_ARROWS:
            errors.append(f"{where} ({item_id}) exceeds max arrows ({MAX_ARROWS})")
        if meta.category == "food" and count > MAX_FOOD:
            errors.append(f"{where} ({item_id}) exceeds max food ({MAX_FOOD})")
        if meta.category == "golden_apple" and count > MAX_GOLDEN_APPLES:
            errors.append(
                f"{where} ({item_id}) exceeds max golden apples ({MAX_GOLDEN_APPLES})"
            )
        if meta.group == "potion" and count > MAX_POTIONS:
            errors.append(f"{where} ({item_id}) exceeds max potions ({MAX_POTIONS})")

        # Track aggregate caps
        if meta.group in ("melee", "ranged"):
            weapon_count += count
        elif meta.group == "armor":
            if meta.category in armor_slots_seen:
                errors.append(f"Duplicate armor slot '{meta.category}' ({item_id})")
            else:
                armor_slots_seen.add(meta.category)
        elif meta.group == "offhand":
            offhand_counts[item_id] = offhand_counts.get(item_id, 0) + count
        elif meta.group == "ammo":
            arrow_total += count
        elif meta.category == "food":
            food_total += count
        elif meta.category == "golden_apple":
            gapple_total += count
        elif meta.group == "potion":
            potion_total += count

        compiled_slots += math.ceil(count / meta.max_stack)

        # Potion validation
        raw_potion = entry.get("potion")
        norm_potion: str | None = None
        if meta.requires_potion:
            if not isinstance(raw_potion, str) or raw_potion not in _COMBAT_POTION_SET:
                errors.append(
                    f"{where}.potion must be a valid minecraft: combat potion id "
                    f"(got {raw_potion!r})"
                )
            else:
                norm_potion = raw_potion
        elif raw_potion is not None:
            errors.append(f"{where}.potion is not allowed on {item_id}")

        # Enchantment validation
        raw_ench = entry.get("enchantments")
        norm_ench: dict[str, int] = {}
        if raw_ench is not None:
            if not isinstance(raw_ench, dict):
                errors.append(f"{where}.enchantments must be an object")
            else:
                if len(raw_ench) > MAX_ENCHANTMENTS_PER_ITEM:
                    errors.append(
                        f"{where} may have at most {MAX_ENCHANTMENTS_PER_ITEM} "
                        f"enchantments (got {len(raw_ench)})"
                    )
                for ench_id, level in raw_ench.items():
                    if not isinstance(ench_id, str) or ench_id not in ENCHANTMENTS:
                        errors.append(
                            f"{where}.enchantments.{ench_id!r} is not a legal "
                            "combat enchantment"
                        )
                        continue
                    emeta = ENCHANTMENTS[ench_id]
                    if meta.category not in emeta.categories:
                        errors.append(
                            f"{where}: {ench_id} cannot be applied to {item_id}"
                        )
                        continue
                    if not _is_whole_int(level) or not 1 <= level <= emeta.max_level:
                        errors.append(
                            f"{where}: {ench_id} level must be 1..{emeta.max_level} "
                            f"(got {level!r})"
                        )
                        continue
                    norm_ench[ench_id] = level

                # Mutual exclusion checks
                seen_ench = sorted(norm_ench)
                for i, first in enumerate(seen_ench):
                    conflicts = ENCHANTMENTS[first].conflicts
                    for second in seen_ench[i + 1 :]:
                        if second in conflicts:
                            errors.append(
                                f"{where}: conflicting enchantments "
                                f"{first} and {second}"
                            )

        norm_entry: dict[str, Any] = {"item": item_id, "count": count}
        if norm_ench:
            norm_entry["enchantments"] = dict(sorted(norm_ench.items()))
        if norm_potion is not None:
            norm_entry["potion"] = norm_potion
        normalized_items.append(norm_entry)

    if weapon_count > MAX_WEAPONS:
        errors.append(
            f"Kit may contain at most {MAX_WEAPONS} weapons (got {weapon_count})"
        )
    for off_id, total in offhand_counts.items():
        if total > MAX_OFFHAND_PER_ITEM:
            errors.append(
                f"Kit may contain at most {MAX_OFFHAND_PER_ITEM} {off_id} (got {total})"
            )
    if arrow_total > MAX_ARROWS:
        errors.append(
            f"Kit may contain at most {MAX_ARROWS} arrows total (got {arrow_total})"
        )
    if food_total > MAX_FOOD:
        errors.append(
            f"Kit may contain at most {MAX_FOOD} food items total (got {food_total})"
        )
    if gapple_total > MAX_GOLDEN_APPLES:
        errors.append(
            f"Kit may contain at most {MAX_GOLDEN_APPLES} golden apples "
            f"(got {gapple_total})"
        )
    if potion_total > MAX_POTIONS:
        errors.append(
            f"Kit may contain at most {MAX_POTIONS} potions total (got {potion_total})"
        )
    if compiled_slots > MAX_GIVE:
        errors.append(
            f"Kit requires {compiled_slots} inventory slots, exceeding limit {MAX_GIVE}"
        )

    if errors:
        raise KitValidationError(errors)

    result: dict[str, Any] = {
        "name": norm_name,
        "description": norm_desc,
        "items": normalized_items,
    }
    if norm_id is not None:
        result["id"] = norm_id
    return result


def _strip_store_meta(kit: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(kit, dict):
        return kit  # type: ignore[return-value]
    return {k: v for k, v in kit.items() if k not in _STORE_META_FIELDS}


# ---------------------------------------------------------------------------
# Power Tier & Bot-Readiness scoring
# ---------------------------------------------------------------------------


def score_kit(kit: dict[str, Any]) -> dict[str, Any]:
    """Compute transparent Power Tier score, breakdown, and bot-readiness notes."""
    normalized = validate_kit(_strip_store_meta(kit))
    items = normalized["items"]

    melee_scores: list[int] = []
    ranged_scores: list[int] = []
    armor_pts = 0
    offhand_pts = 0
    enchant_pts = 0
    food_count = 0
    has_golden_carrot = False
    gapple_count = 0
    potion_pts = 0
    arrow_count = 0
    has_special_arrow = False
    has_infinity_bow = False

    item_ids: set[str] = set()
    for entry in items:
        item_id = entry["item"]
        count = entry["count"]
        meta = ITEMS[item_id]
        item_ids.add(item_id)
        enchants = entry.get("enchantments") or {}

        for ench_id, level in enchants.items():
            enchant_pts += ENCHANTMENTS[ench_id].power_per_level * level
        if item_id == "minecraft:bow" and "minecraft:infinity" in enchants:
            has_infinity_bow = True

        if meta.group == "melee":
            melee_scores.append(meta.power_points)
        elif meta.group == "ranged":
            ranged_scores.append(meta.power_points)
        elif meta.group == "armor":
            armor_pts += meta.power_points
        elif meta.group == "offhand":
            offhand_pts += meta.power_points
        elif meta.group == "ammo":
            arrow_count += count
            if item_id in ("minecraft:spectral_arrow", "minecraft:tipped_arrow"):
                has_special_arrow = True
        elif meta.category == "food":
            food_count += count
            if item_id == "minecraft:golden_carrot":
                has_golden_carrot = True
        elif meta.category == "golden_apple":
            gapple_count += count
        elif meta.group == "potion":
            pid = entry.get("potion", "")
            per_bottle = 4 if ("strong_" in pid or "turtle_master" in pid) else 3
            potion_pts += per_bottle * count

    melee_scores.sort(reverse=True)
    melee_pts = (
        melee_scores[0] + sum(round(p * 0.25) for p in melee_scores[1:])
        if melee_scores
        else 0
    )

    ranged_scores.sort(reverse=True)
    ranged_base = (
        ranged_scores[0] + sum(round(p * 0.25) for p in ranged_scores[1:])
        if ranged_scores
        else 0
    )
    if ranged_base > 0:
        if arrow_count == 0 and not has_infinity_bow:
            ranged_pts = round(ranged_base * 0.4)
        else:
            ammo_bonus = 2 + (2 if has_special_arrow else 0)
            ranged_pts = ranged_base + ammo_bonus
    else:
        ranged_pts = 0

    food_pts = min(3, round((food_count / 16) * 3)) + (1 if has_golden_carrot else 0)
    consumable_pts = food_pts + gapple_count * 3 + potion_pts

    total = (
        melee_pts + ranged_pts + armor_pts + offhand_pts + enchant_pts + consumable_pts
    )
    if total < 45:
        tier = "Starter"
    elif total < 75:
        tier = "Balanced"
    elif total < 125:
        tier = "Heavy"
    else:
        tier = "Champion"

    bot_notes: list[str] = []
    if "minecraft:crossbow" in item_ids:
        bot_notes.append(
            "Bots have limited crossbow support; humans can use it normally."
        )
    if "minecraft:mace" in item_ids:
        bot_notes.append(
            "Bots have limited mace support (no aerial smash); best for human players."
        )
    if "minecraft:totem_of_undying" in item_ids:
        bot_notes.append(
            "Totem of Undying triggers automatically in offhand; bots prefer a shield."
        )
    if "minecraft:lingering_potion" in item_ids:
        bot_notes.append("Bots may not optimally throw lingering potions.")
    if (
        ("minecraft:bow" in item_ids or "minecraft:crossbow" in item_ids)
        and arrow_count == 0
        and not has_infinity_bow
    ):
        bot_notes.append("Ranged weapon included without arrows.")
    if food_count == 0 and gapple_count == 0:
        bot_notes.append(
            "No food included; players cannot regenerate hunger naturally."
        )

    return {
        "score": total,
        "tier": tier,
        "breakdown": {
            "melee": melee_pts,
            "ranged": ranged_pts,
            "armor": armor_pts,
            "offhand": offhand_pts,
            "enchantments": enchant_pts,
            "consumables": consumable_pts,
        },
        "bot_notes": bot_notes,
    }


# ---------------------------------------------------------------------------
# Compiler to arena_kit / PlayerKit / TrialLogic wire format
# ---------------------------------------------------------------------------


def compile_kit(kit: dict[str, Any]) -> dict[str, Any]:
    """Compile a validated kit spec into ``arena_kit`` / ``TrialLogic`` format."""
    normalized = validate_kit(_strip_store_meta(kit))
    give: list[dict[str, Any]] = []
    for entry in normalized["items"]:
        meta = ITEMS[entry["item"]]
        remaining = entry["count"]
        while remaining > 0:
            chunk = min(remaining, meta.max_stack)
            wire_entry: dict[str, Any] = {"item": entry["item"], "count": chunk}
            if "enchantments" in entry:
                wire_entry["enchantments"] = dict(entry["enchantments"])
            if "potion" in entry:
                wire_entry["potion"] = entry["potion"]
            give.append(wire_entry)
            remaining -= chunk
    return {
        "give": give,
        "clear_inventory": True,
        "heal": True,
        "wear": True,
    }


# ---------------------------------------------------------------------------
# Catalog metadata export (single source of truth for Dashboard UI)
# ---------------------------------------------------------------------------


def catalog_metadata() -> dict[str, Any]:
    """Export curated combat catalog, enchantment table, caps, and kit modes."""
    return {
        "schema": SCHEMA,
        "kit_modes": list(KIT_MODES),
        "random_sentinel": RANDOM_SENTINEL,
        "caps": {
            "max_give": MAX_GIVE,
            "max_enchantments_per_item": MAX_ENCHANTMENTS_PER_ITEM,
            "max_weapons": MAX_WEAPONS,
            "max_arrows": MAX_ARROWS,
            "max_food": MAX_FOOD,
            "max_golden_apples": MAX_GOLDEN_APPLES,
            "max_potions": MAX_POTIONS,
            "max_name_len": MAX_NAME_LEN,
            "max_description_len": MAX_DESCRIPTION_LEN,
        },
        "items": [
            {
                "id": meta.id,
                "label": meta.label,
                "category": meta.category,
                "group": meta.group,
                "slot": meta.slot,
                "max_stack": meta.max_stack,
                "damageable": meta.damageable,
                "requires_potion": meta.requires_potion,
                "power_points": meta.power_points,
            }
            for meta in ITEMS.values()
        ],
        "potions": [{"id": pid, "label": _title_id(pid)} for pid in COMBAT_POTIONS],
        "enchantments": [
            {
                "id": emeta.id,
                "label": emeta.label,
                "max_level": emeta.max_level,
                "categories": sorted(emeta.categories),
                "conflicts": sorted(emeta.conflicts),
                "power_per_level": emeta.power_per_level,
            }
            for emeta in ENCHANTMENTS.values()
        ],
        "tiers": [
            {"name": "Starter", "max_score": 44},
            {"name": "Balanced", "max_score": 74},
            {"name": "Heavy", "max_score": 124},
            {"name": "Champion", "max_score": None},
        ],
    }


# ---------------------------------------------------------------------------
# Persistent JSON store (runtime/arena-kits.json) + clone/tweak helper
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug or not slug[0].isalnum():
        slug = "kit"
    return slug[:40]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for attempt in range(SAVE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == SAVE_RETRIES - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.02)


class KitLibrary:
    """Thread-safe persistent kit store backed by ``runtime/arena-kits.json``."""

    def __init__(self, path: Path | None = None):
        self.path = DEFAULT_KITS_PATH if path is None else Path(path)
        self._lock = threading.RLock()
        self._custom: dict[str, dict[str, Any]] = {}
        self._last_mtime_ns: int | None = None

    def _reload_locked(self) -> None:
        if not self.path.is_file():
            self._custom = {}
            self._last_mtime_ns = None
            return
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            return
        if self._last_mtime_ns == mtime:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise KitError(
                f"Cannot read kit library {self.path}: {error}", code="corrupt_store"
            ) from error
        if not isinstance(raw, dict) or not isinstance(raw.get("kits"), dict):
            raise KitError(
                f"Kit library {self.path} has invalid structure", code="corrupt_store"
            )
        loaded: dict[str, dict[str, Any]] = {}
        for kit_id, entry in raw["kits"].items():
            if kit_id in BUILTIN_KITS:
                continue
            if not isinstance(entry, dict):
                raise KitError(
                    f"Stored kit {kit_id!r} is not an object", code="corrupt_store"
                )
            spec = {
                "id": kit_id,
                "name": entry.get("name"),
                "description": entry.get("description", ""),
                "items": entry.get("items"),
            }
            validated = validate_kit(spec)
            loaded[kit_id] = {
                **validated,
                "created_at": entry.get("created_at"),
                "updated_at": entry.get("updated_at"),
            }
        self._custom = loaded
        self._last_mtime_ns = mtime

    def _save_locked(self) -> None:
        payload = {
            "schema": SCHEMA,
            "updated_at": _utc_now(),
            "kits": {
                kit_id: {
                    "id": kit_id,
                    "name": rec["name"],
                    "description": rec["description"],
                    "items": rec["items"],
                    "created_at": rec.get("created_at"),
                    "updated_at": rec.get("updated_at"),
                }
                for kit_id, rec in sorted(self._custom.items())
            },
        }
        _atomic_write_json(self.path, payload)
        try:
            self._last_mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            pass

    def _enrich(
        self, rec: dict[str, Any], *, builtin: bool, include_compiled: bool = True
    ) -> dict[str, Any]:
        spec = {
            "id": rec["id"],
            "name": rec["name"],
            "description": rec.get("description", ""),
            "items": copy.deepcopy(rec["items"]),
        }
        out: dict[str, Any] = {
            **spec,
            "builtin": builtin,
            "created_at": None if builtin else rec.get("created_at"),
            "updated_at": None if builtin else rec.get("updated_at"),
            "power": score_kit(spec),
        }
        if include_compiled:
            out["compiled"] = compile_kit(spec)
        return out

    def _unique_id_locked(self, name: str) -> str:
        base = _slugify(name)
        candidate = base
        suffix = 2
        while candidate in BUILTIN_KITS or candidate in self._custom:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def list_kits(self, *, include_compiled: bool = True) -> list[dict[str, Any]]:
        """Return built-in starter classes first, then custom kits sorted by name."""
        with self._lock:
            self._reload_locked()
            builtins = [
                self._enrich(spec, builtin=True, include_compiled=include_compiled)
                for spec in BUILTIN_KITS.values()
            ]
            customs = [
                self._enrich(rec, builtin=False, include_compiled=include_compiled)
                for rec in sorted(
                    self._custom.values(), key=lambda r: (r["name"].lower(), r["id"])
                )
            ]
            return builtins + customs

    def has_kit(self, kit_id: str) -> bool:
        key = kit_id.strip().lower() if isinstance(kit_id, str) else ""
        with self._lock:
            self._reload_locked()
            return key in BUILTIN_KITS or key in self._custom

    def get_kit(self, kit_id: str, *, include_compiled: bool = True) -> dict[str, Any]:
        if not isinstance(kit_id, str) or not kit_id.strip():
            raise KitError("Kit id is required", code="not_found")
        key = kit_id.strip().lower()
        with self._lock:
            self._reload_locked()
            if key in BUILTIN_KITS:
                return self._enrich(
                    BUILTIN_KITS[key], builtin=True, include_compiled=include_compiled
                )
            if key in self._custom:
                return self._enrich(
                    self._custom[key], builtin=False, include_compiled=include_compiled
                )
            raise KitError(f"Kit {kit_id!r} not found", code="not_found")

    def create_kit(self, spec: dict[str, Any]) -> dict[str, Any]:
        validated = validate_kit(spec)
        with self._lock:
            self._reload_locked()
            explicit_id = validated.get("id")
            if explicit_id is not None:
                if explicit_id in BUILTIN_KITS:
                    raise KitError(
                        f"Kit id {explicit_id!r} is reserved for a built-in class",
                        code="readonly_builtin",
                    )
                if explicit_id in self._custom:
                    raise KitError(
                        f"Kit id {explicit_id!r} already exists", code="duplicate_id"
                    )
                kit_id = explicit_id
            else:
                kit_id = self._unique_id_locked(validated["name"])
            now = _utc_now()
            rec = {
                "id": kit_id,
                "name": validated["name"],
                "description": validated["description"],
                "items": validated["items"],
                "created_at": now,
                "updated_at": now,
            }
            self._custom[kit_id] = rec
            self._save_locked()
            return self._enrich(rec, builtin=False)

    def update_kit(self, kit_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        key = kit_id.strip().lower() if isinstance(kit_id, str) else ""
        with self._lock:
            self._reload_locked()
            if key in BUILTIN_KITS:
                raise KitError(
                    f"Built-in class {key!r} is read-only; duplicate it to customize",
                    code="readonly_builtin",
                )
            if key not in self._custom:
                raise KitError(f"Kit {kit_id!r} not found", code="not_found")
            spec_copy = dict(spec) if isinstance(spec, dict) else spec
            if isinstance(spec_copy, dict):
                if "id" in spec_copy and spec_copy["id"] not in (None, key):
                    raise KitValidationError(
                        f"Cannot change kit id from {key!r} to {spec_copy['id']!r}"
                    )
                spec_copy["id"] = key
            validated = validate_kit(spec_copy)
            existing = self._custom[key]
            rec = {
                "id": key,
                "name": validated["name"],
                "description": validated["description"],
                "items": validated["items"],
                "created_at": existing.get("created_at") or _utc_now(),
                "updated_at": _utc_now(),
            }
            self._custom[key] = rec
            self._save_locked()
            return self._enrich(rec, builtin=False)

    def duplicate_kit(
        self,
        source_id: str,
        new_name: str | None = None,
        *,
        new_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        source = self.get_kit(source_id, include_compiled=False)
        if new_name is None:
            candidate = f"{source['name']} Copy"
            resolved_name = candidate[:MAX_NAME_LEN].strip()
        else:
            resolved_name = new_name
        spec: dict[str, Any] = {
            "name": resolved_name,
            "description": source["description"]
            if description is None
            else description,
            "items": copy.deepcopy(source["items"]),
        }
        if new_id is not None:
            spec["id"] = new_id
        return self.create_kit(spec)

    def delete_kit(self, kit_id: str) -> dict[str, str]:
        key = kit_id.strip().lower() if isinstance(kit_id, str) else ""
        with self._lock:
            self._reload_locked()
            if key in BUILTIN_KITS:
                raise KitError(
                    f"Built-in starter class {key!r} cannot be deleted",
                    code="readonly_builtin",
                )
            if key not in self._custom:
                raise KitError(f"Kit {kit_id!r} not found", code="not_found")
            del self._custom[key]
            self._save_locked()
            return {"deleted": key}

    def clone_and_tweak(
        self,
        source_id: str,
        new_name: str,
        *,
        new_id: str | None = None,
        description: str | None = None,
        add_items: Sequence[dict[str, Any]] | None = None,
        remove_items: Sequence[str] | None = None,
        replace_items: dict[str, dict[str, Any]] | None = None,
        enchant_tweaks: dict[str, dict[str, int | None]] | None = None,
    ) -> dict[str, Any]:
        """Host lobby clone/tweak helper — validated through survival-legal rules."""
        source = self.get_kit(source_id, include_compiled=False)
        items: list[dict[str, Any]] = copy.deepcopy(source["items"])

        if remove_items:
            remove_set = set(remove_items)
            items = [entry for entry in items if entry.get("item") not in remove_set]

        if replace_items:
            items = [
                copy.deepcopy(replace_items[entry["item"]])
                if entry.get("item") in replace_items
                else entry
                for entry in items
            ]

        if add_items:
            items.extend(copy.deepcopy(list(add_items)))

        if enchant_tweaks:
            for entry in items:
                tweaks = enchant_tweaks.get(entry.get("item", ""))
                if not tweaks:
                    continue
                ench = dict(entry.get("enchantments") or {})
                for ench_id, lvl in tweaks.items():
                    if lvl is None:
                        ench.pop(ench_id, None)
                    else:
                        ench[ench_id] = lvl
                if ench:
                    entry["enchantments"] = ench
                else:
                    entry.pop("enchantments", None)

        spec: dict[str, Any] = {
            "name": new_name,
            "description": source["description"]
            if description is None
            else description,
            "items": items,
        }
        if new_id is not None:
            spec["id"] = new_id
        return self.create_kit(spec)


# ---------------------------------------------------------------------------
# Lobby kit modes & deterministic Random Kit resolver
# ---------------------------------------------------------------------------


def _validated_pool(library: KitLibrary, pool: Sequence[str] | None) -> list[str]:
    if pool is None:
        ids = sorted(k["id"] for k in library.list_kits(include_compiled=False))
    else:
        ids = sorted(
            {p.strip().lower() for p in pool if isinstance(p, str) and p.strip()}
        )
    if not ids:
        raise KitError(
            "Kit pool must contain at least one valid kit", code="empty_pool"
        )
    for kit_id in ids:
        library.get_kit(kit_id, include_compiled=False)
    return ids


def _dedupe_participants(
    participants: Sequence[str], teams: dict[str, Sequence[str]] | None
) -> list[str]:
    raw = list(participants)
    if not raw and teams:
        for members in teams.values():
            raw.extend(members)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in raw:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(name)
    if not ordered:
        raise KitError("At least one participant is required", code="no_participants")
    return ordered


def _lookup_player_value(mapping: dict[str, str] | None, player: str) -> str | None:
    if not mapping:
        return None
    if player in mapping:
        return mapping[player]
    lower = player.lower()
    for key, value in mapping.items():
        if key.lower() == lower:
            return value
    return None


def _assignment_entry(
    kit: dict[str, Any], *, requested: str, random_rolled: bool
) -> dict[str, Any]:
    return {
        "kit_id": kit["id"],
        "kit_name": kit["name"],
        "requested": requested,
        "random_rolled": random_rolled,
        "power_tier": kit["power"]["tier"],
        "power_score": kit["power"]["score"],
        "compiled": kit["compiled"],
    }


def resolve_match_kits(
    library: KitLibrary,
    *,
    mode: str,
    participants: Sequence[str],
    teams: dict[str, Sequence[str]] | None = None,
    kit_id: str | None = None,
    team_kits: dict[str, str] | None = None,
    player_choices: dict[str, str] | None = None,
    pool: Sequence[str] | None = None,
    reroll_on_respawn: bool = False,
    seed: int | None = None,
    random_fallback: bool = True,
) -> dict[str, Any]:
    """Resolve kits atomically at match start across all five lobby kit modes."""
    if mode not in KIT_MODES:
        raise KitError(
            f"Unknown kit mode {mode!r}; expected one of {', '.join(KIT_MODES)}",
            code="invalid_mode",
        )
    if seed is not None and not _is_whole_int(seed):
        raise KitError("seed must be an integer", code="invalid_seed")

    ordered_players = _dedupe_participants(participants, teams)
    sorted_pool = _validated_pool(library, pool)
    resolved_seed = secrets.randbits(53) if seed is None else seed
    rng = random.Random(resolved_seed)

    assignments: dict[str, dict[str, Any]] = {}
    shared_kit: str | None = None

    if mode == "uniform":
        requested = (kit_id or "fighter").strip().lower()
        if requested == RANDOM_SENTINEL:
            chosen_id = rng.choice(sorted_pool)
            rolled = True
        else:
            chosen_id = requested
            rolled = False
        kit = library.get_kit(chosen_id)
        shared_kit = kit["id"]
        for player in ordered_players:
            assignments[player] = _assignment_entry(
                kit, requested=requested, random_rolled=rolled
            )

    elif mode == "per_team":
        if not teams:
            raise KitError("per_team mode requires teams", code="missing_team")
        player_team: dict[str, str] = {}
        for team_name, members in teams.items():
            for member in members:
                player_team[member.lower()] = team_name
        used_teams = sorted(
            {
                player_team[p.lower()]
                for p in ordered_players
                if p.lower() in player_team
            }
        )
        for player in ordered_players:
            if player.lower() not in player_team:
                raise KitError(
                    f"Participant {player!r} has no team assignment",
                    code="missing_team",
                )
        resolved_team_kits: dict[str, tuple[dict[str, Any], str, bool]] = {}
        for team_name in used_teams:
            req = (team_kits or {}).get(team_name)
            if not isinstance(req, str) or not req.strip():
                raise KitError(
                    f"Team {team_name!r} has no kit assigned", code="missing_team_kit"
                )
            req_norm = req.strip().lower()
            if req_norm == RANDOM_SENTINEL:
                chosen_id = rng.choice(sorted_pool)
                kit = library.get_kit(chosen_id)
                resolved_team_kits[team_name] = (kit, RANDOM_SENTINEL, True)
            else:
                kit = library.get_kit(req_norm)
                resolved_team_kits[team_name] = (kit, kit["id"], False)
        for player in ordered_players:
            team_name = player_team[player.lower()]
            kit, req_str, rolled = resolved_team_kits[team_name]
            assignments[player] = _assignment_entry(
                kit, requested=req_str, random_rolled=rolled
            )

    elif mode == "class_picker":
        pool_set = set(sorted_pool)
        for player in ordered_players:
            raw_choice = _lookup_player_value(player_choices, player)
            if raw_choice is None or not raw_choice.strip():
                if not random_fallback:
                    raise KitError(
                        f"Participant {player!r} has not selected a kit",
                        code="missing_choice",
                    )
                req_norm = RANDOM_SENTINEL
            else:
                req_norm = raw_choice.strip().lower()
            if req_norm == RANDOM_SENTINEL:
                chosen_id = rng.choice(sorted_pool)
                kit = library.get_kit(chosen_id)
                assignments[player] = _assignment_entry(
                    kit, requested=RANDOM_SENTINEL, random_rolled=True
                )
            else:
                if req_norm not in pool_set:
                    raise KitError(
                        f"Kit {req_norm!r} is not in the host's allowed pool",
                        code="invalid_choice",
                    )
                kit = library.get_kit(req_norm)
                assignments[player] = _assignment_entry(
                    kit, requested=kit["id"], random_rolled=False
                )

    elif mode == "everyone_random":
        for player in ordered_players:
            chosen_id = rng.choice(sorted_pool)
            kit = library.get_kit(chosen_id)
            assignments[player] = _assignment_entry(
                kit, requested=RANDOM_SENTINEL, random_rolled=True
            )

    elif mode == "random_shared":
        chosen_id = rng.choice(sorted_pool)
        kit = library.get_kit(chosen_id)
        shared_kit = kit["id"]
        for player in ordered_players:
            assignments[player] = _assignment_entry(
                kit, requested=RANDOM_SENTINEL, random_rolled=True
            )

    if shared_kit is not None:
        shared_name = library.get_kit(shared_kit, include_compiled=False)["name"]
        summary_text = f"Shared kit: {shared_name} ({mode}, seed {resolved_seed})"
    else:
        parts = [
            f"{p}: {a['kit_name']}" + (" [Random]" if a["random_rolled"] else "")
            for p, a in assignments.items()
        ]
        summary_text = f"Kits ({mode}, seed {resolved_seed}): " + ", ".join(parts)

    recap_summary = {
        "mode": mode,
        "seed": resolved_seed,
        "shared_kit": shared_kit,
        "reroll_on_respawn": bool(reroll_on_respawn),
        "players": {
            player: {
                "kit_id": entry["kit_id"],
                "kit_name": entry["kit_name"],
                "random_rolled": entry["random_rolled"],
                "power_tier": entry["power_tier"],
            }
            for player, entry in assignments.items()
        },
        "text": summary_text,
    }

    return {
        "mode": mode,
        "seed": resolved_seed,
        "pool": sorted_pool,
        "reroll_on_respawn": bool(reroll_on_respawn),
        "shared_kit": shared_kit,
        "assignments": assignments,
        "recap_summary": recap_summary,
    }


def roll_respawn_kit(
    library: KitLibrary,
    *,
    pool: Sequence[str] | None,
    seed: int,
    player: str,
    respawn_index: int,
    previous_kit_id: str | None = None,
) -> dict[str, Any]:
    """Deterministically roll a respawn kit when ``reroll_on_respawn`` is enabled."""
    if not _is_whole_int(seed):
        raise KitError("seed must be an integer", code="invalid_seed")
    if not _is_whole_int(respawn_index) or respawn_index < 1:
        raise KitError("respawn_index must be >= 1", code="invalid_respawn_index")
    if not isinstance(player, str) or not player.strip():
        raise KitError("player is required", code="invalid_player")

    sorted_pool = _validated_pool(library, pool)
    prev = previous_kit_id.strip().lower() if isinstance(previous_kit_id, str) else None
    candidates = (
        [k for k in sorted_pool if k != prev]
        if len(sorted_pool) > 1 and prev in sorted_pool
        else sorted_pool
    )
    digest_bytes = hashlib.sha256(
        f"{seed}:{player.strip().lower()}:{respawn_index}".encode()
    ).digest()
    rng = random.Random(int.from_bytes(digest_bytes, "big"))
    chosen_id = rng.choice(candidates)
    kit = library.get_kit(chosen_id)
    return {
        "player": player,
        "respawn_index": respawn_index,
        "kit_id": kit["id"],
        "kit_name": kit["name"],
        "previous_kit_id": prev,
        "random_rolled": True,
        "power_tier": kit["power"]["tier"],
        "power_score": kit["power"]["score"],
        "compiled": kit["compiled"],
    }


def default_library(path: Path | str | None = None) -> KitLibrary:
    """Return a fresh ``KitLibrary`` bound to ``path`` or ``DEFAULT_KITS_PATH``."""
    return KitLibrary(DEFAULT_KITS_PATH if path is None else Path(path))
