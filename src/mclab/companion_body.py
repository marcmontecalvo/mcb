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
    allow_climb=True,
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
)


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
        found = [
            node
            for node in (
                (tx + dx, ty + dy, tz + dz)
                for dx in range(-r, r + 1)
                for dz in range(-r, r + 1)
                for dy in range(-r, 2)
            )
            if node not in avoid
            and standable(cells, node)
            and interacts(cells, node, target, reach)
        ]
        return sorted(found, key=lambda n: (math.dist(n, here), n))

    def _worth_searching(self, target: Pos) -> bool:
        """The interaction-goal search can only help where the map is still unsensed.
        A near target with no known stance has none: the search floods the known
        graph (8,192 expansions, route of one cell) and ends in "stalled". Live run
        20261101T203526520132Z: 60 such searches from a ravine floor."""
        return math.dist(self.position(), target) > SENSED_RADIUS

    def goto_stance(
        self, target_pos: Pos, reach: float, avoid: Collection[Pos] = ()
    ) -> bool:
        target, avoid = tuple(target_pos), set(avoid)
        if not self._obs:
            self.observe()
        here = self.position()
        cells = self.memory.cells
        if here not in avoid and interacts(cells, here, target, reach):
            return True
        # Known stances first, walked with the body planner; the interaction-goal
        # search only for a target whose stance is not yet on the map.
        stances = self.stances(target, reach, avoid)[:MAX_CANDIDATES]
        for node in stances:
            if self._navigate(node, reach=None, radius=0.6):
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
    return body, memory


def end_session(bridge, memory) -> None:
    memory.close()
    request = bridge.send("natural_stop")
    bridge.acknowledge(request, "received")
