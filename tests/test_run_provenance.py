import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from mclab.run_provenance import record_run


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    for rel, value in {
        "src/mclab/app.py": "initial source\n",
        "config/lab.json": '{"world_seed": 5}\n',
        "config/backend-secrets.json": '{"token":"DO_NOT_HASH"}\n',
        ".gitignore": "runtime/\n",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    for mod in ("mcb-body", "mcb-lab"):
        path = root / "runtime/server/mods" / f"{mod}-0.1.0.jar"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((mod + " binary").encode())
    return root


def _record(tmp_path: Path, root: Path, *, config=None):
    return record_run(
        tmp_path / "results/run-1",
        config
        or {
            "run": {"seed": 4},
            "model": {
                "backend_id": "local",
                "api_key": "SECRET_VALUE",
                "prompt_version": "v7",
            },
        },
        invocation=["run", "--preset", "smoke", "--api-key", "CLI_SECRET"],
        root=root,
    )


def test_clean_run_writes_pair_with_actual_installed_mod_hashes_and_redaction(tmp_path):
    root = _repo(tmp_path)
    folder = tmp_path / "results/run-1"
    record_run(
        folder,
        {
            "run": {"seed": 4},
            "model": {
                "backend_id": "local",
                "api_key": "SECRET_VALUE",
                "prompt_version": "v7",
            },
        },
        invocation=["run", "--preset", "smoke", "--api-key", "CLI_SECRET"],
        root=root,
    )
    config = json.loads((folder / "run_config.json").read_text())
    provenance = json.loads((folder / "provenance.json").read_text())
    assert provenance["git"]["dirty"] is False
    assert provenance["installed_mods"]["mcb-body"]["installed_jar_sha256"]
    assert (
        provenance["installed_mods"]["mcb-body"][
            "recorded_build_matches_current_source"
        ]
        is None
    )
    assert provenance["model"]["prompt_identity"]["status"] == "identified"
    assert provenance["model"]["prompt_identity"]["selection_status"] == "unknown"
    assert config["model"]["api_key"] == "[REDACTED]"
    assert provenance["invocation"][-1] == "[REDACTED]"
    serialized = (folder / "run_config.json").read_text() + (
        folder / "provenance.json"
    ).read_text()
    assert (
        "SECRET_VALUE" not in serialized
        and "CLI_SECRET" not in serialized
        and "DO_NOT_HASH" not in serialized
    )
    assert "config/backend-secrets.json" not in provenance["git"]["relevant_inputs"]


def test_dirty_and_untracked_relevant_inputs_change_fingerprint(tmp_path):
    root = _repo(tmp_path)
    before = _record(tmp_path, root)["provenance"]["git"]["relevant_inputs_sha256"]
    source = root / "src/mclab/app.py"
    source.write_text("modified source\n", encoding="utf-8")
    source.unlink()
    untracked = root / "scripts/new_check.py"
    untracked.parent.mkdir()
    untracked.write_text("new check\n", encoding="utf-8")
    observed = record_run(tmp_path / "results/run-2", {"run": {"seed": 4}}, root=root)[
        "provenance"
    ]["git"]
    assert observed["dirty"] is True
    assert observed["relevant_inputs_sha256"] != before
    assert observed["relevant_inputs"]["src/mclab/app.py"] == "deleted"
    assert observed["relevant_inputs"]["scripts/new_check.py"]


def test_course_profiles_and_client_bridge_sources_bind_relevant_input_digest(
    tmp_path,
):
    root = _repo(tmp_path)
    before = _record(tmp_path, root)["provenance"]["git"]
    for rel, value in {
        "courses/profiles.json": '{"C2":{"spawn":[1,2,3]}}',
        "client-bridge/src/ControlProfile.java": "final class ControlProfile {}\n",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    observed = record_run(
        tmp_path / "results/run-course-and-client", {"run": {"seed": 4}}, root=root
    )["provenance"]["git"]
    assert observed["relevant_inputs_sha256"] != before["relevant_inputs_sha256"]
    assert observed["relevant_inputs"]["courses/profiles.json"]
    assert observed["relevant_inputs"]["client-bridge/src/ControlProfile.java"]
    assert "config/backend-secrets.json" not in observed["relevant_inputs"]


def test_idempotent_repeat_and_conflicting_rewrite_refused(tmp_path):
    root = _repo(tmp_path)
    folder = tmp_path / "results/run-1"
    first = record_run(folder, {"run": {"seed": 4}}, root=root)
    assert record_run(folder, {"run": {"seed": 4}}, root=root) == first
    with pytest.raises(FileExistsError):
        record_run(folder, {"run": {"seed": 5}}, root=root)
    assert json.loads((folder / "run_config.json").read_text()) == {"run": {"seed": 4}}


def test_existing_partial_pair_fails_closed(tmp_path):
    root = _repo(tmp_path)
    folder = tmp_path / "results/run-1"
    folder.mkdir(parents=True)
    (folder / "run_config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Partial"):
        record_run(folder, {}, root=root)
    assert not (folder / "provenance.json").exists()


def test_artifacts_survive_a_later_early_failure(tmp_path):
    root = _repo(tmp_path)
    folder = tmp_path / "results/run-fails-before-world"
    with pytest.raises(RuntimeError, match="simulated early failure"):
        record_run(folder, {"run": {"seed": 4}}, root=root)
        raise RuntimeError("simulated early failure")
    assert (folder / "run_config.json").is_file()
    assert (folder / "provenance.json").is_file()


def test_checkpoint_build_and_selected_preset_are_hashed_without_contents(tmp_path):
    root = _repo(tmp_path)
    preset = root / "config/presets/smoke.json"
    preset.parent.mkdir(parents=True)
    preset.write_text('{"run":{"seed":9}}', encoding="utf-8")
    build = root / "tests/fixtures/house.build.json"
    build.parent.mkdir(parents=True)
    build.write_text('{"blocks":["minecraft:stone"]}', encoding="utf-8")
    resume = root / "results/checkpoint.json"
    resume.parent.mkdir()
    resume.write_text('{"seed":9,"origin":[4,5,6]}', encoding="utf-8")
    selected = root / "config/run.json"
    selected.write_text('{"run":{"seed":9}}', encoding="utf-8")
    result = record_run(
        tmp_path / "results/run-checkpoint",
        {"run": {"build": str(build), "resume": str(resume)}},
        invocation=["run", "--preset", "smoke", "--run-config", str(selected)],
        root=root,
    )["provenance"]
    hashes = result["referenced_inputs"]["input_hashes"]
    assert hashes["run.build"]["status"] == "hashed"
    assert hashes["run.resume"]["status"] == "hashed"
    assert hashes["preset:smoke"]["status"] == "hashed"
    assert hashes["invocation.--run-config"]["status"] == "hashed"
    assert result["run_identity"]["checkpoint"]["origin"] == [4, 5, 6]
    assert result["run_identity"]["effective_origin"] == {
        "status": "identified",
        "value": [4, 5, 6],
        "source": "resume_checkpoint",
    }
    assert "blocks" not in json.dumps(result)


def test_backend_config_reference_is_not_hashed(tmp_path):
    root = _repo(tmp_path)
    result = record_run(
        tmp_path / "results/run-secret-ref",
        {},
        invocation=["run", "--run-config", "config/backend-secrets.json"],
        root=root,
    )["provenance"]
    entry = result["referenced_inputs"]["input_hashes"]["invocation.--run-config"]
    assert entry["status"] == "excluded_sensitive_input"
    assert entry["sha256"] is None


def test_natural_route_data_and_dashboard_profile_references_are_bound(tmp_path):
    root = _repo(tmp_path)
    for rel, value in {
        "config/profiles/routes/hill.json": '{"routes":[]}',
        "config/profiles/movement/dev.json": '{"allow_swim":true}',
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True)
        path.write_text(value, encoding="utf-8")
    route_data = [
        {"route_id": "ridge", "start": [1, 64, 2], "goal": [9, 68, 12], "yaw": 0}
    ]
    provenance = record_run(
        tmp_path / "results/natural-run",
        {
            "run": {
                "test": "natural",
                "seed": -7,
                "route_data": route_data,
                "route_ids": ["ridge"],
                "entrants": ["segmented_goal"],
            }
        },
        input_references={
            "routes": "config/profiles/routes/hill.json",
            "movement_profile": "config/profiles/movement/dev.json",
        },
        root=root,
    )["provenance"]
    identity = provenance["run_identity"]
    assert identity["route_data"] == route_data
    assert identity["route_ids"] == ["ridge"]
    hashes = provenance["referenced_inputs"]["input_hashes"]
    assert hashes["selected.routes"]["status"] == "hashed"
    assert hashes["selected.movement_profile"]["status"] == "hashed"


def test_model_prompt_identity_is_readable_and_prompt_hashes_are_single_pass(tmp_path):
    root = _repo(tmp_path)
    instructions = "Plan one bounded action."
    config = {
        "run": {"test": "natural"},
        "model": {
            "backend": "local",
            "model": "test-model",
            "deadline_s": 12.5,
            "request_limit": 3,
            "prompt_identity": "planner-demo-v2",
            "planner_backend": "secondary",
            "planner_model": "planner-model",
            "planner_request_limit": 2,
            "planner_prompt_identity": "plan-v4",
            "planner_profile": {
                "instructions": instructions,
                "candidates": {"go": "Move one step"},
                "planner_interval_s": 5,
                "plan_ttl_ticks": 200,
                "planner_failure": "fail_attempt",
            },
        },
    }
    folder = tmp_path / "results/model-run"
    provenance = record_run(folder, config, root=root)["provenance"]
    stored = json.loads((folder / "run_config.json").read_text(encoding="utf-8"))
    assert stored["model"]["prompt_identity"] == "planner-demo-v2"
    assert stored["model"]["planner_profile"]["instructions"] == instructions
    ids = provenance["model"]["identifiers"]["values"]
    assert ids["model"] == "test-model"
    assert ids["planner_backend"] == "secondary"
    instruction_hash = hashlib.sha256(instructions.encode()).hexdigest()
    assert ids["planner_profile"]["instructions_sha256"] == instruction_hash
    prompts = provenance["model"]["prompt_identity"]
    assert prompts["entries"]["model.prompt_identity"] == {
        "identity": "planner-demo-v2"
    }
    assert prompts["entries"]["model.planner_prompt_identity"] == {
        "identity": "plan-v4"
    }
    assert (
        prompts["entries"]["model.planner_profile.instructions"]["sha256"]
        == instruction_hash
    )
    assert prompts["selected"] is None
    assert prompts["available_profiles"]["tactical"]["profile_id"] == (
        "bounded-visible-target-tactical-v1"
    )


def test_tactical_prompt_identity_selected_from_model_field(tmp_path):
    root = _repo(tmp_path)
    result = record_run(
        tmp_path / "results/tactical-run",
        {
            "run": {"test": "models"},
            "model": {"prompt_identity": "bounded-visible-target-tactical-v1"},
        },
        root=root,
    )["provenance"]
    prompts = result["model"]["prompt_identity"]
    assert prompts["status"] == "identified"
    assert prompts["selected"] == "bounded-visible-target-tactical-v1"
    assert prompts["available_profiles"]["tactical"]["instructions_sha256"]
