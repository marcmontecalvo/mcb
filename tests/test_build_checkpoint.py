import json
from pathlib import Path

from test_build_plan import make_build
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


def test_a_site_lab_stops_at_site_ready_and_a_resume_from_it_builds_the_house(
    tmp_path,
):
    # The stage labs: a run ends at site_ready with a snapshot; another starts from
    # that snapshot, chests included, and builds the house without gathering.
    path = small_template(tmp_path)
    template = bp.Template.from_build(
        json.loads(path.read_text(encoding="utf-8")), ORIGIN, 0
    )
    body = FakeGatherBody(K, world())
    rec = ck.Recorder(tmp_path / "lab", template, body, {"seed": 1}, area=None)
    stages = []

    def milestone(name):
        stages.append(name)
        rec.milestone(name, containers={})

    report = build_job(path, ORIGIN, 0, body, K, until="site", on_milestone=milestone)
    assert report.stop == "until_site" and stages == ["site_ready"]

    site = ck.load(tmp_path / "lab/checkpoints/site_ready.json")
    fresh = FakeGatherBody(K, world(), {PLANKS: 9, COBBLE: 3})
    for x, y, z, state in site["cells"]:
        fresh.w[(x, y, z)] = state
    seen = []
    report = build_job(
        path, ORIGIN, 0, fresh, K, build_only=True, on_milestone=seen.append
    )
    assert report.stop == "built" and report.gather is None
    assert seen == ["site_ready", "structure_done"]  # no details: at the end


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


class _Seen:
    """A body that knows a few cells and holds some blocks."""

    def __init__(self, cells, inventory):
        self.cells, self.inv = cells, inventory

    def world(self, x, y, z):
        return self.cells.get((x, y, z))

    def inventory(self):
        return dict(self.inv)


def test_a_stage_snapshot_keeps_the_site_around_the_house_and_the_chests():
    # A resumed structure lab needs the levelled site (a filled pond outside the
    # footprint) and the supply chests with what is left in them, not only the
    # house's own cells.
    template = bp.Template.from_build(
        make_build({(0, 0, 0): PLANKS}, (1, 1, 1)), (0, 0, 0), 0
    )
    pond = (2, -3, 0)  # outside the footprint, below the ground
    chest = (-1, 0, 1)
    body = _Seen(
        {(0, 0, 0): PLANKS, pond: "minecraft:dirt", chest: "minecraft:chest"},
        {PLANKS: 3},
    )
    area = ck.site_box(template, margin=2, depth=4, above=1)
    snap = ck.snapshot(
        template, body, {"seed": 1}, "", area=area, containers={chest: {COBBLE: 70}}
    )

    cells = {tuple(c[:3]): c[3] for c in snap["cells"]}
    assert cells[pond] == "minecraft:dirt" and cells[(0, 0, 0)] == PLANKS
    assert snap["containers"] == [{"pos": list(chest), "contents": {COBBLE: 70}}]
    assert snap["version"] == ck.VERSION and snap["inventory"] == {PLANKS: 3}


def test_restore_refills_the_chests_in_stacks():
    calls = []
    data = {
        "cells": [[3, 0, 0, "minecraft:chest[facing=east]"], [4, 0, 0, PLANKS]],
        "inventory": {},
        "containers": [{"pos": [3, 0, 0], "contents": {COBBLE: 70}}],
    }

    ck.restore(data, (0, 0, 0), lambda **f: calls.append(f) or {}, stack=lambda i: 64)

    placed = {tuple(p["offset"]): p for c in calls[1:] for p in c["place"]}
    assert placed[(3, 0, 0)]["contents"] == [
        {"item": COBBLE, "count": 64},
        {"item": COBBLE, "count": 6},
    ]
    assert "contents" not in placed[(4, 0, 0)]


