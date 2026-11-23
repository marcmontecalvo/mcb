"""Burrow: hide from mobs in a sealed hole (COMPANION_DEFENSE_PLAN section 4).

Dig the cell under the feet three times (the body drops each time), then place a
block in the cell above the head. ``plan_burrow`` checks first, from the known cells,
that the 1x2 pocket has solid walls and floor (no cave, fluid or open side a mob or
water could come through), that the digs are quick with the tools held, and that a
block can seal it (one held, or what the digs drop). ``dig_out`` breaks the seal;
getting out of the hole is then ordinary navigation.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from mclab.build_runner import Result

M = "minecraft:"
DEPTH = 3
BURROW_MAX_TICKS = 100  # all three digs: a mob is near, so only quick ground will do
DROP_WAIT_TICKS = 20  # after a dig, the body falls into the cell within this
FLUIDS = frozenset({M + "water", M + "lava", M + "flowing_water", M + "flowing_lava"})
AIR = M + "air"
OPEN = frozenset({AIR, M + "cave_air", M + "void_air"})  # nothing to clear
SIDES = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1))
# Seals a player would use first; any other full, non-falling block held comes next.
SEALS = (M + "dirt", M + "cobblestone", M + "cobbled_deepslate", M + "netherrack")
NOT_SEALS = frozenset(
    M + b
    for b in (
        "crafting_table furnace blast_furnace smoker chest trapped_chest barrel "
        "shulker_box ender_chest tnt"
    ).split()
)
# tool material -> (speed, drop tier)
MATERIALS = {
    "wooden": (2.0, "wood"),
    "golden": (12.0, "wood"),
    "stone": (4.0, "stone"),
    "copper": (5.0, "stone"),
    "iron": (6.0, "iron"),
    "diamond": (8.0, "diamond"),
    "netherite": (9.0, "netherite"),
}
TOOL_KINDS = ("pickaxe", "shovel", "axe", "hoe")


@dataclass(frozen=True)
class Burrow:
    digs: tuple  # cells to break, top first; the body drops into each
    tools: tuple  # the tool for each dig (None: by hand)
    seal: tuple  # the cell above the head, filled last
    item: str  # the block placed as the seal
    seal_tool: str | None  # to break the seal again
    feet: tuple  # where the body stands once dug in
    ticks: float  # estimated digging time
    # Broken first, with no drop: what fills the feet cell (snow, grass, a carpet)
    # has no collision but blocks the view of the block below, so its dig would fail.
    clear: tuple = ()


def plan_burrow(
    cells: Mapping, feet: tuple, inventory: Mapping[str, int], knowledge
) -> "Burrow | str":
    """The burrow from ``feet``, or why there is none here (a reason string)."""
    x, y, z = feet
    digs = tuple((x, y - i, z) for i in range(1, DEPTH + 1))
    pocket = ((x, y - DEPTH, z), (x, y - DEPTH + 1, z))
    floor = (x, y - DEPTH - 1, z)
    seal = digs[0]
    needed = [*digs, floor, *(_add(p, s) for p in (*pocket, seal) for s in SIDES)]
    if any(cells.get(p) is None for p in needed):
        return "unknown ground: cannot see what is under or beside the hole"
    if not all(_solid(cells[p]) for p in (*digs, floor)):
        return "open floor: a cave or fluid under the hole"
    if not all(_solid(cells[_add(p, s)]) for p in pocket for s in SIDES):
        return "open wall: a side of the hole is open or wet"
    if not any(_solid(cells[_add(seal, s)]) for s in SIDES):
        return "no seal: nothing beside the top of the hole to place a block against"
    here = cells.get(tuple(feet)) or {}
    if here.get("block") in FLUIDS:
        return "standing in water: a hole here would flood"
    clear = () if here.get("block", AIR) in OPEN else (tuple(feet),)
    tools, drops, total = [], [], 0.0
    for p in (*clear, *digs):
        block = cells[p]["block"]
        info = knowledge.blocks.get(block) or {}
        ticks, tool, tier = _dig(info, inventory)
        total += ticks
        if p in clear:
            continue  # its tool is picked again by the break op; its drop is no seal
        tools.append(tool)
        drops.extend(d.item for d in _drops(knowledge, block, tier))
    if total > BURROW_MAX_TICKS:
        return f"too slow to dig here ({total:.0f} ticks with the tools held)"
    item = _seal_item(inventory, drops, knowledge)
    if item is None:
        return "no seal: no block to close the hole with"
    _, seal_tool, _ = _dig(knowledge.blocks.get(item) or {}, inventory)
    return Burrow(digs, tuple(tools), seal, item, seal_tool, digs[-1], total, clear)


def dig_in(body, plan: Burrow) -> Result:
    """Dig down and seal the hole. Not ok: the reason (the caller decides again)."""
    for pos in plan.clear:
        result = body.break_block(pos, None)
        if not result.ok:
            return result
    for pos, tool in zip(plan.digs, plan.tools, strict=True):
        result = body.break_block(pos, tool)
        if not result.ok:
            return result
        if not _dropped_to(body, pos[1]):
            return Result(False, "did_not_drop")
    return body.place_block(plan.seal, plan.item, plan.item)


def dig_out(body, plan: Burrow) -> Result:
    return body.break_block(plan.seal, plan.seal_tool)


def _dropped_to(body, y: int) -> bool:
    for _ in range(0, DROP_WAIT_TICKS, 2):
        refresh = getattr(body, "observe", None)  # the real body reads a snapshot
        if refresh is not None:
            refresh()
        if body.position()[1] <= y:
            return True
        body.wait_ticks(2)
    return False


def _add(p, d):
    return (p[0] + d[0], p[1] + d[1], p[2] + d[2])


def _solid(cell) -> bool:
    return bool(cell.get("collision_boxes")) and cell.get("block") not in FLUIDS


def _dig(info: Mapping, inventory: Mapping[str, int]):
    """(ticks, tool, drop tier) to break a block of ``info`` with the best tool held."""
    hardness = float(info.get("hardness", 1.0))
    if hardness < 0:
        return float("inf"), None, None
    best = None
    for kind in info.get("mineable") or ():
        for material, (speed, tier) in MATERIALS.items():
            tool = f"{M}{material}_{kind}"
            if inventory.get(tool, 0) > 0 and (best is None or speed > best[0]):
                best = (speed, tool, tier)
    if best is not None:
        speed, tool, tier = best
        return hardness * 30 / speed, tool, tier
    factor = 100 if info.get("needs_correct_tool") else 30
    return hardness * factor, None, "hand"


def _drops(knowledge, block: str, tier: str | None):
    """What breaking ``block`` at ``tier`` drops: the best listed tier at or below."""
    if tier is None:
        return ()
    by_tier = knowledge.drops.get(block) or {}
    order = [t for t in knowledge.drop_tiers if t != "silk_touch"]
    for t in reversed(order[: order.index(tier) + 1] if tier in order else []):
        if t in by_tier:
            return by_tier[t]
    return ()


def _sealable(knowledge, item: str) -> bool:
    info = knowledge.blocks.get(item)
    return (
        info is not None
        and item not in NOT_SEALS
        and bool(info.get("full_cube"))
        and not info.get("gravity")
        and float(info.get("hardness", -1)) >= 0
    )


def _seal_item(inventory, drops, knowledge) -> str | None:
    held = [i for i, n in inventory.items() if n > 0 and _sealable(knowledge, i)]
    for item in SEALS:
        if item in held:
            return item
    if held:
        return sorted(held)[0]
    return next((d for d in drops if _sealable(knowledge, d)), None)
