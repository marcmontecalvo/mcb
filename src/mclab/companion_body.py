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
import logging
import math
import time
from collections import ChainMap
from collections.abc import Callable, Collection
from contextlib import contextmanager
from dataclasses import asdict, replace

from mclab.awareness import Awareness
from mclab.bridge import REJECTIONS
from mclab.build_runner import Result
from mclab.collision_geometry import standable
from mclab.control_loop import NATIVE, drive
from mclab.execution import RecordedBridge, policy_state
from mclab.gather_tasks import item_stack
from mclab.interaction_goal import interacts
from mclab.memory_policy import MemoryPolicy
from mclab.movement_policy import MovementPolicy
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
from mclab.terrain_steps import TerrainPolicy

Pos = tuple[int, int, int]
AIR_BLOCKS = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}

OVERLAY_TICKS = 200  # until the sensor has re-delivered the cell; 40 was too short (run 20261102T222239739886Z: "occupied")
UNREACHABLE_TICKS = 60
GOTO_WALL_S = 90.0
MAIN_SLOTS = 36  # inventory indices 0..35: hotbar and main

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
OP_TIMEOUTS = {"break_block": 45.0, "attack_entity": 30.0, "eat": 20.0}
STAND_SEARCH = 6  # walk_to: vertical search for a standable cell in the column
WALK_CELLS = 2500  # bound on the stance-ranking flood fill
MAX_CANDIDATES = (
    6  # 3 nearest were often all unreachable (on a wall top, behind a wall)
)
WALK_ONLY_CANDIDATES = 2  # building: 2 probes is 5 s, the longest pause allowed
FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
SENSED_RADIUS = 40.0  # awareness delivers 64; beyond this a stance may be unsensed
SEARCH_STALL_TICKS = 60  # a search that yields no move for this long is abandoned
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
MAX_PICKUPS = 16
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


# neighbour direction -> is the eye of a body at stance cell `node` (1.62 above its feet)
# on the target side of that neighbour's face toward `target`
_FACE_SEEN = {
    (1, 0, 0): lambda n, t: n[0] <= t[0],
    (-1, 0, 0): lambda n, t: n[0] >= t[0],
    (0, 0, 1): lambda n, t: n[2] <= t[2],
    (0, 0, -1): lambda n, t: n[2] >= t[2],
    (0, 1, 0): lambda n, t: n[1] <= t[1] - 1,
    (0, -1, 0): lambda n, t: n[1] >= t[1] - 1,
}


EYE_HEIGHT = 1.62
FACE_SAMPLES = ((0, 0), (0.4, 0.4), (-0.4, 0.4), (0.4, -0.4), (-0.4, -0.4))


def _face_visible(cells, eye, block: Pos, d) -> bool:
    """Is some point of ``block``'s face toward -d (the target side) seen from ``eye``
    with no solid cell between? Live run 20261102T220426500869Z: an inside wall
    corner, whose faces only show from near the corner, was tried from across the
    room three times and skipped."""
    axis = next(i for i in range(3) if d[i])
    other = [i for i in range(3) if i != axis]
    for u, v in FACE_SAMPLES:
        point = [block[i] + 0.5 for i in range(3)]
        point[axis] -= d[axis] * 0.5
        point[other[0]] += u
        point[other[1]] += v
        if _ray_clear(cells, eye, point, block):
            return True
    return False


def _ray_clear(cells, eye, point, block: Pos) -> bool:
    """Every cell the segment eye->point passes through, exactly (grid traversal, as the
    game's clip does), is clear except ``block``. Sampling the segment let a diagonal
    ray slip between two walls meeting at an edge."""
    cell = [math.floor(v) for v in eye]
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
        if c != tuple(math.floor(v) for v in eye) and _clickable(cells.get(c)):
            return False
        i = t_max.index(min(t_max))
        if t_max[i] > 1:
            return True
        cell[i] += step[i]
        t_max[i] += t_delta[i]
    return True


