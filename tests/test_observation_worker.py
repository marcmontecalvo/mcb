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
        assert view.cells.record((2, 0, 0))["observation_session"] == "old-process"
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


def test_automatic_recall_cannot_replace_live_cells(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    old = SpatialMemory(archive, "old")
    old.ingest(observation(*[(x, 0, 0) for x in range(10)]))
    old.close()
    memory = SpatialMemory(archive, "new")
    try:
        obs = observation((9, 0, 0), tick=1, block="minecraft:air")
        obs["body"] = {"position": [0.5, 1, 0.5]}
        memory.ingest(obs)
        memory.recall_surroundings()
        assert len(memory.cells) == 10
        assert memory.cells[(9, 0, 0)]["block"] == "minecraft:air"
        assert memory.cells.record((9, 0, 0))["observation_session"] == "new"
        assert memory.cells.record((8, 0, 0))["observation_session"] == "old"
        assert set(memory.fresh(1)) == {(9, 0, 0)}
    finally:
        memory.close()


def test_automatic_recall_decodes_only_sections_that_can_add_history(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    old = SpatialMemory(archive, "old")
    old.ingest(observation((0, 0, 0)))
    old.close()
    memory = SpatialMemory(archive, "new")
    decoded = []
    recall = archive.recall
    archive.recall = lambda *a, **kw: decoded.append(kw["only"]) or recall(*a, **kw)
    try:
        obs = observation((20, 0, 0), tick=1)
        obs["body"] = {"position": [20.5, 1, 0.5]}
        memory.ingest(obs)
        memory.flush()  # Our own section (1, 0, 0) is now archived too.
        memory.recall_surroundings()
        assert decoded == [frozenset({(0, 0, 0)})]
        assert memory.cells.record((0, 0, 0))["observation_session"] == "old"
    finally:
        memory.close()
    fresh = SpatialMemory(MapArchive(tmp_path / "other.db", identity()), "s")
    fresh.archive.recall = lambda *a, **kw: pytest.fail("nothing to recall")
    try:
        obs = observation((0, 0, 0), tick=1)
        obs["body"] = {"position": [0.5, 1, 0.5]}
        fresh.ingest(obs)
        fresh.flush()
        fresh.recall_surroundings()
    finally:
        fresh.close()
