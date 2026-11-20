# ruff: noqa: E501
"""Scaffold challenges, basic to advanced (src/mclab/scaffold_challenges.py), for the
bot or for a person with a normal Minecraft client.

    uv run --locked python scripts/check-scaffold-challenges.py --list
    uv run --locked python scripts/check-scaffold-challenges.py [--level basic|advanced|all] [NAME ...]
        [--subject bot|human] [--player NAME] [--origin X Y Z] [--show-markers] [--minutes N]

Each challenge is laid out at ``--origin`` (the feet cell of a flat, empty lab spot;
default 990 80 988, the lab fixture), attempted, scored and then cleared back to the
flat spot. ``--subject human``: the named player (joined to the lab server) is moved to
the start with only scaffolding and a pickaxe; the green ring and beam mark the goal
and the run ends when they stand on it (or after ``--minutes``). The bot always shows
its markers (red planned path, green goal, yellow block) with ``--show-markers``;
create or delete <run folder>/MARKERS to flip them while it runs. Results:
results/scaffolding/<stamp>/summary.json."""

import argparse
import json
import math
import time
from dataclasses import replace
from datetime import UTC, datetime

from live_common import kit

from mclab import scaffold_challenges as sch
from mclab import temporary_blocks
from mclab.companion_body import WALK_TERRAIN, end_session, open_bridge, start_session
from mclab.companion_jobs import load_vanilla_pack
from mclab.markers import run_switch
from mclab.natural import _wait_terminal
from mclab.navigation_runner import _new_identity
from mclab.paths import ROOT, write_json
from mclab.recorder import exclusive_arena
from mclab.run_launch import script_run
from mclab.schematic import parse_state

EAST = -90.0  # yaw facing +x, where every course runs


def lay(bridge, c, origin, **who):
    """Clear the spot, then set the course's cells, bottom up."""
    kit(bridge, at=list(origin), clear=c.clear_extent(), **who)
    for batch in c.place_batches():
        kit(bridge, at=list(origin), place=batch, **who)


def restore(bridge, c, origin, **who):
    """Back to the flat spot: the clear empties everything from the ground top up
    (scaffolding left standing included); the pits are filled with stone again."""
    kit(bridge, at=list(origin), clear=c.clear_extent(), **who)
    below = {p: s for p, s in c.restore_cells().items() if p[1] < 0}
    entries = [{"offset": list(p), "state": s} for p, s in sorted(below.items())]
    for i in range(0, len(entries), sch.KIT_BATCH):
        kit(bridge, at=list(origin), place=entries[i : i + sch.KIT_BATCH], **who)


def built_changed(c, origin, world) -> int:
    """Course cells (not air) whose block is no longer the one laid."""
    changed = 0
    for p, state in c.cells.items():
        if state == sch.AIR:
            continue
        now = world(*c.at(origin, p))
        if now is None or parse_state(now)[0] != parse_state(state)[0]:
            changed += 1
    return changed


def run_bot(folder, c, origin, show_markers):
    folder.mkdir(parents=True, exist_ok=True)
    bridge = open_bridge(folder)
    memory = body = None
    try:
        body, memory = start_session(
            bridge,
            folder,
            origin,
            terrain_policy=WALK_TERRAIN,
            world_memory=False,
            markers=run_switch(folder, on=show_markers),
        )
        lay(bridge, c, origin)
        kit(bridge, inventory=sch.kit(c))
        body.wait_ticks(40)
        body.observe()
        built = tuple(c.at(origin, p) for p, s in c.cells.items() if s != sch.AIR)
        body.temporary_ok = temporary_blocks.eligible(load_vanilla_pack())
        body.terrain_policy = replace(
            body.terrain_policy,
            allow_place=True,
            allow_bridge=True,
            allow_pillar=True,
            place_budget=c.scaffolding,
            material_allowlist=(),
            scaffold_materials=(sch.SCAFFOLDING,),
            # a house is not the bot's to edit (the stairs challenge)
            protected_positions=tuple(
                sorted(
                    {
                        *body.terrain_policy.protected_positions,
                        *(built if c.keep_built else ()),
                    }
                )
            ),
        )
        bridge.record("job_started", {"job": f"scaffold-challenge:{c.name}"})
        goal = c.at(origin, c.goal)
        start, t0 = len(bridge.samples), time.monotonic()
        health0, placed0 = body.health(), body.edits_placed
        body._navigate(goal, reach=None, radius=min(c.goal_radius, 0.6))
        state = body.observe()["body"]
        x, y, z = state["position"]
        reached = (
            math.hypot(x - goal[0] - 0.5, z - goal[2] - 0.5) <= c.goal_radius
            and abs(y - goal[1]) <= 0.5
            and state.get("on_ground") is True
        )
        healths = [
            s["body"]["health"]
            for s in bridge.samples[start:]
            if (s.get("body") or {}).get("health") is not None
        ]
        damage = round(max(0.0, health0 - min([*healths, body.health()])), 1)
        outcome = sch.verdict(
            c,
            reached=reached,
            damage=damage,
            scaffolding_used=body.edits_placed - placed0,
            built_changed=built_changed(c, origin, body.world) if c.keep_built else 0,
        )
        outcome.update(wall_s=round(time.monotonic() - t0, 1), position=[x, y, z])
        return outcome
    finally:
        try:
            if memory:
                restore(bridge, c, origin)
        finally:
            try:
                if memory:
                    end_session(bridge, memory)
            finally:
                bridge.close()


