"""CLI adapter for the shared run contract; parser defaults never override presets."""

import argparse
import json
import sys
from pathlib import Path

from mclab.paths import ROOT
from mclab.run_config import resolve_config

GATHER_FLAGS = {
    "tool_choice": "tool_choice",
    "ore_collect": "ore_collect",
    "vision_radius": "vision_radius",
    "search_radius": "search_radius",
    "strip_leg": "strip_leg",
    "strip_turn": "strip_turn",
    "strip_max_legs": "strip_max_legs",
    "spare_pickaxes": "spare_pickaxes",
    "carry_logs": "carry_logs",
}


def parse_run(parser, test, argv=None):
    """Return a legacy runner namespace and the JSON-compatible resolved snapshot."""
    parser.add_argument("--run-config", type=Path, help="shared JSON run config")
    parser.add_argument("--preset", help="preset name in config/presets")
    parser.add_argument(
        "--print-config", action="store_true", help="resolve and print without running"
    )
    for action in parser._actions:
        if action.dest != "help":
            action.default = argparse.SUPPRESS
            action.required = False
    explicit = vars(parser.parse_args(argv))
    source = explicit.pop("run_config", None)
    preset = explicit.pop("preset", None)
    print_config = explicit.pop("print_config", False)
    overrides = {"run": {}}
    for name, value in explicit.items():
        if name == "no_climb":
            overrides.setdefault("planner", {}).setdefault("movement", {})[
                "allow_climb"
            ] = not value
        elif name == "no_ore_vision":
            overrides["run"].setdefault("gather", {})["ore_vision"] = not value
        elif test == "gather" and name in GATHER_FLAGS:
            if name == "strip_max_legs" and value == 0:
                value = None  # existing CLI spelling for unlimited
            overrides["run"].setdefault("gather", {})[GATHER_FLAGS[name]] = value
        else:
            overrides["run"][name] = str(value) if isinstance(value, Path) else value
    try:
        base = json.loads(source.read_text(encoding="utf-8")) if source else None
        config = resolve_config(test, preset=preset, base=base, overrides=overrides)
        from mclab.run_launch import prepare_config

        config = prepare_config(config)
        run = config["run"]
        if run["start"] is None and (test == "gather" or not run.get("resume")):
            raise ValueError("--start is required without --resume")
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    if print_config:
        print(json.dumps(config, indent=2, allow_nan=False))
        parser.exit()
    values = {key: value for key, value in run.items() if key not in {"test", "gather"}}
    for key in ("build", "resume"):
        if values.get(key) is not None:
            values[key] = ROOT / values[key]
    return argparse.Namespace(**values), config


def invocation():
    return sys.argv[1:]
