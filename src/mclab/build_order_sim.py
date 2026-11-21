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


def main(argv=None) -> int:
    from mclab.companion_jobs import load_vanilla_pack

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("build", type=Path)
    parser.add_argument("--rotation", type=int, default=0)
    parser.add_argument("--stances", type=Path, help="write the stance list here")
    args = parser.parse_args(argv)
    build = json.loads(args.build.read_text())
    template = bp.Template.from_build(build, (0, 1, 0), args.rotation)
    result = simulate(template, load_vanilla_pack().blocks)
    print(json.dumps(result.summary()))
    if args.stances:
        args.stances.write_text(json.dumps(result.stances))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
