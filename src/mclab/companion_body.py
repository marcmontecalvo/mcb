# ruff: noqa: E501
"""Real body adapter for the companion executors (gather and build), over ``Bridge``.

``CompanionBody`` implements ``build_runner.BuildBody`` and ``gather_runner.GatherBody``.
Ops: docs/BODY_ACTION_OPS.md. Navigation is reused, not rewritten.

Navigation reuse
- ``goto_stance(target, reach, avoid)``: a ``SegmentedController`` with
  ``interact_reach=reach`` is planned by the Java body and driven by
  ``control_loop.drive(..., profile=NATIVE)`` and its ``follow_route`` follower.
- ``walk_to(pos)``: the same body planner with a stand-on goal (radius).
- ``native_driver.drive_native`` got one hook: a controller's ``stop_reason(observation)``
  may end the drive with ``{"type": "navigation_stopped"}``. Without it the drive only
  ends with the episode, and its own failure path (``_fail``) ends the episode.

Assumptions (the coordinator verifies them live)
1. A companion session is a ``natural_start`` episode (``start_session``): ``/policy``
   state is ``running``, a far goal and a huge tick limit keep it open. Body ops
   (``place_block`` ...) are accepted while it runs, and the lab ``kit`` op works in it.
2. ``world()`` reads only ``SpatialMemory.cells`` (delivered cells; None = unknown). Every
   ``observe()`` feeds the policy observation it fetched into the memory, so no
   ``/policy`` read is discarded. After an op succeeds the new state is also kept in a
   short-lived overlay (``OVERLAY_TICKS``) until the sensor has re-delivered the cell.
3. ``avoid``: when ``avoid`` is non-empty, the adapter picks the nearest known
   standable cell outside ``avoid`` from which
   ``interacts`` holds and walks onto it (``walk_to``); when the planned stance falls in
   ``avoid`` it does the same. Stances are compared as integer feet cells.
4. A goto gives up after ``goto_wall_s`` wall seconds, or when the planner says
   ``unreachable`` for ``UNREACHABLE_TICKS`` ticks, and returns False. The episode stays
   open. Default: shared movement with doors and climbing, no terrain edits;
   ``start_session(dig=True)`` enables digging and support placement (DIG_TERRAIN).
5. Op ``_failed`` events map to ``Result(False, reason, data)``; a ``rejected`` event maps
   to ``Result(False, "rejected:<reason>")``; a wall timeout to ``Result(False, "timeout")``.
   ``bridge.wait`` heartbeats while waiting.
6. Entities come from ``visible_entities`` (``uuid``, ``type``, ``position``, items with
   ``item``/``count``), food from ``body.food``, inventory from ``body.inventory``.
"""

from __future__ import annotations

import functools
import heapq
import logging
import math
import time
from collections import ChainMap
from collections.abc import Callable, Collection
from contextlib import contextmanager
from dataclasses import asdict, replace

from mclab import place_sight
from mclab.awareness import Awareness
from mclab.body_search import BOXED_IN_NODES
from mclab.bridge import REJECTIONS
from mclab.build_runner import Result
from mclab.collision_geometry import standable
from mclab.control_loop import NATIVE, drive
from mclab.execution import RecordedBridge, policy_state
from mclab.gather_tasks import item_stack
from mclab.interaction_goal import _segment_hits_box, interacts
from mclab.markers import Markers
from mclab.memory_policy import MemoryPolicy
from mclab.movement_policy import HURT_DROP_MAX, MovementPolicy
from mclab.native_driver import EdgeFailures
from mclab.navigation_runner import _new_identity
from mclab.navigation_segmented import SegmentedController
from mclab.paths import ROOT, RUNTIME, SERVER, read_json
from mclab.place_graph import Connection, PlaceGraph
from mclab.progress_watch import BodyDied
from mclab.schematic import format_state, parse_state
from mclab.spatial_memory import (
    MapArchive,
    MapIdentity,
    SpatialMemory,
    world_archive,
)
from mclab.temporary_blocks import SCAFFOLDING
from mclab.temporary_blocks import order as temporary_order
from mclab.terrain_steps import TerrainPolicy

Pos = tuple[int, int, int]
AIR_BLOCKS = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}

OVERLAY_TICKS = 200  # until the sensor has re-delivered the cell; 40 was too short (run 20261102T222239739886Z: "occupied")
UNREACHABLE_TICKS = 60
GOTO_WALL_S = 90.0
MAIN_SLOTS = 36  # inventory indices 0..35: hotbar and main
EAT_BELOW = 14  # the food bar a body eats at (gather_runner.EAT_BELOW)
REGEN_FOOD = 18  # health regenerates only at or above this food level
# HP a hurt drop must leave (of 20): three hearts; the user would
# rather take a few hearts than crawl, pillar or stand exposed (docs/RISK_AND_DAMAGE.md)
KEEP_HEALTH = 6
GET_DOWN_TRIES = 4  # landing spots tried when boxed in on a ledge

# Per-world knowledge that outlives sessions (git-ignored runtime/).
WORLD_MEMORY = RUNTIME / "world-memory"
_restored_world = False  # set inside companion_world(seed): a reset snapshot copy
# Placed blocks worth a waypoint: the crafting table marks the gathering site.
RECORDED_BLOCKS = ("crafting_table", "furnace", "chest", "barrel")
# walk_far: legs well inside the 64-block sensed radius, so the leg's end is
# usually observed terrain; a trip ends after MAX_STALLS legs that gained less
# than MIN_PROGRESS blocks toward the goal.
LEG, LEG_RADIUS, MIN_PROGRESS, MAX_STALLS = 48.0, 4.0, 4.0, 2
START_NEAR = 16.0  # go_to starts from a place only when standing this close
OP_TIMEOUT_S = 20.0
OP_TIMEOUTS = {
    "break_block": 45.0,
    "attack_entity": 30.0,
    "eat": 20.0,
    "drink": 20.0,  # 1.6 s of drinking, as long as an eat may take
    "fight": 2400 / 20 + 10.0,  # the longest fight the body accepts, and a margin
    "escape": 1200 / 20 + 10.0,  # the longest escape the body accepts, and a margin
}
# Ops after which the build's remaining needs may have changed: the plug's keep is
# brought up to date before each (a step edit's plug uses the last one sent).
PLUG_KEEP_OPS = frozenset({"break_block", "place_block", "use_block"})
STAND_SEARCH = 6  # walk_to: vertical search for a standable cell in the column
WALK_CELLS = 2500  # bound on the stance-ranking flood fill
MAX_CANDIDATES = (
    6  # 3 nearest were often all unreachable (on a wall top, behind a wall)
)
WALK_ONLY_CANDIDATES = 2  # building: 2 probes is 5 s, the longest pause allowed
FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
SENSED_RADIUS = 40.0  # awareness delivers 64; beyond this a stance may be unsensed
# A search that yields no move for this long is abandoned. The planner's capped
# 10000-node search takes 58-66 ticks, so 60 cancelled it a tick before its verdict:
# six stance probes in a row burned 21 s without a plan (run 20261116T163035614307Z,
# plan-405..409, adopted only when plan-410 finished 2 ticks inside the limit).
SEARCH_STALL_TICKS = 150
# The lab episode a companion job runs in: the mod's companion cap (NaturalRun,
# ~16.7 h). 100 000 ticks (83 min) ended run 20261117T200337044791Z mid-roof, and
# every plan after it was cancelled.
COMPANION_TICK_LIMIT = 1_200_000
SETTLE_TICKS = 40  # settle(): longest wait for the map to show broken blocks
SETTLE_STEP = 5
STUCK_TICKS = 200  # a goto or walk whose position does not change for this long fails
STUCK_MOVE = 0.5  # blocks that count as progress
IDLE_REPORT_S = 5.0  # a still body this long prints a STALL line (report only)
EDIT_MODES = {"terrain_editing", "terrain_step_motion", "terrain_planned_walk"}
PLANNING_MODES = {"planning", "searching", "terrain_planning", "awaiting_map"}
PROBE_WALL_S = (
    2.5  # plan-only stance probe: budget for the body planner's first verdict
)
DROP_RADIUS = 8.0  # collect_drops: items this close to the broken cells are picked up
DROP_WAIT_TICKS = 40  # a tower of scaffolding drops a layer per few ticks
DROP_POLL_TICKS = 5  # collect_drops: look again this often while items are falling
MAX_PICKUPS = 16
# goto_stance: a candidate stance that gets the body no nearer its goal for this long is
# dropped for the next one (a long walk that keeps closing in is never cut by it).
CANDIDATE_PROGRESS_TICKS = 180  # 9 s
STANCE_RETRY_S = 30.0  # a stance that failed is not walked to again for this long
DROP_RISE = 4  # collect_drops: items resting up to this far above the feet (a step, a ledge) are walked to
BODY_HALF_WIDTH = 0.3
BODY_HEIGHT = 1.8
PRECISE_RADIUS = (
    0.2  # centre-to-centre, so the box stays inside the stance cell (0.5 - 0.3)
)


# Gather sessions move like natural cohort 30 (without swimming) (results/natural/20261101T135049338315Z,
# 10/10, body planner): the planner may dig, pillar and bridge as Baritone does, so a
# target under the ground is reached by a planned dig whose way back is a route too.
# cut rules (RoutePlanner.cutAllowed): no break beside or under fluid or unknown cells.
NATURAL_MOVEMENT = MovementPolicy(
    allow_diagonals=True,
    allow_sprint=True,
    max_drop=3,
    allow_shapes=True,
    max_gap=2,
    # swimming is always on: the body has the drowning reflex (treadWater, surface)
    allow_swim=True,
    # every movement ability is always on; the planner prices them
    allow_climb=True,
)
DIG_TERRAIN = TerrainPolicy(
    break_budget=64,
    place_budget=64,
    # every allowed building block (dirt first): a bot holding only cobblestone was sent
    # place_supply 0 and could not pillar out of a hole (runs of 20261113)
    material_allowlist=("minecraft:dirt", "minecraft:cobblestone"),
    allow_bridge=True,
    max_support_stack=3,
    allow_pillar=True,
    planner_tick_costs=True,
    planner_unknown=True,
    planner_edits=True,
    baritone_search=True,
    baritone_diagonals=True,
    baritone_moves=True,
    planner_swim=True,
    heuristic_weight=4,  # see body_search.EDIT_NODE_LIMIT
    allow_falling_neighbours=False,
    use_doors=True,
    climb_vines=True,
)

# One motor and action inventory. A task changes permissions, not body classes.
WALK_TERRAIN = replace(
    DIG_TERRAIN,
    allow_break=False,
    allow_place=False,
    break_budget=0,
    place_budget=0,
    allow_bridge=False,
    allow_pillar=False,
    heuristic_weight=1,  # no digs: keep walking routes optimal
)


def dead_end(last, before, after) -> tuple[bool, bool]:
    """(boxed_in, shut_in) after a walk that ended with plan result ``last``.

    Only an ``unreachable`` plan that expanded nodes ran out of them: the body's
    whole region was searched (a goal proof expands none; ``incomplete`` was
    capped). With no move from the spot the body is shut in that region, whatever
    its size: a ledge the house closed around (run 20261122T024835715542Z: 9 nodes;
    20261122T125350762344Z: a roof slope of 40), or a closed floor. It is boxed in
    after a node or two (run 20261117T155135309814Z: an eave slab, every other
    stance fails the same way). The runner picks a drop or a tower by where the
    goal is (build_runner._stance)."""
    expanded = getattr(last, "expanded", 0) if last is not None else 0
    stuck = (
        last is not None
        and last.status == "unreachable"
        and expanded >= 1
        and math.dist(before, after) < 0.75
    )
    return stuck and expanded <= BOXED_IN_NODES, stuck


def _clickable(cell) -> bool:
    """A cell with a face to click: collision, or scaffolding (a lattice with none)."""
    return bool(cell) and bool(
        cell.get("collision_boxes") or cell.get("block") == "minecraft:scaffolding"
    )


def _tower_top(cells, node: Pos) -> bool:
    """A stable stance above the top platform, rather than hanging inside the column."""
    x, y, z = node
    below, here, above = (
        cells.get((x, y - 1, z)),
        cells.get(node),
        cells.get((x, y + 1, z)),
    )
    return bool(
        below
        and here
        and above
        and below.get("block") == "minecraft:scaffolding"
        and here.get("block") in AIR_BLOCKS
        and above.get("block") in AIR_BLOCKS
    )


def _scaffold_stance(cells, node: Pos) -> bool:
    """Top of a scaffolding column, or a climbed standable cell inside one."""
    if _tower_top(cells, node):
        return True
    cell = cells.get(node)
    return bool(
        cell and cell.get("block") == "minecraft:scaffolding" and standable(cells, node)
    )


def _clean_feet(cells, node: Pos) -> bool:
    """True when the feet cell holds no non-scaffolding collision box."""
    cell = cells.get(node)
    if not cell or cell.get("block") == "minecraft:scaffolding":
        return True
    return not cell.get("collision_boxes")


EYE_HEIGHT = 1.62
_HIT_SAMPLES = ((0, 0), (0.25, 0.25), (-0.25, 0.25), (0.25, -0.25), (-0.25, -0.25))


def _reaches_scaffold_column(cells, node: Pos, target: Pos, reach: float) -> bool:
    """True when every eye offset around ``node`` reaches a side face of a scaffolding
    block below ``target`` (vanilla ScaffoldingBlockItem extends columns upward with no
    vertical cap on a side click)."""
    tx, ty, tz = target
    if (node[0], node[2]) == (tx, tz):
        return False
    col: list[Pos] = []
    cy = ty - 1
    while (cells.get((tx, cy, tz)) or {}).get("block") == "minecraft:scaffolding":
        col.append((tx, cy, tz))
        cy -= 1
    if not col:
        return False
    eyes = [
        (node[0] + 0.5 + dx, node[1] + EYE_HEIGHT, node[2] + 0.5 + dz)
        for dx, dz in ((0, 0), (0.2, 0.2), (-0.2, 0.2), (0.2, -0.2), (-0.2, -0.2))
    ]
    sides = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1))
    for eye in eyes:
        hit = False
        for c in col:
            for d in sides:
                if (node[0] - c[0]) * d[0] + (node[2] - c[2]) * d[2] <= 0:
                    continue
                axis = 0 if d[0] else 2
                other = 2 if axis == 0 else 0
                for u, v in _HIT_SAMPLES:
                    point = [c[0] + 0.5, c[1] + 0.5, c[2] + 0.5]
                    point[axis] += d[axis] * 0.5
                    point[other] += u
                    point[1] += v
                    if math.dist(eye, point) <= reach and _ray_clear(
                        cells, eye, point, c
                    ):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                break
        if not hit:
            return False
    return True


