"""Execution loop for the schematic builder (milestone M2).

``run_build`` turns the pure planner (build_plan.next_actions) into body ops.
It knows nothing about HTTP or routes: it talks to a ``BuildBody``.

What the real body adapter must do (ops: docs/BODY_ACTION_OPS.md, wave 2):

- ``place_block`` / ``use_block`` / ``break_block`` post the matching admin op
  and wait for its ``<op>_done`` / ``<op>_failed`` event (matched by
  ``request_sequence``). Return ``Result(True, data=event data)`` on done and
  ``Result(False, reason=event reason, data=...)`` on failed. The ops never walk.
- ``goto_stance(target, reach, avoid)`` runs the route planner with an
  ``interact_reach`` goal on ``target`` and follows the route. It returns True
  once the body stands on a cell from which ``target`` is within ``reach`` with a
  clear line from the eye. Feet cells listed in ``avoid`` (stances that already
  failed for this target) must not be chosen. It returns False when no stance is
  reachable.
- ``position()`` is the feet cell, ``world(x, y, z)`` is the state string the
  planner has been *delivered* (None for unknown; never poll /policy to get it),
  and ``inventory()`` maps item id -> count from the latest op event.

``walk_to(pos)`` is optional: the footprint survey (``survey=True``) uses it to
visit points around the footprint, falling back to ``goto_stance``.

The loop treats ``out_of_reach``, ``state_unreachable`` and ``entity_in_way`` as
"wrong stance": it asks for another stance (3 tries per cell, then the cell is
skipped). Any other failure counts one try and the plan is recomputed.
``on_need_materials`` is where the gathering planner plugs in: it receives
``{item: count}`` and returns True once the inventory has been refilled, False to
stop.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import NamedTuple, Protocol

from mclab import build_plan as bp
from mclab import scaffolding as sc
from mclab.build_plan import Pos
from mclab.progress_watch import (
    STALL_SECONDS,
    ProgressWatch,
    StallError,
    WatchedBody,
    no_placing,
)

REACH = 4.5
MAX_TRIES = 3
LEAD = 3  # a sweep walks to a stance for the act this far ahead of the nearest one
CLEANUP_ROUNDS = 3
MAX_RECOVERIES = 3
BUILD_WALL = 30.0  # seconds a build walk may take before the route is given up
SETTLE_TICKS = 60  # wait for the sensor before calling a build blocked
RETRY_ROUNDS = 2  # fresh tries for skipped cells before stopping blocked
STANCE_BUDGET_S = 5.0  # a pause longer than this reads as a broken bot
# entity_in_way: a mob or player on the ray that did not move off within ~1 s
STANCE_REASONS = frozenset({"out_of_reach", "state_unreachable", "entity_in_way"})


class Result(NamedTuple):
    ok: bool
    reason: str | None = None
    data: dict = {}  # noqa: RUF012 - never mutated


class BuildBody(Protocol):
    def place_block(self, pos: Pos, state: str, item: str) -> Result: ...

    def use_block(self, pos: Pos, item: str | None = None) -> Result: ...

    def break_block(self, pos: Pos, tool: str | None = None) -> Result: ...

    def goto_stance(
        self, target_pos: Pos, reach: float, avoid: Collection[Pos] = ()
    ) -> bool: ...

    def position(self) -> Pos: ...

    def world(self, x: int, y: int, z: int) -> str | None: ...

    def inventory(self) -> dict[str, int]: ...


@dataclass
class BuildReport:
    placed: list[Pos] = field(default_factory=list)
    broken: list[Pos] = field(default_factory=list)
    used: list[Pos] = field(default_factory=list)
    scaffolded: list[Pos] = field(default_factory=list)
    skipped: dict[Pos, str] = field(default_factory=dict)  # cell -> last reason
    steps: int = 0  # body ops attempted
    escapes: int = 0  # times the body climbed or dug out of a hole to reach a stance
    stop: str = ""  # done | blocked | need_materials | max_steps
    needed: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)  # build_plan.progress
    unknown: list[Pos] = field(default_factory=list)  # template cells never delivered
    surveyed: int = 0  # perimeter points visited (B4)
    trees_cleared: int = 0  # trees felled by Tree Removal skill
    site: dict = field(default_factory=dict)  # site_prep.SiteReport.summary()
    stalls: list[dict] = field(default_factory=list)  # progress_watch events
    stall_reason: str = ""  # why a "stalled" or "site_not_ready" stop happened
    # (cell, reason, stance, body diagnosis) of every failed attempt, to diagnose from
    failures: list[tuple[Pos, str, Pos, dict]] = field(default_factory=list)

    def progress(self) -> float:
        """Fraction of cells that are correct (1.0 for an empty template)."""
        total = sum(self.counts.values())
        return self.counts.get("correct", 0) / total if total else 1.0


SURVEY_STEP = 6
SURVEY_RADIUS = 3.0


def perimeter(template: bp.Template, step: int = SURVEY_STEP) -> list[Pos]:
    """Points on the rectangle one cell outside the footprint, corners included,
    in walking order, at the template's base height."""
    xs = [p[0] for p in template.cells]
    zs = [p[2] for p in template.cells]
    x0, x1, z0, z1 = min(xs) - 1, max(xs) + 1, min(zs) - 1, max(zs) + 1
    y = min(p[1] for p in template.cells)
    pts: list[Pos] = []
    for x in [*range(x0, x1, step), x1]:
        pts.append((x, y, z0))
    for z in range(z0 + step, z1, step):
        pts.append((x1, y, z))
    for x in [*range(x1, x0, -step), x0]:
        pts.append((x, y, z1))
    for z in range(z1 - step, z0, -step):
        pts.append((x0, y, z))
    return list(dict.fromkeys(pts))


