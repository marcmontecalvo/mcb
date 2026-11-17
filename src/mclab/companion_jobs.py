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
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from mclab import build_plan as bp
from mclab import gather_plan as gp
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
from mclab.gather_tasks import Deposit, PlanResult, Withdraw
from mclab.knowledge import Knowledge, load_pack
from mclab.paths import ROOT
from mclab.schematic import from_build_json, material_list
from mclab.site_prep import site_area

# A build supplied through chests refills item by item as layers run out.
MAX_REFILLS = 40
PICKAXES = tuple(
    f"minecraft:{t}_pickaxe" for t in ("stone", "iron", "diamond", "netherite")
)


def _storage_boxes(state: GatherState) -> list[dict]:
    """The plan ``storage`` view of the remembered containers (live contents)."""
    return [
        {"kind": "chest", "pos": list(pos), "contents": dict(contents)}
        for pos, contents in state.containers.items()
    ]


GATHER_ENDS = ("stalled", "failed", "died")
SHULKER = "minecraft:shulker_box"
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


def _withdraw_plan(needed: dict[str, int], body, state: GatherState) -> PlanResult:
    tasks = []
    have = Counter(body.inventory())
    for pos, contents in state.containers.items():
        take = tuple(
            (item, min(n, needed[item] - have[item]))
            for item, n in contents.items()
            if item in needed and needed[item] > have[item] and n > 0
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


def junk(inventory: dict[str, int], wanted: dict[str, int]) -> dict[str, int]:
    """What a refill may put away: nothing the build wants, no tools, no scaffolding."""
    return {
        i: n
        for i, n in inventory.items()
        if n > 0
        and i not in wanted
        and i not in bp.SCAFFOLD_ITEMS
        and i != SHULKER
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
    spare = junk(body.inventory(), wanted)
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
) -> None:
    """Withdraw `items` from the remembered containers, as far as they hold them."""
    plan = _withdraw_plan(items, body, state)
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
    stage = on_stage or (lambda *a, **k: None)  # publishes the dashboard's live stage
    build = json.loads(Path(build_json_path).read_text(encoding="utf-8"))
    template = bp.Template.from_build(build, tuple(origin), rotation, mirror)
    protect(body, template, site_margin)
    if state is None:
        state = GatherState()
        hydrate_containers(body, state)
    report = JobReport()
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
        else {i: n - stored.get(i, 0) for i, n in gap.items() if n > stored.get(i, 0)}
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
                log=lambda m: (print(m, flush=True), stage("site prep (gathering)", detail=m)),
            )
            if site_margin is not None
            else None
        )

        def _clear_area():
            """One slice of useful work while a furnace smelts."""
            if cutter is not None:
                return cutter.slice()
            from mclab.tree_removal import clear_trees_in_footprint

            clear_trees_in_footprint(body, template.cells, template=template)
            return False

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
        stage("refilling materials", refills, MAX_REFILLS)
        wanted = dict(shortfall)
        _stow_junk({**needed, **wanted}, body, knowledge, state, gather_options)
        before = body.inventory()
        # Chests first, and back to building on any gain. Run 20261110T170929838024Z
        # was short one detector rail with the rest of the house in the chests: the
        # full plan put the ore hunt first and walked the explore ring over a swamp.
        _take(wanted, body, knowledge, state, gather_options)
        if any(body.inventory().get(i, 0) > before.get(i, 0) for i in wanted):
            report.refills.append(GatherReport(stop="done"))
            _note_refill(body, wanted, before, "chests", True)
            return True
        fresh = gp.plan(
            wanted,
            body.inventory(),
            knowledge,
            state.stations,
            starter_tools=True,
            storage=_storage_boxes(state),
            kit=gather_options,
        )
        outcome = run_gather(
            fresh,
            body,
            knowledge,
            wanted,
            state=state,
            max_failures=max_failures,
            explore_cap=0,  # craft and cut what is in sight; never leave the site
            storage=lambda: _storage_boxes(state),
            opts=gather_options,
        )
        report.refills.append(outcome)
        held_now = body.inventory()
        # ponytail: any gain counts. The whole remaining house does not fit in one
        # inventory, and demanding all of it stopped run 20261110T063137807602Z at
        # 280 of 1227 blocks with the stock sitting in the chests.
        ok = any(held_now.get(i, 0) > before.get(i, 0) for i in wanted) or all(
            held_now.get(i, 0) >= n for i, n in wanted.items()
        )
        _note_refill(body, wanted, before, f"gather:{outcome.stop}", ok)
        return ok

    _take(
        {bp.SCAFFOLD_ITEMS[0]: SCAFFOLD_STACK},
        body,
        knowledge,
        state,
        gather_options,
    )
    stage("scaffolding")
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


def protect(body, template: bp.Template, site_margin: int | None = None) -> None:
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
    body.build_cells = {p: st for p, st in template.cells.items() if bp.parse_state(st)[0] not in bp.AIR}
    # Its air cells take footholds (the build loop adopts them as scaffolds and takes
    # them back): with them protected, no pillar could rise beside a wall under the
    # eaves, and the upper floor of a house without stairs was out of reach
    # (run 20261117T075353967859Z).
    body.build_air = {p for p, st in template.cells.items() if bp.parse_state(st)[0] in bp.AIR}
    body.terrain_policy = replace(policy, protected_positions=tuple(sorted(cells)))
