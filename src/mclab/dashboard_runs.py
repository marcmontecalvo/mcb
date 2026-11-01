"""Adapters for the local UI; existing runners and files remain authoritative."""

import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from statistics import median

from mclab import companion_progress
from mclab.dashboard_metrics import compare_data
from mclab.paths import ROOT, read_json

PROFILE_TYPES = ("awareness", "movement", "memory", "terrain")
COMPANION_CHECKS = ("crafting", "placement", "animals", "containers")
COMPANION_JOBS = (*COMPANION_CHECKS, "gather", "build")
COORDINATE_LIMIT = 30_000_000
SCHEMATIC_DIRS = ("tests/fixtures/schematics", "builds")
BUILD_DIRS = (*SCHEMATIC_DIRS, "results/companion")
KILL_GRACE_S = 120


def project_file(relative):
    """Only explicit JSON inputs inside this checkout, never secrets or archives."""
    if not isinstance(relative, str):
        raise ValueError("Select a JSON file")
    path = (ROOT / relative).resolve()
    if (
        not path.is_relative_to(ROOT.resolve())
        or path.suffix != ".json"
        or any(p in {"sources", ".git", ".venv", "node_modules"} for p in path.parts)
        or not path.is_file()
    ):
        raise ValueError("Select an existing project JSON file")
    return path


def catalog():
    # Discover profiles by their original schema; do not create a UI config mirror.
    profiles = {key: [] for key in (*PROFILE_TYPES, "routes")}
    route_ids = {}
    from mclab.awareness import Awareness
    from mclab.memory_policy import MemoryPolicy
    from mclab.movement_policy import MovementPolicy
    from mclab.terrain_steps import TerrainPolicy

    validators = {
        "awareness": Awareness.from_payload,
        "memory": MemoryPolicy.from_payload,
        "movement": MovementPolicy.from_payload,
        "terrain": TerrainPolicy.from_payload,
    }
    candidates = set()
    for directory in (
        "config",
        "runtime",
        "docs/evidence",
        "results/natural",
        "results/spatial",
        "results/hills",
        "results/background",
        "results/awareness",
    ):
        # Root and one level below: exclude runner source archives and huge trees.
        base = ROOT / directory
        candidates.update(base.glob("*.json"))
        candidates.update(base.glob("*/*.json"))
    for path in sorted(candidates):
        if path.name in {
            "summary.json",
            "performance.json",
            "client-bridge.json",
        } or path.name.endswith("-summary.json"):
            continue
        try:
            value = read_json(path)
            if isinstance(value, list) or (
                isinstance(value, dict) and isinstance(value.get("routes"), list)
            ):
                routes = value if isinstance(value, list) else value["routes"]
                if routes and all(
                    isinstance(r, dict) and "route_id" in r for r in routes
                ):
                    relative = path.relative_to(ROOT).as_posix()
                    profiles["routes"].append(relative)
                    route_ids[relative] = [r["route_id"] for r in routes]
            if not isinstance(value, dict):
                continue
            for key, validate in validators.items():
                try:
                    validate(value)
                except (ValueError, TypeError, KeyError):
                    continue
                profiles[key].append(path.relative_to(ROOT).as_posix())
        except (OSError, ValueError):
            continue
    return {
        "profiles": profiles,
        "route_ids": route_ids,
        "backends": [
            {k: b.get(k) for k in ("name", "model", "request_limit", "deadline_s")}
            for b in read_json(ROOT / "config/backends.json")["backends"]
            if b.get("enabled")
        ],
        "courses": read_json(ROOT / "courses/profiles.json")["courses"],
        "companion": companion_catalog(),
        "config_files": [
            "config/lab.json",
            "courses/profiles.json",
            "config/backends.json",
            "config/navigation_body_profile.json",
            "config/initial_body_profile.json",
            "config/baselines.lock.json",
        ],
    }


