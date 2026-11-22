"""Offline build-order measurement: ``build_plan.next_actions`` driven over a template
on flat ground, with a body that walks to the nearest stance in reach of each batch.

It measures the order, not the motor: walks are straight lines, any standable cell is
reachable and a stance with nothing under it (a scaffold would be needed) is allowed but
counted. Use it to compare two orders of the same template, not to predict live times.

    uv run --locked python -m mclab.build_order_sim \
        tests/fixtures/schematics/house-large.build.json
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from mclab import build_plan as bp
from mclab import place_state
from mclab import scaffolding as sc

Pos = tuple[int, int, int]
EYE = 1.62
SWING = 3  # a level change at least this tall between two stances is a swing
SEARCH = 5  # stance search half-width around a target
TOOLS = ("iron_axe", "iron_shovel", "iron_hoe", "iron_pickaxe")


class World:
    """Flat ground: solid at and below ``ground_y``, air above, plus what was set."""

    def __init__(self, ground_y: int, ground: str = "minecraft:dirt"):
        self.ground_y, self.ground, self.cells = ground_y, ground, {}

    def __call__(self, x, y, z):
        default = self.ground if y <= self.ground_y else "minecraft:air"
        return self.cells.get((x, y, z), default)

    def set(self, pos: Pos, state: str) -> None:
        self.cells[pos] = state


def _solid(world, pos, blocks) -> bool:
    state = world(*pos)
    return bp._is_solid(state, blocks) or bp.parse_state(state)[0] == sc.BLOCK


def standable(world, feet: Pos, blocks, reserved=frozenset()) -> bool:
    """Something to stand on, and the feet and head cells free (and not saved for the
    build: a body standing in a wall cell keeps the wall from going in)."""
    x, y, z = feet
    head = (x, y + 1, z)
    return (
        _solid(world, (x, y - 1, z), blocks)
        and not _solid(world, feet, blocks)
        and not _solid(world, head, blocks)
        and feet not in reserved
        and head not in reserved
    )


def in_reach(feet: Pos, cell: Pos) -> bool:
    eye = (feet[0] + 0.5, feet[1] + EYE, feet[2] + 0.5)
    centre = (cell[0] + 0.5, cell[1] + 0.5, cell[2] + 0.5)
    return math.dist(eye, centre) <= bp.WORK_REACH


def _cost(a: Pos, b: Pos) -> float:
    """Walking cost between stances: horizontal distance plus every level climbed or
    dropped (a climb is a scaffold or a stair, never free)."""
    return math.hypot(a[0] - b[0], a[2] - b[2]) + abs(a[1] - b[1])


def measure(stances: list[Pos]) -> dict:
    steps = list(zip(stances, stances[1:], strict=False))
    return {
        "walks": len(steps),
        "walk_distance": round(sum(_cost(a, b) for a, b in steps), 1),
        "climb": sum(max(0, b[1] - a[1]) for a, b in steps),
        "descent": sum(max(0, a[1] - b[1]) for a, b in steps),
        "swings": sum(abs(b[1] - a[1]) >= SWING for a, b in steps),
    }


@dataclass
class Result:
    cells: int
    placed: int
    missing: int
    stances: list[Pos]
    walks: int = 0
    walk_distance: float = 0.0
    climb: int = 0
    descent: int = 0
    swings: int = 0
    floating_stances: int = 0  # stances with nothing under them (a scaffold needed)
    scaffolds: int = 0  # Scaffold acts the planner asked for
    props: int = 0  # temporary blocks placed to click a state against (runner sim)
    batches: int = 0
    stop: str = ""
    placements: list[Pos] = field(default_factory=list)

    def summary(self) -> dict:
        keys = (
            "cells placed missing walks walk_distance climb descent swings "
            "floating_stances scaffolds batches stop"
        ).split()
        return {k: getattr(self, k) for k in keys}


def _inventory(template: bp.Template, blocks) -> dict[str, int]:
    inv = {f"minecraft:{t}": 1 for t in TOOLS}
    for state in template.cells.values():
        got = bp.item_for_state(state, blocks)
        if got:
            inv[got[0]] = 100_000
        ident = bp.parse_state(state)[0]
        if ident.startswith("minecraft:potted_"):  # the plant goes in by a Use
            inv["minecraft:" + ident.removeprefix("minecraft:potted_")] = 100_000
    for item in (*bp.SCAFFOLD_ITEMS, bp.FILL_ITEM):
        inv[item] = 100_000
    return inv


def _apply(world: World, act, inv) -> None:
    if isinstance(act, bp.Break):
        world.set(act.pos, "minecraft:air")
    elif isinstance(act, bp.Use):
        cur = world(*act.pos)
        if cur == bp.POT and act.item:
            world.set(
                act.pos, "minecraft:potted_" + act.item.removeprefix("minecraft:")
            )
        elif "open=" in cur:
            flip = cur.replace("open=false", "open=TRUE").replace(
                "open=true", "open=false"
            )
            world.set(act.pos, flip.replace("open=TRUE", "open=true"))
    elif isinstance(act, bp.Scaffold):
        world.set(act.pos, act.item)
    else:
        ident, props = bp.parse_state(act.state)
        if props.get("part") == "foot":
            head = bp._add(act.pos, bp._VEC[props["facing"]])
            world.set(head, act.state.replace("part=foot", "part=head"))
        world.set(act.pos, act.state)
        if props.get("half") == "lower":
            up = bp._add(act.pos, (0, 1, 0))
            world.set(up, act.state.replace("half=lower", "half=upper"))
    if getattr(act, "item", None) in inv:
        inv[act.item] -= getattr(act, "count", 1)


def _stance(world, target: Pos, body: Pos, blocks, reserved, avoid) -> tuple[Pos, bool]:
    """The cheapest stance from ``body`` that reaches ``target``; a stance with nothing
    under it (flagged True) only when no supported one exists."""
    best, floating = None, None
    tx, ty, tz = target
    for x in range(tx - SEARCH, tx + SEARCH + 1):
        for z in range(tz - SEARCH, tz + SEARCH + 1):
            for y in range(ty - SEARCH, ty + SEARCH + 1):
                feet = (x, y, z)
                if feet in avoid or not in_reach(feet, target):
                    continue
                if feet == target or (x, y + 1, z) == target:
                    continue
                key = (_cost(body, feet), feet)
                if standable(world, feet, blocks, reserved):
                    best = min(best or key, key)
                elif (
                    feet not in reserved
                    and (x, y + 1, z) not in reserved
                    and not _solid(world, feet, blocks)
                    and not _solid(world, (x, y + 1, z), blocks)
                    and y > world.ground_y
                ):
                    floating = min(floating or key, key)
    if best is not None:
        return best[1], False
    if floating is not None:
        return floating[1], True
    raise RuntimeError(f"no stance for {target}")


def simulate(
    template: bp.Template, blocks: dict, max_batches: int = 20_000, **plan
) -> Result:
    """Build ``template`` on flat ground from empty, one ``next_actions`` batch at a
    time. ``plan`` is passed on to ``next_actions`` (to compare orders)."""
    world = World(ground_y=template.origin[1] - 1)
    inv = _inventory(template, blocks)
    scaffolds: set = set()
    skipped: set = set()
    want = {p for p, s in template.cells.items() if bp.parse_state(s)[0] not in bp.AIR}
    body = (template.origin[0] - 3, template.origin[1], template.origin[2] - 3)
    result = Result(cells=len(want), placed=0, missing=len(want), stances=[body])
    tries: Counter = Counter()
    last = None
    for _ in range(max_batches):
        acts = bp.next_actions(
            template,
            world,
            blocks,
            inv,
            limit=32,
            scaffolds=scaffolds,
            skipped=skipped,
            near=body,
            focus=last,
            **plan,
        )
        if not acts:
            result.stop = "done"
            break
        if isinstance(acts[0], bp.NeedMaterials):
            short = sorted(i for i, n in acts[0].items.items() if inv.get(i, 0) < n)
            result.stop = f"need_materials {short}"
            break
        result.batches += 1
        target = acts[0].pos
        reserved = frozenset(p for p in want if world(*p) != template.cells[p])
        if not in_reach(body, target) or not standable(world, body, blocks, reserved):
            avoid = {body} if tries[target] else set()
            body, floating = _stance(world, target, body, blocks, reserved, avoid)
            result.stances.append(body)
            result.floating_stances += floating
        done = 0
        for act in acts:
            if not in_reach(body, act.pos) or act.pos in (
                body,
                bp._add(body, (0, 1, 0)),
            ):
                continue
            _apply(world, act, inv)
            done += 1
            if isinstance(act, bp.Scaffold):
                result.scaffolds += 1
            if isinstance(act, bp.Place):
                last = act.pos
                result.placements.append(act.pos)
        if not done:
            tries[target] += 1
            if tries[target] >= 3:
                skipped.add(target)
    else:
        result.stop = "max_batches"
    counts = bp.progress(template, world)
    result.placed, result.missing = counts["correct"], counts["missing"]
    for key, value in measure(result.stances).items():
        setattr(result, key, value)
    return result


class SimBody:
    """A body for ``build_runner.run_build`` on a ``World``: every block in hand,
    stances are teleports ranked the way ``CompanionBody.stances`` ranks them (walking
    cost less three per upcoming sweep cell covered, ``stance_cover``), and the top
    of a scaffolding column is a stance (the motor climbs it). A state is made only
    from a solid face whose click gives it (place_state: a log's axis, a slab's
    half, a hopper's facing). It measures the runner's own order (batches, sweep,
    towers, props), not the motor. ``keep``: the template's block cells; any other
    block placed but scaffolding counts as a prop."""

    def __init__(
        self,
        world: World,
        blocks: dict,
        inventory: dict,
        pos: Pos,
        keep: frozenset = frozenset(),
    ):
        self.w, self.blocks, self.inv, self.pos = world, blocks, inventory, pos
        self.keep = keep
        self.stance_cover: dict = {}  # set by the runner's sweep
        self.stances_walked: list[Pos] = [pos]
        self.scaffold_placed = 0
        self.props_placed = 0
        self.violations: list[str] = []

    # --- BuildBody ---------------------------------------------------------------
    def world(self, x, y, z):
        return self.w(x, y, z)

    def inventory(self):
        return dict(self.inv)

    def position(self):
        return self.pos

    def stances(self, target: Pos, reach: float, avoid=()):
        tx, ty, tz = target
        spots = (
            (tx + dx, ty + dy, tz + dz)
            for dx in range(-SEARCH, SEARCH + 1)
            for dz in range(-SEARCH, SEARCH + 1)
            for dy in range(-SEARCH, 2)
        )
        found = [s for s in spots if s not in avoid and self._stance_ok(s, target)]
        here = self.pos
        return sorted(
            found,
            key=lambda s: (_cost(here, s) - 3.0 * self._covered(s), _cost(here, s), s),
        )

    def goto_stance(self, target: Pos, reach: float, avoid=()) -> bool:
        if self.pos not in avoid and self._stance_ok(self.pos, target):
            return True
        found = self.stances(target, reach, avoid)
        if not found:
            return False
        self.pos = found[0]
        self.stances_walked.append(self.pos)
        return True

    def place_block(self, pos, state, item):
        from mclab.build_runner import Result

        if not in_reach(self.pos, pos):
            return Result(False, "out_of_reach")
        if pos in (self.pos, bp._add(self.pos, (0, 1, 0))):
            return Result(False, "occupied")
        if bp.parse_state(self.w(*pos))[0] not in bp.REPLACEABLE:
            return Result(False, "occupied")
        ident, props = bp.parse_state(state)
        if ident == sc.BLOCK:
            if not sc.can_place(pos, self._scaffolds(), self._sturdy_below):
                return Result(False, "unstable")
            self.scaffold_placed += 1
        elif not any(
            _solid(self.w, bp._add(pos, v), self.blocks) for v in bp._NEIGHBOURS
        ):
            self.violations.append(f"no neighbour: {pos} {state}")
            return Result(False, "no_support_face")
        elif not self._makes(pos, state):
            return Result(False, "state_unreachable")
        elif self.keep and pos not in self.keep:
            self.props_placed += 1
        _apply(self.w, bp.Place(pos, state, item, 1), {})
        return Result(True, data={"placed": state})

    def use_block(self, pos, item=None):
        from mclab.build_runner import Result

        if not in_reach(self.pos, pos):
            return Result(False, "out_of_reach")
        _apply(self.w, bp.Use(pos, item), {})
        return Result(True)

    def break_block(self, pos, tool=None):
        from mclab.build_runner import Result

        if not in_reach(self.pos, pos):
            return Result(False, "out_of_reach")
        cur = self.w(*pos)
        if bp.parse_state(cur)[0] in bp.AIR:
            return Result(False, "no_block")
        scaffolds = self._scaffolds()
        self.w.set(pos, "minecraft:air")
        if pos in scaffolds:  # what hung from it drops with it
            stand = sc.survivors(scaffolds, self._sturdy_below, [pos])
            for p in scaffolds - stand - {pos}:
                self.w.set(p, "minecraft:air")
        return Result(True, data={"broken": cur})

    # --- helpers -------------------------------------------------------------------
    def _makes(self, pos: Pos, state: str) -> bool:
        """Does a click on some solid neighbour's face give ``state``?"""
        for direction, v in bp._VEC.items():
            n = bp._add(pos, v)
            if not (
                _solid(self.w, n, self.blocks)
                or bp.parse_state(self.w(*n))[0] == sc.BLOCK
            ):
                continue
            face = place_state.OPPOSITE[direction]
            fracs = (0.5,) if place_state.AXIS[face] == "y" else (0.25, 0.75)
            if any(place_state.click_gives(state, face, f) for f in fracs):
                return True
        return False

    def _scaffolds(self) -> set[Pos]:
        return {p for p, s in self.w.cells.items() if bp.parse_state(s)[0] == sc.BLOCK}

    def _sturdy_below(self, p: Pos) -> bool:
        below = self.w(p[0], p[1] - 1, p[2])
        return bp._is_solid(below, self.blocks) and bp.parse_state(below)[0] != sc.BLOCK

    def _covered(self, s: Pos) -> int:
        body = (s, (s[0], s[1] + 1, s[2]))
        return sum(in_reach(s, p) and p not in body for p in self.stance_cover)

    def _stance_ok(self, s: Pos, target: Pos) -> bool:
        head = (s[0], s[1] + 1, s[2])
        if target in (s, head) or not in_reach(s, target):
            return False
        if bp.parse_state(self.w(*s))[0] == sc.BLOCK:  # on top of a column
            return bp.parse_state(self.w(*head))[0] in bp.AIR
        return standable(self.w, s, self.blocks)


def simulate_runner(
    template: bp.Template, blocks: dict, max_steps: int = 20_000, **build
) -> Result:
    """Build ``template`` on flat ground through ``build_runner.run_build`` and a
    ``SimBody``: the runner's real batches, sweep and towers. ``build`` is passed on
    to ``run_build`` (``order`` to compare orders)."""
    from mclab import build_runner as br

    world = World(ground_y=template.origin[1] - 1)
    start = (template.origin[0] - 3, template.origin[1], template.origin[2] - 3)
    want = {p for p, s in template.cells.items() if bp.parse_state(s)[0] not in bp.AIR}
    body = SimBody(
        world, blocks, _inventory(template, blocks), start, keep=frozenset(want)
    )
    report = br.run_build(
        template,
        body,
        blocks,
        max_steps=max_steps,
        on_need_materials=lambda items: True,
        site_margin=None,
        stall_seconds=None,
        **build,
    )
    counts = bp.progress(template, world)
    result = Result(
        cells=len(want),
        placed=counts["correct"],
        missing=counts["missing"],
        stances=body.stances_walked,
        scaffolds=body.scaffold_placed,
        props=body.props_placed,
        batches=report.steps,
        stop=report.stop,
        placements=list(report.placed),
    )
    for key, value in measure(result.stances).items():
        setattr(result, key, value)
    return result


def main(argv=None) -> int:
    from mclab.companion_jobs import load_vanilla_pack

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("build", type=Path)
    parser.add_argument("--rotation", type=int, default=0)
    parser.add_argument("--stances", type=Path, help="write the stance list here")
    parser.add_argument("--order", choices=bp.ORDERS, default=bp.ORDERS[0])
    args = parser.parse_args(argv)
    build = json.loads(args.build.read_text())
    template = bp.Template.from_build(build, (0, 1, 0), args.rotation)
    result = simulate(template, load_vanilla_pack().blocks, order=args.order)
    print(json.dumps(result.summary()))
    if args.stances:
        args.stances.write_text(json.dumps(result.stances))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
