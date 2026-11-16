# ruff: noqa: E501
"""Executor for the gathering planner's tasks (G0, F1): ``run_gather``.

Same shape as ``build_runner``: a ``GatherBody`` protocol (the ``BuildBody`` ops plus the
wave 3 ops), a fake in ``gather_fake.py``, the real adapter in ``companion_body.py``.

``run_gather(plan_result, body, knowledge, targets=...)`` executes the tasks in order.
After every task, and after any failure, it re-plans from the observed inventory with
``gather_plan.plan(targets, inventory, knowledge, stations)``, so the plan always fits
what the bot really holds. It stops after ``max_failures`` failed tasks in a row, when
the plan comes back empty (done) or reports only missing items, or after ``max_tasks``.
Without ``targets`` the given task list is run once, with no re-planning.

Watchdog: when the inventory has not changed for ``stall_s`` wall seconds (walking,
exploring and route searches that collect nothing), the run stops with ``stalled``.
Live run 20261102T120137588638Z explored a treeless shore for 100,000 ticks.

Hunger (F1): before each task, food <= EAT_BELOW and food held -> ``eat``. If nothing is
held and food <= HUNT_BELOW, a food target (the best food the planner can reach, usually
cooked meat) joins ``targets`` at the next re-plan. Needs ``targets``.

Task fields used for tasks that are plain dataclasses of ``gather_tasks``:
``Gather(block, count, item, item_count)``, ``Hunt/Shear(entity_type, count, weapon)``,
``PlaceContainer(kind)``, ``Deposit/Withdraw(items, pos)``. Containers and stations are
remembered in ``GatherState`` (positions and contents).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from itertools import product
from typing import Protocol

from mclab import build_plan as bp
from mclab import gather_plan as gp
from mclab.build_runner import REACH, STANCE_REASONS, BuildBody, Result
from mclab.gather_dig import Digger
from mclab.progress_watch import BodyDied
from mclab.gather_kit import craft_local, craft_underground
from mclab.gather_options import SENSE_DY, GatherOptions, pick_tool
from mclab.gather_tasks import TIER_RANK, UNKNOWN_TIER_RANK
from mclab.knowledge import Knowledge
from mclab.tree_removal import TreeFeller, is_huge, is_leaf, is_log, tree_at

Pos = tuple[int, int, int]

EAT_BELOW = 14
HUNT_BELOW = 10
STATION_REACH = 3.5
# Beyond this to a placed crafting table, crafting where the body stands beats
# a walk the planner may never price (see _Exec.craft).
LOCAL_CRAFT_DIST = 8.0
DROP_RADIUS = 10.0
EXPLORE_STEP = 16
SURFACE_SCAN = 24  # how far up/down an explore ring column is read for its surface
EXPLORE_CAP = 64
MAX_IDLE_SLICES = 60  # bound on idle work done per smelt task
IDLE_RADIUS = 24  # smelt idle work: only blocks this close to the furnace
SMELT_TICKS_PER_ITEM = 200
MAX_TRIES = 3
CLIMB_MIN = 4  # explore climbs to the surface when the goal is this far above the body
SCAN_BATCH = 8 # candidates tried per map scan in gather
# A candidate with no reachable stance skips its whole patch (live run
# 20261102T140212509088Z: 12 stuck gotos at one lake-shore sand patch while dry sand
# lay 20 blocks away).
PATCH = 6.0
NEAR_STONE = 24.0  # exposed stone beyond this horizontal distance is not walked to
NEAR_DY = 6  # nor stone this far above or below the bot (ravines, cliffs)
STRIP_UNDERGROUND = 3  # a skipped strip this far under its start resumes in place
MAX_DIGS = 3  # dig-down macros per gather task
FALL_RETRIES = 2  # collect: waits for a still-falling drop before it is given up
DROP_WAIT_TICKS = 40  # one wait for a falling drop to land (2 s)
DROP_ABOVE = 2  # collect: drops higher above the feet than this are not walked to
BURIED_DEPTH = 12  # dig_to_stone: buried stone down to this many blocks below the feet
DIG_CANDIDATES = 3  # buried stone tops offered to the planner, nearest first
DEEP_BELOW = 8  # ore further under the feet than this: staircase down first, no plan
DESCEND_ABOVE = 3  # the staircase stops this far over the ore's top
# A surface block (log, sand, dirt) this far under the feet is not a surface find:
# sensed sections hold buried mineshaft logs 43 below the bot, and every plan to
# them stalled (run 20261116T150256102133Z). Valleys and tall trees stay in range.
SURFACE_BELOW = 12
UNDERGROUND = ("stone", "cobblestone", "deepslate", "andesite", "diorite", "granite")
FOOD_CANDIDATES = 6
FOOD_COUNT = 4
RETRY_REASONS = STANCE_REASONS | {"no_line_of_sight"}
# A placed station whose six neighbours are all non-solid cannot hold: no stance
# reaches it, so rotate to another free cell (run 20261106T182245230212Z ended the
# task retrying one cell three times).
PLACE_ROTATE_REASONS = frozenset({"no_support_face"})
STALL_S = 120.0  # no inventory change for this long ends the run ("stalled")


class GatherBody(BuildBody, Protocol):
    def find_blocks(
        self,
        block: str,
        where: Callable[[Pos], bool] | None = None,
        within: list[tuple[Pos, float]] | None = None,
    ) -> list[Pos]: ...

    def walk_to(self, pos: Pos, radius: float = 1.5) -> bool: ...

    def entities(self) -> list[dict]: ...

    def food(self) -> int: ...

    def wait_ticks(self, ticks: int) -> None: ...

    def craft(self, recipe: str, times: int, table: Pos | None = None) -> Result: ...

    def furnace(self, pos: Pos, action: str, **fields) -> Result: ...

    def equip(self, item: str) -> Result: ...

    def attack_entity(self, entity: str, weapon: str | None = None) -> Result: ...

    def use_entity(self, entity: str, item: str | None = None) -> Result: ...

    def eat(self, item: str | None = None) -> Result: ...

    def container(self, pos: Pos, action: str, items: list[dict] | None = None): ...


@dataclass
class GatherState:
    """What the executor remembers between tasks (and between jobs)."""

    stations: dict[str, Pos] = field(default_factory=dict)
    containers: dict[Pos, dict[str, int]] = field(default_factory=dict)
    last_container: Pos | None = None
    explored: set = field(default_factory=set)
    origin: Pos | None = None
    equipped: set[str] = field(default_factory=set)  # the planner re-lists Equip tasks
    dig_tail: Pos | None = (
        None  # where the last dig-down ended (its staircase is the way)
    )
    dig_dir: tuple[int, int] | None = None
    dig_start_y: int | None = None  # surface height where that staircase began


def hydrate_containers(body, state: GatherState) -> None:
    """Containers the world archive remembers (none: no archive on this body)."""
    places = getattr(body, "places", None)
    if places is not None:
        for pos, contents in places.load_containers().items():
            state.containers.setdefault(pos, contents)


def flush_containers(body, state: GatherState) -> None:
    places = getattr(body, "places", None)
    if places is not None:
        places.save_containers(state.containers)


@dataclass
class GatherReport:
    done: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (task, reason)
    replans: int = 0
    ate: int = 0
    food_targets: dict[str, int] = field(default_factory=dict)
    stop: str = ""  # done | failed | missing | max_tasks | stalled | died
    missing: list[str] = field(default_factory=list)
    state: GatherState = field(default_factory=GatherState)


def _label(task) -> str:
    return (
        type(task).__name__
        + ":"
        + ",".join(
            str(getattr(task, f))
            for f in ("block", "entity_type", "recipe_id", "kind")
            if getattr(task, f, None)
        )
    )


def _dist(a, b) -> float:
    return math.dist(a, b)


def _near(pos, home) -> bool:
    """Within the work site's reach: NEAR_STONE across, NEAR_DY above to BURIED_DEPTH
    below (the window buried_tops offers the planner, so a dug-to block is minable;
    run 20261102T042713034884Z dug to iron 11 below and then skipped it)."""
    return (
        math.hypot(pos[0] - home[0], pos[2] - home[2]) <= NEAR_STONE
        and -NEAR_DY <= home[1] - pos[1] <= BURIED_DEPTH
    )


def _reach(*centres, radius: float = NEAR_STONE) -> list[tuple[Pos, float]]:
    """Scan bound covering ``_near`` of every centre (find_blocks ``within``): the
    body lists only the map sections there, not every stone cell it has seen."""
    return [(c, radius) for c in centres if c is not None]


def _seen_near(pos, body_pos, start_y: int, radius: float) -> bool:
    """Ore vision: horizontally within ``radius`` of the BODY, vertically within the
    sensed band round where the task started (not the current y: no cave ratchet)."""
    return (
        math.hypot(pos[0] - body_pos[0], pos[2] - body_pos[2]) <= radius
        and abs(pos[1] - start_y) <= SENSE_DY
    )


def _by_yield(known: list[Pos], here, need: int) -> list[Pos]:
    """Candidates best first: blocks walked per item gathered, the yield counted in
    the PATCH round the candidate and capped at `need` (a 22-log cluster beats a
    lone tree nearer by). ponytail: straight-line distance, not route cost; a route
    per candidate is too slow. Upgrade: rank the nearest few by planned route."""
    if need <= 1:
        return sorted(known, key=lambda p: _dist(p, here))
    grid: dict[tuple[int, ...], list[Pos]] = {}
    for p in known:
        grid.setdefault(tuple(int(c // PATCH) for c in p), []).append(p)

    def patch(p: Pos) -> int:
        n, cell = 0, [int(c // PATCH) for c in p]
        for d in product((-1, 0, 1), repeat=3):
            for q in grid.get((cell[0] + d[0], cell[1] + d[1], cell[2] + d[2]), ()):
                if _dist(p, q) <= PATCH:
                    n += 1
                    if n >= need:
                        return n
        return n

    return sorted(known, key=lambda p: _dist(p, here) / max(1, patch(p)))


FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _sealed(body, pos: Pos, unknown_ok: bool = False) -> bool:
    """No lava or water (nor, unless ``unknown_ok``, unknown) cell touches ``pos``:
    breaking it opens no flow. Run 20261116T134201634342Z: breaking ore beside water
    flooded a dug pit and drowned the body. Live run 20261102T005434368454Z: mining surface stone beside a lava
    pool killed the bot. Surface blocks allow unknown neighbours: sand's underside is
    rarely sensed (run 20261103T005102941195Z stalled with every candidate refused)."""
    for dx, dy, dz in FACES:
        state = body.world(pos[0] + dx, pos[1] + dy, pos[2] + dz)
        if state is None:
            if unknown_ok:
                continue
            return False
        ident, props = bp.parse_state(state)
        if ident in bp.FLUIDS or props.get("waterlogged") == "true":
            return False
    return True


def buried_tops(
    body,
    block: str,
    here: Pos,
    radius: float = NEAR_STONE,
    up: int = NEAR_DY,
    down: int = BURIED_DEPTH,
) -> list[Pos]:
    """Top cells of ``block`` columns around ``here`` (the work site) within ``radius``
    horizontally, ``up`` above to ``down`` below, with a known dry cell above;
    shallow and near first."""
    found = []
    for x, y, z in body.find_all(block, _reach(here, radius=radius)):
        depth = here[1] - y
        if not -up <= depth <= down:
            continue
        if math.hypot(x - here[0], z - here[2]) > radius:
            continue
        above = body.world(x, y + 1, z)
        if above is None or bp.parse_state(above)[0] in (block, *bp.FLUIDS):
            continue
        found.append((x, y, z))
    return sorted(found, key=lambda p: abs(here[1] - p[1]) + _dist(p[::2], here[::2]))


def _floored(body, pos: Pos, here: Pos) -> bool:
    """The block under the bot's feet may be mined only onto known solid ground one
    below it (Baritone MovementDownward: a drop of exactly one, never into a cave)."""
    if pos != (here[0], here[1] - 1, here[2]):
        return True
    below = body.world(pos[0], pos[1] - 1, pos[2])
    return below is not None and bp.parse_state(below)[0] not in (*bp.AIR, *bp.FLUIDS)


def _cell(position) -> Pos:
    return (
        math.floor(position[0]),
        math.floor(position[1] + 1e-6),
        math.floor(position[2]),
    )


def _held(inv: dict[str, int], suffix: str) -> str | None:
    return next(
        (i for i, n in sorted(inv.items()) if n > 0 and i.endswith(suffix)), None
    )


class _Exec:
    """One run: the body, the knowledge and the shared state."""

    def __init__(
        self,
        body: GatherBody,
        k: Knowledge,
        state: GatherState,
        cap: int,
        stall_s: float = STALL_S,
        clock: Callable[[], float] = time.monotonic,
        idle_work: Callable[[], None] | None = None,
        opts: GatherOptions | None = None,
    ):
        self.body, self.k, self.state, self.cap = body, k, state, cap
        self.opts = opts or GatherOptions()
        self.start_y = int(body.position()[1])  # reset at each gather task's start
        self.last_dig = ""
        self.dig_skip: set[Pos] = set()  # ore tops whose one plan attempt failed
        self.plan_ores: set[str] = set()  # ores the current plan still gathers
        self.stall_s, self.clock = stall_s, clock
        self.idle_work = idle_work
        self.idle_reason = ""
        self._seen, self._since = dict(body.inventory()), clock()

    def stalled(self) -> bool:
        """True once the inventory has not changed for ``stall_s`` seconds."""
        inv = self.body.inventory()
        if inv != self._seen:
            self._seen, self._since = dict(inv), self.clock()
        return self.clock() - self._since > self.stall_s

    # --- shared helpers -------------------------------------------------
    def reach_and_do(self, pos: Pos, do: Callable[[], Result], reach=REACH) -> Result:
        """Stance, act; other stances while the failure says wrong stance."""
        avoid: set[Pos] = set()
        result = Result(False, "no_stance")
        for _ in range(MAX_TRIES):
            if self.stalled():
                return Result(False, "stalled")
            if not self.body.goto_stance(pos, reach, avoid):
                return Result(False, "no_stance")
            stance = self.body.position()
            result = do()
            if result.ok or result.reason not in RETRY_REASONS:
                return result
            avoid.add(stance)
        return result

    def explore(self, found: Callable[[], bool]) -> bool:
        """Walk expanding rings round the first position until ``found()``.
        ponytail: same ring for every ``where``; underground sources are not dug for."""
        st = self.state
        st.origin = st.origin or self.body.position()
        ox, oy, oz = st.origin
        climbed = False
        for radius in range(EXPLORE_STEP, self.cap + 1, EXPLORE_STEP):
            for angle in range(0, 360, 45):
                key = (radius, angle)
                if key in st.explored:
                    continue
                if self.stalled():
                    return False
                st.explored.add(key)
                rad = math.radians(angle)
                x = ox + round(radius * math.cos(rad))
                z = oz + round(radius * math.sin(rad))
                goal = (x, self._surface_y(x, z, oy), z)
                allow_swim = getattr(
                    getattr(self.body, "movement_policy", None), "allow_swim", False
                )
                surface = self.body.world(x, goal[1] - 1, z)
                if (
                    not allow_swim
                    and surface is not None
                    and bp.parse_state(surface)[0] in bp.FLUIDS
                ):
                    continue
                if not climbed and goal[1] - self.body.position()[1] > CLIMB_MIN:
                    # a surface goal from inside a pit costs hundreds of plans
                    # (run 20261113T202531741432Z): climb out first
                    climbed = True
                    digger = Digger(self)
                    if not digger.open_sky():
                        digger.climb()  # failed: the walk below reports it
                arrived = self.body.walk_to(goal, 2.0)
                if arrived:
                    # An arrived ring leg is progress even when the inventory is
                    # quiet: two long surface walks between veins crossed the 120 s
                    # watchdog and ended a healthy sweep at its last legs (iron run
                    # 20261113T012352828934Z, stop=stalled at radius 64). A stuck
                    # walk never arrives, so the treeless-shore guard stands.
                    self._seen, self._since = dict(self.body.inventory()), self.clock()
                # a leg that did not arrive is a failure, not a success (the strip's
                # restart passes a bare True): try the next leg
                if arrived and found():
                    return True
        return False

    def _surface_y(self, x: int, z: int, base: int) -> int:
        """Ring points go on top of their own column: the origin plane can be many
        blocks under the surface the ring is exploring (large-house attempt 6 walked
        every point at feet y=57, inside solid ground, and stuck on the optimistic
        route down to it). Unknown cells keep the base height."""
        for y in range(base + SURFACE_SCAN, base - SURFACE_SCAN, -1):
            state = self.body.world(x, y, z)
            if state is None:
                continue
            ident = bp.parse_state(state)[0]
            if ident in bp.AIR or is_leaf(ident):
                continue
            return y + 1
        return base

    def home(self) -> Pos:
        """The work site: the crafting table once placed, else where the bot stands.
        Underground blocks are judged near from here, not from the bot, so a gather
        cannot ratchet itself down a cave (run 20261102T040249954439Z: y 64 to 53)."""
        table = self.state.stations.get("crafting_table")
        return tuple(table) if table else self.body.position()

    def note(self, what: str, **fields) -> None:
        """One progress row in the body's log (the run's record), events and stdout."""
        row = {"gather": what, **fields}
        if (log := getattr(self.body, "log", None)) is not None:
            log.append(row)
        if (bridge := getattr(self.body, "bridge", None)) is not None and hasattr(
            bridge, "record"
        ):
            bridge.record("gather_note", row)
        print(row, flush=True)

    def fell_tree(
        self, pos: Pos, tool: str | None, items: Collection[str], skip: set[Pos]
    ) -> int | None:
        """Fell the whole tree holding the log ``pos`` with the site-clearing feller,
        past the need (no floating tops), and pick up its drops. Returns the logs
        felled, or None for a huge or unknown tree: the caller then breaks logs one
        at a time and stops at the need. Logs left standing are noted and skipped."""
        body = self.body
        tree = tree_at(body.world, pos)
        if tree is None or is_huge(tree) or (len(tree.all_logs) <= 1 and not tree.leaves):
            return None
        logs = tree.all_logs
        TreeFeller(body).fell(tree, tool)
        left = {p for p in logs if is_log(body.world(*p))}
        self.collect(items, tree.base, None)
        if left:
            skip.update(left)
            self.note("tree_incomplete", base=tree.base, left=sorted(left))
        return len(logs) - len(left)

    def stair_down(self, ore: Pos) -> bool:
        """Ore more than DEEP_BELOW under the feet: staircase to just above it first.
        True when the ore is now within planning range (one short plan, not a capped one)."""
        if self.body.position()[1] - ore[1] > DEEP_BELOW:
            Digger(self).descend(ore[1] + DESCEND_ABOVE, (ore[0], ore[2]))
        return self.body.position()[1] - ore[1] <= DEEP_BELOW

    def dig_to_stone(
        self, block: str, ignore_stair: bool = False, vision: bool = False
    ) -> bool:
        """Baritone-style first stone: let the planner dig down to stand on the top of
        known buried ``block`` (the sensor delivers whole sections, buried cells too).
        Its cut rules refuse cells beside fluid or unknown cells, and the way back is
        a planned route. Nearest and shallowest first; the planner judges the rest."""
        dig_to = getattr(self.body, "dig_to", None)
        # an earlier staircase is the way down to stone (not to ore)
        if dig_to is None or (self.state.dig_tail and not ignore_stair):
            return False
        if vision:  # body-anchored, vertical band round the task's start height
            bx, _, bz = self.body.position()
            tops = buried_tops(
                self.body,
                block,
                (math.floor(bx), self.start_y, math.floor(bz)),
                self.opts.radius,
                SENSE_DY,
                SENSE_DY,
            )
        else:
            tops = buried_tops(self.body, block, self.home())
        here = self.body.position()
        tops = sorted(
            (t for t in tops if t not in self.dig_skip), key=lambda p: _dist(p, here)
        )
        for top in tops[:DIG_CANDIDATES]:
            if top in self.dig_skip:  # its vein failed a moment ago
                continue
            # Deep ore: a player stairs down first. One plan over 30+ blocks of stone
            # always hit the node cap (run 20261113T154142741359Z: 26 plans, 35 s still).
            before = _dist(self.body.position(), top)
            if self.stair_down(top) and dig_to((top[0], top[1] + 1, top[2])):
                return True
            if _dist(self.body.position(), top) < before - 1:
                break  # a partial walk grew the map: the next dig may see more (MAX_DIGS bounds it)
            self.dig_skip.update(p for p in tops if _dist(p, top) <= PATCH)
        return False

    def dig_for(self, item: str, target: int) -> int:
        """No usable stone: dig a player-style staircase (``gather_dig``) until
        ``target`` of ``item`` is held. Returns the blocks broken; ``last_dig`` says
        why it ended."""
        digger = Digger(self)
        digger.run(item, target)
        self.last_dig = digger.reason
        return digger.broken

    def collect(
        self,
        items: Collection[str] | None,
        near,
        enough: Callable[[], bool] | None = None,
    ) -> None:
        """Walk onto the dropped item entities near ``near`` (None: any item)."""
        skipped: set[str] = set()
        falling: dict[str, int] = {}  # uuid -> pickup tries lost to its fall
        later: set[str] = set()  # still falling: other drops first, then a wait
        for _ in range(8):
            if enough is not None and enough():  # no pit-digging for spare drops
                return
            if self.stalled():
                return
            here = self.body.position()
            drops = [
                e
                for e in self.body.entities()
                if e.get("item")
                and (items is None or e["item"] in items)
                and e.get("uuid") not in skipped
                and _dist(e["position"], near) <= DROP_RADIUS
                # ponytail: a drop resting on leaves is out of walking reach (live
                # runs: 10 s "stuck" gotos to y+5..7); it may fall later and is lost.
                and e["position"][1] - here[1] <= DROP_ABOVE
            ]
            if not drops:
                return
            ready = [e for e in drops if e.get("uuid") not in later]
            if not ready:  # only falling items left: let them land, then retry
                self.body.wait_ticks(DROP_WAIT_TICKS)
                later.clear()
                continue
            e = min(ready, key=lambda e: _dist(e["position"], here))
            pickup = getattr(self.body, "walk_to_drop", self.body.walk_to)
            got = pickup(_cell(e["position"]), 0.8)
            uuid = e.get("uuid")
            if got is None:  # ponytail: an item stuck above its ground is dropped after
                falling[uuid] = falling.get(uuid, 0) + 1  # FALL_RETRIES waits
                (skipped if falling[uuid] > FALL_RETRIES else later).add(uuid)
            elif not got:
                skipped.add(uuid)

    def free_spot(self, skip: Collection[Pos] = (), *, center: Pos | None = None) -> Pos | None:
        here = self.body.position()
        x, y, z = center if center is not None else here
        offsets = sorted(
            product(range(-2, 3), repeat=2), key=lambda d: abs(d[0]) + abs(d[1])
        )
        for dx, dz in offsets:
            c = (x + dx, y, z + dz)
            if c in skip:
                continue
            if c == here or self.body.world(*c) not in bp.AIR:
                continue
            below = self.body.world(c[0], c[1] - 1, c[2])
            above = self.body.world(c[0], c[1] + 1, c[2])
            if (
                below is not None
                and bp.parse_state(below)[0] not in bp.REPLACEABLE
                and above in bp.AIR
            ):
                return c
        return None

    def station_pos(self, kind: str) -> Pos | None:
        known = self.state.stations.get(kind)
        if known:
            return known
        near = self.body.find_blocks("minecraft:" + kind)
        here = self.body.position()
        return min(near, key=lambda p: _dist(p, here)) if near else None

    def place_free(
        self, state: str, item: str, *, center: Pos | None = None, tried=()
    ) -> tuple[Result, Pos | None]:
        tried = set(tried)
        worksite = center  # tried first; no free spot there: the usual search
        while True:
            spot = self.free_spot(tried, center=worksite)
            if spot is None:
                if center is not None:
                    center = worksite = None
                    continue
                # Search at the table's base level, not the top of the table:
                # walking to the table itself can arrive standing on top of it.
                if worksite is not None:
                    return Result(False, "no_free_spot"), None
                worksite = self.state.stations.get("crafting_table")
                if worksite is None:
                    return Result(False, "no_free_spot"), None
                continue
            tried.add(spot)
            result = self.reach_and_do(
                spot, lambda p=spot: self.body.place_block(p, state, item)
            )
            # the body refuses a cell whose six neighbours are all non-solid: a
            # different stance cannot place into it (run 20261106T182245230212Z
            # ended the task retrying the same cell 3x)
            if result.ok or result.reason not in PLACE_ROTATE_REASONS:
                return result, spot

    # --- tasks ----------------------------------------------------------
    def harvests(self, t) -> bool:
        """A held tool of the task's kind and drop tier ("hand" tiers need none)."""
        if not t.tool_kind or t.min_tier == "hand":
            return True
        need = TIER_RANK.get(t.min_tier, UNKNOWN_TIER_RANK)
        return any(
            n > 0
            and (tool := self.k.items.get(item, {}).get("tool"))
            and tool["kind"] == t.tool_kind
            and TIER_RANK.get(tool.get("tier", ""), UNKNOWN_TIER_RANK) >= need
            for item, n in self.body.inventory().items()
        )

    def gather(self, t, *, near: Pos | None = None, radius: float = IDLE_RADIUS) -> Result:
        body = self.body
        self.start_y = int(body.position()[1])
        self.dig_skip = set()
        # Interchangeable drops (any log for planks) count together and are searched
        # together; `t.item` alone when the plan named no stand-ins.
        items = {t.item, *(i for i, _ in t.alternatives)}
        blocks = [t.block, *(b for _, b in t.alternatives)]

        def held() -> int:
            inv = body.inventory()
            return sum(inv.get(i, 0) for i in items)

        target = held() + max(1, t.item_count)
        broken, skip, digs, stripped = 0, set(), 0, 0
        strip_ty: int | None = None
        tried_sights: set[Pos] = set()
        # Where exploring last saw ore: near is judged from there too, or ore past
        # the table's reach is walked by and never mined (run 20261112T181731902927Z
        # explored for seven minutes). Ore only: stone is everywhere, and judging it
        # from the bot ratchets a gather down a cave.
        found_at: Pos | None = None
        name = t.block.removeprefix("minecraft:")
        ore = name.endswith("_ore")
        underground = name in UNDERGROUND or ore
        vision = ore and self.opts.ore_vision and near is None
        radius_v = self.opts.radius
        while broken < t.count and held() < target:
            if self.stalled():
                return Result(False, "stalled")
            if not self.harvests(t):
                # The tool broke mid-task: craft one here from the carried kit (live
                # runs 20261113T174328610209Z, 20261113T175858389732Z climbed or sat).
                # Else never hand-tunnel toward ore the hand cannot mine: the replan
                # crafts new tools; the way up to wood is a planned route, dug by
                # hand if it must be (body_search.edit_payload).
                if not (craft_underground(self, t) and self.harvests(t)):
                    return Result(False, "missing_tool", {"broken": broken})
            here, home = body.position(), self.home()
            if vision:
                home = _cell(here)  # ore is judged from the body, not the table
            # Never a block touching water or lava, surface ones included: sand dug out
            # of a shore let water into the pit (run 20261103T003648615312Z drowned).
            # The cheap position tests go first: the open-face test on every stone
            # cell of the map was 2.5-2.9 s of silence per call (run
            # 20261112T180613337809Z). Underground: only stone the bot can
            # plausibly walk to; never a ravine floor.
            known = [
                p
                for b in blocks
                for p in body.find_blocks(
                    b,
                    lambda p, home=home, seen=found_at: p not in skip
                    and (near is None or _dist(p, near) <= radius)
                    and (
                        (not underground and here[1] - p[1] <= SURFACE_BELOW)
                        or underground and (
                            _seen_near(p, home, self.start_y, radius_v)
                            if vision
                            else _near(p, home)
                            or (seen is not None and _near(p, seen))
                        )
                    ),
                    [(near, radius)]
                    if near is not None
                    else _reach(home, radius=radius_v)
                    if vision
                    else _reach(home, found_at)
                    if underground
                    else None,
                )
                if _sealed(body, p) and (not underground or _floored(body, p, here))
            ]
            places = getattr(body, "places", None)
            if known and places is not None:  # remember the nearest finds
                finds = sorted(known, key=lambda p: _dist(p, here))[:SCAN_BATCH]
                places.note_sightings(t.item, finds)
            elif places is not None and (
                seen := places.nearest_sighting(t.item, here, tried_sights)
            ):
                # a stored sighting beats exploring; it is forgotten once visited,
                # so a block that is gone does not draw the bot back
                tried_sights.add(seen)
                places.forget_sighting(t.item, seen)
                body.walk_to(seen, 3.0)
                continue
            # ore vision off: strip-mine at the ore's height. Never dig_to_stone here:
            # it reads buried blocks (x-ray). A descent that fell short retries from a
            # walked-to start (run 20261113T172130173999Z: never logged a leg).
            if not known and ore and not vision and self.cap:
                if stripped >= MAX_DIGS:
                    return Result(False, f"descent_failed:{self.last_dig}", {"broken": broken})
                stripped += 1
                from mclab.gather_strip import Strip

                before = body.inventory().get(t.item, 0)
                strip = Strip(self, t, target, strip_ty)
                res = strip.run()
                strip_ty = strip.ty
                if res is not None:
                    broken += max(0, body.inventory().get(t.item, 0) - before)
                    if res.reason == "missing_tool":
                        return Result(False, "missing_tool", {"broken": broken})
                    if body.inventory().get(t.item, 0) < target:
                        # no fallback to planned digs or surface rings after a strip;
                        # a strip that dug no tunnel says why, not "leg_limit"
                        why = "leg_limit" if res.data.get("tunnelled") else res.data["stop"]
                        return Result(False, why, {"broken": broken})
                    continue
                # Already under the surface (run 20261116T135356630645Z): resume from
                # here; surface rings would walk the descent back up.
                if strip_ty is not None and body.position()[1] <= self.start_y - STRIP_UNDERGROUND:
                    continue
                if not self.explore(lambda: True):  # a short walk to another start
                    return Result(False, f"descent_failed:{self.last_dig}", {"broken": broken})
                continue
            # cap 0 (a build's refill) stays home: no mine under the site, no ring walk
            if not known and underground and digs < MAX_DIGS and self.cap:
                digs += 1
                before = body.inventory().get(t.item, 0)
                if ore:  # a blind tunnel rarely meets ore: only a planned dig to it
                    # A failed candidate still walked a partial route and
                    # delivered its sections, so the next attempt sees more map:
                    # the MAX_DIGS budget retires digging, not one bad verdict
                    # (iron run 20261113T014723010253Z: one incomplete leg
                    # retired all digging and the sweep ended not_found).
                    self.dig_to_stone(t.block, ignore_stair=True, vision=vision)
                else:  # else the staircase starts at the surface
                    self.dig_to_stone(t.block)
                    self.dig_for(t.item, target)
                # dirt broken on the way down is not progress toward t.count
                broken += max(0, body.inventory().get(t.item, 0) - before)
                continue
            if not known:
                if not self.explore(
                    lambda: any(
                        body.find_blocks(
                            b,
                            lambda q: q not in skip
                            and (
                                not underground
                                or (
                                    _seen_near(
                                        q, body.position(), self.start_y, radius_v
                                    )
                                    if vision
                                    else _near(q, self.home())
                                    or (ore and _near(q, _cell(body.position())))
                                )
                            ),
                            _reach(body.position(), radius=radius_v)
                            if vision
                            else _reach(self.home(), _cell(body.position()))
                            if underground
                            else None,
                        )
                        for b in blocks
                    )
                ):
                    return Result(
                        broken > 0, None if broken else "not_found", {"broken": broken}
                    )
                found_at = _cell(body.position()) if ore else None
                continue
            # Several nearest per scan: a scan of a big map costs seconds, and most
            # near candidates (cliff faces, ravine walls) have no stance.
            for pos in _by_yield(known, here, target - held())[:SCAN_BATCH]:
                if pos in skip:  # its patch was skipped earlier in this batch
                    continue
                # Deep known ore: stair down, then plan once. Planning 20-40 blocks of
                # stone hit the 10000-node cap ~25 times (run 20261113T171138236559Z).
                if ore and self.cap and not self.stair_down(pos):
                    skip.update(p for p in known if _dist(p, pos) <= PATCH)
                    continue
                tool = pick_tool(self.k, body.inventory(), t.block, self.opts.tool_choice)
                if is_log(body.world(*pos)):
                    # a log is a whole tree: fell it all before any other candidate
                    # (live run: six trees, a floating top left at each)
                    felled = self.fell_tree(pos, tool, items, skip)
                    if felled is not None:
                        broken += felled
                        break
                result = self.reach_and_do(
                    pos, lambda p=pos, tl=tool: body.break_block(p, tl)
                )
                if result.ok:
                    broken += 1
                    self.collect(items, pos, lambda: held() >= target)
                    break
                skip.add(pos)
                if result.reason == "no_stance":
                    skip.update(p for p in known if _dist(p, pos) <= PATCH)
                if result.reason in ("wrong_tool", "missing_tool"):
                    # make the tool where it stands; the climb is for when it cannot
                    if underground and craft_underground(self, t):
                        break
                    return result
        return Result(broken > 0, None if broken else "nothing_mined", {"broken": broken})

    def craft(self, t) -> Result:
        table = None
        if t.station == "crafting_table":
            table = self.station_pos("crafting_table")
            if table is None:
                return Result(False, "no_table")
            # A table far away or a floor up is a walk the plan cannot price; the
            # held stock alone may make the item right here (20261114T201928310227Z:
            # 430 s livelocked below a shaft for the surface table).
            if (
                math.dist(_cell(self.body.position()), table) > LOCAL_CRAFT_DIST
                and not getattr(self, "_local_craft", False)
                and craft_local(self, t.result, max(1, t.result_count))
            ):
                return Result(True)
            if not self.body.goto_stance(table, STATION_REACH):
                return Result(False, "no_stance")
        return self.body.craft(t.recipe_id, t.times, table)

    def smelt(self, t) -> Result:
        pos = self.station_pos("furnace")
        if pos is None:
            return Result(False, "no_furnace")
        if not self.body.goto_stance(pos, STATION_REACH):
            return Result(False, "no_stance")
        loaded = self.body.furnace(
            pos,
            "load",
            input=t.ingredient,
            count=t.count,
            fuel=t.fuel,
            fuel_count=t.fuel_count,
        )
        if not loaded.ok:
            return loaded
        want = self.body.inventory().get(t.result, 0) + t.result_count

        # Keep moving while the furnace works: idle_work() does one bounded slice of
        # useful work (clearing trees, cutting the build site) and returns True while
        # more remains. Come back to collect every few slices, never stand and wait
        # while there is something to do.
        slices, loaded_at = 0, self.clock()
        self.idle_reason = ""
        while self.idle_work and slices < MAX_IDLE_SLICES:
            slices += 1
            if not self.idle_work():
                break
            # back to the furnace only when the batch can be done, not every few
            # slices: that walk was the bot leaving a half-cleared tree
            if self.clock() - loaded_at >= t.count * SMELT_TICKS_PER_ITEM / 20:
                if not self.body.goto_stance(pos, STATION_REACH):
                    break
                self.body.furnace(pos, "collect")
                if self.body.inventory().get(t.result, 0) >= want:
                    return Result(True)
        if not self.body.goto_stance(pos, STATION_REACH):
            return Result(False, "no_stance")

        why = (
            "no_idle_work_configured"
            if self.idle_work is None
            else f"idle_work_slice_limit:{MAX_IDLE_SLICES}"
            if slices >= MAX_IDLE_SLICES
            else self.idle_reason
            or f"{getattr(self.idle_work, '__name__', 'idle_work')}_done"
        )
        for _ in range(2 * t.count + 4):
            self.body.furnace(pos, "collect")
            if self.body.inventory().get(t.result, 0) >= want:
                return Result(True)
            held = self.body.inventory().get(t.result, 0)
            remaining = max(1, want - held)
            wait = min(SMELT_TICKS_PER_ITEM * remaining, 200)
            self.note(
                "smelt_wait",
                item=t.result,
                ingredient=t.ingredient,
                remaining=remaining,
                want=want,
                ticks=wait,
                at=pos,
                why=why,
            )
            self.body.wait_ticks(wait)
        return Result(False, "smelt_incomplete")

    def place_station(self, t) -> Result:
        item = "minecraft:" + t.kind
        result, spot = self.place_free(item, item)
        if result.ok:
            self.state.stations[t.kind] = spot
        return result

    def transform(self, t) -> Result:
        for _ in range(t.count):
            result, spot = self.place_free(t.input, t.input)
            if not result.ok:
                return result
            tool = _held(self.body.inventory(), "_" + t.tool_kind)
            if tool is None:
                return Result(False, "missing_tool")
            result = self.reach_and_do(
                spot, lambda s=spot, t=tool: self.body.use_block(s, t)
            )
            if result.ok:
                result = self.reach_and_do(
                    spot, lambda s=spot: self.body.break_block(s)
                )
            if not result.ok:
                return result
            self.collect({t.output}, spot)
        return Result(True)

    def mob(self, t, shear: bool) -> Result:
        body, done, fails, used = self.body, 0, 0, set()

        def mobs():
            return [
                e
                for e in body.entities()
                if e.get("type") == t.entity_type and e["uuid"] not in used
            ]

        while done < t.count and fails < MAX_TRIES * 2:
            near = mobs()
            if not near:
                if not self.explore(lambda: bool(mobs())):
                    break
                continue
            here = body.position()
            e = min(near, key=lambda e: _dist(e["position"], here))
            body.walk_to(_cell(e["position"]), 2.0)
            if shear:
                shears = _held(body.inventory(), "shears")
                result = body.use_entity(e["uuid"], shears)
            else:
                result = body.attack_entity(e["uuid"], getattr(t, "weapon", None))
            if result.ok:
                done += 1
                used.add(e["uuid"])
                self.collect(None, e["position"])
            elif result.reason == "no_effect":
                used.add(e["uuid"])  # already sheared
            elif result.reason in ("out_of_reach", "no_line_of_sight", "not_found"):
                fails += 1  # animals move: read the entities again and re-approach
            else:
                used.add(e["uuid"])
                fails += 1
        return Result(done > 0, None if done else "no_target")

    def place_container(self, t) -> Result:
        item = "minecraft:" + t.kind
        # Next to the known chests but never touching one: two touching chests
        # merge into one double chest, which this state would count twice.
        known = list(self.state.containers)
        touching = {
            (x + dx, y, z + dz)
            for x, y, z in known
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
        }
        result, spot = self.place_free(
            item, item, center=known[0] if known else None, tried=touching
        )
        if result.ok:
            self.state.containers[spot] = {}
            self.state.last_container = spot
            flush_containers(self.body, self.state)
        return result

    def container(self, t, action: str) -> Result:
        pos = t.pos or self.state.last_container
        if pos is None and self.state.containers:
            pos = next(iter(self.state.containers))
        if pos is None:
            return Result(False, "no_container")
        if not self.body.goto_stance(pos, STATION_REACH):
            return Result(False, "no_stance")
        items = [{"item": i, "count": n} for i, n in t.items] or None
        result = self.body.container(pos, action, items)
        if result.ok:
            contents: dict[str, int] = {}
            for stack in result.data.get("contents") or ():
                contents[stack["item"]] = (
                    contents.get(stack["item"], 0) + stack["count"]
                )
            self.state.containers[pos] = contents
            self.state.last_container = pos
            flush_containers(self.body, self.state)
        return result

    def run(self, t) -> Result:
        name = type(t).__name__
        if name == "Gather":
            return self.gather(t)
        if name == "Craft":
            return self.craft(t)
        if name == "Smelt":
            return self.smelt(t)
        if name == "Equip":
            result = self.body.equip(t.item)
            if result.ok:
                self.state.equipped.add(t.item)
            return result
        if name == "PlaceStation":
            return self.place_station(t)
        if name == "Transform":
            return self.transform(t)
        if name in ("Hunt", "Shear"):
            return self.mob(t, name == "Shear")
        if name == "PlaceContainer":
            return self.place_container(t)
        if name in ("Deposit", "Withdraw"):
            return self.container(t, name.lower())
        return Result(False, f"no_handler:{name}")