def companion_files(suffix, directories):
    """Project-relative files with this suffix; results/companion is one level deep."""
    found = []
    for directory in directories:
        pattern = "*/*" if directory == "results/companion" else "*"
        found += [p for p in (ROOT / directory).glob(pattern + suffix) if p.is_file()]
    return sorted(p.relative_to(ROOT).as_posix() for p in found)


def companion_catalog():
    return {
        "checks": [
            name
            for name in COMPANION_CHECKS
            if (ROOT / "scripts" / script_name(name)).is_file()
        ],
        "builds": companion_files(".build.json", BUILD_DIRS),
        "schematics": companion_files(".litematic", SCHEMATIC_DIRS),
    }


def companion_file(relative, suffix, directories, what):
    """A file directly inside an allowed folder (resolved: no traversal or symlinks)."""
    if not isinstance(relative, str) or not relative.endswith(suffix):
        raise ValueError(f"Select a {what}")
    path = (ROOT / relative).resolve()
    root = ROOT.resolve()
    flat = {(root / d).resolve() for d in directories if d != "results/companion"}
    nested = (root / "results/companion").resolve()
    in_nested = "results/companion" in directories and path.parent.parent == nested
    if not path.is_file() or not (path.parent in flat or in_nested):
        raise ValueError(f"Select an existing {what} from the list")
    return path


def convert_schematic(relative):
    """mclab schematic convert, in-process: builds/<name>.build.json + material list."""
    from mclab import schematic

    source = companion_file(relative, ".litematic", SCHEMATIC_DIRS, "schematic")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", source.stem):
        raise ValueError("Schematic file name has unsupported characters")
    output = ROOT / "builds" / (source.stem + ".build.json")
    output.parent.mkdir(exist_ok=True)
    try:
        built = schematic.convert(source, output)
    except (OSError, ValueError, TypeError, KeyError, LookupError) as error:
        raise ValueError(f"Conversion failed: {error}") from error
    return {
        "output": output.relative_to(ROOT).as_posix(),
        "size": built["size"],
        "materials": built["materials"],
        "flags": built["flags"],
    }


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}")
    return value


def selection(value, allowed, name):
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(v, str) for v in value)
        or len(value) != len(set(value))
        or any(v not in allowed for v in value)
    ):
        raise ValueError(f"Select supported {name}")
    return value


