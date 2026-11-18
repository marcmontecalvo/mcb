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


# Fewest scaffolding cells a leg needs; a leg may place twice that (a ground layer
# laid, broken and laid again would blow it) and breaks nothing before leg 7.
MIN_CELLS = {"0": 0, "1": 5, "2": 4, "3": 2, "4": 5, "5": 7, "6": 0, "7": 0}
OVERPLACE = 2
STANDSTILL_S = 3.0


def edit_problems(index: str, placed: int, broken: int) -> list[str]:
    """Why a leg's edits are wrong: too many placed, or any break before 7."""
    out = []
    if placed > MIN_CELLS[index] * OVERPLACE:
        out.append(
            f"placed {placed} scaffolding, minimum {MIN_CELLS[index]} "
            f"(limit {MIN_CELLS[index] * OVERPLACE})"
        )
    if broken and index != "7":
        out.append(f"broke {broken} blocks before the take-down")
    return out


def event_stats(events, windows: dict[str, tuple[float, float]]) -> dict[str, dict]:
    """Per leg: plans finished and the most nodes one expanded, from
    build_stats.iter_events (seconds, kind, data); windows are (start, end) seconds."""
    out = {n: {"plans": 0, "max_expanded": 0} for n in windows}
    for at, kind, data in events:
        if kind != "plan_finished":
            continue
        for name, (a, z) in windows.items():
            if a <= at <= z:
                out[name]["plans"] += 1
                out[name]["max_expanded"] = max(
                    out[name]["max_expanded"], data.get("expanded") or 0
                )
    return out


def standstills(rows, tick_ranges: dict[str, tuple[int, int]]):
    """idle_gaps VIOLATION rows -> ({leg: [rows]}, rows in no leg)."""
    by_leg: dict[str, list] = {}
    outside = []
    for r in rows:
        if r.get("kind") != "VIOLATION":
            continue
        a, z = r["ticks"]
        hit = next(
            (n for n, (t0, t1) in tick_ranges.items() if a <= t1 and z >= t0), None
        )
        (by_leg.setdefault(hit, []) if hit else outside).append(r)
    return by_leg, outside


def finish(legs, placed, caps, idle_rows, idle_ok, tick_ranges, stats) -> dict:
    """Fold the run-wide analyses into the legs and return the course verdict:
    a near planner cap hit fails the course, a standstill over STANDSTILL_S fails
    the leg it fell in, unprovable standstills (idle_ok False) fail the course."""
    by_leg, outside = standstills(idle_rows, tick_ranges)
    for name, leg in legs.items():
        leg.update(stats.get(name, {}))
        if name in by_leg:
            worst = max(r["unjustified_s"] for r in by_leg[name])
            leg["standstill_s"] = worst
            leg["passed"] = False
    result = verdict(legs, placed)
    cap_text, cap_lines = caps
    near = cap_text.startswith("VIOLATION")
    result.update(
        search_caps=cap_text,
        cap_hits=cap_lines,
        standstill_outside_legs=len(outside),
        standstill_provable=idle_ok,
    )
    result["passed"] = result["passed"] and not near and not outside and idle_ok
    return result
