"""Bounded admission lane for immutable observations supplied by the driver."""

import queue
import threading
from dataclasses import dataclass

from mclab.terrain_transport import NATIVE_SAMPLE_LIMIT


@dataclass(frozen=True)
class MapView:
    revision: int
    cells: dict
    status: dict


class ObservationWorker:
    """Own/copy/account/persist observations away from control renewal.

    submit borrows the driver's parsed observation until admission finishes.
    The driver must never mutate observations after submission. Published cell
    records are owned by SpatialMemory and replaced, never edited in place.
    Distinct samples cannot be silently coalesced: they may contain unique cells.
    """

    def __init__(self, memory):
        self.memory = memory
        self.pending = queue.Queue(2)
        self.error = None
        self.closed = False
        self.last_sample = None
        revision, cells = memory.planning_snapshot()
        self.latest = MapView(revision, cells, memory.status())
        self.thread = threading.Thread(
            target=self._run, name="mcb-map-admission", daemon=True
        )
        self.thread.start()

    def submit(self, observation):
        self.poll()
        self.memory.validate_observation(observation)
        coverage = observation.get("awareness", {}).get("coverage", {})
        tick = coverage.get("sample_tick")
        sample = (observation.get("awareness", {}).get("revision"), tick)
        if tick is not None and sample == self.last_sample:
            return
        if len(observation.get("visible_blocks", [])) > NATIVE_SAMPLE_LIMIT:
            self.memory.gap = True
            raise RuntimeError("Observation admission sample exceeds native bounds")
        try:
            self.pending.put_nowait(observation)
        except queue.Full as error:
            self.memory.gap = True
            raise RuntimeError(
                "Observation admission queue full; rescan required"
            ) from error
        self.last_sample = sample

    def poll(self):
        if self.closed:
            raise RuntimeError("Observation worker is closed")
        if self.error is not None:
            raise RuntimeError("Background map admission failed") from self.error
        self.memory._check()
        return self.latest

    def _run(self):
        while True:
            observation = self.pending.get()
            try:
                if observation is None:
                    return
                if self.error is not None:
                    continue
                self.memory.ingest(observation)
                self.memory.recall_surroundings()
                revision, cells = self.memory.planning_snapshot()
                self.latest = MapView(revision, cells, self.memory.status())
            except Exception as error:
                self.error = error
                self.memory.gap = True
            finally:
                self.pending.task_done()

    def close(self):
        if self.closed:
            return
        try:
            self.pending.put(None, timeout=10)
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError("Observation worker did not stop")
            if self.error is not None:
                raise RuntimeError("Background map admission failed") from self.error
        finally:
            self.closed = True
