"""Player-style dig-down for stone (companion track): a 1-wide staircase.

Each step breaks the cells ahead from one above head height down to one below the
feet (4 high, so the planner can climb back), then walks
onto the new step with the body planner (the cells were just cleared and observed, so
they are known). Once the cell ahead at feet height is stone (or another pickaxe
block) it tunnels level and keeps mining until enough of ``item`` is held. The way
back is the staircase itself. Never digs straight down, into unknown cells, a missing
floor, or a cell with lava on any face. Gravel and sand are dug like stone: what falls
into a dug cell is broken again until the cell stays empty.
"""

from __future__ import annotations

from mclab import build_plan as bp

Pos = tuple[int, int, int]

DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
MAX_DESCENT = 16
MAX_CLIMB = 128  # escape staircase: deep caves sit ~120 blocks under the surface
SKY_CHECK = 8  # the escape ends once this many cells over the head are known air
MAX_TUNNEL = 24
DROP_WAIT_TICKS = 20
GRAVEL_CAP = 8  # re-breaks of a dug cell that gravel or sand keeps falling into
FALL_WAIT_TICKS = 10  # for what is above a broken cell to land in it
LAVA = "minecraft:lava"
WATER = "minecraft:water"
WATERLOGGED = frozenset(
    "minecraft:" + n for n in ("seagrass", "tall_seagrass", "kelp", "kelp_plant")
)
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
        self.last_dir: tuple[int, int] | None = None  # direction of the last dug step

    def _states(self, cells) -> list[str | None]:
        return [self.body.world(*c) for c in cells]

    def _blocked(self, cells: list[Pos]) -> str | None:
        """Unknown or lava among ``cells`` (the cells to dig, floor and ceiling)."""
        for state in self._states(cells):
            if state is None:
                return "unknown"
            if _ident(state) == LAVA:
                return "lava"
        return None

    def _wet(self, cells) -> bool:
        """Any of ``cells`` is water or waterlogged (as gather_runner._sealed)."""
        for state in self._states(cells):
            if state is None:
                continue
            ident, props = bp.parse_state(state)
            if (
                ident in bp.FLUIDS
                or ident in WATERLOGGED
                or props.get("waterlogged") == "true"
            ):
                return True
        return False

    def _check(self, new: Pos, dig: list[Pos]) -> str | None:
        x, y, z = new
        floor = (x, y - 1, z)
        above = (x, y + len(dig), z)  # the cell over the top dug cell
        if why := self._blocked([*dig, floor, above]):
            return why
        if _ident(self.body.world(*above)) in bp.FLUIDS:
            return "water"
        if any(_ident(s) in WATERLOGGED for s in self._states(dig)):
            return "water"
        if _ident(self.body.world(*floor)) in bp.AIR:
            return "no_floor"
        # A water stance cannot be walked onto (the planner does not swim): run
        # 20261114T205022516064Z dug a stair into a water pocket, then walk_failed.
        if {_ident(self.body.world(*c)) for c in (new, floor)} & bp.FLUIDS:
            return "water"
        # Every face of every dug cell: a dug block next to liquid lets it flow in
        # (the surface-mining rule, gather_runner._sealed). Unknown faces stop too.
        faces = {
            (cx + fx, cy + fy, cz + fz) for cx, cy, cz in dig for fx, fy, fz in FACES
        } - set(dig)
        if why := self._blocked(sorted(faces)):
            return why
        # Run 20261116T134626528028Z: a stance beside an aquifer flooded the stair.
        return "water" if self._wet(sorted(faces)) else None

    def _step_cells(self, feet: Pos, d, tunnel: bool):
        x, y, z = feet
        nx, nz = x + d[0], z + d[1]
        if tunnel:
            return (nx, y, nz), [(nx, y + 1, nz), (nx, y, nz)]
        # 4 high: the route planner climbs a step only with y+3 clear over the lower
        # one (RoutePlanner ascend). Live run 20261102T020413428498Z: 3-high stairs
        # were walked down but never back up.
        return (nx, y - 1, nz), [
            (nx, y + 2, nz),
            (nx, y + 1, nz),
            (nx, y, nz),
            (nx, y - 1, nz),
        ]

    def _dig_cells(self, dig: list[Pos], drops_optional: bool = False) -> bool:
        """Break every cell of ``dig`` until all stay empty. Gravel or sand that falls
        into a cell dug earlier is found by rescanning the whole list (run
        20261116T135356630645Z: it fell behind the break, and the next break had no line of
        sight). Each cell is broken at most GRAVEL_CAP times."""
        body = self.body
        tries: dict[Pos, int] = {}
        failed = ""
        while True:
            cell = next(
                (
                    c
                    for c in dig
                    if _ident(body.world(*c)) not in bp.AIR | bp.REPLACEABLE
                ),
                None,
            )
            if cell is None:
                return True
            tries[cell] = tries.get(cell, 0) + 1
            if tries[cell] > GRAVEL_CAP:
                self.reason = failed or "gravel_cap"
                return False
            result = (
                body.break_block(cell, drops_optional=True)
                if drops_optional
                else body.break_block(cell)
            )
            if not result.ok:
                failed = f"break_failed:{result.reason}"
                if result.reason != "no_line_of_sight":
                    self.reason = failed
                    return False
                # something fell in between: let it land, then look at every cell again
                if settle := getattr(body, "settle", None):
                    settle(dig)
                body.wait_ticks(FALL_WAIT_TICKS)
                continue
            failed = ""
            self.broken += 1
            # gravel or sand over the cell falls into it: wait, look again
            if _ident(body.world(cell[0], cell[1] + 1, cell[2])) in FALLING:
                if settle := getattr(body, "settle", None):
                    settle([cell])
                body.wait_ticks(FALL_WAIT_TICKS)

    def _toward(self, target: tuple[int, int] | None) -> list[tuple[int, int]]:
        """DIRS, those pointing at the horizontal ``target`` (x, z) first; ties keep
        the fixed order."""
        if target is None:
            return list(DIRS)
        x, _, z = self.body.position()
        dx, dz = target[0] - x, target[1] - z
        return sorted(DIRS, key=lambda c: -(c[0] * dx + c[1] * dz))

    def _pick_dir(self, feet: Pos):
        why = "no_direction"
        for d in DIRS:
            new, dig = self._step_cells(feet, d, False)
            problem = self._check(new, dig)
            if problem is None:
                return d
            why = problem
        self.reason = why
        return None

    def _collect(self, item: str, near: Pos, target: int) -> None:
        """A drop not picked up within about DROP_WAIT_TICKS: step onto its cell."""
        drops = [e for e in self.body.entities() if e.get("item") == item]
        if not drops:
            return
        self.body.wait_ticks(DROP_WAIT_TICKS)
        self.ex.collect(
            {item}, near, lambda: self.body.inventory().get(item, 0) >= target
        )
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
        while True:
            if body.inventory().get(item, 0) >= target:
                self.reason = "done"
                break
            feet = body.position()
            if direction is None:
                direction = self._pick_dir(feet)
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
            problem = self._check(new, dig)
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
            if self._wet([*dig, (new[0], new[1] + len(dig), new[2])]):
                self.reason = "water"  # the dug stair flooded: do not walk in
                break
            if not body.walk_to(new, 0.8):
                self.reason = "walk_failed"
                break
            self._collect(item, new, target)
        return self.broken > 0

    def step(self, d, tunnel: bool) -> str | None:
        """One staircase (or, ``tunnel``, level) step toward ``d``: check, dig,
        walk onto it. None on success, else why not."""
        new, dig = self._step_cells(self.body.position(), d, tunnel)
        if why := self._check(new, dig):
            return why
        if not self._dig_cells(dig):
            return self.reason
        if settle := getattr(self.body, "settle", None):
            settle(dig)
        self.last_dir = d
        self.tunnelled += tunnel
        self.descended += not tunnel
        if self._wet([*dig, (new[0], new[1] + len(dig), new[2])]):
            return "water"  # the dug stair flooded: do not walk in
        return None if self.body.walk_to(new, 0.8) else "walk_failed"

    def hard(self, why: str) -> bool:
        return why == "walk_failed" or why.startswith("break_failed")

    def descend(self, y: int, toward: tuple[int, int] | None = None) -> str | None:
        """Staircase down until the feet are at ``y`` or lower, heading for the
        horizontal ``toward`` (x, z) when given; None, else why not."""
        body = self.body
        d, why = None, "no_direction"
        while body.position()[1] > y:
            # re-aimed every step: a deep target close by is reached by a switchback,
            # not overshot by one long straight stair
            order = self._toward(toward)
            if toward is None and d:
                order = [d] + [c for c in order if c != d]
            back = (-self.last_dir[0], -self.last_dir[1]) if self.last_dir else None
            for cand in order:
                if cand == back:  # onto the stair just dug, which may be flooded
                    continue
                why = self.step(cand, False)
                if why is None:
                    d = cand
                    break
                if self.hard(why):
                    return why
            else:
                return why
        return None

    def open_sky(self) -> bool:
        x, y, z = self.body.position()
        return all(
            _ident(self.body.world(x, y + dy, z)) in bp.AIR
            for dy in range(2, 2 + SKY_CHECK)
        )

    def _climb_check(self, feet: Pos, d) -> tuple[list[Pos], str | None]:
        x, y, z = feet
        nx, nz = x + d[0], z + d[1]
        # headroom for the jump, then the two cells the body steps up into
        dig = [(x, y + 2, z), (nx, y + 2, nz), (nx, y + 1, nz)]
        if why := self._blocked([*dig, (nx, y, nz)]):
            return dig, why
        if _ident(self.body.world(nx, y, nz)) in bp.AIR:
            return dig, "no_floor"
        faces = {
            (cx + fx, cy + fy, cz + fz) for cx, cy, cz in dig for fx, fy, fz in FACES
        } - set(dig)
        return dig, self._blocked(sorted(faces))

    def climb(self) -> bool:
        """Escape: a staircase up, by hand if need be (drops are not needed), until
        open sky is over the head. For a body that is buried and cannot reach its
        target, e.g. all tools broke underground and new ones are made on the
        surface. True once under open sky."""
        body, direction = self.body, None
        for _ in range(MAX_CLIMB):
            if self.open_sky():
                self.reason = "open_sky"
                return True
            feet = body.position()
            if _ident(body.world(feet[0], feet[1] + 3, feet[2])) in FALLING:
                # the one gravel rule: never open the cell under gravel that sits over
                # the body's own head. Tunnel one step aside instead: what falls
                # there lands in front of the body and is dug like any other cell.
                if any(self.step(d, True) is None for d in DIRS):
                    continue
                self.reason = "gravel_overhead"
                return False
            order = ([direction] if direction else []) + [
                d for d in DIRS if d != direction
            ]
            for d in order:
                dig, why = self._climb_check(feet, d)
                if why is None:
                    direction = d
                    break
            else:
                self.reason = why or "no_direction"
                return False
            if not self._dig_cells(dig, drops_optional=True):
                return False
            if settle := getattr(body, "settle", None):
                settle(dig)
            new = (feet[0] + direction[0], feet[1] + 1, feet[2] + direction[1])
            if not body.walk_to(new, 0.8):
                self.reason = "walk_failed"
                return False
        self.reason = "climb_cap"
        return False
