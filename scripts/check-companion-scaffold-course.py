# ruff: noqa: E501
"""Live scaffolding obstacle course: the bot uses scaffolding the way a player does.

A hole (11 x 3 x 4) with ground all round, a bot holding only scaffolding and tools,
and eight legs (0-7) in docs/SCAFFOLDING.md. Run: python scripts/check-companion-scaffold-course.py
[--origin X Y Z] (the spawn feet cell of a flat, empty lab spot; default 990 80 988).
Every touched cell is restored afterwards."""

import argparse
import json
import math
import subprocess
import sys
import time
import traceback
from dataclasses import replace
from datetime import UTC, datetime

from live_common import kit

from mclab import build_plan as bp
from mclab import scaffold_course as sc
from mclab import temporary_blocks
from mclab.build_runner import BuildReport, _clean_up, _Runner
from mclab.companion_body import WALK_TERRAIN, end_session, open_bridge, start_session
from mclab.companion_jobs import load_vanilla_pack
from mclab.paths import ROOT, write_json
from mclab.recorder import exclusive_arena
from mclab.run_launch import script_run
from mclab.schematic import parse_state

AIR = ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")
START = 64
KIT_BATCH = 60  # the lab's place cap is 64 blocks per kit
STONE = "minecraft:stone"


def block(body, p):
    s = body.world(*p)
    return parse_state(s)[0] if s else None


def put(bridge, body, cells):
    """Set cells (world pos -> state) in batches, offsets from the body's feet cell."""
    feet = [math.floor(v) for v in body.position()]
    body_cells = {tuple(feet), (feet[0], feet[1] + 1, feet[2])}
    items = [
        {"state": s, "offset": [p[i] - feet[i] for i in range(3)]}
        for p, s in cells.items()
        if tuple(p) not in body_cells  # the kit never sets a cell the body is in
    ]
    for p in body_cells:  # what the body stands in it takes out itself
        if tuple(p) in cells and cells[tuple(p)] in (None, *AIR) and block(body, p) not in AIR:
            body.break_block(p)
    for i in range(0, len(items), KIT_BATCH):
        kit(bridge, place=items[i : i + KIT_BATCH])


def pre_check(body, o):
    """Known, flat, empty ground: air above, solid below, hole region known."""
    bad = []
    for dx in range(-10, 17):
        for dz in range(-5, 6):
            if block(body, sc.at(o, dx, -1, dz)) in (None, *AIR):
                bad.append(("ground", dx, dz))
            for dy in range(0, 10):
                if block(body, sc.at(o, dx, dy, dz)) not in AIR:
                    bad.append(("not empty", dx, dy, dz))
    for p in sc.hole_cells(o):
        if body.world(*p) is None:
            bad.append(("unknown", p))
    if bad:
        raise RuntimeError(f"Course spot is not flat and empty: {bad[:8]} ...")


