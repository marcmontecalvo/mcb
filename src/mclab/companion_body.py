# ruff: noqa: E501
"""Real body adapter for the companion executors (gather and build), over ``Bridge``.

``CompanionBody`` implements ``build_runner.BuildBody`` and ``gather_runner.GatherBody``.
Ops: docs/BODY_ACTION_OPS.md. Navigation is reused, not rewritten.

Navigation reuse
- ``goto_stance(target, reach, avoid)``: a ``SegmentedController`` with
  ``interact_reach=reach`` (route_search ``RouteSearch``/``interacts`` goal) driven by
  ``control_loop.drive(..., profile=NATIVE)`` = ``native_driver.drive_native``, which
  submits the planned route to the body's ``follow_route`` follower, as natural runs do.
- ``walk_to(pos)``: the same controller with a stand-on goal (radius), body planner on.
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
3. ``avoid``: the planner cannot exclude a stance. When ``avoid`` is non-empty, the
   adapter picks the nearest known standable cell outside ``avoid`` from which
   ``interacts`` holds and walks onto it (``walk_to``); when the planned stance falls in
   ``avoid`` it does the same. Stances are compared as integer feet cells.
4. A goto gives up after ``goto_wall_s`` wall seconds, or when the planner says
   ``unreachable`` for ``UNREACHABLE_TICKS`` ticks, and returns False. The episode stays
   open. Default: walking/jumping, no digging; ``start_session(dig=True)`` lets the
   planner dig (DIG_TERRAIN), as gather jobs need.
5. Op ``_failed`` events map to ``Result(False, reason, data)``; a ``rejected`` event maps
   to ``Result(False, "rejected:<reason>")``; a wall timeout to ``Result(False, "timeout")``.
   ``bridge.wait`` heartbeats while waiting.
6. Entities come from ``visible_entities`` (``uuid``, ``type``, ``position``, items with
   ``item``/``count``), food from ``body.food``, inventory from ``body.inventory``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Collection
from contextlib import contextmanager

from mclab.awareness import Awareness
from mclab.bridge import REJECTIONS
from mclab.build_runner import Result
from mclab.control_loop import NATIVE, drive
from mclab.execution import RecordedBridge, policy_state
from mclab.movement_policy import MovementPolicy
from mclab.navigation import standable
from mclab.navigation_runner import _new_identity
from mclab.navigation_segmented import SegmentedController
from mclab.paths import ROOT, SERVER, read_json
from mclab.route_search import interacts
from mclab.schematic import format_state
from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory
from mclab.terrain_steps import TerrainPolicy

Pos = tuple[int, int, int]

OVERLAY_TICKS = 40
UNREACHABLE_TICKS = 60
GOTO_WALL_S = 90.0
OP_TIMEOUT_S = 20.0
OP_TIMEOUTS = {"break_block": 45.0, "attack_entity": 30.0, "eat": 20.0}
STAND_SEARCH = 6  # walk_to: vertical search for a standable cell in the column
MAX_CANDIDATES = 3
FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
SENSED_RADIUS = 40.0  # awareness delivers 64; beyond this a stance may be unsensed
SEARCH_STALL_TICKS = 60  # a search that yields no move for this long is abandoned
SETTLE_TICKS = 40  # settle(): longest wait for the map to show broken blocks
SETTLE_STEP = 5
STUCK_TICKS = 200  # a goto or walk whose position does not change for this long fails
STUCK_MOVE = 0.5  # blocks that count as progress
DROP_RADIUS = 8.0  # collect_drops: items this close to the broken cells are picked up
DROP_WAIT_TICKS = 40  # a tower of scaffolding drops a layer per few ticks
MAX_PICKUPS = 16
BODY_HALF_WIDTH = 0.3
BODY_HEIGHT = 1.8
PRECISE_RADIUS = 0.2  # centre-to-centre, so the box stays inside the stance cell (0.5 - 0.3)


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
    # no climbing either: run 20261102T052209764957Z stepped onto vines over a pond,
    # slid into 2-deep water, and air recovery saw the vine above as no way up.
    allow_climb=False,
)
DIG_TERRAIN = TerrainPolicy(
    break_budget=64,
    place_budget=64,
    # dirt only: cobblestone is what the gather is for (the planner pillars with the
    # first listed material; run 20261102T030233328966Z spent it climbing out)
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
)


def _clickable(cell) -> bool:
    """A cell with a face to click: collision, or scaffolding (a lattice with none)."""
    return bool(cell) and bool(
        cell.get("collision_boxes") or cell.get("block") == "minecraft:scaffolding"
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


def sees_support(cells, node: Pos, target: Pos) -> bool:
    """Does some solid neighbour of `target` have its face toward `target` turned to
    the eye of a body standing at `node`? Live run: the second plank of a pair was
    tried from the far side of the first, which shows it no face."""
    for d, seen in _FACE_SEEN.items():
        n = (target[0] + d[0], target[1] + d[1], target[2] + d[2])
        cell = cells.get(n)
        if _clickable(cell) and seen(node, target):
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

    def __init__(self, *args, deadline: float, **kwargs):
        super().__init__(*args, **kwargs)
        self.deadline = deadline
        self.unreachable_since: int | None = None
        self.searching_since: int | None = None
        self.goal_progress_ticks = None  # companion goals may be far and slow
        self.guard = StuckGuard()

    def stop_reason(self, observation) -> str | None:
        if self.mode == "arrived":
            return "arrived"
        if time.monotonic() > self.deadline:
            return "deadline"
        body = observation.get("body") or {}
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
        if self.mode in {"searching", "incomplete"}:
            # An interaction goal whose stance the known map cannot reach floods the
            # known graph for ever (live run 20261101T190355487393Z: 25 minutes).
            self.searching_since = self.searching_since or observation["tick"]
            if observation["tick"] - self.searching_since >= SEARCH_STALL_TICKS:
                return "stalled"
        else:
            self.searching_since = None
        return None


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
        self.goto_wall_s, self.movement_policy = goto_wall_s, movement_policy
        self.terrain_policy = terrain_policy  # None: walk only, never dig
        self.precise_stance = False  # building: arrive near the cell centre, clear of the target
        self._obs: dict = {}
        self._inv: dict[str, int] = {}
        self._overlay: dict[Pos, tuple[str, int]] = {}
        self.log: list[dict] = []  # one row per op or goto, for the results folder

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
        return [p for p in self._scan(block) if self._open_face(p)]

    def _open_face(self, pos: Pos) -> bool:
        cells = self.memory.cells
        x, y, z = pos
        for dx, dy, dz in FACES:
            cell = cells.get((x + dx, y + dy, z + dz))
            if cell is not None and not cell.get("collision_boxes"):
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
            self._overlay[tuple(pos)] = (state, self._tick())

    def place_block(self, pos: Pos, state: str, item: str) -> Result:
        result = self.op("place_block", pos=list(pos), state=state, item=item)
        if result.ok:
            self._remember(pos, result.data.get("placed"))
        return result

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
        return self.op("furnace", pos=list(pos), action=action, **fields)

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
        return self.op("container", pos=list(pos), action=action, **extra)

    # --- navigation --------------------------------------------------------
    def _navigate(self, goal: Pos, *, reach: float | None, radius: float) -> bool:
        controller = _GotoController(
            goal,
            self.memory,
            radius=radius,
            interact_reach=reach,
            background_planning=True,
            movement_policy=self.movement_policy,
            terrain_policy=self.terrain_policy,
            body_planner=reach is None,  # the body planner has no interact goal yet
            deadline=time.monotonic() + self.goto_wall_s,
        )
        try:
            end = drive(
                self.bridge, controller, self.goto_wall_s + 60.0, profile=NATIVE
            )
        finally:
            self.bridge.state()  # heartbeat: close() joins the worker threads
            controller.close()
        self.observe()
        why = end.get("data", {}).get("reason") if end else None
        ok = end.get("type") == "navigation_stopped" and why == "arrived"
        self.log.append({"goto": list(goal), "reach": reach, "ok": ok, "why": why})
        return ok

    def _stand_y(self, x: int, y: int, z: int) -> int:
        cells = self.memory.cells
        for dy in sorted(range(-STAND_SEARCH, STAND_SEARCH + 1), key=abs):
            if standable(cells, (x, y + dy, z)):
                return y + dy
        return y

    def walk_to(self, pos: Pos, radius: float = 1.5) -> bool:
        x, y, z = pos
        return self._navigate((x, self._stand_y(x, y, z), z), reach=None, radius=radius)

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
            if not self.walk_to((x, y, z), 0.8):
                skipped.add(e.get("uuid"))

    def find_all(self, block: str) -> list[Pos]:
        """Delivered cells holding ``block``, buried ones included."""
        return self._scan(block)

    def dig_to(self, feet: Pos) -> bool:
        """Stand at ``feet``, letting the planner dig there. False when digging is off."""
        if self.terrain_policy is None:
            return False
        return self._navigate(tuple(feet), reach=None, radius=0.6)

    def stances(self, target: Pos, reach: float, avoid: Collection[Pos] = ()):
        """Known standable cells from which ``target`` is reached, nearest first."""
        cells, here = self.memory.cells, self.position()
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
            and standable(cells, node)
            and interacts(cells, node, target, reach)
            and (not placing or sees_support(cells, node, target))
        ]
        return sorted(found, key=lambda n: (math.dist(n, here), n))

    def _placing(self, target: Pos) -> bool:
        """Building into an empty cell: the stance must see a face to click."""
        return self.precise_stance and not _clickable(self.memory.cells.get(target))

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

    def goto_stance(
        self, target_pos: Pos, reach: float, avoid: Collection[Pos] = ()
    ) -> bool:
        target, avoid = tuple(target_pos), set(avoid)
        if not self._obs:
            self.observe()
        here = self.position()
        cells = self.memory.cells
        # A precise stance (building) also needs the body out of the target cell: a
        # block cannot be placed into a cell the body overlaps, and the 0.6 arrival
        # radius lets it stand 0.4 into a neighbour (live run: 21 state_unreachable).
        crowded = self.precise_stance and self._overlaps(target)
        sees = not self._placing(target) or sees_support(cells, here, target)
        if here not in avoid and interacts(cells, here, target, reach) and not crowded and sees:
            return True
        # Known stances first, walked with the body planner; the interaction-goal
        # search only for a target whose stance is not yet on the map.
        stances = self.stances(target, reach, avoid)[:MAX_CANDIDATES]
        radius = PRECISE_RADIUS if self.precise_stance else 0.6
        for node in stances:
            if self._navigate(node, reach=None, radius=radius):
                return True
        if not stances and not avoid and self._worth_searching(target):
            if self._navigate(target, reach=reach, radius=2):
                if self.position() not in avoid:
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
    try:
        with natural.natural_world(seed):
            natural._restore(seed)  # fresh snapshot copy, server started
            yield
    finally:
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


def start_session(
    bridge, folder, start, *, awareness: Awareness | None = None, dig: bool = False
):
    """Begin the long ``natural_start`` episode that keeps /policy and the follower
    open, and return ``(CompanionBody, SpatialMemory)``. Needs an idle lab. ``dig``:
    move and dig like natural cohort 30 (NATURAL_MOVEMENT, DIG_TERRAIN)."""
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
    identity = MapIdentity(
        str(_level_dir().resolve()),
        bridge.episode_id,
        "minecraft:overworld",
        awareness,
    )
    memory = SpatialMemory(
        MapArchive(folder / "map.sqlite3", identity), bridge.episode_id
    )
    body = (
        CompanionBody(
            bridge, memory, movement_policy=NATURAL_MOVEMENT, terrain_policy=DIG_TERRAIN
        )
        if dig
        else CompanionBody(bridge, memory)
    )
    body.observe()
    if not standable(memory.cells, body.position()):
        # live run 20261102T135116924480Z started inside a grass block: every route
        # search expanded nothing, and the bot cut logs whose drops it never reached
        end_session(bridge, memory)
        raise RuntimeError(f"start {list(start)} is not a standable feet cell")
    return body, memory


def end_session(bridge, memory) -> None:
    memory.close()
    request = bridge.send("natural_stop")
    bridge.acknowledge(request, "received")
