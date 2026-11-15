"""Reference-based test selections expanded into the existing dashboard jobs."""

from __future__ import annotations

import copy
import json
import os
import re
import runpy
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from mclab.paths import ROOT, read_json
from mclab.run_config import resolve_config
from mclab.run_launch import CHECKS, prepare_config, validate_launch

MAX_JOBS = 60
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


def battery_checks():
    """Read the battery's real fixed commands; do not maintain a second list."""
    return runpy.run_path(str(ROOT / "scripts/run-battery.py"))["CHECKS"]


def test_catalog():
    """Small defaults plus canonical runner identity, never a second settings store."""
    from mclab import dashboard_runs as runs

    rows = []

    def add(test_id, label, family, overrides=None, description=""):
        rows.append(
            {
                "id": test_id,
                "label": label,
                "family": family,
                "description": description,
                "default_config": resolve_config(
                    family, overrides=overrides, root=ROOT
                ),
            }
        )

    for name in sorted(CHECKS):
        add(
            f"companion:{name}",
            name.replace("-", " ").title(),
            "companion",
            {"run": {"check": name}},
            "Uses the existing check and its original control path.",
        )
    add("gather", "Gather", "gather", {"run": {"start": runs.COMPANION_DEFAULT_START}})
    add("build", "Build", "build", {"run": {"start": runs.COMPANION_DEFAULT_START}})
    for course in ("C1", "C2", "C3", "C4", "C5"):
        add(
            f"course:{course}",
            f"{course} · MCB",
            "courses",
            {
                "run": {
                    "course": course,
                    "development": course == "C1",
                    **({} if course == "C1" else {"seeds": [0]}),
                }
            },
        )
        if course != "C1":
            add(
                f"course:{course}:baritone",
                f"{course} · Baritone",
                "courses",
                {
                    "run": {"course": course, "entrant": "baritone", "seeds": [0]},
                },
            )
    add(
        "course:C1:reset",
        "C1 reset qualification",
        "courses",
        {
            "run": {"course": "C1", "operation": "reset"},
        },
    )
    add(
        "course:C1:episodes",
        "C1 episode checks",
        "courses",
        {
            "run": {"course": "C1", "operation": "episodes"},
        },
    )
    add(
        "natural",
        "Natural routes",
        "natural",
        {"run": {"entrants": ["segmented_goal"]}},
        "Choose a saved seed and supply route_data before launching.",
    )
    backends = read_json(ROOT / "config/backends.json")["backends"]
    for backend in backends:
        if backend.get("enabled"):
            add(
                f"model:{backend['name']}",
                f"Model · {backend['name']}",
                "models",
                {
                    "model": {
                        "backend": backend["name"],
                        "request_limit": backend["request_limit"],
                    },
                },
            )
    add(
        "battery",
        "Full regression battery",
        "battery",
        description="Uses the current CLI battery checks.",
    )
    for name, (command, timeout) in battery_checks().items():
        # Retain the battery's timeout, standstill verdict and cleanup behavior.
        add(
            f"battery:{name}",
            f"Battery · {name}",
            "battery",
            {
                "run": {"checks": [name]},
            },
            f"Original battery command; {timeout}s default timeout.",
        )
        rows[-1]["command"] = command
        rows[-1]["timeout"] = timeout
    for row in rows:
        locked = ["run.test"]
        if row["family"] == "companion":
            locked += ["run.check"]
        elif row["family"] == "courses":
            locked += ["run.course", "run.entrant", "run.operation"]
        elif row["family"] == "models":
            locked += ["model.backend"]
        elif row["id"].startswith("battery:"):
            locked += ["run.checks"]
        row["locked_fields"] = locked
    return rows


def _resolved_entry(test, entry):
    if entry["preset"] is not None and entry["preset"] not in {
        row["id"] for row in presets()
    }:
        raise ValueError("Unknown or unsafe preset reference")
    config = resolve_config(
        test["family"],
        defaults=test["default_config"],
        preset=entry["preset"],
        overrides=entry["overrides"],
        root=ROOT,
    )
    for path in test.get("locked_fields", ["run.test"]):
        section, field = path.split(".")
        if config[section][field] != test["default_config"][section][field]:
            raise ValueError(f"Select another catalog test to change {path}")
    return config


def presets():
    from mclab.run_config import TESTS

    result = []
    folder = (ROOT / "config/presets").resolve()
    if not folder.is_relative_to(ROOT.resolve()):
        raise ValueError("Preset directory must stay inside the checkout")
    for path in sorted(folder.glob("*.json")):
        if path.is_symlink() or not _SLUG.fullmatch(path.stem):
            continue
        value = read_json(path)
        if not isinstance(value, dict):
            raise ValueError(f"Preset must be an object: {path.name}")
        supported = []
        for family in TESTS:
            try:
                resolve_config(
                    family,
                    base=value,
                    root=ROOT,
                    defaults={"run": {"entrants": ["segmented_goal"]}}
                    if family == "natural"
                    else None,
                )
                supported.append(family)
            except ValueError:
                continue
        if not supported:
            raise ValueError(f"Preset has no supported run family: {path.name}")
        result.append(
            {
                "id": path.stem,
                "label": path.stem.replace("-", " ").title(),
                "config": value,
                "supported_tests": supported,
            }
        )
    return result


