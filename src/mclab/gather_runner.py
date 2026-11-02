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
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from itertools import product
from typing import Protocol

from mclab import build_plan as bp
from mclab import gather_plan as gp
from mclab.build_runner import REACH, STANCE_REASONS, BuildBody, Result
from mclab.gather_dig import Digger
from mclab.knowledge import Knowledge

Pos = tuple[int, int, int]

EAT_BELOW = 14
HUNT_BELOW = 10
STATION_REACH = 3.5
DROP_RADIUS = 10.0
EXPLORE_STEP = 16
EXPLORE_CAP = 64
SMELT_TICKS_PER_ITEM = 200
MAX_TRIES = 3
SCAN_BATCH = 8  # candidates tried per map scan in gather
NEAR_STONE = 24.0  # exposed stone beyond this horizontal distance is not walked to
NEAR_DY = 6  # nor stone this far above or below the bot (ravines, cliffs)
MAX_DIGS = 3  # dig-down macros per gather task
DROP_ABOVE = 2  # collect: drops higher above the feet than this are not walked to
BURIED_DEPTH = 12  # dig_to_stone: buried stone down to this many blocks below the feet
DIG_CANDIDATES = 3  # buried stone tops offered to the planner, nearest first
UNDERGROUND = ("stone", "cobblestone", "deepslate", "andesite", "diorite", "granite")
FOOD_CANDIDATES = 6
FOOD_COUNT = 4
RETRY_REASONS = STANCE_REASONS | {"no_line_of_sight"}


class GatherBody(BuildBody, Protocol):
    def find_blocks(self, block: str) -> list[Pos]: ...

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


@dataclass
class GatherReport:
    done: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (task, reason)
    replans: int = 0
    ate: int = 0
    food_targets: dict[str, int] = field(default_factory=dict)
    stop: str = ""  # done | failed | missing | max_tasks
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


def _near(pos, here) -> bool:
    return (
        math.hypot(pos[0] - here[0], pos[2] - here[2]) <= NEAR_STONE
        and abs(pos[1] - here[1]) <= NEAR_DY
    )


FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _sealed(body, pos: Pos) -> bool:
    """No liquid or unknown cell touches ``pos``: breaking it opens no flow. Live run
    20261102T005434368454Z: mining surface stone beside a lava pool killed the bot."""
    for dx, dy, dz in FACES:
        state = body.world(pos[0] + dx, pos[1] + dy, pos[2] + dz)
        if state is None or bp.parse_state(state)[0] in bp.FLUIDS:
            return False
    return True