def run_human(folder, c, origin, player, minutes):
    folder.mkdir(parents=True, exist_ok=True)
    bridge = open_bridge(folder)
    try:
        _new_identity(bridge, "challenge-lay")
        lay(bridge, c, origin, player=player)
        _new_identity(bridge, "challenge")
        goal = c.at(origin, c.goal)
        request = bridge.send(
            "natural_start",
            subject="human",
            player=player,
            start=list(origin),
            goal=list(goal),
            yaw=EAST,
            goal_radius=c.goal_radius,
            goal_dy=0.5,
            tick_limit=int(minutes * 1200),
            mobs=False,
            show_markers=True,
            stall_ticks=0,
            kit=sch.kit(c),
        )
        bridge.acknowledge(request, "natural_accepted", timeout=10)
        print(
            f"\n== {c.name} ({c.level}): {c.task}\n   goal {list(goal)}, {minutes:g} min; you are being moved to the start.",
            flush=True,
        )
        terminal = _wait_terminal(bridge, minutes * 60 + 120)
        data = dict(terminal["data"]) if terminal else {"status": "invalid"}
        stop = bridge.send("natural_stop")
        bridge.acknowledge(stop, "stopped")
        left = (data.get("kit_left") or {}).get(sch.SCAFFOLDING, 0)
        outcome = sch.verdict(
            c,
            reached=data.get("status") == "success",
            damage=float(data.get("damage_taken", 0)),
            scaffolding_used=max(0, c.scaffolding - left),
            built_changed=int(data.get("blocks_broken", 0)) if c.keep_built else 0,
        )
        outcome.update(
            ticks=data.get("ticks"), reason=data.get("reason"), scaffolding_left=left
        )
        _new_identity(bridge, "challenge-restore")
        restore(bridge, c, origin, player=player)
        return outcome
    finally:
        bridge.close()


@script_run("scaffold-challenges")
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "names", nargs="*", help="challenges by name (default: by --level)"
    )
    parser.add_argument("--level", choices=("basic", "advanced", "all"), default="all")
    parser.add_argument("--subject", choices=("bot", "human"), default="bot")
    parser.add_argument("--player", help="the person's player name (--subject human)")
    parser.add_argument("--origin", type=int, nargs=3, default=(990, 80, 988))
    parser.add_argument(
        "--show-markers",
        action="store_true",
        dest="show_markers",
        help="bot: show its markers from the start (a person always sees the goal)",
    )
    parser.add_argument(
        "--minutes", type=float, default=10.0, help="human: time per challenge"
    )
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    challenges = sch.select(names=args.names, level=args.level)
    if args.list:
        for c in sch.CHALLENGES:
            print(f"{c.level:<9} {c.name:<10} {c.task}")
        return
    if args.subject == "human" and not args.player:
        parser.error("--subject human needs --player")
    if not 200 <= args.minutes * 1200 <= 24000:
        parser.error("--minutes must be 0.17 to 20 (the lab's tick limit)")
    folder = (
        ROOT / "results/scaffolding" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    folder.mkdir(parents=True)
    origin = tuple(args.origin)
    summary = {"subject": args.subject, "origin": list(origin), "challenges": {}}
    try:
        with exclusive_arena():
            for c in challenges:
                try:
                    if args.subject == "bot":
                        result = run_bot(folder / c.name, c, origin, args.show_markers)
                    else:
                        result = run_human(
                            folder / c.name, c, origin, args.player, args.minutes
                        )
                except (
                    Exception
                ) as error:  # one broken challenge does not hide the rest
                    result = {
                        "passed": False,
                        "problems": [f"{type(error).__name__}: {error}"],
                    }
                summary["challenges"][c.name] = result
                print(
                    f"{'PASS' if result['passed'] else 'FAIL'}  {c.name:<10} {', '.join(result['problems'])}",
                    flush=True,
                )
    finally:
        passed = sum(r["passed"] for r in summary["challenges"].values())
        summary["passed"] = passed == len(challenges)
        write_json(folder / "summary.json", summary)
        print(f"\nscaffold challenges: {passed}/{len(challenges)} passed -> {folder}")
        print(json.dumps({k: v for k, v in summary.items() if k != "challenges"}))
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
