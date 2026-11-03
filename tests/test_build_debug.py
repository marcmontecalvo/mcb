import json
from types import SimpleNamespace

import pytest

from mclab.build_debug import place_preview, preview_cells, write_walkthrough


def test_preview_translates_air_and_batches_without_inventory_changes():
    template = SimpleNamespace(
        cells={(i, 64, 0): "minecraft:stone" for i in range(130)}
    )
    template.cells[(0, 65, 0)] = "minecraft:air"
    cells = preview_cells(template, (0, 0, 5))
    assert cells[(0, 65, 5)] == "minecraft:air"
    calls = []
    assert place_preview(cells, (0, 64, 0), lambda **f: calls.append(f)) == 131
    assert [len(c["place"]) for c in calls] == [64, 64, 3]
    assert all(set(c) == {"place"} for c in calls)


def test_preview_rejects_overlap_before_any_mutation():
    template = SimpleNamespace(cells={(0, 64, 0): "minecraft:stone"})
    with pytest.raises(ValueError, match="overlaps"):
        preview_cells(template, (0, 0, 0))
    with pytest.raises(ValueError, match="body"):
        place_preview(template.cells, (0, 64, 0), lambda **_: pytest.fail("mutated"))


def test_walkthrough_retains_each_attempt_and_diagnosis(tmp_path):
    template = SimpleNamespace(cells={(1, 64, 2): "minecraft:oak_stairs[facing=east]"})
    detail = {"eye": [0.5, 65.62, 0.5], "candidates": [{"visible": False}]}
    failures = [((1, 64, 2), "state_unreachable", (-2, 64, 3), detail)] * 2
    write_walkthrough(tmp_path, template, failures)
    rows = json.loads((tmp_path / "walkthrough.json").read_text())
    assert len(rows) == 2 and rows[0]["diagnosis"] == detail
    assert rows[0]["teleport"] == "/tp @s -1.5 64 3.5"
    assert rows[0]["state"] == template.cells[(1, 64, 2)]
