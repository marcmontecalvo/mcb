"""Tree removal: find single-log-wide trees on a footprint and fell them (see
TreeFeller for the method). Canopy leaves decay on their own."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field

from mclab.build_runner import REACH, BuildBody, Result
from mclab.progress_watch import no_placing

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

TEMP_PILLAR_BLOCKS = (
    "minecraft:scaffolding",
    "minecraft:dirt",
    "minecraft:cobblestone",
)
MAX_OCCLUDERS = 6  # occluders broken for one log before giving up on it
APPROACH_REACH = 3.0  # close enough that the two bottom logs are in plain view
COLUMN_RADIUS = 0.3
LOW = 4  # a log this far above the base is reached from a ground stance
CLIMB_BELOW = 4  # feet this far under the lowest log left: it is in reach
MAX_CLIMBS = 4
LEAF_TRIES = 2  # failed breaks of one leaf from one stance before it is left
TREE_PASSES = 2  # inside the trunk cell, so the log overhead is straight up
SOFT_NAMES = frozenset(
    "minecraft:" + n
    for n in (
        "vine",
        "short_grass",
        "tall_grass",
        "fern",
        "large_fern",
        "dead_bush",
        "glow_lichen",
        "snow",
        "sugar_cane",
        "brown_mushroom",
        "red_mushroom",
        "blue_orchid",
        "dandelion",
        "poppy",
        "cocoa",
        "moss_carpet",
        "lily_pad",
    )
)
FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def is_log(state: str | None) -> bool:
    if not state:
        return False
    block = state.split("[", 1)[0]
    return block in LOG_NAMES


def is_soft(state: str | None) -> bool:
    """Foliage, vines, small plants and logs: what may be cut to see a log."""
    block = (state or "").split("[", 1)[0]
    return block in SOFT_NAMES or block in LEAF_NAMES or block in LOG_NAMES


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
    leafless: bool = False,
) -> list[Tree]:
    """Identify single-log-wide trees whose logs or canopy meet search_cells (+margin).

    With a template a log stand with no canopy is taken for a build and skipped;
    ``leafless`` keeps it (site prep clears every non-template log anyway, and the
    leaves of a half-felled tree are the first thing to go)."""
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
        while is_log(world(x, curr_y - 1, z)) and not is_template_block(
            (x, curr_y - 1, z)
        ):
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

            if template is not None and not canopy and not leafless:
                continue

            trees.append(Tree(base=base, trunk=trunk, branches=branches, leaves=canopy))

    return trees


def _dist2(a: Pos, b: Pos) -> int:
    return sum((p - q) ** 2 for p, q in zip(a, b, strict=True))


class TreeFeller:
    """Fells one tree by the column method: from a stance beside the trunk break the
    logs bottom-up, then stand where the trunk was and break straight up.

    The earlier method (skip the bottom log, climb onto it, pillar) left half-felled
    trees in live run 20261110T003408754165Z: the climb was a two-block step, the
    stance chooser accepted unseen underground cells (the body dug down to one), and
    every refused break was no_line_of_sight behind vines or leaves it never cleared."""

    def __init__(self, body: BuildBody, template=None):
        self.body = body
        self.template = template
        # failed breaks per (leaf, stance). Keyed by stance and counted: a leaf
        # refused once, or from one spot, is tried again, not given up for the run.
        self._failed: dict[tuple[Pos, Pos], int] = {}

    def _eye(self) -> tuple[float, float, float]:
        x, y, z = self.body.position()
        return (x + 0.5, y + 1.62, z + 0.5)

    def _protected(self, pos: Pos) -> bool:
        """A cell the template already holds as built."""
        if self.template is None:
            return False
        want, have = self.template.cells.get(pos), self.body.world(*pos)
        return bool(want and have and want.split("[", 1)[0] == have.split("[", 1)[0])

    def _try_break(self, pos: Pos) -> bool:
        """One break attempt per cell, and only for a cell within reach of the eye."""
        key = (pos, self.body.position())
        centre = (pos[0] + 0.5, pos[1] + 0.5, pos[2] + 0.5)
        if self._failed.get(key, 0) >= LEAF_TRIES:
            return False
        if math.dist(self._eye(), centre) > REACH:
            return False  # too far from here says nothing about the next stance
        if not self.body.break_block(pos).ok:
            self._failed[key] = self._failed.get(key, 0) + 1
            return False
        return True

    def _clear_blocking_leaves(self, eye, target) -> bool:
        """Break the leaves on the ray from eye to target (bodies that do not name
        the occluder). True if any broke."""
        steps = max(1, math.ceil(math.dist(eye, target) / 0.1))
        goal = tuple(math.floor(c) for c in target)
        cleared, seen = False, set()
        for i in range(steps):
            p = tuple(
                math.floor(e + (t - e) * i / steps)
                for e, t in zip(eye, target, strict=True)
            )
            if p == goal:
                break
            if p not in seen and is_leaf(self.body.world(*p)):
                seen.add(p)
                cleared = self._try_break(p) or cleared
        return cleared

    def _clear_view(self, pos: Pos, refused: Result) -> bool:
        """Break what hides ``pos``. The body names the occluder; only soft blocks
        (foliage, vines, plants, logs) that the template does not hold are broken."""
        data = refused.data or {}
        blocker = data.get("blocker")
        if blocker is None:
            centre = (pos[0] + 0.5, pos[1] + 0.5, pos[2] + 0.5)
            return self._clear_blocking_leaves(self._eye(), centre)
        blocker = tuple(blocker)
        # the server names what it saw; the map may be stale
        state = data.get("blocker_state") or self.body.world(*blocker)
        if not is_soft(state) or self._protected(blocker):
            return False
        return self.body.break_block(blocker).ok

    def _break_log(self, pos: Pos, tool: str | None) -> bool:
        """True once ``pos`` holds no log. Bounded: each retry follows one occluder
        actually broken."""
        if not is_log(self.body.world(*pos)):
            return True
        if self._protected(pos):
            return False
        for attempt in range(MAX_OCCLUDERS + 1):
            result = self.body.break_block(pos, tool)
            if result.ok:
                return True
            # a mob or player on the ray (the body gave up within ~1 s): it moves,
            # so try again, twice at most to keep the body from standing >= 5 s
            if result.reason == "entity_in_way" and attempt < 2:
                continue
            if result.reason != "no_line_of_sight" or not self._clear_view(pos, result):
                return False
        return False

    def _standing(self, tree: Tree) -> list[Pos]:
        logs = sorted(tree.all_logs, key=lambda p: p[1])
        return [p for p in logs if is_log(self.body.world(*p))]

    def fell(self, tree: Tree, axe: str | None = None) -> bool:
        """Remove every log of ``tree``. True only if none is left."""
        body = self.body
        axe = axe or best_axe(body.inventory())
        if axe:
            body.equip(axe)
        # 1. Beside the trunk, on a stance the map knows.
        with no_placing(body):
            near = body.goto_stance(tree.base, APPROACH_REACH)
            walk = getattr(body, "walk_to", None)
            if not near and walk is not None:
                # ponytail: a tree leafy down to the ground has no stance with a
                # view; walk as close as the leaves allow and cut in from there
                # (the feller sent no break at all for two such trees, run 32)
                for radius in (2.5, 3.5, 4.5):
                    if walk(tree.base, radius):
                        break
        # No stance found is not a reason to leave: the logs in reach are still
        # broken (run 20261110T021445878238Z left whole trunks untouched).
        # 2. Bottom-up from the ground, every log in reach (trunk and branches). A
        #    log that stays hidden does not stop the ones above it: it was one more
        #    leaf from opening the whole trunk in the live runs.
        self._break_up(tree, axe)
        # 3. Stand where the trunk was and break straight up: each break uncovers
        #    the next log, and reach covers the trunk to base+6.
        left = self._standing(tree)
        if (
            left
            and left[0][1] >= tree.base[1] + 2
            and self._into_column(tree)
        ):
            self._break_up(tree, axe)
            self._climb(tree, axe)
        # 4. Low leftovers (a branch, a base log the first stance hid): one stance
        #    and one try each. Higher ones are never chased with goto_stance: its
        #    planner pillars cost four attempts and 620 s for one log and were left
        #    standing (run 20261110T011637401098Z).
        for log in self._standing(tree):
            if log[1] > tree.base[1] + LOW:
                continue
            # ponytail: break whatever is in reach whether or not a stance was
            # found, and plan no route when the log already is: the body stood 3 m
            # from four logs after 16 failed plans and broke none
            # (run 20261110T164401279123Z)
            if not self._in_reach(log):
                with no_placing(body):
                    body.goto_stance(log, REACH)
            if self._in_reach(log):
                self._break_log(log, axe)
        if hasattr(body, "collect_drops"):
            body.collect_drops(tree.all_logs)
        return not self._standing(tree)

    def _into_column(self, tree: Tree) -> bool:
        walk = getattr(self.body, "walk_to", None)
        if walk is None:
            return False
        with no_placing(self.body):
            return walk(tree.base, COLUMN_RADIUS)

    def _break_up(self, tree: Tree, axe: str | None) -> None:
        """Every standing log within reach of the eye, lowest first."""
        for log in self._standing(tree):
            if self._in_reach(log):
                self._break_log(log, axe)

    def _in_reach(self, log: Pos) -> bool:
        eye = self._eye()  # reach is to the nearest point of the block
        near = [min(max(e, c), c + 1) for e, c in zip(eye, log, strict=True)]
        return math.dist(eye, near) <= REACH

    def _climb(self, tree: Tree, axe: str | None) -> None:
        """Logs higher than reach (trunk or branch): pillar straight up the emptied
        trunk column, just under the lowest one left, break what is in reach, then
        take the pillar down again by breaking the block under the feet. Bounded by
        MAX_CLIMBS."""
        body, (bx, by, bz) = self.body, tree.base
        climb = getattr(body, "dig_to", None)
        for _ in range(MAX_CLIMBS):
            left = self._standing(tree)
            if not left or climb is None:
                break
            feet = (bx, left[0][1] - CLIMB_BELOW, bz)
            if feet[1] <= body.position()[1] or not climb(feet):
                break
            self._break_up(tree, axe)
        # Down again: each break drops the body one block.
        for _ in range(MAX_CLIMBS * 8):
            x, y, z = body.position()
            under = (x, y - 1, z)
            if y <= by or (x, z) != (bx, bz) or is_log(body.world(*under)):
                break
            if not body.break_block(under).ok:
                break


def trees_in_the_way(
    world, footprint: Collection[Pos], template=None, leafless: bool = False
) -> dict[Pos, Tree]:
    """Trees with any log or leaf in ``footprint`` or rooted in its columns, by base."""
    columns = {(p[0], p[2]) for p in footprint}
    cells = set(footprint)

    def in_the_way(t: Tree) -> bool:
        if (t.base[0], t.base[2]) in columns:
            return True
        return any(p in cells for p in t.all_logs | t.leaves)

    found = find_trees(world, footprint, template=template, leafless=leafless)
    return {t.base: t for t in found if in_the_way(t)}


def clear_trees_in_footprint(
    body: BuildBody,
    footprint: Collection[Pos],
    template=None,
    leafless: bool = False,
) -> int:
    """Whole tree or nothing: a tree with any log or leaf in ``footprint`` (or
    rooted in its columns) loses every log, wherever it is rooted; any other tree
    is not touched. Nearest first; a tree left with logs gets one more attempt on
    a second pass. Leaves decay on their own. Returns the number felled completely."""
    feller = TreeFeller(body, template=template)
    cleared = 0
    for _ in range(TREE_PASSES):
        trees = trees_in_the_way(body.world, footprint, template, leafless)
        if not trees:
            break
        while trees:
            base = min(trees, key=lambda b: (_dist2(b, body.position()), b))
            if feller.fell(trees.pop(base)):
                cleared += 1
    return cleared
