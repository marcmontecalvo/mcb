"""Development spatial archive. Disk work stays outside the motor decision loop.

Namespaces are exact access partitions, never just world seeds. A caller must
provide a new generation whenever a save is reset/restored. Historical cells can
guide search but are not fresh motor evidence across observation sessions.
"""

import json
import math
import queue
import sys
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict, dataclass

from mclab.awareness import Awareness
from mclab.memory_policy import MemoryPolicy
from mclab.spatial_archive import MapArchive as MapArchive

GEOMETRY_FIELDS = frozenset(
    {"block", "fluid", "replaceable", "properties", "collision_boxes"}
)
NATIVE_FIELDS = GEOMETRY_FIELDS | {
    "position",
    "observed_tick",
    "observed_at_ns",
    "observation_session",
}
NATIVE_KEY_BYTES = sum(sys.getsizeof(k) for k in NATIVE_FIELDS)


def accounted_bytes(value):
    """Conservative object-tree charge; shared facts across cells are charged twice.

    Includes per-entry index overhead, not process RSS, SQLite pages or Python's
    allocator arenas. Bounds retained cell data, not total process memory.
    """
    pending, total = [value], 256
    while pending:
        item = pending.pop()
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return total


def owned_cell(cell):
    """Own admitted JSON facts without encoding/decoding the whole sensor batch."""
    result = dict(cell)
    result["position"] = list(cell["position"])
    if "collision_boxes" in cell:
        result["collision_boxes"] = [list(box) for box in cell["collision_boxes"]]
    if "properties" in cell:
        properties = cell["properties"]
        result["properties"] = (
            dict(properties)
            if all(
                isinstance(v, (str, int, float, bool, type(None)))
                for v in properties.values()
            )
            else deepcopy(properties)
        )
    for key, value in cell.items():
        if key not in {"position", "collision_boxes", "properties"} and isinstance(
            value, (dict, list, tuple)
        ):
            result[key] = deepcopy(value)
    return result


