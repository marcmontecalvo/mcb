# ruff: noqa: E501
"""Strip mining for gathers without ore vision: mine like a player.

Pick the ore's best height from the knowledge pack, take a staircase down to it
(``Digger.descend``), tunnel level 2 high by 1 wide to the nearest chunk border, then
run a serpentine along it: ``strip_leg`` blocks, a ``strip_turn`` connector, a leg back
parallel, and so on up to ``strip_max_legs``. After every step the cells touching the
tunnel are scanned (the bot only knows ore with an open face it has seen) and each
wanted ore is mined, with its vein. Why it ended is ``Strip.stop``.

ponytail: no torches, no mob handling. Steps walk with body.walk_to (one cleared cell each).
"""

from __future__ import annotations

import math
from itertools import count

from mclab import build_plan as bp
from mclab.build_runner import REACH, Result
from mclab.gather_dig import FACES, Digger
from mclab.gather_options import SENSE_DY, pick_tool, wanted_ores
from mclab.gather_runner import _cell, _sealed
from mclab.knowledge import best_y

CHUNK = 16
EYE = 1.62
MIN_BUILD_Y = -64
BEDROCK_MARGIN = 5  # lowest strip height: the build floor + this (diamond lands at -59)
SURFACE_MARGIN = 4  # the search height is this far under the surface
DEPTH_SLACK = 8  # a descent that stopped this far above the target gives up
VEIN_CAP = 24
FILL = ("minecraft:cobblestone", "minecraft:dirt")  # floor patches, first held
# these end the whole strip; any other leg or connector failure ends only that leg
TERMINAL = frozenset({"done", "missing_tool", "stalled", "search_radius", "inventory_full"})
REASON = {"done": "count_met", "ok": "leg_done", "search_radius": "radius"}


def surface_y(body, x: int, y: int, z: int) -> int | None:
    """Top solid block of the column near the bot (None: none sensed)."""
    for yy in range(y + SENSE_DY, y - SENSE_DY - 1, -1):
        state = body.world(x, yy, z)
        if state and bp.parse_state(state)[0] not in bp.AIR | bp.FLUIDS:
            return yy
    return None


def border_step(v: int) -> int:
    """Signed distance from ``v`` to the nearest multiple of CHUNK."""
    r = v % CHUNK
    return -r if r <= CHUNK // 2 else CHUNK - r


