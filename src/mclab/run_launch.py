"""Shared launch boundaries; retain existing runners, locking and cleanup."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import runpy
import sys
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

from mclab.paths import ROOT, write_json

_CURRENT = ContextVar("mclab_launch", default=None)
LAUNCHES = ROOT / "results/launches"  # tests point this at their tmp_path
CHECKS = {
    "animals",
    "companion-scaffold",
    "companion-wading",
    "containers",
    "crafting",
    "hill-navigation",
    "natural-ops",
    "natural-reporting",
    "placement",
    "site-fill",
    "spatial-navigation",
}
CHECKS_WITH_FLAGS = {"companion-wading", "hill-navigation", "natural-ops"}
API_CHECKS = {
    "body-commands": ("mclab.qualify", "qualify_commands"),
    "body-protocol": ("mclab.protocolproof", "qualify_protocol"),
    "reference-fixtures": ("mclab.referenceproof", "qualify_reference_fixtures"),
    "awareness": ("mclab.awareness", "check"),
}
CHECKS.update(API_CHECKS)


@contextmanager
def launch_record(config, *, folder=None, invocation=None, input_references=None):
    """Keep a record even when startup/locking fails, and before world mutation."""
    from mclab.run_provenance import record_run

    current = _CURRENT.get()
    if current is not None and current["config"] == config:
        if folder is not None:
            attach_run(folder)
        yield current
        return
    if folder is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        folder = LAUNCHES / stamp
    folder = Path(folder)
    inherited = os.environ.get("MCB_LAUNCH_RECORD")
    if inherited and current is None:
        source = Path(inherited)
        if json.loads((source / "run_config.json").read_text("utf-8")) != config:
            raise ValueError("Child launch differs from its admitted configuration")
        if not (source / "provenance.json").is_file():
            raise ValueError("Child launch provenance is missing")
        folder = source
    else:
        record_run(
            folder,
            config,
            invocation=list(sys.argv[1:]) if invocation is None else invocation,
            **({"input_references": input_references} if input_references else {}),
        )
    state = {
        "folder": folder,
        "config": json.loads(json.dumps(config, allow_nan=False)),
        "attached": set(),
    }
    token = _CURRENT.set(state)
    write_json(folder / "launch_status.json", {"status": "starting"})
    try:
        yield state
    except BaseException as error:
        report = {
            "status": "interrupted"
            if isinstance(error, KeyboardInterrupt)
            else "finished"
            if isinstance(error, SystemExit) and not error.code
            else "failed",
            "error_type": type(error).__name__,
        }
        _write_outcome(state, report)
        raise
    else:
        _write_outcome(state, state.get("outcome", {"status": "finished"}))
    finally:
        _CURRENT.reset(token)


def _write_outcome(state, report):
    for folder in {state["folder"], *state["attached"]}:
        write_json(folder / "launch_status.json", report)


def record_outcome(result):
    """Separate a completed invocation from a passing test."""
    current = _CURRENT.get()
    if current is None:
        return
    status = "finished"
    if type(result) is int:
        status = "failed" if result else "finished"
    elif isinstance(result, dict):
        label = result.get("status", "")
        if label in {"failed", "invalid", "error", "not_ready", "unqualified"}:
            status = "failed"
        elif label in {"killed", "stopped", "cancelled", "interrupted"}:
            status = "interrupted"
        if result.get("returncode") not in (None, 0):
            status = "failed"
        if any(
            result.get(key)
            for key in ("error", "cleanup_error", "stop_error", "recording_error")
        ):
            status = "failed"
        attempts = result.get("attempts", [])
        if any(a.get("status") != "success" for a in attempts):
            status = "failed"
    current["outcome"] = {"status": status}


def record_effective_run(folder, config, *, invocation=None):
    """Use an admitted launch's identity when a legacy script opens its folder."""
    from mclab.run_provenance import record_run

    current = _CURRENT.get()
    if current is not None and current["config"] == config:
        attach_run(folder)
    else:
        record_run(folder, config, invocation=invocation)


def active_record_folder():
    current = _CURRENT.get()
    return str(current["folder"]) if current is not None else None