def survey_footprint(template: bp.Template, body: BuildBody) -> int:
    """Walk round the footprint so the awareness sensor delivers every section
    before building (B4). Uses ``body.walk_to`` when the body has it, else a
    ``goto_stance`` with a short reach. Failures are ignored: ``unknown`` cells
    simply stay unknown and defer their placements."""
    walk = getattr(body, "walk_to", None)
    done = 0
    for pt in perimeter(template):
        ok = walk(pt) if walk else body.goto_stance(pt, SURVEY_RADIUS)
        done += bool(ok)
    return done


def _dist2(a: Pos, b: Pos) -> int:
    return sum((p - q) ** 2 for p, q in zip(a, b, strict=True))


def _do(body: BuildBody, act) -> Result:
    if isinstance(act, bp.Break):
        return body.break_block(act.pos)
    if isinstance(act, bp.Use):
        return body.use_block(act.pos, act.item)
    if isinstance(act, bp.Scaffold):
        return body.place_block(act.pos, act.item, act.item)
    return body.place_block(act.pos, act.state, act.item)


def _record(report: BuildReport, act) -> None:
    if isinstance(act, bp.Break):
        report.broken.append(act.pos)
    elif isinstance(act, bp.Use):
        report.used.append(act.pos)
    elif isinstance(act, bp.Scaffold):
        report.scaffolded.append(act.pos)
    else:
        report.placed.append(act.pos)


def _take_back(runner: _Runner, act) -> None:
    """Pick up a wrong block broken out of the template, to place it again. Run
    20261110T170929838024Z broke a detector rail it had just placed, left the drop,
    and its refill went hunting ore for a new one."""
    collect = getattr(runner.body, "collect_drops", None)  # optional, like walk_to
    if (
        collect
        and isinstance(act, bp.Break)
        and act.pos in runner.reserved  # a cell the house wants a block in
        and act.pos not in (runner.scaffolds or ())
    ):
        collect([act.pos])


