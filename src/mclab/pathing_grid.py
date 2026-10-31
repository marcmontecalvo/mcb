"""Compact planning map: 16-cube sections of palette state ids.

State id 0 and absent sections are unknown. Unknown is never air or support:
views return None for it, and for non-integral coordinates, exactly as a plain
cell dict that lacks the key. Views are read-only mappings, so every existing
predicate (`standable`, `collision_geometry`, the preparers) reads them unchanged.

Snapshots share section arrays; the admission writer copies a section before its
first write after a snapshot (copy-on-write), so a published view never changes.
"""

import json
from array import array
from collections.abc import Mapping
from copy import deepcopy

from mclab.collision_geometry import HAZARDS
from mclab.navigation import empty, full_cube
from mclab.terrain_steps import EXCAVATABLE
from mclab.terrain_steps import FALLING as FALLING_BLOCKS

SECTION_CELLS = 4096
# states ('H', 2 bytes) + provenance ('I', 4 bytes) per cell, plus object overhead.
SECTION_BYTES = SECTION_CELLS * 6 + 256
PROVENANCE_FIELDS = ("observed_tick", "observation_session", "observed_at_ns")
_NOT_FACTS = frozenset({"position", *PROVENANCE_FIELDS})
_GEOMETRY = frozenset(
    {"block", "fluid", "replaceable", "properties", "collision_boxes"}
)

# Per-state flags, derived once per palette entry from the existing predicates.
PASSABLE = 1  # navigation.empty
FULL_SOLID = 2  # navigation.full_cube
PARTIAL = 4  # has collision, not a full cube
WATER = 8
LAVA = 16
HAZARD = 32  # collision_geometry.safe_cell rejects it (fluid or HAZARDS block)
FALLING = 64  # sand/gravel, as terrain_steps checks neighbours
BREAKABLE = 128  # in terrain_steps.EXCAVATABLE (kit-dependent tool still checked)


def state_flags(facts):
    fluid = facts.get("fluid", "minecraft:empty")
    block = facts.get("block")
    flags = 0
    if empty(facts):
        flags |= PASSABLE
    if full_cube(facts):
        flags |= FULL_SOLID
    elif facts.get("collision_boxes"):
        flags |= PARTIAL
    if "water" in fluid:
        flags |= WATER
    if "lava" in fluid:
        flags |= LAVA
    if fluid != "minecraft:empty" or block in HAZARDS:
        flags |= HAZARD
    if block in FALLING_BLOCKS:
        flags |= FALLING
    if block in EXCAVATABLE:
        flags |= BREAKABLE
    return flags


def _state_key(facts):
    """Fast hashable key for native geometry; callers fall back to canonical JSON."""
    if not facts.keys() <= _GEOMETRY:
        raise TypeError("non-native facts")
    properties = facts.get("properties")
    boxes = facts.get("collision_boxes")
    return (
        tuple(sorted(facts)),
        facts.get("block"),
        facts.get("fluid"),
        repr(facts.get("replaceable")),
        None if properties is None else tuple(sorted(properties.items())),
        None if boxes is None else tuple(tuple(box) for box in boxes),
    )


def offset_of(x, y, z):
    return ((x & 15) << 8) | ((y & 15) << 4) | (z & 15)


class Section:
    __slots__ = ("states", "provenance", "known", "generation")

    def __init__(self, generation, source=None):
        self.generation = generation
        if source is None:
            self.states = array("H", [0]) * SECTION_CELLS
            self.provenance = array("I", [0]) * SECTION_CELLS
            self.known = 0
        else:
            self.states = array("H", source.states)
            self.provenance = array("I", source.provenance)
            self.known = source.known


