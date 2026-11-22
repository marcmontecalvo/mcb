"""A view of a live build: the template's layers drawn from the newest checkpoint."""

import json
import os
import struct
import zlib

from test_build_plan import make_build

from mclab import build_plan as bp
from mclab import build_view as bv

PLANKS = "minecraft:oak_planks"
COBBLE = "minecraft:cobblestone"
AIR = "minecraft:air"
SCAFF = "minecraft:scaffolding[bottom=false,distance=0,waterlogged=false]"


def _template():
    cells = {(x, 0, z): COBBLE for x in range(3) for z in range(2)}
    cells |= {(x, 1, 0): PLANKS for x in range(3)}
    return bp.Template.from_build(make_build(cells, (3, 2, 2)), (10, 64, 20))


def _decode(png: bytes):
    """Width, height and rows of RGB pixels of an 8-bit RGB PNG (filter 0 rows)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(png):
        n = struct.unpack(">I", png[pos : pos + 4])[0]
        kind, data = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + n]
        if kind == b"IHDR":
            size = struct.unpack(">II", data[:8])
        elif kind == b"IDAT":
            idat += data
        pos += 12 + n
    w, h = size
    raw = zlib.decompress(idat)
    rows = []
    for r in range(h):
        line = raw[r * (3 * w + 1) : (r + 1) * (3 * w + 1)]
        assert line[0] == 0
        rows.append([tuple(line[1 + 3 * i : 4 + 3 * i]) for i in range(w)])
    return w, h, rows


def test_each_template_cell_is_correct_missing_wrong_or_a_temporary_in_its_way():
    t = _template()
    world = {(x, 64, z): COBBLE for x in range(10, 13) for z in range(20, 22)}
    world[(10, 65, 20)] = PLANKS
    world[(11, 65, 20)] = COBBLE  # wrong block
    world[(11, 65, 21)] = SCAFF  # in a cell the template keeps empty
    kinds = bv.classify(t, world)
    assert kinds[(10, 64, 20)] == "correct" and kinds[(10, 65, 20)] == "correct"
    assert kinds[(11, 65, 20)] == "wrong"
    assert kinds[(12, 65, 20)] == "missing"
    assert kinds[(11, 65, 21)] == "temporary"
    assert (10, 65, 21) not in kinds  # empty and meant to be


def test_the_view_is_a_png_with_a_panel_per_layer_and_the_body_marked():
    t = _template()
    world = {(x, 64, z): COBBLE for x in range(10, 13) for z in range(20, 22)}
    png = bv.render(t, world, body=(12, 65, 21), done=6, total=9)
    w, h, rows = _decode(png)
    assert w > 0 and h > 0
    colours = {px for row in rows for px in row}
    assert bv.COLOURS["correct"] in colours and bv.COLOURS["missing"] in colours
    assert bv.COLOURS["body"] in colours
    assert bv.COLOURS["progress"] in colours  # the bar along the top


def _run_folder(tmp_path, done, stage="placing", position=None):
    run = tmp_path / "run"
    (run / "checkpoints").mkdir(parents=True, exist_ok=True)
    build = tmp_path / "mini.build.json"
    cells = {(x, 0, z): COBBLE for x in range(3) for z in range(2)}
    build.write_text(json.dumps(make_build(cells, (3, 1, 2))), encoding="utf-8")
    placed = [[10 + i % 3, 64, 20 + i // 3, COBBLE] for i in range(done)]
    for n in (5, 10):  # the newest by number is read
        (run / "checkpoints" / f"{n:04d}.json").write_text(
            json.dumps(
                {
                    "build": str(build),
                    "origin": [10, 64, 20],
                    "rotation": 0,
                    "cells": placed if n == 10 else [],
                }
            ),
            encoding="utf-8",
        )
    (run / "progress.json").write_text(
        json.dumps({"stage": stage, "done": done, "total": 6, "updated_at": 1.0}),
        encoding="utf-8",
    )
    return run


def test_a_snapshot_reads_the_progress_and_the_newest_checkpoint(tmp_path):
    run = _run_folder(tmp_path, done=4)
    snap = bv.snapshot(run, position=lambda run: (11.5, 65.0, 20.5))
    assert snap.stage == "placing" and snap.done == 4 and snap.total == 6
    assert snap.checkpoint.name == "0010.json"
    assert snap.counts == {"correct": 4, "missing": 2, "wrong": 0, "temporary": 0}
    assert snap.body == (11.5, 65.0, 20.5)


def test_the_newest_checkpoint_is_the_last_written_named_or_numbered(tmp_path):
    """Run 20261122T013145553434Z: the closing look read checkpoint 0NNN with 53
    scaffolds still up; final.json, written after cleanup, had none."""
    run = _run_folder(tmp_path, done=4)
    final = run / "checkpoints" / "final.json"
    numbered = json.loads((run / "checkpoints" / "0010.json").read_text())
    final.write_text(json.dumps({**numbered, "cells": numbered["cells"][:1]}))
    stamp = (run / "checkpoints" / "0010.json").stat().st_mtime
    os.utime(final, (stamp + 5, stamp + 5))
    assert bv.latest_checkpoint(run) == final
    assert bv.snapshot(run, position=lambda r: None).counts["correct"] == 1


def test_a_bot_that_placed_nothing_and_barely_moved_is_sitting(tmp_path):
    a = bv.snapshot(_run_folder(tmp_path, 2), position=lambda r: (0.0, 64.0, 0.0))
    b = bv.snapshot(_run_folder(tmp_path, 2), position=lambda r: (1.0, 64.0, 0.0))
    c = bv.snapshot(_run_folder(tmp_path, 3), position=lambda r: (1.0, 64.0, 0.0))
    d = bv.snapshot(_run_folder(tmp_path, 2), position=lambda r: (9.0, 64.0, 0.0))
    assert bv.verdict(a, b) == "sitting"
    assert bv.verdict(b, c) == "progressing"  # a block more
    assert bv.verdict(a, d) == "moving"  # nothing placed, but it walked
    assert bv.verdict(None, a) == "progressing"


def test_watching_writes_a_numbered_view_and_one_line_per_look(tmp_path, capsys):
    run = _run_folder(tmp_path, done=6, stage="done")
    out = tmp_path / "views"
    assert bv.main([str(run), "--out", str(out), "--every", "0"]) == 0
    assert sorted(p.name for p in out.glob("*.png")) == ["view-000.png"]
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert "done=6/6" in line and "stage=done" in line