# ScaffoldingBlockItem walks at most this many cells sideways along a run.
SCAFFOLD_SIDEWAYS = 7
# A scaffolding block as the sensor reports it: no collision boxes from the side.
SCAFFOLD_CELL = {
    "block": "minecraft:scaffolding",
    "fluid": "minecraft:empty",
    "collision_boxes": [],
}
# A temporary full-cube block (dirt/cobblestone) used for virtual tower/prop checks.
_SOLID_CELL = {
    "block": "minecraft:dirt",
    "fluid": "minecraft:empty",
    "collision_boxes": [[0, 0, 0, 1, 1, 1]],
}
_TOP_SAMPLES = ((0, 0), (0.25, 0.25), (-0.25, 0.25), (0.25, -0.25), (-0.25, -0.25))


def _reaches_scaffold_run(cells, eye, target: Pos, reach: float) -> bool:
    """Does ``eye`` see the top face of a block of a horizontal scaffolding run
    beside ``target``? Clicked not sneaking with the heading toward ``target``, that
    top puts the new block past the run (vanilla ScaffoldingBlockItem; BodyBuild's
    bridge sources): from on top of a tower, the side face toward the next cell is
    under the body's feet, and the top face is how a player bridges."""
    tx, ty, tz = target
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        for k in range(1, SCAFFOLD_SIDEWAYS + 1):
            c = (tx + dx * k, ty, tz + dz * k)
            if (cells.get(c) or {}).get("block") != "minecraft:scaffolding":
                break
            top = c[1] + 1
            if eye[1] <= top:
                continue
            for u, v in _TOP_SAMPLES:
                point = (c[0] + 0.5 + u, top, c[2] + 0.5 + v)
                if math.dist(eye, point) <= reach and _ray_clear(cells, eye, point, c):
                    return True
    return False


def _ray_clear(cells, eye, point, block: Pos) -> bool:
    """Every cell the segment eye->point passes through, exactly (grid traversal, as the
    game's clip does), is clear except ``block``. Sampling the segment let a diagonal
    ray slip between two walls meeting at an edge."""
    eye_cell = tuple(math.floor(v) for v in eye)
    cell = list(eye_cell)
    end = tuple(math.floor(v) for v in point)
    d = [point[i] - eye[i] for i in range(3)]
    step = [1 if v > 0 else -1 for v in d]
    t_max, t_delta = [], []
    for i in range(3):
        if d[i] == 0:
            t_max.append(math.inf)
            t_delta.append(math.inf)
            continue
        edge = cell[i] + (1 if d[i] > 0 else 0)
        t_max.append((edge - eye[i]) / d[i])
        t_delta.append(abs(1 / d[i]))
    for _ in range(64):
        c = tuple(cell)
        if c == end or c == block:
            return True
        if c == eye_cell:
            ec = cells.get(c)
            if (
                ec is not None
                and ec.get("block") == "minecraft:scaffolding"
                and any(
                    _segment_hits_box(eye, point, c, box)
                    for box in (ec.get("collision_boxes") or ())
                )
            ):
                return False
        elif _clickable(cells.get(c)):
            return False
        i = t_max.index(min(t_max))
        if t_max[i] > 1:
            return True
        cell[i] += step[i]
        t_max[i] += t_delta[i]
    return True


def _support_cells(required) -> set[Pos] | None:
    """``required`` (one cell, several, or None for any) as a set of cells."""
    if required is None:
        return None
    if (
        isinstance(required, tuple)
        and len(required) == 3
        and isinstance(required[0], int)
    ):
        return {required}
    return {tuple(p) for p in required}


def _on_scaffold_column(cells, target: Pos) -> bool:
    return (cells.get((target[0], target[1] - 1, target[2])) or {}).get(
        "block"
    ) == "minecraft:scaffolding"


def places_from(
    cells,
    eye,
    node: Pos,
    target: Pos,
    reach: float,
    required=None,
    *,
    scaffold: bool = False,
    state: str | None = None,
) -> bool:
    """Would place_block put a block into the empty ``target`` from ``eye`` (a body
    standing in ``node``)? The server clicks any of 19 points of a support face in
    reach that the eye sees (place_sight) and that gives ``state`` (place_state: a
    slab's half, a log's axis). A ray to the target's centre is not that
    test: the block placed a moment before beside it hides the centre while the floor
    face past it shows, and the body walked after nearly every block of a wall (run
    20261119T225430670386Z: 119 walks, 195 s, to cells it could place from where it
    stood). Scaffolding on a column is the column's own rule: a side click anywhere
    up it extends it (vanilla ScaffoldingBlockItem)."""
    if scaffold and _on_scaffold_column(cells, target):
        return interacts(cells, node, target, reach) or _reaches_scaffold_column(
            cells, node, target, reach
        )
    if scaffold and _reaches_scaffold_run(cells, eye, target, reach):
        return True
    return place_sight.clickable_from(
        cells, eye, target, reach, sources=_support_cells(required), state=state
    )


# The body stops within PRECISE_RADIUS of a stance's centre. An eye this far off the
# centre on each side still placing is a sound stance; one that places only from the
# exact centre misses as often as not (run 20261120T083111010944Z).
SOUND_OFFSET = 0.15
SOUND_RANKED = 8  # the head of the stance list checked for soundness (4 eyes each)


def stance_eye(node: Pos) -> tuple[float, float, float]:
    """The eye of a body standing at the centre of ``node``."""
    return (node[0] + 0.5, node[1] + EYE_HEIGHT, node[2] + 0.5)


class StuckGuard:
    """True once the position has stayed within STUCK_MOVE for STUCK_TICKS ticks."""

    def __init__(self, ticks: int = STUCK_TICKS, move: float = STUCK_MOVE):
        self.ticks, self.move = ticks, move
        self.anchor: tuple[tuple[float, ...], int] | None = None

    def stuck(self, position, tick: int) -> bool:
        if self.anchor is None or math.dist(position, self.anchor[0]) >= self.move:
            self.anchor = (tuple(position), tick)
            return False
        return tick - self.anchor[1] >= self.ticks


