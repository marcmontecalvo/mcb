"""Unscored regression: out of a 2-deep pond up a free-hanging vine column.

House-large run 20261119T202232979013Z fell into the pond at (-14, 61, 76),
surfaced, then hung 0.22 off the centre of the vine column above it with its
head under the oak leaves of the next column, and failed the same rise 25 times
(RouteMotor vertical climbs now keep the body inside the column; repeat failures
are banned longer each time). The body swims from the pond's shallow shelf into
the deep water beside the vines, then must reach the canopy top the vines lead
to, without placing blocks.
No teleport after spawn; normal session/world cleanup preserves the lab.
"""

import math
from datetime import UTC, datetime

from mclab.companion_body import (
    companion_world,
    end_session,
    open_bridge,
    start_session,
)
from mclab.paths import ROOT, write_json
from mclab.recorder import exclusive_arena
from mclab.run_launch import script_run

SEED = 31415926
SPAWN = (-16, 62, 70)  # the shallow shelf of the same pond (a standable spawn)
POND = (-13, 63, 76)  # afloat in the 2-deep water beside the vines
VINES = [(-14, y, 76) for y in range(63, 68)]
GOAL = (-15, 68, 75)  # on the oak leaves the vines climb to
LEGS = (("swim_in", POND), ("vine_escape", GOAL))


@script_run("companion-vine-escape")
def main():
    folder = ROOT / "results/wading" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    summary = {"development_only": True, "seed": SEED, "attempts": [], "passed": False}
    try:
        with exclusive_arena(), companion_world(SEED):
            bridge = open_bridge(folder)
            memory = None
            try:
                body, memory = start_session(bridge, folder, SPAWN)
                cells = body._cells_now()
                missing = [
                    p
                    for p in VINES
                    if cells.get(p, {}).get("block") != "minecraft:vine"
                ]
                if missing:
                    raise RuntimeError(
                        f"Scenario terrain changed: no vine at {missing}"
                    )
                for kind, goal in LEGS:
                    before = body.position()
                    with body.no_placing(wall=60):
                        ok = body.walk_to(goal, radius=0.45)
                    after = body.position()
                    row = {
                        "kind": kind,
                        "before": before,
                        "goal": goal,
                        "after": after,
                        "ok": ok,
                    }
                    summary["attempts"].append(row)
                    print(row, flush=True)
                    if not ok or math.dist(after, goal) > 1.5:
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
