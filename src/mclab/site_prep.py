"""Site preparation: level the template's footprint plus a margin before building.

Definitions (world coordinates, from the *transformed* template):

- footprint: the x/z extent of ``template.cells`` (rotation and mirror already applied).
- area: the footprint grown by ``margin`` cells on every horizontal side (margin >= 0).
- ground: ``ground_y = template_min_y - 1``. The schematic's lowest layer (its
  foundation) sits directly on the ground, so every column of the area needs a solid
  block at ``ground_y`` (dirt is filled in, down to supporting terrain) and nothing
  above it except intended template blocks, up to the template's top layer.

Nothing outside the area is touched. Template cells with a non-air target, containers
and anything in ``protected`` are never broken or built over; unknown cells are never
edited. Whatever cannot be done is reported in ``SiteReport.blocked`` / ``.unknown``
and ``stop`` is not ``"ready"``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from mclab import build_plan as bp
from mclab.build_plan import Pos
from mclab.progress_watch import StallError, no_placing, walking_with_edits
from mclab.tree_removal import clear_trees_in_footprint, is_log

FILL = "minecraft:dirt"
# Anything solid the clearing dug up fills a hole as well as dirt does. Full cubes
# without gravity only; an item the house itself is built from is never spent.
FILLERS = (
    FILL,
    "minecraft:cobblestone",
    "minecraft:stone",
    "minecraft:andesite",
    "minecraft:diorite",
    "minecraft:granite",
    "minecraft:cobbled_deepslate",
    "minecraft:deepslate",
    "minecraft:tuff",
    "minecraft:coarse_dirt",
    "minecraft:rooted_dirt",
    "minecraft:mud",
    "minecraft:clay",
    "minecraft:netherrack",
)


def fill_stock(inventory: dict[str, int], template: bp.Template) -> dict[str, int]:
    """Held fill material by item, dirt first."""
    wanted = {_ident(state) for state in template.cells.values()}
    return {
        item: inventory[item]
        for item in FILLERS
        if inventory.get(item, 0) > 0 and item not in wanted
    }


MAX_CELL_STALLS = 6  # cells given up on after a >5 s stall before the whole site fails
MAX_FILL_DEPTH = 8  # a column needing more fill than this has no usable support
BATCH = 24
MAX_OCCLUDERS = 4  # blockers broken for one cell before it is left to a stance
CONTAINERS = (
    "chest",
    "barrel",
    "shulker_box",
    "furnace",
    "smoker",
    "hopper",
    "dispenser",
    "dropper",
    "crafting_table",
)
UNBREAKABLE = ("bedrock", "barrier", "end_portal")


@dataclass(frozen=True)
class Site:
    x0: int
    x1: int
    z0: int
    z1: int
    ground_y: int
    top_y: int
    margin: int

    def columns(self) -> Iterable[tuple[int, int]]:
        for z in range(self.z0, self.z1 + 1):
            for x in range(self.x0, self.x1 + 1):
                yield x, z

    def contains(self, pos: Pos) -> bool:
        return self.x0 <= pos[0] <= self.x1 and self.z0 <= pos[2] <= self.z1


def site_area(template: bp.Template, margin: int = 1) -> Site:
    if isinstance(margin, bool) or not isinstance(margin, int) or margin < 0:
        raise ValueError(f"site margin must be an integer >= 0, got {margin!r}")
    xs = [p[0] for p in template.cells]
    ys = [p[1] for p in template.cells]
    zs = [p[2] for p in template.cells]
    return Site(
        min(xs) - margin,
        max(xs) + margin,
        min(zs) - margin,
        max(zs) + margin,
        min(ys) - 1,
        max(ys),
        margin,
    )


@dataclass
class SiteReport:
    # stop: ready | blocked | unknown | need_materials | max_steps | max_rounds
    stop: str = ""
    margin: int = 1
    area: tuple[int, int, int, int] = (0, 0, 0, 0)  # x0 x1 z0 z1
    ground_y: int = 0
    cleared: int = 0  # cells broken
    filled: int = 0  # dirt placed
    trees: int = 0
    unknown: list[Pos] = field(default_factory=list)
    blocked: dict[Pos, str] = field(default_factory=dict)  # cell -> why it stays wrong
    needed: dict[str, int] = field(default_factory=dict)
    rounds: int = 0

    def summary(self) -> dict:
        return {
            "stop": self.stop,
            "margin": self.margin,
            "area": list(self.area),
            "ground_y": self.ground_y,
            "cleared": self.cleared,
            "filled": self.filled,
            "trees": self.trees,
            "unknown": len(self.unknown),
            "blocked": {str(p): r for p, r in list(self.blocked.items())[:20]},
            "needed": self.needed,
            "rounds": self.rounds,
        }


def _ident(state: str | None) -> str | None:
    return None if state is None else bp.parse_state(state)[0]


# ponytail: a flower in a ground-level hole is a hole, not support (run
# 20261110T024448883505Z reported the site ready with every flowered hole open).
# Named list, not a block tag: add a plant here when a run leaves one behind.
FLOWERS = frozenset(
    "minecraft:" + n
    for n in (
        "dandelion",
        "poppy",
        "blue_orchid",
        "allium",
        "azure_bluet",
        "red_tulip",
        "orange_tulip",
        "white_tulip",
        "pink_tulip",
        "oxeye_daisy",
        "cornflower",
        "lily_of_the_valley",
        "sunflower",
        "lilac",
        "rose_bush",
        "peony",
        "fern",
        "large_fern",
        "dead_bush",
        "brown_mushroom",
        "red_mushroom",
        "sugar_cane",
    )
)
# Pond plants are never support, and every one in a column to fill is broken before the
# fill: the server clips its sight rays by outline, so seagrass and lily pads hide the
# faces behind them, and a lily pad's thin collision box read as solid ground at the
# ground level, with the pond still under it.
POND_PLANTS = bp.WATER_PLANTS | {"minecraft:lily_pad", "minecraft:sea_pickle"}
HIGH = 6  # blocks above the ground a standing body cannot reach
REACH_BREAK = 4.2  # eye to cell centre, inside the body's 4.5 reach


def _eye_dist(eye, p: Pos) -> float:
    return math.dist(eye, (p[0] + 0.5, p[1] + 0.5, p[2] + 0.5))


PLACE_REACH = 4.5  # the body's block interaction range, as the server checks it


def _in_place_reach(eye, p: Pos) -> bool:
    """A face of cell p may be clickable from eye: the server ray-tests the centre and
    quarter points of the clicked face within 4.5, and this estimates the nearest of
    them by the cell's quarter-point box; the server's answer is final (a refusal
    is cheap and remembered). The centre test (4.2) left a pond cell two below
    the ground, 4.24 to its centre and 3.9 to its face, unfilled for 200 s until the
    build ran out of time (run 20261117T045722501942Z)."""
    near = [min(max(e, c + 0.25), c + 0.75) for e, c in zip(eye, p, strict=True)]
    return math.dist(eye, near) <= PLACE_REACH


def _dist2(a: Pos, b: Pos) -> int:
    return sum((p - q) ** 2 for p, q in zip(a, b, strict=True))


@dataclass
class _Plan:
    breaks: list[Pos] = field(default_factory=list)
    fills: list[Pos] = field(default_factory=list)
    unknown: list[Pos] = field(default_factory=list)
    blocked: dict[Pos, str] = field(default_factory=dict)
    held: set[Pos] = field(default_factory=set)  # skipped fills, retried bottom up


def plan_site(
    site: Site, template: bp.Template, world, protected=frozenset(), face=None
) -> _Plan:
    """What is still wrong in the area, from the delivered world (pure). ``face(x, y,
    z)`` judges support by real collision boxes: seagrass under a pond is not
    REPLACEABLE by name but has nothing to click (run 20261117T031430547551Z: 63
    no_support_face fills over it, then the fill stalled)."""
    plan = _Plan()
    for x, z in site.columns():
        for y in range(site.top_y, site.ground_y, -1):
            pos = (x, y, z)
            ident = _ident(world(x, y, z))
            if ident is None:
                plan.unknown.append(pos)
                continue
            target = template.cells.get(pos)
            wanted = _ident(target) if target else None
            if ident in bp.AIR or ident == wanted:
                continue  # empty, or already what the house wants
            if wanted and wanted not in bp.AIR:
                # A house block goes here, so whatever stands in it comes out anyway.
                # Cut ground and vegetation now: its dirt is the fill. Leaving it to
                # the builder kept a mound under the ground floor while the fill ran
                # dry and the body carved the hillside for dirt instead (run
                # 20261117T130121185147Z). Fluids, containers and bedrock stay the
                # builder's business.
                if not (
                    ident in bp.FLUIDS
                    or any(c in ident for c in CONTAINERS)
                    or any(u in ident for u in UNBREAKABLE)
                ):
                    plan.breaks.append(pos)
                continue
            elif pos in protected or any(c in ident for c in CONTAINERS):
                plan.blocked[pos] = "protected"
            elif any(u in ident for u in UNBREAKABLE):
                plan.blocked[pos] = "unbreakable"
            elif ident in bp.FLUIDS:
                plan.blocked[pos] = "fluid_above_ground"
            else:
                plan.breaks.append(pos)
        y, run = site.ground_y, []
        if "_log" in (_ident(world(x, y, z)) or "") and (x, y, z) not in template.cells:
            plan.breaks.append((x, y, z))  # a stump in the ground: out, then dirt
        while True:
            ident = _ident(world(x, y, z))
            if ident is None:
                plan.unknown.append((x, y, z))
                run = []
                break
            if ident not in POND_PLANTS and (
                face(x, y, z)
                if face
                else ident not in bp.REPLACEABLE and ident not in FLOWERS
            ):
                break  # supporting block found
            run.append((x, y, z))
            y -= 1
            if len(run) > MAX_FILL_DEPTH:
                plan.blocked[(x, site.ground_y, z)] = "no_support_within_depth"
                run = []
                break
        plan.fills.extend(reversed(run))  # lowest first
    return plan


def prepare_site(
    template: bp.Template,
    body,
    runner,
    site: Site,
    *,
    protected: Iterable[Pos] = (),
    on_need_materials: Callable[[dict[str, int]], bool] | None = None,
    max_rounds: int = 400,
    cut_only: bool = False,
    log: Callable[[str], None] = lambda m: None,
) -> SiteReport:
    """Clear, cut and fill the area through ``runner.attempt`` (stances, retries,
    escapes, step budget and failure records are the builder's own)."""
    rep = SiteReport(
        margin=site.margin,
        area=(site.x0, site.x1, site.z0, site.z1),
        ground_y=site.ground_y,
    )
    protected = frozenset(map(tuple, protected))
    report = runner.report
    walk = getattr(body, "walk_to", None)
    collect = getattr(body, "collect_drops", None)
    visited: set[Pos] = set()
    stalled = 0
    unreachable: list[Pos] = []
    tried: set[Pos] = set()
    borrowed: set[Pos] = set()  # hill cells already cut at twice
    cut_once: set[Pos] = set()
    retried = False
    for rep.rounds in range(1, max_rounds + 1):
        if report.steps >= runner.max_steps:
            rep.stop = "max_steps"
            return rep
        plan = _round_plan(site, template, body, protected, report.skipped)
        rep.unknown, rep.blocked = plan.unknown, plan.blocked
        logs, cuts, leaves = _phases(plan.breaks, body.world)
        log(
            f"site round {rep.rounds}: {len(logs)} logs, {len(cuts)} to cut, "
            f"{len(plan.fills)} to fill, {len(leaves)} leaves, "
            f"{len(plan.unknown)} unknown, {len(plan.blocked)} blocked"
        )
        here = body.position()
        fill_columns = {p[::2] for p in plan.fills}
        # Low foliage can roof a ground-level hole, hiding every clickable support
        # face. Clear it before filling even where the house later replaces it.
        work = (
            logs
            or cuts
            or [
                p
                for p in leaves
                if p[::2] in fill_columns and p[1] <= site.ground_y + 2
            ]
        )
        if cut_only and not work:  # idle slice: filling may make blocked logs reachable
            rep.stop = "cut_done"
            return rep
        fresh = [p for p in plan.unknown if p not in visited]
        if fresh and not work:
            # approach unknown cells once so the sensor delivers them
            p = min(fresh, key=lambda q: _dist2(q, here))
            # whole column and neighbours: one approach per place, never a retry loop
            visited.update(q for q in fresh if _dist2(q, p) <= 36 or q[::2] == p[::2])
            reached = _approach(body, walk, p, site.ground_y)
            if not reached:
                unreachable.append(p)
                log(f"site: cannot approach unknown cell {p}; not retrying")
            continue
        if logs and not getattr(runner, "trees_felled", False):
            # Phase 1: whole trees, by the feller, before any single log is swept
            # (the sweep broke a top log, then the bottom one, then pillared up and
            # left the rest). Once per run: the feller makes its own second pass.
            runner.trees_felled = True
            rep.trees += _fell_trees(body, template, site)
            log(f"site: {rep.trees} trees felled")
            continue
        if work:
            # Phase 2 (logs the feller could not finish are swept first) and 3: cuts.
            rep.cleared += _clear(
                body, runner, work, site.ground_y, tried, frozenset(leaves), log
            )
            tried = set(work)
            continue
        if not plan.fills:
            if leaves:  # Phase 5: what is still standing in the site
                rep.cleared += _clear(
                    body, runner, leaves, site.ground_y, tried, frozenset(), log
                )
                tried = set(leaves)
                continue
            stuck = [
                p
                for p, w in report.skipped.items()
                if site.contains(p)
                and (w in {"could not clear", "no_stance"} or p in plan.held)
            ]
            if stuck and not retried:
                # Given up before the fill: with the ground now whole there is a
                # stance beside them, including fill cells previously without a
                # placement stance. A 3-high dirt pillar on the margin's water edge
                # ended run 20261111T025206013862Z "blocked" with the site level.
                retried = True
                for q in stuck:
                    del report.skipped[q]
                    runner.fails.pop(q, None)
                    runner.avoid.pop(q, None)
                tried = set()
                continue
            rep.blocked = {**rep.blocked, **dict.fromkeys(unreachable, "unreachable")}
            # Audit the delivered world without the skip filter before declaring
            # readiness. Failed work remains visible even after a bounded retry.
            audit = plan_site(
                site, template, body.world, protected, getattr(body, "has_face", None)
            )
            remaining = audit.breaks + audit.fills
            rep.blocked.update(audit.blocked)
            rep.blocked.update(dict.fromkeys(remaining, "unfinished site edit"))
            rep.unknown = audit.unknown
            rep.stop = (
                "blocked" if rep.blocked else ("unknown" if rep.unknown else "ready")
            )
            return rep
        have = sum(fill_stock(body.inventory(), template).values())
        if have < 1:
            # ponytail: skip cells already tried, do not give up on the whole hill —
            # one uncut nearest cell sent every later refill to the slow gather
            # digger, one block per climb (run 20261110T164401279123Z)
            hill = _hill_dirt(
                site, body, set(protected) | borrowed, here, len(plan.fills)
            )
            if hill:
                # Cut the raised ground beside the site for fill, with the same sweep
                # that clears: the gather's digger took 17 s a block and dug pits at
                # ground level (run 20261110T021445878238Z).
                borrowed.update(cut_once.intersection(hill))
                cut_once.update(hill)
                log(f"site: cutting {len(hill)} dirt from raised ground nearby")
                _sweep(body, runner, hill, site.ground_y, rim=True)
                if collect:
                    collect(hill[-BATCH:])
                if fill_stock(body.inventory(), template):
                    continue
            rep.needed = {FILL: len(plan.fills)}
            if on_need_materials and on_need_materials(rep.needed):
                continue
            rep.stop = "need_materials"
            return rep
        # nearest column first (lowest cell first within it): the fill grows out from
        # where the body stands, so each next stance is on land it just made (run
        # 20261109T234059916010Z: lowest-first sent it at y=61 water cells 7+ blocks
        # away, 12 s stalls each)
        plan.fills.sort(
            key=lambda p: (_dist2((p[0], 0, p[2]), (here[0], 0, here[2])), p[1])
        )
        swept = _fill_sweep(
            body,
            runner,
            plan.fills,
            have,
            site.ground_y,
            fill_stock(body.inventory(), template),
        )
        rep.filled += swept
        if swept:
            continue
        # nothing placeable from a plain walk: the builder's stance search, per cell,
        # bottom up: only cells already standing on something. A cell over water or
        # air has no face to click and no stance beside it that is not itself over
        # the hole (run 20261117T015107803188Z: 64 node-limit plans at a pond).
        supported = [p for p in plan.fills if _ground(body, p[0], p[1] - 1, p[2])]
        for pos in supported[: min(BATCH, have)]:
            before = len(report.placed)
            try:
                if _ident(body.world(*pos)) in FLOWERS:
                    # the sweep could not see it to break it; dirt on a flower is
                    # refused "occupied" (run 20261110T033057553889Z, 11 times)
                    runner.attempt(bp.Break(pos))
                item = next(iter(fill_stock(body.inventory(), template)), FILL)
                runner.attempt(bp.Place(pos, item, item))
            except StallError as err:
                # a fill cell the body cannot work: report it, do not retry it
                stalled += 1
                report.skipped[pos] = f"stalled: {err}"
                log(
                    f"site: giving up on fill {pos} after a stall "
                    f"({stalled}/{MAX_CELL_STALLS})"
                )
                watch = getattr(body, "_watch", None)
                if watch is not None:
                    watch.reset()
                if stalled > MAX_CELL_STALLS:
                    raise
            rep.filled += len(report.placed) - before
    rep.stop = "max_rounds"
    return rep


APPROACH_RANGE = 4  # the sensor delivers the cells around the body: beside is enough


def _approach(body, walk, cell: Pos, ground: int) -> bool:
    """Walk onto known footing near an unknown cell, never onto the cell itself: the
    goal snapped above an unknown pond cell had no stance, and every plan for it ran
    the 10,000-node limit (run 20261117T015107803188Z)."""
    if not walk:
        return body.goto_stance(cell, 3.0)
    here = body.position()
    spots = []
    for dx in range(-APPROACH_RANGE, APPROACH_RANGE + 1):
        for dz in range(-APPROACH_RANGE, APPROACH_RANGE + 1):
            x, z = cell[0] + dx, cell[2] + dz
            for y in range(ground + 4, ground - 3, -1):
                if _ground(body, x, y, z):
                    if all(
                        _ident(body.world(x, y + h, z)) in bp.AIR | FLOWERS
                        for h in (1, 2)
                    ):
                        spots.append((x, y + 1, z))
                    break
    if not spots:
        return False
    goal = min(spots, key=lambda q: (_dist2(q, cell), _dist2(q, here), q))
    return walk(goal, 0.45)


def _phases(breaks: list[Pos], world) -> tuple[list[Pos], list[Pos], list[Pos]]:
    """Split the cells to break into tree logs, other cuts (terrain, plants) and
    leaves, in plan order."""
    logs, cuts, leaves = [], [], []
    for p in breaks:
        state = world(*p)
        if is_log(state):
            logs.append(p)
        elif "_leaves" in (_ident(state) or ""):
            leaves.append(p)
        else:
            cuts.append(p)
    return logs, cuts, leaves


def _fell_trees(body, template, site: Site) -> int:
    """Fell every tree in the way whole (see tree_removal.clear_trees_in_footprint);
    stand-alone logs with no canopy count as trees here."""
    area = {
        (x, y, z)
        for x, z in site.columns()
        for y in range(site.ground_y + 1, site.top_y + 1)
    }
    return clear_trees_in_footprint(body, area, template=template, leafless=True)


def _clear(body, runner, cells: list[Pos], ground: int, tried, blockers, log) -> int:
    """One sweep over ``cells``; a cell that survived two sweeps is reported, not
    swept a third time: 6 such cells held the fill back for 100+ s a round with
    nothing broken (run 20261110T020332013460Z)."""
    cleared = _sweep(body, runner, cells, ground, blockers)
    for p in cells:
        if p in tried and _ident(body.world(*p)) not in bp.AIR:
            runner.report.skipped[p] = "could not clear"
            log(f"site: giving up on clearing {p} ({_ident(body.world(*p))})")
    collect = getattr(body, "collect_drops", None)
    if collect:  # once per sweep, not once per block
        collect(cells[-BATCH:])
    return cleared


def _break_in_place(
    body, pos: Pos, todo: set[Pos], report, blockers: frozenset[Pos] = frozenset()
) -> bool:
    """Break ``pos`` from where the body stands. A refusal that names a blocker which
    is itself to be cleared (in ``todo``, or a leaf listed in ``blockers``: leaves
    are only broken early when they hide something) breaks that first."""
    for _ in range(MAX_OCCLUDERS):
        result = body.break_block(pos)
        report.steps += 1
        if result.ok:
            report.broken.append(pos)
            todo.discard(pos)
            return True
        if result.reason == "nothing_to_break":
            # already gone (leaves decay once the logs are felled): clear, not a
            # failure. 628 of these were retried in run 20261110T005916763793Z.
            todo.discard(pos)
            return True
        data = result.data or {}
        blocker = tuple(data.get("blocker") or ())
        # ponytail: a leaf the server names as hiding the cell is always fair game;
        # leaves off the list left a bushy trunk standing behind them for three
        # sweeps (run 20261110T171038 log: 20 refusals, blockers never broken)
        leaf = "_leaves" in str(data.get("blocker_state") or "")
        if (
            result.reason != "no_line_of_sight"
            or blocker == pos
            or not (leaf or blocker in todo or blocker in blockers)
        ):
            return False
        if not body.break_block(blocker).ok:
            return False
        report.broken.append(blocker)
        todo.discard(blocker)
    return False


def _sweep(
    body,
    runner,
    breaks: list[Pos],
    ground: int,
    blockers: frozenset[Pos] = frozenset(),
    rim: bool = False,
) -> int:
    """Clear the whole list in one pass: everything in reach is broken back to back
    from where the body stands (highest first), then one move to the nearest cell
    left, and so on. No re-plan, drop wait or stance search between blocks: those
    cost 5-7 s per 3-6 blocks (run 20261110T004819884804Z).

    ``blockers``: leaves that may be broken when a refusal names them as hiding a
    cell of ``breaks`` (the early phases leave all other leaves to decay)."""
    report, todo, start = runner.report, set(breaks), len(runner.report.broken)
    refused: set[Pos] = set()  # could not be broken from the current stance
    walked: set[Pos] = set()
    stanced: set[Pos] = set()
    walk = getattr(body, "walk_to", None)
    while todo and report.steps < runner.max_steps:
        here = body.position()
        eye = (here[0] + 0.5, here[1] + 1.62, here[2] + 0.5)
        todo -= {p for p in todo if _ident(body.world(*p)) in bp.AIR}  # decayed
        near = [
            p for p in todo if p not in refused and _eye_dist(eye, p) <= REACH_BREAK
        ]
        if near:
            pos = min(near, key=lambda p: (-p[1], _eye_dist(eye, p)))
            if not _break_in_place(body, pos, todo, report, blockers):
                refused.add(pos)
            continue

        # Move: walk to the ground beside the nearest cell (a plain walk the planner
        # finishes at once), solid blocks before leaves (leaves decay on their own
        # once the logs are gone). Only a cell still out of reach after that gets a
        # stance search: searching a stance for a leaf 6 up cost 57 route plans and
        # 123 s for one block (run 20261110T005916763793Z).
        def order(p: Pos, here=here):
            flat = (p[0] - here[0]) ** 2 + (p[2] - here[2]) ** 2
            # p last: a tie is not left to set iteration order
            return ("leaves" in (_ident(body.world(*p)) or ""), flat, p[1], p)

        rest = [p for p in todo if p not in walked]
        if rest and walk:
            pos = min(rest, key=order)
            near_pos = {q for q in rest if _dist2(q, pos) <= 9}
            walked |= near_pos  # one walk serves the whole cluster
            # Under a high cell, beside a low one; never placing blocks to get there.
            goal, radius = (pos[0], ground + 1, pos[2]), 2.0
            if pos[1] > ground + 3:
                radius = 0.8
            if rim:
                # ponytail: cut a mound from the level ground beside it. A goal
                # inside the mound snapped to its top: the body climbed, cut, fell
                # and hopped on, 19 of 38 walks breaking nothing (run 32).
                solid = {q[::2] for q in todo}
                spots = [
                    (pos[0] + dx, ground + 1, pos[2] + dz)
                    for dx in range(-3, 4)
                    for dz in range(-3, 4)
                    if (pos[0] + dx, pos[2] + dz) not in solid
                    and _ground(body, pos[0] + dx, ground, pos[2] + dz)
                    and not _ground(body, pos[0] + dx, ground + 1, pos[2] + dz)
                    and not _ground(body, pos[0] + dx, ground + 2, pos[2] + dz)
                ]
                if spots:
                    goal = min(spots, key=lambda q: (_dist2(q, pos), _dist2(q, here)))
                    radius = 0.8
            with no_placing(body):
                walk(goal, radius)
        else:
            rest = [p for p in todo if p not in stanced]
            if not rest:
                break  # the rest failed from its own stance: next round reports it
            pos = min(rest, key=order)
            stanced.add(pos)
            climb = getattr(body, "dig_to", None)
            if climb and pos[1] > ground + HIGH and climb((pos[0], pos[1] - 3, pos[2])):
                # Overhead (leaves of a tree rooted outside the area never decay):
                # pillar up under it, one planned climb, instead of a stance search
                # over the canopy (120 s for one leaf, run 20261110T011637401098Z).
                # The pillar is inside the area, so the sweep takes it down again.
                refused.clear()
                continue
            with no_placing(body):
                runner.attempt(bp.Break(pos))  # stance search, then the break
            if pos in report.broken[start:]:
                todo.discard(pos)
        refused.clear()  # a new stance sees other faces
    return len(report.broken) - start


BORROW_RADIUS = 10  # how far outside the area raised ground is cut for fill
BORROW_MAX = 64  # cells per cut (one stack)


def _hill_dirt(site: Site, body, protected, here, need: int) -> list[Pos]:
    """Dirt above ground level just outside the area, nearest first: cutting it
    levels the surroundings instead of leaving pits."""
    found = []
    for x in range(site.x0 - BORROW_RADIUS, site.x1 + BORROW_RADIUS + 1):
        for z in range(site.z0 - BORROW_RADIUS, site.z1 + BORROW_RADIUS + 1):
            if site.contains((x, 0, z)):
                continue
            for y in range(site.ground_y + 1, site.ground_y + HIGH):
                pos = (x, y, z)
                if pos not in protected and _ident(body.world(*pos)) in (
                    FILL,
                    "minecraft:grass_block",
                ):
                    found.append(pos)

    # ponytail: one connected mound at a time, from the side facing the area, top
    # layer first. Nearest-to-the-body scattered a cut over two mounds on opposite
    # sides of the house (run 32, third cut).
    def off_site(p: Pos) -> int:
        dx = max(site.x0 - p[0], 0, p[0] - site.x1)
        dz = max(site.z0 - p[2], 0, p[2] - site.z1)
        return dx * dx + dz * dz

    def order(p: Pos):
        return (off_site(p), _dist2((p[0], 0, p[2]), (here[0], 0, here[2])), -p[1])

    want, out, left = min(need, BORROW_MAX), [], sorted(found, key=order)
    while left and len(out) < want:
        columns = {p[::2] for p in left}
        patch, stack = set(), [left[0][::2]]
        while stack:
            c = stack.pop()
            if c in patch or c not in columns:
                continue
            patch.add(c)
            stack += [(c[0] + dx, c[1] + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
        out += [p for p in left if p[::2] in patch]
        left = [p for p in left if p[::2] not in patch]
    return out[:want]


_D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _rings(columns: set[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """Each fill column's distance from the nearest column that is not filled: 1 is the
    perimeter (it walls the rest off from the open water), then inward."""
    ring = {
        c: 1
        for c in columns
        if any((c[0] + dx, c[1] + dz) not in columns for dx, dz in _D4)
    }
    frontier = sorted(ring)
    while frontier:
        nxt = []
        for c in frontier:
            for dx, dz in _D4:
                n = (c[0] + dx, c[1] + dz)
                if n in columns and n not in ring:
                    ring[n] = ring[c] + 1
                    nxt.append(n)
        frontier = nxt
    return ring


def _workable(todo: set[Pos], ground: int) -> list[Pos]:
    """The cell each column fills next (its lowest). The top cell of a column waits
    while a column around it still has cells below the ground: the user's way with a
    pond, the bottom layers first, then the top, so the body can work from the lower
    layer it made (from the bank, the bottom of a two-deep pond is 4.62 under the
    eye, past the 4.5 reach)."""
    lowest: dict[tuple[int, int], int] = {}
    for x, y, z in todo:
        lowest[x, z] = min(y, lowest.get((x, z), y))
    deep = {c for c, y in lowest.items() if y < ground}
    out = []
    for (x, z), y in lowest.items():
        if y >= ground and any(
            (x + dx, z + dz) in deep for dx in (-1, 0, 1) for dz in (-1, 0, 1)
        ):
            continue
        out.append((x, y, z))
    return out


def _may_fill_from(here: Pos, p: Pos, ground: int) -> bool:
    """Standing on the working surface, any cell; standing lower (on the pond's lower
    layer), only cells below the feet: never wall in the body's own level."""
    return p[::2] != here[::2] and (here[1] > ground or p[1] < here[1])


def _whack_plants(body, report, todo: set[Pos], ground: int, eye, here: Pos, whacked):
    """Break every pond plant in reach in the columns to fill, top first, and every
    flower in a cell to fill: a flower hides the faces under it until it is gone, so
    the sweep passed it by and each went to the per-cell stance search (16 tulips in
    run 20261120T075123420431Z). Returns the cells the body's leak plug filled when
    a broken plant let the water in."""
    plugged: list[tuple[Pos, str]] = []
    if not todo:
        return plugged
    lo = min(p[1] for p in todo)
    for x, z in sorted({p[::2] for p in todo}):
        for y in range(ground, lo - 1, -1):
            p = (x, y, z)
            if (p, here) in whacked or _eye_dist(eye, p) > REACH_BREAK:
                continue
            ident = _ident(body.world(*p))
            if ident not in POND_PLANTS and not (ident in FLOWERS and p in todo):
                continue
            whacked.add((p, here))
            report.steps += 1
            broke = body.break_block(p)
            plug = (broke.data or {}).get("plugged") if not broke.ok else None
            if plug:
                plugged.append((p, plug))
    return plugged


def _stance_in(body, x: int, z: int, ground: int, lo: int) -> Pos | None:
    """Where the body stands in column (x, z): the first support from the working
    surface down, with the head out of the water."""
    for y in range(ground + 1, lo, -1):
        if not _ground(body, x, y - 1, z):
            continue
        feet = _ident(body.world(x, y, z))
        head = _ident(body.world(x, y + 1, z))
        ok = feet in bp.AIR | FLOWERS or feet == "minecraft:water"
        return (x, y, z) if ok and head in bp.AIR | FLOWERS else None
    return None


STANCE_RADIUS = 3  # stances tried around each column still to fill
STANCE_MARGIN = 0.25  # reach kept back for the walk's arrival radius


def _fill_stance(body, sight, open_: list[Pos], ring, here: Pos, ground: int, skip):
    """The stance that shows the server a support face of the most cells, perimeter
    first: columns are taken by (ring, distance), and the first column with any
    seeing stance around it decides. A pond plant in a cell counts as seen from
    where it can be broken: no face shows through it, and broken it is filled by
    the leak plug (``_whack_plants``; run 20261120T081707320639Z stopped "blocked"
    on seagrass at the bottom of a one-block pit no stance saw)."""
    lo = min(p[1] for p in open_) - 1
    plants = {p for p in open_ if _ident(body.world(*p)) in POND_PLANTS}
    tried: set[Pos] = set()
    reach = PLACE_REACH - STANCE_MARGIN
    columns = sorted(
        {p[::2] for p in open_},
        key=lambda c: (
            ring.get(c, 0),
            _dist2((c[0], 0, c[1]), (here[0], 0, here[2])),
            c,
        ),
    )
    for cx, cz in columns:
        best = None
        for dx in range(-STANCE_RADIUS, STANCE_RADIUS + 1):
            for dz in range(-STANCE_RADIUS, STANCE_RADIUS + 1):
                node = _stance_in(body, cx + dx, cz + dz, ground, lo)
                if node is None or node == here or node in tried or node in skip:
                    continue
                tried.add(node)
                eye = (node[0] + 0.5, node[1] + 1.62, node[2] + 0.5)
                seen = [
                    p
                    for p in open_
                    if _may_fill_from(node, p, ground)
                    and (
                        _eye_dist(eye, p) <= REACH_BREAK
                        if p in plants
                        else _in_place_reach(eye, p) and sight(p, eye, reach)
                    )
                ]
                if not seen:
                    continue
                key = (
                    min(ring.get(p[::2], 0) for p in seen),
                    -len(seen),
                    _dist2(node, here),
                    node,
                )
                if best is None or key < best[0]:
                    best = (key, node)
        if best is not None:
            return best[1]
    return None


def _fill_sweep(
    body, runner, fills: list[Pos], have: int, ground: int, stock=None
) -> int:
    """Place every fill cell in reach back to back, then one walk to the next stance.
    One stance search per block took so long the first dirt grew grass before the next
    landed (run 20261110T014910705271Z: 9 placed, 9 state_unreachable in 150 s).

    The order is the user's for a pond: pond plants out of the way first, the
    perimeter before the inside, the lower layers before the top (``_workable``),
    with the body standing on the layer it made. A body that can predict the
    server's face search (``sees_placement``) clicks only cells it sees and walks to
    the stance that sees the most; without it, the dry rim is walked."""
    report, todo, placed = runner.report, set(fills), 0
    stock = dict(stock or {FILL: have})
    refused: set[Pos] = set()
    walked: set[Pos] = set()
    failed_stances: dict[tuple[Pos, Pos], int] = {}
    approached: set[tuple[Pos, int]] = set()
    whacked: set[tuple[Pos, Pos]] = set()
    walks_without_edit = 0
    walk = getattr(body, "walk_to", None)
    sight = getattr(body, "sees_placement", None)
    ring = _rings({p[::2] for p in todo})

    def filled(pos: Pos, item: str | None) -> None:
        nonlocal placed, walks_without_edit
        if item is not None and stock.get(item, 0) > 0:
            stock[item] -= 1
        report.placed.append(pos)
        todo.discard(pos)
        # a cell under a fill is buried: no face of it shows again
        todo.difference_update(
            {q for q in todo if q[::2] == pos[::2] and q[1] < pos[1]}
        )
        placed += 1
        walks_without_edit = 0
        walked.clear()  # an edit can open a previously unreachable frontier
        # the new block is a face to click for the cells touching it
        refused.difference_update({q for q in refused if _dist2(q, pos) == 1})

    while todo and placed < have and report.steps < runner.max_steps:
        here = body.position()
        eye = (here[0] + 0.5, here[1] + 1.62, here[2] + 0.5)
        for pos, plug in _whack_plants(body, report, todo, ground, eye, here, whacked):
            if pos in todo:
                # seagrass under water: the leak-plug reflex filled the cell itself;
                # dirt on it was refused "occupied" (run 20261117T033515924527Z)
                filled(pos, bp.parse_state(plug)[0])
        if not todo:
            break
        open_ = _workable(todo, ground)
        near = [
            p
            for p in open_
            if p not in refused
            and failed_stances.get((p, here)) != placed
            and _may_fill_from(here, p, ground)
            and _in_place_reach(eye, p)
            and (sight is None or sight(p))
        ]
        if near:
            pos = min(near, key=lambda p: (ring[p[::2]], p[1], _eye_dist(eye, p)))
            report.steps += 1
            ident = _ident(body.world(*pos))
            if ident and ident not in bp.AIR and ident not in bp.FLUIDS:
                # a flower or grass in the hole: dirt cannot replace it (run
                # 20261110T024448883505Z left every flowered hole unfilled)
                broke = body.break_block(pos)
                plug = (broke.data or {}).get("plugged") if not broke.ok else None
                if plug:
                    filled(pos, bp.parse_state(plug)[0])
                    continue
            item = next(i for i, n in stock.items() if n > 0)
            if body.place_block(pos, item, item).ok:
                filled(pos, item)
            else:
                refused.add(pos)
                failed_stances[pos, here] = placed
            continue
        if walks_without_edit >= 4 or not walk:
            # nothing left in reach: hand the rest to the per-cell stance search,
            # which may stand lower (run 20261117T035331768325Z: a pond cell two
            # below the ground, 4.36 from every rim eye, ended the run)
            break
        if sight is not None:
            skip = {g for g, n in approached if n == placed}
            goal = _fill_stance(body, sight, open_, ring, here, ground, skip)
        else:
            goal = _rim_stance(body, todo, walked, here, ground, approached, placed)
        if goal is None:
            break  # let the bounded stance search handle cells nothing here shows
        approached.add((goal, placed))
        # Let the delivered terrain scanner see newly filled ground before the
        # native planner is asked to stand on it (the local overlay sees it first).
        if placed and (wait := getattr(body, "wait_ticks", None)):
            wait(5)
            if observe := getattr(body, "observe", None):
                observe()
        # Once on the working surface, stay there, except a step down onto the pond's
        # lower layer. Above it, allow the normal descent back from a borrow hill.
        drop = None if here[1] > ground + 1 else max(0, here[1] - goal[1])
        with walking_with_edits(body, max_drop=drop):
            walk(goal, 0.45)
        walks_without_edit += 1
        refused.clear()  # stance history survives walks; only edits permit a retry
    return placed


def _rim_stance(body, todo, walked, here: Pos, ground: int, approached, placed):
    """Without a sight check: whole ground beside the nearest cells, never in a hole
    (the body jumped into one hole to fill its neighbours, run 32, user report)."""
    rest = [p for p in todo if p not in walked]
    if not rest:
        return None
    pos = min(rest, key=lambda p: (_dist2(p[::2], here[::2]), p[1]))
    walked |= {q for q in rest if _dist2(q, pos) <= 2}
    holes = {q[::2] for q in todo}
    rim = {
        (p[0] + dx, ground + 1, p[2] + dz)
        for p in rest
        for dx in range(-1, 2)
        for dz in range(-1, 2)
        if (p[0] + dx, p[2] + dz) not in holes
        and _ground(body, p[0] + dx, ground, p[2] + dz)
        and all(
            _ident(body.world(p[0] + dx, y, p[2] + dz)) in bp.AIR | FLOWERS
            for y in (ground + 1, ground + 2)
        )
    }
    rim = {q for q in rim if q != here and (q, placed) not in approached}
    if not rim:
        return None
    return min(rim, key=lambda q: (_dist2(q, here), q))



def _ground(body, x: int, y: int, z: int) -> bool:
    """A face to stand on or click, by the cell's real collision boxes when the body
    knows them (modded blocks included), else by name."""
    has_face = getattr(body, "has_face", None)
    if not has_face:
        return _solid(body.world(x, y, z))
    return has_face(x, y, z) and _ident(body.world(x, y, z)) not in POND_PLANTS


def _solid(state) -> bool:
    ident = _ident(state)
    return (
        bool(ident)
        and ident not in bp.AIR
        and ident not in bp.FLUIDS
        and ident not in POND_PLANTS
        and (ident not in bp.REPLACEABLE and ident not in FLOWERS)
    )


def _round_plan(site, template, body, protected, skipped) -> _Plan:
    plan = plan_site(
        site, template, body.world, protected, getattr(body, "has_face", None)
    )
    plan.breaks = [p for p in plan.breaks if p not in skipped]
    # A fill over a skipped fill would bury it: no face, no retry ever reaches it
    # again (run 20261117T043111369210Z: seagrass skipped from a far stance, dirt
    # put on top, site blocked). The column waits for the retry, bottom up.
    plan.held = {q for q in skipped if q in plan.fills}  # fill cells, any reason
    floor: dict[tuple[int, int], int] = {}
    for q in plan.held:
        floor[q[::2]] = min(q[1], floor.get(q[::2], q[1]))
    plan.fills = [
        p
        for p in plan.fills
        if p not in skipped and p[1] < floor.get(p[::2], p[1] + 1)
    ]
    for p, why in skipped.items():  # cells whose edits failed are reported, not retried
        if site.contains(p):
            plan.blocked[p] = why
    return plan
