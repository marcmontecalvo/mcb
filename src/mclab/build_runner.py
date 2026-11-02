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

The loop treats ``out_of_reach`` and ``state_unreachable`` as "wrong stance": it
asks for another stance (3 tries per cell, then the cell is skipped). Any other
failure counts one try and the plan is recomputed. ``on_need_materials`` is where
the gathering planner plugs in: it receives ``{item: count}`` and returns True
once the inventory has been refilled, False to stop.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import NamedTuple, Protocol

from mclab import build_plan as bp
from mclab.build_plan import Pos

REACH = 4.5
MAX_TRIES = 3
CLEANUP_ROUNDS = 3
MAX_RECOVERIES = 3
STANCE_REASONS = frozenset({"out_of_reach", "state_unreachable"})


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
    stop: str = ""  # done | blocked | need_materials | max_steps
    needed: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)  # build_plan.progress
    unknown: list[Pos] = field(default_factory=list)  # template cells never delivered
    surveyed: int = 0  # perimeter points visited (B4)

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


class _Runner:
    def __init__(self, body: BuildBody, report: BuildReport, max_steps: int):
        self.body, self.report, self.max_steps = body, report, max_steps
        self.fails: dict[Pos, int] = {}
        self.avoid: dict[Pos, set[Pos]] = {}

    def attempt(self, act) -> None:
        """Stance, act, record. Retries other stances while the failure says so."""
        pos = act.pos
        while self.fails.get(pos, 0) < MAX_TRIES and self.report.steps < self.max_steps:
            self.report.steps += 1
            avoid = self.avoid.setdefault(pos, set())
            if not self.body.goto_stance(pos, REACH, avoid):
                return self._fail(pos, "no_stance")
            stance = self.body.position()
            result = _do(self.body, act)
            if result.ok:
                return _record(self.report, act)
            self._fail(pos, result.reason or "failed")
            if result.reason not in STANCE_REASONS:
                return
            avoid.add(stance)

    def _fail(self, pos: Pos, reason: str) -> None:
        self.fails[pos] = self.fails.get(pos, 0) + 1
        if self.fails[pos] >= MAX_TRIES:
            self.report.skipped[pos] = reason


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
) -> BuildReport:
    """Build ``template`` through ``body``; see the module docstring."""
    report = BuildReport()
    if survey:
        report.surveyed = survey_footprint(template, body)
    scaffolds = set() if scaffolds is None else scaffolds
    runner = _Runner(body, report, max_steps)
    recoveries = 0
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
            limit=8 + len(skipped),
            scaffolds=scaffolds,
        )
        if acts and isinstance(acts[0], bp.NeedMaterials):
            if acts[0].scaffold and recoveries < MAX_RECOVERIES:
                recoveries += 1  # out of scaffolds: take the standing ones back first
                if _clean_up(runner, template, scaffolds):
                    continue
            report.needed = dict(acts[0].items)
            if on_need_materials is None or not on_need_materials(report.needed):
                report.stop = "need_materials"
                break
            continue
        acts = [a for a in acts if a.pos not in skipped]
        if not acts:
            d = bp.diff(template, body.world)
            clean = not (d.breaks or d.places or d.unknown or d.uses)
            report.stop = "done" if clean else "blocked"
            break
        here = body.position()
        acts.sort(key=lambda a: _dist2(a.pos, here))
        for act in acts:
            runner.attempt(act)
    if report.stop != "need_materials":
        _clean_up(runner, template, scaffolds)
    report.counts = bp.progress(template, body.world)
    report.unknown = bp.diff(template, body.world).unknown
    return report
