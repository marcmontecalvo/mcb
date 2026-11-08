"""Tree Removal skill for the companion bot.

Fells trees obstructing building footprints or gathered for wood:
1. Identifies single-log-wide trees (trunk + branches + canopy).
2. Selects the best available axe from inventory (stone or better preferred).
3. Executes bottom-up felling algorithm:
   - Stands beside the trunk base.
   - Skips the very bottom log (Log 1).
   - Breaks logs 2, 3, 4 with the axe.
   - Steps/jumps onto Log 1 to gain elevation.
   - Breaks straight up to reach higher logs.
   - Pillars up as needed for tall trees and clears intervening leaves to reach branch logs.
   - Digs back down through any temporary pillar blocks back to Log 1.
   - Steps down to ground and breaks Log 1.
4. With all logs felled, canopy leaves decay on their own naturally.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Collection, Iterable

from mclab.build_runner import REACH, BuildBody, Result

Pos = tuple[int, int, int]

LOG_NAMES = frozenset(
    f"minecraft:{w}_{suffix}"
    for w in (
        "oak",
        "birch",
        "spruce",
        "jungle",
        "acacia",
        "dark_oak",
        "mangrove",
        "cherry",
        "pale_oak",
    )
    for suffix in ("log", "wood")
)

LEAF_NAMES = frozenset(
    f"minecraft:{w}_leaves"
    for w in (
        "oak",
        "birch",
        "spruce",
        "jungle",
        "acacia",
        "dark_oak",
        "mangrove",
        "cherry",
        "pale_oak",
        "azalea",
        "flowering_azalea",
    )
)

GROUND_BLOCKS = frozenset(
    {
        "minecraft:grass_block",
        "minecraft:dirt",
        "minecraft:coarse_dirt",
        "minecraft:podzol",
        "minecraft:rooted_dirt",
        "minecraft:mud",
        "minecraft:moss_block",
    }
)

AXE_TIERS = (
    "minecraft:netherite_axe",
    "minecraft:diamond_axe",
    "minecraft:iron_axe",
    "minecraft:stone_axe",
    "minecraft:golden_axe",
    "minecraft:wooden_axe",
)

TEMP_PILLAR_BLOCKS = ("minecraft:dirt", "minecraft:scaffolding", "minecraft:cobblestone")


def is_log(state: str | None) -> bool:
    if not state:
        return False
    block = state.split("[", 1)[0]
    return block in LOG_NAMES


def is_leaf(state: str | None) -> bool:
    if not state:
        return False
    block = state.split("[", 1)[0]
    return block in LEAF_NAMES


def best_axe(inventory: dict[str, int]) -> str | None:
    """Return the best axe held in inventory, prioritizing stone or better."""
    for axe in AXE_TIERS:
        if inventory.get(axe, 0) > 0:
            return axe
    return None


@dataclass
class Tree:
    base: Pos  # The very bottom log of the trunk (standing on ground)
    trunk: list[Pos]  # Vertical trunk logs from base up [base, base+1, ...]
    branches: list[Pos] = field(default_factory=list)  # Any non-vertical branch logs
    leaves: set[Pos] = field(default_factory=set)  # Canopy leaf cells

    @property
    def all_logs(self) -> set[Pos]:
        return set(self.trunk) | set(self.branches)


def find_trees(world, search_cells: Iterable[Pos]) -> list[Tree]:
    """Identify single-log-wide trees whose logs or canopy intersect search_cells."""
    seen_logs: set[Pos] = set()
    trees: list[Tree] = []

    # Find candidate log cells
    candidate_logs: set[Pos] = set()
    for pos in search_cells:
        st = world(*pos)
        if is_log(st):
            candidate_logs.add(pos)

    for log in candidate_logs:
        if log in seen_logs:
            continue

        # Trace down to ground to find the trunk base
        x, y, z = log
        curr_y = y
        while is_log(world(x, curr_y - 1, z)):
            curr_y -= 1
        base = (x, curr_y, z)

        # Check if the block below base is ground or solid
        below = world(x, curr_y - 1, z)
        # Note: In natural terrain, tree base sits on ground blocks
        if below and (below.split("[", 1)[0] in GROUND_BLOCKS or "dirt" in below or "stone" in below or "grass" in below):
            # Trace trunk straight up from base
            trunk: list[Pos] = []
            ty = curr_y
            while is_log(world(x, ty, z)):
                p = (x, ty, z)
                trunk.append(p)
                seen_logs.add(p)
                ty += 1

            # Discover connected branch logs (within Chebyshev distance 1 or 2)
            branches: list[Pos] = []
            queue = deque(trunk)
            tree_logs = set(trunk)

            while queue:
                cx, cy, cz = queue.popleft()
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            if dx == 0 and dy == 0 and dz == 0:
                                continue
                            npos = (cx + dx, cy + dy, cz + dz)
                            if npos not in tree_logs and is_log(world(*npos)):
                                # Ensure it's part of this tree canopy (within radius 6 of trunk)
                                if abs(npos[0] - x) <= 5 and abs(npos[2] - z) <= 5:
                                    tree_logs.add(npos)
                                    seen_logs.add(npos)
                                    branches.append(npos)
                                    queue.append(npos)

            # Discover associated canopy leaves (within 4 blocks of any log)
            canopy: set[Pos] = set()
            for lx, ly, lz in tree_logs:
                for dx in range(-4, 5):
                    for dy in range(-4, 5):
                        for dz in range(-4, 5):
                            if abs(dx) + abs(dy) + abs(dz) <= 5:
                                leaf_pos = (lx + dx, ly + dy, lz + dz)
                                if is_leaf(world(*leaf_pos)):
                                    canopy.add(leaf_pos)

            trees.append(Tree(base=base, trunk=trunk, branches=branches, leaves=canopy))

    return trees


def _dist2(a: Pos, b: Pos) -> int:
    return sum((p - q) ** 2 for p, q in zip(a, b, strict=True))


class TreeFeller:
    """Executes tree removal using bottom-up chopping with stepping on Log 1."""

    def __init__(self, body: BuildBody):
        self.body = body

    def fell(self, tree: Tree, axe: str | None = None) -> bool:
        """Fell `tree` using the specified or best available axe."""
        inv = self.body.inventory()
        chosen_axe = axe or best_axe(inv)
        if chosen_axe and hasattr(self.body, "equip"):
            self.body.equip(chosen_axe)

        bx, by, bz = tree.base
        trunk = list(tree.trunk)
        if not trunk:
            return True

        # Step 1: Approach the trunk - stand on ground adjacent to trunk base
        here = self.body.position()
        stances = [
            (bx + dx, by, bz + dz)
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1))
        ]
        stances.sort(key=lambda s: _dist2(s, here))

        arrived = False
        for s in stances:
            if hasattr(self.body, "walk_to"):
                if self.body.walk_to(s, radius=1.0):
                    arrived = True
                    break
            elif self.body.goto_stance(tree.base, reach=REACH):
                arrived = True
                break

        if not arrived and not self.body.goto_stance(tree.base, reach=REACH):
            return False

        # Step 2: SKIP the very bottom log (trunk[0]).
        # Break logs 2, 3, 4 (indices 1, 2, 3) from ground level.
        lower_logs = trunk[1:4]
        for log_pos in lower_logs:
            if is_log(self.body.world(*log_pos)):
                self.body.break_block(log_pos, tool=chosen_axe)

        # Step 3: Jump / step onto Log 1 (tree.base)
        # Position on top of Log 1 is (bx, by + 1, bz)
        log1_stance = (bx, by + 1, bz)
        stepped_on_log1 = False
        if hasattr(self.body, "walk_to"):
            stepped_on_log1 = self.body.walk_to(log1_stance, radius=0.6)
        if not stepped_on_log1:
            stepped_on_log1 = self.body.goto_stance(log1_stance, reach=REACH)

        # Step 4: From standing on Log 1, break straight up
        remaining_trunk = trunk[4:]
        pillars_placed: list[Pos] = []

        for log_pos in remaining_trunk:
            if not is_log(self.body.world(*log_pos)):
                continue

            # Check if reachable from current position
            cur_pos = self.body.position()
            dy = log_pos[1] - cur_pos[1]
            if dy > 3:  # Too high to reach comfortably, pillar up
                pillar_pos = (cur_pos[0], cur_pos[1], cur_pos[2])
                pillar_item = next(
                    (it for it in TEMP_PILLAR_BLOCKS if self.body.inventory().get(it, 0) > 0),
                    None,
                )
                if pillar_item:
                    # Place pillar block and step up
                    self.body.place_block(pillar_pos, pillar_item, pillar_item)
                    pillars_placed.append(pillar_pos)
                    next_stand = (cur_pos[0], cur_pos[1] + 1, cur_pos[2])
                    if hasattr(self.body, "walk_to"):
                        self.body.walk_to(next_stand, radius=0.5)

            # Break log
            self.body.break_block(log_pos, tool=chosen_axe)

        # Step 5: Chop any branch logs in the canopy
        for blog in sorted(tree.branches, key=lambda b: (b[1], _dist2(b, tree.base))):
            if not is_log(self.body.world(*blog)):
                continue

            # If leaves block direct reach or line of sight, clear 1-2 adjacent leaves
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        leaf_check = (blog[0] + dx, blog[1] + dy, blog[2] + dz)
                        if is_leaf(self.body.world(*leaf_check)):
                            self.body.break_block(leaf_check)
                            break

            # Reach and break branch log
            if self.body.goto_stance(blog, reach=REACH):
                self.body.break_block(blog, tool=chosen_axe)

        # Step 6: Dig back down through any temporary pillar blocks
        for ppos in reversed(pillars_placed):
            if self.body.world(*ppos) not in (None, "minecraft:air"):
                self.body.break_block(ppos)

        # Step 7: Step down from Log 1 to ground and break Log 1
        ground_stance = stances[0] if stances else (bx + 1, by, bz)
        if hasattr(self.body, "walk_to"):
            self.body.walk_to(ground_stance, radius=1.0)
        else:
            self.body.goto_stance(tree.base, reach=REACH)

        # Remove the bottom log block (Log 1)
        if is_log(self.body.world(*tree.base)):
            self.body.break_block(tree.base, tool=chosen_axe)

        # Collect drops if supported
        if hasattr(self.body, "collect_drops"):
            self.body.collect_drops([tree.base])

        return True


def clear_trees_in_footprint(body: BuildBody, footprint: Collection[Pos]) -> int:
    """Find and fell all trees intersecting footprint. Leaves decay automatically."""
    trees = find_trees(body.world, footprint)
    feller = TreeFeller(body)
    cleared = 0
    for tree in trees:
        if feller.fell(tree):
            cleared += 1
    return cleared
