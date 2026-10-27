"""Unscored live hill cases using ordinary Survival movement and interactions."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab.awareness import Awareness
from mclab.bootstrap import sha256
from mclab.execution import RecordedBridge
from mclab.memory_policy import MemoryPolicy
from mclab.modbuild import source_hash
from mclab.model_execution import drive
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
        choices=("walk", "cut", "place", "mixed", "prefetch", "all"),
        default="all",
    )
    parser.add_argument("--variants", type=int, default=1, choices=range(1, 11))
    parser.add_argument("--background-planning", action="store_true")
    parser.add_argument("--memory-profile", type=Path)
    parser.add_argument(
        "--terrain-encoding", choices=("cells", "sections-v1"), default="sections-v1"
    )
    args = parser.parse_args()
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
        "mod_provenance": provenance,
        "runner_sha256": sha256(Path(__file__)),
        "request_budget_per_attempt": 1000,
        "planning_mode": "background" if args.background_planning else "foreground",
        "memory_policy": memory_policy.payload(),
        "terrain_encoding": args.terrain_encoding,
        "attempts": [],
        "source_sha256": {
            name: sha256(ROOT / "src/mclab" / name)
            for name in (
                "navigation_segmented.py",
                "route_search.py",
                "spatial_memory.py",
                "spatial_archive.py",
                "section_codec.py",
                "memory_policy.py",
                "observation_worker.py",
                "terrain_transport.py",
                "bridge.py",
                "terrain_steps.py",
                "terrain_route.py",
                "planning_worker.py",
                "navigation.py",
                "awareness.py",
                "model_execution.py",
                "execution.py",
                "protocolproof.py",
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
                    client.budget = client.sequence + 1000
                    client.episode_id = "hill-" + uuid4().hex[:12]
                    client.body_id = "body-" + uuid4().hex[:12]
                    height = 4 if kind == "mixed" else 2 if kind == "place" else 3
                    if kind == "prefetch":
                        height = 0
                    goal = [1280, 80 + height, 1291]
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
                        start=[1280, 80, 1280],
                        goal=goal,
                        yaw=0,
                        goal_radius=1,
                        tick_limit=2400 if kind == "prefetch" else 1200,
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
                        allow_place=kind in {"place", "mixed"},
                    )
                    controller = SegmentedController(
                        goal,
                        memory,
                        radius=0.7,
                        terrain_policy=policy,
                        background_planning=args.background_planning,
                    )
                    try:
                        with _motor_gc_policy():
                            terminal = drive(
                                client, controller, 150 if kind == "prefetch" else 90
                            )
                        result = terminal["data"]
                        summary["attempts"].append(
                            {
                                "kind": kind,
                                "variant": variant,
                                "controller": controller.status(),
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
                        if result["status"] != "success":
                            raise RuntimeError("Hill arrival failed")
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
                        if kind not in {"place", "mixed"} and audit["added"] != 0:
                            raise RuntimeError("Forbidden hill placement")
                        if kind in {"cut", "mixed"} and audit["removed"] < 1:
                            raise RuntimeError("Cut hill completed without cutting")
                        if kind in {"place", "mixed"} and (
                            audit["added"] < 1
                            or result["kit_blocks_spent_adjusted"] != audit["added"]
                            or controller.edits_placed != audit["added"]
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
