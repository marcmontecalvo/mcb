"""Versioned, JSON-safe run settings shared by launchers and the lab UI."""

from __future__ import annotations

import json
import math
import re
import types
import urllib.parse
from dataclasses import asdict, dataclass, fields, is_dataclass
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

from mclab import build_checkpoint as ck
from mclab import play_runs
from mclab.awareness import Awareness
from mclab.companion_body import DIG_TERRAIN, NATURAL_MOVEMENT
from mclab.gather_options import GatherOptions
from mclab.memory_policy import MemoryPolicy
from mclab.movement_policy import MovementPolicy
from mclab.paths import ROOT
from mclab.terrain_steps import TerrainPolicy

SCHEMA_VERSION = 1


@dataclass
class RouteSettings:
    route_id: str = ""
    start: list[int] = None
    goal: list[int] = None
    yaw: float = 0.0


@dataclass
class PlannerSettings:
    instructions: str = ""
    candidates: dict[str, str] = None
    planner_interval_s: float = 5.0
    plan_ttl_ticks: int = 200
    planner_failure: str = "fail_attempt"


@dataclass
class FleeSettings:
    trigger: float = 1.0
    creeper_trigger: float = 1.0
    steps: int = 1


@dataclass
class RunSettings:
    test: str = ""
    start: list[int] | None = None
    seed: int | None = None
    build: str = "tests/fixtures/schematics/small-house.build.json"
    resume: str | None = None
    dx: int = 3
    dz: int = 2
    rotation: int = 0
    margin: int = 1
    inspect: bool = False
    preview: bool = False
    preview_offset: list[int] | None = None
    empty: bool = False
    supply: bool = False
    coal: str = "item"
    stage: bool = False
    start_from: str | None = None  # a library stage (build_checkpoint.is_stage)
    until: str | None = None  # end the build at a milestone (build_checkpoint.is_until)
    order: str = "zones"  # the build order (build_plan.ORDERS)
    scaffolding: int | None = None
    iron: bool = False
    raw_iron: int | None = None
    gather: GatherOptions = None  # initialized in defaults
    course: str | None = None
    entrant: str | None = None
    seeds: list[int] | None = None
    routes: str | None = None
    route_ids: list[str] | None = None
    condition: str = "calm"
    repetitions: int = 1
    show_markers: bool = False
    stall_seconds: int = 10
    cost_profile: str | None = None
    baritone_parkour: bool = False
    count: int = 1
    checks: list[str] | None = None
    output: str | None = None
    timeout: int | None = None
    check: str | None = None
    operation: str = "execute"
    route_data: list[RouteSettings] | None = None
    entrants: list[str] | None = None
    conditions: list[bool] | None = None
    arguments: list[str] | None = None
    no_restart: bool = False
    development: bool = False
    scored: bool = False
    baritone_settings: dict[str, bool] | None = None
    flee: FleeSettings | None = None
    origin: list[int] | None = None
    # play-server runs (mclab.play_runs): arena actions and hunts, combat trials
    arena: str | None = None
    arena_action: str | None = None
    waves: str | None = None
    custom_waves: str | None = None
    kit: str | None = None
    weapon_material: str | None = None
    armor_material: str | None = None
    players: str | None = None
    corner: list[int] | None = None
    restore: bool = False
    weapons: str | None = None
    mobs: str | None = None
    spot: list[int] | None = None


@dataclass
class BodySettings:
    awareness: Awareness = None
    control_profile: str = "lockstep-three-tick-v1"
    view_distance: int | None = None


@dataclass
class ModelSettings:
    backend: str | None = None
    model: str | None = None
    request_limit: int | None = None
    deadline_s: float | None = None
    prompt_identity: str | None = None
    planner_backend: str | None = None
    planner_model: str | None = None
    planner_request_limit: int | None = None
    planner_profile: PlannerSettings | None = None
    planner_prompt_identity: str | None = None


