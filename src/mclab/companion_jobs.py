# ruff: noqa: E501
"""End-to-end "build this" job (M5 glue): gather what is missing, then build.

``build_job(build_json_path, origin, rotation, body, knowledge)``:
1. read the material list (the file's ``materials``, else computed from the palette),
   add the tools the no-item cells need (hoe, shovel), subtract the inventory;
2. withdraw what remembered containers hold (``GatherState.containers``);
3. with no stone pickaxe held, the M4 starter phase first: a stone pickaxe target with
   ``starter_tools`` (table, wooden pickaxe and shovel, then stone pickaxe, axe and
   shovel), so the bulk logs are cut with a stone axe, not by hand (live run
   20261102T120137588638Z cut 35 logs by hand and never made a tool);
4. ``gather_plan.plan`` for the shortfall, then ``run_gather`` (which re-plans as it goes).
   A gather that stops ``stalled`` or ``failed`` ends the job (``gather_failed``);
5. ``run_build`` with the footprint survey, whose ``on_need_materials`` plans and runs a
   gather for the shortfall (``MAX_REFILLS`` times), then resumes building.
The body is any object that is both a ``GatherBody`` and a ``BuildBody``. A body that
digs (``terrain_policy``) gets every template cell, air included, added to its
protected positions first, so the route planner never breaks, pillars or bridges
inside the build, before or during it.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from mclab import build_plan as bp
from mclab import gather_plan as gp
from mclab import temporary_blocks
from mclab.build_runner import BuildReport, SiteCutter, run_build
from mclab.gather_options import GatherOptions
from mclab.gather_runner import (
    STATION_REACH,
    GatherReport,
    GatherState,
    flush_containers,
    hydrate_containers,
    run_gather,
)
from mclab.gather_tasks import Deposit, PlanResult, Withdraw, item_stack, slots_used
from mclab.knowledge import Knowledge, load_pack
from mclab.paths import ROOT
from mclab.schematic import from_build_json, material_list
from mclab.site_prep import site_area

# A build supplied through chests refills item by item as layers run out.
MAX_REFILLS = 40
PICKAXES = tuple(
    f"minecraft:{t}_pickaxe" for t in ("stone", "iron", "diamond", "netherite")
)


FOOD_KEEP = 16  # food items carried through a build, topped up at the chests


def _held_food(body) -> int:
    foods = getattr(body, "foods", None) or {}
    return sum(n for i, n in body.inventory().items() if i in foods)


def _ate_more(body, before: dict[str, int]) -> bool:
    foods = getattr(body, "foods", None) or {}
    held = body.inventory().items()
    return any(n > before.get(i, 0) for i, n in held if i in foods)


def _food_topup(body, state: GatherState) -> dict[str, int]:
    """{best stored food: what brings the carried food up to FOOD_KEEP}, or {}."""
    foods = getattr(body, "foods", None) or {}
    short = FOOD_KEEP - _held_food(body)
    stored: dict[str, int] = {}
    for contents in state.containers.values():
        for item, n in contents.items():
            if item in foods and n > 0:
                stored[item] = stored.get(item, 0) + n
    if short <= 0 or not stored:
        return {}
    best = max(stored, key=lambda i: (float(foods[i].get("nutrition", 0)), i))
    return {best: min(short, stored[best])}


def _storage_boxes(state: GatherState) -> list[dict]:
    """The plan ``storage`` view of the remembered containers (live contents)."""
    return [
        {"kind": "chest", "pos": list(pos), "contents": dict(contents)}
        for pos, contents in state.containers.items()
    ]


GATHER_ENDS = ("stalled", "failed", "died")
SHULKER = "minecraft:shulker_box"


def _scaffold_topup(body, state: GatherState) -> dict[str, int]:
    """{scaffolding: what brings the carried stack back to SCAFFOLD_STACK}, as far
    as the chests hold it, or {}; asked only once it is down to half a stack."""
    held = body.inventory().get(bp.SCAFFOLD_ITEMS[0], 0)
    stored = sum(c.get(bp.SCAFFOLD_ITEMS[0], 0) for c in state.containers.values())
    if held >= SCAFFOLD_STACK // 2 or stored <= 0:
        return {}
    return {bp.SCAFFOLD_ITEMS[0]: min(SCAFFOLD_STACK - held, stored)}


INVENTORY_SLOTS = 36


def _stored(state: GatherState) -> Counter[str]:
    stored: Counter[str] = Counter()
    for contents in state.containers.values():
        stored.update({i: n for i, n in contents.items() if n > 0})
    return stored


def _essential(item: str, body) -> bool:
    """Held through every refill: tools, temporaries, shulker boxes, food."""
    return (
        any(w in item for w in TOOL_WORDS)
        or item in bp.SCAFFOLD_ITEMS
        or item == SHULKER
        or item in (getattr(body, "foods", None) or {})
    )


def _real_slot_counts(body) -> dict[str, int]:
    """Per-item count of main-inventory slots (0..35) currently occupied on ``body``,
    or {} when the body has no per-slot rows (e.g. fakes in unit tests)."""
    rows = getattr(body, "_rows", None)
    if not rows:
        rows = (getattr(body, "_obs", None) or {}).get("body", {}).get("inventory")
    counts: dict[str, int] = {}
    for row in rows or ():
        slot = row.get("slot")
        if slot is not None and not (0 <= int(slot) < INVENTORY_SLOTS):
            continue
        if int(row.get("count", 0)) > 0:
            item = row["item"]
            counts[item] = counts.get(item, 0) + 1
    return counts


def _slots_used_real(counts, items, real: dict[str, int]) -> int:
    return sum(
        max(real.get(i, 0), slots_used({i: n}, items))
        for i, n in counts.items()
        if n > 0
    )


def _load(
    wanted: dict[str, int], body, knowledge, state: GatherState
) -> dict[str, int]:
    """What of ``wanted`` one trip to the chests carries: missing structure blocks
    before held structure before details (bp.is_detail), as much as the slots left
    beside the essentials hold. Uses real occupied slots so fragmented stacks do not
    overbook the 36-slot inventory, and prevents details from evicting already-held
    ``wanted`` details.
    Run 20261118T050156629666Z asked for the whole remaining bill at once: planks,
    lanterns, barrels and anvils filled the 36 slots and the 119 oak stairs the
    walls waited on stayed in the chest for the rest of the build."""
    items = getattr(knowledge, "items", None) or {}
    stored, held = _stored(state), body.inventory()
    real = _real_slot_counts(body)
    room = INVENTORY_SLOTS - _slots_used_real(
        {i: n for i, n in held.items() if _essential(i, body)}, items, real
    )
    load: dict[str, int] = {}
    structure = [i for i in wanted if not bp.is_detail(i)]
    structure.sort(
        key=lambda i: (
            2
            if (held.get(i, 0) >= wanted[i] or stored[i] <= 0)
            else (1 if held.get(i, 0) > 0 else 0)
        )
    )
    for i in structure:
        has = held.get(i, 0)
        take = min(max(0, wanted[i] - has), stored[i])
        if take <= 0 and has <= 0:
            continue
        stack = item_stack(items, i)
        fits = max(0, room * stack - has)
        take = min(take, fits)
        cost = _slots_used_real({i: has + take}, items, real)
        if cost > room:
            if has > 0:
                room = 0  # held structure already fills remaining room; block details
            continue  # what is held already of it may go back
        if take > 0:
            load[i] = take
        room -= cost

    details = [i for i in wanted if bp.is_detail(i)]
    reserved_detail: dict[str, int] = {}
    for i in details:
        has = held.get(i, 0)
        if has <= 0:
            continue
        cost = _slots_used_real({i: has}, items, real)
        if cost <= room:
            room -= cost
            reserved_detail[i] = cost
        else:
            room = 0

    for i in details:
        has = held.get(i, 0)
        take = min(max(0, wanted[i] - has), stored[i])
        if take <= 0:
            continue
        already = reserved_detail.get(i, 0)
        stack = item_stack(items, i)
        fits = max(0, (room + already) * stack - has)
        take = min(take, fits)
        cost = _slots_used_real({i: has + take}, items, real)
        extra = cost - already
        if extra > room or take <= 0:
            continue
        load[i] = take
        room -= extra
    return load


def _make_room(
    load: dict[str, int],
    keep: frozenset[str],
    body,
    knowledge,
    state: GatherState,
    gather_options: GatherOptions | None = None,
    *,
    wanted=(),
    free: int = 0,
) -> None:
    """Before withdrawing ``load`` (or mining with a full inventory), put back into
    the emptiest chest held blocks the refill did not come for (non-wanted first,
    then details, then largest), until the load fits with ``free`` spare slots. Run
    20261118T042119862404Z held 34 of 36 slots of house details waiting on the
    structure; the refill withdrew 4 of the 223 planks it was short."""
    if not state.containers or (not load and free <= 0):
        return
    items = getattr(knowledge, "items", None) or {}
    held = body.inventory()
    real = _real_slot_counts(body)
    after = Counter(held)
    after.update(load)
    spare = [
        i
        for i, n in held.items()
        if n > 0 and i not in keep and not _essential(i, body)
    ]
    spare.sort(
        key=lambda i: (
            i in wanted,
            not bp.is_detail(i),
            -_slots_used_real({i: after[i]}, items, real),
            i,
        )
    )
    out: dict[str, int] = {}
    limit = INVENTORY_SLOTS - free
    for i in spare:
        if _slots_used_real(after, items, real) <= limit:
            break
        out[i] = after.pop(i)
    if not out:
        return
    pos = min(state.containers, key=lambda c: sum(state.containers[c].values()))
    plan = PlanResult((Deposit(tuple(sorted(out.items())), pos),), (), 0.0)
    run_gather(plan, body, knowledge, state=state, opts=gather_options)


def _keeping(wanted, body) -> frozenset[str]:
    """What a refill mid-build never deposits: what it came for, the temporary
    blocks, food and shulker boxes. Run 20261118T023332974582Z stowed 51 scaffolding
    in a refill's deposit and pillared dirt from afar for the rest of the build. The
    rest of the house's blocks may go back to make room: keeping the whole bill
    filled 34 of 36 slots with details waiting on the structure, and the refill
    withdrew 4 items (run 20261118T042119862404Z)."""
    keep = {*wanted, *bp.SCAFFOLD_ITEMS, SHULKER}
    return frozenset(keep | set(getattr(body, "foods", None) or {}))


SCAFFOLD_STACK = 64
KNOWLEDGE = ROOT / "knowledge"


def load_vanilla_pack() -> Knowledge:
    """The committed vanilla pack with its hand-written sources, transforms and foods."""
    return load_pack(
        KNOWLEDGE / "26.3-446ec6bbd497",  # the one with ores.json
        sources=KNOWLEDGE / "sources/vanilla-26.3.json",
        transforms=KNOWLEDGE / "transforms/vanilla-26.3.json",
        foods=KNOWLEDGE / "sources/foods-26.3.json",
    )


PHASE_FOR_STAGE = {
    "reading chests": "chests_setup",
    "scaffolding": "chests_setup",
    "gathering starter tools": "gathering",
    "gathering materials": "gathering",
    "clearing trees": "clearing_and_site_prep",
    "site prep": "clearing_and_site_prep",
    "site prep (gathering)": "clearing_and_site_prep",
    "placing": "building_structure",
    "placing details": "placing_details",
    "refilling materials": "refills",
    "cleanup": "cleanup",
}


class PhaseClock:
    """Accumulates wall-clock seconds across stage transitions."""

    def __init__(self, wall_clock: Callable[[], float] = time.monotonic):
        self.clock = wall_clock
        self.started = wall_clock()
        self._last = self.started
        self.current: str | None = None
        self.stages: dict[str, float] = {}
        self.phases: dict[str, float] = {}

    def enter(self, name: str) -> str | None:
        now = self.clock()
        prev = self.current
        if prev is not None and now > self._last:
            dt = now - self._last
            self.stages[prev] = self.stages.get(prev, 0.0) + dt
            phase = PHASE_FOR_STAGE.get(prev, prev)
            self.phases[phase] = self.phases.get(phase, 0.0) + dt
        self._last = now
        self.current = name
        return prev

    def finish(self) -> tuple[float, dict[str, float], dict[str, float]]:
        now = self.clock()
        if self.current is not None and now > self._last:
            dt = now - self._last
            self.stages[self.current] = self.stages.get(self.current, 0.0) + dt
            phase = PHASE_FOR_STAGE.get(self.current, self.current)
            self.phases[phase] = self.phases.get(phase, 0.0) + dt
        self._last = now
        total = round(max(0.0, now - self.started), 2)
        return (
            total,
            {k: round(v, 2) for k, v in self.phases.items()},
            {k: round(v, 2) for k, v in self.stages.items()},
        )


@dataclass
class JobReport:
    needed: dict[str, int] = field(default_factory=dict)  # material list + tools
    shortfall: dict[str, int] = field(default_factory=dict)
    starter: GatherReport | None = None
    gather: GatherReport | None = None
    refills: list[GatherReport] = field(default_factory=list)
    build: BuildReport | None = None
    stop: str = ""  # built | not_built | gather_failed
    ticks: dict[str, int] = field(default_factory=dict)  # per phase, when clocked
    duration_s: float = 0.0
    phases_s: dict[str, float] = field(default_factory=dict)
    stages_s: dict[str, float] = field(default_factory=dict)


def materials_of(build: dict, knowledge: Knowledge) -> dict[str, int]:
    if build.get("materials"):
        return dict(build["materials"])
    counts, _ = material_list(from_build_json(build), {"blocks": knowledge.blocks})
    return dict(counts)


def _held(body, state: GatherState) -> Counter:
    total = Counter(body.inventory())
    for contents in state.containers.values():
        total.update(contents)
    return total


def _withdraw_plan(
    needed: dict[str, int], body, state: GatherState, *, delta: bool = False
) -> PlanResult:
    tasks = []
    have = Counter(body.inventory())
    target = {i: have.get(i, 0) + n for i, n in needed.items()} if delta else needed
    for pos, contents in state.containers.items():
        take = tuple(
            (item, min(n, target[item] - have[item]))
            for item, n in contents.items()
            if item in target and target[item] > have[item] and n > 0
        )
        if take:
            tasks.append(Withdraw(take, pos))
            have.update(dict(take))
    return PlanResult(tuple(tasks), (), 0.0)


def read_chests(chests, body, state: GatherState) -> None:
    """Open each known chest once (peek) and remember what it holds, before moving on.
    A chest that cannot be reached or read is left out of ``state.containers``."""
    for pos in chests:
        pos = tuple(pos)
        if not body.goto_stance(pos, STATION_REACH):
            continue
        result = body.container(pos, "peek")
        if result.ok:
            contents: dict[str, int] = {}
            for stack in result.data.get("contents") or ():
                contents[stack["item"]] = (
                    contents.get(stack["item"], 0) + stack["count"]
                )
            state.containers[pos] = contents
            flush_containers(body, state)


TOOL_WORDS = ("_axe", "_pickaxe", "_shovel", "_hoe", "_sword", "shears", "bucket")


def junk(inventory: dict[str, int], wanted: dict[str, int], foods=()) -> dict[str, int]:
    """What a refill may put away: nothing the build wants, no tools, no scaffolding, no food."""
    return {
        i: n
        for i, n in inventory.items()
        if n > 0
        and i not in wanted
        and i not in bp.SCAFFOLD_ITEMS
        and i != SHULKER
        and i not in foods
        and not any(w in i for w in TOOL_WORDS)
    }


def _stow_junk(
    wanted: dict[str, int],
    body,
    knowledge,
    state: GatherState,
    gather_options: GatherOptions | None = None,
) -> None:
    # ponytail: the emptiest known chest takes the site-clearing leftovers (dirt,
    # flowers, saplings). Run 20261110T124208809624Z stopped need_materials at 294
    # blocks: the inventory was full of them, so the refill withdrew nothing.
    spare = junk(body.inventory(), wanted, getattr(body, "foods", None) or ())
    if not spare or not state.containers:
        return
    pos = min(state.containers, key=lambda c: sum(state.containers[c].values()))
    plan = PlanResult((Deposit(tuple(sorted(spare.items())), pos),), (), 0.0)
    run_gather(plan, body, knowledge, state=state, opts=gather_options)


def _note_refill(body, wanted, before, source: str, ok: bool) -> None:
    """One ``refill`` record per material request: what was asked, what came in and
    what the chests still hold. Run 20261117T102553117737Z stopped on need_materials
    with nothing in the log to say which item could not be had."""
    held = body.inventory()
    row = {
        "wanted": dict(wanted),
        "gained": {
            i: held.get(i, 0) - before.get(i, 0)
            for i in wanted
            if held.get(i, 0) != before.get(i, 0)
        },
        "short": {
            i: n - held.get(i, 0) for i, n in wanted.items() if held.get(i, 0) < n
        },
        "source": source,
        "ok": ok,
    }
    if (bridge := getattr(body, "bridge", None)) is not None and hasattr(
        bridge, "record"
    ):
        bridge.record("refill", row)
    print({"refill": row["source"], "ok": ok, "short": row["short"]}, flush=True)


def _take(
    items: dict[str, int],
    body,
    knowledge,
    state: GatherState,
    gather_options: GatherOptions | None = None,
    *,
    delta: bool = False,
) -> None:
    """Withdraw `items` from the remembered containers, as far as they hold them."""
    plan = _withdraw_plan(items, body, state, delta=delta)
    if plan.tasks:
        run_gather(plan, body, knowledge, state=state, opts=gather_options)


def build_job(
    build_json_path,
    origin,
    rotation: int,
    body,
    knowledge: Knowledge,
    *,
    mirror: bool = False,
    state: GatherState | None = None,
    survey: bool = True,
    max_failures: int = 3,
    clock: Callable[[], int] | None = None,
    chests=(),
    build_only: bool = False,
    on_progress=None,
    on_stage=None,
    site_margin: int | None = 1,
    gather_options: GatherOptions | None = None,
) -> JobReport:
    """``chests``: positions of chests known at the start. They are read first; shulker
    boxes are taken at once (they carry the gather's overflow), scaffolding stays put
    until the gather is over and is taken just before the build. ``build_only``: the
    inventory already holds everything (a resumed checkpoint); no chests, no gathering.
    ``on_progress(report, final)`` is passed to ``run_build``. ``site_margin``: blocks
    of levelled ground prepared around the footprint before building (>= 0, default
    1; None skips site preparation)."""
    publish_stage = on_stage or (
        lambda *a, **k: None
    )  # publishes the dashboard's live stage
    phase_clock = PhaseClock()

    def stage(name: str, done=None, total=None, detail=None):
        phase_clock.enter(name)
        publish_stage(name, done, total, detail)

    report = JobReport()
    try:
        build = json.loads(Path(build_json_path).read_text(encoding="utf-8"))
        template = bp.Template.from_build(build, tuple(origin), rotation, mirror)
        protect(body, template, site_margin, blocks=knowledge.blocks)
        # What the body may put down to climb and take back: scaffolding, dirt,
        # cobblestone, then spare plain blocks that drop themselves (mclab.temporary_blocks).
        body.temporary_ok = temporary_blocks.eligible(knowledge)
        body.foods = dict(getattr(knowledge, "foods", None) or {})  # eat_when_needed
        if state is None:
            state = GatherState()
            hydrate_containers(body, state)
        started = clock() if clock else 0
        if not build_only:
            stage("reading chests")
            read_chests(chests, body, state)
            _take({SHULKER: 2}, body, knowledge, state, gather_options)
        needed = materials_of(build, knowledge)
        needed.update(bp.tool_needs(template, body.inventory()))
        report.needed = dict(needed)

        held = Counter(body.inventory())
        stored = Counter()
        for contents in state.containers.values():
            stored.update(contents)
        # Remembered containers count before the world: the plan withdraws from them
        # behind Withdraw tasks (``hand_over``) instead of mining the item again.
        gap = {i: n - held[i] for i, n in needed.items() if n > held[i]}
        report.shortfall = (
            {}
            if build_only
            else {
                i: n - stored.get(i, 0) for i, n in gap.items() if n > stored.get(i, 0)
            }
        )

        if gap and report.shortfall and not any(held[p] for p in PICKAXES):
            wanted = {PICKAXES[0]: 1}
            starter = gp.plan(
                wanted,
                body.inventory(),
                knowledge,
                state.stations,
                starter_tools=True,
                storage=_storage_boxes(state),
                kit=gather_options,
            )
            stage("gathering starter tools")
            report.starter = run_gather(
                starter,
                body,
                knowledge,
                wanted,
                state=state,
                max_failures=max_failures,
                storage=lambda: _storage_boxes(state),
                opts=gather_options,
            )
            if report.starter.stop in GATHER_ENDS:
                report.stop = "gather_failed"
                return report
        if gap and not build_only:
            cutter = (
                SiteCutter(
                    template,
                    body,
                    knowledge.blocks,
                    site_margin,
                    protected=tuple(tuple(c) for c in chests),
                    log=lambda m: (
                        print(m, flush=True),
                        stage("site prep (gathering)", detail=m),
                    ),
                )
                if site_margin is not None
                else None
            )

            def _clear_area():
                """One slice of useful work while a furnace smelts."""
                stage("site prep (gathering)")
                try:
                    if cutter is not None:
                        return cutter.slice()
                    from mclab.tree_removal import clear_trees_in_footprint

                    clear_trees_in_footprint(body, template.cells, template=template)
                    return False
                finally:
                    stage("gathering materials")

            first = gp.plan(
                needed,
                body.inventory(),
                knowledge,
                state.stations,
                starter_tools=True,
                storage=_storage_boxes(state),
                kit=gather_options,
            )
            stage("gathering materials")
            report.gather = run_gather(
                first,
                body,
                knowledge,
                needed,
                state=state,
                max_failures=max_failures,
                storage=lambda: _storage_boxes(state),
                idle_work=_clear_area,
                opts=gather_options,
            )
            if report.gather.stop in GATHER_ENDS:
                report.stop = "gather_failed"
                return report
        gathered = clock() if clock else 0

        refills = 0

        def refill(shortfall: dict[str, int]) -> bool:
            nonlocal refills
            if refills >= MAX_REFILLS:
                return False
            refills += 1
            prev_stage = phase_clock.current
            stage("refilling materials", refills, MAX_REFILLS)
            try:
                wanted = dict(shortfall)
                _stow_junk({**needed, **wanted}, body, knowledge, state, gather_options)
                before = body.inventory()
                # Every trip to the chests tops up the food carried (FOOD_KEEP). Food is
                # taken from the chests only, never hunted mid-build: with none stored the
                # body notes it and builds on (live, a player can hand it some).
                food = {**_food_topup(body, state), **_scaffold_topup(body, state)}
                if food:
                    _take(food, body, knowledge, state, gather_options, delta=True)
                    if not wanted:
                        fed = _ate_more(body, before)
                        _note_refill(body, food, before, "chests", fed)
                        return fed
                elif not wanted:
                    _note_refill(
                        body, {"food": FOOD_KEEP}, before, "no_food_stored", False
                    )
                    return False
                # Chests first, and back to building on any gain. Run 20261110T170929838024Z
                # was short one detector rail with the rest of the house in the chests: the
                # full plan put the ore hunt first and walked the explore ring over a swamp.
                load = _load(wanted, body, knowledge, state)
                _make_room(
                    load,
                    frozenset(load),
                    body,
                    knowledge,
                    state,
                    gather_options,
                    wanted=wanted,
                )
                _take(load, body, knowledge, state, gather_options, delta=True)
                if any(body.inventory().get(i, 0) > before.get(i, 0) for i in wanted):
                    report.refills.append(GatherReport(stop="done"))
                    _note_refill(body, wanted, before, "chests", True)
                    return True
                held_now = body.inventory()
                stored = _stored(state)
                missing = {
                    i: n
                    for i, n in wanted.items()
                    if held_now.get(i, 0) + stored[i] < n
                }
                if not missing:
                    _note_refill(body, wanted, before, "chests", False)
                    return False
                keep = _keeping(missing, body)
                _make_room(
                    {},
                    keep,
                    body,
                    knowledge,
                    state,
                    gather_options,
                    wanted=wanted,
                    free=3,
                )
                fresh = gp.plan(
                    missing,
                    body.inventory(),
                    knowledge,
                    state.stations,
                    starter_tools=True,
                    storage=_storage_boxes(state),
                    kit=gather_options,
                    keep=keep,
                )
                outcome = run_gather(
                    fresh,
                    body,
                    knowledge,
                    missing,
                    state=state,
                    max_failures=max_failures,
                    explore_cap=0,  # craft and cut what is in sight; never leave the site
                    storage=lambda: _storage_boxes(state),
                    opts=gather_options,
                    keep=keep,
                )
                report.refills.append(outcome)
                held_now = body.inventory()
                # ponytail: any gain counts. The whole remaining house does not fit in one
                # inventory, and demanding all of it stopped run 20261110T063137807602Z at
                # 280 of 1227 blocks with the stock sitting in the chests.
                ok = any(held_now.get(i, 0) > before.get(i, 0) for i in missing) or all(
                    held_now.get(i, 0) >= n for i, n in missing.items()
                )
                _note_refill(body, missing, before, f"gather:{outcome.stop}", ok)
                return ok
            finally:
                if prev_stage is not None:
                    stage(prev_stage)

        stage("scaffolding")
        held_now = body.inventory()
        _take(
            {
                bp.SCAFFOLD_ITEMS[0]: SCAFFOLD_STACK,
                **{
                    i: held_now.get(i, 0) + n
                    for i, n in _food_topup(body, state).items()
                },
            },
            body,
            knowledge,
            state,
            gather_options,
        )
        body.precise_stance = True  # placing needs the body centred and out of the cell
        try:
            report.build = run_build(
                template,
                body,
                knowledge.blocks,
                on_need_materials=refill,
                survey=survey,
                on_progress=on_progress,
                on_stage=stage,
                site_margin=site_margin,
                log=lambda m: print(m, flush=True),
                protected=tuple(tuple(c) for c in chests),
            )
        finally:
            body.precise_stance = False
        report.stop = "built" if report.build.stop == "done" else "not_built"
        if clock:
            # build includes refills; crafting happens inside the gather phase
            report.ticks = {"gather": gathered - started, "build": clock() - gathered}
        return report
    finally:
        report.duration_s, report.phases_s, report.stages_s = phase_clock.finish()


def protect(
    body,
    template: bp.Template,
    site_margin: int | None = None,
    blocks: dict | None = None,
) -> None:
    """Keep a digging body's route planner, and its gathering, out of the template's
    cells and out of the ground of the site to be levelled: dirt dug for fill must
    come from outside it (run 20261110T001441278034Z dug holes in its own site)."""
    policy = getattr(body, "terrain_policy", None)
    if policy is None:
        return
    cells = set(policy.protected_positions) | set(template.cells)
    if site_margin is not None:
        site = site_area(template, site_margin)
        cells |= {(x, site.ground_y, z) for x, z in site.columns()}
    # The planner may break a standing wall cell at a price (CompanionBody._planner_terrain);
    # the build loop re-places it.
    body.build_cells = {
        p: st for p, st in template.cells.items() if bp.parse_state(st)[0] not in bp.AIR
    }
    # Its open air cells take footholds (the build loop adopts them as scaffolds and takes
    # them back): with them protected, no pillar could rise beside a wall under the
    # eaves, and the upper floor of a house without stairs was out of reach
    # (run 20261117T075353967859Z). Sealed interior cavities (e.g. a 1-high attic gap
    # under a roof ridge) stay protected so no foothold is ever sealed inside them
    # (run 20261120T175636584118Z: dirt at (-12, 75, 63)).
    sealed = bp.sealed_air(template, blocks)
    body.build_air = {
        p
        for p, st in template.cells.items()
        if bp.parse_state(st)[0] in bp.AIR and p not in sealed
    }
    body.terrain_policy = replace(policy, protected_positions=tuple(sorted(cells)))
