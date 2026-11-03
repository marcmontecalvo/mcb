"""Bounded incremental transport for immutable planner geometry snapshots.

An acknowledged commit publishes a generation atomically. The encoder never
promotes a pending generation until the body acknowledges it; callers may abort
and retry without confusing a partial transfer with admitted geometry.
"""

import base64
import json
import sys
import zlib
from array import array
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from mclab.pathing_grid import GridView, offset_of

FRAME_BYTES = 14_000  # leaves room for the bridge envelope under its 16 KiB cap
# The supported 4096 MiB active map budget can contain about 175k sections.
MAX_SECTIONS = 262144


def _facts(cell):
    return {
        k: v
        for k, v in cell.items()
        if k
        not in {"position", "observed_tick", "observation_session", "observed_at_ns"}
    }


class PlannerMapEncoder:
    def __init__(self):
        self.generation = 0
        self.palette = [None]
        self.ids = {}
        self.sections = {}
        self.source_grid = None
        self.source_sections = {}
        self.pending = None

    def prepare(self, cells):
        if self.pending is not None:
            raise RuntimeError("planner map transfer already pending")
        palette = self.palette.copy()
        ids = self.ids.copy()
        sections = defaultdict(lambda: array("H", [0]) * 4096)

        def intern(cell):
            fact = _facts(cell)
            key = json.dumps(
                fact, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            sid = ids.get(key)
            if sid is None:
                sid = len(palette)
                if sid > 65535:
                    raise ValueError("planner map palette exhausted")
                ids[key] = sid
                palette.append(fact)
            return sid

        source_grid = (
            cells.grid if isinstance(cells, GridView) and cells.frozen else None
        )
        source_sections = dict(cells.sections) if source_grid is not None else {}
        unchanged = set()
        if source_grid is not None:
            remap = {}
            for section_key, section in source_sections.items():
                if (
                    source_grid is self.source_grid
                    and self.source_sections.get(section_key) is section
                ):
                    unchanged.add(section_key)
                    continue
                values = sections[section_key]
                for offset, source_id in enumerate(section.states):
                    if source_id:
                        if source_id not in remap:
                            remap[source_id] = intern(cells.states[source_id])
                        values[offset] = remap[source_id]
        else:
            for (x, y, z), cell in cells.items():
                sections[(x >> 4, y >> 4, z >> 4)][offset_of(x, y, z)] = intern(cell)
        if len(sections) + len(unchanged) > MAX_SECTIONS:
            raise ValueError("planner map section limit exceeded")
        # The wire format is little-endian regardless of host architecture.
        packed = {key: self.sections[key] for key in unchanged}
        for key, values in sections.items():
            if sys.byteorder != "little":
                values.byteswap()
            packed[key] = values.tobytes()
        generation = self.generation + 1
        frames = [{"kind": "begin", "base": self.generation, "generation": generation}]
        for index in range(len(self.palette), len(palette)):
            frames.append(
                {"kind": "palette", "first": index, "facts": [palette[index]]}
            )
        for key in sorted(self.sections.keys() - packed.keys()):
            frames.append({"kind": "remove", "section": list(key)})
        for key, data in sorted(packed.items()):
            if self.sections.get(key) != data:
                frames.append(
                    {
                        "kind": "section",
                        "section": list(key),
                        "states": base64.b64encode(zlib.compress(data)).decode("ascii"),
                    }
                )
        # Batch small deltas to reduce tick and request overhead, without crossing
        # begin/commit boundaries or exceeding the bridge's request limit.
        deltas = frames[1:]
        frames = frames[:1]
        batch = []
        for frame in deltas:
            candidate = {"kind": "batch", "frames": [*batch, frame]}
            if (
                len(batch) >= 256
                or len(json.dumps(candidate, separators=(",", ":")).encode())
                > FRAME_BYTES
            ):
                if not batch:
                    raise ValueError("planner map frame exceeds transport limit")
                frames.append({"kind": "batch", "frames": batch})
                batch = [frame]
            else:
                batch.append(frame)
        if batch:
            frames.append({"kind": "batch", "frames": batch})
        frames.append({"kind": "commit", "generation": generation})
        for frame in frames:
            if len(json.dumps(frame, separators=(",", ":")).encode()) > FRAME_BYTES:
                raise ValueError("planner map frame exceeds transport limit")
        self.pending = (generation, palette, ids, packed, source_grid, source_sections)
        return frames

    def acknowledge(self, generation):
        if self.pending is None or self.pending[0] != generation:
            raise ValueError("planner map generation mismatch")
        (
            self.generation,
            self.palette,
            self.ids,
            self.sections,
            self.source_grid,
            self.source_sections,
        ) = self.pending
        self.pending = None

    def abort(self):
        self.pending = None

    def fork(self):
        """A background encoder can prepare a generation without mutating this one."""
        other = PlannerMapEncoder()
        other.generation = self.generation
        other.palette = self.palette
        other.ids = self.ids
        other.sections = self.sections
        other.source_grid = self.source_grid
        other.source_sections = self.source_sections
        return other


class PlannerMapTransfer:
    """One cancellable, acknowledged snapshot transfer; one wire frame per advance."""

    def __init__(self, send_frame):
        self.send_frame = send_frame
        self.encoder = PlannerMapEncoder()
        self.worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="planner-map"
        )
        self.future = None
        self.staged = None
        self.frames = None
        self.index = 0
        self.started = False
        self.recovered = False
        self.uncertain = False

    def start(self, cells):
        if self.future is not None:
            raise RuntimeError("planner map transfer already active")
        self.staged = self.encoder.fork()
        self.future = self.worker.submit(self.staged.prepare, cells)
        self.frames = None
        self.index = 0
        self.started = False
        self.recovered = False

    def advance(self):
        if self.future is None:
            raise RuntimeError("planner map transfer not started")
        if self.frames is None:
            if not self.future.done():
                return None
            self.frames = self.future.result()  # encoding failure is explicit
        frame = self.frames[self.index]
        # A timeout can occur after the receiver applied the request, so a
        # subsequent cancellation must attempt to clear any partial generation.
        self.started = True
        try:
            observed = self.send_frame(frame)
            expected = (
                frame["generation"]
                if frame["kind"] == "commit"
                else self.encoder.generation
            )
            if type(observed) is not int or observed != expected:
                raise RuntimeError("planner map acknowledgement generation mismatch")
        except Exception as error:
            try:
                observed = self.send_frame({"kind": "status"})
            except Exception as recovery_error:
                error.add_note(f"Planner map status failed: {recovery_error}")
                self.uncertain = True
                raise error from recovery_error
            if frame["kind"] == "commit" and observed == frame["generation"]:
                self.recovered = True
            elif observed == self.encoder.generation:
                try:
                    generation = self.send_frame({"kind": "abort"})
                    if generation != self.encoder.generation:
                        raise RuntimeError("planner map abort generation mismatch")
                except Exception as recovery_error:
                    error.add_note(f"Planner map abort failed: {recovery_error}")
                    self.uncertain = True
                    raise error from recovery_error
                self.future = self.staged = self.frames = None
                self.started = False
                raise
            else:
                error.add_note(f"Planner map generation is uncertain: {observed!r}")
                self.uncertain = True
                raise
        self.index += 1
        if self.index != len(self.frames):
            return None
        generation = frame["generation"]
        self.staged.acknowledge(generation)
        self.encoder = self.staged
        self.future = self.staged = self.frames = None
        self.started = False
        return generation

    def cancel(self):
        if self.future is None:
            return
        try:
            if self.started:
                generation = self.send_frame({"kind": "abort"})
                if type(generation) is not int or generation != self.encoder.generation:
                    raise RuntimeError("planner map abort generation mismatch")
        except Exception:
            self.uncertain = True
            raise
        finally:
            self.future.cancel()
            self.future = self.staged = self.frames = None
            self.started = False

    def reset(self):
        if self.future is not None:
            raise RuntimeError("cannot reset an active map transfer")
        if self.send_frame({"kind": "reset"}) != 0:
            raise RuntimeError("planner map reset generation mismatch")
        self.encoder = PlannerMapEncoder()
        self.uncertain = False

    def close(self):
        try:
            self.cancel()
        finally:
            self.worker.shutdown(wait=True, cancel_futures=True)


class PlannerMapCoordinator:
    """Serialize searches sharing the body's one staging area.

    A newly requested snapshot supersedes an unfinished transfer, explicitly
    aborting it on the body. Previously committed readers remain immutable.
    """

    def __init__(self, send_frame):
        self.transfer = PlannerMapTransfer(send_frame)
        self.owner = None

    def begin(self, owner, cells):
        if self.owner is not None:
            self.owner.superseded = True
            self.transfer.cancel()
        if self.transfer.uncertain:
            self.transfer.reset()
        self.owner = owner
        self.transfer.start(cells)

    def advance(self, owner):
        if self.owner is not owner:
            raise RuntimeError("planner map search superseded")
        try:
            generation = self.transfer.advance()
        except Exception:
            if self.transfer.future is None:
                self.owner = None
            raise
        if generation is not None:
            self.owner = None
        return generation

    def cancel(self, owner):
        if self.owner is owner:
            try:
                self.transfer.cancel()
            finally:
                self.owner = None

    def close(self):
        self.transfer.close()
        self.owner = None

    def cancel_active(self):
        if self.owner is not None:
            self.owner.superseded = True
            try:
                self.transfer.cancel()
            finally:
                self.owner = None