def attach_run(folder):
    """Copy the same immutable identity into the runner's native result folder."""
    state = _CURRENT.get()
    if state is None:
        return
    folder = Path(folder)
    if folder in state["attached"] or folder == state["folder"]:
        return
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("run_config.json", "provenance.json"):
        content = (state["folder"] / name).read_bytes()
        target = folder / name
        if target.exists():
            if target.read_bytes() != content:
                raise ValueError("Run identity conflicts with existing result folder")
        else:
            with target.open("xb") as stream:
                stream.write(content)
    state["attached"].add(folder)


def _runtime_config(
    family, values, *, entrant=None, operation="execute", root=None, backend_specs=None
):
    from mclab.run_config import resolve_config

    run, body, planner, model = {}, {}, {}, {}
    if family == "natural":
        from mclab.awareness import Awareness
        from mclab.memory_policy import MemoryPolicy
        from mclab.movement_policy import MovementPolicy
        from mclab.terrain_steps import TerrainPolicy

        run = {
            key: values[key]
            for key in ("seed", "repetitions", "show_markers", "stall_seconds")
        }
        run.update(
            route_data=[
                {k: route[k] for k in ("route_id", "start", "goal", "yaw")}
                for route in values["routes"]
            ],
            entrants=list(values["entrants"]),
            conditions=list(values["conditions"]),
            baritone_settings=values.get("baritone_settings"),
            flee=values.get("flee"),
        )
        body = {
            "awareness": (values["awareness"] or Awareness()).payload(),
            "control_profile": values["control_profile"],
            "view_distance": values["view_distance"],
        }
        if "segmented_goal" in values["entrants"]:
            planner = {
                "terrain": asdict(values["terrain_policy"] or TerrainPolicy()),
                "movement": (values["movement_policy"] or MovementPolicy()).payload(),
                "memory": (values["memory_policy"] or MemoryPolicy()).payload(),
            }
    elif family == "courses":
        run = {
            "course": values.get("course", "C1"),
            "entrant": entrant or "mcb",
            "seeds": values.get("seeds"),
            "count": values.get("count", 1),
            "operation": operation,
        }
        run.update(
            development=values.get("development", False),
            scored=values.get("scored", False),
        )
        if operation == "reset":
            run["seed"] = values.get("seed", 0)
    elif family == "models":
        run = {"count": values["count"]}
        model = {
            "backend": values.get("backend_name", values.get("tactical_backend_name")),
            "request_limit": values["request_limit"],
        }
        if operation == "tandem":
            from mclab.run_config import PlannerSettings

            model.update(
                planner_backend=values["planner_backend_name"],
                planner_request_limit=values["planner_request_limit"],
                planner_profile={
                    **asdict(PlannerSettings()),
                    **values["planner_profile"],
                },
            )
    overrides = json.loads(
        json.dumps(
            {
                "run": run,
                "body": body,
                **({"planner": planner} if planner else {}),
                "model": model,
            },
            allow_nan=False,
        )
    )
    return resolve_config(
        family,
        overrides=overrides,
        root=root,
        **({"backend_specs": backend_specs} if backend_specs is not None else {}),
    )