def test_a_milestone_is_saved_in_the_run_and_published_to_the_stage_library(
    tmp_path,
):
    template = bp.Template.from_build(
        make_build({(0, 0, 0): PLANKS}, (1, 1, 1)), (0, 0, 0), 0
    )
    body = _Seen({(0, 0, 0): PLANKS}, {})
    library = tmp_path / "stages"
    rec = ck.Recorder(
        tmp_path / "run",
        template,
        body,
        {"seed": 1},
        library=library / "build_house@0,0,0#1",
        source={"run": "run-1", "commit": "abc"},
    )

    rec.milestone("site_ready", containers={}, scaffolds={(2, 1, 0), (1, 1, 0)})

    saved = ck.load(tmp_path / "run/checkpoints/site_ready.json")
    assert saved["scaffolds"] == [[1, 1, 0], [2, 1, 0]]
    assert ck.scaffolds_of(saved) == {(1, 1, 0), (2, 1, 0)}
    assert ck.scaffolds_of({"version": 1}) == set()
    # a stage saved before the list: the scaffolding in its site box is the build's
    older = {"cells": [[0, 1, 0, "minecraft:scaffolding[bottom=false,distance=0]"]]}
    assert ck.scaffolds_of(older | {"cells": older["cells"] + [[1, 1, 1, PLANKS]]}) == {
        (0, 1, 0)
    }
    published = ck.load(library / "build_house@0,0,0#1/site_ready.json")
    assert saved == published
    assert published["stage"] == "site_ready"
    assert published["source"] == {"run": "run-1", "commit": "abc"}


def test_the_stage_library_path_is_a_safe_folder_per_build_key(tmp_path):
    path = ck.stage_path("build:house-large@-24,64,56#31415926", "site_ready", tmp_path)
    assert path == tmp_path / "build_house-large@-24,64,56#31415926/site_ready.json"
    try:
        ck.stage_path("build:x@0,0,0", "../evil", tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("a stage name must be one of the known stages")


def test_a_chest_outside_the_site_box_is_still_kept():
    # The supply chests stand west of the start, outside the site; a resumed lab
    # needs them all the same.
    template = bp.Template.from_build(
        make_build({(0, 0, 0): PLANKS}, (1, 1, 1)), (0, 0, 0), 0
    )
    far = (-9, 0, 0)
    body = _Seen({(0, 0, 0): PLANKS, far: "minecraft:chest[facing=east]"}, {})
    area = ck.site_box(template, margin=1, depth=1, above=0)

    snap = ck.snapshot(template, body, {}, area=area, containers={far: {COBBLE: 3}})

    assert [*far, "minecraft:chest[facing=east]"] in snap["cells"]
    assert ck.chests_of(snap) == [list(far)]


def test_find_stage_names_what_the_library_holds_when_the_stage_is_missing(
    tmp_path,
):
    key = "build:house@0,0,0#1"
    ck.save(ck.stage_path(key, "site_ready", tmp_path), {"version": 2})

    assert ck.find_stage(key, "site_ready", tmp_path).name == "site_ready.json"
    try:
        ck.find_stage(key, "structure_done", tmp_path)
    except FileNotFoundError as error:
        assert "site_ready" in str(error) and "--until structure" in str(error)
    else:
        raise AssertionError("a missing stage must say how to make it")


def test_a_stage_of_another_version_of_the_build_file_is_refused():
    data = {"build_sha256": "a" * 64, "stage": "site_ready"}
    ck.check_build(data, "a" * 64)
    ck.check_build({"stage": "site_ready"}, "b" * 64)  # older snapshots: no hash
    try:
        ck.check_build(data, "b" * 64)
    except ValueError as error:
        assert "site_ready" in str(error)
    else:
        raise AssertionError("a changed build file must not reuse a stage")


def test_version_one_checkpoints_still_load(tmp_path: Path):
    old = tmp_path / "old.json"
    old.write_text('{"version": 1, "cells": [], "inventory": {}}', encoding="utf-8")
    assert ck.load(old)["cells"] == []


def test_unknown_checkpoint_versions_are_refused(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 99}', encoding="utf-8")
    try:
        ck.load(bad)
    except ValueError as error:
        assert "99" in str(error)
    else:
        raise AssertionError("expected ValueError")
