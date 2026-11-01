# ruff: noqa: E501
"""Live check: the kit op and every body action op inside a running natural_start session.

Usage: rtk proxy uv run --locked python scripts/check-natural-ops.py [--start X Y Z]
(idle lab; start = a feet cell on open ground). A follow_route first, so a finished route must not block the ops.
"""

import argparse
import sys

from live_common import at, call, feet_cell, finish, kit, results_folder

from mclab.companion_body import end_session, open_bridge, start_session
from mclab.recorder import exclusive_arena


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", nargs=3, type=int, default=[990, 80, 988])
    args = parser.parse_args()
    folder = results_folder()
    folder.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    def record(case, ok, detail):
        rows.append({"case": case, "pass": bool(ok), "detail": str(detail)})

    with exclusive_arena():
        bridge = open_bridge(folder)
        memory = None
        try:
            body, memory = start_session(bridge, folder, args.start)
            state = bridge.http("/state")
            record(
                "natural session running", state["state"] == "running", state["state"]
            )
            feet = feet_cell({"body": {"position": body.observe()["body"]["position"]}})
            r = kit(
                bridge,
                clear=[6, 3, 6],
                inventory=[
                    {"item": "minecraft:oak_log", "count": 8},
                    {"item": "minecraft:chest", "count": 1},
                    {"item": "minecraft:iron_sword", "count": 1},
                    {"item": "minecraft:cooked_beef", "count": 2},
                    {"item": "minecraft:stone", "count": 4},
                    {"item": "minecraft:wooden_pickaxe", "count": 1},
                ],
                place=[{"state": "minecraft:stone", "offset": [6, 0, 0]}],
                food=8,
            )
            record(
                "kit inside a natural session", r["type"] == "kit_done", r.get("stacks")
            )

            # a route first: a finished route must not leave ops blocked (route_active)
            moved = body.goto_stance(at(feet, 6, 0, 0), 2.0)
            record("follow_route to a stance (finishes)", moved, body.position())
            body.observe()
            feet = feet_cell({"body": {"position": body.observe()["body"]["position"]}})
            cow = kit(
                bridge,
                clear=[3, 2, 3],
                summon=[{"entity": "minecraft:cow", "offset": [-2, 0, 0]}],
            )["summoned_entities"][0]["uuid"]

            def ops(label, op, ok, **fields):
                r = call(bridge, op, **fields)
                record(f"{label} after a route", ok(r), r.get("reason", r["type"]))
                return r

            ops(
                "equip",
                "equip",
                lambda r: r["type"] == "equip_done",
                item="minecraft:iron_sword",
            )
            ops(
                "craft planks",
                "craft",
                lambda r: r["type"] == "craft_done",
                recipe="minecraft:oak_planks",
                times=1,
            )
            ops(
                "place_block",
                "place_block",
                lambda r: r["type"] == "place_block_done",
                pos=at(feet, 1, 0, 0),
                state="minecraft:stone",
            )
            ops(
                "use_block",
                "use_block",
                lambda r: r.get("reason") in (None, "no_effect"),
                pos=at(feet, 1, 0, 0),
            )
            ops(
                "break_block",
                "break_block",
                lambda r: r["type"] == "break_block_done",
                pos=at(feet, 1, 0, 0),
            )
            ops(
                "place chest",
                "place_block",
                lambda r: r["type"] == "place_block_done",
                pos=at(feet, 0, 0, 2),
                state="minecraft:chest[facing=north]",
            )
            r = ops(
                "container deposit",
                "container",
                lambda r: r["type"] == "container_done",
                pos=at(feet, 0, 0, 2),
                action="deposit",
                items=[{"item": "minecraft:oak_log", "count": 4}],
            )
            record(
                "container holds the logs",
                sum(s["count"] for s in r.get("contents", [])) == 4,
                r.get("contents"),
            )
            ops(
                "eat",
                "eat",
                lambda r: r["type"] == "eat_done" and r["food_after"] > 8,
                item="minecraft:cooked_beef",
            )
            ops(
                "attack_entity",
                "attack_entity",
                lambda r: r["type"] == "attack_entity_done" and r.get("killed"),
                entity=cow,
            )
            ops(
                "use_entity",
                "use_entity",
                lambda r: r.get("reason") == "not_found",
                entity=cow,
            )

            # explicit kit refusal is still in force for course episodes (not exercised here)
        finally:
            try:
                if memory is not None:
                    end_session(bridge, memory)
            finally:
                bridge.close()
    return finish(folder, rows, "check-natural-ops")


if __name__ == "__main__":
    sys.exit(main())
