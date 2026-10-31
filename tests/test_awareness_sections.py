import time
from copy import deepcopy

import pytest

from mclab.awareness import Awareness
from mclab.memory_policy import MemoryPolicy
from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory
from mclab.terrain_transport import expand_terrain

AIR = {
    "block": "minecraft:air",
    "fluid": "minecraft:empty",
    "replaceable": True,
    "properties": {},
    "collision_boxes": [],
}
STONE = {
    "block": "minecraft:stone",
    "fluid": "minecraft:empty",
    "replaceable": False,
    "properties": {},
    "collision_boxes": [[0, 0, 0, 1, 1, 1]],
}


def batch(sections=(), dense=(), uniform=(), tick=7):
    return {
        "run_id": "r",
        "episode_id": "e",
        "body_id": "b",
        "tick": tick + 3,
        "sensor_profile": "awareness-v1",
        "body": {"position": [0.5, 1.0, 0.5]},
        "awareness": {
            "config": Awareness.resolve("sections").payload(),
            "revision": "e",
            "coverage": {"sample_tick": tick},
        },
        "terrain_sections": {
            "schema_version": 1,
            "encoding": "sparse_section_palette",
            "unknown_is_air": False,
            "sample_tick": tick,
            "known_cells": sum(len(s["cells"]) for s in sections),
            "bulk_cells": (len(dense) + len(uniform)) * 4096,
            "palette": [AIR, STONE],
            "sections": list(sections),
            "dense_sections": list(dense),
            "uniform_sections": list(uniform),
            "encoding_ms": 0.1,
        },
    }


def floor_section(key):
    # y offset 0 of the section is stone, the rest air (offset = x*256 + y*16 + z).
    states = [1 if (i >> 4) & 15 == 0 else 0 for i in range(4096)]
    return {"section": list(key), "states": states, "tick": 7}


def test_sections_preset_is_validated():
    assert Awareness.resolve("sections").horizontal_radius_blocks == 64
    with pytest.raises(ValueError):
        Awareness(preset="chunk", extra_scan_cells_per_update=32768)
    with pytest.raises(ValueError):
        Awareness(preset="sections", extra_scan_cells_per_update=65537)


def test_expand_accepts_bulk_without_per_cell_expansion():
    obs = batch(
        dense=[floor_section((0, 0, 0))],
        uniform=[{"section": [0, 1, 0], "state": 0, "tick": 7}],
    )
    result = expand_terrain(obs)
    assert result["visible_blocks"] == []
    assert [b[0] for b in result.bulk] == [(0, 0, 0), (0, 1, 0)]
    assert result.bulk[1][1] == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["dense_sections"][0].update(states=[0] * 4095),
        lambda b: b["dense_sections"][0].update(states=[2] * 4096),
        lambda b: b["dense_sections"][0].update(tick=99),
        lambda b: b.update(bulk_cells=1),
        lambda b: b["uniform_sections"][0].update(state=5),
        lambda b: b["uniform_sections"].append(deepcopy(b["uniform_sections"][0]))
        or b.update(bulk_cells=3 * 4096),
        lambda b: b.pop("uniform_sections"),
    ],
)
def test_expand_rejects_bad_bulk(mutate):
    obs = batch(
        dense=[floor_section((0, 0, 0))],
        uniform=[{"section": [0, 1, 0], "state": 0, "tick": 7}],
    )
    mutate(obs["terrain_sections"])
    with pytest.raises(ValueError):
        expand_terrain(obs)


def test_legacy_sparse_batches_still_expand():
    obs = batch(sections=[{"section": [0, 0, 0], "cells": [[0, 0, 3]]}])
    obs["terrain_sections"]["palette"] = [STONE]
    for key in ("bulk_cells", "dense_sections", "uniform_sections"):
        del obs["terrain_sections"][key]
    assert len(expand_terrain(obs)["visible_blocks"]) == 1


def memory(tmp_path, mode=None):
    identity = MapIdentity(
        "w", "g", "minecraft:overworld", Awareness.resolve("sections")
    )
    policy = MemoryPolicy() if mode is None else MemoryPolicy(memory_mode=mode)
    return SpatialMemory(MapArchive(tmp_path / "map.db", identity), "s", policy=policy)


def test_bulk_ingest_fills_known_air_and_leaves_unloaded_unknown(tmp_path):
    mem = memory(tmp_path)
    try:
        mem.ingest(
            expand_terrain(
                batch(
                    dense=[floor_section((0, 0, 0))],
                    uniform=[{"section": [0, 1, 0], "state": 0, "tick": 7}],
                )
            )
        )
        grid = mem.grid.view()
        assert grid.get((3, 0, 3))["block"] == "minecraft:stone"
        assert grid.get((3, 1, 3))["block"] == "minecraft:air"
        assert grid.get((3, 17, 3))["block"] == "minecraft:air"
        assert grid.get((3, 40, 3)) is None  # never sent: unknown, not air
        assert grid.get((100, 0, 3)) is None
    finally:
        mem.close()


def test_large_bulk_ingestion_time(tmp_path, capsys):
    """~200k cells (49 dense sections); timing is logged, not asserted."""
    mem = memory(tmp_path)
    try:
        dense = [floor_section((x, 0, z)) for x in range(-3, 4) for z in range(-3, 4)]
        t0 = time.perf_counter()
        observation = expand_terrain(batch(dense=dense))
        t1 = time.perf_counter()
        mem.ingest(observation)
        t2 = time.perf_counter()
        with capsys.disabled():
            print(
                f"\nbulk 200,704 cells: expand {1000 * (t1 - t0):.1f} ms, "
                f"ingest {1000 * (t2 - t1):.1f} ms"
            )
        assert mem.grid.view().get((-48, 0, 47))["block"] == "minecraft:stone"
    finally:
        mem.close()


def test_bulk_sections_are_persisted_and_recalled(tmp_path):
    """Whole bulk sections reach the archive and can be recalled."""
    mem = memory(tmp_path)
    try:
        mem.ingest(expand_terrain(batch(dense=[floor_section((0, 0, 0))])))
        mem.flush()  # raises if the writer failed
    finally:
        mem.close()
    rows = MapArchive(tmp_path / "map.db", mem.archive.identity).recall(
        (8, 8, 8), 16, 16, limit=8192
    )
    blocks = {tuple(r["position"]): r["block"] for r in rows}
    assert blocks[(3, 0, 3)] == "minecraft:stone"
    assert blocks[(3, 1, 3)] == "minecraft:air"
