import json
from pathlib import Path

from test_build_plan import make_build

from mclab.companion_jobs import build_job
from mclab.gather_fake import FakeGatherBody
from mclab.gather_runner import GatherState
from mclab.knowledge import load_pack

ROOT = Path(__file__).parent.parent
K = load_pack(
    ROOT / "tests/fixtures/knowledge/mini",
    transforms=ROOT / "knowledge/transforms/vanilla-26.3.json",
    foods=ROOT / "knowledge/sources/foods-26.3.json",
)
M = "minecraft:"
PLANKS, COBBLE = M + "oak_planks", M + "cobblestone"


def small_template(tmp_path):
    cells = {(x, 0, z): PLANKS for x in range(3) for z in range(3)}
    cells.update({(x, 1, 0): COBBLE for x in range(3)})
    path = tmp_path / "small.build.json"
    path.write_text(json.dumps(make_build(cells, (3, 2, 3))), encoding="utf-8")
    return path


def world():
    w = {(x, -1, z): M + "dirt" for x in range(-6, 40) for z in range(-6, 40)}
    w |= {(12, 0, i * 2): M + "oak_log" for i in range(10)}
    w |= {(i * 2, 0, 14): M + "stone" for i in range(10)}
    return w


def test_empty_inventory_to_a_finished_small_template(tmp_path):
    body = FakeGatherBody(K, world())

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K)

    assert report.stop == "built", report
    assert report.needed == {PLANKS: 9, COBBLE: 3}
    assert report.shortfall == report.needed
    assert report.gather.stop == "done"
    assert report.build.surveyed > 0 and report.build.progress() == 1.0
    assert body.w[(4, 0, 4)] == PLANKS and body.w[(6, 1, 4)] == COBBLE


def test_a_midway_shortfall_refills_through_on_need_materials(tmp_path):
    # The fake loses planks while building (a creeper, say): the hook re-gathers.
    body = FakeGatherBody(K, world(), {PLANKS: 9, COBBLE: 3})
    real_place = body.place_block
    lost = []

    def leaky(pos, state, item):
        result = real_place(pos, state, item)
        if item == PLANKS and len(lost) == 0 and body.inv.get(PLANKS, 0) == 4:
            lost.append(body.inv.pop(PLANKS))
        return result

    body.place_block = leaky

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K)

    assert lost and report.refills, report
    assert report.stop == "built"


def test_remembered_containers_are_withdrawn_before_gathering(tmp_path):
    w = world()
    w[(2, 0, 2)] = M + "chest"
    body = FakeGatherBody(K, w, {})
    body.boxes[(2, 0, 2)] = {PLANKS: 9, COBBLE: 3}
    state = GatherState(containers={(2, 0, 2): {PLANKS: 9, COBBLE: 3}})

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K, state=state)

    assert report.shortfall == {} and report.gather is None
    assert report.stop == "built"