def recorded(family, *, entrant=None, operation="execute", check=None):
    """Give existing direct Python/CLI entry points the shared launch contract."""

    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def launch(*args, **kwargs):
            current = _CURRENT.get()
            if current is not None and current["config"]["run"]["test"] == family:
                return function(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            catalog = None
            if family == "models":
                catalog = function.__globals__["read_json"](
                    function.__globals__["ROOT"] / "config/backends.json"
                )["backends"]
            if check is not None:
                from mclab.run_config import resolve_config

                config = resolve_config(
                    "companion", overrides={"run": {"check": check}}
                )
            else:
                config = _runtime_config(
                    family,
                    bound.arguments,
                    entrant=entrant,
                    operation=operation,
                    root=function.__globals__.get("ROOT", ROOT),
                    backend_specs=catalog,
                )
            with launch_record(config):
                result = function(*args, **kwargs)
                record_outcome(result)
                return result

        return launch

    return decorate


@contextmanager
def _arguments(script, args):
    previous = sys.argv
    sys.argv = [str(script), *args]
    try:
        yield
    finally:
        sys.argv = previous


def script_run(name):
    """Shared options for fixed live checks without replacing their parsers."""

    def decorate(function):
        @wraps(function)
        def run():
            current = _CURRENT.get()
            if current and current["config"]["run"]["test"] == "companion":
                return function()
            from mclab.run_config import resolve_config

            parser = argparse.ArgumentParser(add_help=False)
            parser.add_argument("--run-config", type=Path)
            parser.add_argument("--preset")
            parser.add_argument("--print-config", action="store_true")
            shared, remaining = parser.parse_known_args()
            base = (
                json.loads(shared.run_config.read_text(encoding="utf-8"))
                if shared.run_config
                else None
            )
            config = resolve_config(
                "companion",
                preset=shared.preset,
                base=base,
                overrides={
                    "run": {
                        "check": name,
                        **({"arguments": remaining} if remaining else {}),
                    }
                },
            )
            arguments = config["run"]["arguments"] or []
            if name not in CHECKS_WITH_FLAGS and arguments:
                parser.error(f"{name} accepts no test-specific arguments")
            if shared.print_config:
                print(json.dumps(config, indent=2))
                return 0
            with launch_record(config), _arguments(sys.argv[0], arguments):
                result = function()
                record_outcome(result)
                return result

        return run

    return decorate


def _script(path, arguments):
    # Scripts are fixed by dispatch, never supplied by a run config.
    previous = list(sys.path)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        program = runpy.run_path(str(path))
        with _arguments(path, arguments):
            return program["main"]()
    finally:
        sys.path[:] = previous


def execute_config(config):
    """Dispatch a fully resolved config through the canonical runner APIs."""
    from mclab.run_config import policies

    run, body, model = config["run"], config["body"], config["model"]
    family = run["test"]
    if family in {"gather", "build"}:
        with tempfile.TemporaryDirectory(prefix="mcb-run-config-") as temporary:
            path = Path(temporary) / "run_config.json"
            path.write_text(json.dumps(config, allow_nan=False), encoding="utf-8")
            return _script(
                ROOT / f"scripts/run-{family}.py", ["--run-config", str(path)]
            )
    if family == "companion":
        if run["check"] not in CHECKS:
            raise ValueError("Unknown companion check")
        if run["check"] in API_CHECKS:
            module, name = API_CHECKS[run["check"]]
            return getattr(importlib.import_module(module), name)()
        return _script(
            ROOT / f"scripts/check-{run['check']}.py", run["arguments"] or []
        )
    if family == "battery":
        args = ["--out", run["output"]]
        if run["checks"]:
            args += ["--only", *run["checks"]]
        if run["no_restart"]:
            args.append("--no-restart")
        if run["timeout"] is not None:
            args += ["--timeout", str(run["timeout"])]
        return _script(ROOT / "scripts/run-battery.py", args)
    if family == "natural":
        from mclab import natural

        kwargs = policies(config)
        return natural.run(
            run["seed"],
            run["route_data"],
            entrants=tuple(run["entrants"]),
            conditions=tuple(run["conditions"]),
            repetitions=run["repetitions"],
            view_distance=body["view_distance"],
            control_profile=body["control_profile"],
            show_markers=run["show_markers"],
            stall_seconds=run["stall_seconds"],
            baritone_settings=run["baritone_settings"],
            flee=run["flee"],
            **kwargs,
        )
    if family == "models":
        from mclab.model_execution import run as smoke

        if model["planner_backend"]:
            from mclab.model_execution import run_tandem

            return run_tandem(
                model["backend"],
                model["planner_backend"],
                planner_profile=model["planner_profile"],
                count=run["count"],
                request_limit=model["request_limit"],
                planner_request_limit=model["planner_request_limit"],
            )
        return smoke(
            model["backend"], count=run["count"], request_limit=model["request_limit"]
        )
    if family == "courses":
        from mclab import baritone_runner, execution, navigation_runner

        if run["operation"] == "episodes":
            from mclab.episodeproof import qualify_episodes

            return qualify_episodes()
        if run["course"] == "C1" and run["operation"] == "reset":
            from mclab.resetcheck import qualify_resets

            return qualify_resets(run["count"])
        if run["course"] == "C1":
            return execution.qualify_execution(
                run["count"], development=run["development"]
            )
        if run["operation"] == "reset":
            return navigation_runner.qualify_resets(
                run["course"], run["seed"] or 0, run["count"]
            )
        runner = baritone_runner if run["entrant"] == "baritone" else navigation_runner
        if runner is navigation_runner:
            return runner.execute(run["course"], run["seeds"], scored=run["scored"])
        return runner.execute(run["course"], run["seeds"])
    raise ValueError("Unsupported run family")


def launch_config(config, *, invocation=None):
    config = prepare_config(config)
    validate_launch(config)
    with launch_record(config, invocation=invocation):
        result = execute_config(config)
        record_outcome(result)
        return result


def prepare_config(config):
    """Resolve checkpoint-owned settings and canonical input paths before recording."""
    if config["run"]["test"] != "build":
        return config
    config = json.loads(json.dumps(config, allow_nan=False))
    run = config["run"]
    if run["resume"]:
        from mclab.build_checkpoint import load

        checkpoint = load(ROOT / run["resume"])
        for key in ("start", "seed", "rotation", "build", "origin"):
            run[key] = checkpoint[key]
    elif run["start"] is not None:
        run["origin"] = [
            run["start"][0] + run["dx"],
            run["start"][1],
            run["start"][2] + run["dz"],
        ]
    for key in ("build", "resume"):
        if run[key] is not None:
            path = (ROOT / run[key]).resolve()
            run[key] = (
                path.relative_to(ROOT.resolve()).as_posix()
                if path.is_relative_to(ROOT.resolve())
                else str(path)
            )
    from mclab.run_config import resolve_config

    return resolve_config("build", base=config)


def validate_launch(config):
    """Reject incomplete selections before any runner acquires the arena."""
    run = config["run"]
    test = run["test"]
    if test == "gather" and run["start"] is None:
        raise ValueError("Gather requires run.start")
    if test == "build" and run["start"] is None and run["resume"] is None:
        raise ValueError("Build requires run.start or run.resume")
    if test == "natural" and (run["seed"] is None or not run["route_data"]):
        raise ValueError("Natural run requires seed and route_data")
    if test == "courses":
        if not run["course"]:
            raise ValueError("Course selection required")
        if run["operation"] not in {"execute", "reset", "episodes"}:
            raise ValueError("Invalid course operation")
        if run["course"] != "C1" and run["operation"] == "execute" and not run["seeds"]:
            raise ValueError("Course execution requires explicit seeds")
        if run["course"] not in {"C1", "C2", "C3", "C4", "C5"}:
            raise ValueError("Unknown course")
        if run["operation"] == "episodes":
            if (
                run["course"] != "C1"
                or run["count"] != 1
                or run["entrant"] == "baritone"
                or run["scored"]
                or run["development"]
                or run["seeds"] is not None
                or run["seed"] is not None
            ):
                raise ValueError("Episode checks use their fixed C1 cases")
        elif run["operation"] == "reset":
            if run["entrant"] == "baritone" or run["development"] or run["scored"]:
                raise ValueError(
                    "Reset qualification has no entrant or scoring overrides"
                )
            if run["seeds"] is not None:
                raise ValueError("Reset qualification uses seed, not seeds")
            if run["course"] == "C1" and run["seed"] not in (None, 0):
                raise ValueError("C1 reset uses the frozen seed zero")
        elif run["course"] == "C1":
            if run["entrant"] == "baritone" or run["scored"]:
                raise ValueError("C1 execution supports only its deterministic entrant")
            if run["seeds"] not in (None, [0]) or run["seed"] not in (None, 0):
                raise ValueError("C1 execution uses the frozen seed zero")
        else:
            if run["count"] != 1 or run["development"] or run["seed"] is not None:
                raise ValueError(
                    "Course execution uses seeds, not count/seed/development"
                )
            if run["entrant"] == "baritone" and run["scored"]:
                raise ValueError("Baritone execution does not support scored mode")
        if run["seeds"] is not None and (
            not 1 <= len(run["seeds"]) <= 20
            or len(set(run["seeds"])) != len(run["seeds"])
            or any(not 0 <= seed < 2**31 for seed in run["seeds"])
        ):
            raise ValueError(
                "Course seeds must be distinct 31-bit nonnegative integers"
            )
    if test == "models" and not config["model"]["backend"]:
        raise ValueError("Model run requires a configured backend")
    if test == "battery":
        if not run["output"]:
            raise ValueError("Battery requires an output folder")
        program = runpy.run_path(str(ROOT / "scripts/run-battery.py"))
        if run["checks"] is not None and (
            not run["checks"]
            or any(name not in program["CHECKS"] for name in run["checks"])
        ):
            raise ValueError("Unknown or empty battery check selection")
    if test == "companion":
        if run["check"] not in CHECKS:
            raise ValueError("Unknown companion check")
        if run["check"] not in CHECKS_WITH_FLAGS and run["arguments"]:
            raise ValueError("Selected check accepts no test arguments")


def parse_battery_args(parser):
    """Retain battery flags while applying shared preset/config precedence."""
    from mclab.run_config import resolve_config

    parser.add_argument("--run-config", type=Path)
    parser.add_argument("--preset")
    parser.add_argument("--print-config", action="store_true")
    parser.add_argument(
        "--timeout", type=int, help="override each selected check's wall timeout"
    )
    for action in parser._actions:
        if action.dest != "help":
            action.default = argparse.SUPPRESS
    supplied = vars(parser.parse_args())
    if supplied.get("list"):
        return argparse.Namespace(list=True)
    try:
        base = (
            json.loads(supplied["run_config"].read_text(encoding="utf-8"))
            if supplied.get("run_config")
            else None
        )
        overrides = {
            "run": {
                target: str(value) if isinstance(value, Path) else value
                for source, target in (
                    ("out", "output"),
                    ("only", "checks"),
                    ("no_restart", "no_restart"),
                    ("timeout", "timeout"),
                )
                if (value := supplied.get(source)) is not None
            }
        }
        config = resolve_config(
            "battery", preset=supplied.get("preset"), base=base, overrides=overrides
        )
        if not config["run"]["output"]:
            from mclab.qualification_battery import default_out

            config["run"]["output"] = default_out().as_posix()
        validate_launch(config)
        if supplied.get("print_config"):
            print(json.dumps(config, indent=2))
            raise SystemExit(0)
        run = config["run"]
        return argparse.Namespace(
            list=False,
            out=Path(run["output"]),
            only=run["checks"],
            no_restart=run["no_restart"],
            timeout=run["timeout"],
            run_config=config,
            keep_all=bool(supplied.get("keep_all")),
            fast=bool(supplied.get("fast")),
        )
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))


