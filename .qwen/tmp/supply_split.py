import importlib.util
import json
import sys
from collections import Counter

sys.path.insert(0, "d:/mcb/src")
sys.path.insert(0, "d:/mcb/scripts")
from mclab.companion_jobs import load_vanilla_pack, materials_of  # noqa: E402
from mclab.gather_plan import Gather, plan  # noqa: E402

spec = importlib.util.spec_from_file_location("rb", "scripts/run-build.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

build = json.loads(open("tests/fixtures/schematics/house-large.build.json").read())
k = load_vanilla_pack()
mats = materials_of(build, k)
give = rb.supply_contents(build, k, "item")
print("give:", {i.split(":")[1]: n for i, n in sorted(give.items()) if i not in mats})

one = [{"kind": "chest", "pos": [0, 0, 0], "contents": dict(give)}]
r1 = plan(mats, {}, k, storage=one)
print("one box, pos:", [(t.block.split(":")[1], t.count, t.item_count) for t in r1.tasks if isinstance(t, Gather)])

npos = [{"kind": "chest", "contents": dict(give)}]
r2 = plan(mats, {}, k, storage=npos)
print("one box, no pos:", [(t.block.split(":")[1], t.count, t.item_count) for t in r2.tasks if isinstance(t, Gather)])

bins = rb.pack_into(give, rb.CAPS)
two = [
    {
        "kind": "chest",
        "pos": [rb.OFFSETS[n][0], 0, rb.OFFSETS[n][2]],
        "contents": {c["item"]: c["count"] for c in b},
    }
    for n, b in enumerate(bins)
]
r3 = plan(mats, {}, k, storage=two)
print("split bins:", [(t.block.split(":")[1], t.count, t.item_count) for t in r3.tasks if isinstance(t, Gather)])
print("bin sizes:", [len(b) for b in bins])
print("total slots:", sum(len(b) for b in bins))
# where does each mined item sit in the give map?
for kind in ("minecraft:birch_log", "minecraft:cobblestone", "minecraft:oak_log"):
    print(kind.split(":")[1], "given:", give.get(kind), "in targets:", mats.get(kind))
