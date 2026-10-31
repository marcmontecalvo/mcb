"""Development spatial archive. Disk work stays outside the motor decision loop.

Namespaces are exact access partitions, never just world seeds. A caller must
provide a new generation whenever a save is reset/restored. Historical cells can
guide search but are not fresh motor evidence across observation sessions.

The hot map is a compact section grid (`pathing_grid`). Nothing inside the
awareness volume is evicted; when over the byte budget, whole sections outside
it age out, farthest first. Their facts remain in the archive.
"""

import json
import math
import threading
import time
from array import array
from collections import deque
from dataclasses import asdict, dataclass

from mclab.awareness import Awareness
from mclab.memory_policy import MemoryPolicy
from mclab.pathing_grid import (
    PROVENANCE_FIELDS,
    SECTION_CELLS,
    PathingGrid,
    Section,
    offset_of,
)
from mclab.spatial_archive import MapArchive as MapArchive

# Pending section: state ids (2 bytes), provenance ids (4) and a dirty flag (1).
PENDING_SECTION_BYTES = SECTION_CELLS * 7 + 256
# Live samples kept for fresh/current views; must exceed any freshness age used.
RECENT_TICKS = 64
RECALL_LIMIT = 65536


class PendingSection:
    """Latest unsaved (state, provenance) per cell of one section."""

    __slots__ = ("states", "provenance", "dirty")

    def __init__(self):
        self.states = array("H", [0]) * SECTION_CELLS
        self.provenance = array("I", [0]) * SECTION_CELLS
        self.dirty = bytearray(SECTION_CELLS)


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
    """Compact hot grid and asynchronous durable section deltas; gaps fail closed.

    `queue_limit` bounds the number of distinct sections waiting for the writer.
    Repeated observations of a pending section coalesce (latest cell wins), so the
    backlog is bounded by section count, not by observation count.
    """

    def __init__(self, archive, session, *, queue_limit=16384, policy=None):
        if not session or queue_limit < 1:
            raise ValueError("Invalid memory session/budget")
        self.archive, self.session = archive, session
        self.policy = MemoryPolicy() if policy is None else policy
        if not isinstance(self.policy, MemoryPolicy):
            raise ValueError("Invalid memory policy")
        self.queue_limit = queue_limit
        self.byte_limit = int(self.policy.active_map_budget_mb * 1024 * 1024)
        self.queue_byte_limit = int(
            self.policy.persistence_queue_budget_mb * 1024 * 1024
        )
        self.grid = PathingGrid()
        self.pending_bytes = 0
        self.pending_sections = {}
        self.writing = False
        self.stopping = False
        self.queue_lock = threading.Condition()
        self.latest_tick = None
        self.latest_center = None
        self.recent = deque()  # (sample tick, positions) for expiry
        self.recent_ticks = {}  # position -> latest live sample tick
        self.revision = 0
        self.gap = False
        self.error = None
        self.closed = False
        self.last_sample = None
        self.recall_region = None
        self.recall_count = 0
        self.recall_ms = 0.0
        self.evicted_sections = 0
        # Sections whose archive may hold cells the grid lacks: history from
        # earlier processes, and sections this memory evicted. Our own other
        # writes are always still held, so recalling them is wasted work.
        self.history_sections = (
            archive.section_keys()
            if self.policy.memory_mode == "persistent"
            and self.policy.use_historical_geometry
            else set()
        )
        self.worker = threading.Thread(target=self._write, daemon=True)
        self.worker.start()

    @property
    def cells(self):
        """Live known-cell mapping (current mode: only current cells)."""
        if self.policy.memory_mode == "current":
            return self._current_cells()
        return self.grid.view()

    @property
    def active_bytes(self):
        return self.grid.accounted_bytes

    def _write(self):
        while True:
            with self.queue_lock:
                while not self.pending_sections and not self.stopping:
                    self.queue_lock.wait()
                if not self.pending_sections:
                    return
                batch, self.pending_sections = self.pending_sections, {}
                charged = len(batch) * PENDING_SECTION_BYTES
                self.writing = True
            try:
                self.archive.write_deltas(self._deltas(batch))
            except Exception as error:
                self.error = error
                self.gap = True
            finally:
                batch = None  # Do not hold written arrays while idle.
                with self.queue_lock:
                    self.pending_bytes -= charged
                    self.writing = False
                    self.queue_lock.notify_all()

    def _deltas(self, batch):
        """Yield one section's rows at a time; never materialize the whole batch."""
        states, provenances, shared = self.grid.states, self.grid.provenances, {}
        for key, section in batch.items():
            rows = []
            for offset in range(SECTION_CELLS):
                if not section.dirty[offset]:
                    continue
                pid = section.provenance[offset]
                provenance = shared.get(pid)
                if provenance is None:
                    provenance = shared[pid] = {
                        name: value
                        for name, value in zip(
                            PROVENANCE_FIELDS, provenances[pid], strict=True
                        )
                        if value is not None
                    }
                rows.append((offset, states[section.states[offset]], provenance))
            yield key, rows

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
        grid = self.grid
        observed_at = time.time_ns()
        rows = []
        # Validate and intern everything before changing any shared state.
        for cell in observation.get("visible_blocks", []):
            p = cell.get("position")
            if (
                not isinstance(p, list)
                or len(p) != 3
                or any(type(v) is not int for v in p)
            ):
                raise ValueError("Invalid observed cell position")
            rows.append(
                (
                    tuple(p),
                    grid.state_id(cell),
                    grid.provenance_id(
                        cell.get("observed_tick", observation["tick"]),
                        self.session,
                        observed_at,
                    ),
                )
            )
        bulk = getattr(observation, "bulk", ())
        if bulk:
            sids = [grid.state_id(state) for state in observation.palette]
            built = self._build_bulk(bulk, sids, observed_at)
            if self.policy.memory_mode == "persistent":
                self._queue_bulk(built)
            for key, section in built:
                grid.sections[key] = section
        if self.policy.memory_mode == "persistent":
            self._queue(rows)
        for p, sid, pid in rows:
            grid.put(p, sid, pid)
        sample_tick = observation["tick"]
        positions = [p for p, _, _ in rows]
        self.recent.append((sample_tick, positions))
        self.recent_ticks.update(dict.fromkeys(positions, sample_tick))
        while self.recent and self.recent[0][0] < self.latest_tick - RECENT_TICKS:
            old_tick, old_positions = self.recent.popleft()
            for p in old_positions:
                if self.recent_ticks.get(p) == old_tick:
                    del self.recent_ticks[p]
        self._evict()
        self.last_sample = sample
        self.revision += 1

    def _build_bulk(self, bulk, sids, observed_at):
        """Whole-section slice assignments (no per-cell Python work).

        Fresh Sections are returned, so frozen snapshots keep the old ones.
        """
        grid, built = self.grid, []
        for key, value, tick in bulk:
            section = Section(grid.generation)
            if isinstance(value, int):
                section.states = array("H", [sids[value]]) * SECTION_CELLS
            else:
                section.states = array("H", [sids[i] for i in value])
            pid = grid.provenance_id(tick, self.session, observed_at)
            section.provenance = array("I", [pid]) * SECTION_CELLS
            section.known = SECTION_CELLS
            built.append((key, section))
        return built

    def _queue_bulk(self, built):
        with self.queue_lock:
            pending = self.pending_sections
            new = [key for key, _ in built if key not in pending]
            if len(pending) + len(new) > self.queue_limit:
                self.gap = True
                raise RuntimeError("Spatial persistence queue full; rescan required")
            if self.pending_bytes + len(new) * PENDING_SECTION_BYTES > (
                self.queue_byte_limit
            ):
                self.gap = True
                raise RuntimeError("Spatial persistence byte budget; rescan required")
            for key, section in built:
                entry = pending.get(key)
                if entry is None:
                    entry = pending[key] = PendingSection()
                    self.pending_bytes += PENDING_SECTION_BYTES
                entry.states = array("H", section.states)
                entry.provenance = array("I", section.provenance)
                entry.dirty = bytearray(b"") * SECTION_CELLS
            self.queue_lock.notify_all()

    def _queue(self, rows):
        with self.queue_lock:
            pending = self.pending_sections
            new = {
                key
                for key in {(p[0] >> 4, p[1] >> 4, p[2] >> 4) for p, _, _ in rows}
                if key not in pending
            }
            if len(pending) + len(new) > self.queue_limit:
                self.gap = True
                raise RuntimeError("Spatial persistence queue full; rescan required")
            charge = len(new) * PENDING_SECTION_BYTES
            if self.pending_bytes + charge > self.queue_byte_limit:
                self.gap = True
                raise RuntimeError("Spatial persistence byte budget; rescan required")
            for key in new:
                pending[key] = PendingSection()
            for p, sid, pid in rows:
                section = pending[(p[0] >> 4, p[1] >> 4, p[2] >> 4)]
                offset = offset_of(*p)
                section.states[offset] = sid
                section.provenance[offset] = pid
                section.dirty[offset] = 1
            self.pending_bytes += charge
            self.queue_lock.notify_all()

    def _inside_view(self, key):
        """Whether any cell of section `key` lies in the current awareness volume."""
        if self.latest_center is None:
            return False
        reach = self.archive.identity.awareness
        horizontal = reach.horizontal_radius_blocks
        for axis, radius in ((0, horizontal), (2, horizontal), (1, None)):
            if axis == 1:
                if reach.vertical_radius_blocks == -1:
                    continue
                radius = reach.vertical_radius_blocks
            low = key[axis] * 16
            center = self.latest_center[axis]
            if low + 15 < center - radius or low > center + radius:
                return False
        return True

    def _section_distance(self, key):
        return sum(
            (k * 16 + 8 - c) ** 2 for k, c in zip(key, self.latest_center, strict=True)
        )

    def _evict(self):
        # Without a body position the awareness volume is unknown: keep everything.
        if self.latest_center is None or self.grid.accounted_bytes <= self.byte_limit:
            return
        candidates = sorted(
            (key for key in self.grid.sections if not self._inside_view(key)),
            key=self._section_distance,
            reverse=True,
        )
        for key in candidates:
            if self.grid.accounted_bytes <= self.byte_limit:
                break
            del self.grid.sections[key]
            self.history_sections.add(key)
            self.evicted_sections += 1

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
            and cell.get("observation_session") == self.session
            and 0 <= self.latest_tick - cell["observed_tick"] <= 5
        )

    def _recent_cells(self, keep, oldest_tick):
        """Latest records of cells from live samples at or after `oldest_tick`.

        A cell's observed tick never exceeds its sample tick, so older samples
        cannot hold a cell young enough for `keep`.
        """
        view, result = self.grid.view(), {}
        for p, sample_tick in list(self.recent_ticks.items()):
            if sample_tick < oldest_tick:
                continue
            cell = view.record(p)
            if cell is not None and keep(cell):
                result[p] = cell
        return result

    def _current_cells(self):
        if self.latest_tick is None:
            return {}
        return self._recent_cells(self._is_current, self.latest_tick - 5)

    def _planning_cells(self):
        if (
            self.policy.memory_mode == "current"
            or not self.policy.use_historical_geometry
        ):
            return self._current_cells()
        return self.grid.snapshot()

    def status(self):
        with self.queue_lock:
            pending_bytes = self.pending_bytes
            pending_sections = len(self.pending_sections)
        return {
            "policy": self.policy.payload(),
            "active_cells": self.grid.known,
            "active_sections": len(self.grid.sections),
            "evicted_sections": self.evicted_sections,
            "active_accounted_bytes": self.active_bytes,
            "active_byte_limit": self.byte_limit,
            "persistence_pending_bytes": pending_bytes,
            "persistence_byte_limit": self.queue_byte_limit,
            "persistence_queue": pending_sections,
            "automatic_recall_count": self.recall_count,
            "automatic_recall_last_ms": self.recall_ms,
        }

    def _admit_history(self, cells):
        """Recalled history fills unknown cells only; it never replaces live ones."""
        grid, changed = self.grid, False
        for cell in cells:
            pid = grid.provenance_id(
                cell.get("observed_tick"),
                cell.get("observation_session"),
                cell.get("observed_at_ns"),
            )
            changed |= grid.put(
                tuple(cell["position"]), grid.state_id(cell), pid, only_unknown=True
            )
        return changed

    def recall_surroundings(self):
        """Bounded historical read on the admission lane when entering a section.

        Never flush the writer or evict observed cells to make room for history.
        Recall is planning evidence only; original sessions/timestamps survive.
        """
        self._check()
        if (
            self.latest_center is None
            or self.policy.memory_mode != "persistent"
            or not self.policy.use_historical_geometry
        ):
            return
        region = tuple(v // 16 for v in self.latest_center)
        if region == self.recall_region:
            return
        began = time.perf_counter()
        # Decoding sections we already hold stalled admission (about 250 ms per
        # 32 sections), so only sections that may add history are decoded.
        reach = (32, 16, 32)
        low = tuple(
            (v - r) >> 4 for v, r in zip(self.latest_center, reach, strict=True)
        )
        high = tuple(
            (v + r) >> 4 for v, r in zip(self.latest_center, reach, strict=True)
        )
        wanted = {
            key
            for key in self.history_sections
            if all(a <= k <= b for k, a, b in zip(key, low, high, strict=True))
        }
        if wanted:
            rows = self.archive.recall(
                self.latest_center,
                32,
                16,
                limit=4096,
                section_limit=32,
                only=frozenset(wanted),
            )
            if self._admit_history(rows):
                self.revision += 1
        self.recall_region = region
        self.recall_count += 1
        self.recall_ms = (time.perf_counter() - began) * 1000

    def recall(self, center, radius, height):
        if (
            self.policy.memory_mode != "persistent"
            or not self.policy.use_historical_geometry
        ):
            raise ValueError("Historical recall is disabled by memory policy")
        self.flush()
        self._admit_history(
            self.archive.recall(center, radius, height, limit=RECALL_LIMIT)
        )
        self.revision += 1

    def snapshot(self):
        self._check()
        cells = self._planning_cells()
        records = cells.records() if hasattr(cells, "records") else cells.values()
        return self.revision, json.loads(json.dumps(list(records)))

    def planning_snapshot(self):
        """Internal versioned view that later admission never changes.

        Grid snapshots share section arrays copy-on-write. Consumers must treat
        records as immutable.
        """
        self._check()
        return self.revision, self._planning_cells()

    def fresh(self, tick, max_age=5):
        self._check()
        if max_age >= RECENT_TICKS:
            raise ValueError("Freshness age exceeds retained live samples")
        return self._recent_cells(
            lambda cell: cell.get("observation_session") == self.session
            and 0 <= tick - cell["observed_tick"] <= max_age,
            tick - max_age,
        )

    def flush(self):
        # 10 s was exceeded at attempt end with 8,192-cell awareness sweeps (Step C
        # run 20261030T011203175248Z R5); the archive is evidence, so wait longer.
        deadline = time.monotonic() + 60
        with self.queue_lock:
            while self.pending_sections or self.writing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.gap = True
                    raise RuntimeError("Spatial persistence flush deadline exceeded")
                self.queue_lock.wait(remaining)
        if self.gap:
            self.archive.mark_gap()
        self._check()

    def close(self):
        if self.closed:
            return
        try:
            self.flush()
        finally:
            with self.queue_lock:
                self.stopping = True
                self.queue_lock.notify_all()
            self.worker.join(timeout=10)
            self.closed = True
            if self.worker.is_alive():
                raise RuntimeError("Spatial writer did not stop; archive unverified")
