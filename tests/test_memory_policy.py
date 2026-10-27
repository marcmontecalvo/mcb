import threading

import pytest
from test_spatial_memory import identity, observation

from mclab.memory_policy import MemoryPolicy
from mclab.spatial_memory import MapArchive, SpatialMemory, accounted_bytes


@pytest.mark.parametrize("mode", ["current", "session"])
def test_ephemeral_modes_do_not_write_observed_cells(tmp_path, mode):
    archive = MapArchive(tmp_path / "map.db", identity())
    memory = SpatialMemory(archive, "session", policy=MemoryPolicy(memory_mode=mode))
    try:
        memory.ingest(observation((0, 0, 0), tick=1))
        assert memory.planning_snapshot()[1]
        with pytest.raises(ValueError, match="Historical recall"):
            memory.recall((0, 0, 0), 1, 1)
    finally:
        memory.close()
    assert archive.recall((0, 0, 0), 1, 1, limit=10) == []


@pytest.mark.parametrize(
    "policy",
    [MemoryPolicy(memory_mode="current"), MemoryPolicy(use_historical_geometry=False)],
)
def test_strict_planning_cannot_see_old_cells_or_recall_archive(tmp_path, policy):
    archive = MapArchive(tmp_path / "map.db", identity())
    archive.write(observation((9, 0, 0))["visible_blocks"])
    memory = SpatialMemory(archive, "session", policy=policy)
    try:
        memory.ingest(observation((0, 0, 0), tick=1))
        memory.ingest(observation((1, 0, 0), tick=10))
        assert set(memory.planning_snapshot()[1]) == {(1, 0, 0)}
        assert [c["position"] for c in memory.snapshot()[1]] == [[1, 0, 0]]
        with pytest.raises(ValueError, match="Historical recall"):
            memory.recall((9, 0, 0), 1, 1)
    finally:
        memory.close()


def test_hot_byte_eviction_preserves_durable_data(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    memory = SpatialMemory(
        archive, "session", policy=MemoryPolicy(active_map_budget_mb=0.001)
    )
    try:
        memory.ingest(observation(*[(x, 0, 0) for x in range(20)]))
        assert len(memory.cells) < 20
        assert memory.active_bytes <= 1048
        assert memory.status()["active_accounted_bytes"] == memory.active_bytes
    finally:
        memory.close()
    assert len(archive.recall((0, 0, 0), 30, 0, limit=100)) == 20


def test_strict_reach_rejects_still_young_cells_after_displacement(tmp_path):
    memory = SpatialMemory(
        MapArchive(tmp_path / "map.db", identity()),
        "session",
        policy=MemoryPolicy(use_historical_geometry=False),
    )
    try:
        first = observation((0, 0, 0), tick=1)
        first["body"] = {"position": [0.5, 1, 0.5]}
        memory.ingest(first)
        moved = observation((50, 0, 0), tick=2)
        moved["body"] = {"position": [50.5, 1, 0.5]}
        memory.ingest(moved)
        assert set(memory.planning_snapshot()[1]) == {(50, 0, 0)}
    finally:
        memory.close()


def test_cached_accounting_matches_tree_for_different_positions_and_provenance(
    tmp_path,
):
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    try:
        for offset in (0, 2**50):
            cell = {
                "position": [offset, -offset, offset],
                "block": "minecraft:stone",
                "fluid": "minecraft:empty",
                "collision_boxes": [[0, 0, 0, 1, 1, 1]],
                "properties": {"axis": "x"},
                "replaceable": False,
                "observed_tick": 2 ** offset.bit_length(),
                "observed_at_ns": 10**18,
                "observation_session": "session" * 100,
            }
            assert memory._cell_charge(cell) == accounted_bytes(cell)
        cell["extension"] = {"nested": [1, 2, 3]}
        assert memory._cell_charge(cell) == accounted_bytes(cell)
    finally:
        memory.close()


def test_queue_byte_limit_marks_gap_without_silent_drop(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    entered, release = threading.Event(), threading.Event()
    original_write = archive.write

    def blocked_write(cells):
        entered.set()
        assert release.wait(5)
        original_write(cells)

    archive.write = blocked_write
    memory = SpatialMemory(
        archive, "session", policy=MemoryPolicy(persistence_queue_budget_mb=0.003)
    )
    try:
        memory.ingest(observation((0, 0, 0)))
        assert entered.wait(5)
        with pytest.raises(RuntimeError, match="byte budget"):
            memory.ingest(observation(*[(x, 0, 0) for x in range(30)]))
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="gap"):
            memory.close()
    assert memory.pending_bytes == 0
    with pytest.raises(RuntimeError, match="gap"):
        archive.recall((0, 0, 0), 30, 0, limit=100)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"memory_mode": "anything"},
        {"use_historical_geometry": 1},
        {"active_map_budget_mb": float("nan")},
        {"active_map_budget_mb": float("inf")},
        {"persistence_queue_budget_mb": 0},
    ],
)
def test_invalid_memory_controls_are_rejected(kwargs):
    with pytest.raises(ValueError):
        MemoryPolicy(**kwargs)