TESTS = (
    "gather",
    "build",
    "courses",
    "natural",
    "models",
    "battery",
    "companion",
    *play_runs.FAMILIES,
)
# Families that carry no planner or awareness policies.
_NO_POLICIES = tuple(t for t in TESTS if t not in {"natural", "gather", "build"})


_TUPLE_TYPES = {
    "material_allowlist": list[str],
    "scaffold_materials": list[str],
    "protected_blocks": list[str],
    "protected_positions": list[list[int]],
    "costly_positions": list[list[int]],
}
_ENUMS = {
    "operation": ["execute", "reset", "episodes"],
    "coal": ["item", "block", "ore"],
    "order": ["zones", "storeys"],
    "tool_choice": ["lowest_sufficient", "best"],
    "ore_collect": ["greedy", "specific"],
    "preset": ["legacy", "visible", "chunk", "sections"],
    "entity_visibility": ["visible", "unoccluded"],
    "memory_mode": ["current", "session", "persistent"],
}
# Stage names (build_checkpoint): band_N stages make these open-ended.
_PATTERNS = {
    "start_from": ck.STAGE_PATTERN,
    "until": ck.UNTIL_PATTERN,
}
_METADATA = {
    (MovementPolicy, "max_drop"): {"minimum": 0, "maximum": 3, "units": "blocks"},
    (MovementPolicy, "max_gap"): {"minimum": 0, "maximum": 2, "units": "blocks"},
    (GatherOptions, "vision_radius"): {"minimum": 1, "maximum": 64, "units": "blocks"},
    (RunSettings, "margin"): {"minimum": 0, "units": "blocks"},
    (Awareness, "horizontal_radius_blocks"): {
        "minimum": 1,
        "maximum": 64,
        "units": "blocks",
    },
    (Awareness, "update_interval_ticks"): {
        "minimum": 1,
        "maximum": 20,
        "units": "ticks",
    },
    (MemoryPolicy, "active_map_budget_mb"): {
        "minimumExclusive": 0,
        "maximum": 4096,
        "units": "MB",
    },
    (MemoryPolicy, "persistence_queue_budget_mb"): {
        "minimumExclusive": 0,
        "maximum": 4096,
        "units": "MB",
    },
}