def _food_target(k: Knowledge, inventory, stations) -> dict[str, int] | None:
    """The best food (by nutrition) the planner can reach from this inventory."""
    ranked = sorted(k.foods, key=lambda i: -float(k.foods[i].get("nutrition", 0)))
    for item in ranked[:FOOD_CANDIDATES]:
        result = gp.plan({item: FOOD_COUNT}, inventory, k, stations)
        if result.ok and result.tasks:
            return {item: FOOD_COUNT}
    return None


def _hunger(ex: _Exec, report: GatherReport, targets: dict | None) -> None:
    body, k = ex.body, ex.k
    food = body.food()
    held = [i for i, n in body.inventory().items() if n > 0 and i in k.foods]
    if food <= EAT_BELOW and held:
        best = max(held, key=lambda i: float(k.foods[i].get("nutrition", 0)))
        if body.eat(best).ok:
            report.ate += 1
    elif (
        food <= HUNT_BELOW
        and not held
        and targets is not None
        and not report.food_targets
    ):
        wanted = _food_target(k, body.inventory(), ex.state.stations)
        if wanted:
            report.food_targets = wanted
            targets.update(wanted)


# Failures that, underground, mean the body cannot make what it needs where it stands
# (a tool broke and the kit cannot replace it; no stance): the way up to wood. A failed
# walk or dig is not one: the task's own failure limit handles those.
ESCAPE_REASONS = frozenset({"missing_tool", "wrong_tool", "no_stance"})


