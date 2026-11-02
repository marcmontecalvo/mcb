"""Checkpoints of a build in progress, to fast-forward a test to a known point.

``snapshot`` records, from a live body, every template cell it has seen (air included,
so the cells the run cleared are cleared again) and the inventory. ``restore`` sets a
fresh copy of the same seed's world up from it with the lab ``kit`` op (64 blocks a
call, no neighbour updates, so order does not matter), after which the build can run
alone: ``build_job(build_only=True)``. The body is not moved: the build walks to its
own stances. Not recorded: gathering results (trees cut, stations, containers) and
anything outside the template.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from mclab import build_plan as bp

VERSION = 1
KIT_PLACE_MAX = 64  # LabKit.MAX_PLACE
Pos = tuple[int, int, int]


def snapshot(template: bp.Template, body, meta: dict, stop: str = "") -> dict:
    """``meta``: seed, start, build (path), origin, rotation, mirror - what a resume
    needs to rebuild the template."""
    cells = []
    for pos in sorted(template.cells):
        state = body.world(*pos)
        if state is not None:
            cells.append([*pos, state])
    return {
        "version": VERSION,
        **meta,
        "stop": stop,
        "inventory": body.inventory(),
        "cells": cells,
    }


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def load(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != VERSION:
        raise ValueError(f"unsupported checkpoint version: {data.get('version')}")
    return data


def restore(data: dict, feet: Pos, kit: Callable[..., dict]) -> int:
    """Put the checkpoint's cells and inventory into the world the body stands in.
    ``kit(**fields)`` is the lab kit call; ``feet`` the body's feet cell. Returns the
    number of cells set."""
    own = {feet, (feet[0], feet[1] + 1, feet[2])}  # the kit refuses the body's cells
    places = [
        {"state": state, "offset": [x - feet[0], y - feet[1], z - feet[2]]}
        for x, y, z, state in data["cells"]
        if (x, y, z) not in own
    ]
    stacks = [{"item": i, "count": n} for i, n in data["inventory"].items()]
    kit(inventory=stacks)
    for i in range(0, len(places), KIT_PLACE_MAX):
        kit(place=places[i : i + KIT_PLACE_MAX])
    return len(places)


class Recorder:
    """Writes ``checkpoints/NNN.json`` every ``every`` finished actions (NNN = the
    count) and ``final.json`` when the build stops."""

    def __init__(
        self, folder: Path, template: bp.Template, body, meta: dict, every: int = 10
    ):
        self.folder, self.template, self.body, self.meta = folder, template, body, meta
        self.every, self.last = every, 0

    def __call__(self, report, final: bool) -> None:
        done = len(report.placed) + len(report.broken) + len(report.used)
        if final:
            name = "final"
        elif done - self.last >= self.every:
            name = f"{done:04d}"
        else:
            return
        self.last = done
        save(
            self.folder / "checkpoints" / f"{name}.json",
            snapshot(self.template, self.body, self.meta, report.stop),
        )