def _json(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _defaults(test: str) -> dict:
    awareness = json.loads(
        (ROOT / "config/stepc/awareness-sections-64.json").read_text(encoding="utf-8")
    )
    run = asdict(RunSettings(test=test))
    if test == "natural":
        awareness = Awareness().payload()
        run["entrants"] = ["baritone", "v8_goal"]
        run["conditions"] = [False, True]
    if test == "build":
        # watched in game: the route, the stance it heads for and the block it works on
        run["show_markers"] = True
    if test not in {"natural", "gather", "build"}:
        awareness = None
    run["gather"] = asdict(GatherOptions())
    return _json(
        {
            "schema_version": SCHEMA_VERSION,
            "run": run,
            "body": {
                "awareness": awareness,
                "control_profile": "lockstep-three-tick-v1",
                "view_distance": None,
            },
            "planner": {
                "terrain": asdict(
                    TerrainPolicy() if test == "natural" else DIG_TERRAIN
                ),
                "movement": asdict(
                    MovementPolicy() if test == "natural" else NATURAL_MOVEMENT
                ),
                "memory": asdict(MemoryPolicy()),
            }
            if test in {"natural", "gather", "build"}
            else None,
            "model": asdict(ModelSettings()),
        }
    )


def _merge(target: dict, source: dict, path: str = "config") -> None:
    if type(source) is not dict:
        raise ValueError(f"{path} must be an object")
    for key, value in source.items():
        if key not in target:
            raise ValueError(f"Unknown field {path}.{key}")
        if type(target[key]) is dict:
            _merge(target[key], value, f"{path}.{key}")
        else:
            target[key] = value


def _check(value, annotation, path):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (types.UnionType,):
        for arg in args:
            try:
                _check(value, arg, path)
                return
            except ValueError:
                pass
        raise ValueError(f"Invalid type for {path}")
    if annotation is type(None):
        if value is None:
            return
    elif origin is list:
        if type(value) is list:
            for item in value:
                _check(item, args[0], path + "[]")
            return
    elif origin is dict:
        if type(value) is dict:
            for key, item in value.items():
                _check(key, args[0], path + ".<key>")
                _check(item, args[1], path + "." + key)
            return
    elif is_dataclass(annotation):
        _validate_fields(value, annotation, path)
        return
    elif annotation is float:
        if type(value) in (int, float) and math.isfinite(value):
            return
    elif type(value) is annotation:
        return
    raise ValueError(f"Invalid type for {path}")


def _validate_fields(value, cls, path):
    if type(value) is not dict:
        raise ValueError(f"{path} must be an object")
    hints = get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    if value.keys() != names:
        raise ValueError(f"Invalid fields in {path}")
    for name, item in value.items():
        annotation = (
            _TUPLE_TYPES.get(name, hints[name]) if cls is TerrainPolicy else hints[name]
        )
        _check(item, annotation, f"{path}.{name}")
        if name in _ENUMS and item not in _ENUMS[name] and item is not None:
            raise ValueError(f"Invalid value for {path}.{name}")
        if (
            name in _PATTERNS
            and item is not None
            and not (isinstance(item, str) and re.fullmatch(_PATTERNS[name], item))
        ):
            raise ValueError(f"Invalid value for {path}.{name}")


def resolve_config(
    test: str,
    *,
    preset: str | None = None,
    overrides: dict | None = None,
    base: dict | None = None,
    defaults: dict | None = None,
    root: Path | None = None,
    backend_specs: dict | list | None = None,
) -> dict:
    """Resolve defaults, preset, base and explicit overrides, in that order."""
    if test not in TESTS:
        raise ValueError(f"Unsupported test: {test}")
    result = _defaults(test)

    def merge_source(source):
        if (
            test == "natural"
            and isinstance(source, dict)
            and source.get("planner", False) is None
        ):
            source = {key: value for key, value in source.items() if key != "planner"}
        _merge(result, source)

    if defaults is not None:
        merge_source(defaults)
    if preset is not None:
        if (
            not isinstance(preset, str)
            or not preset
            or Path(preset).name != preset
            or preset in {".", ".."}
        ):
            raise ValueError("Invalid preset name")
        filename = preset if preset.endswith(".json") else preset + ".json"
        try:
            data = json.loads(
                ((root or ROOT) / "config/presets" / filename).read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read preset {preset}: {exc}") from exc
        merge_source(data)
    if base is not None:
        merge_source(base)
    if overrides is not None:
        merge_source(overrides)
    if (
        type(result["schema_version"]) is not int
        or result["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("Unsupported config schema version")
    _validate_fields(result["run"], RunSettings, "run")
    if (
        result["body"]["view_distance"] is not None
        and type(result["body"]["view_distance"]) is not int
    ):
        raise ValueError("view distance must be an integer")
    if result["body"]["awareness"] is not None:
        _validate_fields(result["body"], BodySettings, "body")
    else:
        _check(result["body"]["control_profile"], str, "body.control_profile")
        _check(result["body"]["view_distance"], int | None, "body.view_distance")
    _validate_fields(result["model"], ModelSettings, "model")
    if result["run"]["test"] != test:
        raise ValueError("run.test must match requested test")
    if (
        test == "natural"
        and result["planner"] is None
        and "segmented_goal" not in (result["run"]["entrants"] or [])
    ):
        result["planner"] = _defaults(test)["planner"]
    if test in {"natural", "gather", "build"}:
        _validate_fields(result["body"]["awareness"], Awareness, "body.awareness")
        for name, cls in (
            ("terrain", TerrainPolicy),
            ("movement", MovementPolicy),
            ("memory", MemoryPolicy),
        ):
            _validate_fields(result["planner"][name], cls, "planner." + name)
        policies(result)
    if (
        test in {"gather", "build"}
        and result["body"]["awareness"]["preset"] != "sections"
    ):
        raise ValueError("Only sections awareness is supported by companion")
    run = result["run"]
    default_run = _defaults(test)["run"]
    build_fields = (
        "build",
        "resume",
        "dx",
        "dz",
        "rotation",
        "margin",
        "inspect",
        "preview",
        "preview_offset",
        "empty",
        "supply",
        "coal",
        "stage",
        "start_from",
        "until",
        "order",
        "scaffolding",
        "origin",
        "show_markers",
    )
    natural_fields = (
        "routes",
        "route_ids",
        "condition",
        "repetitions",
        "show_markers",
        "stall_seconds",
        "cost_profile",
        "baritone_parkour",
        "baritone_settings",
    )
    allowed = {
        "gather": {"start", "seed", "iron", "raw_iron", "gather"},
        "build": {"start", "seed", "gather", *build_fields},
        "courses": {
            "course",
            "entrant",
            "seeds",
            "seed",
            "operation",
            "count",
            "development",
            "scored",
        },
        "natural": {
            "seed",
            "entrants",
            "conditions",
            "route_data",
            "flee",
            *[
                name
                for name in natural_fields
                if name not in {"routes", "route_ids", "condition"}
            ],
        },
        "models": {"count"},
        "battery": {"checks", "output", "timeout", "no_restart"},
        "companion": {"check", "arguments"},
        **{family: set(names) for family, names in play_runs.FIELDS.items()},
    }[test] | {"test"}
    unused = set(run) - allowed
    if any(run[name] != default_run[name] for name in unused):
        raise ValueError(f"Settings not supported by {test} runner")
    if test in play_runs.FAMILIES:
        play_runs.check(run)
    defaults_for_test = _defaults(test)
    if test != "natural" and any(
        result["body"][k] != defaults_for_test["body"][k]
        for k in ("control_profile", "view_distance")
    ):
        raise ValueError(f"Body controls not supported by {test} runner")
    if test not in {"natural", "gather", "build"} and (
        result["planner"] != defaults_for_test["planner"]
        or result["body"]["awareness"] != defaults_for_test["body"]["awareness"]
    ):
        raise ValueError(f"Policies not supported by {test} runner")
    if test != "models" and result["model"] != defaults_for_test["model"]:
        raise ValueError(f"Model settings not supported by {test} runner")
    if test == "natural":
        baritone_settings = run["baritone_settings"]
        if baritone_settings is not None:
            allowed = {
                "allowBreak",
                "allowPlace",
                "allowInventory",
                "allowParkour",
                "allowSprint",
                "chatControl",
            }
            if set(baritone_settings) - allowed:
                raise ValueError("Unknown Baritone setting")
        if run["baritone_parkour"]:
            if (
                baritone_settings is not None
                and baritone_settings.get("allowParkour") is False
            ):
                raise ValueError("Conflicting Baritone parkour settings")
            run["baritone_settings"] = {
                **(baritone_settings or {}),
                "allowParkour": True,
            }
            run["baritone_parkour"] = False
        if run["baritone_settings"] and "baritone" not in run["entrants"]:
            raise ValueError("Baritone settings require baritone entrant")
        if (
            not run["entrants"]
            or any(
                e not in {"baritone", "v8_goal", "segmented_goal"}
                for e in run["entrants"]
            )
            or not run["conditions"]
        ):
            raise ValueError("Invalid natural entrants or conditions")
        if "segmented_goal" not in run["entrants"]:
            if result["planner"] != defaults_for_test["planner"]:
                raise ValueError("Natural policy overrides require segmented_goal")
            if result["body"]["control_profile"] != "lockstep-three-tick-v1":
                raise ValueError("Development control profile requires segmented_goal")
            result["planner"] = None
        if (
            result["body"]["view_distance"] is not None
            and not 2 <= result["body"]["view_distance"] <= 32
        ):
            raise ValueError("view distance must be 2..32")
        if result["body"]["control_profile"] not in {
            "lockstep-three-tick-v1",
            "dev-pipelined-v1",
            "dev-native-route-v1",
        }:
            raise ValueError("Invalid control profile")
        if not 1 <= run["repetitions"] <= 3:
            raise ValueError("repetitions must be 1..3")
        if run["condition"] not in {"calm", "mobs", "both"} or not (
            run["stall_seconds"] == 0 or 3 <= run["stall_seconds"] <= 120
        ):
            raise ValueError("Invalid natural settings")
        if run["cost_profile"] not in {
            None,
            "race",
            "safe",
            "frugal",
            "hunt",
            "escort",
        }:
            raise ValueError("Invalid cost profile")
        flee = run["flee"]
        if flee is not None and (
            not 0 < flee["trigger"] <= 16
            or not 0 < flee["creeper_trigger"] <= 16
            or not 1 <= flee["steps"] <= 12
        ):
            raise ValueError("Invalid flee settings")
        if run["cost_profile"] is not None:
            if result["planner"] is None:
                raise ValueError("Cost profiles require segmented_goal")
            from mclab.profiles import apply_profile, load_profile

            result["planner"]["terrain"] = asdict(
                apply_profile(
                    run["cost_profile"],
                    TerrainPolicy.from_payload(result["planner"]["terrain"]),
                )
            )
            expected_flee = load_profile(run["cost_profile"])["flee"]
            if flee is not None and flee != expected_flee:
                raise ValueError("Flee settings conflict with cost profile")
            run["flee"] = expected_flee
        if run["route_data"] is not None:
            from mclab.natural import _validate_run

            _validate_run(
                run["seed"] if run["seed"] is not None else 0,
                run["route_data"],
                run["entrants"] or ["v8_goal"],
                run["conditions"] or [False],
            )
    if test == "courses" and (
        run["course"] not in {None, "C1", "C2", "C3", "C4", "C5"}
        or run["entrant"] not in {None, "v8_goal", "mcb", "baritone"}
        or run["operation"] not in {"execute", "reset", "episodes"}
    ):
        raise ValueError("Invalid course selection")
    if (
        test == "courses"
        and run["operation"] == "episodes"
        and (
            run["course"] != "C1"
            or run["count"] != 1
            or run["entrant"] not in {None, "mcb"}
            or run["seed"] is not None
            or run["seeds"] is not None
            or run["development"]
            or run["scored"]
        )
    ):
        raise ValueError("C1 episodes uses fixed qualification settings")
    if test == "courses" and run["entrant"] is None:
        run["entrant"] = "mcb"
    count_limit = 5 if test == "models" else 20
    if not 1 <= run["count"] <= count_limit:
        raise ValueError(f"Count must be 1 to {count_limit}")
    if run["timeout"] is not None and run["timeout"] <= 0:
        raise ValueError("Timeout must be positive")
    model = result["model"]
    if test == "models" and model["backend"] is not None:
        try:
            catalog = (
                backend_specs
                if backend_specs is not None
                else json.loads(
                    ((root or ROOT) / "config/backends.json").read_text(
                        encoding="utf-8"
                    )
                )
            )
            specs = catalog["backends"] if isinstance(catalog, dict) else catalog
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            raise ValueError("Cannot read backend settings") from exc
        matches = [
            s for s in specs if s.get("name") == model["backend"] and s.get("enabled")
        ]
        if len(matches) != 1:
            raise ValueError("Select exactly one enabled configured backend")
        spec = matches[0]
        for key, expected in (
            ("model", spec.get("model")),
            ("deadline_s", spec.get("deadline_s", 1.0)),
        ):
            if model[key] is not None and model[key] != expected:
                raise ValueError(f"model.{key} must match the configured backend")
            model[key] = expected
        if model["deadline_s"] != 1.0:
            raise ValueError("Live model execution requires a one-second deadline")
        if model["request_limit"] is None:
            model["request_limit"] = 40

        def local(spec):
            return urllib.parse.urlsplit(spec.get("root", "")).hostname in {
                "localhost",
                "127.0.0.1",
                "::1",
            } or (
                spec.get("network_scope") == "private_lan"
                and spec.get("inference_scope") == "local"
            )

        if not local(spec) and model["request_limit"] > spec["request_limit"]:
            raise ValueError("Request limit exceeds configured remote backend cap")
        if model["request_limit"] < run["count"]:
            raise ValueError("Require at least one request per episode")
        if model["planner_backend"] is not None:
            planners = [
                s
                for s in specs
                if s.get("name") == model["planner_backend"] and s.get("enabled")
            ]
            if len(planners) != 1:
                raise ValueError(
                    "Select exactly one enabled configured planner backend"
                )
            planner_spec = planners[0]
            if (
                model["planner_model"] is not None
                and model["planner_model"] != planner_spec["model"]
            ):
                raise ValueError("Planner model must match configured backend")
            model["planner_model"] = planner_spec["model"]
            if planner_spec.get("deadline_s", 1.0) != 1.0:
                raise ValueError("Live planner requires a one-second deadline")
            if model["planner_request_limit"] is None:
                model["planner_request_limit"] = 4
            if not run["count"] <= model["planner_request_limit"] <= 100:
                raise ValueError("Invalid planner request limit")
            if (
                not local(planner_spec)
                and model["planner_request_limit"] > planner_spec["request_limit"]
            ):
                raise ValueError("Planner request limit exceeds configured remote cap")
            if model["planner_profile"] is None:
                raise ValueError("Planner profile is required")
        else:
            stray = [
                k
                for k in ("planner_model", "planner_request_limit", "planner_profile")
                if model[k] is not None
            ]
            if stray:
                raise ValueError(f"{', '.join(stray)} requires planner_backend")
    if model["request_limit"] is not None and not 1 <= model["request_limit"] <= 100:
        raise ValueError("Model request limit must be 1..100")
    if model["deadline_s"] is not None and not 0 < model["deadline_s"] <= 10:
        raise ValueError("Model deadline must be positive and at most 10 seconds")
    profile = model["planner_profile"]
    if profile is not None and (
        not profile["instructions"]
        or not profile["candidates"]
        or profile["planner_interval_s"] < 0.5
        or not 1 <= profile["plan_ttl_ticks"] <= 1200
        or profile["planner_failure"] not in {"fail_attempt", "continue_without_plan"}
    ):
        raise ValueError("Invalid planner profile")
    if run["seed"] is not None and not -(2**63) <= run["seed"] < 2**63:
        raise ValueError("Seed must fit in a signed 64-bit integer")
    if run["rotation"] not in (0, 90, 180, 270):
        raise ValueError("Invalid build rotation")
    if run["raw_iron"] is not None and run["raw_iron"] <= 0:
        raise ValueError("raw_iron must be positive")
    if run["scaffolding"] is not None and run["scaffolding"] < 0:
        raise ValueError("scaffolding must be nonnegative")
    if test == "build" and run["origin"] is not None:
        if len(run["origin"]) != 3 or any(
            abs(value) > 30_000_000 for value in run["origin"]
        ):
            raise ValueError("Invalid build origin")
        if (
            not run["resume"]
            and run["start"] is not None
            and run["origin"]
            != [
                run["start"][0] + run["dx"],
                run["start"][1],
                run["start"][2] + run["dz"],
            ]
        ):
            raise ValueError("Build origin does not match start and offset")
    for name in ("start", "preview_offset"):
        if run[name] is not None and len(run[name]) != 3:
            raise ValueError(f"run.{name} must contain three coordinates")
        if run[name] is not None and any(abs(v) > 30_000_000 for v in run[name]):
            raise ValueError(f"run.{name} coordinates are out of bounds")
    if result["planner"] is not None and any(
        len(p) != 3 for p in result["planner"]["terrain"]["protected_positions"]
    ):
        raise ValueError("Protected positions must contain three coordinates")
    if (
        run["margin"] < 0
        or (run["stage"] and not (run["resume"] or run["start_from"]))
        or (run["stage"] and run["until"])
        or (run["supply"] and (run["empty"] or run["resume"]))
        or (run["start_from"] and (run["resume"] or run["supply"] or run["empty"]))
        # a lab ends at a milestone after the stage it starts from
        or (
            run["start_from"]
            and run["until"]
            and ck.stage_rank(ck.until_stage(run["until"]))
            <= ck.stage_rank(run["start_from"])
        )
        or (
            run["until"]
            and ck.BAND.fullmatch(run["until"])
            and run["order"] != "storeys"
        )
        or (run["coal"] != "item" and not run["supply"])
        or (run["preview_offset"] and not run["preview"])
    ):
        raise ValueError("Invalid build settings combination")
    g = run["gather"]
    if (
        not 1 <= g["vision_radius"] <= 64
        or any(g[k] < 0 for k in ("spare_pickaxes", "carry_logs"))
        or any(
            g[k] is not None and g[k] <= 0 for k in ("search_radius", "strip_max_legs")
        )
        or any(g[k] <= 0 for k in ("strip_leg", "strip_turn"))
    ):
        raise ValueError("Invalid gather settings")
    return _json(result)


def policies(config: dict) -> dict:
    """Return the policy keyword arguments accepted by start_session."""
    result = {"awareness": Awareness.from_payload(config["body"]["awareness"])}
    if config["planner"] is None:
        return result
    return {
        **result,
        "movement_policy": MovementPolicy.from_payload(config["planner"]["movement"]),
        "terrain_policy": TerrainPolicy.from_payload(config["planner"]["terrain"]),
        "memory_policy": MemoryPolicy.from_payload(config["planner"]["memory"]),
    }


def _type_schema(annotation):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is types.UnionType:
        return {"anyOf": [_type_schema(arg) for arg in args]}
    if origin is list:
        return {"type": "array", "items": _type_schema(args[0])}
    if origin is dict:
        return {"type": "object", "additionalProperties": _type_schema(args[1])}
    if is_dataclass(annotation):
        return _class_schema(annotation)
    kind = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }.get(annotation)
    return (
        {"type": kind}
        if kind
        else {"type": "unsupported", "python_type": str(annotation)}
    )


def _class_schema(cls):
    hints = get_type_hints(cls)
    props = {}
    for field in fields(cls):
        typ = (
            _TUPLE_TYPES.get(field.name, hints[field.name])
            if cls is TerrainPolicy
            else hints[field.name]
        )
        spec = _type_schema(typ)
        spec.update(
            {
                "label": field.name.replace("_", " ").title(),
                "group": cls.__name__,
                "help": field.metadata.get("help", ""),
            }
        )
        if field.name in _ENUMS:
            spec["enum"] = _ENUMS[field.name]
        if field.name in _PATTERNS:
            spec["pattern"] = f"^({_PATTERNS[field.name]})$"
        spec.update(_METADATA.get((cls, field.name), {}))
        props[field.name] = spec
    return {"type": "object", "properties": props, "additionalProperties": False}


def config_schema() -> dict:
    """Describe every dataclass field, including unsupported controls."""
    schema = {
        "schema_version": SCHEMA_VERSION,
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "enum": [SCHEMA_VERSION]},
            "run": _class_schema(RunSettings),
            "body": _class_schema(BodySettings),
            "planner": {
                "type": "object",
                "properties": {
                    "terrain": _class_schema(TerrainPolicy),
                    "movement": _class_schema(MovementPolicy),
                    "memory": _class_schema(MemoryPolicy),
                },
                "additionalProperties": False,
            },
            "model": _class_schema(ModelSettings),
        },
        "additionalProperties": False,
    }

    def annotate(node, values):
        node["defaults"] = values
        if node.get("type") == "object":
            node["required"] = list(node["properties"])
            for name, child in node["properties"].items():
                annotate(
                    child,
                    {
                        test: value[name] if value is not None else None
                        for test, value in values.items()
                    },
                )

    annotate(schema, {test: resolve_config(test) for test in TESTS})
    schema["properties"]["planner"]["nullable_for"] = list(_NO_POLICIES)
    schema["properties"]["body"]["properties"]["awareness"]["nullable_for"] = list(
        _NO_POLICIES
    )
    schema["properties"]["planner"]["type"] = ["object", "null"]
    schema["properties"]["body"]["properties"]["awareness"]["type"] = ["object", "null"]
    run = schema["properties"]["run"]["properties"]
    capabilities = {
        "start": ["gather", "build"],
        "seed": ["gather", "build", "natural", "courses"],
        "course": ["courses"],
        "entrant": ["courses"],
        "seeds": ["courses"],
        "operation": ["courses"],
        "count": ["courses", "models", *play_runs.FAMILIES],
        "routes": ["natural"],
        "route_ids": ["natural"],
        "route_data": ["natural"],
        "entrants": ["natural"],
        "conditions": ["natural"],
        "condition": ["natural"],
        "repetitions": ["natural"],
        "show_markers": ["natural", "build"],
        "stall_seconds": ["natural"],
        "cost_profile": ["natural"],
        "flee": ["natural"],
        "development": ["courses"],
        "scored": ["courses"],
        "baritone_parkour": ["natural"],
        "baritone_settings": ["natural"],
        "checks": ["battery"],
        "output": ["battery"],
        "timeout": ["battery", *play_runs.FAMILIES],
        "no_restart": ["battery"],
        "check": ["companion"],
        "arguments": ["companion"],
        "test": list(TESTS),
        **play_runs.field_families(),
    }
    for name, families in capabilities.items():
        run[name]["supported_tests"] = families
    # Positions are shown as X Y Z inputs. Play-run choices are read from the files
    # and catalogs each time (a new arena, wave preset or kit part shows up at once).
    for name in ("start", "origin", "preview_offset"):
        run[name]["format"] = "position"
    play_runs.decorate_schema(run)
    run["count"]["by_test"].update(
        courses={"label": "Episodes", "help": "Times through the course."},
        models={"label": "Episodes", "help": "Episodes per model."},
    )
    run["timeout"]["by_test"]["battery"] = {
        "label": "Time limit per check (seconds)",
        "help": "A check not done by then fails.",
    }
    for name in ("routes", "route_ids", "condition"):
        run[name]["supported_tests"] = []
        run[name]["supported"] = False
        run[name]["help"] = (
            "Use canonical route_data and conditions; nondefault aliases are rejected."
        )
    schema["properties"]["planner"]["nullable_when"] = {
        "test": "natural",
        "entrants_excludes": "segmented_goal",
    }
    for name in ("control_profile", "view_distance"):
        schema["properties"]["body"]["properties"][name]["supported_tests"] = [
            "natural"
        ]
    schema["properties"]["model"]["supported_tests"] = ["models"]
    schema["properties"]["planner"]["supported_tests"] = ["gather", "build", "natural"]
    schema["properties"]["body"]["properties"]["awareness"]["properties"]["preset"][
        "enum_by_test"
    ] = {"gather": ["sections"], "build": ["sections"]}
    for name in ("gather", "iron", "raw_iron"):
        run[name]["supported_tests"] = (
            ["gather", "build"] if name == "gather" else ["gather"]
        )
    for name in (
        "build",
        "resume",
        "dx",
        "dz",
        "rotation",
        "margin",
        "inspect",
        "preview",
        "preview_offset",
        "empty",
        "supply",
        "coal",
        "stage",
        "start_from",
        "until",
        "order",
        "scaffolding",
        "origin",
    ):
        run[name]["supported_tests"] = ["build"]
    return schema
