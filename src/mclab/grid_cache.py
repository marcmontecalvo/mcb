"""Per-search memo layer over an immutable cell snapshot.

A ``Grid`` wraps the snapshot dict and memoises cell classification (flag bits)
and the per-column collision queries of ``collision_geometry``. ``OverlayGrid``
adds planned edits: flags stay exact per cell, and column memos for the few
edited columns are kept apart from the shared ones, so the overlay never
poisons the base tables. Plain dicts keep working everywhere; the memo hooks are
looked up with ``getattr`` and are simply absent on them.
"""

from collections.abc import Mapping

AIR_FLUID = "minecraft:empty"
FALLING = {
    "minecraft:sand",
    "minecraft:red_sand",
    "minecraft:gravel",
    "minecraft:suspicious_sand",
    "minecraft:suspicious_gravel",
}
HAZARDS = {
    "minecraft:fire",
    "minecraft:soul_fire",
    "minecraft:cactus",
    "minecraft:magma_block",
    "minecraft:campfire",
    "minecraft:soul_campfire",
    "minecraft:powder_snow",
    "minecraft:sweet_berry_bush",
    "minecraft:wither_rose",
    "minecraft:bubble_column",  # magma-driven columns pull the body down
}
CLIMBABLE = {
    "minecraft:ladder",
    "minecraft:vine",
    "minecraft:scaffolding",
    "minecraft:twisting_vines",
    "minecraft:twisting_vines_plant",
    "minecraft:weeping_vines",
    "minecraft:weeping_vines_plant",
    "minecraft:cave_vines",
    "minecraft:cave_vines_plant",
}

# Flag bits of one cell.
KNOWN = 1  # cell is not None
EMPTY = 2  # navigation.empty
FULL = 4  # navigation.full_cube
SAFE = 8  # collision_geometry.safe_cell
SOLID = 16  # collision_boxes is non-empty (truthy)
LAVA = 32  # lava fluid or hazard block (route_search._side)
WET = 64  # fluid other than empty
FALLS = 128  # gravity block
CLIMB = 256  # route_moves.climbable
HAS_BOXES = 512  # "collision_boxes" key present (clear_body)


def classify(cell):
    """Flag bits of a cell dict (0 for an unknown cell)."""
    if cell is None:
        return 0
    fluid = cell.get("fluid", AIR_FLUID)
    block = cell.get("block")
    boxes = cell.get("collision_boxes")
    dry = fluid == AIR_FLUID
    flags = KNOWN
    if dry:
        if block == "minecraft:air" or boxes == []:
            flags |= EMPTY
        if block not in HAZARDS:
            flags |= SAFE
        if block in CLIMBABLE:
            flags |= CLIMB
        for box in boxes or ():
            if box == [0, 0, 0, 1, 1, 1]:
                flags |= FULL
                break
    else:
        flags |= WET
    if boxes:
        flags |= SOLID
    if "collision_boxes" in cell:
        flags |= HAS_BOXES
    if "lava" in str(cell.get("fluid", "")) or block in HAZARDS:
        flags |= LAVA
    if block in FALLING:
        flags |= FALLS
    return flags


class _Flags(dict):
    """Point -> flag bits; a miss classifies the cell (hits stay in C)."""

    __slots__ = ("get_cell",)

    def __init__(self, get_cell):
        super().__init__()
        self.get_cell = get_cell

    def __missing__(self, point):
        value = self[point] = classify(self.get_cell(point))
        return value


class _OverlayFlags(dict):
    """Flag bits under planned edits; unedited cells come from the base table."""

    __slots__ = ("edits", "base")

    def __init__(self, edits, base):
        super().__init__()
        self.edits, self.base = edits, base

    def __missing__(self, point):
        cell = self.edits.get(point)
        value = self[point] = classify(cell) if cell is not None else self.base[point]
        return value


class Grid(Mapping):
    """Snapshot dict plus memo tables; treat the snapshot as immutable."""

    __slots__ = (
        "raw",
        "get",
        "fl",
        "cflags",
        "m_clear",
        "m_csurf",
        "m_stand",
        "ecols",
        "o_clear",
        "o_csurf",
        "o_stand",
        "near",
        "o_cut",
    )

    def __init__(self, raw):
        self.raw = raw
        self.get = raw.get
        self.fl = _Flags(raw.get)
        self.cflags = self.fl.__getitem__  # point -> flag bits
        self.m_clear, self.m_csurf, self.m_stand = {}, {}, {}
        self.o_clear, self.o_csurf, self.o_stand = (
            self.m_clear,
            self.m_csurf,
            self.m_stand,
        )
        self.ecols = frozenset()
        self.near = frozenset()  # cells within one step of an edit
        self.o_cut = None  # per-overlay cut_allowed memo for cells near edits

    def __getitem__(self, key):
        return self.raw[key]

    def __contains__(self, key):
        return self.get(key) is not None

    def __iter__(self):
        return iter(self.raw)

    def __len__(self):
        return len(self.raw)


class OverlayGrid(Grid):
    """Planned edits over a base Grid; edited columns use private memos."""

    __slots__ = ("edits", "base")

    def __init__(self, edits, base):
        self.edits, self.base = edits, base
        self.raw = base.raw
        self.fl = _OverlayFlags(edits, base.fl)
        self.cflags = self.fl.__getitem__
        self.m_clear, self.m_csurf, self.m_stand = (
            base.m_clear,
            base.m_csurf,
            base.m_stand,
        )
        self.o_clear, self.o_csurf, self.o_stand = {}, {}, {}
        self.o_cut = {}
        self.ecols = frozenset((p[0], p[2]) for p in edits)
        self.near = frozenset(
            (x + i, y + j, z + k)
            for x, y, z in edits
            for i in (-1, 0, 1)
            for j in (-1, 0, 1)
            for k in (-1, 0, 1)
        )
        base_get, edits_get = base.get, edits.get

        def get(key, default=None):
            value = edits_get(key)
            return value if value is not None else base_get(key, default)

        self.get = get

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __iter__(self):
        yield from self.edits
        yield from (k for k in self.raw if k not in self.edits)

    def __len__(self):
        return sum(1 for _ in self)
