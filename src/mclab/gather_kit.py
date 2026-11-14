"""The underground kit: what a player carries down a mine, and crafting it back below.

Runs 20261113T174328610209Z and 20261113T175858389732Z: one pickaxe per tier broke
~180 blocks down and the bot sat or climbed to craft. ``kit_targets`` adds spares to
the surface plan; ``craft_underground`` makes a pickaxe where it stands.
"""

from __future__ import annotations

from collections.abc import Mapping

from mclab.gather_options import GatherOptions
from mclab.gather_tasks import TIER_RANK, UNKNOWN_TIER_RANK, Gather
from mclab.knowledge import Knowledge

M = "minecraft:"
TABLE = M + "crafting_table"
LOG = M + "oak_log"
LADDER = ("wood", "stone", "iron", "diamond", "netherite")  # gold is no spare


def _rank(k: Knowledge, item: str) -> int | None:
    tool = k.items.get(item, {}).get("tool")
    if not tool or tool["kind"] != "pickaxe":
        return None
    return TIER_RANK.get(tool.get("tier", ""), UNKNOWN_TIER_RANK)


def pickaxe_for(k: Knowledge, need: int) -> str | None:
    """The lowest-tier pickaxe item that harvests drop-tier rank ``need``."""
    for tier in LADDER:
        if TIER_RANK[tier] < need:
            continue
        for item, info in k.items.items():
            tool = info.get("tool")
            if tool and tool["kind"] == "pickaxe" and tool.get("tier") == tier:
                return item
    return None


def kit_targets(
    tasks, held: Mapping[str, int], k: Knowledge, opts: GatherOptions
) -> dict[str, int]:
    """Extra goal items when ``tasks`` mine ore: a carried table, ``spare_pickaxes``
    of the lowest pickaxe that harvests it (better held count), ``carry_logs`` logs."""
    ores = [t for t in tasks if isinstance(t, Gather) and t.block.endswith("_ore")]
    if not ores:
        return {}
    need = max(TIER_RANK.get(t.min_tier, 0) for t in ores)
    out = {TABLE: 1}
    if pick := pickaxe_for(k, need):
        better = sum(
            n for i, n in held.items() if i != pick and (_rank(k, i) or -1) >= need
        )
        if opts.spare_pickaxes > better:
            out[pick] = opts.spare_pickaxes - better
    if opts.carry_logs > 0:
        logs = {i: n for i, n in held.items() if i.endswith("_log")}
        log = max(logs, key=logs.get) if logs else LOG
        out[log] = max(0, opts.carry_logs - (sum(logs.values()) - logs.get(log, 0)))
    return out


def craft_underground(ex, t) -> bool:
    """A tool broke where ``t`` is mined: craft the lowest tier that harvests it
    from the inventory (carried table, logs, cobblestone), taking the table back after.
    False: it cannot (the caller escapes upward)."""
    from mclab import gather_plan as gp

    k, body = ex.k, ex.body
    if (pick := pickaxe_for(k, TIER_RANK.get(t.min_tier, 0))) is None:
        return False
    # stations={}: the plan must place its own table, none of the far-away one
    steps = gp.plan({pick: 1}, body.inventory(), k, {}, with_chest=False).tasks
    allowed = (gp.Craft, gp.PlaceStation)
    if not steps or any(not isinstance(s, allowed) for s in steps):
        return False
    placed = None
    before = ex.state.stations.pop("crafting_table", None)
    try:
        for s in steps:
            if isinstance(s, gp.PlaceStation):
                res, placed = ex.place_free(M + s.kind, M + s.kind)
                if not res.ok:
                    return False
                ex.state.stations[s.kind] = placed
            elif not ex.craft(s).ok:
                return False
        return body.inventory().get(pick, 0) > 0
    finally:
        ex.state.stations.pop("crafting_table", None)
        if before is not None:
            ex.state.stations["crafting_table"] = before
        if placed is not None:
            res = ex.reach_and_do(placed, lambda: body.break_block(placed))
            if res.ok:
                ex.collect({TABLE}, placed)