def plan(request):
    """Validate a small, explicit run matrix before starting any world operations."""
    if not isinstance(request, dict):
        raise ValueError("Expected a run selection")
    kind = request.get("kind")
    jobs = []
    if kind == "setup":
        operation = request.get("operation")
        if operation not in {"server-start", "baritone-prepare", "baritone-launch"}:
            raise ValueError("Unknown setup action")
        return [
            {"kind": kind, "operation": operation, "entrant": operation, "expected": 0}
        ]
    if kind == "courses":
        entrants = selection(
            request.get("entrants"), {"v8_goal", "baritone"}, "entrants"
        )
        stages = selection(
            request.get("courses"), {"C1", "C2", "C3", "C4", "C5"}, "courses"
        )
        seeds = request.get("seeds")
        if not isinstance(seeds, list) or not 1 <= len(seeds) <= 20:
            raise ValueError("Select 1..20 distinct seeds")
        for seed in seeds:
            integer(seed, 0, 2**31 - 1, "Seed")
        if len(set(seeds)) != len(seeds):
            raise ValueError("Seeds must be distinct")
        if "C1" in stages and (entrants != ["v8_goal"] or seeds != [0]):
            raise ValueError(
                "C1 supports MCB deterministic only, seed 0; use C2-C5 for comparisons"
            )
        for stage in stages:
            for entrant in entrants:
                jobs.append(
                    {
                        "kind": kind,
                        "course": stage,
                        "entrant": entrant,
                        "seeds": seeds,
                        "expected": len(seeds),
                    }
                )
    elif kind == "natural":
        from mclab import natural
        from mclab.control_loop import PROFILES

        entrants = selection(
            request.get("entrants"),
            {"v8_goal", "segmented_goal", "baritone"},
            "entrants",
        )
        seed = integer(request.get("seed"), 0, 2**63 - 1, "World seed")
        routes_path = project_file(request.get("routes"))
        routes = selected_routes(read_json(routes_path), request.get("route_ids"))
        condition = request.get("condition", "calm")
        if condition not in {"calm", "mobs", "both"}:
            raise ValueError("Unknown condition")
        conditions = [False, True] if condition == "both" else [condition == "mobs"]
        natural._validate_run(seed, routes, entrants, conditions)
        repetitions = integer(request.get("repetitions", 1), 1, 3, "Repetitions")
        view = request.get("view_distance")
        if view is not None:
            integer(view, 2, 32, "View distance")
        show_markers = request.get("show_markers", False)
        if type(show_markers) is not bool:
            raise ValueError("Show markers must be true or false")
        stall = request.get("stall_seconds", 10)
        if type(stall) is not int or not (stall == 0 or 3 <= stall <= 120):
            raise ValueError("Stall timeout must be 0 (off) or 3..120 seconds")
        control = request.get("control_profile", "lockstep-three-tick-v1")
        if control not in PROFILES:
            raise ValueError("Unknown control profile")
        selected_profiles = {}
        from mclab.awareness import Awareness
        from mclab.memory_policy import MemoryPolicy
        from mclab.movement_policy import MovementPolicy
        from mclab.terrain_steps import TerrainPolicy

        for name, cls in (
            ("awareness", Awareness),
            ("movement", MovementPolicy),
            ("memory", MemoryPolicy),
            ("terrain", TerrainPolicy),
        ):
            relative = request.get(name + "_profile")
            if relative:
                path = project_file(relative)
                cls.from_payload(read_json(path))
                selected_profiles[name + "_profile"] = path.relative_to(ROOT).as_posix()
        for entrant in entrants:
            jobs.append(
                {
                    "kind": kind,
                    "entrant": entrant,
                    "seed": seed,
                    "routes": routes_path.relative_to(ROOT).as_posix(),
                    "route_ids": [r["route_id"] for r in routes],
                    "show_markers": show_markers,
                    "stall_seconds": stall,
                    "conditions": conditions,
                    "repetitions": repetitions,
                    "view_distance": view,
                    "control_profile": control
                    if entrant == "segmented_goal"
                    else "lockstep-three-tick-v1",
                    **{
                        k: v
                        for k, v in selected_profiles.items()
                        if k == "awareness_profile" or entrant == "segmented_goal"
                    },
                    "expected": len(routes) * len(conditions) * repetitions,
                }
            )
    elif kind == "models":
        specs = {
            b["name"]: b
            for b in read_json(ROOT / "config/backends.json")["backends"]
            if b.get("enabled")
        }
        for backend in selection(request.get("backends"), specs, "backends"):
            jobs.append(
                {"kind": kind, "backend": backend, "entrant": backend, "expected": 1}
            )
    elif kind == "companion":
        jobs.append(companion_job(request))
    else:
        raise ValueError("Unknown run type")
    if len(jobs) > 20:
        raise ValueError("Select at most 20 batches per session")
    return jobs


def script_name(name):
    if name == "gather":
        return "run-gather.py"
    return "run-build.py" if name == "build" else f"check-{name}.py"


