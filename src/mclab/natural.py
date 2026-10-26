"""Natural-terrain comparison: Baritone (human client) vs V8 goal mode (Carpet body).

Development only. A generated world lives beside the qualified flat lab world; the
server is pointed at it by temporarily editing server.properties, and the lab
settings are always restored. Each attempt starts from the same world snapshot.
"""

import json
import math
import random
import shutil
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime

from mclab import baritone_client, server
from mclab.execution import RecordedBridge, terminal_event
from mclab.model_execution import ControllerFailure, drive
from mclab.navigation_goal import GoalController
from mclab.navigation_runner import _motor_gc_policy, _new_identity, _source_commit
from mclab.paths import ROOT, RUNTIME, SERVER, write_json

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
def natural_world(seed: int):
    """Point the stopped server at the natural world; always restore the lab world."""
    _stop()
    original = PROPERTIES.read_text()
    PROPERTIES.write_text(
        _set_properties(
            original,
            {
                "level-name": _level(seed),
                "level-type": "minecraft\\:normal",
                "level-seed": str(seed),
            },
        )
    )
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


def attempt(seed: int, route: dict, entrant: str, mobs: bool, destination) -> dict:
    _restore(seed)
    player = _relaunch_client() if entrant == "baritone" else None
    client = _bridge(destination / "events.jsonl")
    try:
        _new_identity(client, f"natural-{entrant}")
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
                with _motor_gc_policy():
                    terminal = drive(
                        client, GoalController(route["goal"], GOAL_RADIUS), wall
                    )
            except ControllerFailure as failure:
                error = str(failure)
                stop = client.send("natural_stop")
                client.acknowledge(stop, "received")
                terminal = terminal_event(client)
        outcome = dict(terminal["data"]) if terminal else {"status": "invalid"}
        if error:
            outcome.update(status="invalid", reason="controller_failure", error=error)
        return outcome
    finally:
        client.close()


def run(
    seed: int,
    routes: list[dict],
    entrants=("baritone", "v8_goal"),
    conditions=(False, True),
) -> dict:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = RESULTS / stamp
    summary = {
        "schema_version": 1,
        "suite": "natural_terrain_v1",
        "development_only": True,
        "seed": seed,
        "routes": routes,
        "goal_radius": GOAL_RADIUS,
        "tick_limit": TICK_LIMIT,
        "kit": "iron pickaxe, shovel, axe; 64 cobblestone; 64 dirt",
        "conditions": {
            "mobs_off": "noon, spawn_mobs false",
            "mobs_on": "midnight, spawn_mobs true",
        },
        "source_commit": _source_commit(),
        "attempts": [],
    }
    with natural_world(seed):
        for route in routes:
            for mobs in conditions:
                for entrant in entrants:
                    folder = (
                        destination
                        / f"{route['route_id']}-{'mobs' if mobs else 'calm'}-{entrant}"
                    )
                    try:
                        outcome = attempt(seed, route, entrant, mobs, folder)
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
                            **outcome,
                        }
                    )
                    write_json(destination / "summary.json", summary)
                    print(json.dumps(summary["attempts"][-1]), flush=True)
    return summary
