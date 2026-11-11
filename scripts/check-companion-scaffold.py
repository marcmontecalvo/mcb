"""Unscored native scaffold climb with the normal build movement permissions."""

import math
from dataclasses import replace
from datetime import UTC, datetime

from live_common import kit

from mclab.companion_body import WALK_TERRAIN, end_session, open_bridge, start_session
from mclab.paths import ROOT, write_json
from mclab.recorder import exclusive_arena


def main():
    folder = (
        ROOT / "results/scaffolding" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    folder.mkdir(parents=True)
    summary = {"development_only": True, "passed": False}
    try:
        with exclusive_arena():
            bridge = open_bridge(folder)
            memory, original, touched = None, {}, set()
            try:
                body, memory = start_session(
                    bridge,
                    folder,
                    (990, 80, 988),
                    terrain_policy=replace(WALK_TERRAIN, baritone_moves=False),
                    world_memory=False,
                )
                column = [(993, y, 988) for y in range(80, 86)]
                original = {p: body.world(*p) for p in column}
                if any(s != "minecraft:air" for s in original.values()):
                    raise RuntimeError("Scaffold fixture needs an empty known column")
                kit(bridge, inventory=[{"item": "minecraft:scaffolding", "count": 3}])
                for p in column[:3]:
                    touched.add(p)  # first: a refused placement may still land
                    result = body.place_block(
                        p, "minecraft:scaffolding", "minecraft:scaffolding"
                    )
                    if not result.ok:
                        raise RuntimeError(f"Scaffold placement failed: {result}")
                    touched.add(p)
                body.wait_ticks(5)
                body.observe()
                with body.no_placing(wall=15, climb=True):
                    assert body.movement_policy.allow_climb
                    assert not body.terrain_policy.baritone_moves
                    ok = body.walk_to((993, 83, 988), radius=0.2)
                observed = body.observe()["body"]
                position = observed["position"]
                summary.update(
                    ok=ok,
                    position=position,
                    on_ground=observed.get("on_ground"),
                    ops=body.log,
                    passed=ok
                    and observed.get("on_ground") is True
                    and abs(position[1] - 83) <= 0.2
                    and math.hypot(position[0] - 993.5, position[2] - 988.5) <= 0.2,
                )
                if not summary["passed"]:
                    raise RuntimeError("Scaffold platform was not reached and stood on")
                with body.no_placing(wall=15, climb=True):
                    if not body.walk_to((990, 80, 988), radius=0.2):
                        raise RuntimeError("Could not descend from scaffold platform")
                    before = body._obs["body"]["position"]
                    feet = body.position()
                    axis = max((0, 2), key=lambda i: abs(before[i] - feet[i] - 0.5))
                    offset = before[axis] - feet[axis] - 0.5
                    if abs(offset) < 1e-4:
                        raise RuntimeError("Radius fixture needs an off-centre start")
                    goal = list(feet)
                    goal[axis] += -2 if offset > 0 else 2
                    distance = math.hypot(
                        before[0] - goal[0] - 0.5, before[2] - goal[2] - 0.5
                    )
                    assert distance > 2
                    ok = body.walk_to(tuple(goal), radius=2)
                    summary["radius_boundary"] = {
                        "before": before,
                        "goal": goal,
                        "distance": distance,
                        "ok": ok,
                        "after": body._obs["body"]["position"],
                    }
                    if not ok:
                        raise RuntimeError(
                            "Rounded goal boundary failed to move into radius"
                        )
            finally:
                try:
                    if memory and touched:
                        feet = body.position()
                        kit(
                            bridge,
                            place=[
                                {
                                    "state": state,
                                    "offset": [p[i] - feet[i] for i in range(3)],
                                }
                                for p, state in original.items()
                                if p in touched
                            ],
                        )
                finally:
                    try:
                        if memory:
                            end_session(bridge, memory)
                    finally:
                        bridge.close()
    except Exception as error:
        summary.update(passed=False, error=str(error))
        raise
    finally:
        write_json(folder / "summary.json", summary)
        print(
            {k: v for k, v in summary.items() if k != "ops"}, "->", folder, flush=True
        )


if __name__ == "__main__":
    main()