def companion_job(request):
    name = request.get("job")
    if name not in COMPANION_JOBS:
        raise ValueError("Unknown companion job")
    if not (ROOT / "scripts" / script_name(name)).is_file():
        raise ValueError(f"scripts/{script_name(name)} does not exist yet")
    job = {"kind": "companion", "companion": name, "entrant": name, "expected": 0}
    args = []
    if name in {"gather", "build"}:
        start = request.get("start")
        if not isinstance(start, list) or len(start) != 3:
            raise ValueError("Enter start X Y Z")
        job["start"] = [
            integer(v, -COORDINATE_LIMIT, COORDINATE_LIMIT, "Start coordinate")
            for v in start
        ]
        args += ["--start", *map(str, job["start"])]
    if name == "gather":
        iron = request.get("iron", False)
        if type(iron) is not bool:
            raise ValueError("Iron must be true or false")
        job["iron"] = iron
        args += ["--iron"] if iron else []
        # The lab world is flat: gathering runs in a saved natural world.
        seed = request.get("seed", 1337)
        if seed is not None:
            from mclab import natural

            seed = integer(seed, -(2**63), 2**63 - 1, "Seed")
            if not (natural.SNAPSHOTS / natural._level(seed) / "level.dat").is_file():
                raise ValueError(f"No saved natural world for seed {seed}")
            job["seed"] = seed
            args += ["--seed", str(seed)]
    if name == "build":
        path = companion_file(request.get("build"), ".build.json", BUILD_DIRS, "build")
        dx = integer(request.get("dx", 3), -1000, 1000, "dx")
        dz = integer(request.get("dz", 2), -1000, 1000, "dz")
        rotation = request.get("rotation", 0)
        if type(rotation) is not int or rotation not in {0, 90, 180, 270}:
            raise ValueError("Rotation must be 0, 90, 180 or 270")
        job.update(
            build=path.relative_to(ROOT.resolve()).as_posix(),
            dx=dx,
            dz=dz,
            rotation=rotation,
        )
        args += ["--build", job["build"], "--dx", str(dx), "--dz", str(dz)]
        args += ["--rotation", str(rotation)]
    job["args"] = args
    return job


def selected_routes(value, route_ids):
    """The file's routes, or only the selected IDs in file order."""
    routes = value["routes"] if isinstance(value, dict) else value
    if route_ids is None:
        return routes
    known = {r["route_id"] for r in routes}
    selection(route_ids, known, "routes")
    return [r for r in routes if r["route_id"] in route_ids]


def folder(job):
    if job["kind"] == "setup":
        return "__setup_no_artifacts__"
    if job["kind"] == "natural":
        return "natural"
    if job["kind"] == "models":
        return "model_execution"
    if job["kind"] == "companion":
        return "companion"
    return (
        "execution"
        if job["course"] == "C1"
        else ("baritone" if job["entrant"] == "baritone" else "navigation")
    )


def setup(operation):
    from mclab import baritone_client, natural, server
    from mclab.recorder import exclusive_arena

    with exclusive_arena():
        if operation == "server-start":
            if server.status()["state"] != "running":
                server.start()
                natural._wait_state({"running"}, 300)
            result = {"state": server.status()["state"]}
        elif operation == "baritone-prepare":
            result = baritone_client.prepare()
        else:
            result = baritone_client.launch()
        return {"status": "completed_setup", "details": result}


def execute(job):
    """Pass original objects to original runners, including all admission checks."""
    if job["kind"] == "setup":
        return setup(job["operation"])
    if job["kind"] == "companion":
        return run_companion(job)
    if job["kind"] == "courses":
        if job["course"] == "C1":
            from mclab.execution import qualify_execution

            return qualify_execution(1, development=True)
        if job["entrant"] == "baritone":
            from mclab.baritone_runner import execute
        else:
            from mclab.navigation_runner import execute
        return execute(job["course"], job["seeds"])
    if job["kind"] == "models":
        from mclab.model_execution import run

        # Use the existing backend's cap, never raise it for the web launcher.
        spec = next(
            b
            for b in read_json(ROOT / "config/backends.json")["backends"]
            if b["name"] == job["backend"] and b.get("enabled")
        )
        return run(job["backend"], count=1, request_limit=spec["request_limit"])
    from mclab import natural
    from mclab.awareness import Awareness
    from mclab.memory_policy import MemoryPolicy
    from mclab.movement_policy import MovementPolicy
    from mclab.terrain_steps import TerrainPolicy

    routes = selected_routes(
        read_json(project_file(job["routes"])), job.get("route_ids")
    )
    profiles = {}
    for name, cls in (
        ("awareness", Awareness),
        ("memory", MemoryPolicy),
        ("movement", MovementPolicy),
        ("terrain", TerrainPolicy),
    ):
        if job.get(name + "_profile"):
            profiles[name if name == "awareness" else name + "_policy"] = (
                cls.from_payload(read_json(project_file(job[name + "_profile"])))
            )
    return natural.run(
        job["seed"],
        routes,
        entrants=(job["entrant"],),
        conditions=tuple(job["conditions"]),
        repetitions=job["repetitions"],
        view_distance=job["view_distance"],
        control_profile=job["control_profile"],
        show_markers=job.get("show_markers", False),
        stall_seconds=job.get("stall_seconds", 10),
        **profiles,
    )