def _support(act, blocks: dict) -> Pos | set[Pos] | None:
    if not isinstance(act, bp.Place):
        return None
    ident, props = bp.parse_state(act.state)
    kind = bp._support_kind(ident, props, blocks.get(ident, {}))
    if kind in ("facing_opposite", "any_face"):
        return bp._support_cell(kind, props, act.pos)
    axis = props.get("axis")
    if axis == "z":
        return {
            (act.pos[0], act.pos[1], act.pos[2] - 1),
            (act.pos[0], act.pos[1], act.pos[2] + 1),
        }
    if axis == "x":
        return {
            (act.pos[0] - 1, act.pos[1], act.pos[2]),
            (act.pos[0] + 1, act.pos[1], act.pos[2]),
        }
    if axis == "y":
        return {
            (act.pos[0], act.pos[1] - 1, act.pos[2]),
            (act.pos[0], act.pos[1] + 1, act.pos[2]),
        }
    return None


class _Runner:
    def __init__(
        self, body: BuildBody, report: BuildReport, max_steps: int, blocks: dict
    ):
        self.body, self.report, self.max_steps = body, report, max_steps
        self.blocks = blocks
        self.fails: dict[Pos, int] = {}
        self.avoid: dict[Pos, set[Pos]] = {}
        # set by _run to let a target with no stance get a scaffolding tower to stand on
        self.scaffolds: set | None = None
        self.reserved: frozenset[Pos] = frozenset()
        self.template: bp.Template | None = None
        self.towered: set[Pos] = set()  # targets that already got their one tower
        self._towering = False

    def attempt(self, act) -> None:
        """Stance, act, record. Retries other stances while the failure says so."""
        if self.report.steps >= self.max_steps:
            return
        pos = act.pos
        here = self.body.position()
        can_place = getattr(self.body, "can_place_here", None)
        direct = not isinstance(act, bp.Place) or can_place is None or can_place(
            pos, REACH, _support(act, self.blocks)
        )
        if direct and _dist2(here, pos) <= (REACH - 0.5) ** 2:
            result = _do(self.body, act)
            if result.ok:
                self.report.steps += 1
                _take_back(self, act)
                return _record(self.report, act)

        while self.fails.get(pos, 0) < MAX_TRIES and self.report.steps < self.max_steps:
            self.report.steps += 1
            avoid = self.avoid.setdefault(pos, set())
            # A block that hangs on one face (a wall torch) needs a stance that sees
            # that face; an optional body attribute, like walk_to.
            if hasattr(self.body, "required_support"):
                self.body.required_support = _support(act, self.blocks)
            if not self._stance(pos, avoid):
                self.report.failures.append(
                    (pos, "no_stance", self.body.position(), {})
                )
                # no stance is not cured by asking again: skip now, the retry
                # round comes back once the build around the cell has changed
                # (site prep keeps its three tries: its corner fills get a stance
                # only on a later one, run 20261111T014649585692Z stopped blocked)
                if self.template is not None:
                    self.fails[pos] = MAX_TRIES
                return self._fail(pos, "no_stance")
            stance = self.body.position()
            result = _do(self.body, act)
            if result.ok:
                _take_back(self, act)
                return _record(self.report, act)
            detail = (result.data or {}).get("diagnosis") or {}
            self.report.failures.append(
                (pos, result.reason or "failed", stance, detail)
            )
            self._fail(pos, result.reason or "failed")
            if result.reason not in STANCE_REASONS:
                return
            avoid.add(stance)

    def _stance(self, pos: Pos, avoid) -> bool:
        """A stance for pos; if none is reachable, climb or dig out of a hole the body
        is in (an optional body method, like walk_to) and try once more."""
        start = time.monotonic()
        if self.body.goto_stance(pos, REACH, avoid):
            return True
        if self._tower(pos, avoid):
            return True
        if self._unwedge() and self.body.goto_stance(pos, REACH, avoid):
            return True
        # Each further try is seconds of standing still; four in a row were the
        # 30-70 s pauses of run 20261110T223248667856Z. Past the budget the cell is
        # skipped and the build moves on.
        if time.monotonic() - start > STANCE_BUDGET_S:
            return False
        escape = getattr(self.body, "escape_hole", None)  # optional
        if escape is None or not escape(pos):
            return False
        self.report.escapes += 1
        return self.body.goto_stance(pos, REACH, avoid)

    def _unwedge(self) -> bool:
        """Break our own scaffolding touching the body. A column placed against the
        body's side overlapped its hitbox by 0.017 and the motor refused every step
        for minutes (run 20261110T115340557226Z); scaffolding breaks instantly and
        the column above the broken cell drops with it."""
        if not self.scaffolds:
            return False
        x, y, z = self.body.position()
        near = sorted(
            c
            for c in self.scaffolds
            if abs(c[0] - x) <= 1 and abs(c[2] - z) <= 1 and abs(c[1] - y) <= 2
        )
        freed = False
        for cell in near:  # lowest first: the rest of the column falls
            if self.body.break_block(cell).ok or not _is_scaffold(self.body, cell):
                freed = True
        for cell in [c for c in self.scaffolds if not _is_scaffold(self.body, c)]:
            self.scaffolds.discard(cell)
        return freed

    def _tower(self, pos: Pos, avoid) -> bool:
        """No stance for pos: raise a scaffolding column beside it and stand on its top
        (the body climbs it; mclab.scaffolding). Every block goes into ``scaffolds``, so
        the planner leaves it alone and cleanup takes it down. Once per target; the
        body then stays up for the rest of the cells in reach of the tower."""
        if self.scaffolds is None or self._towering or pos in self.towered:
            return False
        self.towered.add(pos)
        cells = bp.tower_cells(pos, self.body.world, self.blocks, self.reserved)
        if not cells:
            return False
        if self.body.inventory().get(sc.BLOCK, 0) < len(cells):
            # standing towers are taken back first; the whole stack is only 64
            _clean_up(self, self.template, self.scaffolds)
            if self.body.inventory().get(sc.BLOCK, 0) < len(cells):
                return False
        # Place from two cells away: beside the column the body's box overlaps it.
        cx, _, cz = cells[0]
        _, by, _ = self.body.position()
        beside = {
            (cx + dx, h, cz + dz)
            for dx in (-1, 0, 1)
            for dz in (-1, 0, 1)
            for h in range(by - 3, by + 4)
        }
        self.body.goto_stance(cells[0], REACH, set(avoid) | beside)
        self._towering = True
        try:
            for cell in cells:
                n = len(self.report.scaffolded)
                self.attempt(bp.Scaffold(cell, sc.BLOCK))
                if len(self.report.scaffolded) == n:
                    return False  # what stands is cleaned up with the rest
                self.scaffolds.add(cell)
        finally:
            self._towering = False
        if self.body.goto_stance(pos, REACH, avoid):
            return True
        # Not climbed: a tower nobody stands on is only in the way.
        self.body.break_block(cells[0])
        for cell in cells:
            if not _is_scaffold(self.body, cell):
                self.scaffolds.discard(cell)
        return False

    def _fail(self, pos: Pos, reason: str) -> None:
        self.fails[pos] = self.fails.get(pos, 0) + 1
        if self.fails[pos] >= MAX_TRIES:
            self.report.skipped[pos] = reason