@dataclass(frozen=True)
class MapIdentity:
    world: str
    generation: str
    dimension: str
    awareness: Awareness

    def __post_init__(self):
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.world, self.generation, self.dimension)
        ) or not isinstance(self.awareness, Awareness):
            raise ValueError("Map requires explicit world, generation and dimension")

    @property
    def key(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


class SpatialMemory:
    """Bounded hot cells and asynchronous durable updates with fail-closed gaps."""

    def __init__(
        self, archive, session, *, active_cell_limit=20000, queue_limit=16, policy=None
    ):
        if not session or active_cell_limit < 1 or queue_limit < 1:
            raise ValueError("Invalid memory session/budget")
        self.archive, self.session = archive, session
        self.policy = MemoryPolicy() if policy is None else policy
        if not isinstance(self.policy, MemoryPolicy):
            raise ValueError("Invalid memory policy")
        self.limit = active_cell_limit
        self.byte_limit = int(self.policy.active_map_budget_mb * 1024 * 1024)
        self.queue_byte_limit = int(
            self.policy.persistence_queue_budget_mb * 1024 * 1024
        )
        self.active_bytes = self.pending_bytes = 0
        self.sizes = {}
        self.charge_cache = OrderedDict()
        self.queue_lock = threading.Lock()
        self.latest_tick = None
        self.latest_center = None
        self.cells = OrderedDict()
        self.revision = 0
        self.gap = False
        self.error = None
        self.closed = False
        self.pending = queue.Queue(queue_limit)
        self.last_sample = None
        self.worker = threading.Thread(target=self._write, daemon=True)
        self.worker.start()

    def _write(self):
        while True:
            batch = self.pending.get()
            batches = 1
            charged = 0
            try:
                if batch is None:
                    return
                batch, charged = batch
                latest = {tuple(c["position"]): c for c in batch}
                # Coalesce queued observations of the same cell. A single WAL
                # transaction need not repeatedly rewrite intermediate samples.
                for _ in range(self.pending.maxsize):
                    try:
                        extra = self.pending.get_nowait()
                    except queue.Empty:
                        break
                    batches += 1
                    if extra is None:
                        raise RuntimeError("Unexpected writer stop before flush")
                    extra, extra_charge = extra
                    charged += extra_charge
                    latest.update((tuple(c["position"]), c) for c in extra)
                self.archive.write(list(latest.values()))
            except Exception as error:
                self.error = error
                self.gap = True
            finally:
                with self.queue_lock:
                    self.pending_bytes -= charged
                for _ in range(batches):
                    self.pending.task_done()

    def _check(self):
        if self.closed:
            raise RuntimeError("Spatial memory is closed")
        if self.error is not None:
            raise RuntimeError("Spatial persistence failed") from self.error
        if self.gap:
            raise RuntimeError("Map observation gap; start a new generation and rescan")

    def validate_observation(self, observation):
        """Cheap admission check shared by synchronous and background callers."""
        awareness = self.archive.identity.awareness
        if observation.get("sensor_profile") != awareness.sensor_profile or (
            awareness.preset != "legacy"
            and observation.get("awareness", {}).get("config") != awareness.payload()
        ):
            raise ValueError("Observation does not match map access partition")

    def ingest(self, observation):
        """No disk IO. Exact profile/config validation prevents access widening."""
        self._check()
        self.validate_observation(observation)
        state = observation.get("awareness", {})
        self.latest_tick = observation["tick"]
        body_position = observation.get("body", {}).get("position")
        self.latest_center = (
            tuple(math.floor(v) for v in body_position) if body_position else None
        )
        sample_tick = state.get("coverage", {}).get("sample_tick")
        sample = (state.get("revision"), sample_tick)
        if sample_tick is not None and sample == self.last_sample:
            return
        batch = []
        sizes = []
        observed_at = time.time_ns()
        for cell in observation.get("visible_blocks", []):
            p = cell.get("position")
            if (
                not isinstance(p, list)
                or len(p) != 3
                or any(type(v) is not int for v in p)
            ):
                raise ValueError("Invalid observed cell position")
            batch.append(
                {
                    **owned_cell(cell),
                    "observed_tick": cell.get("observed_tick", observation["tick"]),
                    "observation_session": self.session,
                    "observed_at_ns": observed_at,
                }
            )
            sizes.append(self._cell_charge(batch[-1]))
        if self.policy.memory_mode == "persistent":
            charge = sum(sizes) + sys.getsizeof(batch)
            with self.queue_lock:
                if self.pending_bytes + charge > self.queue_byte_limit:
                    self.gap = True
                    raise RuntimeError(
                        "Spatial persistence byte budget; rescan required"
                    )
                try:
                    self.pending.put_nowait((batch, charge))
                except queue.Full as error:
                    self.gap = True
                    raise RuntimeError(
                        "Spatial persistence queue full; rescan required"
                    ) from error
                self.pending_bytes += charge
        for cell, size in zip(batch, sizes, strict=True):
            self._remember(cell, size)
        if self.policy.memory_mode == "current":
            for p, cell in list(self.cells.items()):
                if not self._is_current(cell):
                    self.cells.pop(p)
                    self.active_bytes -= self.sizes.pop(p)
        self.last_sample = sample
        self.revision += 1

    def _remember(self, cell, size=None):
        p = tuple(cell["position"])
        size = self._cell_charge(cell) if size is None else size
        self.active_bytes += size - self.sizes.get(p, 0)
        self.sizes[p] = size
        self.cells[p] = cell
        self.cells.move_to_end(p)
        while len(self.cells) > self.limit or self.active_bytes > self.byte_limit:
            evicted, _ = self.cells.popitem(last=False)
            self.active_bytes -= self.sizes.pop(evicted)

    def _cell_charge(self, cell):
        """Cache repeated native facts; charge every dynamic scalar separately."""
        if cell.keys() != NATIVE_FIELDS or (
            type(cell["observed_tick"]) is not int
            or type(cell["observed_at_ns"]) is not int
            or type(cell["observation_session"]) is not str
        ):
            return accounted_bytes(cell)
        try:
            key = (
                cell["block"],
                cell["fluid"],
                (type(cell["replaceable"]), cell["replaceable"]),
                tuple(sorted((k, type(v), v) for k, v in cell["properties"].items())),
                tuple(
                    tuple((type(v), v) for v in box) for box in cell["collision_boxes"]
                ),
            )
            cached = self.charge_cache.get(key)
        except TypeError:
            return accounted_bytes(cell)
        if cached is None:
            cached = sum(accounted_bytes(cell[k]) - 256 for k in GEOMETRY_FIELDS)
            # Bound descriptors even for unusual native states.
            if cached <= 8192:
                self.charge_cache[key] = cached
                if len(self.charge_cache) > 64:
                    self.charge_cache.popitem(last=False)
        p = cell["position"]
        return (
            256
            + NATIVE_KEY_BYTES
            + sys.getsizeof(cell)
            + cached
            + sys.getsizeof(p)
            + sys.getsizeof(p[0])
            + sys.getsizeof(p[1])
            + sys.getsizeof(p[2])
            + sys.getsizeof(cell["observed_tick"])
            + sys.getsizeof(cell["observed_at_ns"])
            + sys.getsizeof(cell["observation_session"])
        )

    def _is_current(self, cell):
        if self.latest_center is not None:
            reach = self.archive.identity.awareness
            dx, dy, dz = (
                abs(a - b)
                for a, b in zip(cell["position"], self.latest_center, strict=True)
            )
            if (
                max(dx, dz) > reach.horizontal_radius_blocks
                or reach.vertical_radius_blocks != -1
                and dy > reach.vertical_radius_blocks
            ):
                return False
        return (
            self.latest_tick is not None
            and cell["observation_session"] == self.session
            and 0 <= self.latest_tick - cell["observed_tick"] <= 5
        )

    def _planning_cells(self):
        if (
            self.policy.memory_mode == "current"
            or not self.policy.use_historical_geometry
        ):
            return {p: c for p, c in self.cells.items() if self._is_current(c)}
        return dict(self.cells)

    def status(self):
        with self.queue_lock:
            pending_bytes = self.pending_bytes
        return {
            "policy": self.policy.payload(),
            "active_cells": len(self.cells),
            "active_accounted_bytes": self.active_bytes,
            "active_byte_limit": self.byte_limit,
            "persistence_pending_bytes": pending_bytes,
            "persistence_byte_limit": self.queue_byte_limit,
            "persistence_queue": self.pending.qsize(),
        }

    def recall(self, center, radius, height):
        if (
            self.policy.memory_mode != "persistent"
            or not self.policy.use_historical_geometry
        ):
            raise ValueError("Historical recall is disabled by memory policy")
        self.flush()
        for cell in self.archive.recall(center, radius, height, limit=self.limit):
            p = tuple(cell["position"])
            # Recalled history must not replace a newer live observation.
            if p not in self.cells:
                self._remember(cell)
        self.revision += 1

    def snapshot(self):
        self._check()
        return self.revision, json.loads(
            json.dumps(list(self._planning_cells().values()))
        )

    def planning_snapshot(self):
        """Internal versioned view: ingestion replaces owned cells, never edits them.

        Shallow-copy the bounded index rather than JSON-roundtripping every nested
        collision box during motor renewal. Consumers must treat values as immutable.
        """
        self._check()
        return self.revision, self._planning_cells()

    def fresh(self, tick, max_age=5):
        self._check()
        return {
            p: cell
            for p, cell in self.cells.items()
            if cell["observation_session"] == self.session
            and 0 <= tick - cell["observed_tick"] <= max_age
        }

    def flush(self):
        deadline = time.monotonic() + 10
        while self.pending.unfinished_tasks:
            if time.monotonic() >= deadline:
                self.gap = True
                raise RuntimeError("Spatial persistence flush deadline exceeded")
            time.sleep(0.005)
        if self.gap:
            self.archive.mark_gap()
        self._check()

    def close(self):
        if self.closed:
            return
        try:
            self.flush()
        finally:
            self.pending.put(None, timeout=1)
            self.worker.join(timeout=10)
            self.closed = True
            if self.worker.is_alive():
                raise RuntimeError("Spatial writer did not stop; archive unverified")