# Windows: Ctrl+Break reaches the script as KeyboardInterrupt so its finally
# blocks despawn the body; elsewhere SIGINT does the same.
_SHIM = (
    "import os, runpy, signal, sys\n"
    "sig = getattr(signal, 'SIGBREAK', signal.SIGINT)\n"
    "signal.signal(sig, signal.default_int_handler)\n"
    "script = sys.argv[1]\n"
    "sys.argv = sys.argv[1:]\n"
    "sys.path.insert(0, os.path.dirname(os.path.abspath(script)))\n"
    "runpy.run_path(script, run_name='__main__')\n"
)


def stop_child(process):
    """Ask the script to unwind (it despawns its body), then force after a grace."""
    import signal

    try:
        process.send_signal(
            signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
        )
        process.wait(KILL_GRACE_S)
    except (subprocess.TimeoutExpired, OSError):
        process.kill()
        process.wait()


def companion_result_dir(job):
    """Newest results/companion folder the job's start did not already see."""
    seen = set(job.get("before", []))
    found = [
        p
        for p in (ROOT / "results/companion").glob("*")
        if p.is_dir() and p.relative_to(ROOT).as_posix() not in seen
    ]
    return max(found, key=lambda p: p.name, default=None)


def run_companion(job):
    """Run one companion script as a child process; its stdout is the session log."""
    from mclab import server

    if server.status()["state"] != "running":
        raise RuntimeError("The lab server is not running; use Start lab first")
    script = ROOT / "scripts" / script_name(job["companion"])
    if not script.is_file():
        raise RuntimeError(f"{script.name} not found")
    flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        if os.name == "nt"
        else 0
    )
    # Inherits the worker's stdout/stderr (console.log); -u keeps lines live.
    # The script takes the arena lock itself, so the worker must not.
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _SHIM, str(script), *job["args"]],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )
    try:
        while True:
            try:
                code = process.wait(0.5)
                break
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        stop_child(process)
        raise
    folder = companion_result_dir(job)
    if folder is not None and not (folder / companion_progress.JOB).exists():
        companion_progress.write_job(folder, job["companion"])
    if folder is None or not (folder / "summary.json").is_file():
        return {"error": f"{script.name} exited with code {code} before summary.json"}
    return {"status": "completed_companion", "returncode": code}


def companion_live(folder, events=12):
    """What a running companion job has produced so far (partial files tolerated)."""
    live = {"folder": folder.relative_to(ROOT).as_posix()}
    progress = companion_progress.read_progress(folder)
    if progress:
        live["progress"] = progress
    cases = companion_progress.read_json_file(folder / "summary.json")
    if isinstance(cases, list):
        live["cases"] = [
            {k: c.get(k) for k in ("case", "pass", "detail")}
            for c in cases
            if isinstance(c, dict)
        ]
    report = companion_progress.read_json_file(folder / "build.json")
    if isinstance(report, dict):
        live["report"] = {
            "stop": report.get("stop"),
            "steps": report.get("steps"),
            "counts": report.get("counts"),
            "skipped": len(report.get("skipped") or {}),
            "final_diff": report.get("final_diff"),
        }
    live["events"] = companion_events(folder / "events.jsonl", events)
    return live


