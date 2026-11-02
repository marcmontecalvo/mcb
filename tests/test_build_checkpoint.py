from pathlib import Path

from test_companion_jobs import COBBLE, PLANKS, K, small_template, world

from mclab import build_checkpoint as ck
from mclab import build_plan as bp
from mclab.companion_jobs import build_job
from mclab.gather_fake import FakeGatherBody

ORIGIN = (4, 0, 4)


def run_recorded(tmp_path, every=2):
    path = small_template(tmp_path)
    body = FakeGatherBody(K, world())
    import json

    template = bp.Template.from_build(
        json.loads(path.read_text(encoding="utf-8")), ORIGIN, 0
    )
    meta = {"seed": 1, "start": [0, 0, 0], "origin": list(ORIGIN), "rotation": 0}
    rec = ck.Recorder(tmp_path, template, body, meta, every=every)
    report = build_job(path, ORIGIN, 0, body, K, on_progress=rec)
    return path, report


def test_a_run_leaves_numbered_checkpoints_and_a_final_one(tmp_path):
    _, report = run_recorded(tmp_path)

    files = sorted(p.name for p in (tmp_path / "checkpoints").iterdir())

    assert report.stop == "built"
    assert "final.json" in files and len(files) > 2
    final = ck.load(tmp_path / "checkpoints/final.json")
    states = {tuple(c[:3]): c[3] for c in final["cells"]}
    assert states[(4, 0, 4)] == PLANKS and states[(6, 1, 4)] == COBBLE


def test_a_midway_checkpoint_resumes_into_a_finished_build(tmp_path):
    path, _ = run_recorded(tmp_path)
    middle = sorted((tmp_path / "checkpoints").glob("0*.json"))[0]
    data = ck.load(middle)
    placed = [c for c in data["cells"] if c[3] in (PLANKS, COBBLE)]
    assert 0 < len(placed) < 12  # part way: 9 floor planks + 3 cobblestone

    fresh = FakeGatherBody(K, world(), data["inventory"])
    for x, y, z, state in data["cells"]:
        fresh.w[(x, y, z)] = state
    report = build_job(path, ORIGIN, 0, fresh, K, build_only=True)

    assert report.gather is None and report.starter is None  # nothing gathered
    assert report.stop == "built"


def test_restore_sets_inventory_then_cells_in_chunks_skipping_the_body(tmp_path):
    cells = [[x, 0, 0, PLANKS] for x in range(-70, 0)]  # 70 cells
    cells += [[0, 0, 0, PLANKS], [0, 1, 0, PLANKS]]  # the body's own cells
    calls = []

    n = ck.restore(
        {"cells": cells, "inventory": {PLANKS: 5}},
        (0, 0, 0),
        lambda **f: calls.append(f) or {},
    )

    assert n == 70
    assert calls[0] == {"inventory": [{"item": PLANKS, "count": 5}]}
    sizes = [len(c["place"]) for c in calls[1:]]
    assert sizes == [64, 6]
    assert calls[1]["place"][0] == {"state": PLANKS, "offset": [-70, 0, 0]}


def test_unknown_checkpoint_versions_are_refused(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 99}', encoding="utf-8")
    try:
        ck.load(bad)
    except ValueError as error:
        assert "99" in str(error)
    else:
        raise AssertionError("expected ValueError")