class _GotoController(SegmentedController):
    """Segmented controller that ends its own drive (arrived, unreachable, deadline)."""

    probe = False  # plan-only drive: end on the first verdict, never move the motor
    no_drive = False  # drive_native keeps every motor submission off
    walk_wall = 0.0  # wall of the walk a successful probe turns into
    walk_partial = False  # a probe that ran out of search walks the moves it found
    allow_partial_gain = True  # False for pillar_to: never walk away from the column
    on_pass = None  # called with the body position while a route is followed
    last_partial_walk = None  # (start, end) of the last partial walked to this goal
    dead_ends = (
        None  # {"revision", "ends"}: partial endpoints known unreachable-from, per goal
    )

    @property
    def planner_radius(self):
        # Plans use cell centres; the motor may finish 0.2 blocks off-centre.
        # Leave that room so a boundary cell cannot produce a stationary
        # "reached" plan while the actual body is still outside its goal.
        return (
            self.radius
            if self.interact_reach is not None
            else max(0.0, self.radius - 0.2)
        )

    def __init__(
        self,
        *args,
        deadline: float,
        probe: bool = False,
        idle_state=None,
        walk_wall: float = 0.0,
        walk_partial: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.deadline = deadline
        self.walk_wall = walk_wall
        self.walk_partial = walk_partial
        if probe:
            self.probe = True
            self.no_drive = True
        self.unreachable_since: int | None = None
        self.searching_since: int | None = None
        self.goal_progress_ticks = None  # companion goals may be far and slow
        self.progress_state = None  # set by goto_stance: one clock across its loop
        self.guard = StuckGuard()
        self.idle_anchor = None
        self.idle_since = time.monotonic()
        self.idle_state = idle_state

    def stop_reason(self, observation) -> str | None:
        if self.mode == "arrived":
            return "arrived"
        if not self.probe and time.monotonic() > self.deadline:
            return "deadline"
        body = observation.get("body") or {}
        position = body.get("position")
        if position is not None:
            now = time.monotonic()
            shared = getattr(self, "idle_state", None)
            if shared is not None:
                self.idle_anchor, self.idle_since = shared["anchor"], shared["since"]
            step = observation.get("step_follower", {})
            editing = (
                step.get("state") == "following" and step.get("issued") is True
            ) or self.mode in EDIT_MODES
            # Report-only: the wall-clock idle clock never ends a goto (it fired while
            # plan requests were in flight and during multi-block edits). The one
            # watchdog is the tick-based StuckGuard below.
            if (
                shared is not None
                and shared["anchor"] is not None
                and math.dist(position, shared["anchor"]) < STUCK_MOVE
                and not editing
                and now - shared["since"] >= IDLE_REPORT_S
            ):
                print(
                    f"STALL {now - shared['since']:.1f}s navigation idle at {position}; "
                    f"mode={self.mode}, route={observation.get('route_follower', {})}",
                    flush=True,
                )
                shared["since"] = now
            if shared is not None and (
                shared["anchor"] is None
                or editing
                or math.dist(position, shared["anchor"]) >= STUCK_MOVE
            ):
                shared.update(anchor=tuple(position), since=now)
            if editing or self.mode in PLANNING_MODES:
                self.guard.anchor = None  # ticks spent planning or editing are not idle
        if self.probe:
            return self._probe_verdict()
        last = getattr(self, "last_result", None)
        if (
            self.mode == "incomplete"
            and last is not None
            and last.status == "incomplete"
            and len(last.route) < 2
        ):
            # Acquisition revisions otherwise rerun the same exhausted search
            # without a single usable move. Let the job try another stance.
            return "incomplete"
        if "position" in body and self.guard.stuck(
            body["position"], observation["tick"]
        ):
            return "stuck"
        if self.mode == "unreachable":
            self.unreachable_since = self.unreachable_since or observation["tick"]
            if observation["tick"] - self.unreachable_since >= UNREACHABLE_TICKS:
                return "unreachable"
        else:
            self.unreachable_since = None
        # ponytail: the first map upload is not a search. Run 20261110T023244861255Z
        # cut it off at 60 ticks, 55 times in a row, so no route was ever planned.
        last = getattr(self, "last_result", None)
        uploading = last is not None and last.reason == "planner map transfer"
        if uploading or self.mode in (
            {"arrived", "following", "centering"} | EDIT_MODES
        ):
            self.searching_since = None
        elif self.mode in {"searching", "incomplete", "planning", "terrain_incomplete"}:
            # An interaction goal whose stance the known map cannot reach floods the
            # known graph for ever (live run 20261101T190355487393Z: 25 minutes),
            # and a planning <-> terrain_incomplete loop must not reset this clock.
            self.searching_since = self.searching_since or observation["tick"]
            if observation["tick"] - self.searching_since >= SEARCH_STALL_TICKS:
                return "stalled"
        return None

    def _probe_verdict(self) -> str | None:
        """End the motor-parked probe on the planner's first firm answer.

        The refresh loop replans the same start and goal on a static map, so one
        done verdict is final: the oscillation of searching/unreachable modes kept
        only the 200-tick StuckGuard alive, and 12 of those 10 s walks ended a
        gather at 0 tasks (run 20261106T170919632775Z)."""
        last = self.last_result
        if (
            last is not None
            and last.status == "incomplete"
            and len(last.route) > 1
            and (
                self.walk_partial
                or getattr(self, "route_steps", None)
                or self.mode in EDIT_MODES
                or (self.allow_partial_gain and self._partial_gains(last.route))
            )
        ):
            # The search ran out of nodes. Its moves toward the goal are walked at
            # once, edits included: probing the other stances first stood the body
            # still for 7.4 s (iron run 20261112T184523101728Z) and ruled a plan that
            # starts with a dig "unreachable" (iron run 20261113T211211538468Z, 8 in
            # a row). Only a placing-off walk with no edits treats it as unreachable.
            return self._walk_on()
        if self.mode in {"following", "centering"} | EDIT_MODES or (
            last is not None and last.status in {"reached", "segment"}
        ):
            if last is not None and last.status == "incomplete":
                return "unreachable"
            return self._walk_on()  # route adopted: drive it, do not plan it again
        if self.mode == "terrain_planning":
            return None  # an edit search is in flight: wait for its verdict
        if self.mode in {"unreachable", "incomplete", "terrain_incomplete"} or (
            self.last_result is not None
            and self.last_result.status in {"unreachable", "incomplete"}
        ):
            return "unreachable"
        if time.monotonic() > self.deadline:
            # No verdict inside PROBE_WALL_S: walk it as before, keeping the
            # in-flight map upload and search (run 20261111T134723510228Z).
            return self._walk_on()
        return None

    def _partial_gains(self, route) -> bool:
        """A partial route that ends nearer the goal than it starts is walked even
        with placing off: dropping it and asking again re-ran the same capped
        search from the same start and stood the body 5 s (iron-xray run
        20261116T143915798373Z, plan-172 then plan-173)."""
        if math.dist(route[-1], self.goal) >= math.dist(route[0], self.goal):
            return False
        # A partial that looks nearer on paper but moved the body nowhere last time
        # (boxed in by its own wall: run 20261116T155654335035Z, 13 s standstill)
        # is the same dead end again: let the stance go unreachable instead.
        dead = self.dead_ends
        if dead is not None and dead["ends"]:
            revision = getattr(getattr(self, "map_view", None), "revision", None)
            if (
                dead["revision"] != revision
            ):  # a map fact: new terrain may open the way on
                dead["ends"].clear()
            elif any(math.dist(route[-1], end) <= 1.5 for end in dead["ends"]):
                return False
        walked = self.last_partial_walk
        if walked is None or math.dist(walked[1], route[0]) > 1.5:
            return True
        return math.dist(walked[1], self.goal) < math.dist(walked[0], self.goal) - 0.5

    def _walk_on(self) -> None:
        """Turn the parked probe into the walk on the same plan and upload."""
        self.probe = self.no_drive = False
        self.deadline = time.monotonic() + self.walk_wall
        return None


def _fresh_idle(op):
    """A navigation op starts with a fresh five-second idle clock: the one it left
    behind is as old as the craft, furnace wait or failed break since the last edit,
    and its first check killed the walk as "stuck". Probes and walks inside the op
    still share the clock."""

    @functools.wraps(op)
    def wrapped(self, *args, **kwargs):
        self._navigation_idle.update(anchor=None, since=time.monotonic())
        return op(self, *args, **kwargs)

    return wrapped


# Build blocks the planner must never break: they do not drop themselves intact or hold
# state (glass and ice shatter, leaves decay, the rest have block entities or attachments).
_NO_BUILD_BREAK = (
    "glass",
    "_leaves",
    "ice",
    "chest",
    "barrel",
    "furnace",
    "smoker",
    "bed",
    "door",
    "sign",
    "bookshelf",
    "lantern",
    "hopper",
    "campfire",
    "shulker",
    "banner",
    "head",
)


def _breakable_build_block(cell) -> bool:
    """A full cube (one 0..1 box) whose block drops itself."""
    block = cell.get("block") or ""
    boxes = cell.get("collision_boxes") or []
    return (
        len(boxes) == 1
        and tuple(boxes[0]) == (0, 0, 0, 1, 1, 1)
        and not any(w in block for w in _NO_BUILD_BREAK)
    )


class CompanionBody:
    def __init__(
        self,
        bridge,
        memory: SpatialMemory,
        *,
        goto_wall_s: float = GOTO_WALL_S,
        movement_policy=None,
        terrain_policy: TerrainPolicy | None = None,
    ):
        self.bridge, self.memory = bridge, memory
        self.goto_wall_s = goto_wall_s
        self._partial_walks: dict = {}
        self._dead_ends: dict = {}  # goal -> {"revision": map revision, "ends": [endpoints]}
        self.movement_policy = (
            movement_policy if movement_policy is not None else NATURAL_MOVEMENT
        )
        self.terrain_policy = (
            terrain_policy if terrain_policy is not None else WALK_TERRAIN
        )
        # building: arrive near the cell centre, clear of the target
        self.precise_stance = False
        self.stray_footholds: set[Pos] = set()  # blocks the planner placed to walk
        self.stray_breaks: dict[
            Pos, str
        ] = {}  # blocks it broke to walk -> original state
        self.edits_placed = self.edits_broken = (
            0  # planner edits, every placement counted
        )
        self.build_cells: dict[
            Pos, str
        ] = {}  # the template's non-air cells (see protect)
        # set per placement by build_runner: the one face the block hangs on
        self.required_support: Pos | None = None
        # set with it: the block state placed, whose half, axis or facing the click
        # decides (place_state); None for any click
        self.placing_state: str | None = None
        self.placing_scaffold: bool = False
        # set per sweep step by build_runner: the next cells (-> required support and
        # state) a stance should also place, so one walk serves several blocks
        self.stance_cover: dict[Pos, tuple[Pos | set[Pos] | None, str | None]] = {}
        # in-game route, stance and block markers (mclab.markers); None: off
        self.markers = None
        self._obs: dict = {}
        self._inv: dict[str, int] = {}
        self._rows: list[dict] = []  # the per-slot rows behind _inv
        self._inv_at = -math.inf  # how fresh _inv is (_set_inventory)
        self._overlay: dict[Pos, tuple[str, int, list | None]] = {}
        self.log: list[dict] = []  # one row per op or goto, for the results folder
        self.places: PlaceGraph | None = None  # named waypoints (start_session)
        self.policies: list[dict] = []
        self._navigation_idle = {"anchor": None, "since": time.monotonic()}
        self._failed_stance_probes = {}
        self._failed_stances: dict = {}  # (target, stance) -> retry-after (monotonic)
        self._goto_edges: set | None = None  # failed route edges shared by one goto
        # failed edges of the session, each kept while the blocks around it are unchanged
        self.edge_failures = EdgeFailures(cells=self._cells_now)
        self.boxed_in = False  # the last search from here found no move at all
        self.shut_in = False  # ... or searched its whole region, no way off (dead_end)
        # building: called with the body position while a walk is under way, to
        # place what comes within reach (set by build_runner._sweep)
        # (position, route=points the motor has still to drive) -> None
        self.on_pass: Callable[..., None] | None = None
        self._ahead = None  # plan_ahead: (controller, goal, radius, position)

    def policy_id(self) -> int:
        """Deduplicate large protected footprints across navigation records."""
        snapshot = self.policy_snapshot()
        if snapshot not in self.policies:
            self.policies.append(snapshot)
        return self.policies.index(snapshot)

    def policy_snapshot(self) -> dict:
        """Record the effective task permissions, separately from available ops."""
        return {
            "movement": asdict(self.movement_policy),
            "terrain": asdict(self.terrain_policy),
        }

    # --- observation -------------------------------------------------------
    def observe(self) -> dict:
        """Fetch one policy observation and ingest it into the map."""
        obs = policy_state(self.bridge)
        self.memory.ingest(obs)
        # A cell the sensor sampled after our own edit is the truth: a door we
        # placed closed and the motor then walked through stayed "closed" in the
        # overlay for 200 ticks, so the final door repair saw nothing to fix (run
        # 20261112T032720895516Z, door [-19, 65, 58] opened at tick 3402).
        for pos, (_, at, _boxes) in list(self._overlay.items()):
            sensed_at = self.memory.live_observed_tick(pos)
            if sensed_at is not None and sensed_at > at:
                del self._overlay[pos]
        self._obs = obs
        self._set_inventory(obs.get("body", {}).get("inventory"), obs.get("tick"))
        return obs

    def _set_inventory(self, stacks, tick=None, *, op=False) -> None:
        """Keep the newest inventory. Snapshots (/state, /policy) are published at
        the end of the tick they are stamped with, but the body runs ops at the
        start of the next one, before the server bumps its count, so an op stamped
        T happened after the snapshot stamped T: an op ranks half a tick later.
        Run 20261122T142224546926Z replanned from the snapshot stamped with its craft's own
        tick and crafted planks again from logs it had used up. Unstamped stacks
        (offline fakes) always apply."""
        if stacks is None:
            return
        if tick is not None:
            at = float(tick) + (0.5 if op else 0.0)
            if at < self._inv_at:
                return
            self._inv_at = at
        inv: dict[str, int] = {}
        for stack in stacks:
            inv[stack["item"]] = inv.get(stack["item"], 0) + stack["count"]
        self._inv, self._rows = inv, list(stacks)

    def free_slots(self) -> int:
        """Empty main-inventory slots (0..35; armour and offhand excluded). The mod
        sends only the non-empty slots."""
        return MAIN_SLOTS - len(
            {r["slot"] for r in self._rows if 0 <= r["slot"] < MAIN_SLOTS}
        )

    def can_hold(self, item: str, items: dict | None = None) -> bool:
        """A free slot, or a held stack of ``item`` below its max size (``items``:
        the knowledge pack's item table, 64 where unknown)."""
        if self.free_slots():
            return True
        top = item_stack(items or {}, item)
        return any(
            r["item"] == item and r["count"] < top and 0 <= r["slot"] < MAIN_SLOTS
            for r in self._rows
        )

    def _tick(self) -> int:
        return int(self._obs.get("tick", 0))

    def position(self) -> Pos:
        pos = self._obs["body"].get("position")
        if pos is None:
            raise BodyDied("observation has no position: the body is dead")
        x, y, z = pos
        return (math.floor(x), math.floor(y + 1e-6), math.floor(z))

    def _state_body(self) -> dict | None:
        """The body block of the bridge's small /state: it carries the held inventory,
        food and health already, so the gather loop's many reads of them need no
        /policy round trip (run 20261116T181120247153Z: ~1 s each, 12-75 s standstills).
        None when the bridge has no /state or it holds no body."""
        state = getattr(self.bridge, "state", None)
        return (state().get("body") or None) if state is not None else None

    def episode_ended(self) -> bool:
        """True once the lab episode under the body is over (its tick limit hit):
        every plan after it is cancelled, so the body can only stand still."""
        state = getattr(self.bridge, "state", None)
        return state is not None and state().get("state") == "episode_terminal"

    def inventory(self) -> dict[str, int]:
        """Fresh from the body: an op result's inventory predates the pickup of its
        own drop, so a stale count made each replan ask for more stone (run
        20261102T042713034884Z: five stone passes, 102 cobblestone), unless that
        /state predates the last op (_set_inventory)."""
        state = getattr(self.bridge, "state", None)
        snap = state() if state is not None else {}
        body = snap.get("body") or None
        if body is None or body.get("inventory") is None:
            self.observe()
        else:
            self._set_inventory(body["inventory"], snap.get("tick"))
        return dict(self._inv)

    def food(self) -> int:
        body = self._state_body()
        if body is None or body.get("food") is None:
            body = self.observe()["body"]
        return int(body.get("food", 20))

    def health(self) -> float:
        body = self._state_body()
        if body is None or body.get("health") is None:
            body = self.observe()["body"]
        return float(body.get("health", 20.0))

    def hurt_drop(self) -> int:
        """The longest drop worth taking to get out of a spot with no other way down:
        a fall of h blocks costs h-3 HP, and at least KEEP_HEALTH is kept. 3 (no
        damage) when health is low."""
        spare = int(self.health()) - KEEP_HEALTH
        return max(3, min(HURT_DROP_MAX, 3 + spare))

    def held_food(self) -> int:
        foods = getattr(self, "foods", None) or {}
        return sum(n for i, n in self.inventory().items() if i in foods)

    def eat_when_needed(self) -> bool:
        """Eat the most filling held food when hungry (EAT_BELOW, as gathering
        does), or when hurt and the bar is too low to regenerate (REGEN_FOOD).
        `foods` ({item: facts}) is set by the job from the knowledge pack; without
        it the body does not know what is food."""
        foods = getattr(self, "foods", None) or {}
        food = self.food()
        if food > EAT_BELOW and (food >= REGEN_FOOD or self.health() >= 20):
            return False
        held = [i for i, n in self.inventory().items() if n > 0 and i in foods]
        if not held:
            return False
        best = max(held, key=lambda i: float(foods[i].get("nutrition", 0)))
        return self.eat(best).ok

    @contextmanager
    def allowing_hurt_drops(self):
        """Routes may drop farther than 3 blocks while health allows (run
        20261117T144843447702Z: shut out on an eave slab 6 above the ground, glass and
        flower pots of the house on every side; every plan unreachable for 1 min)."""
        drop = self.hurt_drop()
        saved = self.movement_policy
        if drop > saved.max_drop:
            self.movement_policy = replace(saved, max_drop=drop)
        try:
            yield drop
        finally:
            self.movement_policy = saved

    def get_down(self) -> bool:
        """Shut in on a ledge the house closed around: take the shortest drop to a
        spot beside the body, at most :meth:`hurt_drop` (KEEP_HEALTH is kept). The
        way back up is the stairs or a tower like any other climb; a drop is never
        the planned way down while one of those exists.

        A spot the body walks to without the drop is in the region it is shut in, so
        it is never the way out (issue #11: the nearest lower spot was one block down
        in the same pocket, and getting there cleared ``shut_in`` for nothing)."""
        x, y, z = self.position()
        drop = self.hurt_drop()
        if drop <= 3:
            return False
        cells = self._cells_now()
        walk = self._walkable(cells, (x, y, z))
        spots = sorted(
            (
                (x + dx, y - h, z + dz)
                for h in range(1, drop + 1)
                for dx in range(-2, 3)
                for dz in range(-2, 3)
                if (dx, dz) != (0, 0)
            ),
            key=lambda n: (y - n[1], abs(n[0] - x) + abs(n[2] - z)),
        )
        spots = [n for n in spots if standable(cells, n) and n not in walk]
        spots = spots[:GET_DOWN_TRIES]
        with self.allowing_hurt_drops():
            for spot in spots:
                if self._navigate(spot, reach=None, radius=0.6):
                    self.boxed_in = self.shut_in = False
                    return True
        return False

    def entities(self) -> list[dict]:
        """From the small /state (it carries visible_entities); /policy only when it does not."""
        state = getattr(self.bridge, "state", None)
        found = state().get("visible_entities") if state is not None else None
        if found is None:
            found = self.observe().get("visible_entities")
        return list(found or ())

    def has_face(self, x: int, y: int, z: int) -> bool:
        """The cell has a collision box to click or stand on, by its real shape (modded
        blocks included): a flower, torch or carpet has none."""
        return _clickable(self._cells_now().get((x, y, z)))

    def world(self, x: int, y: int, z: int) -> str | None:
        over = self._overlay.get((x, y, z))
        if over is not None and self._tick() - over[1] <= OVERLAY_TICKS:
            return over[0]
        cell = self.memory.cells.get((x, y, z))
        if cell is None:
            return None
        return format_state(cell["block"], dict(cell.get("properties") or {}))

    def find_blocks(
        self,
        block: str,
        where: Callable[[Pos], bool] | None = None,
        within: list[tuple[Pos, float]] | None = None,
    ) -> list[Pos]:
        """Delivered cells holding ``block`` (no properties compared) that have a
        known open face: a block buried in stone cannot be reached without digging,
        and a gather loop that tried each one cost minutes. ``where`` narrows the
        cells before the face test, which is the slow part for a common block.
        ``within`` (centre, radius pairs) bounds the scan itself: see ``_scan``."""
        keep = set(self.terrain_policy.protected_positions)
        return [
            p
            for p in self._scan(block, within)
            if p not in keep and (where is None or where(p)) and self._open_face(p)
        ]

    def _open_face(self, pos: Pos) -> bool:
        cells = self.memory.cells
        x, y, z = pos
        for dx, dy, dz in FACES:
            cell = cells.get((x + dx, y + dy, z + dz))
            if (
                cell is not None
                and not cell.get("collision_boxes")
                # A wet face is not a way in: mining swamp-bottom dirt drowned the
                # body (run 20261110T011637401098Z, disconnected_or_dead).
                and cell.get("block") not in ("minecraft:water", "minecraft:lava")
            ):
                return True
        return False

    def _scan(
        self, block: str, within: list[tuple[Pos, float]] | None = None
    ) -> list[Pos]:
        """Cells holding ``block``. ``within`` keeps the scan to the 16-block sections
        touching a cube of that radius round one of the centres; cells of those
        sections just outside the cube still come back (the caller tests exactly).
        Listing every stone cell of a 2,040-section map took 0.6-1.1 s a call."""
        cells = self.memory.cells
        sections = getattr(cells, "sections", None)
        if sections is None:  # plain dict map
            return [p for p, c in cells.items() if c.get("block") == block]
        # Pathing-grid view: match palette ids, and scan only sections that hold one
        # (a per-cell walk of the whole map took minutes per call).
        ids = {i for i, s in enumerate(cells.states) if s and s.get("block") == block}
        boxes = [
            [(math.floor(c - r) >> 4, math.floor(c + r) >> 4) for c in centre]
            for centre, r in within or ()
        ]
        found: list[Pos] = []
        for key, section in list(sections.items()):
            sx, sy, sz = key
            if within is not None and not any(
                all(lo <= s <= hi for s, (lo, hi) in zip(key, box, strict=True))
                for box in boxes
            ):
                continue
            if ids.isdisjoint(set(section.states)):
                continue
            found.extend(
                (sx * 16 + (o >> 8), sy * 16 + ((o >> 4) & 15), sz * 16 + (o & 15))
                for o, sid in enumerate(section.states)
                if sid in ids
            )
        return found

    def wait_ticks(self, ticks: int) -> None:
        self.bridge.ticks(ticks)

    def settle(self, cells, max_ticks: int = SETTLE_TICKS) -> bool:
        """Wait until the sensed map shows every cell passable (just broken blocks reach
        ``memory.cells`` a few ticks late; planning before that routes over the
        surface instead). Live run 20261101T205857512430Z: 170 ticks per stair step."""
        for _ in range(0, max_ticks, SETTLE_STEP):
            self.observe()
            known = [self.memory.cells.get(tuple(c)) for c in cells]
            if all(c is not None and not c.get("collision_boxes") for c in known):
                return True
            self.wait_ticks(SETTLE_STEP)
        return False

    # --- ops ---------------------------------------------------------------
    def _await(self, op: str, payload: dict, timeout: float):
        sequence = payload["sequence"]
        kinds = {f"{op}_done", f"{op}_failed"} | REJECTIONS

        def find():
            for event in reversed(self.bridge.events):
                if (
                    event["type"] in kinds
                    and int(event["data"].get("request_sequence", -1)) == sequence
                ):
                    return event
            return None

        def done(_state):
            self._advance_ahead()  # the next walk is planned while this op runs
            return find() is not None

        self.bridge.wait(done, timeout=timeout)
        return find()

    def sync_plug_keep(self) -> None:
        """Tell the body's water plug what the build still has to place, so it plugs
        with spare blocks only (sent when it changed). Run 20261121T180806599463Z
        plugged site-prep water with 21 of the house's cobblestone."""
        if not getattr(self, "build_cells", None):
            return
        keep = self.still_needed()
        if keep != getattr(self, "_plug_keep_sent", None):
            self.bridge.send("plug_keep", items=keep)
            self._plug_keep_sent = keep

    def op(
        self, name: str, /, **fields
    ) -> Result:  # positional-only: arena ops carry a ``name`` field
        if name in PLUG_KEEP_OPS:
            self.sync_plug_keep()
        payload = self.bridge.send(name, **fields)
        try:
            event = self._await(name, payload, OP_TIMEOUTS.get(name, OP_TIMEOUT_S))
        except TimeoutError:
            result = Result(False, "timeout")
        else:
            data = dict(event["data"])
            self._set_inventory(data.get("inventory"), event.get("tick"), op=True)
            if event["type"] == f"{name}_done":
                if name in {"place_block", "break_block", "use_block"}:
                    data["_event_tick"] = event.get("tick")
                result = Result(True, None, data)
            elif event["type"] == f"{name}_failed":
                result = Result(False, str(data.get("reason", "failed")), data)
            else:
                result = Result(False, f"rejected:{data.get('reason')}", data)
        self.log.append({"op": name, "ok": result.ok, "reason": result.reason})
        return result

    def _remember(
        self,
        pos: Pos,
        state: str | None,
        tick: int | None = None,
        boxes: list | None = None,
    ) -> None:
        """``boxes``: the cell's real collision boxes from the body's edit result."""
        if state:
            self._failed_stance_probes.clear()
            self._failed_stances.clear()
            self._navigation_idle.update(
                anchor=tuple(
                    self._obs.get("body", {}).get("position", self.position())
                ),
                since=time.monotonic(),
            )
            self._overlay[tuple(pos)] = (
                state,
                self._tick() if tick is None else int(tick),
                boxes,
            )

    def _mark(self, **aim) -> None:
        """Point the in-game markers (mclab.markers), when the session has them."""
        if self.markers is not None:
            self.markers.aim(**aim)

    def place_block(self, pos: Pos, state: str, item: str) -> Result:
        self._mark(target=pos)
        result = self.op("place_block", pos=list(pos), state=state, item=item)
        if result.ok:
            placed = result.data.get("placed")
            self._remember(
                pos,
                placed,
                result.data.get("_event_tick"),
                result.data.get("collision_boxes"),
            )
            kind = (placed or "").split("[")[0].removeprefix("minecraft:")
            if kind in RECORDED_BLOCKS:
                self._record(kind, pos)
        return result

    def _record(self, kind: str, pos: Pos) -> None:
        if self.places is not None:
            self.places.remember(kind, pos)

    def go_to(self, name: str) -> bool:
        """Walk to a named place of this world; False when the name is unknown.

        From the place the bot stands at (within START_NEAR), follow the place
        graph's walked connections when they reach the target, else go straight.
        Every stop actually reached records the connection it walked."""
        if self.places is None or (target := self.places.named(name)) is None:
            return False
        here = self.position()
        start = self.places.nearest(here)
        if start is not None and math.dist(start.position, here) > START_NEAR:
            start = None
        stops = [target]
        if start is not None and start.id != target.id:
            by_id = {p.id: p for p in self.places.listed()}
            path = self.places.route(start.id, target.id, verified_after_ns=0)
            stops = [by_id[i] for i in path[1:]] or stops
        for stop in stops:
            if not self.walk_far(stop.position):
                return False
            if start is not None and start.id != stop.id:
                self.places.put_connection(
                    Connection(
                        start.id,
                        stop.id,
                        "walk",
                        max(1.0, math.dist(start.position, stop.position)),
                        (),
                        "walked",
                        time.time_ns(),
                    )
                )
            start = stop
        return True

    def walk_far(self, goal: Pos, radius: float = 1.5) -> bool:
        """Walk to a goal of any distance in legs of at most LEG blocks.

        One walk_to ends at its 90 s wall long before 2,000 blocks; a leg ends
        inside the sensed horizon, so each plan is short and fresh. Legs follow
        the straight line at the current height (walk_to snaps it to a stance).
        ponytail: no chunk-level reachability; an ocean or a wall on the line is
        found leg by leg, and MAX_STALLS legs without progress end the trip."""
        here, stalls = self.position(), 0
        while (gap := math.dist(here[::2], goal[::2])) > radius:
            t = min(1.0, LEG / gap)
            x, z = (
                round(a + (b - a) * t)
                for a, b in zip(here[::2], goal[::2], strict=True)
            )
            last = t == 1.0
            ok = self.walk_to(
                (x, goal[1] if last else self._leg_y(x, here[1], z), z),
                radius if last else LEG_RADIUS,
            )
            now = self.position()
            if ok and last:
                return True
            closer = math.dist(here[::2], goal[::2]) - math.dist(now[::2], goal[::2])
            stalls = 0 if closer >= MIN_PROGRESS else stalls + 1
            if stalls >= MAX_STALLS:
                return False
            here = now
        # horizontally there, but a failed last leg may have stopped at another
        # height (below a ledge): only the 3D position counts as arrival
        return math.dist(here, goal) <= radius + 0.25

    def _leg_y(self, x: int, y: int, z: int) -> int:
        """The known stance in column x,z nearest height y, searched a leg's
        height either way: walk_to only looks STAND_SEARCH blocks, so a leg over
        a hill aimed inside it and was unreachable. Unknown column: y."""
        cells, reach = self._cells_now(), int(LEG)
        found = [
            h for h in range(y - reach, y + reach + 1) if standable(cells, (x, h, z))
        ]
        return min(found, key=lambda h: abs(h - y), default=y)

    def use_block(self, pos: Pos, item: str | None = None) -> Result:
        extra = {"item": item} if item else {}
        self._mark(target=pos)
        result = self.op("use_block", pos=list(pos), **extra)
        if result.ok:
            self._remember(
                pos,
                result.data.get("after"),
                result.data.get("_event_tick"),
                result.data.get("collision_boxes"),
            )
        return result

    def break_block(
        self, pos: Pos, tool: str | None = None, drops_optional: bool = False
    ) -> Result:
        extra: dict = {"tool": tool} if tool else {}
        if drops_optional:
            extra["drops_optional"] = True
        self._mark(target=pos)
        result = self.op("break_block", pos=list(pos), **extra)
        if result.ok:
            self._remember(pos, "minecraft:air", result.data.get("_event_tick"))
        elif (data := result.data or {}).get("plugged"):
            # fluid ran in and the leak-plug reflex refilled the cell with a block
            self._remember(
                pos,
                data["plugged"],
                data.get("_event_tick"),
                data.get("collision_boxes"),
            )
        return result

    def craft(self, recipe: str, times: int, table: Pos | None = None) -> Result:
        extra = {"table": list(table)} if table else {}
        return self.op("craft", recipe=recipe, times=times, **extra)

    def furnace(self, pos: Pos, action: str, **fields) -> Result:
        result = self.op("furnace", pos=list(pos), action=action, **fields)
        if result.ok:
            self._record("furnace", pos)
        return result

    def equip(self, item: str, slot: int | None = None) -> Result:
        if slot is None:
            return self.op("equip", item=item)
        return self.op("equip", item=item, slot=slot)

    def _axe(self) -> str | None:
        tiers = ("wooden", "golden", "stone", "copper", "iron", "diamond", "netherite")
        axes = [
            f"minecraft:{t}_axe" for t in tiers if self._inv.get(f"minecraft:{t}_axe")
        ]
        return axes[-1] if axes else None

    def attack_entity(self, entity: str, weapon: str | None = None) -> Result:
        extra = {"weapon": weapon} if weapon else {}
        return self.op("attack_entity", entity=entity, **extra)

    def fight(
        self,
        *,
        radius: int,
        floor: int,
        max_ticks: int,
        entity: str | None = None,
        weapon: str | None = None,
        anchor=None,
        leash: int | None = None,
        tactic: dict | None = None,
        allow_player: bool = False,
    ) -> Result:
        """The body's fight motor (BODY_ACTION_OPS ``fight``): hostiles within
        ``radius`` until none is left, health is at ``floor``, a creeper comes close
        or ``max_ticks`` pass. ``anchor`` and ``leash`` keep it beside an ally;
        ``tactic`` (``companion_combat.Tactic.wire``) says how to fight.
        ``allow_player``: ``entity`` may be a player (an owner-commanded attack
        only; the mod refuses a player target otherwise)."""
        fields = {
            "entity": entity,
            "weapon": weapon,
            "anchor": list(anchor) if anchor is not None else None,
            "leash": leash,
            "tactic": tactic,
            "allow_player": True if allow_player else None,
        }
        extra = {k: v for k, v in fields.items() if v is not None}
        return self.op(
            "fight", radius=radius, floor=floor, max_ticks=max_ticks, **extra
        )

    def escape(self, *, radius: int, clear: int, max_ticks: int) -> Result:
        """The body's escape motor (BODY_ACTION_OPS ``escape``): sprint-jump away
        from every hostile within ``radius`` (new ones too) until none is within
        ``clear``; fails ``cornered`` when no safe heading is left."""
        return self.op("escape", radius=radius, clear=clear, max_ticks=max_ticks)

    def trial(self, **spec) -> Result:
        """Operator combat trials only (BODY_ACTION_OPS ``trial``): clear the area and
        the inventory, give, heal, set the time, summon. Never an intent or a tool."""
        return self.op("trial", **spec)

    # --- arena runtime (docs/ARENA_RUNTIME.md; ops of com.vulpine.mcb.arena) ---
    def arena_build(
        self,
        plan: dict,
        origin,
        *,
        name: str | None = None,
        dimension: str | None = None,
    ) -> Result:
        """Register and place an arena plan with its min corner at ``origin``. The ack
        (``state`` building) comes at once; the job then ends with an ``arena_built``
        event (``arena_build_aborted`` when it cannot finish)."""
        extra = {
            k: v for k, v in (("name", name), ("dimension", dimension)) if v is not None
        }
        return self.op("arena_build", plan=plan, origin=list(origin), **extra)

    def arena_remove(self, name: str, *, restore: bool = False) -> Result:
        """Unregister an arena; ``restore`` first sets its whole box to air."""
        return self.op("arena_remove", name=name, restore=restore)

    def arena_list(self) -> Result:
        return self.op("arena_list")

    def arena_status(self, name: str) -> Result:
        """State, build progress, and the match (wave, live wave mobs, participants)."""
        return self.op("arena_status", name=name)

    def arena_start(
        self, name: str, mode: str, *, waves=None, participants=None, teams=None
    ) -> Result:
        """Start a ``waves`` or ``teams`` match; ``participants`` default to everyone inside."""
        fields = {"waves": waves, "participants": participants, "teams": teams}
        given = {k: v for k, v in fields.items() if v is not None}
        return self.op("arena_start", name=name, mode=mode, **given)

    def arena_stop(self, name: str) -> Result:
        """End the match and discard every wave mob."""
        return self.op("arena_stop", name=name)

    def wear(self) -> Result:
        """Put on the best armor carried in each slot and a shield in the off hand
        (BODY_ACTION_OPS ``wear``); data: ``put_on``, ``worn`` by slot, ``armor``."""
        return self.op("wear")

    def rows(self) -> list[dict]:
        """The inventory rows last seen (slot, item, count): 36-39 armor, 40 the
        off hand."""
        return list(self._rows)

    def use_entity(self, entity: str, item: str | None = None) -> Result:
        extra = {"item": item} if item else {}
        return self.op("use_entity", entity=entity, **extra)

    def eat(self, item: str | None = None) -> Result:
        return self.op("eat", **({"item": item} if item else {}))

    def drink(self, potion: str) -> Result:
        """Drink the first held potion of that id (BODY_ACTION_OPS ``drink``)."""
        return self.op("drink", potion=potion)

    def throw_potion(
        self, potion: str, at, *, item: str = "minecraft:splash_potion"
    ) -> Result:
        """Throw a splash (or lingering) potion of that id (BODY_ACTION_OPS
        ``throw_potion``); ``at``: "self" (at its feet), ``{"entity": uuid}`` or
        ``{"position": [x, y, z]}``. The body aims the arc."""
        return self.op("throw_potion", potion=potion, item=item, at=at)

    def consume(self, use) -> Result:
        """Carry out a ``companion_consumables.Use``."""
        fields = use.fields()
        if use.op == "eat":
            return self.eat(fields["item"])
        if use.op == "drink":
            return self.drink(fields["potion"])
        return self.throw_potion(fields["potion"], fields["at"], item=fields["item"])

    def container(self, pos: Pos, action: str, items: list[dict] | None = None):
        extra = {"items": items} if items is not None else {}
        result = self.op("container", pos=list(pos), action=action, **extra)
        if result.ok:
            self._record("container", pos)
        return result

    # --- navigation --------------------------------------------------------
    def _planner_terrain(self):
        """The policy the route planner gets. A protected cell (the house) that holds
        foliage now is not the build's block, and site prep cuts it anyway, so the
        planner may break it (a protected leaf canopy sealed a 2-block walk into a
        10,000-node search, run 20261117T035331768325Z). A cell holding the build's
        own placed full-cube block that drops itself moves to ``costly_positions``:
        breakable at BUILD_BREAK_COST, and the build loop re-places it (without it
        the upper floor of a closed house was unreachable, run 20261117T055944364786Z).
        The template's air cells (``build_air``) may take footholds: the build loop
        adopts those as scaffolds and takes them back. The site ground stays protected."""
        policy = self._with_temporaries(self.terrain_policy)
        if not policy.protected_positions:
            return policy
        cells = self._cells_now()
        built = getattr(self, "build_cells", None) or {}
        air = getattr(self, "build_air", None) or ()
        keep, costly = [], []
        for p in policy.protected_positions:
            if p in air:
                continue
            if not policy.allow_break:
                keep.append(p)
                continue
            cell = cells.get(p) or {}
            block = cell.get("block") or ""
            if "_leaves" in block:
                continue
            if (
                p in built
                and block == parse_state(built[p])[0]
                and _breakable_build_block(cell)
            ):
                costly.append(p)
            else:
                keep.append(p)
        if not costly and len(keep) == len(policy.protected_positions):
            return policy
        return replace(
            policy, protected_positions=tuple(keep), costly_positions=tuple(costly)
        )

    def temporary_items(
        self, scaffolding: bool = True, prop: bool = False
    ) -> list[str]:
        """Held blocks the body may put down to climb or bridge and take back, best
        first (mclab.temporary_blocks): scaffolding, dirt, cobblestone, then spare
        plain blocks that drop themselves. Spare means beyond what the build still
        has to place. Without a build job: the policy's own materials. ``prop``: a
        block to click against, taken back in the same placement, so scaffold_only
        does not apply (scaffolding over a rail does not place: run
        20261121T222050257793Z left two facing hoppers out)."""
        allowed = getattr(self, "temporary_ok", None)
        if allowed is None:
            items = list(self.terrain_policy.material_allowlist)
            held = self.inventory()
            return [i for i in items if held.get(i, 0) > 0]
        if getattr(self, "scaffold_only", False) and not prop:
            # A build that carries scaffolding climbs, bridges and steps on nothing
            # else: a place scaffolding cannot make is a rule to fix, not a dirt step
            # left behind (run 20261121T180806599463Z).
            held = self.inventory().get(SCAFFOLDING, 0) > 0
            return [SCAFFOLDING] if scaffolding and held else []
        items = temporary_order(self.inventory(), self.still_needed(), allowed)
        return items if scaffolding else [i for i in items if i != SCAFFOLDING]

    def still_needed(self) -> dict[str, int]:
        """Blocks the build has yet to place, by block id (for the plain cubes that
        can be temporaries, block id and item id are the same)."""
        need: dict[str, int] = {}
        for p, state in (getattr(self, "build_cells", None) or {}).items():
            block = parse_state(state)[0]
            if parse_state(self.world(*p) or "minecraft:air")[0] != block:
                need[block] = need.get(block, 0) + 1
        return need

    def _with_temporaries(self, policy):
        """The planner pillars and bridges with the temporaries in preference order.
        The allowlist is the solid fallback; held scaffolding rides separately and the
        planner picks it per placed cell where it will stay up (docs/SCAFFOLDING.md)."""
        if (
            getattr(self, "temporary_ok", None) is None
            or not policy.allow_place
            or getattr(self, "_exact_material", False)  # pillar_to(release=...)
        ):
            return policy
        items = tuple(self.temporary_items(scaffolding=False))
        scaffold = (SCAFFOLDING,) if SCAFFOLDING in self.temporary_items() else ()
        only = getattr(self, "scaffold_only", False)
        if not only and not items and not scaffold:
            return policy
        if (items, scaffold) == (policy.material_allowlist, policy.scaffold_materials):
            return policy
        return replace(
            policy,
            # scaffold_only: no solid fallback at all, not the policy's default list
            material_allowlist=items if only else items or policy.material_allowlist,
            scaffold_materials=scaffold,
        )

    def _goto_controller(self, goal, reach, radius, probe, wall):
        controller = _GotoController(
            goal,
            self.memory,
            radius=radius,
            interact_reach=reach,
            background_planning=True,
            movement_policy=self.movement_policy,
            terrain_policy=self._planner_terrain(),
            deadline=time.monotonic() + wall,
            probe=probe,
            idle_state=self._navigation_idle,
            walk_wall=self.goto_wall_s,
            walk_partial=not getattr(self, "walk_only", False),
        )
        # What the body holds as of its last observation: a search started before the
        # controller's first decide (plan_ahead's prefetch) otherwise priced edits
        # with an empty inventory, so placing was off while the body held 31 dirt and
        # 40 scaffolding and the walk ran to 10,000 nodes (run 20261118T111231396410Z).
        controller.inventory = tuple(dict(r) for r in getattr(self, "_rows", ()))
        controller.allow_partial_gain = not getattr(self, "_pillar_only", False)
        return controller

    def plan_ahead(self, target: Pos, reach: float) -> None:
        """Start planning the walk to the stance of ``target`` from where the body
        stands, while it places the current block (optional BuildBody method).

        The first stance goto_stance would probe gets a controller whose prefetch
        is the plan from here; the next probe of that stance adopts it (decide()
        takes a prefetch that starts at the body's node) instead of uploading the
        map and searching after the placement. Any other navigation, or a body
        that moved, drops it. ponytail: one stance ahead; the block being placed
        is not in the plan's map (a route through it fails its sweep and replans)."""
        from mclab.planner_session import attach

        self._drop_ahead()
        stances = self.stances(target, reach)
        if not stances:
            return
        here = tuple(self.position())
        radius = PRECISE_RADIUS if self.precise_stance else 0.6
        controller = self._goto_controller(stances[0], None, radius, True, PROBE_WALL_S)
        try:
            attach(self.bridge, controller)
            node = controller._stance_node(here, round(here[1]))
            revision, cells = controller._planning_snapshot()
            controller.prefetch = controller._new_search(cells, node, revision)
        except Exception as error:  # a missed head start, never a failed build
            self.log.append({"op": "plan_ahead", "ok": False, "reason": repr(error)})
            self._close_controller(controller)
            return
        self._ahead = (controller, tuple(stances[0]), radius, here)

    def _advance_ahead(self) -> None:
        if self._ahead is None:
            return
        controller = self._ahead[0]
        try:
            controller.prefetch.advance(
                node_limit=controller.search_nodes,
                budget_ms=controller.search_ms,
                moving=True,
            )
        except Exception as error:
            self.log.append({"op": "plan_ahead", "ok": False, "reason": repr(error)})
            self._drop_ahead()

    def _take_ahead(self, goal, reach, radius, probe, wall):
        """The look-ahead controller if this navigation is the one it planned."""
        if self._ahead is None:
            return None
        controller, ahead_goal, ahead_radius, here = self._ahead
        if (
            reach is None
            and probe
            and tuple(goal) == ahead_goal
            and radius == ahead_radius
            and tuple(self.position()) == here
        ):
            self._ahead = None
            controller.deadline = time.monotonic() + wall
            return controller
        self._drop_ahead()
        return None

    def _drop_ahead(self) -> None:
        if self._ahead is not None:
            controller, self._ahead = self._ahead[0], None
            self._close_controller(controller)

    def _close_controller(self, controller) -> None:
        from mclab.planner_session import detach

        try:
            detach(controller)
        except Exception as error:
            self.log.append({"op": "plan_ahead", "ok": False, "reason": repr(error)})
        controller.close()

    def _navigate(
        self,
        goal: Pos,
        *,
        reach: float | None,
        radius: float,
        probe: bool = False,
        progress: dict | None = None,
    ) -> bool:
        self._mark(goal=goal)
        mid = self._hop_toward(goal) if reach is None else None
        if mid is not None:
            # A goal past the sensed terrain is an unreachable search (a plan 101
            # blocks out never completed): walk to the sensed edge, then go on.
            before = self.position()
            if math.hypot(mid[0] - before[0], mid[2] - before[2]) > SENSED_RADIUS:
                return False
            if not self._navigate(
                mid, reach=None, radius=2.0, probe=probe, progress=progress
            ):
                return False
            if math.dist(self.position(), before) < 2.0:
                return False  # the hop did not move us: do not loop
            return self._navigate(
                goal, reach=reach, radius=radius, probe=probe, progress=progress
            )
        # ponytail: the bridge keeps every event and observation and its ack waits
        # rescan all of them; after 835 blocks (run 20261110T075452397123Z, 18.6 GB)
        # the scans ate the whole 2.5 s probe and no route was ever planned again.
        # events.jsonl has the full record; nothing here reads further back.
        for kept in (
            getattr(self.bridge, "events", None),
            getattr(self.bridge, "samples", None),
        ):
            if isinstance(kept, list) and len(kept) > 20_000:
                del kept[:-2_000]
        # A probe whose route is found becomes the walk (_walk_on): one plan.
        wall = PROBE_WALL_S if probe else self.goto_wall_s
        controller = self._take_ahead(goal, reach, radius, probe, wall)
        if controller is None:
            controller = self._goto_controller(goal, reach, radius, probe, wall)
        controller.on_pass = self.on_pass
        controller.progress_state = progress
        # edges any earlier walk failed on, banned longer each repeat (native_driver.EdgeFailures)
        controller.edge_failures = self.edge_failures
        if progress and progress.get("limit_ticks"):
            # this candidate's own no-progress window, in place of the whole goto's
            controller.goal_progress_ticks = progress["limit_ticks"]
        shared_edges = getattr(self, "_goto_edges", None)
        if (
            shared_edges
        ):  # what earlier candidates of this goto learned cannot be walked
            controller.failed_edges |= shared_edges
        controller.last_partial_walk = self._partial_walks.get(tuple(goal))
        controller.dead_ends = dead = self._dead_ends.setdefault(
            tuple(goal), {"revision": None, "ends": []}
        )
        before = tuple(self.position())
        # A passive mob that still blocks a step after the pushes is killed
        # (native_driver.no_progress_decision); an axe first, else the mod's pick.
        controller.clear_mob = lambda uuid: self.attack_entity(uuid, self._axe())
        try:
            end = drive(
                self.bridge,
                controller,
                (PROBE_WALL_S if probe else 0.0) + self.goto_wall_s + 60.0,
                profile=NATIVE,
            )
        finally:
            self.bridge.state()  # heartbeat: close() joins the worker threads
            if shared_edges is not None:
                shared_edges |= controller.failed_edges
            controller.close()
            self.stray_footholds |= set(getattr(controller, "own_footholds", ()))
            self.edits_placed += getattr(controller, "edits_placed", 0)
            self.edits_broken += getattr(controller, "edits_broken", 0)
            for pos, state in getattr(controller, "own_breaks", {}).items():
                self.stray_breaks.setdefault(pos, state)
        self.observe()
        last = getattr(controller, "last_result", None)
        walked = controller.last_partial_walk
        self.boxed_in, self.shut_in = dead_end(last, before, self.position())
        if (
            walked is not None
            and math.dist(before, walked[1]) <= 1.5
            and (
                controller.mode == "unreachable"
                or (last is not None and last.status == "unreachable")
            )
        ):
            # The last partial ended here and no plan leaves: a dead end for this goal.
            revision = getattr(controller.map_view, "revision", None)
            if dead["revision"] != revision:
                dead["ends"].clear()
            dead["revision"] = revision
            dead["ends"].append(walked[1])
        if not controller.probe and last is not None and last.status == "incomplete":
            # the probe became a walk of a partial route: remember what it gained
            self._partial_walks[tuple(goal)] = (before, tuple(self.position()))
        why = end.get("data", {}).get("reason") if end else None
        ok = end.get("type") == "navigation_stopped" and why == "arrived"
        row = {
            "goto": list(goal),
            "reach": reach,
            "ok": ok,
            "why": why,
            "policy_id": self.policy_id(),
        }
        if controller.probe:
            row["probe"] = True  # ops: ended parked on a verdict, never walked
        self.log.append(row)
        return ok

    def _in_water(self, node: Pos) -> bool:
        cell = self._cells_now().get(node)
        return bool(cell) and cell.get("fluid", "minecraft:empty") not in (
            "minecraft:empty",
            "minecraft:lava",
        )

    def _hop_toward(self, goal: Pos) -> Pos | None:
        """A known standable cell on the line to a goal beyond SENSED_RADIUS, else None."""
        here = self.position()
        dist = math.hypot(goal[0] - here[0], goal[2] - here[2])
        if dist <= SENSED_RADIUS:
            return None
        cells = self._cells_now()
        for share in (1.0, 0.75, 0.5):
            k = (SENSED_RADIUS - 1.0) * share / dist
            x = here[0] + round((goal[0] - here[0]) * k)
            z = here[2] + round((goal[2] - here[2]) * k)
            hop = math.hypot(x - here[0], z - here[2])
            if not (1.0 <= hop <= SENSED_RADIUS):
                continue
            if math.hypot(goal[0] - x, goal[2] - z) >= dist:
                continue
            y = self._stand_y(x, here[1], z)
            if standable(cells, (x, y, z)):
                return (x, y, z)
        return None

    def _stand_y(self, x: int, y: int, z: int) -> int:
        cells = self._cells_now()

        def solid(cy: int) -> bool:
            cell = cells.get((x, cy, z))
            return bool(cell and cell.get("collision_boxes"))

        for dy in sorted(range(-STAND_SEARCH, STAND_SEARCH + 1), key=abs):
            if not standable(cells, (x, y + dy, z)):
                continue
            # More than a step up is a hill only if the column below is solid; an
            # air gap means an overhang. Run 20261117T011432772067Z: a site-prep
            # walk to the ground under leaves snapped 3 up onto the canopy, and two
            # plans burnt their 10000 nodes on a goal the body cannot climb to.
            # The same for one step up: the block stepped onto must rest on
            # something. Run 20261117T033515924527Z: a walk to the ground beside a
            # pond snapped onto a lone leaf over the water, 2 plans x 10000 nodes.
            if dy > 0 and not all(solid(cy) for cy in range(y - (dy == 1), y + dy)):
                continue
            return y + dy
        return y

    @_fresh_idle
    def walk_to(self, pos: Pos, radius: float = 1.5) -> bool:
        x, y, z = pos
        goal = (x, self._stand_y(x, y, z), z)
        # With placing off, ask the planner first (as goto_stance does): an
        # unroutable walk stood still for the whole 10 s wall, 11 times in the
        # site prep of run 20261111T021416142202Z.
        return self._navigate(
            goal, reach=None, radius=radius, probe=getattr(self, "walk_only", False)
        )

    @_fresh_idle
    def walk_to_drop(self, pos: Pos, radius: float = 0.8) -> bool | None:
        """Pickups must not turn a nearby falling item into a canopy-climbing goal.
        None: the item is still above its ground (falling); try again after a wait."""
        x, y, z = pos
        standing_y = self._stand_y(x, y, z)
        refused = None
        if y - standing_y > 2:
            refused = "falling"
        elif standing_y - self.position()[1] > DROP_RISE or standing_y - y > 2:
            refused = "out_of_reach"
        if refused is not None:
            if (record := getattr(self.bridge, "record", None)) is not None:
                record(
                    "gather_note",
                    {
                        "gather": "drop_" + refused,
                        "item_cell": list(pos),
                        "stand_y": standing_y,
                        "feet": self.position()[1],
                    },
                )
            return None if refused == "falling" else False
        return self.walk_to((x, standing_y, z), radius)

    def _walk_beside_drop(self, pos: Pos, at) -> bool:
        """An item resting over an empty cell lies on a neighbour's edge: stand beside it
        at its height (pickup reaches a block past the body's box)."""
        x, y, z = pos
        here = self.position()
        spots = []
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx or dz:
                    sy = self._stand_y(x + dx, y, z + dz)
                    if abs(sy - y) <= 1 and sy - here[1] <= DROP_RISE:
                        spots.append((x + dx, sy, z + dz))

        def gap(c):
            return math.hypot(c[0] + 0.5 - at[0], c[2] + 0.5 - at[2])

        for spot in sorted(spots, key=gap):
            if self.walk_to(spot, 0.8):
                return True
        return False

    def collect_drops(self, near: Collection[Pos], radius: float = DROP_RADIUS) -> None:
        """Wait for what fell (a scaffolding tower drops block by block), then walk onto
        the dropped items within ``radius`` of the cells ``near``."""
        centres = [(x + 0.5, y + 0.5, z + 0.5) for x, y, z in near]
        skipped: set = set()
        # Items are remembered once seen, across calls: from the bottom of a pit a stack on its
        # steps is out of sight (scaffold course 20261118T013316689139Z left 3 there), and from
        # its steps the pit floor is (20261118T014026041814Z left 4). One no longer seen where
        # the body stands was picked up or is gone.
        known: dict = getattr(self, "_known_drops", None)
        if known is None:
            known = self._known_drops = {}

        def sight() -> dict:
            found = {
                e["uuid"]: e
                for e in self.entities()
                if e.get("item")
                and e.get("uuid")
                and any(math.dist(e["position"], c) <= radius for c in centres)
            }
            known.update(found)
            return found

        # Wait only for items that are there: none in range, none to wait for; otherwise
        # look every few ticks and go as soon as they rest (or were picked up). A blind
        # 40-tick wait per break was 62 s of a house build (run 20261119T133618892664Z).
        seen = sight()
        waited = 0
        while seen and waited < DROP_WAIT_TICKS:
            before = {u: e["position"][1] for u, e in seen.items()}
            self.wait_ticks(DROP_POLL_TICKS)
            waited += DROP_POLL_TICKS
            seen = sight()
            if all(
                abs(before.get(u, math.inf) - e["position"][1]) < 0.01
                for u, e in seen.items()
            ):
                break  # every item in view rests (an empty view: all picked up)
        if waited and (record := getattr(self.bridge, "record", None)) is not None:
            # The wait leaves no event of its own; idle_gaps.py counts this note as work.
            record("gather_note", {"gather": "drop_wait", "ticks": waited})
        last_y: dict = {}
        view = seen

        def resting(e) -> bool:
            before = last_y.get(e["uuid"])
            return before is not None and abs(before - e["position"][1]) < 0.01

        for _ in range(MAX_PICKUPS):
            here = self.position()
            for u, e in known.items():
                last_y[u] = e["position"][1]
            # the first look is the last one the wait took
            seen, view = (view if view is not None else sight()), None
            for uuid in [u for u, e in known.items() if u not in seen]:
                if math.dist(known[uuid]["position"], here) <= 2.0:
                    del known[uuid]
            drops = [
                e
                for u, e in known.items()
                if u not in skipped
                and e["position"][1] - here[1] <= DROP_RISE
                and any(math.dist(e["position"], c) <= radius for c in centres)
            ]
            if not drops:
                return
            e = min(drops, key=lambda e: math.dist(e["position"], here))
            x, y, z = (math.floor(v) for v in e["position"])
            reached = self.walk_to_drop((x, y, z), 0.8)
            if reached is None and e["uuid"] in seen and resting(e):
                # Not falling: it rests on the edge of a neighbour's block, over an empty cell
                # (scaffold course 20261118T015734978753Z waited on one at a pit rim all leg).
                reached = self._walk_beside_drop((x, y, z), e["position"])
            if not reached and e["uuid"] not in seen:
                # Remembered, not in view, and not walkable to as remembered: it merged into
                # another stack or was last seen mid-fall. Waiting on it spent every pickup of
                # each round (scaffold course 20261118T014508840305Z: 47 of 64 collected).
                del known[e["uuid"]]
            elif reached is None:  # still falling: look again shortly
                self.wait_ticks(5)
            elif not reached:
                skipped.add(e["uuid"])

    def find_all(
        self, block: str, within: list[tuple[Pos, float]] | None = None
    ) -> list[Pos]:
        """Delivered cells holding ``block``, buried ones included."""
        return self._scan(block, within)

    @contextmanager
    def no_opening_doors(self):
        """Finalize door states without navigation reopening previously fixed doors."""
        saved = self.terrain_policy
        self.terrain_policy = replace(saved, use_doors=False)
        try:
            yield
        finally:
            self.terrain_policy = saved

    @contextmanager
    def walk_wall(self, seconds: float):
        """Give up any walk after ``seconds`` (never longer than the usual wall), with
        the movement and terrain policies untouched: a fight's walk toward its target
        ends soon enough that the next look can heal or strike back."""
        saved = self.goto_wall_s
        self.goto_wall_s = min(saved, seconds)
        try:
            yield
        finally:
            self.goto_wall_s = saved

    @contextmanager
    def no_placing(self, wall: float = 10.0, max_drop: int | None = None):
        """Move without placing blocks, and give up a walk after ``wall`` seconds:
        an unreachable goal otherwise replans for 90 s (42 s gaps with 18 plans,
        run 20261110T020332013460Z)."""
        saved, saved_wall = self.terrain_policy, self.goto_wall_s
        saved_move = self.movement_policy
        self.movement_policy = replace(
            saved_move,
            max_drop=saved_move.max_drop if max_drop is None else max_drop,
        )
        self.terrain_policy = replace(
            saved,
            allow_place=False,
            allow_bridge=False,
            allow_pillar=False,
            place_budget=0,
            # no planned dig-down either: a `downward` step dug a 2-deep hole beside a
            # tree on the way to its stance (run 20261110T021445878238Z)
            baritone_moves=False,
        )
        self.goto_wall_s = min(saved_wall, wall)
        saved_only, self.walk_only = getattr(self, "walk_only", False), True
        try:
            yield
        finally:
            self.walk_only = saved_only
            self.terrain_policy, self.goto_wall_s = saved, saved_wall
            self.movement_policy = saved_move

    @contextmanager
    def walking_with_edits(self, wall: float = 20.0, max_drop: int | None = None):
        """Walk to a goal the way a player does: pillar, bridge and place dirt or
        cobblestone to get there, never dig. Protected cells (the house) stay
        untouched through ``protected_positions``; what lands inside the site that
        is not a fill cell is cut by the next site round. ``no_placing`` made a body
        2 below the surface unable to leave a hollow (run 20261116T220215760808Z).
        Non-protected blocks may be broken too, so a body with nothing to place can
        staircase out of a pit; the planner records them (``stray_breaks``) and the
        build puts them back. ``walk_only`` is the stance filter and probe cache of
        goto_stance, not a permission: it stays on here."""
        saved, saved_wall = self.terrain_policy, self.goto_wall_s
        saved_move = self.movement_policy
        self.movement_policy = replace(
            saved_move,
            max_drop=saved_move.max_drop if max_drop is None else max_drop,
        )
        self.terrain_policy = replace(
            saved,
            allow_place=True,
            allow_bridge=True,
            allow_pillar=True,
            allow_break=True,
            break_budget=max(saved.break_budget, 64),
            place_budget=max(saved.place_budget, 64),
            material_allowlist=("minecraft:dirt", "minecraft:cobblestone"),
        )
        self.goto_wall_s = min(saved_wall, wall)
        saved_only, self.walk_only = getattr(self, "walk_only", False), True
        try:
            yield
        finally:
            self.walk_only = saved_only
            self.terrain_policy, self.goto_wall_s = saved, saved_wall
            self.movement_policy = saved_move

    def _place_supply(self) -> int:
        policy = self.terrain_policy
        if not (policy.allow_pillar or policy.allow_bridge or policy.allow_place):
            return 0
        held = sum(self._inv.get(item, 0) for item in policy.material_allowlist)
        return max(0, held - policy.reserve_blocks)

    @_fresh_idle
    def pillar_to(
        self, feet: Pos, item: str, n: int, release: Pos | None = None
    ) -> bool:
        """Stand at ``feet`` straight above the body by pillaring with ``item`` only
        (at most ``n`` blocks, no digging or bridging), even inside ``no_placing``: the
        build's tower of dirt is a solid column nothing can walk or climb onto.

        ``release``: a protected cell (the template's own) the pillar may fill, with
        exactly ``item`` (no scaffolding, no temporary in its place): the build's block
        put under a body the house closed round (build_runner ``_climb_out``)."""
        saved = self.terrain_policy
        saved_only = getattr(self, "walk_only", False)
        saved_pillar = getattr(self, "_pillar_only", False)
        saved_exact = getattr(self, "_exact_material", False)
        policy = replace(
            saved,
            allow_place=True,
            allow_pillar=True,
            allow_break=False,
            allow_bridge=False,
            place_budget=n,
            material_allowlist=(item,),
        )
        if release is not None:
            policy = replace(
                policy,
                protected_positions=tuple(
                    p for p in policy.protected_positions if tuple(p) != tuple(release)
                ),
                scaffold_materials=(),
            )
            self._exact_material = True
        self.terrain_policy = policy
        self.walk_only = True
        self._pillar_only = True
        try:
            # probe=True with allow_partial_gain=False ensures a capped pillar search
            # never wanders away from the column around the house (run
            # 20261120T175636584118Z: 205 near cap hits from pillar_to).
            return self._navigate(tuple(feet), reach=None, radius=0.6, probe=True)
        finally:
            self._exact_material = saved_exact
            self._pillar_only = saved_pillar
            self.walk_only = saved_only
            self.terrain_policy = saved

    @_fresh_idle
    def dig_to(self, feet: Pos) -> bool:
        """Stand at ``feet``, letting the planner dig there. False when digging is off."""
        if not self.terrain_policy.allow_break:
            return False
        here = self.position()
        target = tuple(feet)
        if (
            target[0] == here[0]
            and target[2] == here[2]
            and target[1] > here[1]
            and not standable(self._cells_now(), target)
            and self._place_supply() < target[1] - here[1]
        ):
            return False  # pillaring straight up without enough blocks cannot succeed
        return self._navigate(target, reach=None, radius=0.6)

    def stances(self, target: Pos, reach: float, avoid: Collection[Pos] = ()):
        """Known standable cells from which ``target`` is reached, nearest first."""
        cells, here = self._cells_now(), self.position()
        tx, ty, tz = target
        r = math.ceil(reach)
        placing = self._placing(target)
        col_low = ty
        if (
            placing
            and self.placing_scaffold
            and (cells.get((tx, ty - 1, tz)) or {}).get("block")
            == "minecraft:scaffolding"
        ):
            while (cells.get((tx, col_low - 1, tz)) or {}).get(
                "block"
            ) == "minecraft:scaffolding":
                col_low -= 1
        min_dy = min(-r, col_low - ty - r)
        found = [
            node
            for node in (
                (tx + dx, ty + dy, tz + dz)
                for dx in range(-r, r + 1)
                for dz in range(-r, r + 1)
                for dy in range(min_dy, 2)
            )
            if node not in avoid
            # never inside the target: the feet cell, or the one below (head in it);
            # live run: scaffolding has no collision, so the body stood in the tower
            and node not in ((tx, ty, tz), (tx, ty - 1, tz))
            and (standable(cells, node) or _tower_top(cells, node))
            and (
                self._places_from(cells, stance_eye(node), node, target, reach)
                if placing
                else interacts(cells, node, target, reach)
            )
        ]
        # ponytail: stances the body can walk to come first, ranked by real BFS
        # walking distance (not straight-line crow-distance, which sent the bot out
        # the door and around walls: 43 detours / 413 s in run 20261120T175636584118Z).
        walk = self._walkable(cells, here) if found else {}

        def _walk_cost(n: Pos) -> float:
            if isinstance(walk, dict) and n in walk:
                return float(walk[n])
            return math.dist(n, here)

        # Among walkable stances, balance walking distance against upcoming sweep
        # cells covered (``stance_cover``): each extra covered cell saves ~3 steps of
        # walking, so it prefers a better spot along the same wall without walking 24
        # blocks around the house for +1 cover.
        ranked = sorted(
            found,
            key=lambda n: (
                n not in walk,
                not _clean_feet(cells, n),
                _walk_cost(n) - 3.0 * self._stance_covers(cells, n, reach),
                _walk_cost(n),
                math.dist(n, here),
                n,
            ),
        )
        if not placing:
            return ranked
        # Within the head of the list, a stance that still places from an eye off
        # its centre goes before one that places only from the exact centre.
        head = ranked[:SOUND_RANKED]
        head.sort(key=lambda n: not self._sound(cells, n, target, reach))
        return head + ranked[SOUND_RANKED:]

    def bridge_stances(self, target: Pos, reach: float, avoid: Collection[Pos] = ()):
        """Cells in the air from which ``target`` would be reached if a scaffolding
        block were put under them (a bridge off a standing tower,
        build_plan.bridge_cells): the cell and the one above it empty, the one below
        takes a block. Cells that are stances already (``stances``) are not listed.
        Nearest the body first."""
        cells, here = self._cells_now(), self.position()
        tx, ty, tz = target
        r = math.ceil(reach)
        placing = self._placing(target)

        def empty(p: Pos) -> bool:
            return (cells.get(p) or {}).get("block") in AIR_BLOCKS

        found = []
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                for dy in range(-r, 2):
                    node = (tx + dx, ty + dy, tz + dz)
                    floor = (node[0], node[1] - 1, node[2])
                    if (
                        node in avoid
                        or node in (target, (tx, ty - 1, tz))
                        or floor == target
                        or not empty(node)
                        or not empty((node[0], node[1] + 1, node[2]))
                        or not place_sight.takes_block(cells.get(floor))
                    ):
                        continue
                    view = ChainMap({floor: dict(SCAFFOLD_CELL)}, cells)
                    if (
                        self._places_from(view, stance_eye(node), node, target, reach)
                        if placing
                        else interacts(view, node, target, reach)
                    ):
                        found.append(node)
        return sorted(found, key=lambda n: (math.dist(n, here), n))

    def can_reach_from_tower(
        self,
        target: Pos,
        reach: float,
        foot: Pos,
        tower: list[Pos],
        *,
        scaffolding: bool = True,
        avoid: Collection[Pos] = (),
    ) -> int:
        """Can the body standing at ``foot`` on top of ``tower`` place/interact with
        ``target``? Returns 2 if sound across offset eyes, 1 if centre-only, 0 if not."""
        tx, ty, tz = target
        if foot in avoid or foot in (target, (tx, ty - 1, tz)):
            return 0
        proto = SCAFFOLD_CELL if scaffolding else _SOLID_CELL
        view = ChainMap({c: dict(proto) for c in tower}, self._cells_now())
        if self._placing(target):
            if not self._places_from(view, stance_eye(foot), foot, target, reach):
                return 0
            return 2 if self._sound(view, foot, target, reach) else 1
        return 2 if interacts(view, foot, target, reach) else 0

    def can_place_with_prop_here(
        self,
        prop_pos: Pos,
        prop_item: str,
        target: Pos,
        target_state: str,
        reach: float,
    ) -> bool:
        """From where the body stands now, can it place ``prop_item`` at ``prop_pos``
        and then click that prop to place ``target_state`` at ``target``?"""
        if self._overlaps(prop_pos) or self._overlaps(target):
            return False
        cells = self._cells_now()
        eye = place_sight.eye_of(self._obs["body"]["position"])
        here = self.position()
        if not places_from(cells, eye, here, prop_pos, reach, state=prop_item):
            return False
        view = ChainMap({prop_pos: dict(_SOLID_CELL)}, cells)
        return places_from(
            view, eye, here, target, reach, required=prop_pos, state=target_state
        )

    def prop_stances(
        self,
        prop_pos: Pos,
        prop_item: str,
        target: Pos,
        target_state: str,
        reach: float,
        avoid: Collection[Pos] = (),
    ) -> list[Pos]:
        """Known standable cells from which the body can place ``prop_item`` at
        ``prop_pos`` AND click it to place ``target_state`` at ``target``."""
        cells, here = self._cells_now(), self.position()
        tx, ty, tz = target
        px, py, pz = prop_pos
        r = math.ceil(reach)
        excluded = set(avoid) | {
            target,
            (tx, ty - 1, tz),
            prop_pos,
            (px, py - 1, pz),
        }
        view = ChainMap({prop_pos: dict(_SOLID_CELL)}, cells)

        def both_from(eye, node: Pos) -> bool:
            return places_from(
                cells, eye, node, prop_pos, reach, state=prop_item
            ) and places_from(
                view, eye, node, target, reach, required=prop_pos, state=target_state
            )

        found = [
            node
            for node in (
                (tx + dx, ty + dy, tz + dz)
                for dx in range(-r, r + 1)
                for dz in range(-r, r + 1)
                for dy in range(-r, 2)
            )
            if node not in excluded
            and (standable(cells, node) or _tower_top(cells, node))
            and both_from(stance_eye(node), node)
        ]
        walk = self._walkable(cells, here) if found else {}

        def _walk_cost(n: Pos) -> float:
            if isinstance(walk, dict) and n in walk:
                return float(walk[n])
            return math.dist(n, here)

        ranked = sorted(
            found,
            key=lambda n: (
                n not in walk,
                not _clean_feet(cells, n),
                _walk_cost(n),
                math.dist(n, here),
                n,
            ),
        )

        def sound_both(node: Pos) -> bool:
            x, y, z = stance_eye(node)
            return all(
                both_from((x + dx, y, z + dz), node)
                for dx, dz in (
                    (SOUND_OFFSET, 0),
                    (-SOUND_OFFSET, 0),
                    (0, SOUND_OFFSET),
                    (0, -SOUND_OFFSET),
                )
            )

        head = ranked[:SOUND_RANKED]
        head.sort(key=lambda n: not sound_both(n))
        return head + ranked[SOUND_RANKED:]

    @_fresh_idle
    def goto_prop_stance(
        self,
        prop_pos: Pos,
        prop_item: str,
        target: Pos,
        target_state: str,
        reach: float,
        avoid: Collection[Pos] = (),
    ) -> bool:
        """Walk to a single stance that can place ``prop_item`` at ``prop_pos`` and
        then click it to place ``target_state`` at ``target``."""
        self._goto_edges = set()
        try:
            if not self._obs:
                self.observe()
            here = self.position()
            avoid_set = set(avoid)
            if here not in avoid_set and self.can_place_with_prop_here(
                prop_pos, prop_item, target, target_state, reach
            ):
                return True
            stances = self.prop_stances(
                prop_pos, prop_item, target, target_state, reach, avoid_set
            )
            cells = self._cells_now()
            probe_budget_s = None
            if getattr(self, "walk_only", False):
                walk = self._walkable(cells, here)
                matched = [
                    n for n in stances if n in walk or _scaffold_stance(cells, n)
                ]
                if matched:
                    stances = matched[:WALK_ONLY_CANDIDATES]
                else:
                    stances = stances[:MAX_CANDIDATES]
                    probe_budget_s = WALK_ONLY_CANDIDATES * PROBE_WALL_S
            radius = PRECISE_RADIUS if self.precise_stance else 0.6
            probe_started = time.monotonic()
            for node in stances[:MAX_CANDIDATES]:
                if (
                    probe_budget_s is not None
                    and time.monotonic() - probe_started >= probe_budget_s
                ):
                    break
                progress = {
                    "distance": math.inf,
                    "tick": None,
                    "limit_ticks": CANDIDATE_PROGRESS_TICKS,
                }
                if self._navigate(
                    node, reach=None, radius=radius, probe=True, progress=progress
                ):
                    if self.can_place_with_prop_here(
                        prop_pos, prop_item, target, target_state, reach
                    ):
                        return True
                if self.boxed_in:
                    return False
            return False
        finally:
            self._goto_edges = None

    def _places_from(self, cells, eye, node: Pos, target: Pos, reach: float) -> bool:
        """``places_from`` with this placement's support, state and scaffold rule."""
        return places_from(
            cells,
            eye,
            node,
            target,
            reach,
            self.required_support,
            scaffold=self.placing_scaffold,
            state=self.placing_state,
        )

    def _sound(self, cells, node: Pos, target: Pos, reach: float) -> bool:
        """Does ``target`` place from every eye SOUND_OFFSET off ``node``'s centre?"""
        x, y, z = stance_eye(node)
        return all(
            self._places_from(cells, (x + dx, y, z + dz), node, target, reach)
            for dx, dz in (
                (SOUND_OFFSET, 0),
                (-SOUND_OFFSET, 0),
                (0, SOUND_OFFSET),
                (0, -SOUND_OFFSET),
            )
        )

    def _stance_covers(self, cells, node: Pos, reach: float) -> int:
        """How many of ``stance_cover`` (cell -> required support and state) a body
        standing on ``node`` places. A cell the body would stand in is not one."""
        if not self.stance_cover:
            return 0
        eye, body_cells = stance_eye(node), (node, (node[0], node[1] + 1, node[2]))
        return sum(
            pos not in body_cells
            and places_from(
                cells,
                eye,
                node,
                pos,
                reach,
                required,
                scaffold=self.placing_scaffold,
                state=state,
            )
            for pos, (required, state) in self.stance_cover.items()
        )

    def _walkable(self, cells, here: Pos) -> dict[Pos, int]:
        """Cells reachable from ``here`` with no terrain edits, mapped to their
        walking cost in steps (Dijkstra/BFS up to WALK_CELLS): 4 horizontal moves
        (+1 step up, ``max_drop`` down), wooden doors/gates, and scaffolding climbs/
        descents. Replaces the old DFS set so stances and sweep targets rank by
        actual walking distance instead of straight-line crow-distance across walls."""
        rev = getattr(self.memory, "revision", None)
        key = (here, self._tick() // 40, rev, len(self._overlay))
        if getattr(self, "_walk_memo", (None,))[0] == key:
            return self._walk_memo[1]
        drop = self.movement_policy.max_drop
        use_doors = bool(
            getattr(getattr(self, "terrain_policy", None), "use_doors", True)
        )

        def passable(node: Pos) -> bool:
            if standable(cells, node) or _tower_top(cells, node):
                return True
            if not use_doors:
                return False
            c = cells.get(node)
            if not c:
                return False
            blk = c.get("block") or ""
            if not (
                (blk.endswith("_door") and not blk.endswith("iron_door"))
                or blk.endswith("_fence_gate")
            ):
                return False
            below = cells.get((node[0], node[1] - 1, node[2]))
            return bool(
                below
                and (below.get("collision_boxes") or below.get("block") == SCAFFOLDING)
            )

        dist: dict[Pos, int] = {here: 0}
        heap: list[tuple[int, Pos]] = [(0, here)]
        while heap and len(dist) < WALK_CELLS:
            cost, (x, y, z) = heapq.heappop(heap)
            if cost > dist.get((x, y, z), cost):
                continue
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for dy in range(1, -drop - 1, -1):
                    node = (x + dx, y + dy, z + dz)
                    if passable(node):
                        nc = cost + (2 if dy > 0 else 1)
                        if nc < dist.get(node, 10**9):
                            dist[node] = nc
                            heapq.heappush(heap, (nc, node))
                        break
            cur_blk = (cells.get((x, y, z)) or {}).get("block")
            below_blk = (cells.get((x, y - 1, z)) or {}).get("block")
            if cur_blk == SCAFFOLDING:
                for dy, step_cost in ((1, 3), (-1, 2)):
                    node = (x, y + dy, z)
                    if passable(node) and cost + step_cost < dist.get(node, 10**9):
                        dist[node] = cost + step_cost
                        heapq.heappush(heap, (cost + step_cost, node))
            elif below_blk == SCAFFOLDING:
                node = (x, y - 1, z)
                if passable(node) and cost + 2 < dist.get(node, 10**9):
                    dist[node] = cost + 2
                    heapq.heappush(heap, (cost + 2, node))
        self._walk_memo = (key, dist)
        return dist

    def walk_distance(self, pos: Pos) -> int | None:
        """Minimum cached walking cost from the body to a standable cell within
        work reach of ``pos`` (None if unreachable on foot)."""
        walk = self._walkable(self._cells_now(), self.position())
        if not isinstance(walk, dict):
            return None
        px, py, pz = pos
        best: int | None = None
        for dx in range(-3, 4):
            for dz in range(-3, 4):
                if dx * dx + dz * dz > 9:
                    continue
                for dy in range(-3, 2):
                    c = walk.get((px + dx, py + dy, pz + dz))
                    if c is not None and (best is None or c < best):
                        best = c
        return best

    def _cells_now(self):
        """The sensed map plus this body's own recent edits (the overlay): a wall
        placed a moment ago is not sensed yet, and the sight check must not look
        through it (run 20261102T221946087817Z)."""
        tick = self._tick()
        recent = {}
        for pos, (state, at, boxes) in self._overlay.items():
            if tick - at > OVERLAY_TICKS:
                continue
            block = parse_state(state)[0]
            if boxes is None:
                # Old body or fake: the sensed cell already holding this block has the
                # real shape; else a full cube (a placed trapdoor or torch is not one).
                sensed = self.memory.cells.get(pos)
                if sensed is not None and sensed.get("block") == block:
                    boxes = sensed.get("collision_boxes")
            if boxes is None:
                boxes = [] if block in AIR_BLOCKS else [[0, 0, 0, 1, 1, 1]]
            recent[pos] = {
                "block": block,
                "fluid": "minecraft:empty",
                "collision_boxes": boxes,
            }
        # ponytail: a lookup view, never a copy. Copying the whole sensed map here was
        # 79% of build time, ~13 s per placed block (py-spy, run 20261110T024448883505Z)
        return ChainMap(recent, self.memory.cells) if recent else self.memory.cells

    def _placing(self, target: Pos) -> bool:
        """Building into a cell that takes a block (air, fluid, unknown or grass): the
        stance must show the server a support face. A flower is broken first, and its
        stance is a break's: while the flower counted as a cell to place into, it hid
        every face and site prep found no stance to break 16 tulips (run
        20261120T075123420431Z, stopped "blocked")."""
        return self.precise_stance and place_sight.takes_block(
            self._cells_now().get(target)
        )

    def _worth_searching(self, target: Pos) -> bool:
        """The interaction-goal search can only help where the map is still unsensed.
        A near target with no known stance has none: the search floods the known
        graph (8,192 expansions, route of one cell) and ends in "stalled". Live run
        20261101T203526520132Z: 60 such searches from a ravine floor."""
        return math.dist(self.position(), target) > SENSED_RADIUS

    def _overlaps(self, target: Pos) -> bool:
        """Does the body's box (0.6 wide, 1.8 tall) intersect the cell `target`?"""
        x, y, z = self._obs["body"]["position"]
        tx, ty, tz = target
        half = BODY_HALF_WIDTH
        return (
            tx - half < x < tx + 1 + half
            and tz - half < z < tz + 1 + half
            and ty - BODY_HEIGHT < y < ty + 1
        )

    def sees_placement(
        self, target: Pos, eye=None, reach: float = place_sight.PLACE_REACH
    ) -> bool:
        """Would the server's place_block find a support face of ``target`` to click
        from ``eye`` (default: the body's real eye, as the server uses)? See
        place_sight; the server's answer stays final."""
        if eye is None:
            eye = place_sight.eye_of(self._obs["body"]["position"])
        return place_sight.clickable_from(self._cells_now(), eye, target, reach)

    def can_place_here(
        self, target: Pos, reach: float, required=None, state: str | None = None
    ) -> bool:
        """Would place_block put ``state`` (None: any block) into ``target`` from where
        the body stands, its real eye as the server uses (``places_from``)? Checked
        before an optimistic direct action."""
        if self._overlaps(target):
            return False
        return self._places_from_here(target, reach, required, state)

    def _places_from_here(
        self, target: Pos, reach: float, required, state: str | None = None
    ) -> bool:
        cells = self._cells_now()
        return places_from(
            cells,
            place_sight.eye_of(self._obs["body"]["position"]),
            self.position(),
            target,
            reach,
            required,
            scaffold=self.placing_scaffold,
            state=state,
        )

    @_fresh_idle
    def goto_stance(
        self, target_pos: Pos, reach: float, avoid: Collection[Pos] = ()
    ) -> bool:
        # Route edges that failed under one candidate stay failed for the next one.
        self._goto_edges = set()
        try:
            return self._goto_stance(target_pos, reach, avoid)
        finally:
            self._goto_edges = None

    def _goto_stance(
        self,
        target_pos: Pos,
        reach: float,
        avoid: Collection[Pos] = (),
        recheck: bool = True,
    ) -> bool:
        target, avoid = tuple(target_pos), set(avoid)
        self._mark(target=target)
        if not self._obs:
            self.observe()
        here = self.position()
        cells = self._cells_now()
        # A precise stance (building) also needs the body out of the target cell: a
        # block cannot be placed into a cell the body overlaps, and the 0.6 arrival
        # radius lets it stand 0.4 into a neighbour (live run: 21 state_unreachable).
        crowded = self.precise_stance and self._overlaps(target)
        placing = self._placing(target)
        reach_ok = (
            self._places_from_here(
                target, reach, self.required_support, self.placing_state
            )
            if placing
            else interacts(cells, here, target, reach)
        )
        if here not in avoid and reach_ok and not crowded:
            return True
        # Known stances first, walked with the body planner; the interaction-goal
        # search only for a target whose stance is not yet on the map.
        stances = self.stances(target, reach, avoid)
        probe_budget_s = None
        if getattr(self, "walk_only", False):
            # ponytail: with planner placing off an unwalkable stance cannot be
            # reached, and each probe of one floods the search to its cap (2043 of
            # 3350 plans in run 20261110T150634125260Z). When the walk flood finds
            # nothing (e.g. across a drop/trapdoor), try up to MAX_CANDIDATES fast
            # probes bounded by the WALK_ONLY_CANDIDATES * PROBE_WALL_S wall budget.
            # A scaffolding tower's top is climbed, not walked: the flood does not
            # see it, so the tower built for a target was never the stance tried and
            # the body stopped inside the column (run 20261110T223248667856Z).
            walk = self._walkable(cells, here)
            matched = [n for n in stances if n in walk or _scaffold_stance(cells, n)]
            if matched:
                stances = matched[:WALK_ONLY_CANDIDATES]
            else:
                stances = stances[:MAX_CANDIDATES]
                probe_budget_s = WALK_ONLY_CANDIDATES * PROBE_WALL_S
        # A stance that already failed for this target is not walked to again for a
        # while, wherever the body now stands (run 20261119T133618892664Z: 30 s calls
        # re-testing the same yard stances).
        now = time.monotonic()
        untried = [
            n for n in stances if self._failed_stances.get((target, n), 0) <= now
        ]
        stances = untried[:MAX_CANDIDATES]
        radius = PRECISE_RADIUS if self.precise_stance else 0.6
        hopped = False
        probe_started = time.monotonic()
        for node in stances:
            # Each candidate has its own no-progress window (a candidate walk that keeps
            # closing in is never cut, one that does not gives way to the next). A shared
            # clock let the first dead-end walk spend the whole goto (31 s x 2 in the run).
            progress = {
                "distance": math.inf,
                "tick": None,
                "limit_ticks": CANDIDATE_PROGRESS_TICKS,
            }
            if (
                probe_budget_s is not None
                and time.monotonic() - probe_started >= probe_budget_s
            ):
                break
            # Ask the planner before walking (roadmap item 1): a candidate across
            # known water is unroutable, and testing that by walking it costs the
            # 200-tick StuckGuard timer - 12 of those ended a job at 0 tasks
            # (20261106T170919632775Z; the sand stall of 20261106T185239143471Z).
            # A found route is walked by the same controller, not planned twice
            # (two plans per stance in run 20261111T135214868983Z), and so is the
            # partial route of a search that ran out of nodes (_probe_verdict).
            # Reusing a failed probe for the same source and stance avoids asking
            # the planner for identical inaccessible roof stances for every block.
            distant = math.hypot(node[0] - here[0], node[2] - here[2]) > SENSED_RADIUS
            if hopped and distant and self.position() == here:
                continue
            key = (here, node)
            if (
                getattr(self, "walk_only", False)
                and self._failed_stance_probes.get(key, 0) > time.monotonic()
            ):
                continue
            if distant and self._hop_toward(node) is not None:
                hopped = True
            if self._navigate(
                node, reach=None, radius=radius, probe=True, progress=progress
            ):
                if not (placing and recheck) or self._places_from_here(
                    target, reach, self.required_support, self.placing_state
                ):
                    return True
                return self._chosen_again(target, reach, avoid, node)
            # Shut in, the region failed, not the stance: once the body is out it is
            # the one to try (issue #11, run 20261122T185137031693Z: the ground
            # stances barred from the pocket, the body went back up into it).
            if not self.shut_in:
                self._failed_stances[(target, node)] = time.monotonic() + STANCE_RETRY_S
            if self.boxed_in:
                return False
            if getattr(self, "walk_only", False) and self.position() == here:
                self._failed_stance_probes[key] = time.monotonic() + 10.0
        if (
            not avoid
            and (not stances or self.position() == here)
            and not (hopped and not getattr(self, "walk_only", False))
            and self._worth_searching(target)
        ):
            # All known stances probed without moving the body: from a dug cave the
            # way back needs terrain edits, which a walk-only probe can never plan
            # (iron run 20261112T233253015689Z: 15 unreachable probes, then three
            # furnace crafts died on no_stance and the job with them). The
            # interaction search may edit, so it can dig its own way out.
            search = {"distance": math.inf, "tick": None}  # the whole-goto bound
            if self._navigate(target, reach=reach, radius=2, progress=search):
                if self.position() not in avoid:
                    return True
        return False

    def _chosen_again(self, target: Pos, reach: float, avoid: set, node: Pos) -> bool:
        """Arrived at ``node``, the eye there shows no face that gives the state: a
        block placed while passing hid it (run 20261120T083111010944Z: a log placed
        on the way beside [-7, 66, 67]), or the arrival is off a narrow view's
        centre. Choose once more on the map as it is now; with nothing better, the
        server decides from here, as it did before this check."""
        self._failed_stances[(target, node)] = time.monotonic() + STANCE_RETRY_S
        if self._goto_stance(target, reach, avoid | {node}, recheck=False):
            return True
        return self.position() == node

    @_fresh_idle
    def escape_hole(self, target: Pos) -> bool:
        """The body is in a pit no stance for ``target`` can be walked out of. Climb:
        walk to a standable cell up to 4 blocks higher (the planner pillars with dirt
        to get there). Failing that, dig: break a solid wall block beside the body at
        head height. True if the body moved or a block was broken."""
        here = self.position()
        cells = self._cells_now()
        rims = [
            node
            for node in (
                (here[0] + dx, here[1] + dy, here[2] + dz)
                for dx in range(-3, 4)
                for dz in range(-3, 4)
                for dy in range(1, 5)
            )
            if standable(cells, node)
        ]
        rims.sort(key=lambda n: (math.dist(n, target), n))
        for node in rims[:3]:
            if self._navigate(node, reach=None, radius=0.6):
                return True
        guarded = set(getattr(self.terrain_policy, "protected_positions", ()))
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            wall = (here[0] + dx, here[1] + 1, here[2] + dz)
            if wall in guarded:  # never dig out through the house being built
                continue
            if _clickable(cells.get(wall)) and self.break_block(wall).ok:
                return True
        return False


# --- live session helpers (scripts) ---------------------------------------------


def open_bridge(folder, budget: int = 100000) -> RecordedBridge:
    bridge = RecordedBridge(folder / "events.sqlite3")
    bridge.budget = budget
    bridge.synchronize()
    return bridge


def _raise_interrupt(*_):
    raise KeyboardInterrupt


@contextmanager
def companion_world(seed: int | None):
    """Run in a fresh copy of natural seed `seed` (the lab `world` is flat), then
    restore the lab world and restart the server. None: keep the current world.
    Ctrl+C, SIGTERM and Ctrl+Break unwind through the restore; a hard kill leaves a
    properties backup that the next natural_world restores."""
    if seed is None:
        yield
        return
    import signal
    import threading

    from mclab import natural

    saved = {}
    if threading.current_thread() is threading.main_thread():
        for name in ("SIGTERM", "SIGBREAK"):
            if (number := getattr(signal, name, None)) is not None:
                saved[number] = signal.signal(number, _raise_interrupt)
    global _restored_world
    try:
        with natural.natural_world(seed):
            natural._restore(seed)  # fresh snapshot copy, server started
            _restored_world = True
            yield
    finally:
        _restored_world = False
        try:
            natural._start()
        finally:
            for number, handler in saved.items():
                signal.signal(number, handler)


def _level_dir():
    for line in (SERVER / "server.properties").read_text().splitlines():
        if line.startswith("level-name="):
            return SERVER / line.split("=", 1)[1].strip()
    return SERVER / "world"


def session_archive(
    folder,
    awareness,
    episode_id,
    world_memory=None,
    memory_policy=None,
    memory_root=None,
):
    """Per-world archive that outlives the session, or a per-run one.

    ``memory_root``: the folder the world's archive lives in (default
    ``WORLD_MEMORY``). Companions sharing a server each get their own, so one bot's
    remembered cells and places are never another's.

    None: world memory only in a kept world. A companion_world(seed) copy is
    restored fresh every run, so remembering it would make a run depend on the
    runs before it. Arena/course runs (natural.py) never come here.
    """
    memory_policy = memory_policy or MemoryPolicy()
    if not isinstance(memory_policy, MemoryPolicy):
        raise ValueError("Invalid memory policy")
    if world_memory is None:
        world_memory = not _restored_world
    else:
        # A copied seed world is isolated even if a caller requests persistence.
        world_memory = bool(world_memory) and not _restored_world
    world_memory = world_memory and memory_policy.memory_mode == "persistent"
    level = _level_dir()
    if world_memory:
        archive = world_archive(level, awareness, memory_root or WORLD_MEMORY)
        if archive is not None:
            return archive
        logging.getLogger(__name__).warning(
            "world memory off: no seed in %s/level.dat; using a per-run archive",
            level,
        )
    return MapArchive(
        folder / "map.sqlite3",
        MapIdentity(str(level.resolve()), episode_id, "minecraft:overworld", awareness),
    )


def await_sampled_stream(bridge, timeout: float = 60.0) -> dict:
    """Wait on /state (never /policy) until the stream runs and the sensor has
    sampled: before that the terrain batch is an empty placeholder (tick -100).
    Shared by start_session (lab episode) and the companion host's own spawn."""
    deadline = time.monotonic() + timeout
    while True:
        state = bridge.http("/state")
        sampled = state.get("awareness", {}).get("coverage", {}).get("sample_tick")
        if state["state"] == "running" and isinstance(sampled, int) and sampled >= 0:
            return state
        if time.monotonic() > deadline:
            raise RuntimeError("Policy stream did not reach a sampled running state")
        time.sleep(0.05)


def start_session(
    bridge,
    folder,
    start,
    *,
    awareness: Awareness | None = None,
    dig: bool = False,
    movement_policy: MovementPolicy | None = None,
    terrain_policy: TerrainPolicy | None = None,
    world_memory: bool | None = None,
    memory_policy: MemoryPolicy | None = None,
    markers=None,
):
    """Begin the long ``natural_start`` episode that keeps /policy and the follower
    open, and return ``(CompanionBody, SpatialMemory)``. Needs an idle lab.
    ``dig`` selects edit permissions; explicit movement/terrain policies override
    the defaults, without changing the available action methods.
    ``world_memory``: see session_archive (default: on in a kept world).
    ``memory_policy`` controls retention, history visibility and memory budgets.
    ``markers``: a switch (callable -> bool, e.g. markers.file_switch) for the
    in-game route, stance and block markers; None: never shown."""
    # sections-v1 terrain needs the "sections" preset; same profile as natural runs.
    awareness = awareness or Awareness.from_payload(
        read_json(ROOT / "config/stepc/awareness-sections-64.json")
    )
    memory_policy = memory_policy or MemoryPolicy()
    if not isinstance(memory_policy, MemoryPolicy):
        raise ValueError("Invalid memory policy")
    _new_identity(bridge, "companion")
    bridge.policy_memory = None
    # Opened before the body exists: creating the file costs about a second of disk
    # syncs, which the bot used to spend standing at spawn (1.75 s in run
    # 20261112T043759535110Z). Same db for places: per-world when world memory is
    # on, else per-run. Home is the requested feet cell (the mod spawns the body
    # there; the standable check below verifies the observed one).
    memory = SpatialMemory(
        session_archive(
            folder, awareness, bridge.episode_id, world_memory, memory_policy
        ),
        bridge.episode_id,
        policy=memory_policy,
    )
    places = PlaceGraph(memory.archive)
    if places.named("home") is None:
        places.add("home", tuple(start), kind="home", provenance="spawn")
    try:
        request = bridge.send(
            "natural_start",
            subject="carpet_body",
            player="",
            start=list(start),
            goal=[start[0] + 4000, start[1], start[2]],
            yaw=0,
            goal_radius=3,
            tick_limit=COMPANION_TICK_LIMIT,
            mobs=False,
            stall_ticks=0,
            awareness=awareness.payload(),
            terrain_encoding="sections-v1",
        )
        bridge.acknowledge(request, "natural_accepted", timeout=10)
        await_sampled_stream(bridge)
    except BaseException:
        memory.close()
        raise
    body = CompanionBody(
        bridge,
        memory,
        movement_policy=movement_policy,
        terrain_policy=terrain_policy
        if terrain_policy is not None
        else (DIG_TERRAIN if dig else WALK_TERRAIN),
    )
    body.observe()
    if not standable(memory.cells, body.position()):
        # live run 20261102T135116924480Z started inside a grass block: every route
        # search expanded nothing, and the bot cut logs whose drops it never reached
        end_session(bridge, memory)
        raise RuntimeError(f"start {list(start)} is not a standable feet cell")
    body.places = places
    if markers is not None:
        body.markers = Markers(bridge, markers)
        body.markers.aim()  # shown from the start when switched on
    return body, memory


def end_session(bridge, memory) -> None:
    # Stop the body first: the archive flush can take seconds and the bot must not
    # stand in the world waiting for it (r5 run 20261112T041306068037Z, 16.5 s).
    started = time.perf_counter()
    request = bridge.send("natural_stop")
    bridge.acknowledge(request, "received")
    stopped = time.perf_counter()
    memory.close()
    print(
        f"end_session: stop acknowledged {stopped - started:.2f}s, "
        f"memory.close {time.perf_counter() - stopped:.2f}s"
    )
