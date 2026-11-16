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


# Plain-language catalog text, keyed by test id. Sections in display order; a
# section marked advanced is hidden until the operator asks for lab tools.
SECTIONS = {
    "Skills": False,
    "Getting around": False,
    "Big jobs": False,
    "Obstacle courses": False,
    "Baritone comparison": True,
    "AI model trials": True,
    "Regression checks": True,
    "Developer checks": True,
}
_COURSE_GOAL = " Goal: reach the oak log, break it and pick it up."
PLAIN = {
    "companion:crafting": (
        "Skills",
        "Crafting",
        "Crafts at a crafting table, smelts in a furnace and equips gear.",
    ),
    "companion:placement": (
        "Skills",
        "Placing and breaking blocks",
        "Places blocks, uses them and breaks them again.",
    ),
    "companion:animals": (
        "Skills",
        "Animals and eating",
        "Attacks and interacts with penned animals, then eats.",
    ),
    "companion:containers": (
        "Skills",
        "Chests and shulker boxes",
        "Puts items into containers, takes them out, and checks shulker boxes keep "
        "their items.",
    ),
    "companion:hill-navigation": (
        "Getting around",
        "Climbing hills",
        "Walks over a hilly test area to a goal.",
    ),
    "companion:companion-wading": (
        "Getting around",
        "Wading through a pond",
        "Walks into shallow pond water and back out onto a dry bank.",
    ),
    "companion:companion-scaffold": (
        "Getting around",
        "Scaffold climb",
        "Builds a scaffold under itself to climb up.",
    ),
    "companion:spatial-navigation": (
        "Getting around",
        "Walking and remembering the map",
        "Walks across the flat lab, saves its map, then reloads it.",
    ),
    "gather": (
        "Big jobs",
        "Make a stone pickaxe",
        "Starts with nothing, chops trees, mines stone and crafts a stone pickaxe.",
    ),
    "build": (
        "Big jobs",
        "Build a small house",
        "Is given the materials and builds a small house.",
    ),
    "battery:small-house": (
        "Big jobs",
        "Build a house from chests",
        "Starts empty-handed beside chests of materials, crafts what it needs and "
        "builds the house.",
    ),
    "battery:iron-xray": (
        "Big jobs",
        "Iron pickaxe (sees ore)",
        "Works up to an iron pickaxe and can sense buried ore nearby.",
    ),
    "battery:iron-mine": (
        "Big jobs",
        "Iron pickaxe (strip mining)",
        "The same job, but it can't see ore, so it strip-mines to find it.",
    ),
    "battery:iron-raw": (
        "Big jobs",
        "Mine 3 raw iron",
        "Mines three raw iron with stone pickaxes. No smelting.",
    ),
    "companion:site-fill": (
        "Big jobs",
        "Fill in a pond",
        "Fills a natural pond to make flat ground, then puts the lab back.",
    ),
    "course:C1": (
        "Obstacle courses",
        "Course 1: Straight ahead",
        "The log is 12 blocks straight in front." + _COURSE_GOAL,
    ),
    "course:C2": (
        "Obstacle courses",
        "Course 2: Find the log",
        "Starts facing a random way; the log can be in any direction." + _COURSE_GOAL,
    ),
    "course:C3": (
        "Obstacle courses",
        "Course 3: Wall in the way",
        "A wall two blocks tall blocks the path, so it has to go"
        " around." + _COURSE_GOAL,
    ),
    "course:C4": (
        "Obstacle courses",
        "Course 4: Up and over",
        "A raised ridge to climb up and back down." + _COURSE_GOAL,
    ),
    "course:C5": (
        "Obstacle courses",
        "Course 5: All of it",
        "Random facing, a wall to go around and a ridge to climb." + _COURSE_GOAL,
    ),
    "course:C1:reset": (
        "Developer checks",
        "Course reset check",
        "Checks the course arena rebuilds itself exactly between runs.",
    ),
    "course:C1:episodes": (
        "Developer checks",
        "Course scoring check",
        "Scripted Course 1 runs that check pass/fail scoring.",
    ),
    "battery": (
        "Regression checks",
        "Full regression battery",
        "Runs every regression check below, one after another. Takes a long time.",
    ),
    "natural": (
        "Developer checks",
        "Natural terrain routes",
        "Routes across a saved natural world. Needs a saved seed and route data.",
    ),
    "companion:natural-ops": (
        "Developer checks",
        "Every action in the wild",
        "Runs each body action once inside a natural world.",
    ),
    "companion:natural-reporting": (
        "Developer checks",
        "Death report",
        "Takes one lethal hit and checks the death is reported, then restores the lab.",
    ),
    "companion:body-commands": (
        "Developer checks",
        "Body commands",
        "Checks the bot's command interface.",
    ),
    "companion:body-protocol": (
        "Developer checks",
        "Body protocol",
        "Checks messages between the bot and the server.",
    ),
    "companion:reference-fixtures": (
        "Developer checks",
        "Reference arenas",
        "Checks the reference test arenas are built correctly.",
    ),
    "companion:awareness": (
        "Developer checks",
        "Awareness",
        "Checks what the bot can see and sense around it.",
    ),
}


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
        family, test_id = row["family"], row["id"]
        if test_id in PLAIN:
            row["section"], row["label"], row["description"] = PLAIN[test_id]
        elif test_id.endswith(":baritone"):
            course = row["label"].split(" ")[0]
            row["section"] = "Baritone comparison"
            row["label"] = f"{PLAIN['course:' + course][1]} (Baritone)"
            row["description"] = (
                "Baritone, a different bot, runs the same course for comparison."
                " Needs the Baritone client running."
            )
        elif family == "models":
            name = row["label"].split(" · ")[1]
            row["section"], row["label"] = "AI model trials", f"AI model: {name}"
            row["description"] = (
                f"The {name} language model drives the bot through Course 1."
                " Needs that model set up and qualified."
            )
        elif family == "battery":
            row["section"] = "Regression checks"
            row["label"] = row["label"].replace("Battery · ", "Regression: ")
            row["description"] = (
                "Repeats a check exactly as the full battery runs it"
                f" (gives up after {row['timeout']}s)."
            )
        else:
            row["section"] = "Developer checks"
        row["advanced"] = SECTIONS[row["section"]]
    order, listed = list(SECTIONS), list(PLAIN)
    return sorted(
        rows,
        key=lambda row: (
            order.index(row["section"]),
            listed.index(row["id"]) if row["id"] in listed else len(listed),
        ),
    )


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