def _entry(entry):
    if not isinstance(entry, dict) or set(entry) - {
        "test_id",
        "preset",
        "overrides",
        "seeds",
        "repetitions",
    }:
        raise ValueError("Invalid suite entry")
    if not isinstance(entry.get("test_id"), str):
        raise ValueError("Choose a catalog test")
    preset = entry.get("preset")
    if preset is not None and (
        not isinstance(preset, str) or not _SLUG.fullmatch(preset)
    ):
        raise ValueError("Choose a preset name, not a path")
    overrides = entry.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("Overrides must be a partial run config")
    repeats = entry.get("repetitions", 1)
    if type(repeats) is not int or not 1 <= repeats <= 3:
        raise ValueError("Queue repetitions must be 1..3")
    seeds = entry.get("seeds")
    if seeds is not None and (
        not isinstance(seeds, list)
        or not 1 <= len(seeds) <= 20
        or any(type(seed) is not int for seed in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise ValueError("Choose 1..20 distinct integer seeds")
    return {
        "test_id": entry["test_id"],
        "preset": preset,
        "overrides": overrides,
        "seeds": seeds,
        "repetitions": repeats,
    }


def resolve_entries(entries):
    """Validate the entire mixed queue before the detached worker can start."""
    from mclab import dashboard_runs as runs

    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_JOBS:
        raise ValueError(f"Choose 1..{MAX_JOBS} tests")
    tests = {test["id"]: test for test in test_catalog()}
    jobs = []
    for raw in entries:
        entry = _entry(raw)
        if entry["test_id"] not in tests:
            raise ValueError("Catalog test is no longer available")
        test = tests[entry["test_id"]]
        family = test["family"]
        if entry["seeds"] is not None and family not in {
            "gather",
            "build",
            "courses",
            "natural",
        }:
            raise ValueError("This test does not accept a seed matrix")
        # Defaults are the lowest layer. Presets must beat catalog defaults, and
        # explicit edits beat both; do not save a UI-owned full-config mirror.
        base = _resolved_entry(test, entry)
        if base["run"]["test"] != family:
            raise ValueError("Overrides cannot change the selected test family")
        for seed in entry["seeds"] or [None]:
            for repeat in range(entry["repetitions"]):
                if len(jobs) >= MAX_JOBS:
                    raise ValueError(f"Expanded queue exceeds {MAX_JOBS} jobs")
                config = copy.deepcopy(base)
                if seed is not None:
                    if family == "courses" and config["run"]["operation"] == "execute":
                        config["run"]["seeds"] = [seed]
                    else:
                        config["run"]["seed"] = seed
                    if family == "courses" and config["run"]["operation"] != "execute":
                        config["run"]["seeds"] = None
                if family == "battery" and not config["run"]["output"]:
                    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                    config["run"]["output"] = (
                        f"results/batteries/{stamp}-{len(jobs) + 1}"
                    )
                config = prepare_config(resolve_config(family, base=config, root=ROOT))
                validate_launch(config)
                if family == "natural":
                    from mclab.natural import _validate_run

                    _validate_run(
                        config["run"]["seed"],
                        config["run"]["route_data"],
                        config["run"]["entrants"],
                        config["run"]["conditions"],
                    )
                planned = runs.plan({"kind": family, "run_config": config})
                if len(jobs) + len(planned) > MAX_JOBS:
                    raise ValueError(f"Expanded queue exceeds {MAX_JOBS} jobs")
                for job in planned:
                    job.update(
                        id=f"job-{len(jobs) + 1}",
                        label=test["label"],
                        test_id=test["id"],
                        repetition=repeat + 1,
                    )
                    job["suite_entry"] = entry
                    if entry["preset"]:
                        job.setdefault("input_files", {})["preset"] = (
                            f"config/presets/{entry['preset']}.json"
                        )
                    jobs.append(job)
    return jobs


def list_suites():
    rows = []
    folder = ROOT / "config/suites"
    if not folder.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Suite directory must stay inside the checkout")
    for path in sorted(folder.glob("*.json")):
        if not path.is_symlink() and _SLUG.fullmatch(path.stem):
            value = read_json(path)
            if (
                not isinstance(value, dict)
                or set(value) != {"id", "name", "entries"}
                or value.get("id") != path.stem
                or not isinstance(value["entries"], list)
            ):
                raise ValueError(f"Invalid saved suite: {path.name}")
            value["entries"] = [_entry(entry) for entry in value["entries"]]
            rows.append(value)
    return rows


def save_suite(request):
    if not isinstance(request, dict) or set(request) - {"id", "name", "entries"}:
        raise ValueError("Invalid suite")
    name = request.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128:
        raise ValueError("Choose a suite name of 1..128 characters")
    suite_id = request.get("id")
    if suite_id is None:
        suite_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    if not isinstance(suite_id, str) or not _SLUG.fullmatch(suite_id):
        raise ValueError("Use a short suite ID with letters, digits and hyphens")
    raw = request.get("entries")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_JOBS:
        raise ValueError("Save at least one test selection")
    entries = [_entry(value) for value in raw]
    available = {test["id"] for test in test_catalog()}
    if any(entry["test_id"] not in available for entry in entries):
        raise ValueError("Unknown suite test")
    # Validate shapes and policy overrides without requiring all launch fields.
    tests = {test["id"]: test for test in test_catalog()}
    for entry in entries:
        test = tests[entry["test_id"]]
        _resolved_entry(test, entry)
    value = {"id": suite_id, "name": name.strip(), "entries": entries}
    folder = ROOT / "config/suites"
    if not folder.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Suite directory must stay inside the checkout")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{suite_id}.json"
    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Suite path must stay inside config/suites")
    handle, temporary = tempfile.mkstemp(prefix=".suite-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return value
