"""Dependency-free PNG previews of an arena plan: a top-down floor map and a side section.

Pillow is not a project dependency, so this writes PNGs with ``zlib`` and ``struct``.
The top view shows the floor material coloured by height (brighter is higher), walls and
pillars dark, glass cyan, lamps yellow, and the markers: filled squares for team pads, a
white ring for each entry, magenta squares for mob spawns and a cyan square for the spectator.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from mclab.arena import plan as ap

Color = tuple[int, int, int]
BACKGROUND: Color = (22, 24, 30)
TEAM_COLORS: dict[str, Color] = {
    "red": (235, 70, 60),
    "blue": (70, 120, 245),
    "green": (80, 205, 90),
    "yellow": (245, 215, 60),
}
SPAWN_COLOR: Color = (230, 60, 220)
SPECTATOR_COLOR: Color = (60, 240, 240)
FLOOR_FEET = 2  # feet y of the ground floor; heights above it brighten the top view

_RULES: tuple[tuple[tuple[str, ...], Color], ...] = (
    (("glass",), (140, 215, 235)),
    (("sea_lantern",), (190, 240, 230)),
    (("glowstone", "froglight"), (250, 225, 130)),
    (("shroomlight",), (255, 150, 60)),
    (("lantern", "torch"), (255, 205, 90)),
    (("red_terracotta", "red_wall_banner"), (165, 60, 50)),
    (("blue_terracotta", "blue_wall_banner"), (60, 85, 165)),
    (("green_terracotta", "green_wall_banner"), (70, 125, 60)),
    (("yellow_terracotta", "yellow_wall_banner"), (225, 185, 50)),
    (("banner",), (170, 60, 60)),
    (("gilded", "gold"), (200, 160, 60)),
    (("red_sandstone",), (190, 100, 50)),
    (("sandstone",), (222, 205, 150)),
    (("blackstone",), (62, 58, 70)),
    (("deepslate",), (72, 74, 80)),
    (("purpur",), (165, 120, 165)),
    (("quartz",), (232, 228, 222)),
    (("mossy",), (110, 135, 100)),
    (("log", "wood", "planks"), (110, 78, 45)),
    (("stone",), (128, 128, 130)),
)


def block_color(base: str) -> Color:
    for keys, color in _RULES:
        if any(key in base for key in keys):
            return color
    h = zlib.crc32(base.encode())
    return (90 + h % 120, 90 + (h >> 8) % 120, 90 + (h >> 16) % 120)


def _shade(color: Color, factor: float) -> Color:
    return tuple(max(0, min(255, int(c * factor))) for c in color)  # type: ignore[return-value]


class Canvas:
    def __init__(self, width: int, height: int, background: Color = BACKGROUND):
        self.width, self.height = width, height
        self.pixels = bytearray(bytes(background) * (width * height))

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
        """Fill an inclusive rectangle (clipped to the canvas)."""
        x0, x1 = max(0, x0), min(self.width - 1, x1)
        if x1 < x0:
            return
        row = bytes(color) * (x1 - x0 + 1)
        for y in range(max(0, y0), min(self.height - 1, y1) + 1):
            start = (y * self.width + x0) * 3
            self.pixels[start : start + len(row)] = row

    def outline(self, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
        self.rect(x0, y0, x1, y0, color)
        self.rect(x0, y1, x1, y1, color)
        self.rect(x0, y0, x0, y1, color)
        self.rect(x1, y0, x1, y1, color)

    def save(self, path: Path) -> None:
        raw = b"".join(
            b"\x00" + bytes(self.pixels[y * self.width * 3 : (y + 1) * self.width * 3])
            for y in range(self.height)
        )

        def chunk(kind: bytes, body: bytes) -> bytes:
            return (
                struct.pack(">I", len(body))
                + kind
                + body
                + struct.pack(">I", zlib.crc32(kind + body))
            )

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )


def _scale(plan: ap.Plan) -> int:
    return max(6, min(12, 720 // max(plan.size[0], plan.size[2])))


def top_view(plan: ap.Plan, grid: ap.Grid) -> Canvas:
    s = _scale(plan)
    sx, sy, sz = plan.size
    canvas = Canvas(sx * s, sz * s)
    floor: dict[tuple[int, int], int] = {}
    for x, y, z in grid.standable():
        floor[(x, z)] = max(floor.get((x, z), 0), y)
    for x in range(sx):
        for z in range(sz):
            if (x, z) in floor:
                y = floor[(x, z)]
                below = grid.base(x, y - 1, z)
                color = _shade(block_color(below), 0.75 + 0.12 * (y - FLOOR_FEET))
                if ap.emission(below):
                    color = block_color(below)
            else:
                mid = grid.base(x, min(sy - 1, FLOOR_FEET + 4), z)
                color = (
                    _shade(block_color(mid), 0.32)
                    if not ap.is_glass(mid)
                    else _shade(block_color(mid), 0.7)
                )
            canvas.rect(x * s, z * s, x * s + s - 1, z * s + s - 1, color)
    for name, team in plan.teams.items():
        for px, _, pz in team.pads:
            canvas.rect(
                px * s + 1,
                pz * s + 1,
                px * s + s - 2,
                pz * s + s - 2,
                TEAM_COLORS.get(name, (255, 255, 255)),
            )
        ex, _, ez = team.entry
        canvas.outline(ex * s, ez * s, ex * s + s - 1, ez * s + s - 1, (255, 255, 255))
    for x, _, z in plan.mob_spawns:
        canvas.rect(
            (x - 1) * s,
            (z - 1) * s,
            (x + 2) * s - 1,
            (z + 2) * s - 1,
            _shade(SPAWN_COLOR, 0.45),
        )
        canvas.rect(x * s + 1, z * s + 1, x * s + s - 2, z * s + s - 2, SPAWN_COLOR)
    spx, _, spz = plan.spectator
    canvas.rect(spx * s, spz * s, spx * s + s - 1, spz * s + s - 1, SPECTATOR_COLOR)
    canvas.outline(spx * s - 1, spz * s - 1, spx * s + s, spz * s + s, (0, 0, 0))
    return canvas


def side_view(plan: ap.Plan, grid: ap.Grid) -> Canvas:
    """A section at the spectator's x: z runs left to right, up is up."""
    s = _scale(plan)
    sx, sy, sz = plan.size
    x = min(max(plan.spectator[0], 0), sx - 1)
    canvas = Canvas(sz * s, sy * s)
    for z in range(sz):
        for y in range(sy):
            base = grid.base(x, y, z)
            if base == "air":
                continue
            color = block_color(base)
            if ap.is_passable(base):
                color = _shade(color, 0.8)
            py = (sy - 1 - y) * s
            canvas.rect(z * s, py, z * s + s - 1, py + s - 1, color)
    mx, my, mz = plan.spectator
    if mx == x:
        py = (sy - 1 - my) * s
        canvas.rect(mz * s + 2, py + 2, mz * s + s - 3, py + s - 3, SPECTATOR_COLOR)
    for name, team in plan.teams.items():
        for px, py_, pz in team.pads:
            if px == x:
                py = (sy - 1 - py_) * s
                canvas.rect(
                    pz * s + 2,
                    py + 2,
                    pz * s + s - 3,
                    py + s - 3,
                    TEAM_COLORS.get(name, (255, 255, 255)),
                )
    for mx, my, mz in plan.mob_spawns:
        if abs(mx - x) <= 1:
            py = (sy - 1 - my) * s
            canvas.rect(mz * s + 2, py + 2, mz * s + s - 3, py + s - 3, SPAWN_COLOR)
    return canvas


def render(plan: ap.Plan, out_dir: Path) -> tuple[Path, Path]:
    """Write ``<name>-top.png`` and ``<name>-side.png`` into ``out_dir``."""
    out_dir = Path(out_dir)
    grid = ap.voxelize(plan)
    top, side = out_dir / f"{plan.name}-top.png", out_dir / f"{plan.name}-side.png"
    top_view(plan, grid).save(top)
    side_view(plan, grid).save(side)
    return top, side
