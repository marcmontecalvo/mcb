"""Natural-terrain comparison: Baritone (human client) vs V8 goal mode (Carpet body).

Development only. A generated world lives beside the qualified flat lab world; the
server is pointed at it by temporarily editing server.properties, and the lab
settings are always restored. Each attempt starts from the same world snapshot.
"""

import json
import math
import random
import re
import shutil
import subprocess
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from mclab import baritone_client, server
from mclab.awareness import Awareness
from mclab.bootstrap import sha256
from mclab.execution import RecordedBridge, terminal_event
from mclab.memory_policy import MemoryPolicy
from mclab.modbuild import source_hash
from mclab.model_execution import ControllerFailure, drive
from mclab.movement_policy import MovementPolicy
from mclab.navigation_goal import GoalController
from mclab.navigation_runner import _motor_gc_policy, _new_identity, _source_commit
from mclab.navigation_segmented import SegmentedController
from mclab.paths import ROOT, RUNTIME, SERVER, read_json, write_json
from mclab.recorder import arena_guard
from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory
from mclab.terrain_steps import TerrainPolicy

PROPERTIES = SERVER / "server.properties"
SNAPSHOTS = RUNTIME / "natural"
RESULTS = ROOT / "results/natural"
PROBE_RADIUS = 160
PROBE_STRIDE = 4
ROUTE_MIN, ROUTE_MAX = 100, 150
GOAL_RADIUS = 3
TICK_LIMIT = 6000  # five minutes of game time
POLL_SECONDS = 0.25


def _level(seed: int) -> str:
    return f"natural-{seed}"


def _set_properties(text: str, values: dict) -> str:
    lines = text.splitlines()
    for key, value in values.items():
        lines = [
            f"{key}={value}" if line.startswith(key + "=") else line for line in lines
        ]
    return "\n".join(lines) + "\n"


