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

import functools
import math
import time
from collections.abc import Callable, Collection
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import NamedTuple, Protocol

from mclab import build_checkpoint as ck
from mclab import build_plan as bp
from mclab import scaffolding as sc
from mclab.build_plan import Pos
from mclab.place_graph import Knowledge
from mclab.progress_watch import (
    STALL_SECONDS,
    BodyDied,
    ProgressWatch,
    StallError,
    WatchedBody,
    no_placing,
    walking_with_edits,
)

REACH = 4.5
MAX_TRIES = 3
STALE_MARGIN = 3  # a sweep ends when its nearest act is this far beyond reach
COVER_CELLS = 8  # next cells a sweep's stance is ranked by (stance_cover)
COVER_SPAN = 6  # ... within this many blocks of the act walked for
CLEANUP_ROUNDS = 3
MAX_RECOVERIES = 3
BUILD_WALL = 30.0  # seconds a build walk may take before the route is given up
SETTLE_TICKS = 60  # wait for the sensor before calling a build blocked
# Where a build can be told to end early: at the site_ready or structure_done
# milestone (a stage lab, build_checkpoint.STAGES), or a storeys build's band_N
# (build_checkpoint.BAND); None builds everything.
UNTIL = (None, "site", "structure")


class _BandStages:
    """The band_N milestones of a storeys build: band N is finished once the
    structure (no details, no doors) of bands 0..N (bp.storey_bands) is in."""

    def __init__(self, template: bp.Template):
        bands = bp.storey_bands(template)
        cells: dict[int, list] = {}
        for p, s in template.cells.items():
            if not bp.is_structure(s):
                continue
            cells.setdefault(bands[p[1]], []).append((p, s))
        top = max(bands.values(), default=-1)
        self.cells = [cells.get(b, []) for b in range(top + 1)]
        self.next = 0  # the lowest band not yet finished

    def finished(self, world) -> list[int]:
        """The bands finished since the last call, bottom up."""
        out = []
        while self.next < len(self.cells) and all(
            bp._classify(s, world(*p), bp.DEFAULT_IGNORE, False) == "ok"
            for p, s in self.cells[self.next]
        ):
            out.append(self.next)
            self.next += 1
        return out


# A broken bridge root: scaffolding past distance 6 falls a tick a cell, and the
# items then drop to the ground (a run of 6 and a few blocks of fall).
COLLAPSE_TICKS = 12
RETRY_ROUNDS = 2  # fresh tries for skipped cells before stopping blocked
STANCE_BUDGET_S = 5.0  # a pause longer than this reads as a broken bot
FOOTHOLD_STOCK = 32  # spare blocks to climb or step down on, held from the start
FOOTHOLD_LOW = 8  # fewer spare than this mid-build: top the stock back up
FOOTHOLD_RETRY = 40  # build actions before a refill that brought none is tried again
PROP_BLOCK = "minecraft:dirt"  # asked for when a cell needs a prop and none is held
FOOTHOLD_ITEM = "minecraft:dirt"
# Taking an idle column down for its blocks (_reclaim_cost), in blocks of sprinting.
# A climb goes at the vanilla ladder/scaffolding limit, 0.12 blocks/tick up and 0.15
# down, against ~0.28 sprinting: each block of height costs about 2.5 blocks of walk.
CLIMB_COST = 2.5
REACH_UP = 3  # a column foot this far above the floor is broken from the floor
# A column in a zone still being built is wanted again: putting it back up costs a
# place per block on top of the walk there (run 20261120T101447633151Z).
REBUILD_COST = 4.0
# entity_in_way: a mob or player on the ray that did not move off within ~1 s
STANCE_REASONS = frozenset({"out_of_reach", "state_unreachable", "entity_in_way"})
FACING_STEP = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "east": (1, 0, 0),
    "west": (-1, 0, 0),
    "up": (0, 1, 0),
    "down": (0, -1, 0),
}


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
    # blocks the planner broke to walk: put back / why not
    restored: list[Pos] = field(default_factory=list)
    unrestored: dict[Pos, str] = field(default_factory=dict)
    trees_cleared: int = 0  # trees felled by Tree Removal skill
    site: dict = field(default_factory=dict)  # site_prep.SiteReport.summary()
    stalls: list[dict] = field(default_factory=list)  # progress_watch events
    long_stops: list[dict] = field(default_factory=list)  # stalls and >5s ops
    stall_reason: str = ""  # why a "stalled" or "site_not_ready" stop happened
    # (cell, reason, stance, body diagnosis) of every failed attempt, to diagnose from
    failures: list[tuple[Pos, str, Pos, dict]] = field(default_factory=list)
    diff: dict = field(default_factory=dict)  # what the loop saw when it ended blocked

    def progress(self) -> float:
        """Fraction of cells that are correct (1.0 for an empty template)."""
        total = sum(self.counts.values())
        return self.counts.get("correct", 0) / total if total else 1.0


SURVEY_STEP = 6
SURVEY_RADIUS = 3.0
SURVEY_NEAR = 24  # a perimeter point is walked only with an unseen cell this close


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
    # Only cells the sensor has not delivered are worth a walk, and only perimeter
    # points near them: the live sensor already covers a 64-block radius, and one
    # point two blocks over a pond (only a bridge reaches it) held run
    # 20261117T031430547551Z for 100 s of node-limit plans before any building.
    # A survey walk never builds its way there (no_placing).
    unknown = bp.diff(template, body.world).unknown
    walk = getattr(body, "walk_to", None)
    done = 0
    for pt in perimeter(template):
        if not unknown:
            break
        if not any(
            _dist2(pt[::2], u[::2]) <= SURVEY_NEAR * SURVEY_NEAR for u in unknown
        ):
            continue
        with no_placing(body):
            ok = walk(pt) if walk else body.goto_stance(pt, SURVEY_RADIUS)
        done += bool(ok)
        unknown = bp.diff(template, body.world).unknown
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
    if kind in ("facing_opposite", "any_face", "above"):
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


def _support_for(act, world, blocks: dict) -> Pos | set[Pos] | None:
    sup = _support(act, blocks)
    if sup is not None or not isinstance(act, bp.Place):
        return sup
    ident, props = bp.parse_state(act.state)
    if ident == "minecraft:hopper" and world is not None:
        step = FACING_STEP.get(props.get("facing", ""))
        if step:
            cell = (
                act.pos[0] + step[0],
                act.pos[1] + step[1],
                act.pos[2] + step[2],
            )
            if bp._is_solid(world(*cell), blocks):
                return cell
    return None


def _state_of(act) -> str | None:
    """The block state ``act`` puts down: a slab's half, a log's axis or a hopper's
    facing decides which faces a click can make it from (place_state)."""
    if isinstance(act, bp.Place):
        return act.state
    if isinstance(act, bp.Scaffold):
        return act.item
    return None


# The optional attributes a body chooses its stance by (CompanionBody): set per
# placement, kept by a nested one (a tower, a temporary block) for its caller.
AIM = ("required_support", "placing_scaffold", "placing_state")


@contextmanager
def _aim_kept(body):
    saved = {name: getattr(body, name) for name in AIM if hasattr(body, name)}
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(body, name, value)


