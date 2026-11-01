# ruff: noqa: E501
"""Wave 3 live check: container op, shulker boxes keeping contents, sneaking placement (B7)."""

import json
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


def main() -> int:
    folder = results_folder()
    rows: list[dict] = []

    def record(case, ok, detail):
        rows.append({"case": case, "pass": bool(ok), "detail": str(detail)})

    def contents_total(r, item):
        return sum(s["count"] for s in r.get("contents", []) if s["item"] == item)

    with exclusive_arena():
        bridge = open_bridge(folder)
        try:
            feet = feet_cell(spawn_ready(bridge))
            chest, supply, table, shulker_pos = (
                at(feet, 2, 0, 0),
                at(feet, -2, 0, 0),
                at(feet, 0, 0, 2),
                at(feet, 2, 0, 2),
            )
            kit(
                bridge,
                clear=[6, 3, 6],
                inventory=[
                    {"item": "minecraft:chest", "count": 1},
                    {"item": "minecraft:oak_log", "count": 64},
                    {"item": "minecraft:diamond_pickaxe", "count": 1},
                    {"item": "minecraft:cooked_beef", "count": 2},
                    {"item": "minecraft:stone", "count": 8},
                ],
                place=[
                    {"state": "minecraft:crafting_table", "offset": [0, 0, 2]},
                    {
                        "state": "minecraft:chest[facing=east]",
                        "offset": [-2, 0, 0],
                        "contents": [{"item": "minecraft:shulker_box", "count": 4}],
                    },
                ],
            )

            # B7: placing against a crafting table while sneaking places and opens no menu
            r = call(
                bridge, "place_block", pos=at(feet, 1, 0, 2), state="minecraft:stone"
            )
            record(
                "B7 place stone on top of crafting table (sneaking, only support is the table)",
                r["type"] == "place_block_done"
                and r.get("placed") == "minecraft:stone",
                r.get("reason", f"face={r.get('face')}"),
            )
            check = call(bridge, "container", pos=table, action="peek")
            record(
                "crafting table is not a container",
                check.get("reason") == "not_a_container",
                check.get("reason"),
            )

            # chest: place from the kit, deposit 64 oak_log, peek, withdraw 32
            r = call(
                bridge, "place_block", pos=chest, state="minecraft:chest[facing=west]"
            )
            record(
                "place chest via place_block",
                r["type"] == "place_block_done",
                r.get("reason", r.get("placed")),
            )
            r = call(
                bridge,
                "container",
                pos=chest,
                action="deposit",
                items=[{"item": "minecraft:oak_log", "count": 64}],
            )
            record(
                "deposit 64 oak_log",
                r["type"] == "container_done"
                and contents_total(r, "minecraft:oak_log") == 64
                and count(r.get("inventory", []), "minecraft:oak_log") == 0,
                r.get("reason", r.get("moved")),
            )
            r = call(bridge, "container", pos=chest, action="peek")
            record(
                "peek shows 64 oak_log",
                r["type"] == "container_done"
                and contents_total(r, "minecraft:oak_log") == 64,
                r.get("reason", r.get("contents")),
            )
            r = call(
                bridge,
                "container",
                pos=chest,
                action="withdraw",
                items=[{"item": "minecraft:oak_log", "count": 32}],
            )
            record(
                "withdraw 32 oak_log",
                r["type"] == "container_done"
                and contents_total(r, "minecraft:oak_log") == 32
                and count(r.get("inventory", []), "minecraft:oak_log") == 32,
                r.get("reason", r.get("moved")),
            )
            r = call(
                bridge,
                "container",
                pos=chest,
                action="withdraw",
                items=[{"item": "minecraft:oak_log", "count": 99}],
            )
            record(
                "withdraw more than held -> missing_item",
                r.get("reason") == "missing_item",
                r.get("reason", r["type"]),
            )
            r = call(
                bridge,
                "container",
                pos=chest,
                action="deposit",
                items=[{"item": "minecraft:diamond", "count": 1}],
            )
            record(
                "deposit absent item -> missing_item",
                r.get("reason") == "missing_item",
                r.get("reason", r["type"]),
            )
            r = call(bridge, "container", pos=at(feet, 5, 0, 5), action="peek")
            record(
                "container on air -> not_a_container",
                r.get("reason") == "not_a_container",
                r.get("reason"),
            )
            r = call(bridge, "container", pos=at(feet, 20, 0, 0), action="peek")
            record(
                "container far away -> not_a_container or out_of_reach",
                r.get("reason") in ("not_a_container", "out_of_reach"),
                r.get("reason"),
            )

            # default deposit keeps tools, food and the first hotbar slots
            r = call(bridge, "container", pos=chest, action="deposit")
            inv = r.get("inventory", [])
            record(
                "default deposit keeps pickaxe and food, moves the rest",
                r["type"] == "container_done"
                and count(inv, "minecraft:diamond_pickaxe") == 1
                and count(inv, "minecraft:cooked_beef") == 2
                and count(inv, "minecraft:oak_log") == 0,
                r.get("reason", r.get("moved")),
            )

            call(
                bridge,
                "container",
                pos=chest,
                action="withdraw",
                items=[{"item": "minecraft:stone", "count": 7}],
            )  # restock

            # shulker boxes: withdraw from the kitted supply chest, place, deposit, break, keep contents
            r = call(bridge, "container", pos=supply, action="peek")
            record(
                "supply chest holds 4 shulker boxes (kit contents)",
                contents_total(r, "minecraft:shulker_box") == 4,
                r.get("contents"),
            )
            r = call(
                bridge,
                "container",
                pos=supply,
                action="withdraw",
                items=[{"item": "minecraft:shulker_box", "count": 1}],
            )
            record(
                "withdraw a shulker box",
                r["type"] == "container_done"
                and count(r.get("inventory", []), "minecraft:shulker_box") == 1,
                r.get("reason", r.get("moved")),
            )
            r = call(
                bridge, "place_block", pos=shulker_pos, state="minecraft:shulker_box"
            )
            record(
                "place the shulker box",
                r["type"] == "place_block_done",
                r.get("reason", r.get("placed")),
            )
            r = call(
                bridge,
                "container",
                pos=shulker_pos,
                action="deposit",
                items=[{"item": "minecraft:stone", "count": 7}],
            )
            record(
                "deposit 7 stone into the shulker box",
                r["type"] == "container_done"
                and contents_total(r, "minecraft:stone") == 7,
                r.get("reason", r.get("contents")),
            )
            r = call(bridge, "break_block", pos=shulker_pos, timeout=30)
            record(
                "break the shulker box",
                r["type"] == "break_block_done",
                r.get("reason", r.get("ticks")),
            )
            bridge.ticks(5)
            items = kit(bridge, nearby=8)["nearby"]
            shulkers = [
                e
                for e in items
                if e["type"] == "minecraft:item"
                and e.get("item") == "minecraft:shulker_box"
            ]
            record("dropped shulker item exists", bool(shulkers), shulkers)
            comp = [e.get("container") for e in shulkers]
            record(
                "dropped shulker keeps its contents (container component)",
                bool(comp) and comp[0] and "minecraft:stone" in json.dumps(comp[0]),
                comp,
            )
        finally:
            try:
                bridge.send("despawn")
            finally:
                bridge.close()
    return finish(folder, rows, "check-containers")


if __name__ == "__main__":
    sys.exit(main())
