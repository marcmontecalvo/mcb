# ruff: noqa: E501
"""Live build run (companion track: M2 with materials given, M5 with --empty).

Usage (lab server up, idle lab; start = a feet cell on open ground):
    rtk proxy uv run --locked python scripts/run-build.py --start X Y Z [--build FILE] [--dx 3 --dz 2]
    rtk proxy uv run --locked python scripts/run-build.py --start X Y Z --empty --seed 42424242
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
import json
import sys
import time
from collections import Counter
from pathlib import Path

from live_common import finish, kit, record_crash, results_folder

from mclab import build_checkpoint as ck
from mclab import build_plan as bp
from mclab.build_debug import place_preview, preview_cells, write_walkthrough
from mclab.companion_body import (
    companion_world,
    end_session,
    open_bridge,
    start_session,
)
from mclab.companion_jobs import build_job, load_vanilla_pack, materials_of
from mclab.companion_progress import write_progress
from mclab.gather_options import GatherOptions
from mclab.gather_plan import Gather, Hunt, Shear, plan
from mclab.gather_tasks import item_stack
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
# at the item's own limit, so a cap counts slots: bin 0 gives three to BIN_TOOLS
# (a scaffolding stack and two single-stack shulker boxes)
CAPS = [24, 27, 27, 27, 27, 27, 27, 27]
BIN_TOOLS = [
    {"item": "minecraft:scaffolding", "count": 64},
    {"item": "minecraft:shulker_box", "count": 2},
]  # the first bin's three spare slots: what the build works with, not its materials
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


def supply_contents(build: dict, knowledge, coal: str) -> dict[str, int]:
    """What the bins must hold for the run to build without searching: the house's
    blocks (except the coal consumers, crafted from the chosen `--coal` form) plus,
    round after round, whatever a plan over that stock still wants to take from the
    world - until the plan asks for nothing out there."""
    targets = materials_of(build, knowledge)
    give = {i: n for i, n in targets.items() if i not in COAL_CONSUMERS}
    give.update(COAL_FORMS[coal])
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", nargs=3, type=int)
    parser.add_argument(
        "--resume",
        type=Path,
        help="a checkpoint (results/companion/<run>/checkpoints/NNN.json): set the world "
        "up as it was then and run only the build",
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
        help="with --resume: set the world up, then wait (join at 127.0.0.1:25575) "
        "until the run folder gets a file named STOP; no build",
    )
    parser.add_argument(
        "--scaffolding",
        type=int,
        help="kit this much scaffolding instead of dirt; the run must give it all back",
    )
    parser.add_argument(
        "--seed", type=int, help="run in a fresh copy of this natural seed's world"
    )
    args, config = parse_run(parser, "build")
    with launch_record(config):
        result = run(args, config, parser)
        record_outcome(result)
        return result


def run(args, config, parser):
    # the mode decides the job: a run without --supply has no scaffolding
    print(f"run-build: arguments {' '.join(sys.argv[1:])}", flush=True)
    if args.margin < 0:
        parser.error("--margin must be >= 0")

    if args.stage and not args.resume:
        parser.error("--stage requires --resume")
    if args.supply and (args.empty or args.resume):
        parser.error("--supply is an empty-hands mode: not with --empty or --resume")
    if args.coal != "item" and not args.supply:
        parser.error("--coal requires --supply")
    if args.preview_offset and not args.preview:
        parser.error("--preview-offset requires --preview")
    resume = ck.load(args.resume) if args.resume else None
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
    write_progress(folder, job="build", stage="starting")
    with exclusive_arena(), companion_world(args.seed):
        bridge = open_bridge(folder)
        memory = None
        try:
            body, memory = start_session(
                bridge, folder, args.start, dig=True, **policies(config)
            )
            body.places.remember("build_site", args.start)
            chests = []
            if resume:
                body.observe()
                feet = body.position()
                n = ck.restore(resume, feet, lambda **f: kit(bridge, **f))
                bridge.ticks(60)  # let the sensor deliver the restored cells
                rows.append(
                    {
                        "case": "resumed",
                        "pass": True,
                        "detail": f"{n} cells from {args.resume.name}",
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
                write_progress(folder, job="build", stage="staged")
                print(f"STAGED {folder} - create {folder / 'STOP'} to end", flush=True)
                while not (folder / "STOP").exists():
                    time.sleep(2)
                rows.append(
                    {"case": "staged", "pass": True, "detail": args.resume.name}
                )
                return finish(folder, rows, "run-build")
            checkpoint = ck.Recorder(
                folder,
                template,
                body,
                {
                    "seed": args.seed,
                    "start": list(args.start),
                    "build": args.build.resolve().relative_to(ROOT).as_posix(),
                    "origin": list(origin),
                    "rotation": args.rotation,
                },
                every=5,
            )

            def recorder(report, final):
                checkpoint(report, final)
                site = report.site or {}
                write_progress(
                    folder,
                    job="build",
                    stage="done" if final else "building",
                    done=len(report.placed),
                    detail=f"site={site.get('stop') or 'preparing'} "
                    f"cleared={site.get('cleared', 0)} filled={site.get('filled', 0)} "
                    f"steps={report.steps} stalls={len(report.stalls)} stop={report.stop}",
                )

            job = build_job(
                args.build,
                origin,
                args.rotation,
                body,
                knowledge,
                gather_options=GatherOptions(**config["run"]["gather"]),
                clock=lambda: int(body.observe()["tick"]),
                chests=chests,
                build_only=bool(resume),
                on_progress=recorder,
                site_margin=args.margin,
            )
            report = job.build
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
                clean = not (d.breaks or d.places or d.unknown or d.uses)
                rows += [
                    {
                        "case": "build stop",
                        "pass": report.stop == "done",
                        "detail": f"{report.stop} steps={report.steps} counts={report.counts} ticks={job.ticks}",
                    },
                    {
                        "case": "no unknown cells",
                        "pass": not report.unknown,
                        "detail": f"{len(report.unknown)} unknown",
                    },
                    {
                        "case": "empty diff",
                        "pass": clean,
                        "detail": f"breaks={len(d.breaks)} places={len(d.places)} unknown={len(d.unknown)} uses={len(d.uses)}",
                    },
                ]
            if args.scaffolding:
                left = body.inventory().get("minecraft:scaffolding", 0)
                rows.append(
                    {
                        "case": "scaffolding all picked up",
                        "pass": left == args.scaffolding,
                        "detail": f"{left} of {args.scaffolding} held; placed {len(out.get('scaffolded', []))}",
                    }
                )
            out["ops"] = body.log
            out["policies"] = body.policies
            (folder / "build.json").write_text(
                json.dumps(out, indent=1), encoding="utf-8"
            )
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
                write_progress(folder, job="build", stage="inspection")
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
