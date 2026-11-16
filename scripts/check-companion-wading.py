"""Unscored regression at the natural pond where site prep became trapped.

Uses delivered geometry to choose a shallow-water waypoint and a dry bank.
No building materials or terrain edits; normal session/world cleanup preserves
the lab. Evidence is kept even if either movement fails.
"""

import argparse
import math
from datetime import UTC, datetime

from mclab.collision_geometry import standable
from mclab.companion_body import (
    companion_world,
    end_session,
    open_bridge,
    start_session,
)
from mclab.paths import ROOT, write_json
from mclab.recorder import exclusive_arena
from mclab.run_launch import script_run


@script_run("companion-wading")
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trapped",
        action="store_true",
        help="Replay the large-house tall-seagrass trap",
    )
    args = parser.parse_args()
    folder = ROOT / "results/wading" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    summary = {
        "development_only": True,
        "seed": 31415926,
        "attempts": [],
        "passed": False,
    }
    try:
        with exclusive_arena(), companion_world(31415926):
            bridge = open_bridge(folder)
            memory = None
            try:
                spawn = (-17, 62, 71) if args.trapped else (-16, 62, 70)
                body, memory = start_session(bridge, folder, spawn)
                start = body.position()
                cells = body._cells_now()
                candidates = [
                    p
                    for p in cells
                    if standable(cells, p) and 1.5 <= math.dist(p, start) <= 7
                ]
                wet = [
                    p
                    for p in candidates
                    if p[1] == start[1] and cells[p].get("fluid") == "minecraft:water"
                ]
                dry = [
                    p
                    for p in candidates
                    if p[1] > start[1]
                    and cells[p].get("fluid", "minecraft:empty") == "minecraft:empty"
                ]
                if not wet or not dry:
                    raise RuntimeError(
                        "No delivered wading/bank goals at regression start"
                    )
                goals = [
                    min(wet, key=lambda p: math.dist(p, start)),
                    min(dry, key=lambda p: math.dist(p, start)),
                ]
                if args.trapped:
                    # Enter the tall-seagrass column from its adjacent shallow
                    # shelf, then leave for the bank. No teleport or terrain edit.
                    goals.insert(0, (-19, 63, 71))
                kinds = (
                    ("plant_water", "wade", "bank")
                    if args.trapped
                    else ("wade", "bank")
                )
                for kind, goal in zip(kinds, goals, strict=True):
                    before = body.position()
                    swim = body.movement_policy.allow_swim
                    with body.no_placing(wall=15):
                        ok = body.walk_to(goal, radius=0.45)
                    after = body.position()
                    row = {
                        "kind": kind,
                        "before": before,
                        "goal": goal,
                        "after": after,
                        "swimming_enabled": swim,
                        "ok": ok,
                    }
                    summary["attempts"].append(row)
                    print(row, flush=True)
                    if (
                        not ok
                        # swimming is always on: a wade must still end on the
                        # shallow floor, not afloat
                        or (kind == "wade" and math.floor(after[1]) != spawn[1])
                        or math.dist(before, after) < 1
                    ):
                        raise RuntimeError(f"Failed {kind} regression")
                summary["ops"] = body.log
                summary["passed"] = True
            finally:
                try:
                    if memory is not None:
                        summary["ops"] = body.log
                        end_session(bridge, memory)
                finally:
                    bridge.close()
    except Exception as exc:
        summary["error"] = repr(exc)
        raise
    finally:
        write_json(folder / "summary.json", summary)
        print(folder / "summary.json", flush=True)


if __name__ == "__main__":
    main()
