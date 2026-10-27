import json
import subprocess
import sys
import threading
from dataclasses import replace

import pytest

from mclab.awareness import Awareness
from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory


def identity():
    return MapIdentity("save-uuid", "reset-1", "minecraft:overworld", Awareness())


def observation(*positions, tick=20, block="minecraft:stone"):
    return {
        "sensor_profile": "local-visibility-v1",
        "tick": tick,
        "visible_blocks": [{"position": list(p), "block": block} for p in positions],
    }


def test_evicted_cells_survive_restart_and_air_replaces_solid(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    first = SpatialMemory(archive, "process-1", active_cell_limit=2)
    try:
        first.ingest(observation((0, 0, 0), (1, 0, 0), (2, 0, 0)))
        assert len(first.cells) == 2
        assert (0, 0, 0) not in first.cells
    finally:
        first.close()
    second = SpatialMemory(MapArchive(archive.path, identity()), "process-2")
    try:
        second.recall((0, 0, 0), 5, 5)
        assert len(second.cells) == 3
        assert second.fresh(20) == {}  # Tick restart cannot freshen historical data.
        second.ingest(observation((0, 0, 0), tick=21, block="minecraft:air"))
        assert second.fresh(21)[(0, 0, 0)]["block"] == "minecraft:air"
        second.flush()
        assert archive.recall((0, 0, 0), 0, 0, limit=1)[0]["block"] == "minecraft:air"
    finally:
        second.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("world", "other-save"),
        ("generation", "reset-2"),
        ("dimension", "minecraft:the_nether"),
        ("awareness", Awareness.resolve("chunk")),
        ("awareness", Awareness.resolve("visible", horizontal_radius_blocks=4)),
    ],
)
def test_partitions_do_not_leak(tmp_path, field, value):
    archive = MapArchive(tmp_path / "map.db", identity())
    archive.write(observation((1, 2, 3))["visible_blocks"])
    restricted = MapArchive(archive.path, replace(identity(), **{field: value}))
    assert restricted.recall((1, 2, 3), 10, 10, limit=100) == []


def test_rejects_mismatched_observation_and_owns_nested_data(tmp_path):
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    try:
        with pytest.raises(ValueError, match="partition"):
            memory.ingest({"sensor_profile": "awareness-v1"})
        obs = observation((0, 0, 0))
        obs["visible_blocks"][0].update(
            collision_boxes=[[0, 0, 0, 1, 1, 1]],
            properties={"axis": "x"},
            extra={"nested": [1]},
        )
        memory.ingest(obs)
        obs["visible_blocks"][0]["position"][0] = 999
        obs["visible_blocks"][0]["collision_boxes"][0][3] = 0.5
        obs["visible_blocks"][0]["properties"]["axis"] = "z"
        obs["visible_blocks"][0]["extra"]["nested"][0] = 2
        assert memory.cells[(0, 0, 0)]["position"] == [0, 0, 0]
        assert memory.cells[(0, 0, 0)]["collision_boxes"][0][3] == 1
        assert memory.cells[(0, 0, 0)]["properties"]["axis"] == "x"
        assert memory.cells[(0, 0, 0)]["extra"]["nested"] == [1]
        _, snapshot = memory.snapshot()
        snapshot[0]["position"][0] = 100
        assert memory.cells[(0, 0, 0)]["position"] == [0, 0, 0]
    finally:
        memory.close()


def test_overflow_is_explicit_and_persists_untrusted_coverage(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    entered, release = threading.Event(), threading.Event()
    write = archive.write

    def blocked_write(cells):
        entered.set()
        assert release.wait(5)
        write(cells)

    archive.write = blocked_write
    memory = SpatialMemory(archive, "session", queue_limit=1)
    try:
        memory.ingest(observation((0, 0, 0)))
        assert entered.wait(5)
        memory.ingest(observation((1, 0, 0)))
        with pytest.raises(RuntimeError, match="queue full"):
            memory.ingest(observation((2, 0, 0)))
        with pytest.raises(RuntimeError, match="gap"):
            memory.snapshot()
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="gap"):
            memory.close()
    with pytest.raises(RuntimeError, match="gap"):
        MapArchive(archive.path, identity()).recall((0, 0, 0), 5, 5, limit=100)


def test_missing_cells_remain_unknown_and_recall_is_bounded(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    archive.write(observation(*[(x, 0, 0) for x in range(100)])["visible_blocks"])
    rows = archive.recall((0, 0, 0), 100, 0, limit=3)
    assert [r["position"] for r in rows] == [[0, 0, 0], [1, 0, 0], [2, 0, 0]]
    assert archive.recall((0, 1, 0), 100, 0, limit=100) == []


def test_writer_failure_is_reported_and_archived_as_gap(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())

    def failed_write(_):
        raise OSError("disk unavailable")

    archive.write = failed_write
    memory = SpatialMemory(archive, "session")
    memory.ingest(observation((1, 2, 3)))
    with pytest.raises(RuntimeError, match="persistence failed"):
        memory.close()
    assert memory.closed
    with pytest.raises(RuntimeError, match="gap"):
        MapArchive(archive.path, identity()).recall((0, 0, 0), 10, 10, limit=10)


def test_duplicate_native_samples_are_not_requeued(tmp_path):
    selected = Awareness.resolve("visible")
    archive = MapArchive(tmp_path / "map.db", replace(identity(), awareness=selected))
    memory = SpatialMemory(archive, "session")
    try:
        obs = observation((0, 0, 0))
        obs.update(
            sensor_profile=selected.sensor_profile,
            awareness={
                "config": selected.payload(),
                "revision": "r1",
                "coverage": {"sample_tick": 20},
            },
        )
        memory.ingest(obs)
        revision = memory.revision
        obs["tick"] += 1
        memory.ingest(obs)
        assert memory.revision == revision
    finally:
        memory.close()


def test_archive_recall_from_independent_python_process(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    memory = SpatialMemory(archive, "writer-process")
    memory.ingest(observation((7, 8, 9)))
    memory.close()
    code = """
import json, sys
from mclab.awareness import Awareness
from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory
identity = MapIdentity('save-uuid', 'reset-1', 'minecraft:overworld', Awareness())
memory = SpatialMemory(MapArchive(sys.argv[1], identity), 'reader-process')
try:
    memory.recall((7, 8, 9), 0, 0)
    print(json.dumps({'cells': memory.snapshot()[1], 'fresh': len(memory.fresh(20))}))
finally:
    memory.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(archive.path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    recalled = json.loads(result.stdout)
    assert recalled["cells"][0]["position"] == [7, 8, 9]
    assert recalled["fresh"] == 0


def test_planning_view_keeps_its_revision_when_live_cells_change(tmp_path):
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    try:
        memory.ingest(observation((0, 0, 0), tick=1))
        revision, view = memory.planning_snapshot()
        memory.ingest(observation((0, 0, 0), tick=2, block="minecraft:air"))
        assert view[(0, 0, 0)]["block"] == "minecraft:stone"
        assert memory.cells[(0, 0, 0)]["block"] == "minecraft:air"
        assert memory.revision > revision
    finally:
        memory.close()