def _is_scaffold(body, cell: Pos) -> bool:
    return (body.world(*cell) or "").startswith(sc.BLOCK)


def _sweep(runner: _Runner, template: bp.Template, acts: list) -> None:
    """Work one batch in walking order: lowest layer first, then everything within
    reach of where the body stands (same item as the last one first), only then the
    nearest act outside reach. The body position is re-read after every act, so the
    bot sweeps along a wall instead of crossing the house. A wall cell waits for an
    inside corner of the batch beside it (the corner must not be placed last: a
    pocket nothing sees into), which replaces the old corners-of-the-whole-layer-first.
    ponytail: the batch is not re-planned mid-sweep; a placement unlocks its
    neighbours only in the next batch."""
    pool = list(acts)
    corners = {a.pos for a in pool if bp.inside_corner(template, a.pos)}
    last, tried = None, False
    while pool and runner.report.steps < runner.max_steps:
        here = runner.body.position()

        def key(a, here=here, last=last):
            d2 = _dist2(a.pos, here)
            near = d2 <= (REACH - 0.5) ** 2
            same = getattr(a, "item", None) != last
            return (a.pos[1], not near, 0 if near else d2, same, d2)

        ready = [
            a
            for a in pool
            if a.pos in corners
            or not any(
                (a.pos[0] + dx, a.pos[1], a.pos[2] + dz) in corners
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
            )
        ]
        act = min(ready, key=key)
        if tried and _dist2(act.pos, here) > (REACH + LEAD) ** 2:
            return  # stale: only far leftovers (the batch was cut round the old spot)
        if _dist2(act.pos, here) > (REACH - 0.5) ** 2:
            # The body walks to the nearest stance that reaches its target, i.e. to the
            # edge of reach, so the next cell along the wall is out of reach again: one
            # walk per block. Stand for the act LEAD cells ahead instead; the nearest
            # one is then in reach too. ponytail: best effort, a failed walk is ignored
            # (attempt walks for the act itself); no lookahead beyond LEAD.
            lead = max(
                (
                    b
                    for b in pool
                    if b.pos[1] == act.pos[1] and _dist2(b.pos, act.pos) <= LEAD**2
                ),
                key=lambda b: _dist2(b.pos, here),
            )
            if lead is not act and hasattr(runner.body, "required_support"):
                runner.body.required_support = _support(lead, runner.blocks)
            if lead is not act:
                runner.body.goto_stance(lead.pos, REACH)
        pool.remove(act)
        corners.discard(act.pos)
        runner.attempt(act)
        last, tried = getattr(act, "item", None), True


