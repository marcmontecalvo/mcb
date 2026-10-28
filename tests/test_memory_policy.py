import threading

import pytest
from test_spatial_memory import identity, observation

from mclab.memory_policy import MemoryPolicy
from mclab.spatial_memory import MapArchive, SpatialMemory


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


def test_hot_byte_eviction_drops_far_sections_only_and_preserves_durable_data(
    tmp_path,
):
    archive = MapArchive(tmp_path / "map.db", identity())
    memory = SpatialMemory(
        archive, "session", policy=MemoryPolicy(active_map_budget_mb=0.001)
    )
    try:
        far = observation(*[(200 + x, 0, 0) for x in range(20)], tick=1)
        far["body"] = {"position": [200.5, 1, 0.5]}
        memory.ingest(far)
        near = observation(*[(x, 0, 0) for x in range(-8, 9)], tick=2)
        near["body"] = {"position": [0.5, 1, 0.5]}
        memory.ingest(near)
        # Over budget, but sections inside the awareness volume are never evicted.
        assert set(memory.cells) == {(x, 0, 0) for x in range(-8, 9)}
        assert memory.status()["evicted_sections"] == 2
        assert memory.status()["active_accounted_bytes"] == memory.active_bytes
    finally:
        memory.close()
    assert len(archive.recall((100, 0, 0), 120, 0, limit=100)) == 37


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


def test_queue_byte_limit_marks_gap_without_silent_drop(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    entered, release = threading.Event(), threading.Event()
    original_write = archive.write_deltas

    def blocked_write(cells):
        entered.set()
        assert release.wait(5)
        original_write(cells)

    archive.write_deltas = blocked_write
    memory = SpatialMemory(
        archive, "session", policy=MemoryPolicy(persistence_queue_budget_mb=0.04)
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
