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
from mclab.build_runner import BuildReport, run_build
from mclab.gather_runner import STATION_REACH, GatherReport, GatherState, run_gather
from mclab.gather_tasks import PlanResult, Withdraw
from mclab.knowledge import Knowledge, load_pack
from mclab.paths import ROOT
from mclab.schematic import from_build_json, material_list

MAX_REFILLS = 3
PICKAXES = tuple(
    f"minecraft:{t}_pickaxe" for t in ("stone", "iron", "diamond", "netherite")
)
GATHER_ENDS = ("stalled", "failed")
SHULKER = "minecraft:shulker_box"
SCAFFOLD_STACK = 64
KNOWLEDGE = ROOT / "knowledge"


def load_vanilla_pack() -> Knowledge:
    """The committed vanilla pack with its hand-written sources, transforms and foods."""
    return load_pack(
        KNOWLEDGE / "26.3-f7dba04d8b5b",
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


def _take(items: dict[str, int], body, knowledge, state: GatherState) -> None:
    """Withdraw `items` from the remembered containers, as far as they hold them."""
    plan = _withdraw_plan(items, body, state)
    if plan.tasks:
        run_gather(plan, body, knowledge, state=state)


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
) -> JobReport:
    """``chests``: positions of chests known at the start. They are read first; shulker
    boxes are taken at once (they carry the gather's overflow), scaffolding stays put
    until the gather is over and is taken just before the build. ``build_only``: the
    inventory already holds everything (a resumed checkpoint); no chests, no gathering.
    ``on_progress(report, final)`` is passed to ``run_build``."""
    build = json.loads(Path(build_json_path).read_text(encoding="utf-8"))
    template = bp.Template.from_build(build, tuple(origin), rotation, mirror)
    protect(body, template)
    state = state or GatherState()
    report = JobReport()
    started = clock() if clock else 0
    if not build_only:
        read_chests(chests, body, state)
        _take({SHULKER: 2}, body, knowledge, state)
    needed = materials_of(build, knowledge)
    needed.update(bp.tool_needs(template, body.inventory()))
    report.needed = dict(needed)

    # Remembered containers first: withdrawing is cheaper than gathering again.
    stored = _withdraw_plan(needed, body, state)
    if stored.tasks and not build_only:
        run_gather(stored, body, knowledge, state=state)
    held = Counter(body.inventory())
    report.shortfall = (
        {} if build_only else {i: n - held[i] for i, n in needed.items() if n > held[i]}
    )

    if report.shortfall and not any(held[p] for p in PICKAXES):
        wanted = {PICKAXES[0]: 1}
        starter = gp.plan(
            wanted, body.inventory(), knowledge, state.stations, starter_tools=True
        )
        report.starter = run_gather(
            starter, body, knowledge, wanted, state=state, max_failures=max_failures
        )
        if report.starter.stop in GATHER_ENDS:
            report.stop = "gather_failed"
            return report
    if report.shortfall:
        first = gp.plan(
            needed, body.inventory(), knowledge, state.stations, starter_tools=True
        )
        report.gather = run_gather(
            first, body, knowledge, needed, state=state, max_failures=max_failures
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
        wanted = dict(shortfall)
        fresh = gp.plan(
            wanted, body.inventory(), knowledge, state.stations, starter_tools=True
        )
        outcome = run_gather(
            fresh, body, knowledge, wanted, state=state, max_failures=max_failures
        )
        report.refills.append(outcome)
        held_now = body.inventory()
        return all(held_now.get(i, 0) >= n for i, n in wanted.items())

    _take({bp.SCAFFOLD_ITEMS[0]: SCAFFOLD_STACK}, body, knowledge, state)
    body.precise_stance = True  # placing needs the body centred and out of the cell
    try:
        report.build = run_build(
            template,
            body,
            knowledge.blocks,
            on_need_materials=refill,
            survey=survey,
            on_progress=on_progress,
        )
    finally:
        body.precise_stance = False
    report.stop = "built" if report.build.stop == "done" else "not_built"
    if clock:
        # build includes refills; crafting happens inside the gather phase
        report.ticks = {"gather": gathered - started, "build": clock() - gathered}
    return report


def protect(body, template: bp.Template) -> None:
    """Keep a digging body's route planner out of the template's cells."""
    policy = getattr(body, "terrain_policy", None)
    if policy is None:
        return
    cells = set(policy.protected_positions) | set(template.cells)
    body.terrain_policy = replace(policy, protected_positions=tuple(sorted(cells)))
