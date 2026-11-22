# ruff: noqa: E501
"""Live build run (companion track: M2 with materials given, M5 with --empty).

Usage (lab server up, idle lab; start = a feet cell on open ground):
    uv run --locked python scripts/run-build.py --start X Y Z [--build FILE] [--dx 3 --dz 2]
    uv run --locked python scripts/run-build.py --start X Y Z --empty --seed 42424242
    uv run --locked python scripts/run-build.py --build tests/fixtures/schematics/house-large.build.json --supply --coal item --start -24 64 56 --seed 31415926

A run writes checkpoints/NNN.json (every 10 actions) and final.json; ``--resume`` sets
the same seed's world up from one and runs only the build (no gathering).

Default (M2): kits the build's material list (plus dirt for scaffolds and a hoe/shovel if
needed) and clears a box round the start. --empty (M5): empty inventory, nothing cleared;
the bot gathers and crafts everything. --supply: empty hands, but the materials sit in
containers beside the start - the bill of materials plus whatever a plan over that stock
would still want out of the world - so the run withdraws, crafts and builds without ever
searching for a block: it measures the build. Lanterns and the campfire stay out of the
bins on purpose; the bot makes them from the --coal form (item, block or ore), the other
thing under test. Either way companion_jobs.build_job builds
tests/fixtures/schematics/small-house.build.json at start+(dx,0,dz) with digging on
(start_session(dig=True)); the template's cells are protected from the route planner.
Pass: build stop == done, no unknown cells, and the diff against the delivered map is empty.
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

from live_common import digest_on_exit, finish, kit, record_crash, results_folder

from mclab import build_checkpoint as ck
from mclab import build_plan as bp
from mclab import run_digest
from mclab.build_debug import place_preview, preview_cells, write_walkthrough
from mclab.companion_body import (
    companion_world,
    end_session,
    open_bridge,
    start_session,
)
from mclab.companion_jobs import build_job, load_vanilla_pack, materials_of
from mclab.companion_progress import Progress
from mclab.gather_options import GatherOptions
from mclab.gather_plan import Gather, Hunt, Shear, plan
from mclab.gather_runner import GatherState, hydrate_containers
from mclab.gather_tasks import item_stack
from mclab.markers import run_switch
from mclab.paths import ROOT
from mclab.recorder import exclusive_arena
from mclab.run_config import policies
from mclab.run_config_cli import invocation, parse_run
from mclab.run_launch import launch_record, record_outcome
from mclab.run_launch import record_effective_run as record_run

DEFAULT_BUILD = ROOT / "tests/fixtures/schematics/small-house.build.json"
CLEAR = [10, 6, 10]
# the blocks a coal form pays for: never given, always crafted
COAL_CONSUMERS = ("minecraft:lantern", "minecraft:campfire")
# each form covers the large house's coal bill (25) with a margin: a bin that runs
# short would send the bot off smelting logs for charcoal instead of building
COAL_FORMS = {
    "item": {"minecraft:coal": 32},
    "block": {"minecraft:coal_block": 4},  # unwrapped by a craft, not by mining
    "ore": {"minecraft:coal_ore": 32},  # one furnace queue
}
# containers west and north of the start (the house stands east and south of it);
# a container's slots are what limits it, and the server splits any stack it writes
# at the item's own limit, so a cap counts slots: bin 0 gives four to BIN_TOOLS
# (two scaffolding stacks, the body carries both, and two single-stack shulker boxes)
BIN_TOOLS = [
    {"item": "minecraft:scaffolding", "count": 64},
    {"item": "minecraft:scaffolding", "count": 64},
    {"item": "minecraft:shulker_box", "count": 2},
]  # what the build works with, not its materials
TOOL_SLOTS = 4  # BIN_TOOLS as slots: a shulker box stacks to 1
CHEST_SLOTS = 27
CAPS = [CHEST_SLOTS - TOOL_SLOTS] + [CHEST_SLOTS] * 7
OFFSETS = [
    [-2, 0, 0],
    [-2, 0, -1],
    [-3, 0, 0],
    [-3, 0, -1],
    [-2, 0, -2],
    [-3, 0, -2],
    [-2, 0, -3],
    [-3, 0, -3],
]
STATES = ["minecraft:chest", "minecraft:barrel"]
SUPPLY_ROUNDS = 8
SUPPLY_MARGIN = 4  # slack per raw item, so a batch margin never means a search
SUPPLY_FOOD = {"minecraft:cooked_beef": 32}  # the body carries 16 and eats on the job


def supply_contents(build: dict, knowledge, coal: str) -> dict[str, int]:
    """What the bins must hold for the run to build without searching: the house's
    blocks (except the coal consumers, crafted from the chosen `--coal` form) plus,
    round after round, whatever a plan over that stock still wants to take from the
    world - until the plan asks for nothing out there."""
    targets = materials_of(build, knowledge)
    give = {i: n for i, n in targets.items() if i not in COAL_CONSUMERS}
    give.update(COAL_FORMS[coal])
    give.update(SUPPLY_FOOD)
    for _ in range(SUPPLY_ROUNDS):
        # planned against the layout the run will really lay out: a chest's slot
        # count changes how the planner batches and what it prefers to mine
        done = plan(targets, {}, knowledge, storage=supply_boxes(give, knowledge))
        if done.missing:
            raise RuntimeError(f"supply cannot cover the build: {done.missing}")
        wanted: Counter[str] = Counter()
        for t in done.tasks:
            if isinstance(t, Gather | Hunt | Shear):
                wanted[t.item or t.block] += t.item_count or t.count
        if not wanted:
            return give
        give.update({i: give.get(i, 0) + n + SUPPLY_MARGIN for i, n in wanted.items()})
    raise RuntimeError(f"supply did not converge, still wanted: {sorted(wanted)}")


def supply_bins(give: dict[str, int], knowledge) -> list[list[dict]]:
    """The stock packed into stack chunks per container, in placement order."""
    bins = pack_into(give, knowledge, CAPS)
    if len(bins) > len(OFFSETS):
        raise RuntimeError(
            f"supply needs {len(bins)} containers, {len(OFFSETS)} laid out"
        )
    return bins


def supply_boxes(give: dict[str, int], knowledge) -> list[dict]:
    """The same stock as planner storage: contents and the offset it stands at.
    Stack chunks add up to the kind's total (the planner's storage view has no
    slot model, the packing above is what gives one)."""
    boxes = []
    for n, contents in enumerate(supply_bins(give, knowledge)):
        held: Counter[str] = Counter()
        for c in (BIN_TOOLS if n == 0 else []) + contents:
            held[c["item"]] += c["count"]  # a kind can span chunks in one bin
        boxes.append({"kind": "chest", "pos": OFFSETS[n], "contents": dict(held)})
    return boxes


def pack_into(items: dict[str, int], knowledge, caps: list[int]) -> list[list[dict]]:
    """Split into full-stack chunks and pack them into containers by slot count.
    The chunk size is the item's own stack limit, not always 64: two doors are two
    slots, and a container rejects a stack it cannot hold (`container_full`)."""
    chunks: list[dict] = []
    for item, total in sorted(items.items()):
        size = item_stack(knowledge.items, item)
        chunks += [{"item": item, "count": size} for _ in range(total // size)]
        if total % size:
            chunks.append({"item": item, "count": total % size})
    bins, k = [], 0
    for cap in caps:
        bins.append(chunks[k : k + cap])
        k += len(bins[-1])
        if k >= len(chunks):
            break
    if k < len(chunks):
        raise RuntimeError(f"supply needs more than {sum(caps)} slots")
    return [b for b in bins if b]


STAGE_LIBRARY = ROOT / "results" / "stages"


def stage_arg(name: str) -> str:
    """--from: site_ready, band_N or structure_done."""
    if not ck.is_stage(name):
        raise argparse.ArgumentTypeError(
            f"{name!r}: one of site_ready, band_N (storeys), structure_done"
        )
    return name


def until_arg(name: str) -> str:
    """--until: site, band_N or structure."""
    if not ck.is_until(name):
        raise argparse.ArgumentTypeError(f"{name!r}: one of site, band_N, structure")
    return name


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_start(build: Path, start, seed, stage: str, library: Path = STAGE_LIBRARY):
    """The library file and data of ``stage`` for this build, start and seed.
    FileNotFoundError: none saved yet; ValueError: taken from another build file."""
    key = run_digest.build_key(
        {"job": "build", "build": str(build), "start": list(start), "seed": seed},
        segment=False,
    )
    path = ck.find_stage(key, stage, library)
    data = ck.load(path)
    ck.check_build(data, _sha256(build))
    return path, data


def build_rows(report, d, *, ticks, until=None, saved=False) -> list[dict]:
    """The build's summary rows. A stage lab (``until``) passes at its milestone with
    its stage saved; the rest of the house is left unbuilt on purpose."""
    reached = (
        report.stop == "done"
        if until is None
        # a house finished whole has every band and the structure in it
        else (
            report.stop == f"until_{until}"
            or (until != "site" and report.stop == "done")
        )
    )
    rows = [
        {
            "case": "build stop",
            "pass": reached,
            "detail": f"{report.stop} steps={report.steps} counts={report.counts} ticks={ticks}",
        },
        {
            "case": "no unknown cells",
            "pass": not report.unknown,
            "detail": f"{len(report.unknown)} unknown",
        },
    ]
    if until is not None:
        stage = ck.until_stage(until)
        rows.append(
            {
                "case": "stage saved",
                "pass": saved,
                "detail": f"checkpoints/{stage}.json" if saved else f"no {stage}",
            }
        )
        return rows
    clean = not (d.breaks or d.places or d.unknown or d.uses)
    rows.append(
        {
            "case": "empty diff",
            "pass": clean,
            "detail": f"breaks={len(d.breaks)} places={len(d.places)} unknown={len(d.unknown)} uses={len(d.uses)}",
        }
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", nargs=3, type=int)
    parser.add_argument(
        "--resume",
        type=Path,
        help="a checkpoint (results/companion/<run>/checkpoints/NNN.json): set the world "
        "up as it was then and run only the build",
    )
    parser.add_argument(
        "--from",
        dest="start_from",
        type=stage_arg,
        help="start from the stage library's snapshot of this build, start and seed "
        "(results/stages/): site_ready (site cleared and level, chests as they were), "
        "band_N (a storeys build with the structure of bands 0..N in) or "
        "structure_done (only the details left)",
    )
    parser.add_argument(
        "--until",
        type=until_arg,
        help="end the build at a milestone and save its stage: site (cleared and "
        "level), band_N (bands 0..N built; --order storeys) or structure (only the "
        "details left)",
    )
    parser.add_argument(
        "--order",
        choices=bp.ORDERS,
        help="the build order: zones (8x8 work zones, bottom up; the default) or "
        "storeys (floor, then the walls all round three high, then the next floor)",
    )
    parser.add_argument("--build", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--dx", type=int, default=3)
    parser.add_argument("--dz", type=int, default=2)
    parser.add_argument("--rotation", type=int, default=0)
    parser.add_argument(
        "--margin",
        type=int,
        default=1,
        help="blocks of levelled, cleared ground prepared around the footprint "
        "before building (>= 0; default 1)",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="keep the completed attempt and preview in-game until a STOP file is created",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="kit a reference copy beside the attempt; diagnostic run only",
    )
    parser.add_argument(
        "--preview-offset",
        nargs=3,
        type=int,
        metavar=("X", "Y", "Z"),
        help="reference displacement from build origin (default: template width + 5 on X)",
    )
    parser.add_argument(
        "--empty", action="store_true", help="M5: start empty, gather everything"
    )
    parser.add_argument(
        "--supply",
        action="store_true",
        help="empty hands, but the materials sit in supply containers beside the "
        "start: withdraw, craft and build without ever searching for a block",
    )
    parser.add_argument(
        "--coal",
        choices=("item", "block", "ore"),
        default="item",
        help="with --supply: which coal form the chests hold; lanterns and the "
        "campfire are never given, the bot must craft them from this coal",
    )
    parser.add_argument(
        "--stage",
        action="store_true",
        help="with --resume or --from: set the world up, then wait (join at "
        "127.0.0.1:25575) until the run folder gets a file named STOP; no build",
    )
    parser.add_argument(
        "--scaffolding",
        type=int,
        help="kit this much scaffolding instead of dirt; the run must give it all back",
    )
    parser.add_argument(
        "--seed", type=int, help="run in a fresh copy of this natural seed's world"
    )
    parser.add_argument(
        "--show-markers",
        dest="show_markers",
        action=argparse.BooleanOptionalAction,
        help="in-game markers: red planned route, green stance, yellow block (on "
        "by default; --no-show-markers starts them off); create or delete "
        "<run folder>/MARKERS to flip them mid-run",
    )
    args, config = parse_run(parser, "build")
    with digest_on_exit(), launch_record(config):
        result = run(args, config, parser)
        record_outcome(result)
        return result


def _gather_failure_counts(job) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for g in (job.starter, job.gather, *job.refills):
        if g is None:
            continue
        for _, why in g.failures:
            counts[str(why)] += 1
    return dict(counts.most_common())


def summarize_run(
    job,
    *,
    setup_s: float = 0.0,
    audit_s: float = 0.0,
) -> dict:
    """Summarize total wall time, per-phase timings, failures, and long stops."""
    report = job.build
    phases = {"setup_arena": round(setup_s, 2)}
    phases.update(job.phases_s)
    phases["audit"] = round(audit_s, 2)
    total_s = round(setup_s + job.duration_s + audit_s, 2)

    build_failures: Counter[str] = Counter()
    if report is not None:
        for _, why, _, _ in report.failures:
            build_failures[str(why)] += 1
    gather_failures = _gather_failure_counts(job)
    build_total = sum(build_failures.values())
    gather_total = sum(gather_failures.values())

    long_stops = list(report.long_stops) if report is not None else []
    stalls_n = len(report.stalls) if report is not None else 0
    slow_n = sum(1 for s in long_stops if s.get("kind") == "slow_op")
    max_stop_s = max((float(s.get("duration_s", 0.0)) for s in long_stops), default=0.0)
    total_stop_s = round(sum(float(s.get("duration_s", 0.0)) for s in long_stops), 1)

    return {
        "duration_s": total_s,
        "job_duration_s": round(job.duration_s, 2),
        "phases_s": phases,
        "stages_s": dict(job.stages_s),
        "failure_summary": {
            "total": build_total + gather_total,
            "build": build_total,
            "build_by_reason": dict(build_failures.most_common()),
            "gather": gather_total,
            "gather_by_reason": gather_failures,
        },
        "long_stops_summary": {
            "count": len(long_stops),
            "stalls": stalls_n,
            "slow_ops": slow_n,
            "total_s": total_stop_s,
            "max_s": round(max_stop_s, 1),
            "items": long_stops,
        },
    }


def format_run_summary(summary: dict) -> str:
    """Human-readable end-of-run timing, failure, and long-stop report."""
    lines = [
        "=== Build Run Summary ===",
        f"Total time: {summary['duration_s']:.1f}s (job {summary['job_duration_s']:.1f}s)",
    ]
    for name, sec in summary["phases_s"].items():
        if sec > 0 or name in (
            "gathering",
            "clearing_and_site_prep",
            "building_structure",
            "placing_details",
            "refills",
            "cleanup",
        ):
            lines.append(f"  {name}: {sec:.1f}s")
    fs = summary["failure_summary"]
    lines.append(
        f"Failures: {fs['total']} total "
        f"(build={fs['build']} {fs['build_by_reason']}, "
        f"gather={fs['gather']} {fs['gather_by_reason']})"
    )
    ls = summary["long_stops_summary"]
    lines.append(
        f"Long stops (>5.0s): {ls['count']} total "
        f"(stalls={ls['stalls']}, slow_ops={ls['slow_ops']}, "
        f"total={ls['total_s']:.1f}s, max={ls['max_s']:.1f}s)"
    )
    for item in ls["items"]:
        kind = item.get("kind", "stop")
        op = item.get("op")
        target = item.get("target")
        at = item.get("at")
        dur = item.get("duration_s", 0.0)
        extra = (
            f"recovery={item.get('recovery')}"
            if "recovery" in item
            else f"ok={item.get('ok')}"
        )
        lines.append(f"  - [{kind}] {op} {target} at {at} ({dur}s, {extra})")
    return "\n".join(lines)


def run(args, config, parser):
    # the mode decides the job: a run without --supply has no scaffolding
    print(f"run-build: arguments {' '.join(sys.argv[1:])}", flush=True)
    run_started = time.monotonic()
    if args.margin < 0:
        parser.error("--margin must be >= 0")

    start_from = getattr(args, "start_from", None)
    until = getattr(args, "until", None)
    if args.stage and not (args.resume or start_from):
        parser.error("--stage requires --resume or --from")
    if args.supply and (args.empty or args.resume):
        parser.error("--supply is an empty-hands mode: not with --empty or --resume")
    if args.coal != "item" and not args.supply:
        parser.error("--coal requires --supply")
    if args.preview_offset and not args.preview:
        parser.error("--preview-offset requires --preview")
    resume = ck.load(args.resume) if args.resume else None
    stage_file = staged = None
    if start_from:
        try:
            stage_file, staged = stage_start(
                args.build, args.start, args.seed, start_from
            )
        except (FileNotFoundError, ValueError) as error:
            parser.error(str(error))
        origin_asked = [args.start[0] + args.dx, args.start[1], args.start[2] + args.dz]
        if (staged["origin"], staged["rotation"]) != (origin_asked, args.rotation):
            parser.error(
                f"the {start_from} stage was built at origin {staged['origin']} "
                f"rotation {staged['rotation']}, not {origin_asked} {args.rotation}"
            )
    if resume:
        args.start, args.seed, args.rotation = (
            resume["start"],
            resume["seed"],
            resume["rotation"],
        )
        args.build = ROOT / resume["build"]
    elif not args.start:
        parser.error("--start is required without --resume")
    folder = results_folder()
    folder.mkdir(parents=True, exist_ok=True)
    # Resolution precedes admission; a changed checkpoint must not change the task.
    actual_origin = (
        list(resume["origin"])
        if resume
        else [args.start[0] + args.dx, args.start[1], args.start[2] + args.dz]
    )
    actual = (args.start, args.seed, args.rotation, actual_origin, args.build.resolve())
    recorded = (
        config["run"]["start"],
        config["run"]["seed"],
        config["run"]["rotation"],
        config["run"]["origin"],
        (ROOT / config["run"]["build"]).resolve(),
    )
    if actual != recorded:
        raise ValueError("Checkpoint changed after launch configuration was resolved")
    record_run(folder, config, invocation=invocation())
    knowledge = load_vanilla_pack()
    build = json.loads(args.build.read_text(encoding="utf-8"))
    origin = (
        tuple(resume["origin"])
        if resume
        else (args.start[0] + args.dx, args.start[1], args.start[2] + args.dz)
    )
    template = bp.Template.from_build(build, origin, args.rotation)
    preview = None
    if args.preview:
        width = (
            max(p[0] for p in template.cells) - min(p[0] for p in template.cells) + 1
        )
        offset = args.preview_offset or [width + 5, 0, 0]
        preview = preview_cells(template, offset)
    kit_items = materials_of(build, knowledge)
    if args.scaffolding:
        kit_items["minecraft:scaffolding"] = args.scaffolding
    else:
        kit_items["minecraft:dirt"] = kit_items.get("minecraft:dirt", 0) + 20
    kit_items.update(bp.tool_needs(template, {}))
    rows: list[dict] = []
    progress = Progress(folder, job="build")
    progress.write(stage="starting")
    with exclusive_arena(), companion_world(args.seed):
        bridge = open_bridge(folder)
        memory = None
        try:
            body, memory = start_session(
                bridge,
                folder,
                args.start,
                dig=True,
                markers=run_switch(folder, on=args.show_markers),
                **policies(config),
            )
            body.places.remember("build_site", args.start)
            chests = []
            restored = resume or staged
            if restored:
                body.observe()
                feet = body.position()
                began = time.monotonic()
                n = ck.restore(
                    restored,
                    feet,
                    lambda **f: kit(bridge, **f),
                    stack=lambda item: item_stack(knowledge.items, item),
                )
                bridge.ticks(60)  # let the sensor deliver the restored cells
                chests = ck.chests_of(restored)
                source = args.resume.name if resume else f"{start_from} {stage_file}"
                if staged:  # the library stage may be replaced later: keep this one
                    ck.save(folder / "checkpoints" / f"from_{start_from}.json", staged)
                rows.append(
                    {
                        "case": "resumed" if resume else "restored",
                        "pass": True,
                        "detail": f"{n} cells, {len(chests)} chests from {source} "
                        f"in {time.monotonic() - began:.1f}s",
                    }
                )
            elif args.empty:
                # a supply chest beside the start: scaffolding and two shulker boxes
                # west of the start: the house stands east of it
                chest = [args.start[0] - 2, args.start[1], args.start[2]]
                kit(
                    bridge,
                    inventory=[],
                    place=[
                        {
                            "state": "minecraft:chest[facing=east]",
                            "offset": [-2, 0, 0],
                            "contents": [
                                {"item": "minecraft:scaffolding", "count": 64},
                                {"item": "minecraft:shulker_box", "count": 2},
                            ],
                        }
                    ],
                )
                chests.append(chest)
            elif args.supply:
                # the materials the plan would otherwise search for, in containers
                # west and north of the start (the house stands east and south of
                # it); the coal consumers are missing on purpose, crafted from the
                # chosen coal form instead
                bins = supply_bins(
                    supply_contents(build, knowledge, args.coal), knowledge
                )
                place = []
                for n, contents in enumerate(bins):
                    ox, _, oz = OFFSETS[n]
                    chests.append(
                        [args.start[0] + ox, args.start[1], args.start[2] + oz]
                    )
                    place.append(
                        {
                            "state": STATES[n % 2] + "[facing=east]",
                            "offset": OFFSETS[n],
                            "contents": (BIN_TOOLS if n == 0 else []) + contents,
                        }
                    )
                kit(
                    bridge,
                    inventory=[
                        {"item": f"minecraft:iron_{t}", "count": 1}
                        # ponytail: two spare shovels. One iron shovel (250 uses)
                        # wore out in site prep and the rest was dug by hand at
                        # 2.3x the time (run 20261110T171038); the body picks the
                        # best tool held, so a spare takes over with no new code.
                        for t in ("pickaxe", "axe", "shovel", "shovel", "shovel", "hoe")
                    ],
                    place=place,
                )
            else:
                kit(
                    bridge,
                    clear=CLEAR,
                    inventory=[{"item": i, "count": n} for i, n in kit_items.items()],
                )
            body.observe()
            if preview is not None and args.stage:
                placed = place_preview(
                    preview, body.position(), lambda **f: kit(bridge, **f)
                )
                (folder / "preview.json").write_text(
                    json.dumps(
                        {"offset": offset, "cells": placed, "diagnostic_only": True}
                    ),
                    encoding="utf-8",
                )
            if args.stage:
                (folder / "STOP").unlink(missing_ok=True)
                progress.write(stage="staged")
                print(f"STAGED {folder} - create {folder / 'STOP'} to end", flush=True)
                while not (folder / "STOP").exists():
                    time.sleep(2)
                rows.append(
                    {
                        "case": "staged",
                        "pass": True,
                        "detail": args.resume.name if resume else start_from,
                    }
                )
                return finish(folder, rows, "run-build")
            key = run_digest.build_key(
                {
                    "job": "build",
                    "build": str(args.build),
                    "start": list(args.start),
                    "seed": args.seed,
                },
                segment=False,
            )
            git = json.loads(
                (folder / "provenance.json").read_text(encoding="utf-8")
            ).get("git", {})
            checkpoint = ck.Recorder(
                folder,
                template,
                body,
                {
                    "seed": args.seed,
                    "start": list(args.start),
                    "build": args.build.resolve().relative_to(ROOT).as_posix(),
                    "build_sha256": _sha256(args.build),
                    "origin": list(origin),
                    "rotation": args.rotation,
                },
                every=5,
                area=ck.site_box(template, args.margin),
                # a resumed checkpoint may not hold the site the way a stage does
                library=None
                if resume
                else ck.stage_path(key, ck.STAGES[0], STAGE_LIBRARY).parent,
                source={
                    "run": folder.name,
                    "commit": git.get("commit"),
                    "dirty": git.get("dirty"),
                },
            )
            state = GatherState()
            hydrate_containers(body, state)
            # the stage's standing scaffolding is this run's own to climb and take back
            scaffolds = ck.scaffolds_of(staged) if staged else set()
            # A run from a stage does not save that stage or an earlier one again.
            behind = ck.stage_rank(start_from) if start_from else -1

            def milestone(name):
                if ck.stage_rank(name) <= behind:
                    return
                checkpoint.milestone(
                    name, containers=state.containers, scaffolds=scaffolds
                )
                print(f"STAGE {name} saved", flush=True)

            current_stage = "starting"

            def stage(name, done=None, total=None, detail=None):
                nonlocal current_stage
                current_stage = name
                progress.write(stage=name, done=done, total=total, detail=detail)

            def recorder(report, final):
                checkpoint(report, final)
                site = report.site or {}
                live_stage = (
                    current_stage
                    if current_stage not in ("starting", "clearing trees", "site prep")
                    else ("placing" if report.site else "site prep")
                )
                progress.write(
                    stage="done" if final else live_stage,
                    done=len(report.placed),
                    total=sum(
                        bp.parse_state(c)[0] not in bp.AIR
                        for c in template.cells.values()
                    ),
                    detail=f"site={site.get('stop') or 'preparing'} "
                    f"cleared={site.get('cleared', 0)} filled={site.get('filled', 0)} "
                    f"steps={report.steps} stalls={len(report.stalls)} stop={report.stop}",
                )

            setup_s = time.monotonic() - run_started
            job = build_job(
                args.build,
                origin,
                args.rotation,
                body,
                knowledge,
                gather_options=GatherOptions(**config["run"]["gather"]),
                clock=lambda: int(body.observe()["tick"]),
                state=state,
                chests=chests,
                # a stage holds the chests: read and withdraw from them as a supply
                # run does; a plain checkpoint holds the inventory only
                build_only=bool(resume),
                on_progress=recorder,
                on_stage=stage,
                site_margin=args.margin,
                until=until,
                on_milestone=milestone,
                scaffolds=scaffolds,
                order=config["run"]["order"],
            )
            audit_started = time.monotonic()
            report = job.build
            # The job is over: the harness waits below are not the body's work, so
            # idle_gaps.py ends the standstill window here.
            bridge.record("job_finished", {"job": "build", "stop": job.stop})
            phases = {
                name: None
                if g is None
                else {"stop": g.stop, "tasks": len(g.done), "failures": g.failures}
                for name, g in (("starter", job.starter), ("gather", job.gather))
            }
            out = {
                "policy": body.policy_snapshot(),
                "diagnostic_preview": bool(args.preview),
                "argv": sys.argv[1:],
                "supplied": bool(args.supply),
                "from_stage": None
                if not staged
                else {
                    "stage": start_from,
                    "library": str(stage_file),
                    "source": staged.get("source"),
                },
                "until": until,
                "order": config["run"]["order"],
                "coal_form": args.coal if args.supply else None,
                "job_stop": job.stop,
                "ticks": job.ticks,
                "needed": job.needed,
                "shortfall": job.shortfall,
                **phases,
                "refills": [r.stop for r in job.refills],
            }
            rows.append(
                {
                    "case": "gathered",
                    "pass": job.stop != "gather_failed",
                    "detail": f"{job.stop} {phases}",
                }
            )
            if report is not None:
                write_walkthrough(folder, template, report.failures)
                bridge.ticks(60)  # let the sensor re-deliver the placed cells
                body.observe()
                d = bp.diff(template, body.world)
                out |= {
                    "stop": report.stop,
                    "site": report.site,
                    "stalls": report.stalls,
                    "long_stops": report.long_stops,
                    "stall_reason": report.stall_reason,
                    "steps": report.steps,
                    "counts": report.counts,
                    "surveyed": report.surveyed,
                    "unknown": [list(p) for p in report.unknown],
                    "skipped": {str(p): r for p, r in report.skipped.items()},
                    "scaffolded": [list(p) for p in report.scaffolded],
                    "failures": [
                        [list(p), why, list(at), detail]
                        for p, why, at, detail in report.failures
                    ],
                    "final_diff": {
                        "breaks": len(d.breaks),
                        "places": len(d.places),
                        "unknown": len(d.unknown),
                        "uses": len(d.uses),
                    },
                }
                saved = (
                    bool(until)
                    and (
                        folder / "checkpoints" / f"{ck.until_stage(until)}.json"
                    ).is_file()
                )
                rows += build_rows(report, d, ticks=job.ticks, until=until, saved=saved)
            if args.scaffolding:
                left = body.inventory().get("minecraft:scaffolding", 0)
                rows.append(
                    {
                        "case": "scaffolding all picked up",
                        "pass": left == args.scaffolding,
                        "detail": f"{left} of {args.scaffolding} held; placed {len(out.get('scaffolded', []))}",
                    }
                )
            audit_s = time.monotonic() - audit_started
            run_summary = summarize_run(job, setup_s=setup_s, audit_s=audit_s)
            out |= run_summary
            out["ops"] = body.log
            out["policies"] = body.policies
            (folder / "build.json").write_text(
                json.dumps(out, indent=1), encoding="utf-8"
            )
            print(format_run_summary(run_summary), flush=True)
            # Place only after the job: a reference house must not supply gather
            # materials or change the routes used by the attempted construction.
            if preview is not None:
                placed = place_preview(
                    preview, body.position(), lambda **f: kit(bridge, **f)
                )
                (folder / "preview.json").write_text(
                    json.dumps(
                        {"offset": offset, "cells": placed, "diagnostic_only": True}
                    ),
                    encoding="utf-8",
                )
            if args.inspect:
                (folder / "STOP").unlink(missing_ok=True)
                progress.write(stage="inspection")
                print(f"INSPECT {folder} - create {folder / 'STOP'} to end", flush=True)
                while not (folder / "STOP").exists():
                    bridge.state()  # heartbeat while the human examines the build
                    time.sleep(2)
        except Exception as error:  # noqa: BLE001 - recorded, run ends as failed
            record_crash(folder, rows, error)
        finally:
            try:
                if memory is not None:
                    end_session(bridge, memory)
            finally:
                bridge.close()
    return finish(folder, rows, "run-build")


if __name__ == "__main__":
    sys.exit(main())
