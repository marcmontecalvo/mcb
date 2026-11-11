import importlib.util
import json
import sys
from collections import Counter

sys.path.insert(0, "d:/mcb/src")
sys.path.insert(0, "d:/mcb/scripts")
from mclab.companion_jobs import load_vanilla_pack, materials_of  # noqa: E402
from mclab.gather_check import check_order  # noqa: E402
from mclab.gather_plan import Gather, Hunt, Shear, plan  # noqa: E402

spec = importlib.util.spec_from_file_location("rb", "scripts/run-build.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

for build_path in (
    "tests/fixtures/schematics/house-large.build.json",
    "tests/fixtures/schematics/small-house.build.json",
):
    build = json.loads(open(build_path).read())
    k = load_vanilla_pack()
    mats = materials_of(build, k)
    for form in ("item", "block", "ore"):
        try:
            give = rb.supply_contents(build, k, form)
        except Exception as e:  # noqa: BLE001
            print(build_path.split("/")[-1], form, "SUPPLY FAILED:", e)
            continue
        boxes = rb.supply_boxes(give)
        r = plan(mats, {}, k, storage=boxes)
        taken = Counter(
            (t.item or t.block).split(":")[1]
            for t in r.tasks
            if isinstance(t, Gather | Hunt | Shear)
        )
        problems = check_order(r.tasks, {}, k, None, boxes)
        print(
            build_path.split("/")[-1],
            form,
            "| tasks",
            len(r.tasks),
            "| from world",
            dict(taken) or "NOTHING",
            "| order problems",
            problems[:3] or "none",
            "| missing",
            [m.item for m in r.missing],
        )
