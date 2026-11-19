import json
import runpy
import sys

import pytest

from mclab.paths import ROOT
from mclab.run_provenance import record_run


def _write_checkpoint(tmp_path):
    from mclab.build_checkpoint import save

    build = tmp_path / "checkpoint-template.json"
    build.write_text('{"cells":[]}', encoding="utf-8")
    checkpoint = tmp_path / "final.json"
    save(
        checkpoint,
        {
            "version": 1,
            "start": [1, 70, 3],
            "seed": 12,
            "rotation": 90,
            "build": str(build),
            "origin": [8, 70, 9],
            "inventory": {},
            "cells": [],
        },
    )
    return checkpoint, build


@pytest.mark.parametrize("test", ["gather", "build"])
def test_real_cli_resolves_before_world_access(test, tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    config_file = tmp_path / "input.json"
    config_file.write_text(
        json.dumps(
            {
                "run": {"start": [1, 70, 3], "seed": 12},
                "planner": {"movement": {"max_gap": 0}},
            }
        )
    )
    program = runpy.run_path(str(ROOT / f"scripts/run-{test}.py"))

    def forbidden(*args, **kwargs):
        pytest.fail("Resolving configuration must not access the world or create a run")

    for name in (
        "record_run",
        "load_vanilla_pack",
        "exclusive_arena",
        "companion_world",
    ):
        program["main"].__globals__[name] = forbidden
    monkeypatch.setattr(
        sys,
        "argv",
        [
            f"run-{test}.py",
            "--run-config",
            str(config_file),
            "--seed",
            "14",
            "--print-config",
        ],
    )
    with pytest.raises(SystemExit) as error:
        program["main"]()
    assert error.value.code == 0
    config = json.loads(capsys.readouterr().out)
    assert config["run"]["seed"] == 14
    assert config["run"]["start"] == [1, 70, 3]
    assert config["planner"]["movement"]["max_gap"] == 0


def test_gather_requires_start_before_world_access(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    program = runpy.run_path(str(ROOT / "scripts/run-gather.py"))
    monkeypatch.setattr(sys, "argv", ["run-gather.py", "--print-config"])
    with pytest.raises(SystemExit) as error:
        program["main"]()
    assert error.value.code == 2


def test_build_cli_print_config_uses_checkpoint_values_before_cli_overrides(
    tmp_path, monkeypatch, capsys
):
    import mclab.run_config_cli as config_cli
    import mclab.run_launch as launch

    checkpoint, build = _write_checkpoint(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    competing_build = tmp_path / "wrong-template.json"
    competing_build.write_text(
        '{"cells":[[0,0,0,"minecraft:stone"]]}', encoding="utf-8"
    )
    monkeypatch.setattr(config_cli, "ROOT", workspace)
    monkeypatch.setattr(launch, "ROOT", workspace)
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    program = runpy.run_path(str(ROOT / "scripts/run-build.py"))
    globals_ = program["main"].__globals__

    def forbidden(*args, **kwargs):
        pytest.fail("print-config accessed the world or initialized a run")

    for name in (
        "launch_record",
        "record_run",
        "load_vanilla_pack",
        "exclusive_arena",
        "companion_world",
    ):
        globals_[name] = forbidden
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-build.py",
            "--resume",
            str(checkpoint),
            "--start",
            "99",
            "88",
            "77",
            "--seed",
            "77",
            "--rotation",
            "180",
            "--build",
            str(competing_build),
            "--print-config",
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        program["main"]()

    assert exit_info.value.code == 0
    config = json.loads(capsys.readouterr().out)
    assert config["run"]["start"] == [1, 70, 3]
    assert config["run"]["seed"] == 12
    assert config["run"]["rotation"] == 90
    assert config["run"]["build"] == str(build)
    assert config["run"]["origin"] == [8, 70, 9]
    assert config["run"]["resume"] == str(checkpoint)


def test_build_initialization_failure_records_effective_checkpoint_and_inputs(
    tmp_path, monkeypatch
):
    import mclab.run_config_cli as config_cli
    import mclab.run_launch as launch

    checkpoint, build = _write_checkpoint(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(config_cli, "ROOT", workspace)
    monkeypatch.setattr(launch, "ROOT", workspace)
    monkeypatch.setattr(launch, "LAUNCHES", workspace / "results/launches")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    program = runpy.run_path(str(ROOT / "scripts/run-build.py"))
    globals_ = program["main"].__globals__
    result_folder = tmp_path / "native-result"
    globals_["ROOT"] = workspace
    globals_["results_folder"] = lambda: result_folder

    def fail():
        raise RuntimeError("knowledge unavailable after admission")

    globals_["load_vanilla_pack"] = fail
    monkeypatch.setattr(sys, "argv", ["run-build.py", "--resume", str(checkpoint)])
    with pytest.raises(RuntimeError, match="knowledge unavailable after admission"):
        program["main"]()

    config = json.loads((result_folder / "run_config.json").read_text("utf-8"))
    assert config["run"]["start"] == [1, 70, 3]
    assert config["run"]["seed"] == 12
    assert config["run"]["rotation"] == 90
    assert config["run"]["build"] == str(build)
    assert config["run"]["origin"] == [8, 70, 9]
    provenance = json.loads((result_folder / "provenance.json").read_text("utf-8"))
    assert provenance["run_identity"]["effective_origin"]["value"] == [8, 70, 9]
    build_input = provenance["referenced_inputs"]["input_hashes"]["run.build"]
    assert (
        build_input["sha256"]
        == __import__("hashlib").sha256(build.read_bytes()).hexdigest()
    )
    assert (
        provenance["referenced_inputs"]["input_hashes"]["run.resume"]["status"]
        == "hashed"
    )
    launch_folder = next((workspace / "results/launches").iterdir())
    for name in ("run_config.json", "provenance.json"):
        assert (launch_folder / name).read_bytes() == (
            result_folder / name
        ).read_bytes()


@pytest.mark.parametrize("test", ["gather", "build"])
def test_runner_records_identity_before_initialization_failure(
    test, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    program = runpy.run_path(str(ROOT / f"scripts/run-{test}.py"))
    globals_ = program["main"].__globals__
    folder = tmp_path / "run"
    globals_["results_folder"] = lambda: folder
    globals_["record_run"] = lambda folder, config, **kwargs: record_run(
        folder, config, root=tmp_path, **kwargs
    )

    def fail():
        raise RuntimeError("knowledge unavailable")

    globals_["load_vanilla_pack"] = fail
    monkeypatch.setattr(sys, "argv", [f"run-{test}.py", "--start", "1", "70", "3"])
    with pytest.raises(RuntimeError, match="knowledge unavailable"):
        program["main"]()
    config = json.loads((folder / "run_config.json").read_text())
    assert config["run"]["start"] == [1, 70, 3]
    assert (folder / "provenance.json").exists()
