"""Rebuild the map the controller actually had, then replay one planning decision.

Only terrain batches Python read (policy_observation sample_tick) are ingested:
the body's policy_sample log also holds batches the controller never received,
and replaying those gives a map the live planner never saw (Oct 31 R1 analysis).
Since mod fdc9e533 unread sections are carried into the next read and the event
log records each section only when scanned, so every batch up to the latest read
one counts as delivered. OLD_MOD=1 restores the read-batches-only rule.

Usage: GOAL="[-152,72,-136]" CAP=1229 uv run --locked python     scripts/analysis/replay_delivered.py <attempt>/events.jsonl <tick> "[x,y,z]"
Env: GOAL (required), CAP node cap (default 1229), TERRAIN / MOVEMENT profile paths.
"""

import json
import os
import pathlib
import sys
import tempfile

from mclab import route_search as rs
from mclab.awareness import Awareness
from mclab.memory_policy import MemoryPolicy
from mclab.movement_policy import MovementPolicy
from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory
from mclab.terrain_steps import TerrainPolicy
from mclab.terrain_transport import expand_terrain

f, upto, start = sys.argv[1], int(sys.argv[2]), tuple(json.loads(sys.argv[3]))
goal = tuple(json.loads(os.environ["GOAL"]))
got = set()
samples = []
for line in open(f):
    h = line[:600]
    if '"policy_observation"' in h:
        p = json.loads(line)["payload"]
        if p.get("tick", 0) <= upto:
            st = ((p.get("awareness") or {}).get("coverage") or {}).get("sample_tick")
            if st is not None:
                got.add(st)
    elif '"policy_sample"' in h:
        d = json.loads(line)["payload"]["data"]
        if "terrain_sections" in d and d["tick"] <= upto:
            samples.append(d)
# Since mod fdc9e533 unread sections are carried into the next /policy read, so
# every batch scanned up to the latest read one was delivered (older mods: only
# read batches). Set OLD_MOD=1 for attempts before fdc9e533.
last = max(got, default=-1)
mem = None
n = 0
for d in samples:
    st = d["terrain_sections"]["sample_tick"]
    if (st not in got) if os.environ.get("OLD_MOD") else st > last:
        continue
    if mem is None:
        ident = MapIdentity(
            "w",
            "g",
            "minecraft:overworld",
            Awareness.from_payload(d["awareness"]["config"]),
        )
        mem = SpatialMemory(
            MapArchive(pathlib.Path(tempfile.mkdtemp()) / "m.db", ident),
            "s",
            policy=MemoryPolicy(memory_mode="session"),
        )
    mem.ingest(expand_terrain(d))
    n += 1
print("batches sent", len(samples), "read", n, "sections known", len(mem.grid.sections))
for k in [(-4, 4, -5), (-3, 4, -5), (-4, 4, -4), (-3, 4, -4), (-4, 3, -5)]:
    print("  ", k, getattr(mem.grid.sections.get(k), "known", 0))
INV = [
    {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
    {"slot": 1, "item": "minecraft:iron_shovel", "count": 1},
    {"slot": 2, "item": "minecraft:iron_axe", "count": 1},
    {"slot": 3, "item": "minecraft:cobblestone", "count": 64},
    {"slot": 4, "item": "minecraft:dirt", "count": 64},
]
tp = TerrainPolicy.from_payload(
    json.load(
        open(
            os.environ.get("TERRAIN", "config/stepc/terrain-stepc-baritone-moves.json")
        )
    )
)
pol = MovementPolicy.from_payload(
    json.load(open(os.environ.get("MOVEMENT", "config/stepc/movement-baritone.json")))
)
s = rs.RouteSearch(
    mem.planning_snapshot()[1].frozen_view(),
    start,
    goal,
    map_revision=0,
    goal_radius=2.5,
    owned_snapshot=True,
    movement_policy=pol,
    terrain_policy=tp,
    inventory=INV,
)
cap = int(os.environ.get("CAP", "1229"))
while (
    r := s.advance(node_limit=min(512, cap), budget_ms=1000, segment_length=999)
).status == "searching" and s.expanded < cap:
    pass
print(r.status, r.reason, s.expanded, [tuple(map(round, p)) for p in r.route[:16:3]])
