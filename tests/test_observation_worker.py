import threading

import pytest
from test_spatial_memory import identity, observation

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