def _aim(body, act, support=None) -> None:
    """Tell a body that picks stances what the next click must make: the support
    face it hangs on, scaffolding held, and the state."""
    if hasattr(body, "required_support"):
        body.required_support = support
    if hasattr(body, "placing_scaffold"):
        body.placing_scaffold = getattr(act, "item", None) == sc.BLOCK
    if hasattr(body, "placing_state"):
        body.placing_state = _state_of(act)


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
        # scaffolding a tower lacked: _run asks for it once, or stops the build
        self.short: dict[str, int] = {}
        self.short_for: Pos | None = None  # the cell the short tower was for
        self.propped: set[Pos] = set()  # cells a temporary block was put beside
        # the build order (bp.ORDERS); "storeys" sweeps each column up from one stance
        self.order = bp.ORDERS[0]
        self.held: set[Pos] = set()  # storeys: cells this sweep failed (nothing over)

    def attempt(self, act) -> None:
        """Stance, act, record. Retries other stances while the failure says so."""
        if self.report.steps >= self.max_steps:
            return
        pos = act.pos
        world = getattr(self.body, "world", None)
        if (
            isinstance(act, bp.Place)
            and world is not None
            and bp._gate(pos, act.state, world, self.blocks) == "floating"
        ):
            # Offered beside a cell planned in the same batch (next_actions counts the
            # batch as in) that is not in: it waits for the next plan, no try counted.
            # Run 20261122T071747687093Z took such a roof plank for a prop case, gave
            # it up, put the slab over it on the released column and walled it in.
            return None
        here = self.body.position()
        can_place = getattr(self.body, "can_place_here", None)
        support = _support_for(act, getattr(self.body, "world", None), self.blocks)
        _aim(self.body, act, support)
        # Scaffolding too: can_place_here answers for it by the column and run rules
        # (placing_scaffold, set by _aim), so a far column top is still clicked from
        # here and a cell out of every reach is walked to, not sent to the server to
        # fail (review of e029b74: a Scaffold act clicked from 71 blocks away).
        direct = (
            not isinstance(act, (bp.Place, bp.Scaffold))
            or can_place is None
            or can_place(pos, REACH, support, _state_of(act))
        )
        if direct and self._traps(act, here):
            direct = False
            self.avoid.setdefault(pos, set()).add(here)
        # A body with can_place_here answered for a Place with the server's own test
        # (real eye, support faces, reach), so no cell-distance guess overrides it:
        # the guess sent the body on gotos that ended where it stood (run
        # 20261119T225430670386Z). Breaks and uses keep the distance guard.
        judged = can_place is not None and (
            isinstance(act, bp.Place)
            or (
                isinstance(act, bp.Scaffold)
                and getattr(self.body, "placing_scaffold", False)
            )
        )
        if direct and (judged or _dist2(here, pos) <= (REACH - 0.5) ** 2):
            result = _do(self.body, act)
            if result.ok:
                self.report.steps += 1
                _take_back(self, act)
                return _record(self.report, act)

        while self.fails.get(pos, 0) < MAX_TRIES and self.report.steps < self.max_steps:
            self.report.steps += 1
            avoid = self.avoid.setdefault(pos, set())
            # A block that hangs on one face (a wall torch) needs a stance that sees
            # that face, a top slab one that sees the upper half of a side face;
            # optional body attributes, like walk_to. Set again each try: a tower or
            # a temporary block in between aims at other cells.
            _aim(
                self.body,
                act,
                _support_for(act, getattr(self.body, "world", None), self.blocks),
            )
            # When no currently solid neighbour can produce ``act.state`` (e.g.
            # mutually facing hoppers), plan the temporary prop up front instead of
            # letting stance search fail and raise a useless tower.
            if isinstance(act, bp.Place) and bp.prop_cells(
                pos, act.state, getattr(self.body, "world", None), self.blocks
            ):
                if self._prop_item() is None:
                    # No block to prop with: ask for one. Climbing cannot make the
                    # click face, so a stance search and a tower here only ended in a
                    # refused dirt ask (run 20261121T222050257793Z, two hoppers).
                    self.short = _hold_more(self.body, PROP_BLOCK, 1)
                    self.short_for = pos
                    self.report.failures.append(
                        (pos, "no_prop_block", self.body.position(), {})
                    )
                    if self.template is not None:
                        self.fails[pos] = MAX_TRIES
                    return self._fail(pos, "no_material")
                if self._propped(act, avoid):
                    _take_back(self, act)
                    return _record(self.report, act)
                self.report.failures.append(
                    (pos, "no_stance", self.body.position(), {})
                )
                if self.template is not None:
                    self.fails[pos] = MAX_TRIES
                return self._fail(pos, "no_stance")
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
            if self._traps(act, stance):
                # placed from here it would take the body's last way off this spot
                # (glass behind an eave slab between flower pots): another stance
                self.report.failures.append((pos, "would_trap", stance, {}))
                avoid.add(stance)
                continue
            result = _do(self.body, act)
            if result.ok:
                _take_back(self, act)
                return _record(self.report, act)
            detail = (result.data or {}).get("diagnosis") or {}
            self.report.failures.append(
                (pos, result.reason or "failed", stance, detail)
            )
            if result.reason == "state_unreachable" and self._propped(act, avoid):
                _take_back(self, act)
                return _record(self.report, act)
            if _blind(detail) and self._tower(pos, avoid):
                # Every face in reach hidden (a chain under the roof tried from the
                # floor five below, through the stairs and the tower's own column:
                # run 20261118T094938114426Z): from the top of a tower beside the
                # cell the eye is level with it.
                result = _do(self.body, act)
                if result.ok:
                    _take_back(self, act)
                    return _record(self.report, act)
            self._fail(pos, result.reason or "failed")
            if result.reason not in STANCE_REASONS:
                return
            avoid.add(stance)

    def _propped(self, act, avoid=()) -> bool:
        """A block whose state needs a click face with nothing solid on that side yet
        (two hoppers facing each other, run 20261118T070135016043Z &
        20261120T175636584118Z): put a temporary block there, place the block against
        it, and take the temporary back. Prefers a single stance that can place both
        the temporary and the target block. Once per cell (reset on retry rounds)."""
        if not isinstance(act, bp.Place) or act.pos in self.propped:
            return False
        self.propped.add(act.pos)
        ident, props = bp.parse_state(act.state)
        item = self._prop_item()
        if item is None:
            return False
        candidates = bp.prop_cells(act.pos, act.state, self.body.world, self.blocks)
        if not candidates:
            step = FACING_STEP.get(props.get("facing", ""))
            if step is None:
                return False
            x, y, z = act.pos
            signs = (1,) if ident == "minecraft:hopper" else (1, -1)
            candidates = [
                (x + sign * step[0], y + sign * step[1], z + sign * step[2])
                for sign in signs
                if bp.parse_state(
                    self.body.world(
                        x + sign * step[0], y + sign * step[1], z + sign * step[2]
                    )
                    or ""
                )[0]
                in bp.AIR
            ]
        can_place = getattr(self.body, "can_place_here", None)
        goto_prop = getattr(self.body, "goto_prop_stance", None)
        tx, ty, tz = act.pos
        avoid_target = set(avoid) | {act.pos, (tx, ty - 1, tz)}
        for cell in candidates:
            if bp.parse_state(self.body.world(*cell) or "")[0] not in bp.REPLACEABLE:
                continue
            with _aim_kept(self.body):
                temp = bp.Place(cell, item, item)
                _aim(self.body, temp)
                shared = False
                if goto_prop is not None:
                    shared = goto_prop(cell, item, act.pos, act.state, REACH, avoid)
                if not shared:
                    if can_place is not None and not can_place(cell, REACH, None, item):
                        self.body.goto_stance(cell, REACH, avoid_target)
                if self._traps(temp, self.body.position()):
                    continue
                if not self.body.place_block(cell, item, item).ok:
                    continue
                _aim(self.body, act, cell)
                if can_place is not None and not can_place(
                    act.pos, REACH, cell, act.state
                ):
                    self.body.goto_stance(act.pos, REACH, avoid)
                if self._traps(act, self.body.position()):
                    result = Result(False, "would_trap")
                else:
                    result = _do(self.body, act)
            broke = self.body.break_block(cell)
            if not broke.ok and getattr(self.body, "goto_stance", None):
                self.body.goto_stance(cell, REACH)
                broke = self.body.break_block(cell)
            if broke.ok:
                collect = getattr(self.body, "collect_drops", None)  # optional
                if collect:
                    with no_placing(self.body):
                        collect([cell])
            elif self.scaffolds is not None and _standing(self.body, cell):
                self.scaffolds.add(cell)
            if result.ok:
                return True
        return False

    def _traps(self, act, stance: Pos) -> bool:
        return isinstance(act, bp.Place) and bp.traps(self.body.world, stance, act.pos)

    def _stance(self, pos: Pos, avoid) -> bool:
        """A stance for pos; if none is reachable, climb or dig out of a hole the body
        is in (an optional body method, like walk_to) and try once more."""
        start = time.monotonic()
        if self.body.goto_stance(pos, REACH, avoid):
            return True
        # Shut in: the search walked the body's whole region and nothing leaves it (a
        # ledge or roof slope the house closed around; runs 20261122T024835715542Z,
        # 9 nodes, and 20261122T125350762344Z, 40). Only here is a drop that hurts
        # taken; standing in one of the house's own unbuilt cells, the body climbs
        # out on that cell's block instead. Boxed in, or with the goal below, it gets
        # out first and climbs back by stairs or a tower like any other climb. With
        # the goal level or above (a closed floor), a tower comes first, and getting
        # down stays the way out when no tower reaches it.
        boxed = getattr(self.body, "boxed_in", False)
        shut = boxed or getattr(self.body, "shut_in", False)
        out_first = boxed or (shut and pos[1] < self.body.position()[1])
        if out_first:
            if not self._get_out():
                return False
            if self.body.goto_stance(pos, REACH, avoid):
                return True
        if self._tower(pos, avoid):
            return True
        if (
            not out_first
            and getattr(self.body, "shut_in", False)
            and self._get_out()
            and self.body.goto_stance(pos, REACH, avoid)
        ):
            return True
        if self.short:
            return False
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

    def _get_out(self) -> bool:
        """Out of a region with no way off: climb out of an own unbuilt cell, else
        take a drop."""
        return self._climb_out() or self._get_down()

    def _climb_out(self) -> bool:
        """The house closed round the body standing in one of its own unbuilt cells
        (run 20261122T045703502025Z: a roof cell of planks, the roof's stairs and slabs
        all round, nothing lower in reach): put that cell's own block under the body
        and step out, a pillar of one, as a person does. Only a plain full cube is
        placed so (temporary_ok: no facing or half; an axis comes out upright from
        above), which is the template's state whichever way the body looks."""
        pillar = getattr(self.body, "pillar_to", None)
        plain = getattr(self.body, "temporary_ok", None)
        if pillar is None or plain is None or self.template is None:
            return False
        x, y, z = self.body.position()
        state = self.template.cells.get((x, y, z))
        if state is None:
            return False
        block, props = bp.parse_state(state)
        if (
            block not in plain
            or block == sc.BLOCK
            or set(props) - {"axis"}
            or props.get("axis", "y") != "y"
            or self.body.inventory().get(block, 0) < 1
        ):
            return False
        return bool(pillar((x, y + 1, z), block, 1, release=(x, y, z)))

    def _get_down(self) -> bool:
        """Boxed in on a ledge: the body's optional ``get_down`` takes the shortest
        drop it has the health for, and the body eats to heal after."""
        get_down = getattr(self.body, "get_down", None)
        if get_down is None or not get_down():
            return False
        heal = getattr(self.body, "eat_when_needed", None)  # optional
        if heal is not None:
            heal()
        return True

    def _unwedge(self) -> bool:
        """Break our own scaffolding touching the body. A column placed against the
        body's side overlapped its hitbox by 0.017 and the motor refused every step
        for minutes (run 20261110T115340557226Z); scaffolding breaks instantly and
        the column above the broken cell drops with it."""
        if not self.scaffolds:
            return False
        x, y, z = self.body.position()
        fx, fz = math.floor(x), math.floor(z)

        def underfoot(c: Pos) -> bool:
            return c[0] == fx and c[2] == fz and c[1] < y

        # Beside the body, lowest first (the rest of a scaffolding column falls).
        # The column under the feet is dug straight down, top first: its lower block
        # is out of sight behind the one stood on (run 20261117T165351012696Z: 20
        # no_line_of_sight breaks of it, then trapped).
        near = sorted(
            (
                c
                for c in self.scaffolds
                if abs(c[0] - x) <= 1
                and abs(c[2] - z) <= 1
                and abs(c[1] - y) <= 2
                # one already gone is no wedge: breaking it again only failed
                # nothing_to_break (run 20261117T200337044791Z)
                and _standing(self.body, c)
            ),
            key=lambda c: (underfoot(c), -c[1] if underfoot(c) else c[1]),
        )
        freed = False
        for cell in near:  # lowest first: the rest of the column falls
            if not _standing(self.body, cell):
                continue  # fell with the cell broken under it
            if self.body.break_block(cell).ok:
                freed = True
        for cell in [c for c in self.scaffolds if not _standing(self.body, c)]:
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
        bridge = self._bridge_plan(pos, avoid)
        reach_fn = getattr(self.body, "can_reach_from_tower", None)
        listed = getattr(self.body, "stances", None)
        allow_roof_lift = listed is None or any(
            n[1] >= pos[1] for n in listed(pos, REACH, avoid)
        )
        can_reach_scaff = (
            (
                lambda foot, tc: reach_fn(
                    pos, REACH, foot, tc, scaffolding=True, avoid=avoid
                )
            )
            if reach_fn is not None
            else None
        )
        can_reach_dirt = (
            (
                lambda foot, tc: reach_fn(
                    pos, REACH, foot, tc, scaffolding=False, avoid=avoid
                )
            )
            if reach_fn is not None
            else None
        )
        cells = bp.tower_cells(
            pos,
            self.body.world,
            self.blocks,
            self.reserved,
            roof=True,
            scaffolding=True,
            can_reach=can_reach_scaff,
            allow_roof_lift=allow_roof_lift,
        )
        dirt_only = False
        if not cells and not getattr(self.body, "scaffold_only", False):
            cells = bp.tower_cells(
                pos,
                self.body.world,
                self.blocks,
                self.reserved,
                roof=True,
                scaffolding=False,
                can_reach=can_reach_dirt,
                allow_roof_lift=allow_roof_lift,
            )
            dirt_only = bool(cells)
        # A few blocks off a tower already standing beat a new one: no climb from
        # the ground, no idle tower taken down for it (house-large run
        # 20261120T141244368734Z put a second tower beside the first for want of
        # one block off its top).
        if (
            bridge
            and (not cells or dirt_only or len(bridge[1]) <= len(cells))
            and self._bridge(pos, avoid, *bridge)
        ):
            return True
        if cells:
            # Scaffolding the new tower lacks comes off idle towers before dirt goes
            # up: counting dirt as cover left the scaffolding standing in the
            # unfinished wing while dirt pillars climbed the walls (run
            # 20261119T225430670386Z). Retry while idle structures keep yielding.
            for _ in range(CLEANUP_ROUNDS):
                lacking = self._lacking(len(cells), not dirt_only)
                if lacking is None:
                    break
                if not self._reclaim(pos, cells[0], avoid, lacking):
                    break
        if not cells:
            # Every neighbouring column is the template itself (an interior roof
            # cell): the stance is the roof's edge, reached by pillaring or stairs of
            # placed blocks. A body holding none has nothing to plan with (run
            # 20261116T224011482858Z: 8 roof cells no_stance, dirt never asked for).
            item, items = self._footholds()
            held = self.body.inventory()
            have = sum(held.get(i, 0) for i in items)
            need = max(1, pos[1] - self.body.position()[1])
            if have < need:
                self.short = _hold_more(self.body, item, need - have)
                self.short_for = pos
                self.towered.discard(pos)
            return False
        if dirt_only:
            held = self.body.inventory()
            item = next(
                (
                    i
                    for i in self._temporaries(scaffolding=False)
                    if held.get(i, 0) >= len(cells)
                ),
                None,
            )
            if item is None:
                have = sum(held.get(i, 0) for i in self._temporaries(scaffolding=False))
                self.short = _hold_more(
                    self.body, "minecraft:dirt", max(1, len(cells) - have)
                )
                self.short_for = pos
                self.towered.discard(pos)
                return False
            return self._pillar(pos, cells, item, avoid)
        item = self._scaffold_item(len(cells))
        if item is None:
            # standing towers are taken back first; the whole stack is only 64
            _clean_up(self, self.template, self.scaffolds)
            item = self._scaffold_item(len(cells))
            if item is None:
                # Without a tower every cell up there is one more round of failed
                # stance probes: 27 roof cells, 5-12 s of standing still each (run
                # 20261112T143613464413Z). Ask for the scaffolding instead.
                self.short = {sc.BLOCK: len(cells)}
                self.short_for = pos
                self.towered.discard(pos)
                return False
        if item != sc.BLOCK:
            return self._pillar(pos, cells, item, avoid)
        # Place from two cells away: beside the column the body's box overlaps it.
        cx, _, cz = cells[0]
        _, by, _ = self.body.position()
        beside = {
            (cx + dx, h, cz + dz)
            for dx in (-1, 0, 1)
            for dz in (-1, 0, 1)
            for h in range(by - 3, by + 4)
        }
        # The column's own clicks, not the cell it is for (a top slab's half or a
        # torch's wall), choose the stances while it goes up.
        with _aim_kept(self.body), ExitStack() as done:
            _aim(self.body, bp.Scaffold(cells[0], item))
            self.body.goto_stance(cells[0], REACH, set(avoid) | beside)
            self._towering = True
            done.callback(setattr, self, "_towering", False)
            for cell in cells:
                if bp.parse_state(self.body.world(*cell) or "")[0] == sc.BLOCK:
                    self.scaffolds.add(cell)  # the column is up to here already
                    continue
                n = len(self.report.scaffolded)
                was_skipped = self.report.skipped.get(cell)
                self.attempt(bp.Scaffold(cell, item))
                if len(self.report.scaffolded) == n:
                    if was_skipped is None:
                        self.report.skipped.pop(cell, None)
                    self.fails.pop(cell, None)
                    self.avoid.pop(cell, None)
                    return False  # what stands is cleaned up with the rest
                self.scaffolds.add(cell)
        # The tower's top, not a floor stance nearer by: walkable stances come first,
        # and the floor is what failed (run 20261118T094938114426Z).
        tops = {(cx, cells[-1][1] + 1, cz), cells[-1]}  # on, or in, the top cell
        listed = getattr(self.body, "stances", None)  # optional
        if listed and cells[-1][1] >= pos[1]:
            others = {
                n for n in listed(pos, REACH, avoid) if n not in tops and n[1] <= pos[1]
            }
        else:
            others = set(listed(pos, REACH, avoid)) - tops if listed else set()
        if self.body.goto_stance(pos, REACH, set(avoid) | others):
            return True
        # Not climbed: a tower nobody stands on is only in the way; wait for its
        # collapse and pick the scaffolding back up so the stack is not bled dry
        # (run 20261120T175636584118Z abandoned 15+ blocks on failed climbs).
        self._drop(cells)
        return False

    def _bridge_plan(self, pos: Pos, avoid) -> tuple[Pos, list[Pos]] | None:
        """The stance in the air for ``pos`` (the body's optional ``bridge_stances``)
        that the fewest scaffolding blocks off a standing tower's top hold up
        (bp.bridge_cells: straight, L or U, up to six off the tower), and those
        blocks, the tower's side first. None when no bridge reaches one."""
        listed = getattr(self.body, "bridge_stances", None)
        if listed is None:
            return None
        best = None
        for node in listed(pos, REACH, avoid):
            floor = (node[0], node[1] - 1, node[2])
            cells = bp.bridge_cells(floor, self.body.world, self.blocks, self.reserved)
            if cells and (best is None or len(cells) < len(best[1])):
                best = (node, cells)
                if len(cells) == 1:
                    break
        return best

    def _structure(self, start: Pos) -> set[Pos]:
        """The standing scaffolding connected to ``start``'s neighbours (the tower
        and bridges a new bridge hangs from)."""
        world = self.body.world
        todo = [
            n
            for n in (bp._add(start, v) for v in bp._AROUND)
            if bp._scaffolding(world(*n))
        ]
        seen = set(todo)
        while todo and len(seen) < bp._DISTANCE_FLOOD:
            p = todo.pop()
            for v in bp._AROUND:
                n = bp._add(p, v)
                if n not in seen and bp._scaffolding(world(*n)):
                    seen.add(n)
                    todo.append(n)
        return seen

    def _bridge(self, pos: Pos, avoid, node: Pos, cells: list[Pos]) -> bool:
        """Put ``cells`` down off the tower's top, the tower's side first (each from
        on top of the last), and stand on ``node`` above the last one. Short of
        scaffolding, idle towers come down first, never the one bridged from."""
        keep = {c[::2] for c in self._structure(cells[0])}
        self._reclaim(pos, cells[0], avoid, self._lacking(len(cells), True), keep)
        if self._lacking(len(cells), True) is not None:
            return False
        with _aim_kept(self.body), ExitStack() as done:
            _aim(self.body, bp.Scaffold(cells[0], sc.BLOCK))
            self._towering = True
            done.callback(setattr, self, "_towering", False)
            for cell in cells:
                if bp._scaffolding(self.body.world(*cell)):
                    self.scaffolds.add(cell)
                    continue
                n = len(self.report.scaffolded)
                was_skipped = self.report.skipped.get(cell)
                self.attempt(bp.Scaffold(cell, sc.BLOCK))
                if len(self.report.scaffolded) == n:
                    if was_skipped is None:
                        self.report.skipped.pop(cell, None)
                    self.fails.pop(cell, None)
                    self.avoid.pop(cell, None)
                    self._drop(cells)
                    return False
                self.scaffolds.add(cell)
        listed = getattr(self.body, "stances", None)  # optional
        others = set(listed(pos, REACH, avoid)) - {node} if listed else set()
        if self.body.goto_stance(pos, REACH, set(avoid) | others):
            return True
        self._drop(cells)
        return False

    def _drop(self, cells: list[Pos]) -> None:
        """A bridge or tower nobody stands on is only in the way: break its base
        and the rest falls with it; once it has fallen, pick the scaffolding up
        without placing new footholds (left lying, blocks bled the 64 stack in runs
        20261120T141244368734Z and 20261120T175636584118Z)."""
        if _standing(self.body, cells[0]) and self.body.break_block(cells[0]).ok:
            if settle := getattr(self.body, "wait_ticks", None):  # optional
                settle(COLLAPSE_TICKS)
            if collect := getattr(self.body, "collect_drops", None):  # optional
                with no_placing(self.body):
                    collect(list(cells))
        gone = {c for c in cells if not _standing(self.body, c)}
        self.scaffolds -= gone
        if isinstance(strays := getattr(self.body, "stray_footholds", None), set):
            strays -= gone

    def _lacking(self, n: int, scaffolding: bool) -> tuple[frozenset[str], int] | None:
        """The blocks a tower of ``n`` would be built of (scaffolding, or else the
        other temporaries) and how many of them the body lacks; None when it holds
        enough spare."""
        held, still = self.body.inventory(), _still_needed(self.body)
        items = [sc.BLOCK] if scaffolding else self._temporaries(scaffolding=False)
        have = max((held.get(i, 0) - still.get(i, 0) for i in items), default=0)
        return None if have >= n else (frozenset(items), n - have)

    def _sturdy_below(self, p: Pos) -> bool:
        below = self.body.world(p[0], p[1] - 1, p[2])
        return (
            below is not None
            and bp.parse_state(below)[0] not in bp.REPLACEABLE
            and bp.parse_state(below)[0] != sc.BLOCK
        )

    def _scaffold_components(self, seeds: set[Pos]) -> list[set[Pos]]:
        """Connected standing scaffolding structures in the world touching ``seeds``."""
        world = self.body.world
        visited: set[Pos] = set()
        comps: list[set[Pos]] = []
        for start in sorted(seeds):
            if start in visited or not bp._scaffolding(world(*start)):
                continue
            comp = {start}
            todo = [start]
            while todo and len(comp) < bp._DISTANCE_FLOOD:
                p = todo.pop()
                for v in bp._AROUND:
                    n = bp._add(p, v)
                    if n not in comp and bp._scaffolding(world(*n)):
                        comp.add(n)
                        todo.append(n)
            visited |= comp
            comps.append(comp)
        return comps

    def _reclaim(
        self,
        pos: Pos,
        base: Pos,
        avoid,
        lacking: tuple[frozenset[str], int] | None,
        keep_columns=frozenset(),
    ) -> bool:
        """Before raising a tower short of blocks, take down idle structures and
        footholds holding them and pick them up: one stack of scaffolding moves
        round the house instead of standing where it was first put up while dirt
        steps go up the walls (run 20261117T130121185147Z: every tower stood to the
        end). ``lacking`` (items, n): the new tower is short of n of those items;
        None: nothing comes down, the rest comes down at cleanup.

        Scaffolding is evaluated as whole connected structures (via ``sc.roots``
        and ``sc.survivors``): picking hanging columns without their roots yielded
        zero breaks, and breaking one root of a bridged structure left the rest
        standing (run 20261120T175636584118Z). Step-placed footholds are adopted
        before ranking so they are reclaimable too. Returns True if anything was
        broken."""
        if lacking is None:
            return False
        if self.template is not None and self.scaffolds is not None:
            _adopt_footholds(self.body, self.template, self.scaffolds)
        keep = {(pos[0] + dx, pos[2] + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        keep |= set(keep_columns)
        idle = {c for c in self.scaffolds or () if c[::2] not in keep}
        if not idle:
            return False
        done = self._to_cover(base, idle, *lacking, keep)
        if not any(_standing(self.body, c) for c in done):
            return False
        self.body.goto_stance(base, REACH, avoid)
        if self.template is not None and self.scaffolds is not None:
            _adopt_footholds(self.body, self.template, self.scaffolds)
        bx, _, bz = self.body.position()
        body_keep = {
            (math.floor(bx) + dx, math.floor(bz) + dz)
            for dx in (-1, 0, 1)
            for dz in (-1, 0, 1)
        }
        all_keep = keep | body_keep
        idle = {c for c in self.scaffolds or () if c[::2] not in all_keep}
        done = self._to_cover(base, idle, *lacking, all_keep)
        if not any(_standing(self.body, c) for c in done):
            return False
        return self._take_down(done)

    def _to_cover(
        self,
        base: Pos,
        idle: set,
        items: frozenset,
        n: int,
        keep_columns=frozenset(),
    ) -> set:
        """Whole idle scaffolding structures (all removable roots + everything that
        falls with them) and solid columns, cheapest per actually dropped block of
        ``items`` first (``_reclaim_cost``), until their yield makes up ``n``."""
        open_zones = self._open_zones()
        ranked: list[tuple[float, tuple[int, int], int, set[Pos]]] = []
        if sc.BLOCK in items:
            scaff_idle = {c for c in idle if bp._scaffolding(self.body.world(*c))}
            for comp in self._scaffold_components(scaff_idle):
                all_roots = sc.roots(comp, self._sturdy_below)
                rem_roots = [
                    r
                    for r in all_roots
                    if r[::2] not in keep_columns
                    and r not in self.report.skipped
                    and not (
                        self.template is not None
                        and bp.parse_state(self.template.cells.get(r, "minecraft:air"))[
                            0
                        ]
                        == sc.BLOCK
                    )
                ]
                if not rem_roots:
                    continue
                dropped = comp - sc.survivors(comp, self._sturdy_below, rem_roots)
                if not dropped:
                    continue
                cost = self._reclaim_cost(base, rem_roots, len(dropped), open_zones)
                ranked.append((cost, rem_roots[0][::2], len(dropped), dropped))
        solid_items = items - {sc.BLOCK}
        if solid_items:
            columns: dict[tuple[int, int], list[Pos]] = {}
            for c in idle:
                if c[::2] in keep_columns:
                    continue
                if (
                    bp.parse_state(self.body.world(*c) or "minecraft:air")[0]
                    in solid_items
                ):
                    columns.setdefault(c[::2], []).append(c)
            for xz, cells in columns.items():
                cost = self._reclaim_cost(base, cells, len(cells), open_zones)
                ranked.append((cost, xz, len(cells), set(cells)))
        picked: set = set()
        for _, _, got, cells in sorted(ranked, key=lambda r: (r[0], r[1])):
            if n <= 0:
                break
            picked.update(cells)
            n -= got
        return picked

    def _open_zones(self) -> set | None:
        """Work zones with cells still to place, break or use (None: no template)."""
        if self.template is None:
            return None
        d = bp.diff(self.template, self.body.world)
        towers = self.scaffolds or set()  # in the footprint they read as breaks
        return {
            bp.zone_of(self.template, p)
            for p in [
                *(p for p, _ in d.places),
                *(p for p in d.breaks if p not in towers),
                *(u[0] for u in d.uses),
            ]
        }

    def _reclaim_cost(self, base: Pos, cells: list, got: int, open_zones) -> float:
        """Blocks of walking per block got back from one column or structure: the
        walk from the new tower's ``base`` to the roots, their climbs weighted by
        CLIMB_COST, and REBUILD_COST more per block for a root in a zone still being
        built (run 20261119T133618892664Z took towers down mid-wing and put them up
        again)."""
        standing = [c for c in cells if _standing(self.body, c)] or cells
        root = min(standing, key=lambda c: (math.dist(c[::2], base[::2]), c[1]))
        climb = max(0, root[1] - base[1] - REACH_UP)
        walk = math.dist(root[::2], base[::2]) + CLIMB_COST * climb
        # Extra roots of a bridged structure each add a walk/climb between them.
        extra_roots = {c[::2]: c for c in standing if c[::2] != root[::2]}
        prev = root
        for r in sorted(
            extra_roots.values(), key=lambda c: math.dist(c[::2], prev[::2])
        ):
            walk += math.dist(r[::2], prev[::2]) + CLIMB_COST * max(
                0, r[1] - base[1] - REACH_UP
            )
            prev = r
        if open_zones is None or self.template is None:
            return walk / got
        rebuild = any(bp.zone_of(self.template, r) in open_zones for r in standing)
        return walk / got + (REBUILD_COST if rebuild else 0.0)

    def _take_down(self, done: set) -> bool:
        """Break ``done`` (cleanup order), wait for scaffolding collapses and pick
        the drops up without placing new footholds; they leave ``scaffolds`` and
        their failures are forgotten. Returns True if anything was broken."""
        prev_skips = {
            c: self.report.skipped[c] for c in done if c in self.report.skipped
        }
        collect = getattr(self.body, "collect_drops", None)  # optional
        settle = getattr(self.body, "wait_ticks", None)  # optional
        broke_any = False
        for _ in range(CLEANUP_ROUNDS):
            acts = [
                a
                for a in bp.cleanup_actions(self.template, self.body.world, done)
                if a.pos not in self.report.skipped
            ]
            if not acts:
                break
            scaff_roots = {
                a.pos for a in acts if bp._scaffolding(self.body.world(*a.pos))
            }
            has_collapse = bool(scaff_roots) and any(
                c not in scaff_roots and bp._scaffolding(self.body.world(*c))
                for c in done
            )
            broken_before = len(self.report.broken)
            for act in acts:
                self.attempt(act)
            if len(self.report.broken) > broken_before:
                broke_any = True
            if has_collapse and settle:
                settle(COLLAPSE_TICKS)
            if collect:
                with no_placing(self.body):
                    collect(list(done))
        if self.template is not None and self.scaffolds is not None:
            _adopt_footholds(self.body, self.template, self.scaffolds)
        gone = {c for c in done if not _standing(self.body, c)}
        self.scaffolds -= gone
        if isinstance(strays := getattr(self.body, "stray_footholds", None), set):
            strays -= gone
        for c in done:
            if c not in prev_skips:
                self.report.skipped.pop(c, None)
            self.fails.pop(c, None)
            self.avoid.pop(c, None)
            self.towered.discard(c)
        return broke_any

    def _pillar(self, pos: Pos, cells: list, item: str, avoid) -> bool:
        """A column of anything but scaffolding is solid: nothing walks or climbs onto
        its top (run 20261116T131538770036Z: 116 plans, all unreachable). The body
        pillars up it itself, so it ends up standing on the top."""
        pillar = getattr(self.body, "pillar_to", None)  # optional, like walk_to
        if pillar is None:
            return False
        cx, _, cz = cells[0]
        # Walk to the column's foot first: pillar_to's search is only for going up.
        # Run 20261118T023332974582Z asked it from 18 blocks off with 6 blocks to
        # place and no bridging, and 140 searches ran into the node limit.
        walk = getattr(self.body, "walk_to", None)
        here = self.body.position()
        if walk is not None and math.dist((here[0], here[2]), (cx + 0.5, cz + 0.5)) > 1:
            if not walk(cells[0], 0.6):
                return False
        pillar((cx, cells[-1][1] + 1, cz), item, len(cells))
        for cell in cells:  # what stands is cleaned up with the rest
            if _standing(self.body, cell):
                self.scaffolds.add(cell)
                self.report.scaffolded.append(cell)
        if self.body.goto_stance(pos, REACH, avoid):
            return True
        # Not a stance after all: only this column comes down. Cleaning up every
        # tower here took the scaffolding of the rest of the wing with it, and the
        # next tower went up in dirt again (run 20261119T225430670386Z).
        self._take_down({c for c in cells if c in self.scaffolds})
        return False

    def _scaffold_item(self, n: int) -> str | None:
        """First item the planner may scaffold with (scaffolding, then dirt, ...) of
        which the body holds ``n``."""
        held = self.body.inventory()
        need = _still_needed(self.body)  # the house's own blocks are not spare
        return next(
            (i for i in self._temporaries() if held.get(i, 0) - need.get(i, 0) >= n),
            None,
        )

    def _temporaries(self, scaffolding: bool = True) -> list[str]:
        """Blocks to climb on and take back, best first: scaffolding, dirt,
        cobblestone, then spare plain blocks that drop themselves (the body's
        ``temporary_items``; a body without it: the fixed scaffold items)."""
        pick = getattr(self.body, "temporary_items", None)
        if pick is not None and getattr(self.body, "temporary_ok", None) is not None:
            return pick(scaffolding)
        items = list(bp.SCAFFOLD_ITEMS)
        return items if scaffolding else [i for i in items if i != sc.BLOCK]

    def _footholds(self) -> tuple[str, list[str]]:
        """The block to ask for when a climb lacks footholds, and the held items that
        count as footholds. A build that carries scaffolding climbs on nothing else
        (CompanionBody.scaffold_only): asked for dirt, it never counted the dirt as
        held, so each ask grew by the last refill (run 20261122T152815791116Z: 1 to
        346 dirt over all 40 refills, pits dug beside the house)."""
        if getattr(self.body, "scaffold_only", False):
            return sc.BLOCK, [sc.BLOCK]
        return FOOTHOLD_ITEM, self._temporaries(scaffolding=False)

    def _prop_item(self) -> str | None:
        """A held solid block to put beside a cell as its click face and take back
        straight away. Never scaffolding (it does not place over a rail or a slab),
        and allowed while the build otherwise climbs on scaffolding only."""
        pick = getattr(self.body, "temporary_items", None)
        if pick is not None and getattr(self.body, "temporary_ok", None) is not None:
            items = pick(False, prop=True)
        else:
            items = self._temporaries(False)
        held = self.body.inventory()
        return next((i for i in items if held.get(i, 0) > 0), None)

    def _fail(self, pos: Pos, reason: str) -> None:
        self.fails[pos] = self.fails.get(pos, 0) + 1
        if self.fails[pos] >= MAX_TRIES:
            self.report.skipped[pos] = reason


def _still_needed(body) -> dict[str, int]:
    """Blocks the build has yet to place (an optional body method)."""
    if getattr(body, "temporary_ok", None) is None:
        return {}
    still = getattr(body, "still_needed", None)
    return still() if still is not None else {}


HUNGRY = 6  # a food bar this low with nothing to eat sends the body to the chests


def _tend_hunger(body, on_need_materials, hunger: dict) -> None:
    """Eat between actions when hungry or hurt (optional body methods). Starving
    with nothing to eat, go to the chests once for food; the build goes on either
    way, and `hunger["asked"]` keeps it from walking there every action."""
    eat = getattr(body, "eat_when_needed", None)
    if eat is None:
        return
    eat()
    if body.held_food() > 0:
        hunger["asked"] = False
    elif on_need_materials and not hunger.get("asked") and body.food() <= HUNGRY:
        hunger["asked"] = True
        on_need_materials({})  # an empty ask: the refill tops up food only


def _stock_footholds(runner, on_need_materials, log, low=FOOTHOLD_STOCK) -> bool | None:
    """Hold FOOTHOLD_STOCK spare blocks to pillar, bridge and step down on (dirt
    first): at the start, and again whenever fewer than ``low`` are left. Site prep
    spends every dirt it digs on the fill; with none left and the house's own blocks
    not spare, a body on a ledge had nothing to stand on (run
    20261117T144843447702Z). Stocked only at the start, the 32 were spent on pillars
    and the last 300 s of run 20261117T165351012696Z planned every climb with
    nothing to place: 58 searches of 10,000 nodes to goals a few blocks up.
    None when no refill was needed, else whether the one asked for brought some."""
    if on_need_materials is None:
        return None
    held = runner.body.inventory()
    need = _still_needed(runner.body)
    # A build that carries scaffolding climbs on it: that is its stock, and dirt is
    # not fetched for it (the dirt goes back to the chests after site prep).
    scaffolding = held.get(sc.BLOCK, 0) > 0 or getattr(runner, "scaffold_run", False)
    if scaffolding:
        runner.scaffold_run = True
    item = sc.BLOCK if scaffolding else FOOTHOLD_ITEM
    have = sum(
        max(0, held.get(i, 0) - need.get(i, 0))
        for i in runner._temporaries(scaffolding=scaffolding)
    )
    if have >= low:
        return None
    ask = _hold_more(runner.body, item, FOOTHOLD_STOCK - have)
    log(f"stocking footholds: {ask} (holding {have} spare)")
    before = runner.body.inventory().get(item, 0)
    on_need_materials(ask)  # best effort: the build goes on without them
    return runner.body.inventory().get(item, 0) > before


def _hold_more(body, item: str, more: int) -> dict[str, int]:
    """The ask for ``more`` of ``item`` than the body holds. ``on_need_materials``
    asks are totals to hold, like the build's own material asks (companion refill
    tops up to them). Run 20261122T064728624126Z held 1 dirt and asked for the 1
    more a roof stair's pillar needed: the refill saw the 1 held, brought nothing,
    and the stair was skipped no_material."""
    return {item: body.inventory().get(item, 0) + more}


def _keep_footholds(runner, on_need_materials, log, stock: dict) -> None:
    """Mid-build top-up of the foothold stock; one that brought nothing is not asked
    again for FOOTHOLD_RETRY actions (each ask is a walk to the chests)."""
    if stock.get("wait", 0) > 0:
        stock["wait"] -= 1
        return
    got = _stock_footholds(runner, on_need_materials, log, low=FOOTHOLD_LOW)
    stock["wait"] = FOOTHOLD_RETRY if got is False else 0


def _blind(detail: dict) -> bool:
    """A place diagnosis where faces were in reach but none could be seen."""
    sources = detail.get("sources") or ()
    return (
        bool(sources)
        and any(s.get("points_in_reach", 0) > 0 for s in sources)
        and all(s.get("points_visible", 0) == 0 for s in sources)
    )


def _standing(body, cell: Pos) -> bool:
    """A tower cell still holds a block (scaffolding or the dirt of a
    pillared column)."""
    return bp.parse_state(body.world(*cell) or "minecraft:air")[0] not in bp.AIR


def _plan_ahead(runner: _Runner, act, pool: list) -> None:
    """Start planning the walk to the next target before ``act`` is made, so the
    route is ready when the placement ends instead of a stop-and-plan per block.
    Optional body method ``plan_ahead(pos, reach)``; nothing when the next target
    is already within reach of where the body stands (no walk to plan)."""
    plan = getattr(runner.body, "plan_ahead", None)
    if plan is None or not pool:
        return
    here = runner.body.position()
    nxt = min(pool, key=lambda b: (b.pos[1], _dist2(b.pos, act.pos)))
    if not _in_reach(runner, nxt, here):
        plan(nxt.pos, REACH)


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
    # While the body walks (a goto drive), place what comes within reach: a body
    # with an ``on_pass`` slot calls it with its position along the route.
    if hasattr(runner.body, "on_pass"):
        runner.body.on_pass = _passing(runner, pool, corners)
    try:
        _sweep_pool(runner, pool, corners, last, tried)
    finally:
        if hasattr(runner.body, "on_pass"):
            runner.body.on_pass = None


def _ready(a, corners) -> bool:
    return a.pos in corners or not any(
        (a.pos[0] + dx, a.pos[1], a.pos[2] + dz) in corners
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
    )


def _stacked(runner: _Runner) -> bool:
    return runner.order == "storeys"


def _on_top(runner: _Runner, a, pool) -> bool:
    """Storeys: nothing of the batch is still missing under ``a`` in its column, nor
    a cell of it this sweep failed (the wall is not built over a hole)."""
    x, y, z = a.pos
    return not any(
        b.pos[0] == x and b.pos[2] == z and b.pos[1] < y for b in pool
    ) and not any(h[0] == x and h[2] == z and h[1] < y for h in runner.held)


def _wants_more(runner: _Runner, act, world) -> bool:
    """Is a neighbour missing for ``act`` in ``world``: one it rests on, or a face
    whose click makes its state (a log's axis, a stair's half)?"""
    return bp._gate(act.pos, act.state, world, runner.blocks) is not None or bool(
        bp.prop_cells(act.pos, act.state, world, runner.blocks)
    )


def _waiting(runner: _Runner, pool) -> set[Pos]:
    """The cells of ``pool`` to be clicked against cells of it not in yet: each
    cannot be made from the world as it stands and can once the batch cells it needs
    are in, those in turn (a log on a log on a plank). Cells that only need each
    other (two hoppers facing) never come in that way and do not wait: the first
    goes in against a temporary block (run 20261122T013145553434Z: the storey order
    offered logs with the cells their axis needs; taken first, each wanted a dirt
    prop, and 16 cells were skipped)."""
    world = getattr(runner.body, "world", None)
    if world is None:
        return set()
    places = {
        a.pos: a
        for a in pool
        if isinstance(a, bp.Place) and getattr(a, "item", None) != sc.BLOCK
    }
    blocked = {
        p
        for p, a in places.items()
        if any(bp._add(p, v) in places for v in bp._NEIGHBOURS)
        and _wants_more(runner, a, world)
    }
    if not blocked:
        return set()
    coming = {p: a.state for p, a in places.items() if p not in blocked}

    def with_batch(x, y, z):
        got = coming.get((x, y, z))
        return got if got is not None else world(x, y, z)

    while True:
        fresh = {
            p
            for p in blocked - coming.keys()
            if not _wants_more(runner, places[p], with_batch)
        }
        if not fresh:
            return blocked & coming.keys()
        coming.update((p, places[p].state) for p in fresh)


def _in_place(runner: _Runner, act) -> bool:
    """Did ``act`` leave its block in (a Place only; the rest hold up nothing)?"""
    if not isinstance(act, bp.Place):
        return True
    got = runner.body.world(*act.pos)
    return got is not None and bp.parse_state(got)[0] == bp.parse_state(act.state)[0]


BODY_HALF_WIDTH = 0.3  # the body's hitbox is 0.6 wide
BODY_HEIGHT = 1.8
JUMP_ROOM = 3.0  # head room a step up needs above the take-off (RouteGeometry.sweep)


def _top_cell(y: float) -> int:
    """The highest cell a body with its feet at ``y`` reaches into."""
    return math.ceil(y + BODY_HEIGHT - 1e-6) - 1


def _body_cells(position) -> set[Pos]:
    """The cells the body's hitbox overlaps where it stands (world coordinates)."""
    x, y, z = (float(v) for v in position)
    r = BODY_HALF_WIDTH - 1e-6
    return {
        (cx, cy, cz)
        for cx in range(math.floor(x - r), math.floor(x + r) + 1)
        for cz in range(math.floor(z - r), math.floor(z + r) + 1)
        for cy in range(math.floor(y + 1e-6), _top_cell(y) + 1)
    }


def _swept_cells(route) -> set[Pos]:
    """Every cell the body passes through following ``route`` (block points, feet
    height may be fractional on stairs and slabs): feet to head at each point, the
    body's height over the higher end of each edge in both columns (a step down
    crosses the lower column at the upper height), the jump room over the take-off
    of a step up, and both corner columns of a diagonal."""
    pts = [(math.floor(p[0]), float(p[1]), math.floor(p[2])) for p in route]
    cells: set[Pos] = set()
    for x, y, z in pts:
        cells.update((x, h, z) for h in range(math.floor(y), _top_cell(y) + 1))
    for (ax, ay, az), (bx, by, bz) in zip(pts, pts[1:], strict=False):
        top = _top_cell(max(ay, by))
        if by > ay:  # the take-off needs room for the jump
            top = max(top, math.ceil(ay + JUMP_ROOM - 1e-6) - 1)
        columns = [((ax, az), ay), ((bx, bz), by)]
        if ax != bx and az != bz:
            low = min(ay, by)
            columns += [((ax, bz), low), ((bx, az), low)]
        for (cx, cz), low in columns:
            cells.update((cx, h, cz) for h in range(math.floor(low), top + 1))
    return cells


def _passing(runner: _Runner, pool: list, corners: set):
    """The ``on_pass`` hook: place one pending block the body can reach right now.

    Margin of one block inside REACH (the body is moving). A failure is not
    recorded and the block is not tried again while passing: the sweep's own
    attempt keeps the retry and failure bookkeeping.

    ``route``: the points the motor has still to drive. A block is never placed
    into them, nor into the body itself: the motor stops on a target or swept
    cell that changed (run 20261119T133618892664Z: all 52
    route_target_unknown_or_changed and 13 route_swept_geometry stops came within
    40 ticks of a block placed while passing)."""
    refused: set = set()

    def on_pass(position, route=None) -> None:
        body = runner.body
        if runner._towering or runner.short or runner.report.steps >= runner.max_steps:
            return
        near = [
            a
            for a in pool
            if isinstance(a, bp.Place)
            and a.pos not in refused
            and _dist2(a.pos, position) <= (REACH - 1.0) ** 2
            and _ready(a, corners)
            and (not _stacked(runner) or _on_top(runner, a, pool))
        ]
        if not near:
            return
        path = _body_cells(position) | _swept_cells(route or ())
        near = [a for a in near if a.pos not in path]
        if not near:
            return
        if observe := getattr(body, "observe", None):
            observe()  # the position the checks below read is the body's own
        # Called from inside a walk: the stance that walk is for keeps its aim (the
        # arrival check reads it).
        with _aim_kept(body):
            _pass_one(runner, pool, corners, near, position, refused)

    return on_pass


def _pass_one(runner: _Runner, pool, corners, near, position, refused) -> None:
    body = runner.body
    can_place = getattr(body, "can_place_here", None)
    world = getattr(body, "world", None)
    for act in sorted(near, key=lambda a: (a.pos[1], _dist2(a.pos, position))):
        support = _support_for(act, world, runner.blocks)
        _aim(body, act, support)
        if can_place is not None and not can_place(act.pos, REACH, support, act.state):
            continue
        refused.add(act.pos)
        if _do(body, act).ok:
            pool.remove(act)
            corners.discard(act.pos)
            runner.report.steps += 1
            _take_back(runner, act)
            _record(runner.report, act)
        return


def _in_reach(runner: _Runner, act, here: Pos) -> bool:
    """Can ``act`` be made from where the body stands? A Place on a body with
    ``can_place_here`` is the server's own test (real eye, support faces); the rest
    keep the cell-distance guess with half a block of margin."""
    can_place = getattr(runner.body, "can_place_here", None)
    d2 = _dist2(act.pos, here)
    if (
        can_place is None
        or not isinstance(act, bp.Place)
        or getattr(act, "item", None) == sc.BLOCK
    ):
        return d2 <= (REACH - 0.5) ** 2
    if d2 > (REACH + 2) ** 2:
        return False  # beyond any eye's reach: not worth the sight test
    support = _support_for(act, getattr(runner.body, "world", None), runner.blocks)
    with _aim_kept(runner.body):
        _aim(runner.body, act, support)
        return can_place(act.pos, REACH, support, act.state)


def _cover(runner: _Runner, act, ready: list) -> dict:
    """The sweep's next cells after ``act`` (same layer, nearest to it first; any
    layer of the batch in the storey order, which puts the column up from there)
    with their required support and state: the stance for ``act`` should place them
    too (CompanionBody.stance_cover)."""
    scaffold = getattr(act, "item", None) == sc.BLOCK
    world = getattr(runner.body, "world", None)
    stacked = _stacked(runner)
    near = sorted(
        (
            b
            for b in ready
            if b is not act
            and isinstance(b, bp.Place)
            and (getattr(b, "item", None) == sc.BLOCK) == scaffold
            and (stacked or b.pos[1] == act.pos[1])
            and _dist2(b.pos, act.pos) <= COVER_SPAN**2
        ),
        key=lambda b: (_dist2(b.pos, act.pos), b.pos),
    )[:COVER_CELLS]
    return {b.pos: (_support_for(b, world, runner.blocks), b.state) for b in near}


def _sweep_pool(runner: _Runner, pool: list, corners: set, last, tried) -> None:
    prev = None  # the cell worked last: in reach, the next one goes on beside it
    covers = hasattr(runner.body, "stance_cover")
    walk_dist = getattr(runner.body, "walk_distance", None)
    # Storeys: no layer first. Every cell in reach whose column is in under it goes
    # on, so one stance puts the wall up as high as the batch carries it.
    stacked = _stacked(runner)
    runner.held = set()
    while pool and runner.report.steps < runner.max_steps and not runner.short:
        here = runner.body.position()
        standing = [
            a
            for a in pool
            if _ready(a, corners) and (not stacked or _on_top(runner, a, pool))
        ]
        if not standing:
            return  # stacked: the rest stands over cells this sweep failed
        # A cell clicked against one of the batch waits for it (_waiting); with
        # nothing else left, it goes on against a prop.
        waiting = _waiting(runner, pool)
        ready = [a for a in standing if a.pos not in waiting] or standing
        layer = min(a.pos[1] for a in ready)
        # Reach only matters in the lowest layer: the key orders by layer first.
        near = {
            id(a)
            for a in ready
            if (stacked or a.pos[1] == layer) and _in_reach(runner, a, here)
        }
        walk_cache: dict[Pos, int | None] = {}

        def key(
            a,
            here=here,
            last=last,
            prev=prev,
            near=near,
            layer=layer,
            walk_cache=walk_cache,
        ):
            d2 = _dist2(a.pos, here)
            reached = id(a) in near
            same = getattr(a, "item", None) != last
            # In reach, go on along the wall from the last cell rather than by
            # distance to the body: from a corner both arms are as near, and the
            # nearest-first order swung between them, leaving the far ends of both
            # for a walk each. Out of reach, rank by real walking distance when the
            # body provides it so the bot finishes one side of a wall / floor first.
            along = _dist2(a.pos, prev) if reached and prev is not None else d2
            if reached:
                travel = 0
            elif walk_dist is not None and (stacked or a.pos[1] == layer):
                if a.pos not in walk_cache:
                    walk_cache[a.pos] = walk_dist(a.pos)
                wd = walk_cache[a.pos]
                travel = wd if wd is not None else 1000 + d2
            else:
                travel = d2
            return (0 if stacked else a.pos[1], not reached, travel, same, along, d2)

        act = min(ready, key=key)
        if tried and _dist2(act.pos, here) > (REACH + STALE_MARGIN) ** 2:
            return  # stale: only far leftovers (the batch was cut round the old spot)
        if act not in pool:
            continue  # placed on the way (on_pass)
        pool.remove(act)
        corners.discard(act.pos)
        _plan_ahead(runner, act, pool)
        # Out of reach, the walk for ``act`` should end where the next cells are in
        # reach too: nearest-first stood at the edge of reach, one walk per block
        # (run 20261119T225430670386Z: 119 walks, 195 s).
        if covers and id(act) not in near:
            upcoming = [a for a in pool if _ready(a, corners)] if stacked else ready
            runner.body.stance_cover = _cover(runner, act, upcoming)
        try:
            runner.attempt(act)
        finally:
            if covers:
                runner.body.stance_cover = {}
        if stacked and not _in_place(runner, act):
            runner.held.add(act.pos)
        last, tried, prev = getattr(act, "item", None), True, act.pos


@functools.cache
def _drop_items(block: str) -> tuple[str, ...]:
    """Items breaking ``block`` can drop without silk touch, from the knowledge
    pack (stone: cobblestone; grass_block: dirt). Empty when the pack has none."""
    from mclab.companion_jobs import load_vanilla_pack

    table = load_vanilla_pack().drops.get(block) or {}
    return tuple(
        dict.fromkeys(
            d.item for t, ds in table.items() if t != "silk_touch" for d in ds
        )
    )


def _restore(runner: _Runner, act: bp.Place) -> None:
    report = runner.report
    runner.attempt(act)
    if act.pos in report.placed:
        report.restored.append(act.pos)
    else:
        report.unrestored[act.pos] = "place failed"


def _restores(runner: _Runner, template: bp.Template) -> list[bp.Place]:
    """The places that put back the non-template blocks the planner broke to walk,
    with the drop the body picked up; a cell whose item is not held is reported in
    ``unrestored``. Takes the breaks (``stray_breaks``) so none is restored twice."""
    body, report = runner.body, runner.report
    lost = getattr(body, "stray_breaks", None)
    if not lost:
        return []
    pending = dict(lost)
    lost.clear()
    held = dict(body.inventory())
    out = []
    for pos, state in sorted(pending.items(), key=lambda kv: (kv[0][1], kv[0])):
        if pos in template.cells:
            continue  # the build owns it
        current = body.world(*pos)
        if current is not None and bp.parse_state(current)[0] not in bp.AIR:
            continue  # something stands there again (a foothold, the build)
        ident = bp.parse_state(state)[0]
        # The block's own item, else what breaking it drops (stone gives cobblestone).
        options = (ident, *_drop_items(ident))
        item = next((i for i in options if held.get(i, 0) > 0), None)
        if item is None:
            report.unrestored[pos] = f"not held: {ident}"
            continue
        held[item] -= 1
        if item != ident:
            state = item  # the stand-in is placed as itself
        out.append(bp.Place(pos, state, item))
    return out


def _adopt_footholds(body, template: bp.Template, scaffolds: set) -> None:
    """Blocks the route planner placed to walk become scaffolds: never broken by the
    build before cleanup (a foothold in one of the house's air cells reads as a block to
    break), and taken back by it. One in a cell the house fills is the house's."""
    strays = getattr(body, "stray_footholds", None) or ()
    scaffolds |= {
        p
        for p in strays
        if p not in template.cells or bp.parse_state(template.cells[p])[0] in bp.AIR
    }


def _sweep_cleanup(
    runner: _Runner,
    acts: list,
    steps: Collection[Pos] = frozenset(),
    restores: Collection[bp.Place] = (),
) -> None:
    """Break cleanup targets nearest-first (walking distance when available),
    always taking the highest remaining block of any solid column first so the
    body clears one corner/room at a time instead of zig-zagging across the house
    (run 20261120T175636584118Z spent 363 s in global top-down cleanup).

    ``steps`` (blocks the planner placed to walk) come down only once nothing above
    them is left, the way a person takes the step away on the way down: run
    20261121T154048852669Z broke a stairwell step with footholds upstairs still to
    clear, walked up through the gap placing it again, twice, and left the last.

    ``restores`` (ground the planner dug out, ``_restores``) are put back in their
    nearest-first turn too: run 20261122T054201856122Z ended the structure on the
    roof ridge, restored a far ground cell first and, nearest first from there, left
    the ridge for last (three climbs back, cleanup 5:19 against 2:27 for zones)."""
    pool = list(acts)
    fills = list(restores)
    walk_dist = getattr(runner.body, "walk_distance", None)
    while (pool or fills) and runner.report.steps < runner.max_steps:
        here = runner.body.position()
        by_col: dict[tuple[int, int], int] = {}
        for a in pool:
            col = a.pos[::2]
            by_col[col] = max(by_col.get(col, a.pos[1]), a.pos[1])
        ready = [a for a in pool if a.pos[1] == by_col[a.pos[::2]]]
        if pool:
            top = max(a.pos[1] for a in pool)
            ready = [a for a in ready if a.pos not in steps or a.pos[1] == top] or ready

        def key(a, here=here):
            d2 = _dist2(a.pos, here)
            wd = walk_dist(a.pos) if walk_dist is not None else None
            dist = wd if wd is not None else (1000 + d2 if walk_dist else d2)
            return (dist, d2, -a.pos[1], a.pos)

        act = min(ready + fills, key=key)
        if isinstance(act, bp.Place):
            fills.remove(act)
            current = runner.body.world(*act.pos)
            if current is None or bp.parse_state(current)[0] in bp.AIR:
                _restore(runner, act)
            continue
        pool.remove(act)
        if not _standing(runner.body, act.pos):
            continue
        runner.attempt(act)


def _clear_indoor_strays(
    runner: _Runner, template: bp.Template, scaffolds: set
) -> None:
    """When structure finishes and details begin, clear temporary dirt/scaffolding
    rooted inside template air cells (rooms/balconies) so indoor stances are not
    crowded before glazing and furniture go in. Exterior towers rooted outside the
    template stay standing for gable/roof details."""
    _adopt_footholds(runner.body, template, scaffolds)
    indoor = {
        c
        for c in scaffolds
        if c in template.cells
        and bp.parse_state(template.cells[c])[0] in bp.AIR
        and _standing(runner.body, c)
    }
    if not indoor:
        return
    scaff_indoor = {c for c in indoor if bp._scaffolding(runner.body.world(*c))}
    drop_set = set(indoor - scaff_indoor)
    for comp in runner._scaffold_components(scaff_indoor):
        roots = sc.roots(comp, runner._sturdy_below)
        if roots and all(
            r in template.cells and bp.parse_state(template.cells[r])[0] in bp.AIR
            for r in roots
        ):
            drop_set |= comp
    if drop_set:
        runner._take_down(drop_set)


def _clean_up(runner: _Runner, template: bp.Template, scaffolds: set) -> bool:
    """Take the scaffolds back: break them (scaffolding: only the blocks on the ground,
    the rest drops with them), wait for scaffolding collapses, then pick up what fell
    without placing new footholds. Re-adopts any footholds placed by cleanup walks
    every round so none survive into the final diff (run 20261120T175636584118Z left
    three cleanup-placed footholds inside the house). True if anything was broken."""
    body, broken = runner.body, False
    collect = getattr(body, "collect_drops", None)  # optional, like walk_to
    settle = getattr(body, "wait_ticks", None)  # optional
    steps: set[Pos] = set()  # placed by the planner to walk: taken back last
    for round_idx in range(CLEANUP_ROUNDS * 2):
        # dirt/scaffolding the planner placed to walk (walking_with_edits) comes down
        # with the rest — including footholds placed during earlier cleanup rounds
        _adopt_footholds(body, template, scaffolds)
        strays = getattr(body, "stray_footholds", None)
        if strays:
            steps |= strays & scaffolds
            strays.clear()
        acts = [
            a
            for a in bp.cleanup_actions(template, body.world, scaffolds)
            if a.pos not in runner.report.skipped
        ]
        # ground dug out by the build's walks, and by this cleanup's own
        restores = _restores(runner, template)
        if not acts and not restores:
            break
        standing_before = [c for c in scaffolds if _standing(body, c)]
        scaff_roots = {a.pos for a in acts if bp._scaffolding(body.world(*a.pos))}
        has_collapse = bool(scaff_roots) and any(
            c not in scaff_roots and bp._scaffolding(body.world(*c))
            for c in standing_before
        )
        # On later convergence rounds, avoid placing new footholds or raising new
        # towers while sweeping up strays created by earlier cleanup walks.
        with ExitStack() as stack:
            if round_idx >= CLEANUP_ROUNDS:
                stack.enter_context(no_placing(body))
                prev_towering = runner._towering
                runner._towering = True
                stack.callback(setattr, runner, "_towering", prev_towering)
            _sweep_cleanup(runner, acts, steps, restores)
        broken = broken or bool(acts)
        if has_collapse and settle:
            settle(COLLAPSE_TICKS)
        if collect:
            fell = [c for c in standing_before if not _standing(body, c)]
            with no_placing(body):
                collect(fell or [a.pos for a in acts])
    return broken


_FACING = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}


def _opens_by_hand(ident: str, props: dict) -> bool:
    """Wooden doors and fence gates (BuildingMoves.opensByHand); not iron doors."""
    if ident.endswith("_door"):
        return not ident.endswith("iron_door") and props.get("half") != "upper"
    return ident.endswith("_fence_gate")


def _within(box, pos) -> bool:
    return all(lo <= v <= hi for lo, v, hi in zip(box[0], pos, box[1], strict=True))


def record_build(knowledge, template: bp.Template, phase: str) -> None:
    """Put the build's `structure` fact and one `entrance` per hand-opened door.

    Inside is the door's neighbour (along its `facing` axis, at the door's own
    floor level) that lies within the structure's box; `facing` only breaks a tie.
    """
    cells = [p for p, s in template.cells.items() if bp.parse_state(s)[0] not in bp.AIR]
    if not cells:
        return
    lo, hi = (tuple(f(c) for c in zip(*cells, strict=True)) for f in (min, max))
    box = [list(lo), list(hi)]
    ox, oy, oz = template.origin
    subject = f"{template.name}@{ox},{oy},{oz}"
    source = f"build:{template.name}"
    knowledge.put(
        "structure",
        subject,
        {"template": template.name, "phase": phase},
        region=box,
        source=source,
    )
    for pos, state in template.cells.items():
        ident, props = bp.parse_state(state)
        if not _opens_by_hand(ident, props) or props.get("facing") not in _FACING:
            continue
        dx, dz = _FACING[props["facing"]]
        x, y, z = pos
        if _within(box, (x - dx, y, z - dz)) and not _within(box, (x + dx, y, z + dz)):
            dx, dz = -dx, -dz  # the footprint, not `facing`, says which side is in
        knowledge.put(
            "entrance",
            f"{x},{y},{z}",
            {
                "door": [x, y, z],
                "structure": subject,
                "outside": [x - dx, y, z - dz],
                "inside": [x + dx, y, z + dz],
            },
            region=[[x, y, z], [x, y, z]],
            source=source,
            check="door",
        )


def recheck(knowledge, cells: dict) -> None:
    """Drop an `entrance` whose door cell was sensed and is no longer a door/gate."""
    for fact in knowledge.all("entrance"):
        cell = cells.get(tuple(fact["value"]["door"]))
        if cell is not None and not _opens_by_hand(
            *bp.parse_state(str(cell["block"]).removeprefix("minecraft:"))
        ):
            knowledge.drop("entrance", fact["subject"])


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
    on_stage: Callable[..., None] | None = None,
    stall_seconds: float | None = STALL_SECONDS,
    clock: Callable[[], float] | None = None,
    log: Callable[[str], None] | None = None,
    on_site_ready: Callable[[], None] | None = None,
    on_milestone: Callable[[str], None] | None = None,
    until: str | None = None,
    order: str = bp.ORDERS[0],
) -> BuildReport:
    """Build ``template`` through ``body``; see the module docstring.

    ``order``: the build order (``build_plan.ORDERS``) every batch is planned in.

    ``on_milestone(name)``: ``site_ready`` once the site is ready (after
    ``on_site_ready``), in the storeys order ``band_N`` once the structure of bands
    0..N is in (``_BandStages``), ``structure_done`` the first time only details are
    left (a stage snapshot is taken at each). ``until``: ``site``, ``band_N`` (storeys
    only) or ``structure`` ends the build at that milestone (stop ``until_site``,
    ``until_band_N``, ``until_structure``); the rest is then left for a run that
    starts from the snapshot.

    ``site_margin``: prepare the footprint plus this many cells on every side first
    (site_prep; ``None`` skips it, negative raises ValueError). ``stall_seconds``: a
    stationary retry loop longer than this is recovered, then stopped as ``stalled``
    (progress_watch; ``None`` disables). Stops: ``site_not_ready``, ``stalled``.
    ``on_site_ready()``: called once the site is clear and level, before the first
    block is placed (the job puts the levelling's dirt away there).
    """
    if until is not None and not ck.is_until(until):
        raise ValueError(
            f"until must be one of {sorted(u for u in UNTIL if u)} or band_N"
        )
    if order not in bp.ORDERS:
        raise ValueError(f"order must be one of {bp.ORDERS}, not {order!r}")
    if until is not None and ck.BAND.fullmatch(until) and order != "storeys":
        raise ValueError("a band milestone needs the storeys order")
    report = BuildReport()
    places = getattr(body, "places", None)  # None for fakes and tests
    knowledge = Knowledge(places.archive) if places is not None else None
    if knowledge:
        record_build(knowledge, template, "build")
    watch = None
    if stall_seconds is not None:
        watch = ProgressWatch(
            limit=stall_seconds, events=report.stalls, long_stops=report.long_stops
        )
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

        if on_site_ready:
            ready = on_site_ready

            def on_site_ready(_ready=ready):
                _ready()  # a walk to the chests and back
                watch.progress(body.position())

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
            on_stage or (lambda *a, **k: None),
            on_site_ready or (lambda: None),
            on_milestone or (lambda name: None),
            until,
            order=order,
        )
    except StallError as exc:
        report.stop, report.stall_reason = "stalled", str(exc)
        if log:
            log(f"STOP stalled: {exc}")
    except BodyDied:
        report.stop = "died"
        if log:
            log("STOP died")
    if not report.counts:  # stopped early (stalled, site not ready)
        report.counts = bp.progress(template, body.world)
        report.unknown = bp.diff(template, body.world).unknown
    if knowledge and report.stop == "done":
        record_build(knowledge, template, "done")
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
    on_stage=lambda *a, **k: None,
    on_site_ready=lambda: None,
    on_milestone=lambda name: None,
    until=None,
    order=bp.ORDERS[0],
) -> None:
    on_stage("clearing trees")
    if survey:
        report.surveyed = survey_footprint(template, body)
    from mclab.tree_removal import clear_trees_in_footprint

    scaffolds = set() if scaffolds is None else scaffolds
    runner = _Runner(body, report, max_steps, blocks)
    runner.order = order
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
            on_stage("site prep", detail=message)
            if on_progress:  # each round refreshes the run's progress/status
                on_progress(report, False)

        on_stage("site prep", detail="")
        sr = site_prep.prepare_site(
            template,
            body,
            runner,
            site,
            protected=protected,
            keep=set(scaffolds),  # the run's own scaffolding (a resumed stage)
            on_need_materials=on_need_materials,
            log=site_log,
        )
        sr.trees = report.trees_cleared
        report.site = sr.summary()
        log(f"site prepared: {report.site}")
        on_stage("placing")
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
        on_stage("placing")
    on_site_ready()
    on_milestone("site_ready")
    if until == "site":
        report.stop = "until_site"
        return
    runner.scaffolds, runner.template = scaffolds, template
    runner.reserved = frozenset(
        p for p, s in template.cells.items() if bp.parse_state(s)[0] not in bp.AIR
    )
    _stock_footholds(runner, on_need_materials, log)

    def calm():
        # Scaffolding is strongly preferred over solid temporaries so towers and
        # bridges use climbable columns; solid blocks stay as a costly fallback.
        return walking_with_edits(body, wall=BUILD_WALL)

    recoveries = retries = 0
    fetched: set[frozenset[str]] = set()
    refused: set[frozenset] = set()
    settled = False
    hunger: dict = {}
    stock: dict = {}
    indoor_cleared = False
    structure_done = False
    build_stage = "placing"
    on_stage(build_stage)
    band_stages = _BandStages(template) if order == "storeys" else None
    while True:
        if report.steps >= max_steps:
            report.stop = "max_steps"
            break
        skipped = report.skipped
        # adopted first: a stage lists the footholds placed finishing its band
        _adopt_footholds(body, template, scaffolds)
        if band_stages is not None:
            stage_at = None
            for band in band_stages.finished(body.world):
                on_milestone(ck.band_stage(band))
                if until == ck.band_stage(band):
                    stage_at = until
                    break
            if stage_at:
                report.stop = f"until_{stage_at}"
                break
        _tend_hunger(body, on_need_materials, hunger)
        _keep_footholds(runner, on_need_materials, log, stock)
        # the zone of the last block placed is finished before the next one is begun
        focus = report.placed[-1] if report.placed else None
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
            focus=focus,
            order=order,
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
                    focus=focus,
                    order=order,
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
        # The build around a skipped cell has changed since: try it again with fresh
        # stances rather than stopping (a cell is fixed, not skipped). So too before
        # the structure is declared done with a cell of it skipped: run
        # 20261122T074325272877Z saved structure_done with a ridge slab missing.
        structure_skipped = any(
            bp.is_structure(template.cells.get(p, "minecraft:air")) for p in skipped
        )
        if (
            skipped
            and retries < RETRY_ROUNDS
            and (
                not acts
                or (
                    not structure_done
                    and structure_skipped
                    and not any(
                        isinstance(a, bp.Place) and bp.is_structure(a.state)
                        for a in acts
                    )
                )
            )
        ):
            retries += 1
            skipped.clear()
            runner.fails.clear()
            runner.avoid.clear()
            runner.towered.clear()
            runner.propped.clear()
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
            if not clean:
                report.diff = {
                    "breaks": list(d.breaks),
                    "places": list(d.places),
                    "unknown": list(d.unknown),
                    "uses": list(d.uses),
                }
            break
        has_structure = any(
            isinstance(a, bp.Place)
            and not bp.is_detail(a.state)
            and not bp._is_door(a.state)
            for a in acts
        )
        has_details = any(
            (
                isinstance(a, bp.Place)
                and (bp.is_detail(a.state) or bp._is_door(a.state))
            )
            or isinstance(a, bp.Use)
            for a in acts
        )
        has_non_door_details = any(
            isinstance(a, bp.Place) and bp.is_detail(a.state) for a in acts
        )
        if has_structure:
            build_stage = "placing"
        elif has_details:
            if not structure_done:
                structure_done = True
                on_milestone("structure_done")
                if until == "structure":
                    report.stop = "until_structure"
                    break
            if build_stage == "placing" and has_non_door_details and not indoor_cleared:
                indoor_cleared = True
                with calm():
                    _clear_indoor_strays(runner, template, scaffolds)
            build_stage = "placing details"
        on_stage(build_stage)
        with calm():
            _sweep(runner, template, acts)
        if runner.short:
            need, runner.short = runner.short, {}
            short_for, runner.short_for = runner.short_for, None
            ask = frozenset(need.items())
            if (
                on_need_materials is None
                or ask in refused  # asked before and the tower is still short
                or not on_need_materials(need)
            ):
                if on_need_materials is not None and short_for is not None:
                    # One cell out of reach is not the house: run
                    # 20261117T140114411942Z stopped at 416 of 1227 placed because a
                    # pillar to one upper-floor cell wanted 2 dirt nobody had.
                    refused.add(ask)  # the next cell short the same is not asked again
                    report.skipped[short_for] = "no_material"
                    continue
                report.needed, report.stop = need, "need_materials"
                break
            refused.add(ask)
            skipped.clear()  # the cells that waited for the tower are tried again
            runner.fails.clear()
            runner.avoid.clear()
            runner.towered.clear()
            runner.propped.clear()
        if on_progress:
            on_progress(report, False)
    # A build stopped short of materials cleans up too: nothing more can be placed
    # until someone brings them, and a later resume builds its own towers again
    # (run 20261121T171016886377Z left every scaffolding tower standing).
    has_cleanup = bool(
        scaffolds
        or getattr(body, "stray_breaks", None)
        or getattr(body, "stray_footholds", None)
        or (
            report.stop in ("done", "blocked")
            and (
                getattr(body, "wait_ticks", None) is not None
                or bp.diff(template, body.world).uses
            )
        )
    )
    if has_cleanup:
        on_stage("cleanup")
    no_doors = getattr(body, "no_opening_doors", nullcontext)
    with calm():
        _clean_up(runner, template, scaffolds)
    if report.stop in ("done", "blocked"):
        # the final audit below decides: a clean diff is done, whatever the loop said
        # Cleanup walks can reopen doors after the main loop's clean audit.
        # Repair only state toggles, with automatic door opening disabled;
        # audit again after all movement, rather than reporting stale success.
        with calm(), no_doors():
            # The cleanup's last steps may have opened a door the sensor has not
            # delivered yet: a stale audit skipped the repair and ended blocked
            # with one open door (run 20261116T230737426066Z).
            if settle := getattr(body, "wait_ticks", None):
                settle(SETTLE_TICKS)
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
        if (final.breaks or final.places or final.unknown or final.uses) and (
            settle := getattr(body, "wait_ticks", None)
        ):
            # the door use may not be delivered yet (run 20261116T232024268879Z:
            # "blocked" with all 84 correct and an empty diff moments later)
            settle(SETTLE_TICKS)
            final = bp.diff(template, body.world)
        if final.breaks or final.places or final.unknown or final.uses:
            report.stop = "blocked"
        else:
            report.stop = "done"
    if report.stop == "done" and not structure_done:
        on_milestone("structure_done")  # a house with no details: its end
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