class Course:
    def __init__(self, bridge, body, origin):
        self.bridge, self.body, self.o = bridge, body, origin
        self.placed: dict = {}  # cell -> block id, every temporary block recorded
        self.seen: set = set()
        self.legs: dict = {}
        self.windows: dict = {}  # leg -> (start, end) monotonic seconds
        self.ticks: dict = {}  # leg -> (first, last) tick

    def record_placed(self):
        """Read the block id of every cell the planner placed since last time."""
        self.body.observe()
        for p in sorted(self.body.stray_footholds - self.seen):
            self.placed[p] = block(self.body, p)
        self.seen |= self.body.stray_footholds

    def window(self, start):
        return [s.get("body") or {} for s in self.bridge.samples[start:]]

    def stand(self, dx, up, radius=0.3):
        """Walk to the stance dx east of the spawn, ``up`` above it, placing allowed."""
        goal = sc.stance(self.o, dx, up)
        # walk_to snaps a goal to the nearest standable height (the ground): aim exactly.
        ok = self.body._navigate(goal, reach=None, radius=radius)
        pos = self.body.observe()["body"]
        self.record_placed()
        near = (
            math.hypot(pos["position"][0] - goal[0] - 0.5, pos["position"][2] - goal[2] - 0.5)
            <= 0.6
        )
        return ok and near and abs(pos["position"][1] - goal[1]) <= 0.3 and (
            pos.get("on_ground") is True
        ), pos["position"]

    def scaffold_at(self, cells):
        return all(block(self.body, p) == sc.SCAFFOLDING for p in cells)

    def leg(self, name, run):
        if any(not v["passed"] for v in self.legs.values()):
            self.legs[name] = {"passed": False, "skipped": "an earlier leg failed"}
            return
        start = len(self.bridge.samples)
        self.bridge.state()
        t0, tick0 = time.monotonic_ns() / 1e9, self.bridge.last_tick
        edits0 = (self.body.edits_placed, self.body.edits_broken)
        try:
            result = run(start)
        except Exception:
            result = {"passed": False, "error": traceback.format_exc()}
        self.bridge.state()
        t1, tick1 = time.monotonic_ns() / 1e9, self.bridge.last_tick
        placed = self.body.edits_placed - edits0[0]
        broken = self.body.edits_broken - edits0[1]
        problems = sc.edit_problems(name.split()[0], placed, broken)
        self.windows[name], self.ticks[name] = (t0, t1), (tick0, tick1)
        result.update(
            wall_s=round(t1 - t0, 1), ticks=tick1 - tick0,
            scaffolding_placed=placed, scaffolding_broken=broken, edit_problems=problems,
        )
        result["passed"] = bool(result["passed"]) and not problems
        result["foreign"] = {str(p): b for p, b in sc.foreign(self.placed).items()}
        result["passed"] = bool(result["passed"]) and not result["foreign"]
        self.legs[name] = result
        print(name, "PASS" if result["passed"] else "FAIL", flush=True)

    # --- legs -------------------------------------------------------------------
    def leg0(self, start):
        o = self.o
        goal = sc.at(o, -7, 0, 0)
        with self.body.no_placing(wall=40):
            ok = self.body.walk_to(goal, radius=0.5)
        samples = self.window(start)
        inside = [
            s for s in samples
            if o[0] - 4 <= s["position"][0] <= o[0] - 1 and abs(s["position"][2] - o[2] - 0.5) < 0.6
        ]
        poses = [s.get("pose") for s in samples]
        # the walls beside the cells leave only the way through (a detour is longer)
        return {
            "passed": ok and len(inside) >= 3 and sc.never_crouched(poses)
            and bool(poses) and None not in poses,
            "inside_samples": len(inside), "poses": sorted({str(p) for p in poses}),
        }

    def leg1(self, start):
        ok, pos = self.stand(sc.TOWER_A, 5)
        cells = sc.tower_cells(self.o, sc.TOWER_A)
        return {"passed": ok and self.scaffold_at(cells), "position": pos, "tower": len(cells)}

    def leg2(self, start):
        ok, pos = self.stand(6, 5)
        return {"passed": ok and self.scaffold_at([sc.at(self.o, 6, 4, 0)]), "position": pos}

    def leg3(self, start):
        ok, pos = self.stand(6, 7)
        cells = [sc.at(self.o, 6, dy, 0) for dy in (4, 5, 6)]
        return {"passed": ok and self.scaffold_at(cells), "position": pos}

    def leg4(self, start):
        ok, pos = self.stand(sc.TOWER_B, 5)
        samples = self.window(start)
        ys = [s["position"][1] for s in samples if s.get("position")]
        crouched = [s.get("pose") for s in samples if str(s.get("pose")).upper() == "CROUCHING"]
        cells = sc.tower_cells(self.o, sc.TOWER_B)
        return {
            "passed": ok and self.scaffold_at(cells) and bool(ys)
            and min(ys) <= self.o[1] + 0.3 and bool(crouched),
            "position": pos, "reached_ground": bool(ys) and min(ys) <= self.o[1] + 0.3,
            "crouch_samples": len(crouched),
        }

    def leg5(self, start):
        ok1, _ = self.stand(10, 5)
        ok2, pos = self.stand(7, 5)
        span = sc.span_cells(self.o)
        missing = [p for p in span if block(self.body, p) != sc.SCAFFOLDING]
        rim = sc.TOWER_B - sc.TOWER_A
        return {
            "passed": ok1 and ok2 and not missing and rim <= 12,
            "position": pos, "missing": missing, "rim_distance": rim,
        }

    def leg6(self, start):
        ok, _ = self.stand(sc.TOWER_B, 5)
        begin = len(self.bridge.samples)
        ok2, pos = self.stand(sc.TOWER_A, 5)
        ys = [s["position"][1] for s in self.window(begin) if s.get("position")]
        floor = self.o[1] + 4  # one below the stance: the ground is 5 down
        return {
            "passed": ok and ok2 and sc.stayed_up(ys, floor),
            "position": pos, "min_y": min(ys) if ys else None, "floor_y": floor,
        }

    def leg7(self, start):
        o, body = self.o, self.body
        with body.no_placing(wall=90):
            down = body.walk_to(o, radius=0.5)
        self.record_placed()
        scaffolds = set(self.placed)
        template = bp.Template(cells={}, size=(0, 0, 0), origin=(0, 0, 0), name="")
        report = BuildReport()
        runner = _Runner(body, report, 5000, load_vanilla_pack().blocks)
        floor_cells = [sc.at(o, dx, -sc.DEPTH, 0) for dx in sc.HOLE_X]
        floor_cells += [sc.at(o, dx, 0, 0) for dx in (sc.TOWER_A, sc.TOWER_B)]
        with body.no_placing(wall=90):
            _clean_up(runner, template, scaffolds)
            for _ in range(3):
                body.wait_ticks(40)
                body.observe()
                left = [p for p in scaffolds if block(body, p) == sc.SCAFFOLDING]
                if not left and body.inventory().get(sc.SCAFFOLDING, 0) >= START:
                    break
                body.collect_drops(floor_cells, radius=12.0)
                if left:
                    _clean_up(runner, template, set(left))
        body.observe()
        left = [p for p in scaffolds if block(body, p) == sc.SCAFFOLDING]
        held = body.inventory().get(sc.SCAFFOLDING, 0)
        # No tolerance: every block dropped to an item and the body can walk to all of them
        # (the hole's stairs). A shortfall means items despawned or were left, a real failure.
        return {
            "passed": down and not left and held == START and len(report.broken) <= 6,
            "left": [list(p) for p in left], "held": held, "start": START,
            "base_breaks": len(report.broken), "placed_cells": len(scaffolds),
        }


