# ruff: noqa: E501
"""Gather run options and tool/ore choice from knowledge (per-bot profiles come later)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from mclab.gather_tasks import TIER_RANK, UNKNOWN_TIER_RANK
from mclab.knowledge import Knowledge

SENSE_RADIUS = 64  # the sensor delivers whole sections this far across
SENSE_DY = 48  # and this far above and below
NO_HARVEST = 99


@dataclass(frozen=True)
class GatherOptions:
    tool_choice: str = "lowest_sufficient"  # | "best"
    ore_collect: str = "greedy"  # | "specific" (greedy: any ore the held pickaxes harvest)
    ore_vision: bool = True  # buried ore within vision_radius counts (x-ray sensing)
    vision_radius: int = 32
    search_radius: int | None = None  # None: unlimited within sensing
    strip_leg: int = 64  # ore vision off: strip-mine legs along a chunk border
    strip_turn: int = 16  # the connector between legs (to the next chunk border)
    strip_max_legs: int | None = 8  # None: unlimited
    spare_pickaxes: int = 3  # underground kit: pickaxes of the ore tier (gather_kit)
    carry_logs: int = 16  # logs for planks and sticks below (0: no kit)

    @property
    def radius(self) -> int:
        """Horizontal reach for ore, bounded by sensing, vision and search."""
        r = min(self.vision_radius, SENSE_RADIUS)
        return r if self.search_radius is None else min(r, self.search_radius)


def harvest_rank(k: Knowledge, block: str) -> int:
    """Lowest tool tier rank whose break drops the block's item (0: hand)."""
    for tier in k.drop_tiers:
        if tier != "silk_touch" and k.drops_for(block, tier):
            return 0 if tier == "hand" else TIER_RANK.get(tier, UNKNOWN_TIER_RANK)
    return NO_HARVEST


def _tools(k: Knowledge, inventory, kind: str):
    for item, n in inventory.items():
        tool = k.items.get(item, {}).get("tool")
        if n > 0 and tool and tool["kind"] == kind:
            yield item, TIER_RANK.get(tool.get("tier", ""), UNKNOWN_TIER_RANK)


def pick_tool(k: Knowledge, inventory, block: str, mode: str) -> str | None:
    """The held tool to break ``block`` with: lowest tier that harvests it, or the
    best. None: no kind known or none able (the body then picks as before)."""
    kind = (k.blocks.get(block, {}).get("mineable") or [None])[0]
    need = harvest_rank(k, block)
    able = [(r, i) for i, r in _tools(k, inventory, kind) if r >= need] if kind else []
    if not able:
        return None
    return (max(able) if mode == "best" else min(able))[1]


def wanted_ores(
    task, inventory, k: Knowledge, opts: GatherOptions, needed: Collection[str] = ()
) -> list[str]:
    """Ore blocks to mine: the task's block, plus (greedy) every ``*_ore`` block a
    held pickaxe can harvest, or (specific) the ``needed`` ores of the current plan
    (coal for a smelt; run 20261113T175858389732Z never mined any)."""
    best = max((r for _, r in _tools(k, inventory, "pickaxe")), default=-1)
    if opts.ore_collect != "greedy":
        more = {b for b in needed if b != task.block and harvest_rank(k, b) <= best}
        return [task.block, *sorted(more)]
    ores = [
        b
        for b in k.blocks
        if b.endswith("_ore") and b != task.block and harvest_rank(k, b) <= best
    ]
    return [task.block, *sorted(ores)]
