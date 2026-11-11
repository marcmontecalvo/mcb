import importlib.util
import json
import math
import sys
from collections import Counter

sys.path.insert(0, "d:/mcb/src")
sys.path.insert(0, "d:/mcb/scripts")
from mclab.companion_jobs import load_vanilla_pack, materials_of  # noqa: E402
from mclab.gather_plan import Gather, Hunt, Shear, plan  # noqa: E402
from mclab.gather_tasks import item_stack  # noqa: E402

spec = importlib.util.spec_from_file_location("rb", "scripts/run-build.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

build = json.loads(open("tests/fixtures/schematics/house-large.build.json").read())
k = load_vanilla_pack()
mats = materials_of(build, k)


def slots(entries):
    """Slots the server's fill() spends: it splits every entry at the item's limit."""
    return sum(
        math.ceil(c["count"] / item_stack(k.items, c["item"])) for c in entries
    )


for form in ("item", "block", "ore"):
    give = rb.supply_contents(build, k, form)
    bins = rb.supply_bins(give, k)
    layout = [b + (rb.BIN_TOOLS if n == 0 else []) for n, b in enumerate(bins)]
    boxes = rb.supply_boxes(give, k)
    bad = [
        (c["item"], c["count"], item_stack(k.items, c["item"]))
        for b in layout
        for c in b
        if c["count"] > item_stack(k.items, c["item"])
    ]
    over = [(n, slots(b), 27) for n, b in enumerate(layout) if slots(b) > 27]
    totals = Counter()
    for b in layout:
        for c in b:
            totals[c["item"]] += c["count"]
    lost = {i: n - totals.get(i, 0) for i, n in give.items() if n - totals.get(i, 0)}
    r = plan(mats, {}, k, storage=boxes)
    wanted = Counter()
    for t in r.tasks:
        if isinstance(t, Gather | Hunt | Shear):
            wanted[(t.item or t.block).split(":")[1]] += t.item_count or t.count
    print(
        f"== {form}: {len(give)} kinds in {len(bins)} bins,"
        f" slots {[(slots(b), 27) for b in layout]}"
    )
    print("   chunk>stack:", bad or "none", "| over 27 slots:", over or "none")
    print("   lost in packing:", lost or "none")
    print("   missing:", [(m.item, m.reason) for m in r.missing])
    print("   takes from the world:", dict(wanted) or "NOTHING")
    print("   tasks:", len(r.tasks), "cost:", round(r.estimated_cost))
    print("   beyond the bill:", {
        i.split(":")[1]: n for i, n in sorted(give.items()) if i not in mats
    })