def _wait_state(wanted: set[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.status()["state"] in wanted:
            return
        time.sleep(2)
    raise TimeoutError(f"Server did not reach {wanted}")


def _stop() -> None:
    server.stop()
    _wait_state({"stopped", "exited"}, 120)


def _start() -> None:
    server.start()
    _wait_state({"running"}, 300)


@contextmanager
def natural_world(seed: int, view_distance: int | None = None):
    """Point the stopped server at the natural world; always restore the lab world."""
    _stop()
    original = PROPERTIES.read_text()
    values = {
        "level-name": _level(seed),
        "level-type": "minecraft\\:normal",
        "level-seed": str(seed),
    }
    if view_distance is not None:
        values["view-distance"] = str(view_distance)
    PROPERTIES.write_text(_set_properties(original, values))
    try:
        yield
    finally:
        _stop()
        PROPERTIES.write_text(original)


def _bridge(path):
    client = RecordedBridge(path)
    client.budget = 10**6  # ponytail: development runs; bounded by tick limits instead
    client.synchronize()
    return client


def probe(seed: int) -> list:
    """Generate the world and read its surface around spawn; snapshot it after."""
    destination = RESULTS / "seeds" / f"{seed}.json"
    with natural_world(seed):
        _start()
        client = _bridge(RESULTS / "seeds" / f"{seed}-{time.time_ns()}.jsonl")
        try:
            request = client.send(
                "natural_probe",
                center_x=0,
                center_z=0,
                radius=PROBE_RADIUS,
                stride=PROBE_STRIDE,
            )
            cells = client.acknowledge(request, "natural_probe", timeout=240)["data"][
                "cells"
            ]
        finally:
            client.close()
    snapshot = SNAPSHOTS / _level(seed)
    if snapshot.exists():
        shutil.rmtree(snapshot)
    shutil.copytree(SERVER / _level(seed), snapshot)
    write_json(destination, {"seed": seed, "cells": cells})
    return cells


def _grid(cells):
    return {(x, z): (h, water, block) for x, z, h, water, block in cells}


def ruggedness(cells) -> dict:
    grid = _grid(cells)
    steps, water = [], 0
    for (x, z), (h, wet, _) in grid.items():
        water += wet
        for nx, nz in ((x + PROBE_STRIDE, z), (x, z + PROBE_STRIDE)):
            if (nx, nz) in grid and not wet and not grid[(nx, nz)][1]:
                steps.append(abs(grid[(nx, nz)][0] - h))
    land = 1 - water / len(grid)
    mean = sum(steps) / max(1, len(steps))
    cliffs = sum(s >= 5 for s in steps)
    return {
        "land_fraction": land,
        "mean_step": mean,
        "cliffs": cliffs,
        "score": (mean + cliffs / 100) * land,
    }


def _endpoint(grid, x, z):
    value = grid.get((x, z))
    if value is None or value[1] or "log" in value[2] or "leaves" in value[2]:
        return None
    # Stand on locally level ground so the spawn is not on a pillar or ledge lip.
    neighbors = [
        grid.get((x + dx, z + dz)) for dx, dz in ((4, 0), (-4, 0), (0, 4), (0, -4))
    ]
    if any(n is None or abs(n[0] - value[0]) > 2 for n in neighbors):
        return None
    return value[0]


def _profile(grid, start, goal):
    length = math.dist(start, goal)
    samples = int(length // PROBE_STRIDE)
    heights, wet = [], 0
    for i in range(samples + 1):
        x = start[0] + (goal[0] - start[0]) * i / samples
        z = start[1] + (goal[1] - start[1]) * i / samples
        key = (
            round(x / PROBE_STRIDE) * PROBE_STRIDE,
            round(z / PROBE_STRIDE) * PROBE_STRIDE,
        )
        h, water, _ = grid[key]
        heights.append(h)
        wet += water
    deltas = [b - a for a, b in zip(heights, heights[1:], strict=False)]
    return {
        "length": round(length, 1),
        "climb": sum(d for d in deltas if d > 0),
        "descent": -sum(d for d in deltas if d < 0),
        "max_step": max(abs(d) for d in deltas),
        "big_steps": sum(abs(d) >= 4 for d in deltas),
        "water_fraction": wet / len(heights),
        "relief": max(heights) - min(heights),
    }


def pick_routes(cells, count=5, rng_seed=0) -> list[dict]:
    """Most rugged straight-line routes with dry, level endpoints, spread apart."""
    grid = _grid(cells)
    keys = list(grid)
    rng = random.Random(rng_seed)
    candidates = []
    for _ in range(40000):
        sx, sz = rng.choice(keys)
        angle = rng.uniform(0, 2 * math.pi)
        length = rng.uniform(ROUTE_MIN, ROUTE_MAX)
        gx = round((sx + math.cos(angle) * length) / PROBE_STRIDE) * PROBE_STRIDE
        gz = round((sz + math.sin(angle) * length) / PROBE_STRIDE) * PROBE_STRIDE
        if (gx, gz) not in grid:
            continue
        sh, gh = _endpoint(grid, sx, sz), _endpoint(grid, gx, gz)
        if sh is None or gh is None:
            continue
        profile = _profile(grid, (sx, sz), (gx, gz))
        if (
            profile["water_fraction"] > 0.1
            or not ROUTE_MIN <= profile["length"] <= ROUTE_MAX
        ):
            continue
        score = profile["climb"] + profile["descent"] + 5 * profile["big_steps"]
        candidates.append((score, (sx, sh, sz), (gx, gh, gz), profile))
    candidates.sort(key=lambda c: -c[0])
    routes = []
    for score, start, goal, profile in candidates:
        if all(
            math.dist(start[::2], r["start"][::2]) >= 48
            and math.dist(goal[::2], r["goal"][::2]) >= 48
            for r in routes
        ):
            yaw = math.degrees(math.atan2(-(goal[0] - start[0]), goal[2] - start[2]))
            routes.append(
                {
                    "route_id": f"R{len(routes) + 1}",
                    "start": list(start),
                    "goal": list(goal),
                    # Both bots start facing the goal; heading search is not under test.
                    "yaw": round(yaw, 1) if yaw < 180 else -180.0,
                    "score": score,
                    **profile,
                }
            )
        if len(routes) == count:
            break
    return routes


def _restore(seed: int) -> None:
    _stop()
    live = SERVER / _level(seed)
    if live.exists():
        shutil.rmtree(live)
    shutil.copytree(SNAPSHOTS / _level(seed), live)
    _start()


def _baritone_cache() -> Path:
    port = baritone_client.config()["game_port"]
    return (
        baritone_client.prism_dir()
        / "instances"
        / baritone_client.INSTANCE_ID
        / f"minecraft/baritone/127.0.0.1_{port}"
    )


def _server_view_distance() -> int:
    match = re.search(r"^view-distance=(\d+)$", PROPERTIES.read_text(), re.M)
    return int(match.group(1)) if match else 10  # vanilla default


def _relaunch_client() -> str:
    """Reconnect the Baritone client after a server restart; returns the player name."""
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='javaw.exe'\" | "
            "? { $_.CommandLine -match 'mclab-baritone' } | "
            "% { Stop-Process -Id $_.ProcessId -Force }",
        ],
        check=True,
    )
    time.sleep(3)
    # Parity: MCB gets a fresh map each attempt, so Baritone must not path from
    # its on-disk chunk cache of earlier attempts (or the flat lab world).
    shutil.rmtree(_baritone_cache(), ignore_errors=True)
    if _baritone_cache().exists():
        raise RuntimeError("Could not clear the Baritone chunk cache")
    baritone_client.launch()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        time.sleep(5)
        try:
            status = baritone_client.BaritoneClient().status()
        except (OSError, RuntimeError):
            continue
        if status.get("in_world"):
            time.sleep(5)  # let the client finish loading nearby chunks
            return status["player"]
    raise TimeoutError("Baritone client did not join the natural world")


def _wait_terminal(client, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client.state(heartbeat=True)
        terminal = terminal_event(client)
        if terminal is not None:
            return terminal
        time.sleep(POLL_SECONDS)
    raise TimeoutError("Natural attempt exceeded its wall-clock deadline")


def attempt(
    seed: int,
    route: dict,
    entrant: str,
    mobs: bool,
    destination,
    *,
    awareness=None,
    terrain_policy=None,
    memory_policy=None,
    movement_policy=None,
) -> dict:
    awareness = Awareness() if awareness is None else awareness
    _restore(seed)
    player = _relaunch_client() if entrant == "baritone" else None
    client = _bridge(destination / "events.jsonl")
    try:
        _new_identity(client, f"natural-{entrant}")
        # The wire payload in each policy_observation is already replayable. The
        # expanded-cell recorder rewrites the whole accumulated map every 20th
        # observation inside the control loop; at chunk radius 64 that reached
        # 29 MB and caused late actions (Phase 0 run 20261028T025756029115Z).
        client.policy_memory = None
        request = client.send(
            "natural_start",
            subject="human" if entrant == "baritone" else "carpet_body",
            player=player or "",
            start=route["start"],
            goal=route["goal"],
            yaw=route["yaw"],
            goal_radius=GOAL_RADIUS,
            tick_limit=TICK_LIMIT,
            mobs=mobs,
            awareness=awareness.payload()
            if entrant != "baritone"
            else Awareness().payload(),
            terrain_encoding="sections-v1"
            if entrant == "segmented_goal" and awareness.preset != "legacy"
            else "cells",
        )
        client.acknowledge(request, "natural_accepted", timeout=10)
        client.wait(
            lambda _: any(
                e["type"] == "course_started"
                and e.get("episode_id") == client.episode_id
                for e in client.events
            ),
            timeout=60,
        )
        wall = TICK_LIMIT / 20 + 60
        error = None
        if entrant == "baritone":
            bot = baritone_client.BaritoneClient()
            bot.policy(allowBreak=True, allowPlace=True, allowInventory=False)
            bot.goto(*route["goal"])
            try:
                terminal = _wait_terminal(client, wall)
            finally:
                bot.cancel()
        else:
            # The start event can precede the next policy snapshot by a tick.
            deadline = time.monotonic() + 5
            while client.http("/policy")["state"] != "running":
                if time.monotonic() > deadline:
                    raise RuntimeError("Policy stream did not reach running")
                time.sleep(0.05)
            try:
                with ExitStack() as resources, _motor_gc_policy():
                    controller = GoalController(
                        route["goal"], GOAL_RADIUS, awareness=awareness
                    )
                    if entrant == "segmented_goal":
                        # Each snapshot restore creates an isolated map generation.
                        # Never reuse geometry from a preceding benchmark attempt.
                        memory = SpatialMemory(
                            MapArchive(
                                destination / "map.sqlite3",
                                MapIdentity(
                                    str((SNAPSHOTS / _level(seed)).resolve()),
                                    client.episode_id,
                                    "minecraft:overworld",
                                    awareness,
                                ),
                            ),
                            client.episode_id,
                            policy=memory_policy,
                        )
                        resources.callback(memory.close)
                        controller = SegmentedController(
                            route["goal"],
                            memory,
                            radius=GOAL_RADIUS - 0.5,
                            terrain_policy=terrain_policy or TerrainPolicy(),
                            background_planning=True,
                            movement_policy=movement_policy,
                        )
                        resources.callback(controller.close)
                    terminal = drive(
                        client,
                        controller,
                        wall,
                    )
                    navigation_status = (
                        controller.status() if entrant == "segmented_goal" else None
                    )
            except ControllerFailure as failure:
                error = str(failure)
                stop = client.send("natural_stop")
                client.acknowledge(stop, "received")
                terminal = terminal_event(client)
        outcome = dict(terminal["data"]) if terminal else {"status": "invalid"}
        if error:
            outcome.update(status="invalid", reason="controller_failure", error=error)
        if entrant != "baritone" and getattr(client, "last_controller_error", None):
            outcome.update(
                native_status=outcome["status"],
                status="invalid",
                controller_error=client.last_controller_error,
            )
        if entrant == "segmented_goal" and not error:
            outcome["navigation"] = navigation_status
        return outcome
    finally:
        client.close()


@arena_guard
def run(
    seed: int,
    routes: list[dict],
    entrants=("baritone", "v8_goal"),
    conditions=(False, True),
    *,
    awareness=None,
    terrain_policy=None,
    memory_policy=None,
    movement_policy=None,
    repetitions=1,
    view_distance=None,
) -> dict:
    awareness = Awareness() if awareness is None else awareness
    _validate_run(seed, routes, entrants, conditions)
    if type(repetitions) is not int or not 1 <= repetitions <= 3:
        raise ValueError("Natural repetitions must be between one and three")
    if view_distance is not None and (
        type(view_distance) is not int or not 2 <= view_distance <= 32
    ):
        raise ValueError("Server view distance must be between 2 and 32 chunks")
    if movement_policy is not None and (
        not isinstance(movement_policy, MovementPolicy)
        or "segmented_goal" not in entrants
    ):
        raise ValueError("Movement policy requires a segmented_goal entrant")
    if memory_policy is not None and (
        not isinstance(memory_policy, MemoryPolicy) or "segmented_goal" not in entrants
    ):
        raise ValueError("Memory policy requires only the segmented_goal entrant")
    if terrain_policy is not None and (
        not isinstance(terrain_policy, TerrainPolicy)
        or "segmented_goal" not in entrants
    ):
        raise ValueError("Terrain policy requires only the segmented_goal entrant")
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError("Rebuild and install the current natural-terrain mod first")
    if not (SNAPSHOTS / _level(seed) / "level.dat").is_file():
        raise ValueError(
            "Natural-world snapshot missing; probe and snapshot this seed first"
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = RESULTS / stamp
    summary = {
        "schema_version": 1,
        "suite": "natural_terrain_v1",
        "development_only": True,
        "seed": seed,
        "repetitions": repetitions,
        "routes": routes,
        "goal_radius": GOAL_RADIUS,
        "tick_limit": TICK_LIMIT,
        "kit": "iron pickaxe, shovel, axe; 64 cobblestone; 64 dirt",
        "conditions": {
            "mobs_off": "noon, spawn_mobs false",
            "mobs_on": "midnight, spawn_mobs true",
        },
        "source_commit": _source_commit(),
        "mod_provenance": provenance,
        "runner_sha256": sha256(Path(__file__)),
        "controller_sha256": sha256(ROOT / "src/mclab/navigation_goal.py"),
        "navigation_sha256": sha256(ROOT / "src/mclab/navigation.py"),
        "segmented_source_sha256": {
            name: sha256(ROOT / "src/mclab" / name)
            for name in (
                "navigation_segmented.py",
                "route_search.py",
                "spatial_memory.py",
                "spatial_archive.py",
                "section_codec.py",
                "memory_policy.py",
                "movement_policy.py",
                "collision_geometry.py",
                "model_execution.py",
                "control_runtime.py",
                "awareness.py",
                "observation_worker.py",
                "terrain_transport.py",
                "bridge.py",
                "execution.py",
                "terrain_route.py",
                "planning_worker.py",
                "terrain_steps.py",
                "terrain_placement.py",
                "pillar_steps.py",
                "gap_jump.py",
                "water_movement.py",
            )
        },
        "mcb_awareness": awareness.payload(),
        "movement_policy": (movement_policy or MovementPolicy()).payload()
        if "segmented_goal" in entrants
        else None,
        "mcb_terrain_encoding": "sections-v1"
        if "segmented_goal" in entrants and awareness.preset != "legacy"
        else "cells",
        "memory_policy": (memory_policy or MemoryPolicy()).payload()
        if "segmented_goal" in entrants
        else None,
        "terrain_policy": asdict(terrain_policy or TerrainPolicy())
        if "segmented_goal" in entrants
        else None,
        "baritone_awareness": "native client chunks within server view distance; "
        "on-disk chunk cache cleared before each attempt",
        "server_view_distance_chunks": view_distance or _server_view_distance(),
        "policy_map_recording": "wire payloads only; no expanded-cell snapshots",
        "attempts": [],
    }
    with natural_world(seed, view_distance):
        for route in routes:
            for mobs, repetition, entrant in (
                (mobs, repetition, entrant)
                for mobs in conditions
                for repetition in range(repetitions)
                for entrant in entrants
            ):
                folder = destination / (
                    f"{route['route_id']}-{'mobs' if mobs else 'calm'}-"
                    f"{entrant}-{repetition + 1}"
                )
                try:
                    outcome = attempt(
                        seed,
                        route,
                        entrant,
                        mobs,
                        folder,
                        awareness=awareness,
                        terrain_policy=terrain_policy,
                        memory_policy=memory_policy,
                        movement_policy=movement_policy,
                    )
                except Exception as error:  # keep going; record the failure
                    outcome = {
                        "status": "invalid",
                        "reason": type(error).__name__,
                        "error": str(error),
                    }
                summary["attempts"].append(
                    {
                        "route_id": route["route_id"],
                        "mobs": mobs,
                        "entrant": entrant,
                        "repetition": repetition + 1,
                        **outcome,
                    }
                )
                write_json(destination / "summary.json", summary)
                print(json.dumps(summary["attempts"][-1]), flush=True)
    return summary


def _validate_run(seed, routes, entrants, conditions):
    """Reject malformed input before restarting servers or restoring world files."""
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Natural seed must be a nonnegative signed 64-bit integer")
    if not isinstance(routes, list) or not 1 <= len(routes) <= 10:
        raise ValueError("Select 1..10 explicit natural routes")
    if not entrants or any(
        e not in {"v8_goal", "segmented_goal", "baritone"} for e in entrants
    ):
        raise ValueError("Unknown natural entrant")
    if not conditions or any(type(c) is not bool for c in conditions):
        raise ValueError("Natural conditions must be booleans")
    identifiers = set()
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("Invalid natural route")
        name = route.get("route_id")
        if not isinstance(name, str) or not re.fullmatch(r"R[1-9][0-9]*", name):
            raise ValueError("Invalid natural route ID")
        if name in identifiers:
            raise ValueError("Duplicate natural route ID")
        identifiers.add(name)
        for key in ("start", "goal"):
            point = route.get(key)
            if (
                not isinstance(point, list)
                or len(point) != 3
                or any(type(v) is not int or abs(v) > 29_000_000 for v in point)
            ):
                raise ValueError("Invalid natural route coordinates")
        yaw = route.get("yaw")
        if (
            type(yaw) not in {int, float}
            or not math.isfinite(yaw)
            or not -180 <= yaw < 180
        ):
            raise ValueError("Invalid natural route yaw")
        if math.dist(route["start"], route["goal"]) > 384:
            raise ValueError("Natural route exceeds server distance limit")