def main(argv=None):
    """One shared-config CLI, also exposed as ``mclab run``."""
    from mclab.run_config import TESTS, resolve_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", choices=TESTS)
    parser.add_argument("--run-config", type=Path)
    parser.add_argument("--preset")
    parser.add_argument("--print-config", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--start", nargs=3, type=int)
    parser.add_argument("--count", type=int)
    parser.add_argument("--backend")
    parser.add_argument("--request-limit", type=int)
    args = parser.parse_args(argv)
    try:
        base = (
            json.loads(args.run_config.read_text(encoding="utf-8"))
            if args.run_config
            else None
        )
        test = args.test or (base or {}).get("run", {}).get("test")
        overrides = {
            "run": {
                k: getattr(args, k)
                for k in ("seed", "start", "count")
                if getattr(args, k) is not None
            },
            "model": {
                k: getattr(args, k)
                for k in ("backend", "request_limit")
                if getattr(args, k) is not None
            },
        }
        config = resolve_config(
            test, preset=args.preset, base=base, overrides=overrides
        )
        if args.print_config:
            print(json.dumps(config, indent=2))
            return 0
        result = launch_config(
            config, invocation=list(sys.argv[1:]) if argv is None else list(argv)
        )
        if isinstance(result, dict):
            print(json.dumps(result, indent=2))
            return (
                0
                if result.get("status") not in {"invalid", "failed", "not_ready"}
                else 1
            )
        return result or 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
