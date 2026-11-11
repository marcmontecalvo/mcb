import sys

sys.path.insert(0, "d:/mcb/src")
from mclab import gather_plan as gp  # noqa: E402
from mclab.companion_jobs import load_vanilla_pack  # noqa: E402
from mclab.gather_plan import _Model, normalize_inventory, plan  # noqa: E402
from mclab.gather_tasks import container_contents  # noqa: E402

k = load_vanilla_pack()
COAL = {
    "block": {"minecraft:coal_block": 4},
    "item": {"minecraft:coal": 16},
    "ore": {"minecraft:coal_ore": 16},
}


def trace(target, form):
    box = [
        {
            "kind": "chest",
            "pos": [1, 0, 1],
            "contents": {
                "minecraft:oak_log": 24,
                "minecraft:cobblestone": 32,
                "minecraft:sand": 16,
                "minecraft:iron_nugget": 208,
                **COAL[form],
            },
        }
    ]
    counts, wear = normalize_inventory({})
    reachable = dict(counts)
    for i, n in container_contents(box).items():
        reachable[i] = reachable.get(i, 0) + n
    m = _Model(
        k,
        reachable,
        {},
        {},
        ("inventory", "crafting_table", "furnace"),
        (),
        gp.DEFAULT_PREFER,
    )
    print(f"--- {target} with {form}: cost coal={m.cost.get('minecraft:coal'):.2f} "
          f"charcoal={m.cost.get('minecraft:charcoal'):.2f} "
          f"coal_block={m.cost.get('minecraft:coal_block'):.2f}")
    print("    best coal:", m.best["minecraft:coal"][0],
          getattr(m.best["minecraft:coal"][1], "id", "gat"))
    print("    best coal_n:", m.best_n["minecraft:coal"][0],
          getattr(m.best_n["minecraft:coal"][1], "id", "gat"))
    print("    children coal:", gp._children(m, "minecraft:coal", m.best))
    best = dict(m.best)
    for n in range(8):
        order, state, cycle = [], {}, []
        gp._visit(m, target, best, state, order, cycle)
        if not cycle:
            print(
                f"    round {n}: acyclic; coal ->",
                best["minecraft:coal"][0],
                getattr(best["minecraft:coal"][1], "id", "gat"),
            )
            break
        alt = m.best_n.get(cycle[0])
        print(f"    round {n}: cycle re-enters", cycle[0], "->", alt[0] if alt else None)
        if alt is None or alt == best.get(cycle[0]):
            print("    gives up")
            break
        best[cycle[0]] = alt
    r = plan({target: 4}, {}, k, storage=box)
    print("    tasks:", [(type(t).__name__, getattr(t, "recipe_id", getattr(t, "block", getattr(t, "item", getattr(t, "kind", ""))))) for t in r.tasks])
    print("    stored:", {i.split(":")[1]: n for i, n in r.stored.items() if "coal" in i})


for form in ("block", "item", "ore"):
    for target in ("minecraft:torch", "minecraft:lantern", "minecraft:campfire"):
        trace(target, form)