def _clean_up(runner: _Runner, template: bp.Template, scaffolds: set) -> bool:
    """Take the scaffolds back: break them (scaffolding: only the blocks on the ground,
    the rest drops with them), then pick up what fell. A few rounds, since the blocks
    that hung from a root go a few ticks later. True if anything was broken."""
    body, broken = runner.body, False
    for _ in range(CLEANUP_ROUNDS):
        acts = [
            a
            for a in bp.cleanup_actions(template, body.world, scaffolds)
            if a.pos not in runner.report.skipped
        ]
        if not acts:
            break
        for act in acts:
            runner.attempt(act)
        broken = True
        collect = getattr(body, "collect_drops", None)  # optional, like walk_to
        if collect:
            collect([a.pos for a in acts])
    return broken


def run_build(
    template: bp.Template,
    body: BuildBody,
    blocks: dict,
    *,
    max_steps: int = 5000,
    on_need_materials: Callable[[dict[str, int]], bool] | None = None,
    scaffolds: set | None = None,
    survey: bool = False,
    on_progress: Callable[[BuildReport, bool], None] | None = None,
    site_margin: int | None = 1,
    protected: Collection[Pos] = (),
    stall_seconds: float | None = STALL_SECONDS,
    clock: Callable[[], float] | None = None,
    log: Callable[[str], None] | None = None,
) -> BuildReport:
    """Build ``template`` through ``body``; see the module docstring.

    ``site_margin``: prepare the footprint plus this many cells on every side first
    (site_prep; ``None`` skips it, negative raises ValueError). ``stall_seconds``: a
    stationary retry loop longer than this is recovered, then stopped as ``stalled``
    (progress_watch; ``None`` disables). Stops: ``site_not_ready``, ``stalled``.
    """
    report = BuildReport()
    watch = None
    if stall_seconds is not None:
        watch = ProgressWatch(limit=stall_seconds, events=report.stalls)
        if clock:
            watch.clock = clock
        if log:
            watch.log = log
        body = WatchedBody(body, watch)
        if on_need_materials:
            refill = on_need_materials

            def on_need_materials(items, _refill=refill):
                ok = _refill(items)  # gathering is bounded work of its own
                watch.progress(body.position())
                return ok

    try:
        _run(
            template,
            body,
            blocks,
            report,
            max_steps,
            on_need_materials,
            scaffolds,
            survey,
            on_progress,
            site_margin,
            protected,
            log or (lambda m: None),
        )
    except StallError as exc:
        report.stop, report.stall_reason = "stalled", str(exc)
        if log:
            log(f"STOP stalled: {exc}")
    if not report.counts:  # stopped early (stalled, site not ready)
        report.counts = bp.progress(template, body.world)
        report.unknown = bp.diff(template, body.world).unknown
    if on_progress:
        on_progress(report, True)
    return report


