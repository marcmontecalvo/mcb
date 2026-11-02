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
    # stone tools first; the house's logs are then cut with the stone axe
    assert "Craft:minecraft:stone_axe" in report.starter.done
    assert "Craft:minecraft:wooden_axe" not in report.starter.done
    assert report.gather.done[:2] == ["Equip:", "Gather:minecraft:oak_log"]
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


def test_a_supply_chest_is_read_first_shulkers_taken_scaffolding_kept_until_building(
    tmp_path,
):
    scaffolding, shulker = M + "scaffolding", M + "shulker_box"
    w = world()
    w[(2, 0, 2)] = M + "chest"
    body = FakeGatherBody(K, w)
    body.boxes[(2, 0, 2)] = {scaffolding: 64, shulker: 2}
    state = GatherState()
    seen = {}
    real_place = body.place_block

    def watch(pos, st, item):
        if item in (PLANKS, COBBLE):  # a house block, not a table or a chest
            seen.setdefault("scaffolding_at_first_place", body.inv.get(scaffolding, 0))
        return real_place(pos, st, item)

    body.place_block = watch

    report = build_job(
        small_template(tmp_path), (4, 0, 4), 0, body, K, state=state, chests=[(2, 0, 2)]
    )

    assert (2, 0, 2) in state.containers  # the chest is remembered
    assert body.boxes[(2, 0, 2)][shulker] == 0  # shulkers were taken at the start
    assert body.boxes[(2, 0, 2)][scaffolding] == 0
    assert seen["scaffolding_at_first_place"] == 64  # scaffolding taken before building
    assert report.stop == "built"


def test_a_precise_stance_needs_the_body_out_of_the_target_cell():
    from mclab.companion_body import CompanionBody

    body = CompanionBody.__new__(CompanionBody)
    body._obs = {"body": {"position": [4.6, 0.0, 4.5]}}  # box x 4.3..4.9: clear of x=5
    assert not body._overlaps((5, 0, 4))
    body._obs = {"body": {"position": [4.75, 0.0, 4.5]}}  # box x 4.45..5.05
    assert body._overlaps((5, 0, 4))
    body._obs = {"body": {"position": [4.5, 0.0, 4.5]}}
    assert not body._overlaps((5, 0, 4))


def test_a_digging_body_is_kept_out_of_the_template(tmp_path):
    from mclab.companion_body import DIG_TERRAIN

    body = FakeGatherBody(K, world())
    body.terrain_policy = DIG_TERRAIN
    ticks = iter(range(0, 1000, 100))

    report = build_job(
        small_template(tmp_path), (4, 0, 4), 0, body, K, clock=lambda: next(ticks)
    )

    assert report.stop == "built"
    protected = set(body.terrain_policy.protected_positions)
    assert len(protected) == 3 * 2 * 3  # every cell, air included
    assert (4, 0, 4) in protected and (6, 1, 6) in protected  # (6,1,6) is air
    assert report.ticks == {"gather": 100, "build": 100}


def test_a_stalled_gather_ends_the_job_before_building(tmp_path, monkeypatch):
    from mclab import gather_runner

    monkeypatch.setattr(gather_runner, "STALL_S", -1.0)  # every check is a stall
    body = FakeGatherBody(K, world())

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K)

    assert report.stop == "gather_failed" and report.build is None
    assert report.starter.stop == "stalled"
