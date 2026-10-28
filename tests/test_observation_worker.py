import threading

import pytest
from test_spatial_memory import identity, observation

from mclab.memory_policy import MemoryPolicy
from mclab.observation_worker import ObservationWorker
from mclab.spatial_memory import MapArchive, SpatialMemory


def test_distinct_sample_overflow_fails_closed_without_dropping_geometry(tmp_path):
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    entered, release = threading.Event(), threading.Event()
    original_ingest = memory.ingest

    def gated_ingest(obs, **kwargs):
        entered.set()
        assert release.wait(3)
        original_ingest(obs, **kwargs)

    memory.ingest = gated_ingest
    worker = ObservationWorker(memory)
    try:
        worker.submit(observation((0, 0, 0), tick=1))
        assert entered.wait(3)
        worker.submit(observation((1, 0, 0), tick=2))
        worker.submit(observation((2, 0, 0), tick=3))
        with pytest.raises(RuntimeError, match="admission queue full"):
            worker.submit(observation((3, 0, 0), tick=4))
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="admission failed"):
            worker.close()
        with pytest.raises(RuntimeError, match="gap"):
            memory.close()
    assert not worker.thread.is_alive()
    with pytest.raises(RuntimeError, match="gap"):
        memory.archive.recall((0, 0, 0), 10, 10, limit=10)


def test_wrong_awareness_is_rejected_before_background_admission(tmp_path):
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    worker = ObservationWorker(memory)
    try:
        with pytest.raises(ValueError, match="partition"):
            worker.submit({"sensor_profile": "wrong"})
        assert not worker.latest.cells
    finally:
        worker.close()
        memory.close()


def test_late_persistence_error_closes_admission_even_without_a_new_sample(tmp_path):
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "session")
    worker = ObservationWorker(memory)
    try:
        # The durable writer can fail after an admitted view has been published.
        memory.error = OSError("late write failure")
        memory.gap = True
        with pytest.raises(RuntimeError, match="persistence failed"):
            worker.poll()
        with pytest.raises(RuntimeError, match="persistence failed"):
            worker.submit(observation((0, 0, 0)))
    finally:
        worker.close()
        with pytest.raises(RuntimeError, match="persistence failed"):
            memory.close()


def test_background_recall_after_restart_preserves_fresh_changes(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    old = SpatialMemory(archive, "old-process")
    old.ingest(observation((1, 0, 0), (2, 0, 0), tick=100))
    old.close()
    memory = SpatialMemory(archive, "new-process")
    worker = ObservationWorker(memory)
    try:
        obs = observation((1, 0, 0), tick=1, block="minecraft:air")
        obs["body"] = {"position": [0.5, 1, 0.5]}
        worker.submit(obs)
        worker.pending.join()
        view = worker.poll()
        assert view.cells[(1, 0, 0)]["block"] == "minecraft:air"
        assert view.cells[(2, 0, 0)]["observation_session"] == "old-process"
        assert (2, 0, 0) not in memory.fresh(100)
        assert view.status["automatic_recall_count"] == 1
        obs = observation(tick=2)
        obs["body"] = {"position": [1.5, 1, 0.5]}
        worker.submit(obs)
        worker.pending.join()
        assert worker.poll().status["automatic_recall_count"] == 1
    finally:
        worker.close()
        memory.close()


@pytest.mark.parametrize(
    "mode,historical", [("current", True), ("session", True), ("persistent", False)]
)
def test_automatic_recall_respects_memory_policy(tmp_path, mode, historical):
    archive = MapArchive(tmp_path / "map.db", identity())
    memory = SpatialMemory(
        archive,
        "session",
        policy=MemoryPolicy(memory_mode=mode, use_historical_geometry=historical),
    )
    archive.recall = lambda *a, **kw: pytest.fail("Forbidden historical read")
    worker = ObservationWorker(memory)
    try:
        obs = observation((0, 0, 0))
        obs["body"] = {"position": [0.5, 1, 0.5]}
        worker.submit(obs)
        worker.pending.join()
        assert worker.poll().status["automatic_recall_count"] == 0
    finally:
        worker.close()
        memory.close()


def test_automatic_recall_cannot_evict_live_cells_or_exceed_budget(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    old = SpatialMemory(archive, "old")
    old.ingest(observation(*[(x, 0, 0) for x in range(10)]))
    old.close()
    memory = SpatialMemory(archive, "new", active_cell_limit=2)
    try:
        obs = observation((9, 0, 0), tick=1, block="minecraft:air")
        obs["body"] = {"position": [0.5, 1, 0.5]}
        memory.ingest(obs)
        memory.recall_surroundings()
        assert len(memory.cells) == 2
        assert memory.cells[(9, 0, 0)]["block"] == "minecraft:air"
        assert memory.active_bytes <= memory.byte_limit
    finally:
        memory.close()
