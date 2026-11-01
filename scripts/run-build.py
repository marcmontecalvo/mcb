# ruff: noqa: E501
"""Live build run (companion track, M2 on the real body). Written, not yet run.

Usage (lab server up, idle lab; start = a feet cell on flat, open ground):
    rtk proxy uv run --locked python scripts/run-build.py --start X Y Z [--build FILE] [--dx 3 --dz 2]

Kits the build's material list (plus dirt for scaffolds and a hoe/shovel if needed), clears a box
round the start, then builds tests/fixtures/schematics/small-house.build.json at start+(dx,0,dz)
through companion_body.CompanionBody + build_runner.run_build (footprint survey on).
Pass: build stop == done, no unknown cells, and the diff against the delivered map is empty.
"""

import argparse
import json
import sys
from pathlib import Path

from live_common import finish, kit, results_folder

from mclab import build_plan as bp
from mclab.build_runner import run_build
from mclab.companion_body import end_session, open_bridge, start_session
from mclab.companion_jobs import load_vanilla_pack
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
    args = parser.parse_args()
    folder = results_folder()
    folder.mkdir(parents=True, exist_ok=True)
    knowledge = load_vanilla_pack()
    build = json.loads(args.build.read_text(encoding="utf-8"))
    origin = (args.start[0] + args.dx, args.start[1], args.start[2] + args.dz)
    template = bp.Template.from_build(build, origin, args.rotation)
    kit_items = dict(build["materials"])
    kit_items["minecraft:dirt"] = kit_items.get("minecraft:dirt", 0) + 20
    kit_items.update(bp.tool_needs(template, {}))
    rows: list[dict] = []
    with exclusive_arena():
        bridge = open_bridge(folder)
        memory = None
        try:
            body, memory = start_session(bridge, folder, args.start)
            kit(
                bridge,
                clear=CLEAR,
                inventory=[{"item": i, "count": n} for i, n in kit_items.items()],
            )
            body.observe()
            report = run_build(template, body, knowledge.blocks, survey=True)
            bridge.ticks(60)  # let the sensor re-deliver the placed cells
            body.observe()
            d = bp.diff(template, body.world)
            (folder / "build.json").write_text(
                json.dumps(
                    {
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
                        "ops": body.log,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
            clean = not (d.breaks or d.places or d.unknown or d.uses)
            rows += [
                {
                    "case": "build stop",
                    "pass": report.stop == "done",
                    "detail": f"{report.stop} steps={report.steps} counts={report.counts}",
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
        finally:
            try:
                if memory is not None:
                    end_session(bridge, memory)
            finally:
                bridge.close()
    return finish(folder, rows, "run-build")


if __name__ == "__main__":
    sys.exit(main())