def _buried(reason: str | None) -> bool:
    return (reason or "") in ESCAPE_REASONS


def _escape(ex: _Exec, report: GatherReport) -> bool:
    """Buried after a failure: staircase up to open sky, by hand if need be."""
    digger = Digger(ex)
    if digger.open_sky():
        return False
    ok = digger.climb()
    report.failures.append(("escape", digger.reason))
    ex._seen, ex._since = dict(ex.body.inventory()), ex.clock()  # climbing is progress
    return ok


def run_gather(
    plan_result,
    body: GatherBody,
    knowledge: Knowledge,
    targets: dict[str, int] | None = None,
    *,
    state: GatherState | None = None,
    max_failures: int = 3,
    max_tasks: int = 300,
    explore_cap: int = EXPLORE_CAP,
    starter_tools: bool = True,
    stall_s: float | None = None,  # None: STALL_S
    clock: Callable[[], float] = time.monotonic,
    storage: Callable[[], list] | None = None,
    idle_work: Callable[[], None] | None = None,
    opts: GatherOptions | None = None,
) -> GatherReport:
    """Run the plan's tasks through ``body``; see the module docstring.

    ``storage`` (a callable built fresh at every re-plan) lists the known
    containers, so re-plans withdraw from them instead of mining the item again.
    """
    if state is None:
        state = GatherState()
        hydrate_containers(body, state)
    report = GatherReport(state=state)
    stall = STALL_S if stall_s is None else stall_s
    ex = _Exec(body, knowledge, state, explore_cap, stall, clock, idle_work=idle_work, opts=opts)
    targets = dict(targets) if targets is not None else None
    tasks = list(plan_result.tasks)

    spares = [
        replace(t, count=1, item_count=1, margin=0)
        for t in plan_result.tasks
        if type(t).__name__ == "Gather"
    ]

    def _gather_ahead() -> bool:
        """Smelt idle work for a pure gather run: the plan's next Gather task, if
        it heads the queue (so it cannot need the smelt output), else one spare
        block of something the plan gathered; only blocks near the furnace. Else
        pick up drops round the furnace. False: nothing."""
        furnace = state.stations.get("furnace")
        if furnace is None:
            ex.idle_reason = "no_furnace_station"
            return False
        ahead = tasks[:1] if tasks and type(tasks[0]).__name__ == "Gather" else []
        for task in ahead + spares:
            if not body.find_blocks(
                task.block,
                lambda p: _dist(p, furnace) <= IDLE_RADIUS,
                [(furnace, IDLE_RADIUS)],
            ):
                continue
            cap, ex.cap = ex.cap, 0  # ponytail: no ring walk or dig while waiting
            before_count = body.inventory().get(task.item, 0)
            try:
                result = ex.gather(task, near=furnace, radius=IDLE_RADIUS)
            finally:
                ex.cap = cap
            if result.ok and ahead and task is ahead[0]:
                broken = result.data["broken"]
                gained = max(0, body.inventory().get(task.item, 0) - before_count)
                if broken >= task.count or gained >= max(1, task.item_count):
                    tasks.pop(0)
                else:
                    tasks[0] = replace(
                        task,
                        count=task.count - broken,
                        item_count=max(1, task.item_count - gained),
                        margin=min(task.margin, task.count - broken),
                    )
                return True
            if result.ok:
                return True
        before = dict(body.inventory())
        ex.collect(None, furnace)
        if body.inventory() != before:
            return True
        next_task = _label(tasks[0]) if tasks else "none"
        checked = sorted({task.block for task in ahead + spares})
        ex.idle_reason = (
            f"no_open_face_blocks_within_{IDLE_RADIUS}_or_drops"
            f"(next={next_task},checked={checked})"
        )
        return False

    if idle_work is None:
        ex.idle_work = _gather_ahead
    def _note_plan() -> None:
        names = {type(t).__name__ for t in tasks}
        ex.plan_ores = {
            t.block for t in tasks if type(t).__name__ == "Gather" and t.block.endswith("_ore")
        }
        if "Smelt" in names:
            ex.plan_ores.add("minecraft:coal_ore")  # the furnace's fuel, taken along the strip

    _note_plan()
    try:
        failures = executed = 0
        last_failed = None
        while tasks and executed < max_tasks:
            task = tasks.pop(0)
            executed += 1
            _hunger(ex, report, targets)
            result = ex.run(task)
            if result.ok:
                failures = 0
                report.done.append(_label(task))
            else:
                # only consecutive failures of one task count: a failed task that the
                # re-plan then gets past is not a strike against the next one
                failures = failures + 1 if _label(task) == last_failed else 1
                last_failed = _label(task)
                report.failures.append((_label(task), result.reason or "failed"))
                if _buried(result.reason) and _escape(ex, report):
                    failures = 0  # out under the sky: the re-plan below starts afresh
                if ex.stalled():
                    report.stop = "stalled"
                    return report
                if failures >= max_failures:
                    report.stop = "failed"
                    return report
            if targets is not None:
                fresh = gp.plan(
                    targets,
                    body.inventory(),
                    knowledge,
                    state.stations,
                    starter_tools=starter_tools,
                    storage=storage() if storage else None,
                    kit=opts,
                )
                report.replans += 1
                tasks = [
                    t
                    for t in fresh.tasks
                    if not (type(t).__name__ == "Equip" and t.item in state.equipped)
                ]
                report.missing = [m.item for m in fresh.missing]
                _note_plan()
                if not tasks and fresh.missing:
                    report.stop = "missing"
                    return report
        report.stop = "max_tasks" if tasks else "done"
        return report
    except BodyDied:
        report.stop = "died"  # the body has no position: dead
        return report
