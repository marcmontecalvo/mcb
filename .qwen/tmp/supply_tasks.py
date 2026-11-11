import importlib.util
import json
import sys

sys.path.insert(0, "d:/mcb/src")
sys.path.insert(0, "d:/mcb/scripts")
from mclab.companion_jobs import load_vanilla_pack, materials_of  # noqa: E402
from mclab.gather_check import Replay  # noqa: E402
from mclab.gather_plan import plan  # noqa: E402

spec = importlib.util.spec_from_file_location("rb", "scripts/run-build.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

build = json.loads(open("tests/fixtures/schematics/house-large.build.json").read())
k = load_vanilla_pack()
mats = materials_of(build, k)
give = rb.supply_contents(build, k, "item")
boxes = rb.supply_boxes(give)
r = plan(mats, {}, k, storage=boxes)
for i, t in enumerate(r.tasks):
    print(f"{i:3d} {t!r}"[:200])
print("shortfall-ish: bill keys not in give:", {i.split(':')[1]: n for i, n in mats.items() if i not in give})
