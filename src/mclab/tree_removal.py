"""Tree Removal skill for the companion bot.

Fells trees obstructing building footprints or gathered for wood:
1. Identifies single-log-wide trees (trunk + branches + canopy) intersecting
   the footprint or its immediate perimeter.
2. Selects the best available axe from inventory (stone or better preferred).
3. Approaches the trunk at ground level (never standing on top of the canopy).
4. Bottom-up felling algorithm:
   - Stands on ground adjacent to trunk base.
   - Skips the very bottom log (Log 1).
   - If the tree is short (<= 5 logs tall), chops all logs 2..N directly from the ground with the axe.
   - If any leaf blocks line of sight, clears the leaf and breaks the log.
   - For taller trees (> 5 logs tall where top logs exceed ground reach):
     * Breaks logs 2, 3, 4 from ground level.
     * Steps/jumps onto Log 1 to gain elevation.
     * Breaks straight up to reach higher logs.
     * Pillars up as needed for very tall trees and clears branch logs.
     * Digs back down through pillar blocks to Log 1.
     * Steps down to ground.
   - Once all logs in the trunk and canopy above Log 1 are removed, breaks Log 1 last.
5. With all logs felled, canopy leaves decay on their own naturally.
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

TEMP_PILLAR_BLOCKS = ("minecraft:scaffolding", "minecraft:dirt", "minecraft:cobblestone")
FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


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


def find_trees(
    world,
    search_cells: Iterable[Pos],
    margin: int = 3,
    template=None,
) -> list[Tree]:
    """Identify single-log-wide trees whose logs or canopy intersect search_cells or its margin."""
    cells_list = list(search_cells)
    if not cells_list:
        return []

    def is_template_block(p: Pos) -> bool:
        if template is None:
            return False
        target = template.cells.get(p)
        if target is None:
            return False
        curr = world(*p)
        if curr is None:
            return False
        return curr == target or curr.split("[", 1)[0] == target.split("[", 1)[0]

    xs = [p[0] for p in cells_list]
    ys = [p[1] for p in cells_list]
    zs = [p[2] for p in cells_list]
    x_min, x_max = min(xs) - margin, max(xs) + margin
    y_min, y_max = min(ys) - 1, max(ys) + margin
    z_min, z_max = min(zs) - margin, max(zs) + margin

    candidate_logs: set[Pos] = set()

    # 1. Search for logs in search_cells and the expanded bounding box
    for p in cells_list:
        if is_log(world(*p)) and not is_template_block(p):
            candidate_logs.add(p)

    for x in range(x_min, x_max + 1):
        for z in range(z_min, z_max + 1):
            for y in range(y_min, y_max + 1):
                p = (x, y, z)
                if is_log(world(*p)) and not is_template_block(p):
                    candidate_logs.add(p)
                elif is_leaf(world(*p)):
                    # Look adjacent/downwards for connecting trunk logs within radius 3
                    for dx in range(-3, 4):
                        for dz in range(-3, 4):
                            for dy in range(-4, 2):
                                lp = (x + dx, y + dy, z + dz)
                                if is_log(world(*lp)) and not is_template_block(lp):
                                    candidate_logs.add(lp)

    seen_logs: set[Pos] = set()
    trees: list[Tree] = []

    for log in sorted(candidate_logs, key=lambda p: (p[0], p[2], p[1])):
        if log in seen_logs:
            continue

        # Trace down to ground to find the trunk base
        x, y, z = log
        curr_y = y
        while is_log(world(x, curr_y - 1, z)) and not is_template_block((x, curr_y - 1, z)):
            curr_y -= 1
        base = (x, curr_y, z)
        if is_template_block(base):
            continue
        below = world(x, curr_y - 1, z)

        # Tree base sits at lowest connected trunk log
        if not is_log(below):
            # Trace trunk straight up from base
            trunk: list[Pos] = []
            ty = curr_y
            while is_log(world(x, ty, z)) and not is_template_block((x, ty, z)):
                p = (x, ty, z)
                trunk.append(p)
                seen_logs.add(p)
                ty += 1

            if not trunk:
                continue

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
                            if (
                                npos not in tree_logs
                                and is_log(world(*npos))
                                and not is_template_block(npos)
                            ):
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

            if template is not None and not canopy:
                continue

            trees.append(Tree(base=base, trunk=trunk, branches=branches, leaves=canopy))

    return trees


def _dist2(a: Pos, b: Pos) -> int:
    return sum((p - q) ** 2 for p, q in zip(a, b, strict=True))


class TreeFeller:
    """Executes tree removal using bottom-up chopping with stepping on Log 1."""

    def __init__(self, body: BuildBody, template=None):
        self.body = body
        self.template = template

    def _clear_blocking_leaves(self, eye: tuple[float, float, float], target: tuple[float, float, float]) -> bool:
        """Trace ray from eye to target center and break any intervening leaves."""
        dist = math.dist(eye, target)
        steps = max(1, math.ceil(dist / 0.1))
        cleared = False
        target_pos = (math.floor(target[0]), math.floor(target[1]), math.floor(target[2]))
        for i in range(steps):
            p = (
                math.floor(eye[0] + (target[0] - eye[0]) * i / steps),
                math.floor(eye[1] + (target[1] - eye[1]) * i / steps),
                math.floor(eye[2] + (target[2] - eye[2]) * i / steps),
            )
            if p == target_pos:
                break
            if is_leaf(self.body.world(*p)):
                leaf_center = (p[0] + 0.5, p[1] + 0.5, p[2] + 0.5)
                if math.dist(eye, leaf_center) <= 4.2:
                    self.body.break_block(p)
                    cleared = True
        return cleared

    def _carve_corridor(self, start: Pos, goal: Pos, trunk_base: Pos) -> None:
        """Break leaves in a 1-wide, 2-high line from start to goal and around stance."""
        sx, sy, sz = start
        gx, gy, gz = goal
        dist = math.hypot(gx - sx, gz - sz)
        steps = max(1, math.ceil(dist / 0.5))
        for i in range(steps + 1):
            t = i / steps
            x = math.floor(sx + (gx - sx) * t)
            z = math.floor(sz + (gz - sz) * t)
            y_base = math.floor(sy + (gy - sy) * t)
            for dy in (0, 1, 2):
                p = (x, y_base + dy, z)
                if is_leaf(self.body.world(*p)):
                    self.body.break_block(p)

        # Clear stance volume (feet, torso, head)
        for dy in (0, 1, 2):
            sp = (gx, gy + dy, gz)
            if is_leaf(self.body.world(*sp)):
                self.body.break_block(sp)

        # Clear leaves between ground_stance and trunk base / logs 2..4
        bx, by, bz = trunk_base
        eye = (gx + 0.5, gy + 1.62, gz + 0.5)
        for dy in range(5):
            tp = (bx + 0.5, by + dy + 0.5, bz + 0.5)
            self._clear_blocking_leaves(eye, tp)

    def _break_log(self, pos: Pos, tool: str | None) -> bool:
        """Break a log block, clearing any blocking leaves if line of sight is obstructed."""
        if not is_log(self.body.world(*pos)):
            return True

        if self.template is not None:
            target = self.template.cells.get(pos)
            if target is not None:
                curr = self.body.world(*pos)
                if curr and (curr == target or curr.split("[", 1)[0] == target.split("[", 1)[0]):
                    return False

        here = self.body.position()
        eye = (here[0] + 0.5, here[1] + 1.62, here[2] + 0.5)
        target = (pos[0] + 0.5, pos[1] + 0.5, pos[2] + 0.5)
        self._clear_blocking_leaves(eye, target)

        for _ in range(3):
            res = self.body.break_block(pos, tool=tool)
            if res.ok:
                return True
            if res.reason == "no_line_of_sight":
                self._clear_blocking_leaves(eye, target)
                for dx, dy, dz in FACES:
                    adj = (pos[0] + dx, pos[1] + dy, pos[2] + dz)
                    if is_leaf(self.body.world(*adj)):
                        self.body.break_block(adj)
            elif res.reason == "out_of_reach":
                return False
        return not is_log(self.body.world(*pos))

    def _ground_stance(self, tree_base: Pos) -> Pos:
        """Find an adjacent ground-level stance around tree base (NEVER on top of canopy)."""
        bx, by, bz = tree_base
        here = self.body.position()
        candidates: list[Pos] = []
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            for gy in (by, by - 1, by + 1):
                candidates.append((bx + dx, gy, bz + dz))
        candidates.sort(key=lambda s: _dist2(s, here))

        for cand in candidates:
            cand_y = cand[1]
            feet = self.body.world(cand[0], cand_y, cand[2])
            if feet in (None, "minecraft:air") or is_leaf(feet):
                return cand
        return candidates[0] if candidates else (bx + 1, by, bz)

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

        # Step 1: Approach the trunk - break through leaves in a straight line to stance
        ground_stance = self._ground_stance(tree.base)
        here = self.body.position()
        self._carve_corridor(here, ground_stance, tree.base)

        if hasattr(self.body, "walk_to"):
            self.body.walk_to(ground_stance, radius=0.8)
        else:
            self.body.goto_stance(ground_stance, reach=1.0)

        # Ensure bot is at ground level adjacent to tree base (not on top of canopy)
        cur = self.body.position()
        if cur[1] > by + 2:
            if hasattr(self.body, "walk_to"):
                self.body.walk_to(ground_stance, radius=0.6)
            else:
                self.body.goto_stance(ground_stance, reach=1.0)

        # Step 2: SKIP the very bottom log (trunk[0], Log 1).
        # Break logs from ground level:
        here = self.body.position()
        eye_y = here[1] + 1.62
        broken_from_ground: list[Pos] = []
        unreached: list[Pos] = []

        for log_pos in trunk[1:]:
            dist = math.dist((here[0] + 0.5, eye_y, here[2] + 0.5), (log_pos[0] + 0.5, log_pos[1] + 0.5, log_pos[2] + 0.5))
            if dist <= 4.2:
                if self._break_log(log_pos, tool=chosen_axe):
                    broken_from_ground.append(log_pos)
                else:
                    unreached.append(log_pos)
            else:
                unreached.append(log_pos)

        # Step 3: If there are unreached higher logs, step/jump onto Log 1
        pillars_placed: list[Pos] = []
        if unreached:
            log1_stance = (bx, by + 1, bz)
            if hasattr(self.body, "walk_to"):
                self.body.walk_to(log1_stance, radius=0.6)
            else:
                self.body.goto_stance(log1_stance, reach=1.0)

            # From Log 1, break straight up
            for log_pos in list(unreached):
                cur_pos = self.body.position()
                dist = math.dist((cur_pos[0] + 0.5, cur_pos[1] + 1.62, cur_pos[2] + 0.5), (log_pos[0] + 0.5, log_pos[1] + 0.5, log_pos[2] + 0.5))
                if dist > 4.0:
                    # Pillar up
                    pillar_pos = (cur_pos[0], cur_pos[1], cur_pos[2])
                    pillar_item = next(
                        (it for it in TEMP_PILLAR_BLOCKS if self.body.inventory().get(it, 0) > 0),
                        None,
                    )
                    if pillar_item:
                        self.body.place_block(pillar_pos, pillar_item, pillar_item)
                        pillars_placed.append(pillar_pos)
                        next_stand = (cur_pos[0], cur_pos[1] + 1, cur_pos[2])
                        if hasattr(self.body, "walk_to"):
                            self.body.walk_to(next_stand, radius=0.5)
                if self._break_log(log_pos, tool=chosen_axe):
                    unreached.remove(log_pos)

        # Step 4: Chop any branch logs in the canopy
        for blog in sorted(tree.branches, key=lambda b: (b[1], _dist2(b, tree.base))):
            if not is_log(self.body.world(*blog)):
                continue
            if self.body.goto_stance(blog, reach=REACH):
                self._break_log(blog, tool=chosen_axe)

        # Step 5: Dig back down through any temporary pillar blocks
        for ppos in reversed(pillars_placed):
            if self.body.world(*ppos) not in (None, "minecraft:air"):
                self.body.break_block(ppos)

        # Step 6: Step down from Log 1 to ground
        if hasattr(self.body, "walk_to"):
            self.body.walk_to(ground_stance, radius=1.0)
        else:
            self.body.goto_stance(ground_stance, reach=1.0)

        # Step 7: Remove the bottom log block (Log 1) LAST with the axe ONLY IF all upper logs are felled!
        remaining_upper = [p for p in trunk[1:] + tree.branches if is_log(self.body.world(*p))]
        if not remaining_upper and is_log(self.body.world(*tree.base)):
            self._break_log(tree.base, tool=chosen_axe)

        # Collect drops if supported
        if hasattr(self.body, "collect_drops"):
            self.body.collect_drops([tree.base])

        return True


def clear_trees_in_footprint(
    body: BuildBody,
    footprint: Collection[Pos],
    template=None,
) -> int:
    """Find and fell all trees intersecting footprint. Leaves decay automatically."""
    trees = find_trees(body.world, footprint, template=template)
    feller = TreeFeller(body, template=template)
    cleared = 0
    for tree in trees:
        if feller.fell(tree):
            cleared += 1
    return cleared
