"""Java RoutePlanner vs Python RouteSearch on one delivered map (ROADMAP item 8).

Rebuilds the map the controller had at <tick> (as replay_delivered.py does), exports
a box around start and goal, runs the Python planner with the land-only policy the
Java core implements, then the Java core on the export, and compares them.

Usage: GOAL="[x,y,z]" CAP=8000 uv run --locked python scripts/analysis/planner_parity.py
       <attempt>/events.jsonl <tick> "[x,y,z]"
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from dataclasses import replace

from mclab.awareness import Awareness
from mclab.body_search import edits_payload
from mclab.memory_policy import MemoryPolicy
from mclab.movement_policy import MovementPolicy
from mclab.route_search import RouteSearch
from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory
from mclab.terrain_steps import TerrainPolicy
from mclab.terrain_transport import expand_terrain

ROOT = pathlib.Path(__file__).resolve().parents[2]
MARGIN, HEADROOM = 24, 24
LAND = TerrainPolicy(
    allow_break=False,
    allow_place=False,
    planner_tick_costs=True,
    heuristic_weight=1,
    baritone_search=True,
    baritone_diagonals=True,
)
if os.environ.get("UNKNOWN"):  # planner_unknown: plan through unknown space (D3)
    LAND = replace(LAND, planner_unknown=True)
EDITS = bool(os.environ.get("EDITS"))  # production profile with edit edges
KIT = [
    {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
    {"slot": 1, "item": "minecraft:iron_shovel", "count": 1},
    {"slot": 2, "item": "minecraft:iron_axe", "count": 1},
    {"slot": 3, "item": "minecraft:cobblestone", "count": 64},
    {"slot": 4, "item": "minecraft:dirt", "count": 64},
]
if EDITS:
    LAND = TerrainPolicy.from_payload(
        json.load(open(ROOT / "config/stepc/terrain-stepc-baritone-moves.json"))
    )
SWIM = EDITS or bool(os.environ.get("SWIM"))  # planner_swim + baritone_moves (no edits)
if SWIM:
    LAND = replace(LAND, planner_swim=True, baritone_moves=True)
MOVES = MovementPolicy(
    allow_diagonals=True,
    allow_sprint=True,
    max_drop=3,
    allow_shapes=True,
    max_gap=2 if SWIM else 0,
    allow_swim=SWIM,
    allow_climb=SWIM,
)


def delivered_map(path, upto):
    got, samples = set(), []
    for line in open(path, encoding="utf-8"):
        head = line[:600]
        if '"policy_observation"' in head:
            p = json.loads(line)["payload"]
            if p.get("tick", 0) <= upto:
                st = ((p.get("awareness") or {}).get("coverage") or {}).get(
                    "sample_tick"
                )
                if st is not None:
                    got.add(st)
        elif '"policy_sample"' in head:
            d = json.loads(line)["payload"]["data"]
            if "terrain_sections" in d and d["tick"] <= upto:
                samples.append(d)
    last, mem = max(got, default=-1), None
    for d in samples:
        if d["terrain_sections"]["sample_tick"] > last:
            continue
        if mem is None:
            config = Awareness.from_payload(d["awareness"]["config"])
            ident = MapIdentity("w", "g", "minecraft:overworld", config)
            archive = MapArchive(pathlib.Path(tempfile.mkdtemp()) / "m.db", ident)
            mem = SpatialMemory(
                archive, "s", policy=MemoryPolicy(memory_mode="session")
            )
        mem.ingest(expand_terrain(d))
    return mem.planning_snapshot()[1].frozen_view()


def export(view, start, goal, target):
    xs = range(min(start[0], goal[0]) - MARGIN, max(start[0], goal[0]) + MARGIN + 1)
    zs = range(min(start[2], goal[2]) - MARGIN, max(start[2], goal[2]) + MARGIN + 1)
    ys = range(
        int(min(start[1], goal[1])) - HEADROOM, int(max(start[1], goal[1])) + HEADROOM
    )
    palette, index, rows = [], {}, []
    for x in xs:
        for z in zs:
            for y in ys:
                cell = view.get((x, y, z))
                if cell is None:
                    continue
                facts = {
                    k: cell[k]
                    for k in ("block", "fluid", "collision_boxes", "properties")
                    if k in cell
                }
                key = json.dumps(facts, sort_keys=True)
                if key not in index:
                    index[key] = len(palette)
                    palette.append(facts)
                rows.append([x, y, z, index[key]])
    payload = {
        **({"edits": edits_payload(LAND, KIT)} if EDITS else {}),
        "unknown_penalty": LAND.unknown_penalty if LAND.planner_unknown else 0,
        "swim": SWIM,
        "max_gap": MOVES.max_gap,
        "start": list(start),
        "goal": list(goal),
        "radius": 2.5,
        "max_drop": MOVES.max_drop,
        "palette": palette,
        "cells": rows,
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    return len(rows)


def main():
    path, tick, start = sys.argv[1], int(sys.argv[2]), tuple(json.loads(sys.argv[3]))
    goal, cap = (
        tuple(json.loads(os.environ["GOAL"])),
        int(os.environ.get("CAP", "8000")),
    )
    view = delivered_map(path, tick)
    out = pathlib.Path(tempfile.mkdtemp()) / "export.json"
    count = export(view, start, goal, out)
    # The Python run uses the export too, so both see exactly the same cells.
    data = json.loads(out.read_text(encoding="utf-8"))
    cells = {
        (x, y, z): {**data["palette"][i], "position": [x, y, z]}
        for x, y, z, i in data["cells"]
    }
    s = RouteSearch(
        cells,
        start,
        goal,
        map_revision=0,
        goal_radius=2.5,
        owned_snapshot=True,
        movement_policy=MOVES,
        terrain_policy=LAND,
        inventory=KIT if EDITS else (),
    )
    began = time.perf_counter()
    while (
        r := s.advance(node_limit=512, budget_ms=60000, segment_length=9999)
    ).status == "searching" and s.expanded < cap:
        pass
    py_ms = (time.perf_counter() - began) * 1000
    end = tuple(r.route[-1])
    gradle = ROOT / "mcb-body" / ("gradlew.bat" if os.name == "nt" else "gradlew")
    java = subprocess.run(
        [
            str(gradle),
            "-q",
            "-p",
            str(ROOT / "mcb-body"),
            "routePlannerParity",
            f"-Pargs={out} {cap} 5",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    j = json.loads(java.stdout.strip().splitlines()[-1])
    same = [tuple(p) for p in j["route"]] == [
        tuple(map(float, p)) for p in r.route
    ] or [tuple(p) for p in j["route"]] == [tuple(p) for p in r.route]
    print(f"cells {count}; cap {cap}")
    print(
        f"python {r.status:10} expanded {s.expanded:6} cost {s.costs.get(end, 0):9.2f} "
        f"{py_ms:8.1f} ms {s.expanded / py_ms * 1000:9.0f} nodes/s"
    )
    print(
        f"java   {j['status']:10} expanded {j['expanded']:6} cost {j['cost']:9.2f} "
        f"{j['ms']:8.1f} ms {j['expanded'] / max(j['ms'], 1e-6) * 1000:9.0f} nodes/s"
    )
    print(
        "same route"
        if same
        else f"routes differ: py {r.route[-1]} java {j['route'][-1]}"
    )


if __name__ == "__main__":
    main()