def main_inner(folder, origin, summary):
    bridge = open_bridge(folder)
    memory, body, restore, course = None, None, {}, None
    try:
        body, memory = start_session(
            bridge, folder, origin,
            terrain_policy=WALK_TERRAIN, world_memory=False,  # builds keep the extra moves (pillar)
        )
        body.observe()
        o = tuple(math.floor(v) for v in body.position())
        if o != tuple(origin):
            raise RuntimeError(f"Spawned at {o}, not {origin}")
        # Clear what an earlier run left (it may have stopped before its restore):
        # air over the course, stone under it, then let the sensor see it.
        kit(bridge, clear=[16, 10, 5])
        for dy in (0, 1):  # the clear skips the body's own cells
            if block(body, sc.at(o, 0, dy, 0)) not in AIR:
                body.break_block(sc.at(o, 0, dy, 0))
        body.wait_ticks(40)
        body.observe()
        pre_check(body, o)
        holes = sc.hole_cells(o)
        leg0_cells = {sc.at(o, dx, 0, 0): sc.SCAFFOLDING for dx in (-2, -3, -4)}
        for dx in (-2, -3, -4):
            for dz in (-2, -1, 1, 2):
                for dy in (0, 1):
                    leg0_cells[sc.at(o, dx, dy, dz)] = STONE
        restore = {p: body.world(*p) for p in (*holes, *leg0_cells)}
        # first: a refused kit may still land part of the cells
        put(bridge, body, holes)
        put(bridge, body, leg0_cells)
        kit(bridge, inventory=[
            {"item": sc.SCAFFOLDING, "count": START},
            {"item": "minecraft:iron_pickaxe", "count": 1},
            {"item": "minecraft:iron_axe", "count": 1},
            {"item": "minecraft:iron_shovel", "count": 1},
        ])
        body.observe()
        body.temporary_ok = temporary_blocks.eligible(load_vanilla_pack())
        body.terrain_policy = replace(
            body.terrain_policy, allow_place=True, allow_bridge=True, allow_pillar=True,
            place_budget=64, material_allowlist=(), scaffold_materials=(sc.SCAFFOLDING,),
            protected_positions=tuple(
                sorted({*body.terrain_policy.protected_positions, *sc.protected_air(o)})
            ),
        )
        course = Course(bridge, body, o)
        # The course is built: the body's own time starts here (idle_gaps.py starts its window at it).
        bridge.record("job_started", {"job": "scaffold-course"})
        for name, run in (
            ("0 walk through", course.leg0), ("1 up", course.leg1), ("2 out", course.leg2),
            ("3 up again", course.leg3), ("4 second tower", course.leg4),
            ("5 bridge across", course.leg5), ("6 walk the bridge", course.leg6),
            ("7 take down", course.leg7),
        ):
            course.leg(name, run)
        course.record_placed()
        summary.update(legs=course.legs, windows=course.windows, ticks=course.ticks)
        summary["placed"] = {str(p): b for p, b in course.placed.items()}
    finally:
        try:
            if course:
                restore.update({p: "minecraft:air" for p in course.placed})
            if memory and restore:
                put(bridge, body, {p: s or "minecraft:air" for p, s in restore.items()})
        finally:
            try:
                if memory:
                    end_session(bridge, memory)
            finally:
                bridge.close()


