import sys

sys.path.insert(0, "d:/mcb/src")
from mclab.companion_jobs import load_vanilla_pack  # noqa: E402
from mclab.gather_plan import Gather, Smelt, Withdraw, plan  # noqa: E402

k = load_vanilla_pack()
for form, coal in (
    ("item", {"minecraft:coal": 16}),
    ("block", {"minecraft:coal_block": 4}),
    ("ore", {"minecraft:coal_ore": 16}),
    ("none", {}),
):
    box = [
        {
            "kind": "chest",
            "pos": [1, 0, 1],
            "contents": {
                "minecraft:oak_log": 24,
                "minecraft:cobblestone": 32,
                "minecraft:sand": 16,
                "minecraft:iron_nugget": 208,
                **coal,
            },
        }
    ]
    for target in (
        {"minecraft:torch": 16},
        {"minecraft:lantern": 4},
        {"minecraft:campfire": 1},
    ):
        r = plan(target, {}, k, storage=box)
        mined = [t.block.split(":")[1] for t in r.tasks if isinstance(t, Gather)]
        coal_taken = [
            sorted(i.split(":")[1] for i, _ in t.items)
            for t in r.tasks
            if isinstance(t, Withdraw) and any("coal" in i for i, _ in t.items)
        ]
        smelted = [t.ingredient.split(":")[1] for t in r.tasks if isinstance(t, Smelt)]
        print(
            f"{form:5s} {list(target)[0].split(':')[1]:9s}",
            "missing:",
            [(m.item.split(':')[1], m.reason) for m in r.missing],
            "mined:",
            mined,
            "withdrew:",
            coal_taken,
            "smelt:",
            smelted,
            "stored_left:",
            {i.split(":")[1]: n for i, n in r.stored.items() if "coal" in i},
        )
