"""Replay one planning decision through the Java RoutePlanner on the delivered map.

Rebuilds the map the controller actually had at <tick>, exports a box around start
and goal, and runs RoutePlanner on the export (gradle task routePlannerParity).

Only terrain batches Python read (policy_observation sample_tick) are ingested: the
body's policy_sample log also holds batches the controller never received, and
replaying those gives a map the live planner never saw (Oct 31 R1 analysis). Since
mod fdc9e533 unread sections are carried into the next read, so every batch up to
the latest read one counts as delivered. OLD_MOD=1 restores the read-batches-only
rule for attempts before fdc9e533. Exact only for plans whose plan_accepted says
terrain: uploaded; live plans read the world within their terrain_radius.

Usage: GOAL="[x,y,z]" CAP=8000 uv run --locked python scripts/analysis/planner_parity.py
       <attempt>/events.jsonl <tick> "[x,y,z]"
Flags: UNKNOWN=1 (plan through unknown space), SWIM=1 (swimming and Baritone moves),
PLAN=plan-N replays that recorded plan_route request (edits and all) on the map.
EDITS=1 (the production terrain profile with the natural kit: everything, edits included).
The export is kept (path printed) for reruns: gradlew -q -p mcb-body routePlannerParity
"-Pargs=<export.json> <nodeLimit> 1".
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
from dataclasses import replace

from mclab.awareness import Awareness
from mclab.body_search import edit_payload
from mclab.memory_policy import MemoryPolicy
from mclab.movement_policy import MovementPolicy
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
SWIM = bool(os.environ.get("SWIM"))  # planner_swim + baritone_moves (no edits)
if SWIM:
    LAND = replace(LAND, planner_swim=True, baritone_moves=True)
EDITS = bool(os.environ.get("EDITS"))  # the frozen-comparison terrain profile
KIT = (
    {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
    {"slot": 1, "item": "minecraft:iron_shovel", "count": 1},
    {"slot": 2, "item": "minecraft:iron_axe", "count": 1},
    {"slot": 3, "item": "minecraft:cobblestone", "count": 64},
)
if EDITS:
    SWIM = True
    profile = ROOT / "config/stepc/terrain-stepc-baritone-moves.json"
    LAND = TerrainPolicy.from_payload(json.loads(profile.read_text(encoding="utf-8")))
    if os.environ.get("UNKNOWN") == "0":
        LAND = replace(LAND, planner_unknown=False)
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
    old_mod = bool(os.environ.get("OLD_MOD"))
    for d in samples:
        tick = d["terrain_sections"]["sample_tick"]
        if (tick not in got) if old_mod else tick > last:
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
        "unknown_penalty": LAND.unknown_penalty if LAND.planner_unknown else 0,
        "swim": SWIM,
        "max_gap": MOVES.max_gap,
        "start": list(start),
        "goal": list(goal),
        "radius": 2.5,
        "max_drop": MOVES.max_drop,
        **({"edits": edit_payload(LAND, KIT, ())} if EDITS else {}),
        "palette": palette,
        "cells": rows,
    }
    if PLAN:  # replay the recorded request itself (edits, radius, moves) on this map
        payload["request"] = PLAN
    target.write_text(json.dumps(payload), encoding="utf-8")
    return len(rows)


def recorded_request(path, plan_id):
    """The `plan_route` request payload that carried `plan_id` (PLAN=plan-N)."""
    needle = f'"{plan_id}"'
    with open(path, encoding="utf-8") as f:
        for line in f:
            if '"plan_route"' in line and needle in line:
                e = json.loads(line)
                if e["type"] == "request" and e["payload"].get("plan_id") == plan_id:
                    return e["payload"]
    raise SystemExit(f"no plan_route request {plan_id} in {path}")


PLAN = None


def main():
    global PLAN
    path, tick, start = sys.argv[1], int(sys.argv[2]), tuple(json.loads(sys.argv[3]))
    if os.environ.get("PLAN"):
        PLAN = recorded_request(path, os.environ["PLAN"])
    goal, cap = (
        tuple(json.loads(os.environ["GOAL"])),
        int(os.environ.get("CAP", "8000")),
    )
    view = delivered_map(path, tick)
    out = pathlib.Path(tempfile.mkdtemp()) / "export.json"
    count = export(view, start, goal, out)
    gradle = ROOT / "mcb-body" / ("gradlew.bat" if os.name == "nt" else "gradlew")
    java = subprocess.run(
        [
            str(gradle),
            "-q",
            "-p",
            str(ROOT / "mcb-body"),
            "routePlannerParity",
            f"-Pargs={out} {cap} 1",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    j = json.loads(java.stdout.strip().splitlines()[-1])
    print(f"export {out}: cells {count}; cap {cap}")
    print(
        f"java {j['status']:10} expanded {j['expanded']:6} cost {j['cost']:9.2f} "
        f"{j['ms']:8.1f} ms; ends {j['route'][-1] if j['route'] else None}"
    )
    print("route", [tuple(map(round, p)) for p in j["route"][::3]])
    for step in j.get("steps", ()):
        print("edit", step)


if __name__ == "__main__":
    main()