def analyse(folder, summary):
    """Run-wide checks on the closed event log: planner cap hits, standstills, plan stats."""
    from mclab.build_stats import iter_events
    from mclab.search_caps import cap_hits, verdict

    caps = verdict(cap_hits(folder / "events.sqlite3"))
    done = subprocess.run(
        [sys.executable, "scripts/analysis/idle_gaps.py", "--json", "--min-seconds",
         str(sc.STANDSTILL_S), str(folder)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    try:
        rows = [r for rs in json.loads(done.stdout).values() for r in rs]
    except json.JSONDecodeError:
        rows = []
    # exit 0: clean, 1: a standstill, 2: the recording cannot prove either way
    (folder / "idle.txt").write_text(done.stderr + done.stdout, encoding="utf-8")
    placed = summary["placed"]
    summary.update(
        sc.finish(
            summary["legs"], placed, caps, rows, done.returncode in (0, 1),
            summary["ticks"],
            sc.event_stats(iter_events(folder), summary["windows"]),
        )
    )


@script_run("companion-scaffold-course")
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", type=int, nargs=3, default=(990, 80, 988))
    args = parser.parse_args()
    folder = ROOT / "results/scaffolding" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    summary = {"development_only": True, "passed": False}
    try:
        with exclusive_arena():
            main_inner(folder, tuple(args.origin), summary)
        analyse(folder, summary)
    except Exception as error:
        summary.update(passed=False, error=str(error))
        raise
    finally:
        write_json(folder / "summary.json", summary)
        print({k: v for k, v in summary.items() if k not in ("placed", "legs")}, "->", folder)
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