def _run(
    template,
    body,
    blocks,
    report,
    max_steps,
    on_need_materials,
    scaffolds,
    survey,
    on_progress,
    site_margin,
    protected,
    log,
) -> None:
    if survey:
        report.surveyed = survey_footprint(template, body)
    from mclab.tree_removal import clear_trees_in_footprint

    scaffolds = set() if scaffolds is None else scaffolds
    runner = _Runner(body, report, max_steps, blocks)
    if site_margin is not None:
        from mclab import site_prep

        site = site_prep.site_area(template, site_margin)
        area = {
            (x, y, z)
            for x, z in site.columns()
            for y in range(site.ground_y + 1, site.top_y + 1)
        }
        report.trees_cleared = clear_trees_in_footprint(body, area, template=template)

        def site_log(message: str) -> None:
            log(message)
            if on_progress:  # each round refreshes the run's progress/status
                on_progress(report, False)

        sr = site_prep.prepare_site(
            template,
            body,
            runner,
            site,
            protected=protected,
            on_need_materials=on_need_materials,
            log=site_log,
        )
        sr.trees = report.trees_cleared
        report.site = sr.summary()
        log(f"site prepared: {report.site}")
        if on_progress:
            on_progress(report, False)
        if sr.stop != "ready":
            report.stop = "site_not_ready"
            why = dict(list(sr.blocked.items())[:5]) or sr.unknown[:5] or sr.needed
            report.stall_reason = f"site {sr.stop}: {why}"
            return
    else:
        report.trees_cleared = clear_trees_in_footprint(
            body, template.cells, template=template
        )
    runner.scaffolds, runner.template = scaffolds, template
    runner.reserved = frozenset(
        p for p, s in template.cells.items() if bp.parse_state(s)[0] not in bp.AIR
    )

    def calm():
        # The route planner places no dirt while building: the body stands on
        # scaffolding towers (climbing on) instead of dirt stairs it never takes down.
        return no_placing(body, wall=BUILD_WALL, climb=True)

    recoveries = retries = 0
    fetched: set[frozenset[str]] = set()
    refused: set[frozenset] = set()
    settled = False
    while True:
        if report.steps >= max_steps:
            report.stop = "max_steps"
            break
        skipped = report.skipped
        acts = bp.next_actions(
            template,
            body.world,
            blocks,
            body.inventory(),
            limit=32 + len(skipped),
            scaffolds=scaffolds,
            skipped=skipped,
            near=body.position(),
            fetch_first=True,
        )
        fetching = False
        if acts and isinstance(acts[0], bp.NeedMaterials) and not acts[0].scaffold:
            # Fetch a lower layer's missing item once per shortage; when the chests
            # did not have it, build on with what is held rather than stop.
            held = body.inventory()
            short = frozenset(i for i in acts[0].items if held.get(i, 0) < 1)
            if short in fetched:
                acts = bp.next_actions(
                    template,
                    body.world,
                    blocks,
                    held,
                    limit=32 + len(skipped),
                    scaffolds=scaffolds,
                    skipped=skipped,
                    near=body.position(),
                )
            else:
                fetching = True
            fetched.add(short)
        if acts and isinstance(acts[0], bp.NeedMaterials):
            if acts[0].scaffold and recoveries < MAX_RECOVERIES:
                recoveries += 1  # out of scaffolds: take the standing ones back first
                with calm():
                    cleaned = _clean_up(runner, template, scaffolds)
                if cleaned:
                    continue
            report.needed = dict(acts[0].items)
            ask = frozenset(report.needed.items())
            if (
                on_need_materials is None
                or ask in refused  # the same request was already turned down
                or not on_need_materials(report.needed)
            ):
                refused.add(ask)
                if fetching:  # a lower layer's item cannot be had: build on without
                    continue
                report.stop = "need_materials"
                break
            continue
        acts = [a for a in acts if a.pos not in skipped]
        if not acts and skipped and retries < RETRY_ROUNDS:
            # The build around a skipped cell has changed since: try it again with
            # fresh stances rather than stopping (a cell is fixed, not skipped).
            retries += 1
            skipped.clear()
            runner.fails.clear()
            runner.avoid.clear()
            continue
        if not acts:
            d = bp.diff(template, body.world)
            clean = not (d.breaks or d.places or d.unknown or d.uses)
            settle = getattr(body, "wait_ticks", None)  # optional, like walk_to
            if not clean and settle and not settled:
                # the sensor may not have delivered the last placements yet (run
                # 20261102T233918895522Z: "blocked" with 1 missing, all 84 correct
                # 60 ticks later)
                settled = True
                settle(SETTLE_TICKS)
                continue
            report.stop = "done" if clean else "blocked"
            break
        with calm():
            _sweep(runner, template, acts)
        if on_progress:
            on_progress(report, False)
    if report.stop != "need_materials":
        with calm():
            _clean_up(runner, template, scaffolds)
    if report.stop == "done":
        # Cleanup walks can reopen doors after the main loop's clean audit.
        # Repair only state toggles, with automatic door opening disabled;
        # audit again after all movement, rather than reporting stale success.
        with calm(), getattr(body, "no_opening_doors", nullcontext)():
            for _ in range(CLEANUP_ROUNDS):
                uses = bp.diff(template, body.world).uses
                if not uses:
                    break
                # Scaffolds are already swept: a door without a stance must fail,
                # not raise a new column that nothing would take down.
                runner.towered.update(pos for pos, _ in uses)
                for pos, item in uses:
                    runner.attempt(bp.Use(pos, item))
                settle = getattr(body, "wait_ticks", None)
                if settle:
                    settle(5)
        final = bp.diff(template, body.world)
        if final.breaks or final.places or final.unknown or final.uses:
            report.stop = "blocked"
    report.counts = bp.progress(template, body.world)
    report.unknown = bp.diff(template, body.world).unknown


