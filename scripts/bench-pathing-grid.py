"""Bench: admit a 64-radius full-height chunk view through SpatialMemory.

Synthetic rolling terrain, sent as native-sized samples (8192 cells). Reports
admission ms per sample, retained cells, section memory, persistence backlog and
A* expansions/s on the grid snapshot vs a plain dict of the same surface band.

    rtk proxy uv run --locked python scripts/bench-pathing-grid.py [--memory-module P]

`--memory-module` loads another spatial_memory.py (e.g. the pre-grid version from
git) for comparison; it must expose the same SpatialMemory constructor.
"""

import argparse
import importlib.util
import json
import math
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from mclab.awareness import Awareness
from mclab.route_search import RouteSearch
from mclab.spatial_archive import MapArchive

RADIUS, BOTTOM, TOP, SAMPLE = 64, -64, 320, 8192
AIR = {
    "block": "minecraft:air",
    "fluid": "minecraft:empty",
    "replaceable": True,
    "properties": {},
    "collision_boxes": [],
}
SOLID = {
    name: {
        "block": f"minecraft:{name}",
        "fluid": "minecraft:empty",
        "replaceable": False,
        "properties": {},
        "collision_boxes": [[0, 0, 0, 1, 1, 1]],
    }
    for name in ("grass_block", "dirt", "stone", "deepslate")
}


def surface(x, z):
    return 70 + round(4 * math.sin(x / 9) + 3 * math.cos(z / 7))


def state(x, y, z):
    top = surface(x, z)
    if y > top:
        return AIR
    if y == top:
        return SOLID["grass_block"]
    if y > top - 4:
        return SOLID["dirt"]
    return SOLID["stone"] if y > 0 else SOLID["deepslate"]


def samples(awareness, tick0):
    batch = []
    tick = tick0
    for x in range(-RADIUS, RADIUS + 1):
        for z in range(-RADIUS, RADIUS + 1):
            for y in range(BOTTOM, TOP):
                batch.append(
                    {**state(x, y, z), "position": [x, y, z], "observed_tick": tick}
                )
                if len(batch) == SAMPLE:
                    yield observation(batch, tick, awareness)
                    batch, tick = [], tick + 5
    if batch:
        yield observation(batch, tick, awareness)


def observation(cells, tick, awareness):
    return {
        "sensor_profile": awareness.sensor_profile,
        "tick": tick,
        "awareness": {
            "config": awareness.payload(),
            "revision": f"r{tick}",
            "coverage": {"sample_tick": tick},
        },
        "body": {"position": [0.5, surface(0, 0) + 1, 0.5]},
        "visible_blocks": cells,
    }


def load_memory(path):
    if path is None:
        from mclab import spatial_memory

        return spatial_memory
    spec = importlib.util.spec_from_file_location("bench_memory", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_memory"] = module
    spec.loader.exec_module(module)
    return module


def astar(cells, label):
    start = (-40, surface(-40, -40) + 1, -40)
    goal = (40, surface(40, 40) + 1, 40)
    search = RouteSearch(cells, start, goal, map_revision=1, owned_snapshot=True)
    began = time.perf_counter()
    result = search.advance(node_limit=200000, budget_ms=60000, segment_length=1000)
    elapsed = time.perf_counter() - began
    return {
        "map": label,
        "status": result.status,
        "expanded": result.expanded,
        "nodes_per_s": round(result.expanded / elapsed),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory-module")
    parser.add_argument(
        "--trace-memory", action="store_true", help="tracemalloc (slows admission)"
    )
    args = parser.parse_args()
    module = load_memory(args.memory_module)
    awareness = Awareness.resolve(
        "chunk", horizontal_radius_blocks=RADIUS, entity_radius_blocks=RADIUS
    )
    with tempfile.TemporaryDirectory() as folder:
        identity = module.MapIdentity("bench", "g1", "minecraft:overworld", awareness)
        memory = module.SpatialMemory(MapArchive(Path(folder) / "m.db", identity), "s")
        durations, backlog, failure = [], [], None
        if args.trace_memory:
            tracemalloc.start()
        for obs in samples(awareness, 100):
            began = time.perf_counter()
            try:
                memory.ingest(obs)
            except RuntimeError as error:
                failure = f"sample {len(durations)}: {error}"
                break
            durations.append((time.perf_counter() - began) * 1000)
            backlog.append(memory.status()["persistence_queue"])
        status = memory.status()
        traced_mb = traced_after_drain = None
        if args.trace_memory:
            traced_mb = round(tracemalloc.get_traced_memory()[0] / 2**20, 1)
        drain_s = None
        if failure is None:
            _, view = memory.planning_snapshot()
            # No deadline here: measure how long the writer needs to catch up.
            drain_began = time.perf_counter()
            while memory.status()["persistence_queue"] or getattr(
                memory, "writing", False
            ):
                time.sleep(0.05)
            drain_s = time.perf_counter() - drain_began
            memory.flush()
            if args.trace_memory:
                traced_after_drain = round(
                    tracemalloc.get_traced_memory()[0] / 2**20, 1
                )
                tracemalloc.stop()
        band = {
            (x, y, z): state(x, y, z) | {"position": [x, y, z]}
            for x in range(-RADIUS, RADIUS + 1)
            for z in range(-RADIUS, RADIUS + 1)
            for y in range(surface(x, z) - 2, surface(x, z) + 5)
        }
        report = {
            "module": args.memory_module or "mclab.spatial_memory",
            "samples": len(durations),
            "admission_ms": {
                "p50": round(statistics.median(durations), 1),
                "p95": round(sorted(durations)[int(len(durations) * 0.95)], 1),
                "max": round(max(durations), 1),
            },
            "view_cells": (2 * RADIUS + 1) ** 2 * (TOP - BOTTOM),
            "retained_cells": status["active_cells"],
            "status": status,
            "failure": failure,
            "traced_mb_after_admission": traced_mb,
            "traced_mb_after_writer_drain": traced_after_drain,
            "max_persistence_queue": max(backlog),
            "writer_drain_after_admission_s": drain_s and round(drain_s, 1),
            "writer_cells_per_s": drain_s
            and round(status["active_cells"] / (sum(durations) / 1000 + drain_s)),
            "live_inflow_cells_per_s": SAMPLE * 4,
            "astar": [astar(band, "dict surface band")]
            + ([astar(view, "grid snapshot")] if failure is None else []),
        }
        try:
            memory.close()
        except RuntimeError as error:
            report["close_error"] = str(error)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
