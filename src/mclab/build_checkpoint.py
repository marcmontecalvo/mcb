"""Checkpoints of a build in progress, to fast-forward a test to a known point.

``snapshot`` records, from a live body, every cell it has seen in an area (by default
the template's cells, air included, so the cells the run cleared are cleared again),
the inventory and the containers it knows the contents of. ``restore`` sets a fresh
copy of the same seed's world up from it with the lab ``kit`` op (64 blocks a call, no
neighbour updates, so order does not matter), chests refilled, after which the build can
run alone: ``build_job(build_only=True)``. The body is not moved: the build walks to its
own stances. Not recorded: gathering results (trees cut, stations) outside the area.

Stage snapshots (``Recorder.milestone``): ``site_ready`` (site cleared and level, no
house block yet) and ``structure_done`` (the structure in, the details not) cover the
whole site box and the chests, and are published to the stage library
(``results/stages/<build key>/<stage>.json``, newest wins) so a lab can start from
the middle of a build: ``run-build.py --from site_ready``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from mclab import build_plan as bp

VERSION = 2
VERSIONS = (1, 2)  # 1: template cells and inventory only
KIT_PLACE_MAX = 64  # LabKit.MAX_PLACE
STAGES = ("site_ready", "structure_done")
# Below the site's ground: site prep fills ponds and holes from their bed up.
SITE_DEPTH = 8
SITE_ABOVE = 4  # above the template: tree tops cleared over the site
Pos = tuple[int, int, int]
Box = tuple[Pos, Pos]  # inclusive corners


def site_box(
    template: bp.Template,
    margin: int = 1,
    depth: int = SITE_DEPTH,
    above: int = SITE_ABOVE,
) -> Box:
    """The cells a stage snapshot keeps: the site prep area (footprint plus
    ``margin``), from ``depth`` under the ground to ``above`` over the template."""
    from mclab import site_prep

    site = site_prep.site_area(template, margin)
    return (
        (site.x0, site.ground_y - depth, site.z0),
        (site.x1, site.top_y + above, site.z1),
    )


def _box_cells(box: Box):
    (x0, y0, z0), (x1, y1, z1) = box
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            for z in range(z0, z1 + 1):
                yield x, y, z


def snapshot(
    template: bp.Template,
    body,
    meta: dict,
    stop: str = "",
    *,
    area: Box | None = None,
    containers: dict | None = None,
) -> dict:
    """``meta``: seed, start, build (path), origin, rotation, mirror - what a resume
    needs to rebuild the template. ``area``: a box of cells to keep instead of the
    template's; ``containers``: position -> {item: count} known to the run."""
    cells = []
    kept = set()
    for pos in sorted(template.cells) if area is None else _box_cells(area):
        state = body.world(*pos)
        if state is not None:
            cells.append([*pos, state])
            kept.add(pos)
    for pos in sorted(containers or ()):  # the supply chests stand outside the site
        if pos not in kept and (state := body.world(*pos)) is not None:
            cells.append([*pos, state])
    return {
        "version": VERSION,
        **meta,
        "stop": stop,
        "inventory": body.inventory(),
        "cells": cells,
        "containers": [
            {"pos": list(pos), "contents": dict(items)}
            for pos, items in sorted((containers or {}).items())
        ],
    }


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)  # a reader never sees half a stage


def load(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") not in VERSIONS:
        raise ValueError(f"unsupported checkpoint version: {data.get('version')}")
    return data


def stage_path(build_key: str, stage: str, root: Path) -> Path:
    """The stage library file of a build key (``run_digest.build_key``)."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; one of {STAGES}")
    return root / re.sub(r"[^A-Za-z0-9@,#._+-]", "_", build_key) / f"{stage}.json"


def find_stage(build_key: str, stage: str, root: Path) -> Path:
    """The library's ``stage`` of a build key; FileNotFoundError says what is there
    and how to make the missing one."""
    path = stage_path(build_key, stage, root)
    if path.is_file():
        return path
    held = sorted(p.stem for p in path.parent.glob("*.json")) or ["nothing"]
    make = {"site_ready": "--until site", "structure_done": "--until structure"}
    raise FileNotFoundError(
        f"no {stage} stage for {build_key} (the library holds {', '.join(held)}); "
        f"a run with {make[stage]} saves one"
    )


def check_build(data: dict, build_sha256: str) -> None:
    """Refuse a stage taken with another version of the build file: its cells
    would not be that template's."""
    taken = data.get("build_sha256")
    if taken and taken != build_sha256:
        raise ValueError(
            f"the {data.get('stage', 'checkpoint')} stage was taken with another "
            "version of the build file; make it again"
        )


def chests_of(data: dict) -> list[list[int]]:
    """The container positions a snapshot holds the contents of."""
    return [list(c["pos"]) for c in data.get("containers") or ()]


def _stacks(contents: dict[str, int], stack: Callable[[str], int]) -> list[dict]:
    out = []
    for item, n in contents.items():
        size = max(1, stack(item))
        while n > 0:
            out.append({"item": item, "count": min(n, size)})
            n -= size
    return out


def restore(
    data: dict,
    feet: Pos,
    kit: Callable[..., dict],
    stack: Callable[[str], int] = lambda item: 64,
) -> int:
    """Put the checkpoint's cells, chest contents and inventory into the world the body
    stands in. ``kit(**fields)`` is the lab kit call; ``feet`` the body's feet cell;
    ``stack(item)`` an item's stack size. Returns the number of cells set."""
    own = {feet, (feet[0], feet[1] + 1, feet[2])}  # the kit refuses the body's cells
    filled = {tuple(c["pos"]): c["contents"] for c in data.get("containers") or ()}
    places = []
    for x, y, z, state in data["cells"]:
        if (x, y, z) in own:
            continue
        place = {"state": state, "offset": [x - feet[0], y - feet[1], z - feet[2]]}
        if (x, y, z) in filled:
            place["contents"] = _stacks(filled[(x, y, z)], stack)
        places.append(place)
    stacks = [{"item": i, "count": n} for i, n in data["inventory"].items()]
    kit(inventory=stacks)
    for i in range(0, len(places), KIT_PLACE_MAX):
        kit(place=places[i : i + KIT_PLACE_MAX])
    return len(places)


class Recorder:
    """Writes ``checkpoints/NNN.json`` every ``every`` finished actions (NNN = the
    count), ``final.json`` when the build stops and ``<stage>.json`` at each
    milestone, which also goes to ``library`` (with ``source``: run, commit)."""

    def __init__(
        self,
        folder: Path,
        template: bp.Template,
        body,
        meta: dict,
        every: int = 10,
        *,
        area: Box | None = None,
        library: Path | None = None,
        source: dict | None = None,
    ):
        self.folder, self.template, self.body, self.meta = folder, template, body, meta
        self.every, self.last = every, 0
        self.area, self.library, self.source = area, library, source or {}

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

    def milestone(self, stage: str, containers: dict | None = None) -> dict:
        """A stage snapshot of the whole site box and the containers."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        data = snapshot(
            self.template,
            self.body,
            self.meta,
            area=self.area,
            containers=containers,
        )
        data |= {"stage": stage, "source": self.source}
        save(self.folder / "checkpoints" / f"{stage}.json", data)
        if self.library is not None:
            save(self.library / f"{stage}.json", data)
        return data