class SiteCutter:
    """Cut and clear the site in short slices while the caller would otherwise wait
    (a furnace smelting). The dirt comes up into the inventory for the later fill.
    ``slice()`` is True while cells remain to cut, False once the site is cut."""

    def __init__(self, template, body, blocks, margin, protected=(), log=None):
        from mclab import site_prep

        self.template, self.body, self.protected = template, body, tuple(protected)
        self.site = site_prep.site_area(template, margin)
        self.runner = _Runner(body, BuildReport(), 5000, blocks)
        self.log = log or (lambda m: None)
        self.trees_done = self.done = False

    def slice(self, rounds: int = 3) -> bool:
        if self.done:
            return False
        from mclab import site_prep
        from mclab.tree_removal import clear_trees_in_footprint

        site = self.site
        if not self.trees_done:
            self.trees_done = True
            area = {
                (x, y, z)
                for x, z in site.columns()
                for y in range(site.ground_y + 1, site.top_y + 1)
            }
            clear_trees_in_footprint(self.body, area, template=self.template)
            return True
        rep = site_prep.prepare_site(
            self.template,
            self.body,
            self.runner,
            site,
            protected=self.protected,
            max_rounds=rounds,
            cut_only=True,
            log=self.log,
        )
        self.done = rep.stop == "cut_done"
        return not self.done and rep.stop == "max_rounds"
