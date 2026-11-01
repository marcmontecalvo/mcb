# ruff: noqa: E501
"""Wave 3 live check: attack_entity, use_entity and eat body ops on the lab server (development only).

Summoned animals are fixtures with NoAI (see docs/BODY_ACTION_OPS.md, kit), so they stay in reach.
"""

import sys

from live_common import (
    call,
    count,
    finish,
    kit,
    open_bridge,
    results_folder,
    spawn_ready,
)  # fmt: skip

from mclab.recorder import exclusive_arena

ANIMALS = [
    ("minecraft:sheep", [2, 0, 0]),
    ("minecraft:sheep", [0, 0, 2]),
    ("minecraft:cow", [-2, 0, 0]),
    ("minecraft:cow", [0, 0, -2]),
    ("minecraft:pig", [2, 0, 2]),
    ("minecraft:villager", [-2, 0, 2]),
]


def main() -> int:
    folder = results_folder()
    rows: list[dict] = []

    def record(case, ok, detail):
        rows.append({"case": case, "pass": bool(ok), "detail": str(detail)})

    with exclusive_arena():
        bridge = open_bridge(folder)
        try:
            spawn_ready(bridge)
            r = kit(
                bridge,
                clear=[6, 3, 6],
                inventory=[
                    {"item": "minecraft:shears", "count": 1},
                    {"item": "minecraft:iron_sword", "count": 1},
                ],
                summon=[{"entity": e, "offset": o} for e, o in ANIMALS],
                nearby=8,
            )
            ids = [e["uuid"] for e in r["summoned_entities"]]
            sheep, cow, pig, villager = ids[0], ids[2], ids[4], ids[5]
            record("kit summons 6 fixtures", len(ids) == 6, r["summoned"])

            def nearby():
                return kit(bridge, nearby=8)["nearby"]

            # A3: damage reported on damageable stacks
            r0 = call(bridge, "equip", item="minecraft:shears")
            shears = next(s for s in r0["inventory"] if s["item"] == "minecraft:shears")
            record(
                "A3 observe: damage + max_damage on shears",
                shears.get("damage") == 0 and shears.get("max_damage") == 238,
                shears,
            )

            r = call(bridge, "use_entity", entity=sheep, item="minecraft:shears")
            record(
                "use_entity shears on sheep -> sheared flips",
                r["type"] == "use_entity_done"
                and r.get("before") == "sheared=false"
                and r.get("after") == "sheared=true",
                r.get(
                    "reason", f"{r.get('before')} -> {r.get('after')} {r.get('result')}"
                ),
            )
            wool = [
                e
                for e in nearby()
                if e["type"] == "minecraft:item" and e.get("item", "").endswith("_wool")
            ]
            record(
                "wool drops on the ground",
                bool(wool) or count(r.get("inventory", []), "minecraft:white_wool") > 0,
                f"{[(w['item'], w['count']) for w in wool]}",
            )
            r = call(bridge, "use_entity", entity=sheep, item="minecraft:shears")
            record(
                "shear a sheared sheep -> no_effect",
                r.get("reason") == "no_effect",
                r.get("reason", r["type"]),
            )
            r = call(
                bridge, "use_entity", entity="00000000-0000-0000-0000-000000000000"
            )
            record(
                "use_entity unknown -> not_found",
                r.get("reason") == "not_found",
                r.get("reason"),
            )

            r = call(bridge, "attack_entity", entity=villager)
            record(
                "attack villager -> not_allowed",
                r.get("reason") == "not_allowed",
                r.get("reason", r["type"]),
            )
            still = any(e["uuid"] == villager for e in nearby())
            record("villager untouched", still, still)

            r = call(bridge, "attack_entity", entity=cow, timeout=15)
            record(
                "attack cow with iron sword -> killed",
                r["type"] == "attack_entity_done"
                and r.get("killed") is True
                and r.get("hits", 0) >= 1,
                r.get(
                    "reason",
                    f"ticks={r.get('ticks')} hits={r.get('hits')} type={r.get('type')}",
                ),
            )
            sword = next(
                (
                    s
                    for s in r.get("inventory", [])
                    if s["item"] == "minecraft:iron_sword"
                ),
                {},
            )
            record(
                "A3: sword damage grew",
                sword.get("damage", 0) >= 1 and sword.get("max_damage") == 250,
                sword,
            )
            bridge.ticks(5)
            drops = [
                (e["item"], e["count"])
                for e in nearby()
                if e["type"] == "minecraft:item"
            ]
            record(
                "cow drops beef (and leather) on the ground",
                any(i == "minecraft:beef" for i, _ in drops),
                drops,
            )
            r = call(bridge, "attack_entity", entity=cow)
            record(
                "attack dead cow -> not_found",
                r.get("reason") == "not_found",
                r.get("reason"),
            )
            r = call(bridge, "attack_entity", entity=pig)
            record(
                "attack pig (second animal kind)",
                r["type"] == "attack_entity_done" and r.get("killed") is True,
                r.get("reason", f"hits={r.get('hits')}"),
            )

            kit(
                bridge,
                inventory=[{"item": "minecraft:cooked_beef", "count": 3}],
                food=6,
            )
            r = call(bridge, "eat", timeout=20)
            record(
                "eat cooked_beef at food 6 -> food rises",
                r["type"] == "eat_done" and r.get("food_after", 0) > 6,
                r.get(
                    "reason",
                    f"{r.get('food_before')} -> {r.get('food_after')} in {r.get('ticks')} ticks",
                ),
            )
            kit(
                bridge,
                inventory=[{"item": "minecraft:cooked_beef", "count": 1}],
                food=20,
            )
            r = call(bridge, "eat")
            record(
                "eat at full food -> not_hungry",
                r.get("reason") == "not_hungry",
                r.get("reason", r["type"]),
            )
            kit(bridge, inventory=[{"item": "minecraft:stick", "count": 1}], food=6)
            r = call(bridge, "eat")
            record(
                "eat with no food -> no_food",
                r.get("reason") == "no_food",
                r.get("reason", r["type"]),
            )
        finally:
            try:
                bridge.send("despawn")
            finally:
                bridge.close()
    return finish(folder, rows, "check-animals")


if __name__ == "__main__":
    sys.exit(main())
