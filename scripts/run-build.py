# ruff: noqa: E501
"""Live build run (companion track: M2 with materials given, M5 with --empty).

Usage (lab server up, idle lab; start = a feet cell on open ground):
    rtk proxy uv run --locked python scripts/run-build.py --start X Y Z [--build FILE] [--dx 3 --dz 2]
    rtk proxy uv run --locked python scripts/run-build.py --start X Y Z --empty --seed 42424242

Default (M2): kits the build's material list (plus dirt for scaffolds and a hoe/shovel if
needed) and clears a box round the start. --empty (M5): empty inventory, nothing cleared;
the bot gathers and crafts everything. Either way companion_jobs.build_job builds
tests/fixtures/schematics/small-house.build.json at start+(dx,0,dz) with digging on
(start_session(dig=True)); the template's cells are protected from the route planner.
Pass: build stop == done, no unknown cells, and the diff against the delivered map is empty.
"""

import argparse
import json
import sys
from pathlib import Path

from live_common import finish, kit, results_folder

from mclab import build_plan as bp
from mclab.companion_body import (
    companion_world,
    end_session,
    open_bridge,
    start_session,
)
from mclab.companion_jobs import build_job, load_vanilla_pack, materials_of
from mclab.companion_progress import write_progress
from mclab.paths import ROOT
from mclab.recorder import exclusive_arena

DEFAULT_BUILD = ROOT / "tests/fixtures/schematics/small-house.build.json"
CLEAR = [10, 6, 10]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", nargs=3, type=int, required=True)
    parser.add_argument("--build", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--dx", type=int, default=3)
    parser.add_argument("--dz", type=int, default=2)
    parser.add_argument("--rotation", type=int, default=0)
    parser.add_argument(
        "--empty", action="store_true", help="M5: start empty, gather everything"
    )
    parser.add_argument(
        "--scaffolding",
        type=int,
        help="kit this much scaffolding instead of dirt; the run must give it all back",
    )
    parser.add_argument(
        "--seed", type=int, help="run in a fresh copy of this natural seed's world"
    )
    args = parser.parse_args()
    folder = results_folder()
    folder.mkdir(parents=True, exist_ok=True)
    knowledge = load_vanilla_pack()
    build = json.loads(args.build.read_text(encoding="utf-8"))
    origin = (args.start[0] + args.dx, args.start[1], args.start[2] + args.dz)
    template = bp.Template.from_build(build, origin, args.rotation)
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
            body, memory = start_session(bridge, folder, args.start, dig=True)
            chests = []
            if args.empty:
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
            else:
                kit(
                    bridge,
                    clear=CLEAR,
                    inventory=[{"item": i, "count": n} for i, n in kit_items.items()],
                )
            body.observe()
            job = build_job(
                args.build,
                origin,
                args.rotation,
                body,
                knowledge,
                clock=lambda: int(body.observe()["tick"]),
                chests=chests,
            )
            report = job.build
            phases = {
                name: None
                if g is None
                else {"stop": g.stop, "tasks": len(g.done), "failures": g.failures}
                for name, g in (("starter", job.starter), ("gather", job.gather))
            }
            out = {
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
                bridge.ticks(60)  # let the sensor re-deliver the placed cells
                body.observe()
                d = bp.diff(template, body.world)
                out |= {
                    "stop": report.stop,
                    "steps": report.steps,
                    "counts": report.counts,
                    "surveyed": report.surveyed,
                    "unknown": [list(p) for p in report.unknown],
                    "skipped": {str(p): r for p, r in report.skipped.items()},
                    "scaffolded": [list(p) for p in report.scaffolded],
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
            (folder / "build.json").write_text(
                json.dumps(out, indent=1), encoding="utf-8"
            )
        finally:
            try:
                if memory is not None:
                    end_session(bridge, memory)
            finally:
                bridge.close()
    return finish(folder, rows, "run-build")


if __name__ == "__main__":
    sys.exit(main())
