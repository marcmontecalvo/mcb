"""Player-style dig-down for stone (companion track): a 1-wide staircase.

Each step breaks the cells ahead from one above head height down to one below the
feet (4 high, so the planner can climb back), then walks
onto the new step with the body planner (the cells were just cleared and observed, so
they are known). Once the cell ahead at feet height is stone (or another pickaxe
block) it tunnels level and keeps mining until enough of ``item`` is held. The way
back is the staircase itself. Never digs straight down, into unknown cells, or past
water, lava, gravel above the head, a missing floor or (underground) a cave opening.
"""

from __future__ import annotations

from mclab import build_plan as bp

Pos = tuple[int, int, int]

DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
MAX_DESCENT = 16
MAX_TUNNEL = 24
DROP_WAIT_TICKS = 20
CAVE_DEPTH = 4  # sides are only checked for air this far below the start
# (a neighbouring column one or two blocks lower is not a cave)
FALLING = frozenset("minecraft:" + n for n in ("gravel", "sand", "red_sand"))
STONE_LIKE = ("stone", "cobblestone", "deepslate", "andesite", "diorite", "granite")


def _ident(state: str | None) -> str | None:
    return bp.parse_state(state)[0] if state else None


def stone_like(state: str | None) -> bool:
    ident = _ident(state)
    if ident is None:
        return False
    name = ident.removeprefix("minecraft:")
    return name in STONE_LIKE or name.endswith("_ore")


class Digger:
    def __init__(self, ex) -> None:
        self.ex, self.body = ex, ex.body
        self.reason = ""  # why the dig ended
        self.descended = self.tunnelled = self.broken = 0

    def _states(self, cells) -> list[str | None]:
        return [self.body.world(*c) for c in cells]

    def _blocked(self, cells: list[Pos]) -> str | None:
        """Unknown or liquid among ``cells`` (the cells to dig, floor and ceiling)."""
        for state in self._states(cells):
            if state is None:
                return "unknown"
            if _ident(state) in bp.FLUIDS:
                return "liquid"
        return None

    def _check(self, new: Pos, dig: list[Pos], d, depth: int) -> str | None:
        x, y, z = new
        floor = (x, y - 1, z)
        above = (x, y + len(dig), z)  # the cell over the top dug cell
        if why := self._blocked([*dig, floor, above]):
            return why
        if _ident(self.body.world(*floor)) in bp.AIR:
            return "no_floor"
        if _ident(self.body.world(*above)) in FALLING:
            return "gravel_above"
        # Every face of every dug cell: a dug block next to liquid lets it flow in
        # (the surface-mining rule, gather_runner._sealed). Unknown faces stop too.
        faces = {
            (cx + fx, cy + fy, cz + fz) for cx, cy, cz in dig for fx, fy, fz in FACES
        } - set(dig)
        if why := self._blocked(sorted(faces)):
            return why
        sides = [
            (x + dx, y + dy, z + dz)
            for dx, dz in DIRS
            if (dx, dz) != (-d[0], -d[1])  # the way back is open by design
            for dy in (0, 1)
        ]
        if depth >= CAVE_DEPTH and any(
            _ident(s) in bp.AIR for s in self._states(sides)
        ):
            return "cave_opening"
        return None

    def _step_cells(self, feet: Pos, d, tunnel: bool):
        x, y, z = feet
        nx, nz = x + d[0], z + d[1]
        if tunnel:
            return (nx, y, nz), [(nx, y + 1, nz), (nx, y, nz)]
        # 4 high: the route planner climbs a step only with y+3 clear over the lower
        # one (route_search dy == 1). Live run 20261102T020413428498Z: 3-high stairs
        # were walked down but never back up.
        return (nx, y - 1, nz), [
            (nx, y + 2, nz),
            (nx, y + 1, nz),
            (nx, y, nz),
            (nx, y - 1, nz),
        ]

    def _dig_cells(self, dig: list[Pos]) -> bool:
        for cell in dig:
            ident = _ident(self.body.world(*cell))
            if ident in bp.AIR or ident in bp.REPLACEABLE:
                continue
            result = self.body.break_block(cell)
            if not result.ok:
                self.reason = f"break_failed:{result.reason}"
                return False
            self.broken += 1
        return True

    def _pick_dir(self, feet: Pos, start_y: int):
        why = "no_direction"
        for d in DIRS:
            new, dig = self._step_cells(feet, d, False)
            problem = self._check(new, dig, d, start_y - new[1])
            if problem is None:
                return d
            why = problem
        self.reason = why
        return None

    def _collect(self, item: str, near: Pos) -> None:
        """A drop not picked up within about DROP_WAIT_TICKS: step onto its cell."""
        drops = [e for e in self.body.entities() if e.get("item") == item]
        if not drops:
            return
        self.body.wait_ticks(DROP_WAIT_TICKS)
        self.ex.collect({item}, near)
        if self.body.position() != near:
            self.body.walk_to(near, 0.8)  # back onto the stair

    def run(self, item: str, target: int) -> bool:
        """Dig until ``target`` of ``item`` is held; True if anything was dug."""
        body = self.body
        state = self.ex.state
        direction, tunnel = None, False
        if state.dig_tail and state.dig_dir:  # back down the earlier staircase
            if body.position() == state.dig_tail or body.walk_to(state.dig_tail, 0.8):
                direction = state.dig_dir  # tunnel is re-decided from the cell ahead
            else:
                state.dig_tail = state.dig_dir = state.dig_start_y = None
        if state.dig_start_y is None:
            state.dig_start_y = body.position()[1]
        start_y = state.dig_start_y
        while True:
            if body.inventory().get(item, 0) >= target:
                self.reason = "done"
                break
            feet = body.position()
            if direction is None:
                direction = self._pick_dir(feet, start_y)
                if direction is None:
                    break
            tunnel = tunnel or stone_like(
                body.world(feet[0] + direction[0], feet[1], feet[2] + direction[1])
            )
            if tunnel and self.tunnelled >= MAX_TUNNEL:
                self.reason = "tunnel_cap"
                break
            if not tunnel and self.descended >= MAX_DESCENT:
                self.reason = "depth_cap"
                break
            new, dig = self._step_cells(feet, direction, tunnel)
            problem = self._check(new, dig, direction, start_y - new[1])
            if problem:
                self.reason = problem
                break
            if not self._dig_cells(dig):
                break
            state.dig_tail, state.dig_dir = new, direction
            if settle := getattr(body, "settle", None):
                settle(dig)  # the map must show the cleared cells before walking
            self.tunnelled += tunnel
            self.descended += not tunnel
            if not body.walk_to(new, 0.8):
                self.reason = "walk_failed"
                break
            self._collect(item, new)
        return self.broken > 0
