"""Unscored live hill cases using ordinary Survival movement and interactions."""

import argparse
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab.awareness import Awareness
from mclab.bootstrap import sha256
from mclab.control_loop import LOCKSTEP, PROFILES, drive
from mclab.control_runtime import thread_switch_interval
from mclab.development_checks import (
    bridge_completed,
    drop_refused,
    gap_completed,
    pillar_completed,
    staircase_completed,
    water_completed,
)
from mclab.execution import RecordedBridge
from mclab.memory_policy import MemoryPolicy
from mclab.modbuild import source_hash
from mclab.movement_policy import MovementPolicy
from mclab.navigation_runner import _motor_gc_policy
from mclab.navigation_segmented import SegmentedController
from mclab.paths import ROOT, SERVER, read_json, write_json
from mclab.recorder import exclusive_arena
from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory
from mclab.terrain_steps import TerrainPolicy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        choices=(
            "walk",
            "cut",
            "place",
            "mixed",
            "prefetch",
            "slab",
            "stairs",
            "drop",
            "bridge",
            "staircase",
            "pillar",
            "gap",
            "water",
            "all",
        ),
        default="all",
    )
    parser.add_argument("--variants", type=int, default=1, choices=range(1, 11))
    parser.add_argument("--background-planning", action="store_true")
    parser.add_argument("--trace-processing", action="store_true")
    parser.add_argument("--diagonals", action="store_true")
    parser.add_argument("--sprint", action="store_true")
    parser.add_argument("--max-drop", type=int, choices=range(4), default=1)
    parser.add_argument("--max-gap", type=int, choices=range(3), default=0)
    parser.add_argument("--swim", action="store_true")
    parser.add_argument("--collision-shapes", action="store_true")
    parser.add_argument("--bridges", action="store_true")
    parser.add_argument("--pillars", action="store_true")
    parser.add_argument("--support-stack", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--no-place", action="store_true")
    parser.add_argument("--expect-refusal", action="store_true")
    parser.add_argument("--thread-switch-ms", type=float, choices=(0.5, 1.0, 2.0, 5.0))
    parser.add_argument("--memory-profile", type=Path)
    parser.add_argument(
        "--terrain-encoding", choices=("cells", "sections-v1"), default="sections-v1"
    )
    parser.add_argument("--control-profile", choices=PROFILES, default=LOCKSTEP)
    args = parser.parse_args()
    if args.expect_refusal and not (
        (args.kind == "drop" and args.max_drop == 0)
        or (args.kind == "bridge" and not args.bridges)
        or (args.kind == "staircase" and args.no_place)
        or (args.kind == "pillar" and (args.no_place or not args.pillars))
        or (args.kind == "gap" and args.max_gap == 0)
        or (args.kind == "water" and not args.swim)
    ):
        parser.error(
            "Expected refusal requires disabled drops, bridging or staircase placement"
        )
    movement_policy = MovementPolicy(
        args.diagonals,
        args.sprint,
        args.max_drop,
        args.collision_shapes,
        args.max_gap,
        args.swim,
    )
    memory_policy = (
        MemoryPolicy.from_payload(read_json(args.memory_profile))
        if args.memory_profile
        else MemoryPolicy()
    )
    if (
        "level-name=world"
        not in (SERVER / "server.properties").read_text().splitlines()
    ):
        raise RuntimeError("Hill probe requires the flat lab world")
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError("Rebuild/install the current server mod first")
    destination = (
        ROOT / "results/hills" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    summary = {
        "development_only": True,
        "expected_outcome": "refusal" if args.expect_refusal else "arrival",
        "mod_provenance": provenance,
        "runner_sha256": sha256(Path(__file__)),
        # Pipelined control sends a command every tick: one per tick plus slack.
        "request_budget_per_attempt": 1000
        if args.control_profile == LOCKSTEP
        else 4000,
        "control_profile": args.control_profile,
        "planning_mode": "background" if args.background_planning else "foreground",
        "memory_policy": memory_policy.payload(),
        "movement_policy": movement_policy.payload(),
        "terrain_encoding": args.terrain_encoding,
        "trace_processing": args.trace_processing,
        "thread_switch_ms": args.thread_switch_ms
        if args.thread_switch_ms is not None
        else sys.getswitchinterval() * 1000,
        "attempts": [],
        "source_sha256": {
            name: sha256(ROOT / "src/mclab" / name)
            for name in (
                "navigation_segmented.py",
                "route_search.py",
                "spatial_memory.py",
                "pathing_grid.py",
                "spatial_archive.py",
                "section_codec.py",
                "memory_policy.py",
                "movement_policy.py",
                "collision_geometry.py",
                "development_checks.py",
                "observation_worker.py",
                "terrain_transport.py",
                "bridge.py",
                "terrain_steps.py",
                "terrain_placement.py",
                "pillar_steps.py",
                "gap_jump.py",
                "water_movement.py",
                "terrain_route.py",
                "planning_worker.py",
                "navigation.py",
                "awareness.py",
                "model_execution.py",
                "control_loop.py",
                "native_driver.py",
                "execution.py",
                "protocolproof.py",
                "control_runtime.py",
            )
        },
    }
    kinds = ("walk", "cut", "place") if args.kind == "all" else (args.kind,)
    with exclusive_arena():
        client = RecordedBridge(destination / "events.jsonl")
        client.budget = 1000
        active = False
        try:
            client.synchronize()
            if client.http("/state")["body"]:
                raise RuntimeError("Hill probe requires an idle lab")
            selected = Awareness.resolve(
                "chunk",
                horizontal_radius_blocks=32 if args.kind == "prefetch" else 16,
                vertical_radius_blocks=1 if args.kind == "prefetch" else 8,
            )
            for kind in kinds:
                for variant in range(args.variants):
                    client.budget = (
                        client.sequence + summary["request_budget_per_attempt"]
                    )
                    client.episode_id = "hill-" + uuid4().hex[:12]
                    client.body_id = "body-" + uuid4().hex[:12]
                    height = (
                        4
                        if kind in {"mixed", "staircase"}
                        else 2
                        if kind == "place"
                        else 3
                    )
                    if kind in {"prefetch", "drop", "bridge", "gap", "water"}:
                        height = 0
                    if kind == "pillar":
                        height = 3 + variant % 2
                    start_height = 80 + (1 + variant % 3 if kind == "drop" else 0)
                    goal = [1280, 80 + height, 1291]
                    if kind == "staircase":
                        goal[2] = 1288 + variant
                    if kind == "pillar":
                        goal[2] = 1285 + variant // 2
                    if kind == "gap":
                        goal[2] = 1287 + variant // 2 + variant % 2
                    if kind == "water":
                        goal[2] = 1288 + variant // 2 + variant % 2
                    if kind == "prefetch":
                        goal = [
                            1280 + variant % 3,
                            80,
                            1320 + variant % 4 + 17 * (variant // 4),
                        ]
                    request = client.send(
                        "natural_start",
                        subject="carpet_body",
                        player="",
                        start=[
                            1280,
                            start_height,
                            1280,
                        ],
                        goal=goal,
                        yaw=0,
                        goal_radius=1,
                        tick_limit=200
                        if args.expect_refusal
                        else 2400
                        if kind == "prefetch"
                        else 1200,
                        mobs=False,
                        awareness=selected.payload(),
                        development_hill=kind,
                        hill_variant=variant,
                        terrain_encoding=args.terrain_encoding,
                    )
                    client.acknowledge(request, "natural_accepted")
                    active = True
                    client.wait(lambda state: state["state"] == "running", timeout=45)
                    folder = destination / f"{kind}-{variant}"
                    identity = MapIdentity(
                        str((SERVER / "world").resolve()),
                        client.episode_id,
                        "minecraft:overworld",
                        selected,
                    )
                    memory = SpatialMemory(
                        MapArchive(folder / "map.sqlite3", identity),
                        client.episode_id,
                        policy=memory_policy,
                    )
                    policy = TerrainPolicy(
                        allow_break=kind in {"cut", "mixed"},
                        allow_place=kind
                        in {"place", "mixed", "bridge", "staircase", "pillar"}
                        and not args.no_place,
                        allow_bridge=args.bridges,
                        allow_pillar=args.pillars,
                        max_support_stack=args.support_stack,
                    )
                    controller = SegmentedController(
                        goal,
                        memory,
                        radius=0.7,
                        terrain_policy=policy,
                        background_planning=args.background_planning,
                        movement_policy=movement_policy,
                    )
                    try:
                        with (
                            _motor_gc_policy(),
                            thread_switch_interval(args.thread_switch_ms),
                        ):
                            terminal = drive(
                                client,
                                controller,
                                150 if kind == "prefetch" else 90,
                                trace_processing=args.trace_processing,
                                profile=args.control_profile,
                            )
                        result = dict(terminal["data"])
                        if client.last_controller_error is not None:
                            result.update(
                                native_status=result["status"],
                                status="invalid",
                                controller_error=client.last_controller_error,
                            )
                        if args.expect_refusal:
                            result["refusal_verified"] = drop_refused(
                                result, start_height
                            )
                        if kind == "bridge" and not args.expect_refusal:
                            result["bridge_verified"] = bridge_completed(
                                result, variant, controller.edits_placed
                            )
                        if kind == "staircase" and not args.expect_refusal:
                            result["staircase_verified"] = staircase_completed(
                                result, variant, controller.edits_placed
                            )
                        if kind == "pillar" and not args.expect_refusal:
                            result["pillar_verified"] = pillar_completed(
                                result, variant, controller.edits_placed
                            )
                        if kind == "gap" and not args.expect_refusal:
                            result["gap_verified"] = gap_completed(
                                result, controller.gap_jumps_completed
                            )
                        if kind == "water" and not args.expect_refusal:
                            result["water_verified"] = water_completed(
                                result, variant, controller.air_recoveries_completed
                            )
                        summary["attempts"].append(
                            {
                                "kind": kind,
                                "variant": variant,
                                "episode_id": client.episode_id,
                                "controller": controller.status(),
                                "control_loop": getattr(
                                    client, "control_loop_summary", None
                                ),
                                "terrain_policy": asdict(policy),
                                **result,
                            }
                        )
                        write_json(destination / "summary.json", summary)
                        print(
                            kind,
                            variant,
                            result["status"],
                            result["reason"],
                            flush=True,
                        )
                        audit = result["development_hill"]
                        if args.expect_refusal:
                            if not result["refusal_verified"]:
                                raise RuntimeError(
                                    "Forbidden action was not safely refused"
                                )
                        elif result["status"] != "success":
                            raise RuntimeError("Hill arrival failed")
                        if (
                            kind == "bridge"
                            and not args.expect_refusal
                            and not result["bridge_verified"]
                        ):
                            raise RuntimeError(
                                "Bridge lacks exact no-fall geometry/stock proof"
                            )
                        if kind == "drop" and result["damage_taken"] != 0:
                            raise RuntimeError("No-damage drop caused damage")
                        if (
                            kind == "water"
                            and not args.expect_refusal
                            and not result["water_verified"]
                        ):
                            raise RuntimeError(
                                "Water lacks swimming, air, exit or no-edit proof"
                            )
                        if (
                            kind == "gap"
                            and not args.expect_refusal
                            and not result["gap_verified"]
                        ):
                            raise RuntimeError("Gap lacks no-fall/jump/no-edit proof")
                        if (
                            kind == "pillar"
                            and not args.expect_refusal
                            and not result["pillar_verified"]
                        ):
                            raise RuntimeError(
                                "Pillar lacks exact geometry/stock/ascent proof"
                            )
                        if (
                            kind == "staircase"
                            and not args.expect_refusal
                            and not result["staircase_verified"]
                        ):
                            raise RuntimeError(
                                "Staircase lacks exact geometry/stock/ascent proof"
                            )
                        if (
                            kind == "prefetch"
                            and args.background_planning
                            and controller.prefetch_adopted < 1
                        ):
                            raise RuntimeError(
                                "Long route reached without adopting "
                                "a prefetched segment"
                            )
                        if kind not in {"cut", "mixed"} and audit["removed"] != 0:
                            raise RuntimeError("Forbidden hill excavation")
                        if (
                            kind
                            not in {"place", "mixed", "bridge", "staircase", "pillar"}
                            and audit["added"] != 0
                        ):
                            raise RuntimeError("Forbidden hill placement")
                        if kind in {"cut", "mixed"} and audit["removed"] < 1:
                            raise RuntimeError("Cut hill completed without cutting")
                        if (
                            kind in {"place", "mixed", "bridge", "staircase", "pillar"}
                            and not args.expect_refusal
                            and (
                                audit["added"] < 1
                                or result["kit_blocks_spent_adjusted"] != audit["added"]
                                or controller.edits_placed != audit["added"]
                            )
                        ):
                            raise RuntimeError(
                                "Place hill lacks geometry/stock evidence"
                            )
                    finally:
                        try:
                            controller.close()
                        finally:
                            try:
                                memory.close()
                            finally:
                                # Cleanup is outside the attempt's control budget.
                                client.budget = max(client.budget, client.sequence + 50)
                                request = client.send("natural_stop")
                                client.acknowledge(request, "received")
                                client.wait(lambda state: state["state"] == "idle")
                                active = False
            summary["status"] = "passed"
        except Exception as error:
            summary.update(status="error", error=str(error))
            raise
        finally:
            try:
                client.budget = max(client.budget, client.sequence + 50)
                if active:
                    request = client.send("natural_stop")
                    client.acknowledge(request, "received")
                client.episode_id = "restore-" + uuid4().hex[:12]
                client.body_id = "body-" + uuid4().hex[:12]
                request = client.send(
                    "course_reset", profile_id="initial-c1-survival-v1"
                )
                client.acknowledge(request, "course_reset_complete", timeout=90)
                client.send("despawn")
                client.wait(lambda state: state["state"] == "idle")
                summary["lab_restored"] = True
            finally:
                client.close()
                write_json(destination / "summary.json", summary)
    print(destination / "summary.json", flush=True)


if __name__ == "__main__":
    main()
