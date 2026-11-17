"""Which held blocks the body may put down to climb or bridge and take back later.

Scaffolding first, then dirt, then cobblestone, then any other held block that comes
back as itself when broken, is a full collision cube with no facing or half, does not
fall, and is spare beyond what the build still needs. Stone is never one: broken, it is
cobblestone, and a house wanting stone would be short.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

SCAFFOLDING = "minecraft:scaffolding"
FIRST = (SCAFFOLDING, "minecraft:dirt", "minecraft:cobblestone")
TIER = "iron"  # the body carries iron tools; what an iron tool's break drops
PLAIN_PROPERTIES = frozenset({"axis"})
# Plain cubes that drop themselves but must not be used up as footholds.
NEVER = ("_leaves", "ice", "shulker", "spawner", "bedrock", "_ore", "tnt")


def eligible(knowledge) -> frozenset[str]:
    """Every block item that is safe to place and break back: the static half of the
    choice, computed once per knowledge pack."""
    out = set(FIRST)
    for block, facts in knowledge.blocks.items():
        # full_cube: the real collision shape, dumped by the mod (absent: not trusted)
        if (
            facts.get("item") != block
            or facts.get("gravity")
            or not facts.get("full_cube")
        ):
            continue
        if set(facts.get("properties") or {}) - PLAIN_PROPERTIES:
            continue
        if any(word in block for word in NEVER):
            continue
        drops = knowledge.drops_for(block, TIER)
        if len(drops) != 1:
            continue
        drop = drops[0]
        if (drop.item, drop.min, drop.max, drop.chance) == (block, 1, 1, 1.0):
            out.add(block)
    return frozenset(out)


def order(
    held: Mapping[str, int],
    needed: Mapping[str, int],
    allowed: Iterable[str],
) -> list[str]:
    """The held temporary blocks, best first, each with some spare beyond ``needed``
    (what the build still has to place). Scaffolding, dirt and cobblestone lead in
    that order; the rest follow most spare first."""
    allowed = set(allowed)
    spare = {
        item: n - needed.get(item, 0)
        for item, n in held.items()
        if item in allowed and n - needed.get(item, 0) > 0
    }
    first = [i for i in FIRST if i in spare]
    rest = sorted((i for i in spare if i not in FIRST), key=lambda i: (-spare[i], i))
    return first + rest


def spare(held: Mapping[str, int], needed: Mapping[str, int], item: str) -> int:
    return max(0, held.get(item, 0) - needed.get(item, 0))
