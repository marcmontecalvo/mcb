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
from collections.abc import Collection
from contextlib import contextmanager
from dataclasses import asdict, replace

from mclab.awareness import Awareness
from mclab.bridge import REJECTIONS
from mclab.build_runner import Result
from mclab.collision_geometry import standable
from mclab.control_loop import NATIVE, drive
from mclab.execution import RecordedBridge, policy_state
from mclab.interaction_goal import interacts
from mclab.movement_policy import MovementPolicy
from mclab.navigation_runner import _new_identity
from mclab.navigation_segmented import SegmentedController
from mclab.paths import ROOT, RUNTIME, SERVER, read_json
from mclab.place_graph import Connection, PlaceGraph
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
    # ponytail: no swimming until the body has a drowning reflex (surface when air
    # runs low); run 20261102T041702403876Z explored across a lake and drowned on
    # its floor while the planner planned from the surface node.
    allow_swim=False,
    # ladders and scaffolding are climbed; vines are not (DIG_TERRAIN.climb_vines):
    # run 20261102T052209764957Z stepped onto vines over a pond and drowned.
    allow_climb=True,
)
DIG_TERRAIN = TerrainPolicy(
    break_budget=64,
    place_budget=64,
    # dirt only: cobblestone is what the gather is for (the planner pillars with the
    material_allowlist=("minecraft:dirt",),
    allow_bridge=True,
    max_support_stack=3,
    allow_pillar=True,
    planner_tick_costs=True,
    planner_unknown=True,
    planner_edits=True,
    baritone_search=True,
    baritone_diagonals=True,
    baritone_moves=True,
    planner_swim=False,
    heuristic_weight=1,
    allow_falling_neighbours=False,
    use_doors=True,
    climb_vines=False,
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
    route_seen = None  # last native route id seen following (idle grace)
    graced_at = None  # where the last idle grace was granted

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
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.deadline = deadline
        self.walk_wall = walk_wall
        if probe:
            self.probe = True
            self.no_drive = True
        self.unreachable_since: int | None = None
        self.searching_since: int | None = None
        self.goal_progress_ticks = None  # companion goals may be far and slow
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
            editing = step.get("state") == "following" and step.get("issued") is True
            if editing:
                self.guard.anchor = None
            anchor = getattr(self, "idle_anchor", None)
            route = observation.get("route_follower") or {}
            if (
                route.get("state") == "following"
                and route.get("route_id") != self.route_seen
            ):
                self.route_seen = route.get("route_id")
                # A route just issued needs ticks to cover STUCK_MOVE: cancelling
                # it 0.25 s in failed run results/wading/20261111T164143495401Z.
                # Once per spot, so a stationary replan loop still ends at 5 s.
                graced = self.graced_at
                if graced is None or math.dist(position, graced) >= STUCK_MOVE:
                    self.graced_at = tuple(position)
                    if anchor is not None and now - self.idle_since >= 5.0:
                        print(  # report-only: the wait still shows in the log
                            f"STALL {now - self.idle_since:.1f}s navigation idle "
                            f"at {position}; mode={self.mode}, route={route}",
                            flush=True,
                        )
                    anchor = None  # restart the clock at the first issued tick
            if anchor is None or math.dist(position, anchor) >= STUCK_MOVE or editing:
                self.idle_anchor, self.idle_since = tuple(position), now
                if shared is not None:
                    shared.update(anchor=self.idle_anchor, since=now)
            elif now - self.idle_since >= 5.0:
                print(
                    f"STALL {now - self.idle_since:.1f}s navigation idle at {position}; "
                    f"mode={self.mode}, route={observation.get('route_follower', {})}",
                    flush=True,
                )
                if shared is not None:
                    shared["since"] = now
                return "stuck"
            # A walk can begin on land and slip into a deeper pond mid-route.
            # Enable recovery from the delivered water column on the next replan.
            if (
                body.get("survival", {}).get("in_water") is True
                and not self.movement_policy.allow_swim
                # a jump while wading lifts the feet off the half-rounded cell: the
                # whole cell under them must be unstandable too (a pond, not a puddle)
                and not any(
                    standable(
                        self._known_cells(),
                        (math.floor(position[0]), y, math.floor(position[2])),
                    )
                    for y in (round(position[1] * 2) / 2, math.floor(position[1]))
                )
            ):
                self.movement_policy = replace(self.movement_policy, allow_swim=True)
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
        if self.mode in {"searching", "incomplete", "planning"} and not uploading:
            # An interaction goal whose stance the known map cannot reach floods the
            # known graph for ever (live run 20261101T190355487393Z: 25 minutes).
            self.searching_since = self.searching_since or observation["tick"]
            if observation["tick"] - self.searching_since >= SEARCH_STALL_TICKS:
                return "stalled"
        else:
            self.searching_since = None
        return None

    def _probe_verdict(self) -> str | None:
        """End the motor-parked probe on the planner's first firm answer.

        The refresh loop replans the same start and goal on a static map, so one
        done verdict is final: the oscillation of searching/unreachable modes kept
        only the 200-tick StuckGuard alive, and 12 of those 10 s walks ended a
        gather at 0 tasks (run 20261106T170919632775Z)."""
        if self.mode in {"following", "terrain_step_motion", "centering"} or (
            self.last_result is not None
            and self.last_result.status in {"reached", "segment"}
        ):
            if self.last_result is not None and self.last_result.status == "incomplete":
                return "unreachable"
            return self._walk_on()  # route adopted: drive it, do not plan it again
        if self.mode in {"unreachable", "incomplete"} or (
            self.last_result is not None
            and self.last_result.status in {"unreachable", "incomplete"}
        ):
            return "unreachable"
        if time.monotonic() > self.deadline:
            # No verdict inside PROBE_WALL_S: walk it as before, keeping the
            # in-flight map upload and search (run 20261111T134723510228Z).
            return self._walk_on()
        return None

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
        self._inv: dict[str, int] = {}
        self._overlay: dict[Pos, tuple[str, int]] = {}
        self.log: list[dict] = []  # one row per op or goto, for the results folder
        self.places: PlaceGraph | None = None  # named waypoints (start_session)
        self.policies: list[dict] = []
        self._navigation_idle = {"anchor": None, "since": time.monotonic()}

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
        self._obs = obs
        self._set_inventory(obs.get("body", {}).get("inventory"))
        return obs

    def _set_inventory(self, stacks) -> None:
        if stacks is None:
            return
        inv: dict[str, int] = {}
        for stack in stacks:
            inv[stack["item"]] = inv.get(stack["item"], 0) + stack["count"]
        self._inv = inv

    def _tick(self) -> int:
        return int(self._obs.get("tick", 0))

    def position(self) -> Pos:
        x, y, z = self._obs["body"]["position"]
        return (math.floor(x), math.floor(y + 1e-6), math.floor(z))

    def inventory(self) -> dict[str, int]:
        """Fresh from the body: an op result's inventory predates the pickup of its
        own drop, so a stale count made each replan ask for more stone (run
        20261102T042713034884Z: five stone passes, 102 cobblestone)."""
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

    def find_blocks(self, block: str) -> list[Pos]:
        """Delivered cells holding ``block`` (no properties compared) that have a
        known open face: a block buried in stone cannot be reached without digging,
        and a gather loop that tried each one cost minutes."""
        keep = set(self.terrain_policy.protected_positions)
        return [p for p in self._scan(block) if p not in keep and self._open_face(p)]

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

    def _scan(self, block: str) -> list[Pos]:
        cells = self.memory.cells
        sections = getattr(cells, "sections", None)
        if sections is None:  # plain dict map
            return [p for p, c in cells.items() if c.get("block") == block]
        # Pathing-grid view: match palette ids, and scan only sections that hold one
        # (a per-cell walk of the whole map took minutes per call).
        ids = {i for i, s in enumerate(cells.states) if s and s.get("block") == block}
        found: list[Pos] = []
        for (sx, sy, sz), section in list(sections.items()):
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

        self.bridge.wait(lambda _state: find() is not None, timeout=timeout)
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
                result = Result(True, None, data)
            elif event["type"] == f"{name}_failed":
                result = Result(False, str(data.get("reason", "failed")), data)
            else:
                result = Result(False, f"rejected:{data.get('reason')}", data)
        self.log.append({"op": name, "ok": result.ok, "reason": result.reason})
        return result

    def _remember(self, pos: Pos, state: str | None) -> None:
        if state:
            self._navigation_idle.update(
                anchor=tuple(
                    self._obs.get("body", {}).get("position", self.position())
                ),
                since=time.monotonic(),
            )
            self._overlay[tuple(pos)] = (state, self._tick())

    def place_block(self, pos: Pos, state: str, item: str) -> Result:
        result = self.op("place_block", pos=list(pos), state=state, item=item)
        if result.ok:
            placed = result.data.get("placed")
            self._remember(pos, placed)
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
            self._remember(pos, result.data.get("after"))
        return result

    def break_block(self, pos: Pos, tool: str | None = None) -> Result:
        extra = {"tool": tool} if tool else {}
        result = self.op("break_block", pos=list(pos), **extra)
        if result.ok:
            self._remember(pos, "minecraft:air")
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

    def _move_policy(self) -> MovementPolicy:
        """ponytail: swimming stays off for routes (run 20261102 drowned crossing
        lakes), but a body already in water has no standable start without it and
        every plan came back unreachable (run 20261110T112815592970Z)."""
        wet = self._obs.get("body", {}).get("survival", {}).get("in_water")
        policy = self.movement_policy
        # Wading in one-deep water is a standable start: no swimming needed, and
        # turning it on there would let the route cross the deep pond.
        if not wet or standable(self._cells_now(), self.position()):
            return policy
        return replace(policy, allow_swim=True)

    def _navigate(
        self, goal: Pos, *, reach: float | None, radius: float, probe: bool = False
    ) -> bool:
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
        controller = _GotoController(
            goal,
            self.memory,
            radius=radius,
            interact_reach=reach,
            background_planning=True,
            movement_policy=self._move_policy(),
            terrain_policy=self.terrain_policy,
            deadline=time.monotonic() + wall,
            probe=probe,
            idle_state=self._navigation_idle,
            walk_wall=self.goto_wall_s,
        )
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

    def find_all(self, block: str) -> list[Pos]:
        """Delivered cells holding ``block``, buried ones included."""
        return self._scan(block)

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
        self, wall: float = 10.0, climb: bool = False, max_drop: int | None = None
    ):
        """Move without placing blocks, and give up a walk after ``wall`` seconds:
        an unreachable goal otherwise replans for 90 s (42 s gaps with 18 plans,
        run 20261110T020332013460Z). ``climb``: keep climbing (the build climbs its
        scaffolding towers)."""
        saved, saved_wall = self.terrain_policy, self.goto_wall_s
        saved_move = self.movement_policy
        # no vine climbing: site work is on the ground, and a route up the vines left
        # the body standing on a swamp tree's canopy (run 20261110T024448883505Z)
        self.movement_policy = replace(
            saved_move,
            allow_climb=saved_move.allow_climb and climb,
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

    @_fresh_idle
    def dig_to(self, feet: Pos) -> bool:
        """Stand at ``feet``, letting the planner dig there. False when digging is off."""
        if not self.terrain_policy.allow_break:
            return False
        return self._navigate(tuple(feet), reach=None, radius=0.6)

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
        for node in stances:
            # Ask the planner before walking (roadmap item 1): a candidate across
            # known water is unroutable, and testing that by walking it costs the
            # 200-tick StuckGuard timer - 12 of those ended a job at 0 tasks
            # (20261106T170919632775Z; the sand stall of 20261106T185239143471Z).
            # A found route is walked by the same controller, not planned twice
            # (two plans per stance in run 20261111T135214868983Z).
            if self._navigate(node, reach=None, radius=radius, probe=True):
                return True
        if not stances and not avoid and self._worth_searching(target):
            if self._navigate(target, reach=reach, radius=2):
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


def session_archive(folder, awareness, episode_id, world_memory=None):
    """Per-world archive that outlives the session, or a per-run one.

    None: world memory only in a kept world. A companion_world(seed) copy is
    restored fresh every run, so remembering it would make a run depend on the
    runs before it. Arena/course runs (natural.py) never come here.
    """
    if world_memory is None:
        world_memory = not _restored_world
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
):
    """Begin the long ``natural_start`` episode that keeps /policy and the follower
    open, and return ``(CompanionBody, SpatialMemory)``. Needs an idle lab.
    ``dig`` selects edit permissions; explicit movement/terrain policies override
    the defaults, without changing the available action methods.
    ``world_memory``: see session_archive (default: on in a kept world)."""
    # sections-v1 terrain needs the "sections" preset; same profile as natural runs.
    awareness = awareness or Awareness.from_payload(
        read_json(ROOT / "config/stepc/awareness-sections-64.json")
    )
    _new_identity(bridge, "companion")
    bridge.policy_memory = None
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
    memory = SpatialMemory(
        session_archive(folder, awareness, bridge.episode_id, world_memory),
        bridge.episode_id,
    )
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
    # same db as the archive: per-world when world memory is on, else per-run
    body.places = PlaceGraph(memory.archive)
    if body.places.named("home") is None:
        body.places.add("home", body.position(), kind="home", provenance="spawn")
    return body, memory


def end_session(bridge, memory) -> None:
    memory.close()
    request = bridge.send("natural_stop")
    bridge.acknowledge(request, "received")
