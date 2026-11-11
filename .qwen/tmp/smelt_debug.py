import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "d:/mcb/src")
ROOT = Path("d:/mcb")
from mclab import gather_plan as gp  # noqa: E402
from mclab.gather_plan import _Model, plan  # noqa: E402
from mclab.knowledge import load_pack  # noqa: E402

tmp = ROOT / ".qwen/tmp/minicoal"
if tmp.exists():
    shutil.rmtree(tmp)
shutil.copytree(ROOT / "tests/fixtures/knowledge/mini", tmp)
rec = json.loads((tmp / "recipes.json").read_text())
rec["recipes"] += [
    {
        "id": "minecraft:coal",
        "type": "minecraft:crafting_shapeless",
        "station": "inventory",
        "ingredients": [{"options": ["minecraft:coal_block"], "count": 1}],
        "result": {"item": "minecraft:coal", "count": 9},
    },
    {
        "id": "minecraft:coal_from_smelting_coal_ore",
        "type": "minecraft:smelting",
        "station": "furnace",
        "cook_ticks": 200,
        "ingredients": [{"options": ["minecraft:coal_ore"], "count": 1}],
        "result": {"item": "minecraft:coal", "count": 1},
    },
]
(tmp / "recipes.json").write_text(json.dumps(rec))
items = json.loads((tmp / "items.json").read_text())
items["items"]["minecraft:coal_block"] = {"stack": 64, "fuel_ticks": None}
(tmp / "items.json").write_text(json.dumps(items))

k = load_pack(tmp)
box = [
    {
        "kind": "chest",
        "pos": [1, 0, 1],
        "contents": {
            "minecraft:oak_log": 24,
            "minecraft:cobblestone": 32,
            "minecraft:coal_ore": 6,
        },
    }
]
from mclab.gather_plan import normalize_inventory  # noqa: E402
from mclab.gather_tasks import container_contents  # noqa: E402

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
    {"minecraft:oak_log": 0.9, "minecraft:sand": 0.9},
)
print("FUEL_ITEMS:", gp.FUEL_ITEMS)
print("fuel_choices:", [i for i, _ in m.fuel_choices()])
print("best coal (held):", m.best.get("minecraft:coal"))
print("best coal (held-agnostic):", m.best_n.get("minecraft:coal"))

# what does the children graph say?
best = dict(m.best)
kids = gp._children(m, "minecraft:coal", best)
print("children of coal:", kids)

roots = ["minecraft:torch"]
order, state, cycle = [], {}, []
for r in [ "minecraft:torch" ]:
    pass
# replicate _consumers_first to see the cycle
bb = dict(m.best)
for _ in range(len(bb) + 1):
    order, state, cycle = [], {}, []
    gp._visit(m, "minecraft:torch", bb, state, order, cycle)
    if not cycle:
        break
    alt = m.best_n.get(cycle[0])
    print("cycle at", cycle[0], "alt ->", alt)
    if alt is None or alt == bb.get(cycle[0]):
        break
    bb[cycle[0]] = alt
print("coal after cycle-break:", bb.get("minecraft:coal"))

r = plan({"minecraft:torch": 16}, {}, k, storage=box)
print("tasks:", [(type(t).__name__, getattr(t, "recipe_id", getattr(t, "block", getattr(t, "item", "")))) for t in r.tasks])
print("stored:", r.stored)
