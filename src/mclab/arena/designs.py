"""Generators for the four combat arenas, built from small reusable helpers.

Every design is a function returning a :class:`~mclab.arena.plan.Plan`.  Floors
are 2 thick, so the floor surface is ``y=1`` and feet stand at ``y=2``; the roof
is 2 thick, so the highest interior row is ``size_y - 3``.  A design builds in
this order: shell, floor, features (later fills win), markers, lights.

To add an arena write a new ``def my_arena(**params) -> Plan`` using ``Builder``,
register it in ``DESIGNS`` and run ``mcplay arena plan my_arena``; the validator
reports anything the runtime would trip over.  See ``docs/ARENAS.md``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field

from mclab.arena import plan as ap
from mclab.arena.plan import Capacity, Cell, Fill, Plan, Team

DIRS = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}
CLOCKWISE = ("north", "east", "south", "west")
LANTERN = "minecraft:lantern[hanging=true]"


def _mc(name: str) -> str:
    return f"minecraft:{name}"


class Builder:
    """Collects fills and markers.  Local origin is the min corner (0, 0, 0)."""

    def __init__(self, name: str, size: Cell, feet: int = 2):
        self.name = name
        self.size = size
        self.feet = feet
        self.top = size[1] - 3
        self.fills: list[Fill] = ap.split_fill(
            Fill((0, 0, 0), (size[0] - 1, size[1] - 1, size[2] - 1), ap.AIR)
        )
        self.teams: dict[str, Team] = {}
        self.mob_spawns: list[Cell] = []
        self.spectator: Cell | None = None
        self._turns = 0
        self._centre = (0, 0)

    # --- rotation: draw one arm, repeat it a quarter turn at a time -----------

    @contextmanager
    def rotated(self, turns: int, centre: tuple[int, int]):
        """Everything drawn inside is turned ``turns`` quarter turns clockwise about ``centre``."""
        before = (self._turns, self._centre)
        self._turns, self._centre = turns % 4, centre
        try:
            yield self
        finally:
            self._turns, self._centre = before

    def _rotate_xz(self, x: int, z: int) -> tuple[int, int]:
        cx, cz = self._centre
        for _ in range(self._turns):
            x, z = cx - (z - cz), cz + (x - cx)
        return x, z

    def facing(self, direction: str) -> str:
        return CLOCKWISE[(CLOCKWISE.index(direction) + self._turns) % 4]

    def at(self, x: int, y: int, z: int) -> Cell:
        """A marker position in the current rotation."""
        rx, rz = self._rotate_xz(x, z)
        return (rx, y, rz)

    # --- fills ----------------------------------------------------------------

    def _add(self, a: Cell, b: Cell, block: str) -> None:
        lo = tuple(min(p, q) for p, q in zip(a, b, strict=True))
        hi = tuple(max(p, q) for p, q in zip(a, b, strict=True))
        if any(c < 0 for c in lo) or any(
            c >= s for c, s in zip(hi, self.size, strict=True)
        ):
            raise ValueError(
                f"{self.name}: fill {list(lo)}..{list(hi)} leaves the bounds {list(self.size)}"
            )
        self.fills.extend(ap.split_fill(Fill(lo, hi, block)))

    def box(self, a: Cell, b: Cell, block: str) -> None:
        (ax, ay, az), (bx, by, bz) = a, b
        ra, rb = self._rotate_xz(ax, az), self._rotate_xz(bx, bz)
        self._add((ra[0], ay, ra[1]), (rb[0], by, rb[1]), block)

    def point(self, p: Cell, block: str) -> None:
        self.box(p, p, block)

    def scatter(self, points: Iterable[tuple[int, int]], y: int, block: str) -> None:
        for x, z in points:
            self.point((x, y, z), block)

    def shell(self, block: str, interior: tuple[Cell, Cell] | None = None) -> None:
        """Fill the whole bounds solid, then carve the interior (if given) back to air."""
        self.box(
            (0, 0, 0), (self.size[0] - 1, self.size[1] - 1, self.size[2] - 1), block
        )
        if interior:
            self.box(interior[0], interior[1], ap.AIR)

    def disc(
        self,
        cx: int,
        cz: int,
        r: float,
        y0: int,
        y1: int,
        block: str,
        inner: float | None = None,
    ) -> None:
        """Cells whose centre is within ``r`` of (cx, cz), skipping those closer than ``inner``."""
        reach = math.ceil(r)
        for x in range(cx - reach, cx + reach + 1):
            run = None
            for z in range(cz - reach, cz + reach + 2):
                d2 = (x - cx) ** 2 + (z - cz) ** 2
                inside = (
                    z <= cz + reach
                    and d2 <= r * r
                    and (inner is None or d2 >= inner * inner)
                )
                if inside and run is None:
                    run = z
                if not inside and run is not None:
                    self.box((x, y0, run), (x, y1, z - 1), block)
                    run = None

    def band(self, y: int, block: str, inset: int = 0) -> None:
        """A flush trim ring around the outer walls (``inset`` cells in from the bounds)."""
        sx, _, sz = self.size
        lo, hi_x, hi_z = inset, sx - 1 - inset, sz - 1 - inset
        self.box((lo, y, lo), (hi_x, y, lo), block)
        self.box((lo, y, hi_z), (hi_x, y, hi_z), block)
        self.box((lo, y, lo), (lo, y, hi_z), block)
        self.box((hi_x, y, lo), (hi_x, y, hi_z), block)

    def banner(self, x: int, y: int, z: int, color: str, facing: str) -> None:
        self.point(
            (x, y, z), f"minecraft:{color}_wall_banner[facing={self.facing(facing)}]"
        )

    # --- cover ----------------------------------------------------------------

    def pillar(
        self,
        x: int,
        z: int,
        y0: int,
        y1: int,
        block: str,
        size: int = 2,
        light_ys: Iterable[int] = (),
        light_block: str = "minecraft:glowstone",
    ) -> None:
        """A floor-to-ceiling pillar, with glowing bands at ``light_ys``."""
        self.box((x, y0, z), (x + size - 1, y1, z + size - 1), block)
        for y in light_ys:
            self.box((x, y, z), (x + size - 1, y, z + size - 1), light_block)

    def low_wall(
        self,
        a: tuple[int, int],
        b: tuple[int, int],
        feet: int,
        block: str,
        height: int = 1,
    ) -> None:
        """A cover wall.  Two-high walls end in one-block steps so the top stays reachable."""
        (x0, z0), (x1, z1) = a, b
        if x0 != x1 and z0 != z1:
            raise ValueError("a low wall runs along one axis")
        if height not in (1, 2):
            raise ValueError("a low wall is 1 or 2 blocks high")
        if height == 2 and abs(x1 - x0) + abs(z1 - z0) < 2:
            raise ValueError("a two-high wall needs at least 3 cells for its steps")
        self.box((x0, feet, z0), (x1, feet + height - 1, z1), block)
        if height == 2:
            for x, z in (a, b):
                self.point((x, feet + 1, z), ap.AIR)

    def stair_ramp(
        self,
        start: tuple[int, int],
        direction: str,
        width: int,
        rise: int,
        floor_top: int,
        block: str,
        stairs: str,
    ) -> None:
        """Climb ``rise`` blocks, one per cell, going ``direction`` from ``start``.

        ``start`` is the lowest step; the ramp widens towards +x (north/south) or +z
        (east/west).  ``floor_top`` is the y of the floor surface it starts from.
        """
        dx, dz = DIRS[direction]
        cross = (abs(dz), abs(dx))
        facing = self.facing(direction)
        for i in range(rise):
            x, z = start[0] + dx * i, start[1] + dz * i
            far = (x + cross[0] * (width - 1), z + cross[1] * (width - 1))
            self.box((x, floor_top + 1, z), (far[0], floor_top + rise, far[1]), ap.AIR)
            if i:
                self.box((x, floor_top + 1, z), (far[0], floor_top + i, far[1]), block)
            self.box(
                (x, floor_top + 1 + i, z),
                (far[0], floor_top + 1 + i, far[1]),
                f"{stairs}[facing={facing}]",
            )

    def gallery(
        self,
        inner: tuple[tuple[int, int], tuple[int, int]],
        raise_by: int,
        window: tuple[tuple[int, int], tuple[int, int]],
        wall: str | None = None,
        wall_box: tuple[tuple[int, int], tuple[int, int]] | None = None,
        glass: str = "minecraft:glass",
    ) -> Cell:
        """A raised spectator box sealed by solid wall, with a glass window onto the floor.

        ``wall_box`` (x,z corners) is first filled solid with ``wall`` (skip it when the
        surrounding mass is already solid).  Returns the spectator's feet cell.
        """
        floor = self.feet + raise_by
        if wall and wall_box:
            self.box(
                (wall_box[0][0], self.feet, wall_box[0][1]),
                (wall_box[1][0], self.top, wall_box[1][1]),
                wall,
            )
        (x0, z0), (x1, z1) = inner
        self.box((x0, floor, z0), (x1, self.top, z1), ap.AIR)
        self.box(
            (window[0][0], floor + 1, window[0][1]),
            (window[1][0], floor + 3, window[1][1]),
            glass,
        )
        mid_z = (z0 + z1) // 2
        for x in (x0 + 1, x1 - 1):
            self.point((x, self.top, mid_z), LANTERN)
        return ((x0 + x1) // 2, floor, mid_z)

    def gallery_north(
        self,
        cx: int,
        width: int,
        depth: int,
        raise_by: int,
        wall: str,
        window_depth: int = 1,
    ) -> Cell:
        """A gallery set into the north wall, centred on ``cx`` and looking south."""
        front = 2 + depth
        self.spectator = self.gallery(
            inner=((cx - width, 2), (cx + width, front - 1)),
            raise_by=raise_by,
            window=(
                (cx - width + 1, front),
                (cx + width - 1, front + window_depth - 1),
            ),
            wall=wall,
            wall_box=((cx - width - 1, 2), (cx + width + 1, front)),
        )
        return self.spectator

    # --- markers --------------------------------------------------------------

    def team(self, name: str, pads: list[Cell], entry: Cell) -> None:
        self.teams[name] = Team(pads=pads, entry=entry)

    def spawns(self, *cells: Cell) -> None:
        self.mob_spawns.extend(cells)

    def spawn_pad(self, cell: Cell, floor: str, centre: str) -> None:
        """Mark a 3x3 mob portal in the floor and register the spawn point."""
        x, y, z = cell
        self.box((x - 1, y - 1, z - 1), (x + 1, y - 1, z + 1), floor)
        self.point((x, y - 1, z), centre)
        self.mob_spawns.append(self.at(x, y, z))

    # --- plan and lights ------------------------------------------------------

    def to_plan(self, capacity: Capacity, spectator: Cell | None = None) -> Plan:
        return Plan(
            name=self.name,
            size=self.size,
            fills=list(self.fills),
            teams=dict(self.teams),
            mob_spawns=list(self.mob_spawns),
            spectator=spectator or self.spectator or (0, 0, 0),
            capacity=capacity,
        )

    def grid(self) -> ap.Grid:
        return ap.voxelize(self.to_plan(Capacity(1, 1, 0), spectator=(0, 0, 0)))

    def hang_lanterns(
        self, points: Iterable[tuple[int, int]], y: int | None = None
    ) -> None:
        """Hang lanterns from the ceiling at the given (x, z), where the cell is free."""
        y = self.top if y is None else y
        grid = self.grid()
        for x, z in points:
            rx, rz = self._rotate_xz(x, z)
            if (
                grid.in_bounds(rx, y + 1, rz)
                and not grid.solid[grid.idx(rx, y, rz)]
                and grid.solid[grid.idx(rx, y + 1, rz)]
            ):
                self._add((rx, y, rz), (rx, y, rz), LANTERN)

    def ensure_light(
        self, block: str, min_level: int = ap.MIN_LIGHT, rounds: int = 12
    ) -> None:
        """Top up darkness with flush floor lamps: the darkest spots first, a few blocks apart."""
        for _ in range(rounds):
            grid = self.grid()
            light = ap.light_levels(grid)
            dark = []
            for cell in grid.standable():
                level = light[grid.idx(*cell)]
                below = grid.base(cell[0], cell[1] - 1, cell[2])
                if level < min_level and not below.endswith(("stairs", "slab")):
                    dark.append((level, cell))
            if not dark:
                return
            chosen: list[Cell] = []
            for _, cell in sorted(dark):
                if all(
                    abs(cell[0] - c[0]) + abs(cell[2] - c[2]) > 4 or cell[1] != c[1]
                    for c in chosen
                ):
                    chosen.append(cell)
            for x, y, z in chosen:
                self._add((x, y - 1, z), (x, y - 1, z), block)
        raise RuntimeError(
            f"{self.name}: could not light every floor cell in {rounds} rounds"
        )


# --- design: vault ------------------------------------------------------------


def vault(pillars: int = 6) -> Plan:
    """Raid-vault chamber: dark gilded stone, a stepped central dais, pillars, glowing inlays.

    One team of up to four fighters starts in the gilded bay at the south end; mobs
    pour out of seven floor portals; spectators watch from a raised box in the north wall.
    """
    wall, floor = _mc("polished_blackstone_bricks"), _mc("deepslate_tiles")
    plain, gold, glow = (
        _mc("polished_blackstone"),
        _mc("gilded_blackstone"),
        _mc("glowstone"),
    )
    chisel, stairs = (
        _mc("chiseled_polished_blackstone"),
        _mc("polished_blackstone_brick_stairs"),
    )
    b = Builder("vault", (41, 15, 47))
    top, sx, sz = b.top, 41, 47

    b.shell(wall, interior=((2, 2, 2), (38, top, 44)))
    b.box((2, 1, 2), (38, 1, 44), floor)
    b.disc(20, 26, 17, 1, 1, chisel, inner=16)
    b.disc(20, 26, 12, 1, 1, gold, inner=11)

    # trim bands, glowing wall inlays and ceiling ribs sit flush in the wall planes
    b.band(4, gold, inset=1)
    b.band(top - 1, chisel, inset=1)
    for z in range(10, 45, 8):
        b.point((1, 7, z), glow)
        b.point((sx - 2, 7, z), glow)
    for x in (8, 20, 32):
        b.point((x, 7, sz - 2), glow)
    for z in (12, 20, 28, 36):
        b.box((2, top + 1, z), (38, top + 1, z), gold)
        b.banner(2, 7, z, "red", "east")
        b.banner(38, 7, z, "red", "west")

    # central dais: a one-block stair ring around a two-high platform, two tiers
    b.box((14, 2, 20), (26, 2, 32), wall)
    b.box((15, 2, 21), (25, 3, 31), wall)
    b.box((15, 3, 21), (25, 3, 31), plain)
    b.disc(20, 26, 4, 3, 3, gold)
    b.disc(20, 26, 2, 3, 3, _mc("gold_block"))
    b.box((14, 2, 21), (14, 2, 31), f"{stairs}[facing=east]")
    b.box((26, 2, 21), (26, 2, 31), f"{stairs}[facing=west]")
    b.box((15, 2, 20), (25, 2, 20), f"{stairs}[facing=south]")
    b.box((15, 2, 32), (25, 2, 32), f"{stairs}[facing=north]")
    b.scatter([(17, 23), (23, 23), (17, 29), (23, 29), (20, 26)], 3, glow)

    # pillars in a ring of three rows either side, with glowing bands
    if pillars not in (4, 6):
        raise ValueError("vault pillars must be 4 or 6")
    rows = (14, 25, 36) if pillars == 6 else (14, 36)
    for z in rows:
        for x in (8, 31):
            b.pillar(x, z, 2, top, wall, light_ys=(4, 8), light_block=glow)

    # cover: stepped two-high walls beside the dais, one-high shields before the bay
    for x in (11, 29):
        b.low_wall((x, 17), (x, 23), feet=2, block=plain, height=2)
        b.low_wall((x, 29), (x, 35), feet=2, block=plain, height=2)
    b.low_wall((13, 37), (17, 37), feet=2, block=plain, height=1)
    b.low_wall((23, 37), (27, 37), feet=2, block=plain, height=1)

    # the team bay
    b.box((15, 1, 39), (25, 1, 43), gold)
    b.box((16, 1, 40), (24, 1, 42), plain)
    pads = [(x, 2, z) for z in (41, 43) for x in (17, 19, 21, 23)]
    b.team("red", pads, entry=(20, 2, 39))

    # seven mob portals
    for cell in [
        (6, 2, 6),
        (34, 2, 6),
        (5, 2, 24),
        (35, 2, 24),
        (5, 2, 34),
        (35, 2, 34),
        (20, 2, 13),
    ]:
        b.spawn_pad(cell, gold, glow)

    # spectator box: raised three blocks, a pane of glass over the floor
    b.gallery_north(20, width=6, depth=6, raise_by=3, wall=wall)
    b.hang_lanterns([(x, z) for x in (6, 13, 20, 27, 34) for z in (12, 19, 33, 40)])
    b.ensure_light(glow)
    return b.to_plan(Capacity(teams=1, per_team=4, mobs=15))


# --- design: duel -------------------------------------------------------------


def duel(central_dais: bool = True) -> Plan:
    """A small sandstone pit: sunken circular floor, raised terrace ring, a team on each side.

    Two ramps (north and south) climb to the terrace; four pillars and four one-high
    shields give archers something to trade around; mobs rise from the north and south floor.
    """
    wall, smooth = _mc("sandstone"), _mc("smooth_sandstone")
    cut, chisel, red = (
        _mc("cut_sandstone"),
        _mc("chiseled_sandstone"),
        _mc("red_sandstone"),
    )
    stairs, glow = _mc("sandstone_stairs"), _mc("shroomlight")
    b = Builder("duel", (33, 13, 39))
    top = b.top
    cx, cz = 16, 22

    b.shell(wall)
    b.band(4, cut, inset=1)
    b.band(top, chisel, inset=1)
    b.gallery_north(cx, width=5, depth=5, raise_by=3, wall=wall, window_depth=4)

    b.disc(cx, cz, 13, 2, top, ap.AIR)
    b.disc(cx, cz, 13, 1, 1, smooth)
    b.disc(cx, cz, 8, 1, 1, red, inner=7)
    b.disc(cx, cz, 13, 2, 3, cut, inner=10)
    b.disc(cx, cz, 13, 3, 3, smooth, inner=10)
    b.disc(cx, cz, 10.9, 3, 3, chisel, inner=10)

    b.stair_ramp(
        (cx - 1, cz - 10),
        "north",
        width=3,
        rise=2,
        floor_top=1,
        block=cut,
        stairs=stairs,
    )
    b.stair_ramp(
        (cx - 1, cz + 10),
        "south",
        width=3,
        rise=2,
        floor_top=1,
        block=cut,
        stairs=stairs,
    )

    for x in (cx - 5, cx + 4):
        for z in (cz - 5, cz + 4):
            b.pillar(x, z, 2, top, wall, light_ys=(5,), light_block=glow)
    if central_dais:
        b.disc(cx, cz, 3, 2, 2, cut)
        b.disc(cx, cz, 2, 2, 2, chisel)
        b.point((cx, 2, cz), glow)
    for x in (cx - 6, cx + 6):
        b.low_wall((x, cz - 2), (x, cz + 2), feet=2, block=smooth, height=1)
    for z in (cz - 6, cz + 6):
        b.low_wall((cx - 2, z), (cx + 2, z), feet=2, block=smooth, height=1)

    # the two sides: coloured rugs on the terrace, banners on the end walls
    for team, color, sign in (("red", "red", -1), ("blue", "blue", 1)):
        side = (lambda x: x) if sign < 0 else (lambda x: 2 * cx - x)
        rug = (3, 6) if sign < 0 else (cx * 2 - 6, cx * 2 - 3)
        b.box((rug[0], 3, cz - 3), (rug[1], 3, cz + 3), _mc(f"{color}_terracotta"))
        pads = [
            (side(4), 4, cz - 2),
            (side(4), 4, cz),
            (side(4), 4, cz + 2),
            (side(5), 4, cz - 1),
            (side(5), 4, cz + 1),
        ]
        b.team(team, pads, entry=(side(6), 4, cz))
        b.banner(cx + sign * 13, 7, cz, color, "east" if sign < 0 else "west")

    for cell in [(cx, 2, cz - 8), (cx, 2, cz + 8)]:
        b.spawn_pad(cell, red, glow)
    b.hang_lanterns(
        [
            (round(cx + r * math.cos(a)), round(cz + r * math.sin(a)))
            for r in (6, 11)
            for a in [i * math.pi / 6 for i in range(12)]
        ]
    )
    b.ensure_light(glow)
    return b.to_plan(Capacity(teams=2, per_team=2, mobs=6))


# --- design: hall -------------------------------------------------------------


def hall(pillars_per_row: int = 3) -> Plan:
    """A long hall with a colonnade, side aisles and a raised dais at each end.

    Red holds the west dais, blue the east (or one co-op group uses both); thirty mobs
    come from the aisles and are funnelled through the colonnade gaps into the nave.
    """
    wall, floor, trim = (
        _mc("stone_bricks"),
        _mc("deepslate_tiles"),
        _mc("chiseled_stone_bricks"),
    )
    mossy, lamp = _mc("mossy_stone_bricks"), _mc("sea_lantern")
    beam, stairs = _mc("stripped_dark_oak_log[axis=z]"), _mc("stone_brick_stairs")
    rug, edge = _mc("red_terracotta"), _mc("yellow_terracotta")
    b = Builder("hall", (67, 15, 43))
    top = b.top
    mirror_x = lambda x: 66 - x  # noqa: E731

    b.shell(wall, interior=((2, 2, 2), (64, top, 40)))
    b.box((2, 1, 2), (64, 1, 40), floor)
    b.box((10, 1, 20), (56, 1, 22), rug)
    b.box((10, 1, 19), (56, 1, 19), edge)
    b.box((10, 1, 23), (56, 1, 23), edge)
    b.band(4, mossy, inset=1)
    b.band(top - 1, trim, inset=1)

    xs = [14 + 8 * i for i in range(pillars_per_row)]
    cols = sorted({*xs, *(65 - x for x in xs)})
    for x in cols:
        b.box((x, top, 2), (x + 1, top, 40), beam)
        for z in (10, 31):
            b.pillar(x, z, 2, top - 1, wall, light_ys=(4, 8), light_block=lamp)

    # raised dais at each end, 19 wide, with a full-width ramp
    for side in (0, 1):
        fx = (lambda x: x) if side == 0 else mirror_x
        b.box((fx(2), 2, 12), (fx(9), 3, 30), wall)
        b.box((fx(3), 3, 13), (fx(8), 3, 29), floor)
        b.stair_ramp(
            (fx(11), 12),
            "west" if side == 0 else "east",
            width=19,
            rise=2,
            floor_top=1,
            block=wall,
            stairs=stairs,
        )
        color = ("red", "blue")[side]
        b.box((fx(3), 3, 14), (fx(7), 3, 28), _mc(f"{color}_terracotta"))
        b.box((fx(3), 3, 13), (fx(8), 3, 13), edge)
        b.box((fx(3), 3, 29), (fx(8), 3, 29), edge)
        pads = [(fx(x), 4, z) for x in (4, 7) for z in (15, 18, 21, 24, 27)]
        b.team(color, pads, entry=(fx(9), 4, 21))
        for z in (14, 21, 28):
            b.banner(fx(2), 8, z, color, "east" if side == 0 else "west")

    # the nave: a low central stage and cover either side of the rug
    b.box((29, 2, 17), (37, 2, 25), trim)
    b.box((31, 2, 19), (35, 2, 23), floor)
    b.scatter([(30, 18), (36, 18), (30, 24), (36, 24), (33, 21)], 2, lamp)
    for x in (20, mirror_x(20)):
        b.low_wall((x, 14), (x, 18), feet=2, block=mossy, height=2)
        b.low_wall((x, 24), (x, 28), feet=2, block=mossy, height=2)
    for x in (25, mirror_x(25)):
        b.low_wall((x, 19), (x, 23), feet=2, block=mossy, height=1)

    for cell in [(18, 2, 5), (48, 2, 5), (18, 2, 37), (33, 2, 37), (48, 2, 37)]:
        b.spawn_pad(cell, trim, lamp)

    b.gallery_north(33, width=6, depth=6, raise_by=3, wall=wall)
    b.hang_lanterns(
        [(x, z) for x in range(12, 56, 6) for z in (4, 14, 28, 38)], y=top - 1
    )
    b.ensure_light(lamp)
    return b.to_plan(Capacity(teams=2, per_team=10, mobs=30))


# --- design: crossroads -------------------------------------------------------


def crossroads() -> Plan:
    """Four arms meeting at a plaza: one base per side, a stepped obelisk in the centre.

    Red north, blue east, green south, yellow west; each arm is the north arm turned a
    quarter.  Mobs rise in the four plaza corners.  Spectators watch the north base
    from a raised box buried in the north-east corner block.
    """
    wall, floor, trim = (
        _mc("quartz_bricks"),
        _mc("smooth_quartz"),
        _mc("chiseled_quartz_block"),
    )
    pillar, stairs, lamp = (
        _mc("quartz_pillar[axis=y]"),
        _mc("quartz_stairs"),
        _mc("sea_lantern"),
    )
    accent = _mc("purpur_block")
    b = Builder("crossroads", (63, 15, 63))
    top, c = b.top, 31
    colors = ("red", "blue", "green", "yellow")

    b.shell(wall)
    for a, z in (((22, 2), (40, 60)), ((2, 22), (60, 40)), ((19, 19), (43, 43))):
        b.box((a[0], 2, a[1]), (z[0], top, z[1]), ap.AIR)
        b.box((a[0], 1, a[1]), (z[0], 1, z[1]), floor)
    b.disc(c, c, 11, 1, 1, trim, inner=10)
    b.disc(c, c, 6, 1, 1, accent, inner=5)

    # central stepped platform with an obelisk
    b.box((25, 2, 25), (37, 2, 37), wall)
    b.box((26, 2, 26), (36, 3, 36), wall)
    b.box((26, 3, 26), (36, 3, 36), floor)
    b.disc(c, c, 4, 3, 3, trim, inner=3)
    b.box((25, 2, 26), (25, 2, 36), f"{stairs}[facing=east]")
    b.box((37, 2, 26), (37, 2, 36), f"{stairs}[facing=west]")
    b.box((26, 2, 25), (36, 2, 25), f"{stairs}[facing=south]")
    b.box((26, 2, 37), (36, 2, 37), f"{stairs}[facing=north]")
    b.pillar(30, 30, 4, top, pillar, size=3, light_ys=(6, 9), light_block=lamp)
    for i, color in enumerate(colors):
        with b.rotated(i, (c, c)):
            b.box((30, 3, 27), (32, 3, 28), _mc(f"{color}_terracotta"))

    for i, color in enumerate(colors):
        with b.rotated(i, (c, c)):
            b.box((22, 2, 2), (40, 3, 9), wall)
            b.box((23, 3, 3), (39, 3, 8), floor)
            b.box((25, 3, 3), (37, 3, 8), _mc(f"{color}_terracotta"))
            b.box((30, 1, 10), (32, 1, 18), _mc(f"{color}_terracotta"))
            b.stair_ramp(
                (22, 11),
                "north",
                width=19,
                rise=2,
                floor_top=1,
                block=wall,
                stairs=stairs,
            )
            for x in (23, 38):
                b.pillar(x, 15, 2, top, wall, light_ys=(4, 8), light_block=lamp)
            b.low_wall((27, 13), (30, 13), feet=2, block=trim, height=1)
            b.low_wall((32, 13), (35, 13), feet=2, block=trim, height=1)
            for x in (26, 31, 36):
                b.banner(x, 8, 2, color, "south")
            for z in (6, 13):
                b.point((21, 6, z), lamp)
                b.point((41, 6, z), lamp)
            b.box((21, 4, 2), (21, 4, 18), accent)
            b.box((41, 4, 2), (41, 4, 18), accent)
            b.low_wall((23, 27), (23, 35), feet=2, block=trim, height=2)
            pads = [b.at(x, 4, z) for z in (4, 7) for x in (25, 28, 31, 34, 37)]
            b.team(color, pads, entry=b.at(31, 4, 9))
    for cell in [(22, 2, 22), (40, 2, 22), (22, 2, 40), (40, 2, 40)]:
        b.spawn_pad(cell, trim, lamp)

    # spectator box in the north-east corner block, looking west over the north base
    b.spectator = b.gallery(
        inner=((44, 5), (50, 11)), raise_by=3, window=((41, 6), (43, 10))
    )
    b.hang_lanterns(
        [(x, z) for x in range(24, 40, 5) for z in (6, 12, 17)]
        + [(c, z) for z in range(24, 40, 5)]
    )
    b.ensure_light(lamp)
    return b.to_plan(Capacity(teams=4, per_team=10, mobs=24))


# --- design: crash_site -------------------------------------------------------


def _scatter_points(seed: int, count: int, radius: float, cx: int, cz: int):
    """``count`` deterministic (x, z, k) points within ``radius`` of (cx, cz); k is a
    free 16-bit draw for choosing a block or a size."""
    state = seed & 0xFFFFFFFF
    out = []
    while len(out) < count:
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        a = (state >> 8) / float(1 << 24)
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        d = math.sqrt((state >> 8) / float(1 << 24)) * radius
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        out.append(
            (
                cx + round(d * math.cos(a * 2 * math.pi)),
                cz + round(d * math.sin(a * 2 * math.pi)),
                state >> 16,
            )
        )
    return out


def crash_site() -> Plan:
    """A broken airliner on a round island under a night sky: a team-versus-team map.

    The island is 83 blocks across, ringed by a mossy cliff, its floor patchy earth.
    The fuselage lies north to south across the middle, snapped in two either side of
    a smouldering crater; both halves are walk-through cabins with walkable roofs, the
    north one reached up its tilted east wing, the south one up a spill of cargo on its
    west side. The tail fin climbs a dozen blocks over the ground, step by step up its
    leading edge. Around it: the torn-off west wing and an engine, a second engine,
    two shacks with crate stairs to their roofs, four giant trees that hold up the sky
    (no perches: their canopies meet the ceiling), fire pits and scattered wreckage.
    Red starts on glowing pads at the west rim, blue at the east; mobs (in waves mode)
    rise at four portals between them; spectators watch from behind the north cliff.
    No real fire: embers are shroomlight.
    """
    rock, cliff, sky, star = (
        _mc("deepslate"),
        _mc("mossy_cobblestone"),
        _mc("black_concrete"),
        _mc("sea_lantern"),
    )
    soil, ember, scorch, char = (
        _mc("coarse_dirt"),
        _mc("shroomlight"),
        _mc("coal_block"),
        _mc("blackstone"),
    )
    hull, belly, stripe, accent = (
        _mc("white_concrete"),
        _mc("light_gray_concrete"),
        _mc("cyan_concrete"),
        _mc("orange_concrete"),
    )
    pane, cockpit, metal, dark = (
        _mc("light_blue_stained_glass"),
        _mc("black_stained_glass"),
        _mc("gray_concrete"),
        _mc("black_concrete"),
    )
    planks, post, roof, crate = (
        _mc("spruce_planks"),
        _mc("spruce_log[axis=y]"),
        _mc("dark_oak_planks"),
        _mc("stripped_spruce_wood[axis=y]"),
    )
    trunk, canopy, stone = (
        _mc("dark_oak_log[axis=y]"),
        _mc("moss_block"),
        _mc("cobblestone"),
    )
    size = (89, 22, 97)
    cx, cz, radius = 44, 52, 41
    b = Builder("crash_site", size, feet=3)
    g, top = b.feet, b.top  # feet on the ground at y 3; the interior reaches y 19

    # --- the island: rock all round, a round hollow, a mossy cliff foot, a night sky
    b.shell(rock)
    b.disc(cx, cz, radius, g, top, ap.AIR)
    b.disc(cx, cz, radius + 2, g, g + 3, cliff, inner=radius + 0.01)
    b.disc(cx, cz, radius, top + 1, top + 1, sky)
    for x, z, _ in _scatter_points(7, 70, radius - 1, cx, cz):
        b.point((x, top + 1, z), star)

    # --- the floor: coarse dirt with patches of other earth
    b.disc(cx, cz, radius, g - 1, g - 1, soil)
    earth = ("podzol", "mud", "packed_mud", "rooted_dirt", "moss_block", "coarse_dirt")
    for x, z, k in _scatter_points(11, 46, radius - 3, cx, cz):
        b.disc(x, z, 2 + k % 4, g - 1, g - 1, _mc(earth[k % len(earth)]))
    # the scorched skid behind the tail, and burn marks
    for z in range(cz + 22, cz + 31):
        b.box((cx - 2, g - 1, z), (cx + 2, g - 1, z), scorch if z % 3 else char)
    for x, z, k in _scatter_points(23, 18, 16, cx, cz):
        b.point((x, g - 1, z), scorch if k % 2 else char)

    # --- the crater where it broke: a raised lip, a ground-level step, a dip, a
    # smouldering pit; a block a step all the way, so nothing down there is a trap
    b.disc(cx, cz, 10, g, g, soil, inner=8)
    b.disc(cx, cz, 6.5, g - 1, g - 1, ap.AIR)
    b.disc(cx, cz, 6.5, g - 2, g - 2, char)
    b.disc(cx, cz, 3, g - 2, g - 2, ap.AIR)
    b.disc(cx, cz, 3, g - 3, g - 3, scorch)
    b.box((cx - 1, g - 3, cz - 1), (cx + 1, g - 3, cz + 1), ember)

    # --- the fuselage: two halves, 7 wide, a 5-wide cabin 4 high, a walkable crown
    def cabin(z0: int, z1: int, seats: str) -> None:
        b.box((cx - 3, g, z0), (cx + 3, g + 4, z1), hull)
        b.box((cx - 2, g + 5, z0), (cx + 2, g + 5, z1), hull)
        b.box((cx - 3, g, z0), (cx + 3, g, z1), belly)
        for x in (cx - 3, cx + 3):
            b.box((x, g + 3, z0), (x, g + 3, z1), stripe)
            for z in range(z0 + 1, z1, 2):
                b.point((x, g + 2, z), pane)
        b.box((cx - 2, g, z0), (cx + 2, g + 3, z1), ap.AIR)
        b.box((cx - 2, g - 1, z0), (cx + 2, g - 1, z1), planks)
        for z in range(z0 + 2, z1 - 1, 2):
            for x in (cx - 2, cx - 1, cx + 1, cx + 2):
                b.point((x, g, z), seats)
        for z in range(z0 + 1, z1 + 1, 4):
            b.point((cx, g + 3, z), LANTERN)

    north, south = cz - 6, cz + 5  # the broken ends, either side of the crater
    cabin(cz - 19, north, _mc("spruce_stairs[facing=north]"))
    cabin(south, cz + 18, _mc("spruce_stairs[facing=north]"))
    # the nose, closed, with a dark cockpit
    b.box((cx - 2, g, cz - 20), (cx + 2, g + 4, cz - 20), hull)
    b.box((cx - 1, g + 3, cz - 20), (cx + 1, g + 3, cz - 20), cockpit)
    b.box((cx - 1, g, cz - 21), (cx + 1, g + 3, cz - 21), hull)
    b.box((cx - 1, g, cz - 22), (cx + 1, g + 1, cz - 22), belly)
    # the tail cone sweeps up, so there is room to crouch under it
    b.box((cx - 2, g + 1, cz + 19), (cx + 2, g + 5, cz + 19), hull)
    b.box((cx - 1, g + 2, cz + 20), (cx + 1, g + 5, cz + 20), hull)
    b.box((cx - 1, g + 3, cz + 21), (cx + 1, g + 5, cz + 21), hull)
    # the fin: its leading edge rises a block a step, so its top is a climb
    for k in range(11):
        z = cz + 11 + k
        height = min(k, 8)
        b.box((cx, g + 6, z), (cx, g + 6 + height, z), hull)
        if height >= 4:
            b.point((cx, g + 6 + height - 2, z), stripe)
        if height >= 6:
            b.point((cx, g + 6 + height - 4, z), accent)
    # tailplanes either side at the crown's height
    for side in (-1, 1):
        b.box((cx + side * 3, g + 5, cz + 17), (cx + side * 7, g + 5, cz + 19), hull)
        b.point((cx + side * 7, g + 5, cz + 19), accent)
    # torn ends: missing panels, the pieces on the ground
    for x, y, z in (
        (cx + 3, g + 4, north),
        (cx + 2, g + 5, north),
        (cx + 3, g + 1, north),
        (cx - 3, g + 4, south),
        (cx - 2, g + 5, south),
        (cx - 3, g + 1, south),
    ):
        b.point((x, y, z), ap.AIR)

    # the north half's east wing: snapped at the root, its tip in the dirt, a ramp
    for i, x in enumerate(range(cx + 4, cx + 14)):
        b.box((x, g + max(0, 4 - i), cz - 16), (x, g + max(0, 4 - i), cz - 11), belly)
    b.box((cx + 13, g, cz - 16), (cx + 13, g, cz - 11), _mc("red_concrete"))
    b.box((cx + 4, g + 4, cz - 16), (cx + 4, g + 4, cz - 11), metal)
    # the south half's west side: a spill of cargo stepping up to the crown
    for i, x in enumerate(range(cx - 7, cx - 3)):
        b.box((x, g, cz + 7), (x, g + i, cz + 9), crate if i % 2 else planks)
    # the west wing, torn off and lying flat to the north-west, its engine beside it
    b.box((cx - 20, g, cz - 15), (cx - 10, g, cz - 10), belly)
    b.box((cx - 20, g, cz - 15), (cx - 20, g, cz - 10), _mc("lime_concrete"))
    b.box((cx - 16, g, cz - 18), (cx - 14, g + 1, cz - 16), metal)
    b.box((cx - 15, g, cz - 18), (cx - 15, g + 1, cz - 18), dark)
    # the other engine to the south-east, and a slab of skin to climb it from
    b.box((cx + 12, g, cz + 14), (cx + 14, g + 1, cz + 16), metal)
    b.box((cx + 13, g, cz + 16), (cx + 13, g + 1, cz + 16), dark)
    b.box((cx + 11, g, cz + 13), (cx + 11, g, cz + 17), hull)
    # wreckage and luggage: one block high, all of it
    for dx, dz, block in (
        (5, -8, hull), (-5, -4, belly), (7, -3, metal), (-8, -7, stripe),
        (6, 5, hull), (9, 9, belly), (-9, 4, metal), (3, 25, hull), (-3, 28, metal),
        (2, 31, belly), (-11, -2, _mc("light_blue_wool")), (10, -1, _mc("brown_wool")),
        (-7, 13, _mc("red_wool")), (8, 20, _mc("brown_wool")), (-4, -25, belly),
        (5, -27, metal),
    ):  # fmt: skip
        b.point((cx + dx, g, cz + dz), block)

    # --- shacks, fire pits and giant trees, each pair a half turn apart
    for turns in (0, 2):
        with b.rotated(turns, (cx, cz)):
            hx, hz = cx - 18, cz + 26
            b.box((hx - 2, g, hz - 2), (hx + 2, g + 2, hz + 2), planks)
            for x in (hx - 2, hx + 2):
                for z in (hz - 2, hz + 2):
                    b.box((x, g, z), (x, g + 2, z), post)
            b.box((hx - 1, g, hz - 1), (hx + 1, g + 2, hz + 1), ap.AIR)
            b.box((hx + 2, g, hz), (hx + 2, g + 1, hz), ap.AIR)  # the doorway
            b.point((hx, g + 1, hz - 2), _mc("glass"))
            b.box((hx - 3, g + 3, hz - 3), (hx + 3, g + 3, hz + 3), roof)
            b.point((hx, g + 2, hz), LANTERN)
            for i, z in enumerate(range(hz + 4, hz + 7)):
                b.box((hx, g, z), (hx + 1, g + 2 - i, z), crate)
            # a fire pit: a ring of stones round embers
            fx, fz = hx + 6, hz - 4
            b.box((fx - 1, g, fz - 1), (fx + 1, g, fz + 1), stone)
            b.point((fx, g, fz), ap.AIR)
            b.point((fx, g - 1, fz), ember)
            for tx, tz in ((cx - 26, cz - 24), (cx - 12, cz + 34)):
                for i, r in enumerate((6.5, 5.5, 4.5, 3.5, 2.5)):
                    b.disc(tx, tz, r, top - i, top - i, canopy)
                for dx, dz in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                    b.point((tx + dx, top - 4, tz + dz), ember)
                b.box((tx, g, tz), (tx + 1, top, tz + 1), trunk)
                b.point((tx - 1, g, tz), _mc("dark_oak_log[axis=x]"))
                b.point((tx + 2, g, tz + 1), _mc("dark_oak_log[axis=x]"))
                b.point((tx + 1, g, tz - 1), _mc("dark_oak_log[axis=z]"))
                b.point((tx, g, tz + 2), _mc("dark_oak_log[axis=z]"))

    # --- the two bases: glowing pads on a team rug at the west and east rim
    for team, sign, glow in (
        ("red", -1, _mc("pearlescent_froglight")),
        ("blue", 1, star),
    ):
        base = lambda d, s=sign: cx + s * d  # noqa: E731
        b.box(
            (base(36), g - 1, cz - 4),
            (base(32), g - 1, cz + 4),
            _mc(f"{team}_terracotta"),
        )
        pads = [(base(d), g, cz + dz) for dz in (-3, 0, 3) for d in (35, 33)]
        for x, _, z in pads:
            b.point((x, g - 1, z), glow)
        b.team(team, pads, entry=(base(30), g, cz))
        for dz in (-7, 3):
            b.low_wall(
                (base(28), cz + dz),
                (base(28), cz + dz + 4),
                feet=g,
                block=hull,
                height=2,
            )
        b.banner(base(40), g + 4, cz, team, "east" if sign < 0 else "west")

    # --- mob portals between the bases (waves mode)
    for dx, dz in ((0, -33), (0, 33), (-16, 14), (16, -14)):
        b.spawn_pad(
            (cx + dx, g, cz + dz), _mc("polished_blackstone"), _mc("crying_obsidian")
        )

    # --- the spectators' box behind the north cliff, a window onto the field
    b.box((cx - 7, g, cz - radius - 1), (cx + 7, top, cz - radius), rock)
    b.spectator = b.gallery(
        inner=((cx - 5, cz - radius - 7), (cx + 5, cz - radius - 2)),
        raise_by=3,
        window=((cx - 4, cz - radius - 1), (cx + 4, cz - radius)),
    )
    b.ensure_light(ember)
    return b.to_plan(Capacity(teams=2, per_team=4, mobs=12))


# --- the arena server's lobby (not an arena: never in DESIGNS) ---------------


@dataclass(frozen=True)
class LobbyStation:
    """One clickable vanilla interaction station built into the arena lobby walls."""

    label: str
    local_pos: Cell
    block: str
    action: str
    args: dict[str, str] = field(default_factory=dict)
    banner_pos: Cell | None = None
    banner_color: str | None = None
    banner_facing: str | None = None


LOBBY_STATIONS: tuple[LobbyStation, ...] = (
    # North showcase wall (under spectator gallery, facing spawn)
    LobbyStation(
        "Play", (9, 3, 5), _mc("emerald_block"), "play",
        banner_pos=(9, 4, 6), banner_color="lime", banner_facing="south",
    ),
    LobbyStation(
        "Watch", (11, 3, 5), _mc("amethyst_block"), "watch",
        banner_pos=(11, 4, 6), banner_color="purple", banner_facing="south",
    ),
    LobbyStation(
        "Play Again", (13, 3, 5), _mc("gold_block"), "rematch",
        banner_pos=(13, 4, 6), banner_color="yellow", banner_facing="south",
    ),
    LobbyStation(
        "Ready", (15, 3, 5), _mc("diamond_block"), "ready_toggle",
        banner_pos=(15, 4, 6), banner_color="light_blue", banner_facing="south",
    ),
    # West wall: team join / create & leave controls
    LobbyStation(
        "Join Red", (1, 3, 8), _mc("red_concrete"), "join", {"team": "red"},
        banner_pos=(2, 4, 8), banner_color="red", banner_facing="east",
    ),
    LobbyStation(
        "Join Blue", (1, 3, 11), _mc("blue_concrete"), "join", {"team": "blue"},
        banner_pos=(2, 4, 11), banner_color="blue", banner_facing="east",
    ),
    LobbyStation(
        "Join Green", (1, 3, 14), _mc("green_concrete"), "join", {"team": "green"},
        banner_pos=(2, 4, 14), banner_color="green", banner_facing="east",
    ),
    LobbyStation(
        "Join Yellow", (1, 3, 17), _mc("yellow_concrete"), "join", {"team": "yellow"},
        banner_pos=(2, 4, 17), banner_color="yellow", banner_facing="east",
    ),
    LobbyStation(
        "Leave", (1, 3, 20), _mc("white_concrete"), "leave",
        banner_pos=(2, 4, 20), banner_color="white", banner_facing="east",
    ),
    # East wall: host rule, bot, and countdown controls
    LobbyStation(
        "Map", (23, 3, 8), _mc("lapis_block"), "cycle_map",
        banner_pos=(22, 4, 8), banner_color="light_blue", banner_facing="west",
    ),
    LobbyStation(
        "Mode", (23, 3, 10), _mc("purpur_block"), "cycle_mode",
        banner_pos=(22, 4, 10), banner_color="purple", banner_facing="west",
    ),
    LobbyStation(
        "Add Bot", (23, 3, 12), _mc("iron_block"), "bot_add",
        banner_pos=(22, 4, 12), banner_color="light_gray", banner_facing="west",
    ),
    LobbyStation(
        "Remove Bot", (23, 3, 14), _mc("oxidized_copper"), "bot_remove",
        banner_pos=(22, 4, 14), banner_color="cyan", banner_facing="west",
    ),
    LobbyStation(
        "Uneven Teams", (23, 3, 16), _mc("copper_block"), "toggle_uneven",
        banner_pos=(22, 4, 16), banner_color="orange", banner_facing="west",
    ),
    LobbyStation(
        "Respawns", (23, 3, 18), _mc("netherite_block"), "cycle_respawns",
        banner_pos=(22, 4, 18), banner_color="black", banner_facing="west",
    ),
    LobbyStation(
        "Reopen", (23, 3, 20), _mc("crying_obsidian"), "reopen",
        banner_pos=(22, 4, 20), banner_color="magenta", banner_facing="west",
    ),
)


def lobby() -> Plan:
    """The room the arena server's players spawn in: a sealed, lit hall floating in the void.

    Glass walls look out on the sky; sixteen pads (one red team) hold an 8v8 roster
    while it waits, and the team entry at the centre is the world spawn.  Interactive
    vanilla wall stations (Play, Watch, Play Again, Ready, team join/create, and host
    match/bot controls) sit flush in the walls and dispatch structured ``lobby``
    commands through ``ArenaGuard`` / ``ArenaLobbyStations``.
    """
    wall, floor, glass = _mc("quartz_bricks"), _mc("smooth_quartz"), _mc("glass")
    trim, glow = _mc("cut_copper"), _mc("sea_lantern")
    b = Builder("lobby", (25, 12, 25))
    top = b.top
    b.shell(wall, interior=((2, 2, 2), (22, top, 22)))
    b.box((2, 1, 2), (22, 1, 22), floor)
    for side in range(4):  # a window band in every wall, above the station banners
        with b.rotated(side, (12, 12)):
            b.box((5, 5, 0), (19, top - 1, 1), glass)
    b.disc(12, 12, 3, 1, 1, trim)
    b.point((12, 1, 12), glow)
    pads = [(x, 2, z) for z in (16, 19) for x in range(5, 21, 2)]
    for x, _, z in pads:
        b.point((x, 1, z), trim)
    b.team("red", pads, entry=(12, 2, 12))
    b.gallery_north(12, width=4, depth=3, raise_by=3, wall=wall)
    for station in LOBBY_STATIONS:
        b.point(station.local_pos, station.block)
        if station.banner_pos and station.banner_color and station.banner_facing:
            bx, by, bz = station.banner_pos
            b.banner(bx, by, bz, station.banner_color, station.banner_facing)
    b.hang_lanterns([(x, z) for x in (5, 12, 19) for z in (9, 14, 20)])
    b.ensure_light(glow)
    return b.to_plan(Capacity(teams=1, per_team=len(pads), mobs=0))


DESIGNS: dict[str, Callable[..., Plan]] = {
    "vault": vault,
    "duel": duel,
    "hall": hall,
    "crossroads": crossroads,
    "crash_site": crash_site,
}
NAMES = tuple(DESIGNS)


def build(name: str, **params) -> Plan:
    return DESIGNS[name](**params)