def sees_support(
    cells, node: Pos, target: Pos, required: Pos | Collection[Pos] | None = None
) -> bool:
    """Does some solid neighbour of `target` have its face toward `target` turned to
    the eye of a body standing at `node`? Live run: the second plank of a pair was
    tried from the far side of the first, which shows it no face."""
    # the body arrives within PRECISE_RADIUS of the centre: a face must show from all of it
    eyes = [
        (node[0] + 0.5 + dx, node[1] + EYE_HEIGHT, node[2] + 0.5 + dz)
        for dx, dz in ((0, 0), (0.2, 0.2), (-0.2, 0.2), (0.2, -0.2), (-0.2, -0.2))
    ]
    if required is not None:
        if (
            isinstance(required, tuple)
            and len(required) == 3
            and isinstance(required[0], int)
        ):
            allowed = {required}
        else:
            allowed = {tuple(p) for p in required}
    else:
        allowed = None

    for d, seen in _FACE_SEEN.items():
        n = (target[0] + d[0], target[1] + d[1], target[2] + d[2])
        if allowed is not None and n not in allowed:
            continue
        cell = cells.get(n)
        if (
            _clickable(cell)
            and seen(node, target)
            and all(_face_visible(cells, eye, n, d) for eye in eyes)
        ):
            return True
        # a column of scaffolding is extended by clicking its side, from anywhere beside it
        if d == (0, -1, 0) and cell and cell.get("block") == "minecraft:scaffolding":
            return True
    return False


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
    on_pass = None  # called with the body position while a route is followed
    last_partial_walk = None  # (start, end) of the last partial walked to this goal

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
        if uploading or self.mode in ({"arrived", "following", "centering"} | EDIT_MODES):
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
                or self._partial_gains(last.route)
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
        self.movement_policy = (
            movement_policy if movement_policy is not None else NATURAL_MOVEMENT
        )
        self.terrain_policy = (
            terrain_policy if terrain_policy is not None else WALK_TERRAIN
        )
        # building: arrive near the cell centre, clear of the target
        self.precise_stance = False
        # set per placement by build_runner: the one face the block hangs on
        self.required_support: Pos | None = None
        self._obs: dict = {}
        self._obs_tick: int | None = None
        self._inv: dict[str, int] = {}
        self._rows: list[dict] = []  # the per-slot rows behind _inv
        self._overlay: dict[Pos, tuple[str, int]] = {}
        self.log: list[dict] = []  # one row per op or goto, for the results folder
        self.places: PlaceGraph | None = None  # named waypoints (start_session)
        self.policies: list[dict] = []
        self._navigation_idle = {"anchor": None, "since": time.monotonic()}
        self._failed_stance_probes = {}
        # building: called with the body position while a walk is under way, to
        # place what comes within reach (set by build_runner._sweep)
        self.on_pass: Callable[[Pos], None] | None = None
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
        for pos, (_, at) in list(self._overlay.items()):
            sensed_at = self.memory.live_observed_tick(pos)
            if sensed_at is not None and sensed_at > at:
                del self._overlay[pos]
        self._obs = obs
        # the server tick the observation was read at: inventory() reuses it until the
        # game moves on (run 20261116T153811910615Z: 17 s of full observe() round-trips
        # for one gather-loop iteration's inventory reads)
        self._obs_tick = getattr(self.bridge, "last_tick", None)
        self._set_inventory(obs.get("body", {}).get("inventory"))
        return obs

    def _set_inventory(self, stacks) -> None:
        if stacks is None:
            return
        inv: dict[str, int] = {}
        for stack in stacks:
            inv[stack["item"]] = inv.get(stack["item"], 0) + stack["count"]
        self._inv, self._rows = inv, list(stacks)

    def free_slots(self) -> int:
        """Empty main-inventory slots (0..35; armour and offhand excluded). The mod
        sends only the non-empty slots."""
        return MAIN_SLOTS - len({r["slot"] for r in self._rows if 0 <= r["slot"] < MAIN_SLOTS})

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

    def inventory(self) -> dict[str, int]:
        """Fresh from the body: an op result's inventory predates the pickup of its
        own drop, so a stale count made each replan ask for more stone (run
        20261102T042713034884Z: five stone passes, 102 cobblestone). Only the cheap
        /state tick is read when the game has not advanced since the last
        observation: nothing can have changed, so the held inventory is exact."""
        tick = getattr(self.bridge, "state", None)
        if (
            self._obs_tick is not None
            and tick is not None
            and tick()["tick"] == self._obs_tick
        ):
            return dict(self._inv)
        self.observe()
        return dict(self._inv)

    def food(self) -> int:
        return int(self.observe()["body"].get("food", 20))

    def entities(self) -> list[dict]:
        return list(self.observe().get("visible_entities") or ())

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
                all(lo <= s <= hi for s, (lo, hi) in zip(key, box, strict=True)) for box in boxes
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

    def op(self, name: str, **fields) -> Result:
        payload = self.bridge.send(name, **fields)
        try:
            event = self._await(name, payload, OP_TIMEOUTS.get(name, OP_TIMEOUT_S))
        except TimeoutError:
            result = Result(False, "timeout")
        else:
            data = dict(event["data"])
            self._set_inventory(data.get("inventory"))
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

    def _remember(self, pos: Pos, state: str | None, tick: int | None = None) -> None:
        if state:
            self._failed_stance_probes.clear()
            self._navigation_idle.update(
                anchor=tuple(
                    self._obs.get("body", {}).get("position", self.position())
                ),
                since=time.monotonic(),
            )
            self._overlay[tuple(pos)] = (state, self._tick() if tick is None else int(tick))

    def place_block(self, pos: Pos, state: str, item: str) -> Result:
        result = self.op("place_block", pos=list(pos), state=state, item=item)
        if result.ok:
            placed = result.data.get("placed")
            self._remember(pos, placed, result.data.get("_event_tick"))
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
        result = self.op("use_block", pos=list(pos), **extra)
        if result.ok:
            self._remember(pos, result.data.get("after"), result.data.get("_event_tick"))
        return result

    def break_block(
        self, pos: Pos, tool: str | None = None, drops_optional: bool = False
    ) -> Result:
        extra: dict = {"tool": tool} if tool else {}
        if drops_optional:
            extra["drops_optional"] = True
        result = self.op("break_block", pos=list(pos), **extra)
        if result.ok:
            self._remember(pos, "minecraft:air", result.data.get("_event_tick"))
        return result

    def craft(self, recipe: str, times: int, table: Pos | None = None) -> Result:
        extra = {"table": list(table)} if table else {}
        return self.op("craft", recipe=recipe, times=times, **extra)

    def furnace(self, pos: Pos, action: str, **fields) -> Result:
        result = self.op("furnace", pos=list(pos), action=action, **fields)
        if result.ok:
            self._record("furnace", pos)
        return result

    def equip(self, item: str) -> Result:
        return self.op("equip", item=item)

    def _axe(self) -> str | None:
        tiers = ("wooden", "golden", "stone", "copper", "iron", "diamond", "netherite")
        axes = [
            f"minecraft:{t}_axe" for t in tiers if self._inv.get(f"minecraft:{t}_axe")
        ]
        return axes[-1] if axes else None

    def attack_entity(self, entity: str, weapon: str | None = None) -> Result:
        extra = {"weapon": weapon} if weapon else {}
        return self.op("attack_entity", entity=entity, **extra)

    def use_entity(self, entity: str, item: str | None = None) -> Result:
        extra = {"item": item} if item else {}
        return self.op("use_entity", entity=entity, **extra)

    def eat(self, item: str | None = None) -> Result:
        return self.op("eat", **({"item": item} if item else {}))

    def container(self, pos: Pos, action: str, items: list[dict] | None = None):
        extra = {"items": items} if items is not None else {}
        result = self.op("container", pos=list(pos), action=action, **extra)
        if result.ok:
            self._record("container", pos)
        return result

    # --- navigation --------------------------------------------------------
    def stage_fill(self) -> str | None:
        """The planner prices a pillar or bridge edit from hotbar stock only. When the
        policy allows placing and the allowed material sits in the main inventory
        (slots 9-35) with none on the hotbar, equip it. Returns the item moved, else
        None. (Run 20261109T204446994225Z: dirt only in slots 14 and 16, so
        placeSupply was 0, every pillar edit was refused and the body stood in a pit
        planning "unreachable".)"""
        policy = self.terrain_policy
        if not (policy.allow_pillar or policy.allow_bridge or policy.allow_place):
            return None
        rows = ((self._obs or {}).get("body") or {}).get("inventory") or []
        for item in policy.material_allowlist:
            held = [r for r in rows if r.get("item") == item and r.get("count", 0) > 0]
            if held and not any(0 <= r.get("slot", -1) < 9 for r in held):
                if self.equip(item).ok:
                    self.observe()
                    return item
        return None

    def _goto_controller(self, goal, reach, radius, probe, wall):
        return _GotoController(
            goal,
            self.memory,
            radius=radius,
            interact_reach=reach,
            background_planning=True,
            movement_policy=self.movement_policy,
            terrain_policy=self.terrain_policy,
            deadline=time.monotonic() + wall,
            probe=probe,
            idle_state=self._navigation_idle,
            walk_wall=self.goto_wall_s,
            walk_partial=not getattr(self, "walk_only", False),
        )

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
        mid = self._hop_toward(goal) if reach is None else None
        if mid is not None:
            # A goal past the sensed terrain is an unreachable search (a plan 101
            # blocks out never completed): walk to the sensed edge, then go on.
            before = self.position()
            if math.hypot(mid[0] - before[0], mid[2] - before[2]) > SENSED_RADIUS:
                return False
            if not self._navigate(mid, reach=None, radius=2.0, probe=probe, progress=progress):
                return False
            if math.dist(self.position(), before) < 2.0:
                return False  # the hop did not move us: do not loop
            return self._navigate(
                goal, reach=reach, radius=radius, probe=probe, progress=progress
            )
        self.stage_fill()
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
        controller.last_partial_walk = self._partial_walks.get(tuple(goal))
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
            controller.close()
        self.observe()
        last = getattr(controller, "last_result", None)
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
        for dy in sorted(range(-STAND_SEARCH, STAND_SEARCH + 1), key=abs):
            if standable(cells, (x, y + dy, z)):
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
        if y - standing_y > 2:
            return None
        if standing_y - self.position()[1] > 2 or standing_y - y > 2:
            return False
        return self.walk_to((x, standing_y, z), radius)

    def collect_drops(self, near: Collection[Pos], radius: float = DROP_RADIUS) -> None:
        """Wait for what fell (a scaffolding tower drops block by block), then walk onto
        the dropped items within ``radius`` of the cells ``near``."""
        self.wait_ticks(DROP_WAIT_TICKS)
        centres = [(x + 0.5, y + 0.5, z + 0.5) for x, y, z in near]
        skipped: set = set()
        for _ in range(MAX_PICKUPS):
            here = self.position()
            drops = [
                e
                for e in self.entities()
                if e.get("item")
                and e.get("uuid") not in skipped
                and e["position"][1] - here[1] <= 2
                and any(math.dist(e["position"], c) <= radius for c in centres)
            ]
            if not drops:
                return
            e = min(drops, key=lambda e: math.dist(e["position"], here))
            x, y, z = (math.floor(v) for v in e["position"])
            if not self.walk_to_drop((x, y, z), 0.8):
                skipped.add(e.get("uuid"))

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
    def no_placing(
        self, wall: float = 10.0, max_drop: int | None = None
    ):
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

    def _place_supply(self) -> int:
        policy = self.terrain_policy
        if not (policy.allow_pillar or policy.allow_bridge or policy.allow_place):
            return 0
        held = sum(self._inv.get(item, 0) for item in policy.material_allowlist)
        return max(0, held - policy.reserve_blocks)

    @_fresh_idle
    def pillar_to(self, feet: Pos, item: str, n: int) -> bool:
        """Stand at ``feet`` straight above the body by pillaring with ``item`` only
        (at most ``n`` blocks, no digging or bridging), even inside ``no_placing``: the
        build's tower of dirt is a solid column nothing can walk or climb onto."""
        saved = self.terrain_policy
        self.terrain_policy = replace(
            saved,
            allow_place=True,
            allow_pillar=True,
            allow_break=False,
            allow_bridge=False,
            place_budget=n,
            material_allowlist=(item,),
        )
        try:
            return self._navigate(tuple(feet), reach=None, radius=0.6)
        finally:
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
        found = [
            node
            for node in (
                (tx + dx, ty + dy, tz + dz)
                for dx in range(-r, r + 1)
                for dz in range(-r, r + 1)
                for dy in range(-r, 2)
            )
            if node not in avoid
            # never inside the target: the feet cell, or the one below (head in it);
            # live run: scaffolding has no collision, so the body stood in the tower
            and node not in ((tx, ty, tz), (tx, ty - 1, tz))
            and (standable(cells, node) or _tower_top(cells, node))
            and interacts(cells, node, target, reach)
            and (
                not placing or sees_support(cells, node, target, self.required_support)
            )
        ]
        # ponytail: stances the body can walk to come first. Sorted by distance
        # alone, wall tops and roof cells two blocks away outranked the floor four
        # away; each cost a failed 4000-node plan (run 20261110T095528994372Z: 284
        # unreachable probes, a dirt tower per wall). Upgrade path: plan cost.
        walk = self._walkable(cells, here) if found else ()
        return sorted(found, key=lambda n: (n not in walk, math.dist(n, here), n))

    def _walkable(self, cells, here: Pos) -> set[Pos]:
        """Cells reachable from ``here`` on foot with no edits: 4 directions, one
        up, ``max_drop`` down, at most WALK_CELLS cells. A ranking hint, not a
        verdict (no diagonals, jumps or headroom between cells)."""
        key = (here, self._tick() // 40)
        if getattr(self, "_walk_memo", (None,))[0] == key:
            return self._walk_memo[1]
        drop = self.movement_policy.max_drop
        seen, todo = {here}, [here]
        while todo and len(seen) < WALK_CELLS:
            x, y, z = todo.pop()
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for dy in range(1, -drop - 1, -1):
                    node = (x + dx, y + dy, z + dz)
                    if node not in seen and standable(cells, node):
                        seen.add(node)
                        todo.append(node)
                        break
        self._walk_memo = (key, seen)
        return seen

    def _cells_now(self):
        """The sensed map plus this body's own recent edits (the overlay): a wall
        placed a moment ago is not sensed yet, and the sight check must not look
        through it (run 20261102T221946087817Z)."""
        tick = self._tick()
        recent = {
            pos: {
                "block": parse_state(state)[0],
                "fluid": "minecraft:empty",
                "collision_boxes": []
                if parse_state(state)[0] in AIR_BLOCKS
                else [[0, 0, 0, 1, 1, 1]],
            }
            for pos, (state, at) in self._overlay.items()
            if tick - at <= OVERLAY_TICKS
        }
        # ponytail: a lookup view, never a copy. Copying the whole sensed map here was
        # 79% of build time, ~13 s per placed block (py-spy, run 20261110T024448883505Z)
        return ChainMap(recent, self.memory.cells) if recent else self.memory.cells

    def _placing(self, target: Pos) -> bool:
        """Building into an empty cell: the stance must see a face to click."""
        return self.precise_stance and not _clickable(self._cells_now().get(target))

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

    def can_place_here(self, target: Pos, reach: float, required=None) -> bool:
        """Check the current placement stance before an optimistic direct action."""
        cells, here = self._cells_now(), self.position()
        return (
            interacts(cells, here, target, reach)
            and not self._overlaps(target)
            and sees_support(cells, here, target, required)
        )

    @_fresh_idle
    def goto_stance(
        self, target_pos: Pos, reach: float, avoid: Collection[Pos] = ()
    ) -> bool:
        target, avoid = tuple(target_pos), set(avoid)
        if not self._obs:
            self.observe()
        here = self.position()
        cells = self._cells_now()
        # A precise stance (building) also needs the body out of the target cell: a
        # block cannot be placed into a cell the body overlaps, and the 0.6 arrival
        # radius lets it stand 0.4 into a neighbour (live run: 21 state_unreachable).
        crowded = self.precise_stance and self._overlaps(target)
        sees = not self._placing(target) or sees_support(
            cells, here, target, self.required_support
        )
        if (
            here not in avoid
            and interacts(cells, here, target, reach)
            and not crowded
            and sees
        ):
            return True
        # Known stances first, walked with the body planner; the interaction-goal
        # search only for a target whose stance is not yet on the map.
        stances = self.stances(target, reach, avoid)
        if getattr(self, "walk_only", False):
            # ponytail: with planner placing off an unwalkable stance cannot be
            # reached, and each probe of one floods the search to its cap (2043 of
            # 3350 plans in run 20261110T150634125260Z). One is kept in case the
            # walk flood missed a jump.
            # A scaffolding tower's top is climbed, not walked: the flood does not
            # see it, so the tower built for a target was never the stance tried and
            # the body stopped inside the column (run 20261110T223248667856Z).
            walk = self._walkable(cells, here)
            stances = [
                n for n in stances if n in walk or _tower_top(cells, n)
            ] or stances[:1]
            # each probe is up to PROBE_WALL_S of standing still
            stances = stances[:WALK_ONLY_CANDIDATES]
        stances = stances[:MAX_CANDIDATES]
        radius = PRECISE_RADIUS if self.precise_stance else 0.6
        hopped = False
        # One no-progress clock for the whole stance loop: each probe resets the
        # drive's own timer, so 6 stances could burn 6 x 150 s on one goal.
        progress = {"distance": math.dist(here, target), "tick": None}
        for node in stances:
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
            if getattr(self, "walk_only", False) and self._failed_stance_probes.get(key, 0) > time.monotonic():
                continue
            if distant and self._hop_toward(node) is not None:
                hopped = True
            if self._navigate(node, reach=None, radius=radius, probe=True, progress=progress):
                return True
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
            if self._navigate(target, reach=reach, radius=2, progress=progress):
                if self.position() not in avoid:
                    return True
        return False

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
    bridge = RecordedBridge(folder / "events.jsonl")
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
    folder, awareness, episode_id, world_memory=None, memory_policy=None
):
    """Per-world archive that outlives the session, or a per-run one.

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
        archive = world_archive(level, awareness, WORLD_MEMORY)
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
):
    """Begin the long ``natural_start`` episode that keeps /policy and the follower
    open, and return ``(CompanionBody, SpatialMemory)``. Needs an idle lab.
    ``dig`` selects edit permissions; explicit movement/terrain policies override
    the defaults, without changing the available action methods.
    ``world_memory``: see session_archive (default: on in a kept world).
    ``memory_policy`` controls retention, history visibility and memory budgets."""
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
            tick_limit=100_000,
            mobs=False,
            stall_ticks=0,
            awareness=awareness.payload(),
            terrain_encoding="sections-v1",
        )
        bridge.acknowledge(request, "natural_accepted", timeout=10)
        deadline = time.monotonic() + 60
        # Wait on /state (never /policy) until the stream runs and the sensor has
        # sampled: before that the terrain batch is an empty placeholder (tick -100).
        while True:
            state = bridge.http("/state")
            sampled = state.get("awareness", {}).get("coverage", {}).get("sample_tick")
            if state["state"] == "running" and isinstance(sampled, int) and sampled >= 0:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Policy stream did not reach a sampled running state")
            time.sleep(0.05)
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
