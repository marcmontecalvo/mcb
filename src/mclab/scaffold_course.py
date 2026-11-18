"""Geometry and verdicts for the live scaffolding course
(scripts/check-companion-scaffold-course.py); no server, so tests cover it.

Coordinates are world cells. ``origin`` is the spawn feet cell; the ground top is the
cell below it. The hole is HOLE_W (x) by 3 (z) by DEPTH deep, 3 cells east of the
spawn, so the rims are tower A at dx=2 and tower B at dx=14, 12 apart: every span
cell is within scaffold distance 6 of one tower."""

from __future__ import annotations

SCAFFOLDING = "minecraft:scaffolding"
HOLE_X = range(3, 14)  # dx of the hole's cells (11 wide)
HOLE_Z = range(-1, 2)
DEPTH = 4  # hole cells dy -DEPTH..-1; the floor below them is left alone
TOWER_A, TOWER_B = 2, 14  # dx of the towers on the rims (dz 0)
TOWER_H = 5  # stance above the ground: five scaffolding cells
STAIRS = {3: -1, 4: -2, 5: -3, 6: -4}  # dz=+1 row: stone top dy (way down to drops)

Pos = tuple[int, int, int]


def at(origin: Pos, dx: int, dy: int, dz: int) -> Pos:
    return (origin[0] + dx, origin[1] + dy, origin[2] + dz)


def hole_cells(origin: Pos) -> dict[Pos, str]:
    """Every cell the fixture changes -> new state (air, or stone for the stairs)."""
    out = {}
    for dx in HOLE_X:
        for dz in HOLE_Z:
            for dy in range(-DEPTH, 0):
                stone = dz == 1 and dx in STAIRS and dy <= STAIRS[dx]
                out[at(origin, dx, dy, dz)] = (
                    "minecraft:stone" if stone else "minecraft:air"
                )
    return out


def protected_air(origin: Pos) -> frozenset[Pos]:
    """The hole's air cells: nothing may be placed or broken in them."""
    return frozenset(p for p, s in hole_cells(origin).items() if s == "minecraft:air")


def tower_cells(origin: Pos, dx: int) -> list[Pos]:
    return [at(origin, dx, dy, 0) for dy in range(TOWER_H)]


def span_cells(origin: Pos) -> list[Pos]:
    """The bridge cells between the towers, at the towers' top-cell height."""
    return [at(origin, dx, TOWER_H - 1, 0) for dx in HOLE_X]


def stance(origin: Pos, dx: int, up: int) -> Pos:
    """A feet cell ``up`` above the ground stance at column dx."""
    return at(origin, dx, up, 0)


def foreign(placed: dict[Pos, str]) -> dict[Pos, str]:
    """Placed cells whose block is not scaffolding (the course fails on any)."""
    return {p: b for p, b in placed.items() if b != SCAFFOLDING}


def never_crouched(poses) -> bool:
    return not any(str(p).upper() == "CROUCHING" for p in poses)


def stayed_up(ys, floor_y: float) -> bool:
    """A walk that never dropped below ``floor_y`` did not go round on the ground."""
    return bool(ys) and min(ys) >= floor_y


def verdict(legs: dict[str, dict], placed: dict[Pos, str]) -> dict:
    """The course passes when every leg passed and nothing but scaffolding went down."""
    bad = foreign(placed)
    failed = [n for n, leg in legs.items() if not leg.get("passed")]
    return {
        "passed": not failed and not bad and len(legs) == 8,
        "failed_legs": failed,
        "foreign_blocks": {str(p): b for p, b in bad.items()},
        "placed_count": len(placed),
    }
