"""Contracts for phase-one launch recording and dispatch.

All runner, provider, and world boundaries are replaced with recording fakes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from mclab import run_launch
from mclab.run_config import resolve_config


def _recording_launch(monkeypatch):
    @contextmanager
    def record(config, **_kwargs):
        yield {"config": config, "attached": set()}

    monkeypatch.setattr(run_launch, "launch_record", record)


def _natural_config():
    return resolve_config(
        "natural",
        overrides={
            "run": {
                "seed": 41,
                "route_data": [
                    {
                        "route_id": "R1",
                        "start": [0, 64, 0],
                        "goal": [4, 64, 0],
                        "yaw": 90.0,
                    }
                ],
                "entrants": ["baritone", "segmented_goal"],
                "conditions": [False],
                "baritone_settings": {"allowParkour": True},
                "flee": {"trigger": 5.0, "creeper_trigger": 7.0, "steps": 3},
            },
            "body": {"control_profile": "dev-pipelined-v1", "view_distance": 8},
            "planner": {
                "terrain": {"allow_break": False},
                "movement": {"allow_climb": True},
                "memory": {"memory_mode": "session"},
            },
        },
    )


def _backend_specs():
    return {
        "backends": [
            {
                "name": "tactical-local",
                "enabled": True,
                "model": "tactical-model",
                "root": "http://127.0.0.1:8080",
                "deadline_s": 1.0,
                "request_limit": 10,
            },
            {
                "name": "planner-local",
                "enabled": True,
                "model": "planner-model",
                "root": "http://127.0.0.1:8081",
                "deadline_s": 1.0,
                "request_limit": 10,
            },
        ]
    }


def test_launch_record_survives_startup_failure_and_attaches_same_identity(
    tmp_path, monkeypatch
):
    config = {"schema_version": 1, "run": {"test": "gather"}}
    launch = tmp_path / "launch"
    result_folder = tmp_path / "results" / "gather-1"

    with pytest.raises(RuntimeError, match="arena lock"):
        with run_launch.launch_record(
            config, folder=launch, invocation=["--start", "1", "2", "3"]
        ):
            run_launch.attach_run(result_folder)
            raise RuntimeError("arena lock unavailable")

    for folder in (launch, result_folder):
        assert (folder / "run_config.json").is_file()
        assert (folder / "provenance.json").is_file()
        assert (
            json.loads((folder / "launch_status.json").read_text())["status"]
            == "failed"
        )
    assert (launch / "run_config.json").read_bytes() == (
        result_folder / "run_config.json"
    ).read_bytes()
    assert (launch / "provenance.json").read_bytes() == (
        result_folder / "provenance.json"
    ).read_bytes()


def test_attach_run_refuses_conflicting_preexisting_identity(tmp_path, monkeypatch):
    import mclab.run_provenance as provenance

    def fake_record(folder, config, **_kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "run_config.json").write_text('{"run":{}}\n')
        (folder / "provenance.json").write_text('{"schema_version":1}\n')
        return {}

    monkeypatch.setattr(provenance, "record_run", fake_record)
    target = tmp_path / "already-there"
    target.mkdir()
    (target / "run_config.json").write_text("different\n")
    config = {"schema_version": 1, "run": {"test": "gather"}}

    with run_launch.launch_record(config, folder=tmp_path / "launch"):
        with pytest.raises(ValueError, match="conflicts"):
            run_launch.attach_run(target)


def test_child_launch_reuses_admitted_snapshot_and_rejects_mismatch(
    tmp_path, monkeypatch
):
    from mclab.run_provenance import record_run

    config = {"schema_version": 1, "run": {"test": "gather", "seed": 13}}
    admitted = tmp_path / "admitted"
    record_run(admitted, config, root=tmp_path)
    original_config_bytes = (admitted / "run_config.json").read_bytes()
    original_provenance_bytes = (admitted / "provenance.json").read_bytes()
    monkeypatch.setenv("MCB_LAUNCH_RECORD", str(admitted))

    attached = tmp_path / "native-result"
    with run_launch.launch_record(config, folder=tmp_path / "ignored-child") as state:
        assert state["folder"] == admitted
        config["run"]["seed"] = 99
        assert state["config"]["run"]["seed"] == 13
        run_launch.attach_run(attached)

    assert (attached / "run_config.json").read_bytes() == original_config_bytes
    assert (attached / "provenance.json").read_bytes() == original_provenance_bytes
    assert (admitted / "run_config.json").read_bytes() == original_config_bytes

    mismatched = {"schema_version": 1, "run": {"test": "gather", "seed": 14}}
    with pytest.raises(ValueError, match="differs from its admitted configuration"):
        with run_launch.launch_record(mismatched, folder=tmp_path / "rejected-child"):
            pytest.fail("mismatched child launch entered the run")
    assert not (tmp_path / "rejected-child").exists()
    assert (admitted / "provenance.json").read_bytes() == original_provenance_bytes


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (0, "finished"),
        (1, "failed"),
        ({"status": "invalid"}, "failed"),
        ({"status": "stopped"}, "interrupted"),
        ({"status": "finished", "returncode": 2}, "failed"),
        ({"attempts": [{"status": "failed"}]}, "failed"),
        ({"status": "finished", "error": "cleanup failed"}, "failed"),
    ],
)
def test_launch_outcome_tracks_invocation_state(tmp_path, result, expected):
    config = {"schema_version": 1, "run": {"test": "gather"}}
    folder = tmp_path / "launch"
    with run_launch.launch_record(config, folder=folder):
        run_launch.record_outcome(result)
    assert json.loads((folder / "launch_status.json").read_text())["status"] == expected


def test_invalid_readiness_fails_before_record_or_dispatch(monkeypatch):
    config = resolve_config("natural")
    monkeypatch.setattr(
        run_launch,
        "launch_record",
        lambda *_args, **_kwargs: pytest.fail("invalid config was recorded"),
    )
    monkeypatch.setattr(
        run_launch,
        "execute_config",
        lambda _config: pytest.fail("invalid config reached a runner"),
    )
    with pytest.raises(ValueError, match="Natural run requires seed and route_data"):
        run_launch.launch_config(config)


def test_natural_dispatch_preserves_route_and_all_selected_controls(monkeypatch):
    from mclab import natural
    from mclab.memory_policy import MemoryPolicy
    from mclab.movement_policy import MovementPolicy
    from mclab.terrain_steps import TerrainPolicy

    config = _natural_config()
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "finished"}

    monkeypatch.setattr(natural, "run", fake_run)
    result = run_launch.execute_config(config)

    args, kwargs = calls[0]
    assert result["status"] == "finished"
    assert args == (41, config["run"]["route_data"])
    assert kwargs["entrants"] == ("baritone", "segmented_goal")
    assert kwargs["conditions"] == (False,)
    assert kwargs["baritone_settings"] == {"allowParkour": True}
    assert kwargs["flee"] == {"trigger": 5.0, "creeper_trigger": 7.0, "steps": 3}
    assert kwargs["control_profile"] == "dev-pipelined-v1"
    assert kwargs["view_distance"] == 8
    assert isinstance(kwargs["terrain_policy"], TerrainPolicy)
    assert kwargs["terrain_policy"].allow_break is False
    assert isinstance(kwargs["movement_policy"], MovementPolicy)
    assert kwargs["movement_policy"].allow_climb is True
    assert isinstance(kwargs["memory_policy"], MemoryPolicy)
    assert kwargs["memory_policy"].memory_mode == "session"


def test_tandem_model_dispatch_uses_selected_models_profile_and_caps(monkeypatch):
    from mclab import model_execution

    config = resolve_config(
        "models",
        overrides={
            "run": {"count": 2},
            "model": {
                "backend": "tactical-local",
                "request_limit": 3,
                "planner_backend": "planner-local",
                "planner_request_limit": 5,
                "planner_profile": {
                    "instructions": "Keep a visible target in view.",
                    "candidates": {"track": "Track target", "wait": "Wait"},
                    "planner_interval_s": 1.5,
                    "plan_ttl_ticks": 90,
                    "planner_failure": "continue_without_plan",
                },
            },
        },
        backend_specs=_backend_specs(),
    )
    calls = []
    monkeypatch.setattr(
        model_execution,
        "run_tandem",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "tandem-result",
    )
    monkeypatch.setattr(
        model_execution,
        "run",
        lambda *_args, **_kwargs: pytest.fail("smoke backend chosen for tandem"),
    )

    assert run_launch.execute_config(config) == "tandem-result"
    args, kwargs = calls[0]
    assert args == ("tactical-local", "planner-local")
    assert kwargs["count"] == 2
    assert kwargs["request_limit"] == 3
    assert kwargs["planner_request_limit"] == 5
    assert kwargs["planner_profile"]["instructions"] == "Keep a visible target in view."
    assert kwargs["planner_profile"]["planner_failure"] == "continue_without_plan"


@pytest.mark.parametrize(
    ("run_values", "runner_name", "method", "expected_args"),
    [
        (
            {"course": "C1", "operation": "reset", "count": 2},
            "resetcheck",
            "qualify_resets",
            (2,),
        ),
        ({"course": "C1", "count": 3}, "execution", "qualify_execution", (3,)),
        (
            {"course": "C2", "operation": "reset", "seed": 9, "count": 4},
            "navigation_runner",
            "qualify_resets",
            ("C2", 9, 4),
        ),
        ({"course": "C2", "seeds": [9]}, "navigation_runner", "execute", ("C2", [9])),
        (
            {"course": "C2", "entrant": "baritone", "seeds": [9]},
            "baritone_runner",
            "execute",
            ("C2", [9]),
        ),
    ],
)
def test_course_dispatch_keeps_reset_and_execution_paths_separate(
    monkeypatch, run_values, runner_name, method, expected_args
):
    from mclab import baritone_runner, execution, navigation_runner, resetcheck

    config = resolve_config("courses", overrides={"run": run_values})
    calls = []
    runner = {
        "baritone_runner": baritone_runner,
        "execution": execution,
        "navigation_runner": navigation_runner,
        "resetcheck": resetcheck,
    }[runner_name]
    monkeypatch.setattr(
        runner,
        method,
        lambda *args, **kwargs: calls.append((args, kwargs)) or "course-result",
    )

    assert run_launch.execute_config(config) == "course-result"
    args, kwargs = calls[0]
    assert args == expected_args
    if method == "execute" and runner_name == "navigation_runner":
        assert kwargs == {"scored": False}
    elif method == "qualify_execution":
        assert kwargs == {"development": False}
    else:
        assert kwargs == {}


def test_companion_dispatch_uses_fixed_script_and_preserves_arguments(
    tmp_path, monkeypatch
):
    config = resolve_config(
        "companion",
        overrides={"run": {"check": "natural-ops", "arguments": ["--mode", "fast"]}},
    )
    monkeypatch.setattr(run_launch, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(
        run_launch,
        "_script",
        lambda path, args: calls.append((path, args)) or 0,
    )

    assert run_launch.execute_config(config) == 0
    assert calls == [(tmp_path / "scripts/check-natural-ops.py", ["--mode", "fast"])]


@pytest.mark.parametrize("family", ["gather", "build"])
def test_gather_and_build_dispatch_with_resolved_config_file(
    family, tmp_path, monkeypatch
):
    config = resolve_config(
        family,
        overrides={"run": {"start": [10, 64, -5], "seed": 23}},
    )
    monkeypatch.setattr(run_launch, "ROOT", tmp_path)
    calls = []

    def fake_script(path, arguments):
        calls.append((path, arguments, json.loads(Path(arguments[1]).read_text())))
        return "runner-result"

    monkeypatch.setattr(run_launch, "_script", fake_script)
    assert run_launch.execute_config(config) == "runner-result"
    path, arguments, saved = calls[0]
    assert path == tmp_path / f"scripts/run-{family}.py"
    assert arguments[0] == "--run-config"
    assert saved == config


def test_battery_dispatch_forwards_selected_checks_and_runtime_limits(
    tmp_path, monkeypatch
):
    config = resolve_config(
        "battery",
        overrides={
            "run": {
                "output": "battery-output",
                "checks": ["animals", "crafting"],
                "no_restart": True,
                "timeout": 42,
            }
        },
    )
    monkeypatch.setattr(run_launch, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(
        run_launch,
        "_script",
        lambda path, args: calls.append((path, args)) or 0,
    )

    assert run_launch.execute_config(config) == 0
    assert calls == [
        (
            tmp_path / "scripts/run-battery.py",
            [
                "--out",
                "battery-output",
                "--only",
                "animals",
                "crafting",
                "--no-restart",
                "--timeout",
                "42",
            ],
        )
    ]


def test_script_run_print_config_has_no_launch_or_test_side_effects(
    monkeypatch, capsys
):
    calls = []

    @run_launch.script_run("containers")
    def check():
        calls.append("check")
        return 0

    monkeypatch.setattr(sys, "argv", ["check-containers.py", "--print-config"])
    monkeypatch.setattr(
        run_launch,
        "launch_record",
        lambda *_args, **_kwargs: pytest.fail("print-config started a launch"),
    )
    assert check() == 0
    assert calls == []
    assert json.loads(capsys.readouterr().out)["run"]["check"] == "containers"


def test_shared_cli_print_config_does_not_launch_or_dispatch(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "natural.json"
    path.write_text(json.dumps(_natural_config()), encoding="utf-8")
    monkeypatch.setattr(
        run_launch,
        "launch_config",
        lambda *_args, **_kwargs: pytest.fail("print-config launched a run"),
    )

    assert run_launch.main(["--run-config", str(path), "--print-config"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["run"]["route_data"][0]["route_id"] == "R1"


def test_script_run_forwards_only_allowed_fixed_script_arguments(monkeypatch):
    seen = []

    @run_launch.script_run("natural-ops")
    def check():
        seen.append(sys.argv[1:])
        return 0

    _recording_launch(monkeypatch)
    monkeypatch.setattr(
        sys, "argv", ["check-natural-ops.py", "--mode", "bounded", "--trace"]
    )
    assert check() == 0
    assert seen == [["--mode", "bounded", "--trace"]]


def test_script_run_rejects_unknown_fixed_script_arguments_before_launch(monkeypatch):
    called = []

    @run_launch.script_run("containers")
    def check():
        called.append(True)

    monkeypatch.setattr(sys, "argv", ["check-containers.py", "--unowned-flag"])
    monkeypatch.setattr(
        run_launch,
        "launch_record",
        lambda *_args, **_kwargs: pytest.fail("invalid fixed args were recorded"),
    )
    with pytest.raises(SystemExit):
        check()
    assert called == []


def _battery_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument("--list", action="store_true")
    return parser


def test_battery_flags_merge_preset_run_config_then_explicit_values(
    tmp_path, monkeypatch
):
    import mclab.run_config as run_config

    preset_root = tmp_path / "config/presets"
    preset_root.mkdir(parents=True)
    awareness = tmp_path / "config/stepc/awareness-sections-64.json"
    awareness.parent.mkdir(parents=True)
    shutil.copyfile(
        run_config.ROOT / "config/stepc/awareness-sections-64.json", awareness
    )
    preset = resolve_config("battery")
    preset["run"].update(
        output="preset-output", checks=["animals"], timeout=100, no_restart=False
    )
    (preset_root / "phase1.json").write_text(json.dumps(preset), encoding="utf-8")
    base = resolve_config("battery")
    base["run"].update(
        output="base-output", checks=["containers"], timeout=80, no_restart=True
    )
    base_path = tmp_path / "base.json"
    base_path.write_text(json.dumps(base), encoding="utf-8")
    monkeypatch.setattr(run_config, "ROOT", tmp_path)
    monkeypatch.setattr(run_launch, "validate_launch", lambda _config: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-battery.py",
            "--preset",
            "phase1",
            "--run-config",
            str(base_path),
            "--out",
            "explicit-output",
            "--timeout",
            "35",
        ],
    )

    args = run_launch.parse_battery_args(_battery_parser())
    assert args.out == Path("explicit-output")
    assert args.only == ["containers"]
    assert args.no_restart is True
    assert args.timeout == 35


def test_battery_explicit_check_selection_wins_and_print_config_exits_cleanly(
    tmp_path, monkeypatch, capsys
):
    config = resolve_config("battery")
    config["run"].update(output="default-output", checks=["animals"])
    path = tmp_path / "battery.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(run_launch, "validate_launch", lambda _config: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-battery.py",
            "--run-config",
            str(path),
            "--only",
            "crafting",
            "--out",
            "out",
        ],
    )
    args = run_launch.parse_battery_args(_battery_parser())
    assert args.only == ["crafting"]

    monkeypatch.setattr(
        sys,
        "argv",
        ["run-battery.py", "--run-config", str(path), "--print-config"],
    )
    with pytest.raises(SystemExit) as exit_info:
        run_launch.parse_battery_args(_battery_parser())
    assert exit_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["run"]["checks"] == ["animals"]