class Strip:
    def __init__(self, ex, t, target: int, ty: int | None = None) -> None:
        self.ex, self.body, self.t, self.target = ex, ex.body, t, target
        self.opts, self.digger = ex.opts, Digger(ex)
        self.broken, self.stop, self.origin = 0, "", (0, 0)
        self.ty = ty  # target height; kept by a rerun from underground, where the surface is unsensed

    def done(self) -> bool:
        return self.body.inventory().get(self.t.item, 0) >= self.target

    def run(self) -> Result | None:
        """None: not applicable here (no ore height known, or the descent fell short)."""
        ex, body = self.ex, self.body
        x, y, z = _cell(body.position())
        self.origin = (x, z)
        top = surface_y(body, x, y, z)
        ty = self.ty
        if ty is None:
            ty = best_y(ex.k, self.t.block, (y if top is None else top) - SURFACE_MARGIN)
        if ty is None:
            ex.last_dig = "no_target_height"
            ex.note("strip_skipped", why=ex.last_dig, at=(x, y, z))
            return None
        ty = self.ty = max(ty, MIN_BUILD_Y + BEDROCK_MARGIN)
        # ore touching the staircase is mined on the way (run 20261116T135356630645Z
        # walked past four iron cells)
        why = self.digger.descend(ty, after=lambda: self.mine() or ("done" if self.done() else None))
        if why in TERMINAL:
            self.stop = why
            data = {"broken": self.broken, "stop": why, "tunnelled": self.digger.tunnelled}
            return Result(self.broken > 0, why if why == "missing_tool" else None, data)
        if body.position()[1] > ty + DEPTH_SLACK:
            ex.last_dig = why or "short_descent"
            ex.note("strip_skipped", why=ex.last_dig, target_y=ty, at=_cell(body.position()))
            return None
        self.stop = self.serpentine()
        data = {"broken": self.broken, "stop": self.stop, "tunnelled": self.digger.tunnelled}
        if self.stop == "missing_tool":
            return Result(False, "missing_tool", data)
        return Result(self.broken > 0, None, data)

    def serpentine(self) -> str:
        x, _, z = _cell(self.body.position())
        dx, dz = border_step(x), border_step(z)
        on_x = abs(dx) <= abs(dz)  # the border line to walk along is x = const
        along, across = ((0, 1), (1, 0)) if on_x else ((1, 0), (0, 1))
        far = dx if on_x else dz
        if far:
            step = (1 if far > 0 else -1) * across[0], (1 if far > 0 else -1) * across[1]
            why = self.tunnel(step, abs(far))
            if why in TERMINAL:
                return why
            if why != "ok":  # the way to the border is only a convenience: legs start here
                self.ex.note("strip_approach_failed", why=why, at=_cell(self.body.position()))
        sign, legs = 1, self.opts.strip_max_legs
        for i in count():
            if legs is not None and i >= legs:
                return "legs_done"
            why = self.leg(i, (along[0] * sign, along[1] * sign), self.opts.strip_leg)
            if why in TERMINAL:
                return why
            if legs is not None and i + 1 >= legs:
                return "legs_done"
            why = self.leg(f"{i}c", across, self.opts.strip_turn)
            if why not in TERMINAL and why != "ok":  # blocked: turn the other way
                across = (-across[0], -across[1])
                why = self.leg(f"{i}c", across, self.opts.strip_turn)
            if why in TERMINAL or why != "ok":
                return why
            sign = -sign
        return "unreachable"

    def leg(self, name, d, n: int) -> str:
        """One tunnel run, logged at start and end with why it ended."""
        self.ex.note("strip_leg_start", leg=name, dir=d, n=n, at=_cell(self.body.position()))
        why = self.tunnel(d, n)
        end = REASON.get(why) or ("hazard:" + why if why in ("lava", "unknown") else why)
        self.ex.note("strip_leg_end", leg=name, why=end, at=_cell(self.body.position()))
        return why

    def tunnel(self, d: tuple[int, int], n: int) -> str:
        for _ in range(n):
            if self.done():
                return "done"
            if not self.ex.harvests(self.t):
                return "missing_tool"
            if self.ex.stalled():
                return "stalled"
            why = self.digger.step(d, True)
            if why == "no_floor" and self.bridge(d):  # a player fills the hole and goes on
                why = self.digger.step(d, True)
            if why:
                return why
            x, _, z = _cell(self.body.position())
            r = self.opts.search_radius
            if r is not None and max(abs(x - self.origin[0]), abs(z - self.origin[1])) > r:
                return "search_radius"
            if why := self.mine():
                return why
        return "ok"

    def bridge(self, d: tuple[int, int]) -> bool:
        """Place a carried block in the missing floor cell ahead (a cave, or ore mined
        out of the floor). Runs 20261116T124031122360Z and 20261114T203457845925Z ended
        legs at 17 and 37 of 64 blocks on ``no_floor``."""
        body = self.body
        new, _ = self.digger._step_cells(body.position(), d, True)
        floor = (new[0], new[1] - 1, new[2])
        inv = body.inventory()
        item = next((i for i in FILL if inv.get(i, 0) > 0), None)
        if item is None:
            return False
        # From where the body stands when the cell is in reach: the goto_stance search
        # found none for a floor cell beside the body (run 20261116T135356630645Z).
        x, y, z = body.position()
        if math.dist((x + 0.5, y + EYE, z + 0.5), (floor[0] + 0.5, floor[1] + 0.5, floor[2] + 0.5)) <= REACH:
            return body.place_block(floor, item, item).ok  # no stance probes for a reachable cell
        return self.ex.reach_and_do(floor, lambda: body.place_block(floor, item, item)).ok

    def mine(self) -> str | None:
        """Mine the wanted ores touching the tunnel here, and their veins."""
        body, ex = self.body, self.ex
        x, y, z = here = _cell(body.position())
        wanted = set(wanted_ores(self.t, body.inventory(), ex.k, self.opts, ex.plan_ores))

        def ident(p):
            return bp.parse_state(body.world(*p) or "minecraft:air")[0]

        seeds = {(x + fx, y + dy + fy, z + fz) for dy in (0, 1) for fx, fy, fz in FACES}
        queue = [(p, None) for p in seeds if ident(p) in wanted]
        seen: set = set()
        refused: list = []  # cells with no stance yet: a break elsewhere can open one
        while queue and len(seen) < VEIN_CAP:
            p, kind = queue.pop()
            if p in seen or ident(p) not in ({kind} if kind else wanted):
                continue
            seen.add(p)
            if self.done():
                return "done"
            if not _sealed(body, p):
                continue
            block = ident(p)
            tool = pick_tool(ex.k, body.inventory(), block, self.opts.tool_choice)
            res = ex.reach_and_do(p, lambda p=p, tl=tool: body.break_block(p, tl))
            if not res.ok:
                if res.reason in ("wrong_tool", "missing_tool") and block == self.t.block:
                    return "missing_tool"
                refused.append((p, kind))
                continue
            # Run 20261116T124031122360Z: a vein cell tried before the cell below it
            # had no stance, was marked seen, and was never retried once that cell was
            # mined and the body stood in the hole, so the cells next to it stayed.
            for q, k in refused:
                seen.discard(q)
                queue.append((q, k))
            refused.clear()
            self.broken += block == self.t.block
            ex.collect(None, p, self.done)
            if any(e.get("item") and not body.can_hold(e["item"], ex.k.items) for e in body.entities()):
                return "inventory_full"
            queue.extend(((p[0] + a, p[1] + b, p[2] + c), block) for a, b, c in FACES)
        if _cell(body.position()) != here:
            body.walk_to(here, 0.8)
        return None