def companion_events(path, count):
    """Last op results (X_done / X_failed / rejected) from a bounded tail."""
    from mclab.dashboard import tail

    rows = []
    for line in tail(path, 400_000).splitlines():
        try:
            row = companion_event(json.loads(line))
        except (ValueError, AttributeError, TypeError):
            continue  # truncated first line or an in-progress append
        if row:
            rows.append(row)
    return rows[-count:]


def companion_event(event):
    if event.get("type") != "server_event":
        return None
    payload = event["payload"]
    kind = payload.get("type", "")
    data = payload.get("data") or {}
    if kind.endswith(("_done", "_failed")):
        op, _, result = kind.rpartition("_")
    elif kind.startswith("rejected"):
        op, result = data.get("op", "?"), "rejected"
    else:
        return None
    return {
        "op": op,
        "result": result,
        "reason": data.get("reason"),
        "tick": payload.get("tick"),
    }


def summary_paths():
    # Original suite/run layout; never recurse into archived executable sources.
    return sorted((ROOT / "results").glob("*/*/summary.json"))


def result_rows(path, value):
    """Separate entrants in historical natural cohorts without altering evidence."""
    if isinstance(value, list) and all(
        isinstance(c, dict) and "case" in c and "pass" in c for c in value
    ):
        value = companion_summary(value, companion_progress.job_name(path.parent))
    if not isinstance(value, dict):
        raise ValueError("Run summary must be a JSON object")
    entrants = sorted(
        {
            r["entrant"]
            for r in value.get("attempts", [])
            if isinstance(r.get("entrant"), str)
        }
    )
    if entrants:
        return [
            result_row(
                path,
                {
                    **value,
                    "entrant": entrant,
                    "attempts": [
                        r for r in value["attempts"] if r.get("entrant") == entrant
                    ],
                },
            )
            for entrant in entrants
        ]
    return [result_row(path, value)]


def companion_summary(cases, job=None):
    """Companion scripts (check-*, run-*) write a plain list of cases."""
    passed = all(c["pass"] for c in cases)
    return {
        "suite": "companion",
        "controller": "mcb",
        "course_id": job or "companion check",
        "status": "passed" if passed else "failed",
        "checks": [{**c, "status": "passed" if c["pass"] else "failed"} for c in cases],
    }


def outcomes(value):
    for key in ("attempts", "episodes", "outcomes", "checks", "resets"):
        rows = value.get(key)
        if isinstance(rows, list):
            return rows
    if isinstance(value.get("outcome"), dict):
        row = dict(value["outcome"])
        if isinstance(value.get("controller"), dict):
            row["navigation"] = value["controller"]
        return [row]
    return []


def result_row(path, value):
    rows = outcomes(value)
    statuses = Counter(
        r.get("status", "passed" if r.get("passed") else "unknown") for r in rows
    )
    ticks = []
    for row in rows:
        tick = row.get("ticks")
        if tick is None and all(
            type(row.get(k)) is int for k in ("end_tick", "start_tick")
        ):
            tick = row["end_tick"] - row["start_tick"]
        if (
            row.get("status") == "success"
            and type(tick) in (int, float)
            and math.isfinite(tick)
        ):
            ticks.append(tick)
    body = value.get(
        "entrant", value.get("controller", value.get("backend", "unknown"))
    )
    if isinstance(body, dict):
        body = body.get("name", "unknown")
    return {
        "id": path.relative_to(ROOT).as_posix() + "::" + str(value.get("entrant", "")),
        "path": path.relative_to(ROOT).as_posix(),
        "date": path.parent.name,
        "suite": value.get("suite", path.parent.parent.name),
        "body": body,
        "stage": value.get("course_id", "natural" if "routes" in value else "unknown"),
        "status": value.get("status", "not declared"),
        "scored": value.get("scored", False),
        "attempted": len(rows),
        "statuses": dict(statuses),
        "median_ticks": median(ticks) if ticks else None,
        "comparison": compare_data(value, rows),
        "configuration": {
            k: v
            for k, v in value.items()
            if k not in {"attempts", "episodes", "outcomes", "checks", "resets"}
        },
    }
