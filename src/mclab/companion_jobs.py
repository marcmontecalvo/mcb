# ruff: noqa: E501
"""End-to-end "build this" job (M5 glue): gather what is missing, then build.

``build_job(build_json_path, origin, rotation, body, knowledge)``:
1. read the material list (the file's ``materials``, else computed from the palette),
   add the tools the no-item cells need (hoe, shovel), subtract the inventory;
2. withdraw what remembered containers hold (``GatherState.containers``);
3. ``gather_plan.plan`` for the shortfall, then ``run_gather`` (which re-plans as it goes);
4. ``run_build`` with the footprint survey, whose ``on_need_materials`` plans and runs a
   gather for the shortfall (``MAX_REFILLS`` times), then resumes building.
The body is any object that is both a ``GatherBody`` and a ``BuildBody``.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from mclab import build_plan as bp
from mclab import gather_plan as gp
from mclab.build_runner import BuildReport, run_build
from mclab.gather_runner import GatherReport, GatherState, run_gather
from mclab.gather_tasks import PlanResult, Withdraw
from mclab.knowledge import Knowledge, load_pack
from mclab.paths import ROOT
from mclab.schematic import from_build_json, material_list

MAX_REFILLS = 3
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
    gather: GatherReport | None = None
    refills: list[GatherReport] = field(default_factory=list)
    build: BuildReport | None = None
    stop: str = ""  # built | not_built | gather_failed


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
) -> JobReport:
    build = json.loads(Path(build_json_path).read_text(encoding="utf-8"))
    template = bp.Template.from_build(build, tuple(origin), rotation, mirror)
    state = state or GatherState()
    report = JobReport()
    needed = materials_of(build, knowledge)
    needed.update(bp.tool_needs(template, body.inventory()))
    report.needed = dict(needed)

    # Remembered containers first: withdrawing is cheaper than gathering again.
    stored = _withdraw_plan(needed, body, state)
    if stored.tasks:
        run_gather(stored, body, knowledge, state=state)
    held = Counter(body.inventory())
    report.shortfall = {i: n - held[i] for i, n in needed.items() if n > held[i]}

    if report.shortfall:
        first = gp.plan(
            needed, body.inventory(), knowledge, state.stations, starter_tools=True
        )
        report.gather = run_gather(
            first, body, knowledge, needed, state=state, max_failures=max_failures
        )

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

    report.build = run_build(
        template,
        body,
        knowledge.blocks,
        on_need_materials=refill,
        survey=survey,
    )
    report.stop = "built" if report.build.stop == "done" else "not_built"
    return report