def buried_tops(body, block: str, here: Pos) -> list[Pos]:
    """Top cells of ``block`` columns below the bot within NEAR_STONE horizontally and
    BURIED_DEPTH down, with a known dry cell above; shallow and near first."""
    found = []
    for x, y, z in body.find_all(block):
        depth = here[1] - y
        if not 1 <= depth <= BURIED_DEPTH:
            continue
        if math.hypot(x - here[0], z - here[2]) > NEAR_STONE:
            continue
        above = body.world(x, y + 1, z)
        if above is None or bp.parse_state(above)[0] in (block, *bp.FLUIDS):
            continue
        found.append((x, y, z))
    return sorted(found, key=lambda p: (here[1] - p[1]) + _dist(p[::2], here[::2]))


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

    def __init__(self, body: GatherBody, k: Knowledge, state: GatherState, cap: int):
        self.body, self.k, self.state, self.cap = body, k, state, cap
        self.last_dig = ""

    # --- shared helpers -------------------------------------------------
    def reach_and_do(self, pos: Pos, do: Callable[[], Result], reach=REACH) -> Result:
        """Stance, act; other stances while the failure says wrong stance."""
        avoid: set[Pos] = set()
        result = Result(False, "no_stance")
        for _ in range(MAX_TRIES):
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
        for radius in range(EXPLORE_STEP, self.cap + 1, EXPLORE_STEP):
            for angle in range(0, 360, 45):
                key = (radius, angle)
                if key in st.explored:
                    continue
                st.explored.add(key)
                rad = math.radians(angle)
                spot = (
                    ox + round(radius * math.cos(rad)),
                    oy,
                    oz + round(radius * math.sin(rad)),
                )
                self.body.walk_to(spot, 2.0)
                if found():
                    return True
        return False

    def dig_to_stone(self, block: str) -> bool:
        """Baritone-style first stone: let the planner dig down to stand on the top of
        known buried ``block`` (the sensor delivers whole sections, buried cells too).
        Its cut rules refuse cells beside fluid or unknown cells, and the way back is
        a planned route. Nearest and shallowest first; the planner judges the rest."""
        dig_to = getattr(self.body, "dig_to", None)
        if dig_to is None or self.state.dig_tail:  # an earlier staircase is the way
            return False
        here = self.body.position()
        for top in buried_tops(self.body, block, here)[:DIG_CANDIDATES]:
            if dig_to((top[0], top[1] + 1, top[2])):
                return True
        return False

    def dig_for(self, item: str, target: int) -> int:
        """No usable stone: dig a player-style staircase (``gather_dig``) until
        ``target`` of ``item`` is held. Returns the blocks broken; ``last_dig`` says
        why it ended."""
        digger = Digger(self)
        digger.run(item, target)
        self.last_dig = digger.reason
        return digger.broken

    def collect(self, items: Collection[str] | None, near) -> None:
        """Walk onto the dropped item entities near ``near`` (None: any item)."""
        skipped: set[str] = set()
        for _ in range(8):
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
            e = min(drops, key=lambda e: _dist(e["position"], here))
            if not self.body.walk_to(_cell(e["position"]), 0.8):
                skipped.add(e.get("uuid"))

    def free_spot(self) -> Pos | None:
        x, y, z = self.body.position()
        offsets = sorted(
            product(range(-2, 3), repeat=2), key=lambda d: abs(d[0]) + abs(d[1])
        )
        for dx, dz in offsets:
            c = (x + dx, y, z + dz)
            if (dx, dz) == (0, 0) or self.body.world(*c) not in bp.AIR:
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

    def place_free(self, state: str, item: str) -> tuple[Result, Pos | None]:
        spot = self.free_spot()
        if spot is None:
            return Result(False, "no_free_spot"), None
        result = self.reach_and_do(
            spot, lambda: self.body.place_block(spot, state, item)
        )
        return result, spot

    # --- tasks ----------------------------------------------------------
    def gather(self, t) -> Result:
        body = self.body
        target = body.inventory().get(t.item, 0) + max(1, t.item_count)
        broken, skip, digs = 0, set(), 0
        underground = t.block.removeprefix("minecraft:") in UNDERGROUND
        while broken < t.count and body.inventory().get(t.item, 0) < target:
            here = body.position()
            known = [p for p in body.find_blocks(t.block) if p not in skip]
            if (
                underground
            ):  # only stone the bot can plausibly walk to; never a ravine floor
                known = [p for p in known if _near(p, here) and _sealed(body, p)]
            if not known and underground and digs < MAX_DIGS:
                digs += 1
                before = body.inventory().get(t.item, 0)
                self.dig_to_stone(t.block)  # else the staircase starts at the surface
                self.dig_for(t.item, target)
                # dirt broken on the way down is not progress toward t.count
                broken += max(0, body.inventory().get(t.item, 0) - before)
                continue
            if not known:
                if not self.explore(
                    lambda: bool(set(body.find_blocks(t.block)) - skip)
                ):
                    return Result(broken > 0, None if broken else "not_found")
                continue
            # Several nearest per scan: a scan of a big map costs seconds, and most
            # near candidates (cliff faces, ravine walls) have no stance.
            for pos in sorted(known, key=lambda p: _dist(p, here))[:SCAN_BATCH]:
                result = self.reach_and_do(pos, lambda p=pos: body.break_block(p))
                if result.ok:
                    broken += 1
                    self.collect({t.item}, pos)
                    break
                skip.add(pos)
                if result.reason in ("wrong_tool", "missing_tool"):
                    return result
        return Result(broken > 0, None if broken else "nothing_mined")

    def craft(self, t) -> Result:
        table = None
        if t.station == "crafting_table":
            table = self.station_pos("crafting_table")
            if table is None:
                return Result(False, "no_table")
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
        for _ in range(2 * t.count + 4):
            self.body.wait_ticks(min(SMELT_TICKS_PER_ITEM * t.count, 600))
            self.body.furnace(pos, "collect")
            if self.body.inventory().get(t.result, 0) >= want:
                return Result(True)
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
        result, spot = self.place_free(item, item)
        if result.ok:
            self.state.containers[spot] = {}
            self.state.last_container = spot
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
) -> GatherReport:
    """Run the plan's tasks through ``body``; see the module docstring."""
    state = state or GatherState()
    report = GatherReport(state=state)
    ex = _Exec(body, knowledge, state, explore_cap)
    targets = dict(targets) if targets is not None else None
    tasks = list(plan_result.tasks)
    failures = executed = 0
    while tasks and executed < max_tasks:
        task = tasks.pop(0)
        executed += 1
        _hunger(ex, report, targets)
        result = ex.run(task)
        if result.ok:
            failures = 0
            report.done.append(_label(task))
        else:
            failures += 1
            report.failures.append((_label(task), result.reason or "failed"))
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
            )
            report.replans += 1
            tasks = [
                t
                for t in fresh.tasks
                if not (type(t).__name__ == "Equip" and t.item in state.equipped)
            ]
            report.missing = [m.item for m in fresh.missing]
            if not tasks and fresh.missing:
                report.stop = "missing"
                return report
    report.stop = "max_tasks" if tasks else "done"
    return report