class PathingGrid:
    """Single-writer store. Readers use `view()` (live) or `snapshot()` (frozen)."""

    def __init__(self):
        self.sections = {}
        self.states = [None]
        self.flags = bytearray(1)
        self._state_ids = {}
        self.provenances = [None]
        self._provenance_ids = {}
        self.generation = 0

    def state_id(self, cell):
        """Intern the geometry facts of a cell; the palette owns its own copy."""
        facts = {k: v for k, v in cell.items() if k not in _NOT_FACTS}
        try:
            key = _state_key(facts)
            sid = self._state_ids.get(key)
        except (TypeError, AttributeError):
            # Unusual facts (nested values, non-dict properties): exact JSON key.
            # Non-JSON values raise here, failing admission closed.
            key = json.dumps(facts, sort_keys=True, separators=(",", ":"))
            sid = self._state_ids.get(key)
        if sid is None:
            sid = len(self.states)
            if sid > 0xFFFF:
                raise RuntimeError("Pathing grid state palette exhausted")
            self.states.append(deepcopy(facts))
            self.flags.append(state_flags(facts))
            self._state_ids[key] = sid
        return sid

    def provenance_id(self, tick, session, observed_at_ns):
        key = (tick, session, observed_at_ns)
        pid = self._provenance_ids.get(key)
        if pid is None:
            pid = len(self.provenances)
            self.provenances.append(key)
            self._provenance_ids[key] = pid
        return pid

    def put(self, position, sid, pid, *, only_unknown=False):
        """Write one cell; returns False when `only_unknown` kept an existing cell."""
        x, y, z = position
        key = (x >> 4, y >> 4, z >> 4)
        section = self.sections.get(key)
        if section is None:
            section = self.sections[key] = Section(self.generation)
        offset = offset_of(x, y, z)
        if only_unknown and section.states[offset]:
            return False
        if section.generation != self.generation:
            section = self.sections[key] = Section(self.generation, section)
        if not section.states[offset]:
            section.known += 1
        section.states[offset] = sid
        section.provenance[offset] = pid
        return True

    def record(self, position, sid, pid):
        record = dict(self.states[sid])
        tick, session, observed_at_ns = self.provenances[pid]
        for name, value in zip(
            PROVENANCE_FIELDS, (tick, session, observed_at_ns), strict=True
        ):
            if value is not None:
                record[name] = value
        record["position"] = list(position)
        return record

    def view(self):
        return GridView(self, self.sections, frozen=False)

    def snapshot(self):
        sections = dict(self.sections)
        self.generation += 1
        return GridView(self, sections, frozen=True)

    @property
    def known(self):
        return sum(section.known for section in self.sections.values())

    @property
    def accounted_bytes(self):
        # ponytail: palettes are small next to section arrays; count sections only.
        return len(self.sections) * SECTION_BYTES


class GridView(Mapping):
    """Read-only `(x, y, z) -> geometry facts` mapping over grid sections.

    `get`/`[]` return the shared palette facts (block, fluid, replaceable,
    properties, collision_boxes) without position or provenance; that is all
    planners and preparers read. `record(p)` returns the full owned-style record.
    Consumers must never mutate returned facts: they are shared by every cell
    in that state.
    """

    __slots__ = ("grid", "sections", "states", "frozen")

    def __init__(self, grid, sections, *, frozen):
        self.grid, self.sections, self.frozen = grid, sections, frozen
        self.states = grid.states

    def _locate(self, position):
        """(section, offset, integral position) or None for unknown/non-cells."""
        try:
            x, y, z = position
        except (TypeError, ValueError):
            return None
        if not (type(x) is int and type(y) is int and type(z) is int):
            try:
                ix, iy, iz = int(x), int(y), int(z)
            except (TypeError, ValueError, OverflowError):
                return None
            if ix != x or iy != y or iz != z:
                return None  # A half-block height never names a cell.
            x, y, z = ix, iy, iz
        section = self.sections.get((x >> 4, y >> 4, z >> 4))
        if section is None:
            return None
        offset = ((x & 15) << 8) | ((y & 15) << 4) | (z & 15)
        if not section.states[offset]:
            return None
        return section, offset, (x, y, z)

    def get(self, position, default=None):
        # Inlined integer fast path: this is the planners' hottest call.
        try:
            x, y, z = position
        except (TypeError, ValueError):
            return default
        if type(x) is int and type(y) is int and type(z) is int:
            section = self.sections.get((x >> 4, y >> 4, z >> 4))
            if section is None:
                return default
            sid = section.states[((x & 15) << 8) | ((y & 15) << 4) | (z & 15)]
            return self.states[sid] if sid else default
        located = self._locate(position)
        if located is None:
            return default
        section, offset, _ = located
        return self.states[section.states[offset]]

    def __getitem__(self, position):
        located = self._locate(position)
        if located is None:
            raise KeyError(position)
        section, offset, _ = located
        return self.states[section.states[offset]]

    def __contains__(self, position):
        return self._locate(position) is not None

    def record(self, position, default=None):
        """Facts plus position and provenance, as admitted (None if unknown)."""
        located = self._locate(position)
        if located is None:
            return default
        section, offset, p = located
        return self.grid.record(p, section.states[offset], section.provenance[offset])

    def flags(self, position):
        """0 means unknown. Never treat 0 as passable or as support."""
        located = self._locate(position)
        if located is None:
            return 0
        section, offset, _ = located
        return self.grid.flags[section.states[offset]]

    def __iter__(self):
        for (sx, sy, sz), section in list(self.sections.items()):
            states = section.states
            for offset in range(SECTION_CELLS):
                if states[offset]:
                    yield (
                        sx * 16 + (offset >> 8),
                        sy * 16 + ((offset >> 4) & 15),
                        sz * 16 + (offset & 15),
                    )

    def records(self):
        return [self.record(p) for p in self]

    def __len__(self):
        return sum(section.known for section in list(self.sections.values()))

    def __deepcopy__(self, memo):
        return self if self.frozen else self.grid.snapshot()

    def frozen_view(self):
        return self if self.frozen else self.grid.snapshot()
