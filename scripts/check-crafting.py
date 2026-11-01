# ruff: noqa: E501
"""M3 live check: craft, furnace and equip body ops on the lab server (development only)."""

import sys

from live_common import (
    at,
    call,
    count,
    feet_cell,
    finish,
    kit,
    open_bridge,
    results_folder,
    spawn_ready,
)  # fmt: skip

from mclab.recorder import exclusive_arena

SMELT_TICKS = 650


def main() -> int:
    folder = results_folder()
    rows: list[dict] = []

    def record(case, ok, detail):
        rows.append({"case": case, "pass": bool(ok), "detail": str(detail)})

    with exclusive_arena():
        bridge = open_bridge(folder)
        try:
            state = spawn_ready(bridge)
            feet = feet_cell(state)
            table, furnace, far = (
                at(feet, 2, 0, 0),
                at(feet, 0, 0, 2),
                at(feet, 9, 0, 0),
            )
            kit(
                bridge,
                clear=[6, 3, 6],
                inventory=[
                    {"item": "minecraft:oak_log", "count": 4},
                    {"item": "minecraft:raw_iron", "count": 3},
                    {"item": "minecraft:coal", "count": 1},
                    {"item": "minecraft:oak_log", "count": 8},
                ],
                place=[
                    {"state": "minecraft:crafting_table", "offset": [2, 0, 0]},
                    {"state": "minecraft:furnace", "offset": [0, 0, 2]},
                    {"state": "minecraft:crafting_table", "offset": [9, 0, 0]},
                ],
            )

            r = call(bridge, "craft", recipe="minecraft:oak_planks", times=4)
            planks = count(r.get("inventory", []), "minecraft:oak_planks")
            record(
                "craft oak_planks 2x2",
                r["type"] == "craft_done" and planks == 16,
                f"{r.get('crafted')} crafts, planks={planks}",
            )
            r = call(bridge, "craft", recipe="minecraft:stick", times=1)
            sticks = count(r.get("inventory", []), "minecraft:stick")
            record(
                "craft stick 2x2",
                r["type"] == "craft_done" and sticks == 4,
                f"sticks={sticks}",
            )
            r = call(
                bridge, "craft", recipe="minecraft:wooden_pickaxe", times=1, table=table
            )
            record(
                "craft wooden_pickaxe at table",
                r["type"] == "craft_done"
                and count(r.get("inventory", []), "minecraft:wooden_pickaxe") == 1,
                r.get("reason", r.get("stopped")),
            )
            r = call(bridge, "craft", recipe="minecraft:wooden_pickaxe", times=1)
            record(
                "3x3 without table -> no_table",
                r.get("reason") == "no_table",
                r.get("reason"),
            )
            r = call(
                bridge, "craft", recipe="minecraft:wooden_pickaxe", times=1, table=far
            )
            record(
                "3x3 from far away -> out_of_reach",
                r.get("reason") == "out_of_reach",
                r.get("reason"),
            )

            r = call(
                bridge, "furnace", pos=furnace, action="load", input="minecraft:raw_iron",
                count=3, fuel="minecraft:coal", fuel_count=1,
            )  # fmt: skip
            record(
                "furnace load raw_iron + coal",
                r["type"] == "furnace_done"
                and r.get("input_moved") == 3
                and r.get("fuel_moved") == 1,
                r.get("reason", r.get("furnace")),
            )
            bridge.ticks(SMELT_TICKS)
            r = call(bridge, "furnace", pos=furnace, action="collect")
            ingots = count(r.get("inventory", []), "minecraft:iron_ingot")
            record(
                "furnace collect 3 iron_ingot",
                r["type"] == "furnace_done" and r.get("collected") == 3 and ingots == 3,
                f"collected={r.get('collected')} {r.get('collected_item')}, inventory ingots={ingots}",
            )

            r = call(bridge, "equip", item="minecraft:wooden_pickaxe")
            selected = r.get("hotbar_slot")
            record(
                "equip wooden_pickaxe",
                r["type"] == "equip_done",
                f"hotbar_slot={selected}",
            )
        finally:
            try:
                bridge.send("despawn")
            finally:
                bridge.close()
    return finish(folder, rows, "check-crafting")


if __name__ == "__main__":
    sys.exit(main())
