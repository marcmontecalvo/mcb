import json
from dataclasses import asdict

import pytest

from mclab.companion_body import DIG_TERRAIN, NATURAL_MOVEMENT
from mclab.gather_options import GatherOptions
from mclab.run_config import config_schema, policies, resolve_config


def test_defaults_are_effective_companion_policies_and_json_safe():
    config = resolve_config("gather")
    assert config["run"]["gather"] == asdict(GatherOptions())
    actual = policies(config)
    assert actual["movement_policy"] == NATURAL_MOVEMENT
    assert actual["terrain_policy"] == DIG_TERRAIN
    assert actual["awareness"].preset == "sections"
    assert json.loads(json.dumps(config)) == config


def test_base_preset_overrides_precedence(tmp_path):
    directory = tmp_path / "config" / "presets"
    directory.mkdir(parents=True)
    (directory / "test.json").write_text(
        json.dumps({"run": {"seed": 2}, "planner": {"movement": {"max_gap": 1}}})
    )
    config = resolve_config(
        "build",
        root=tmp_path,
        preset="test",
        base={"run": {"seed": 1}},
        overrides={"run": {"seed": 3}, "planner": {"movement": {"max_gap": 0}}},
    )
    assert config["run"]["seed"] == 3
    assert policies(config)["movement_policy"].max_gap == 0
    assert (
        resolve_config(
            "build", root=tmp_path, preset="test", base={"run": {"seed": 1}}
        )["run"]["seed"]
        == 1
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"run": {"unknown": 1}},
        {"run": {"seed": True}},
        {"run": {"start": [1, 2]}},
        {"run": {"gather": {"tool_choice": "invalid"}}},
        {"planner": {"terrain": {"protected_positions": [[1, "2", 3]]}}},
        {"planner": {"movement": {"max_gap": 9}}},
        {"model": {"backend": "unsupported"}},
        {"schema_version": 2},
        {"run": {"seed": 2**63}},
        {"run": {"start": [30_000_001, 0, 0]}},
        {"run": {"raw_iron": 0}},
        {"run": {"rotation": 90}},
    ],
)
def test_invalid_settings_are_rejected(overrides):
    with pytest.raises(ValueError):
        resolve_config("gather", overrides=overrides)


def test_caller_defaults_precede_preset_and_explicit_null(tmp_path):
    directory = tmp_path / "config" / "presets"
    directory.mkdir(parents=True)
    (directory / "null-seed.json").write_text(json.dumps({"run": {"seed": None}}))
    defaults = {"run": {"seed": 1337, "start": [1, 2, 3]}}
    assert resolve_config("gather", defaults=defaults)["run"]["seed"] == 1337
    run = resolve_config(
        "gather", defaults=defaults, preset="null-seed", root=tmp_path
    )["run"]
    assert run["seed"] is None
    assert run["start"] == [1, 2, 3]


def test_schema_exposes_nested_fields_and_unsupported_memory():
    schema = config_schema()["properties"]
    assert "tool_choice" in schema["run"]["properties"]["gather"]["properties"]
    planner = schema["planner"]["properties"]
    assert "memory_mode" in planner["memory"]["properties"]
    positions = planner["terrain"]["properties"]["protected_positions"]
    assert positions["items"]["items"]["type"] == "integer"
    assert schema["run"]["properties"]["iron"]["supported_tests"] == ["gather"]
    assert schema["run"]["properties"]["seed"]["defaults"]["gather"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"run": {"rotation": 45}},
        {"run": {"scaffolding": -1}},
        {"run": {"iron": True}},
    ],
)
def test_invalid_build_settings(overrides):
    with pytest.raises(ValueError):
        resolve_config("build", overrides=overrides)


def test_a_stage_lab_starts_from_a_stage_and_stops_at_another():
    run = resolve_config(
        "build", overrides={"run": {"start_from": "site_ready", "until": "structure"}}
    )["run"]
    assert run["start_from"] == "site_ready" and run["until"] == "structure"
    shown = resolve_config(
        "build", overrides={"run": {"start_from": "structure_done", "stage": True}}
    )["run"]
    assert shown["stage"] is True  # show a saved stage without building
    props = config_schema()["properties"]["run"]["properties"]
    assert props["start_from"]["enum"] == ["site_ready", "structure_done"]
    assert props["until"]["enum"] == ["site", "structure"]
    assert props["until"]["supported_tests"] == ["build"]


@pytest.mark.parametrize(
    "run",
    [
        {"start_from": "halfway"},
        {"until": "roof"},
        {"start_from": "site_ready", "resume": "x.json"},
        {"start_from": "site_ready", "supply": True},
        {"start_from": "site_ready", "empty": True},
        {"start_from": "site_ready", "until": "site"},  # already there
        {"start_from": "structure_done", "until": "structure"},
        {"until": "site", "stage": True, "resume": "x.json"},  # staging builds nothing
    ],
)
def test_impossible_stage_labs_are_refused(run):
    with pytest.raises(ValueError):
        resolve_config("build", overrides={"run": run})


def test_a_build_can_start_with_its_markers_showing():
    config = resolve_config("build", overrides={"run": {"show_markers": True}})
    assert config["run"]["show_markers"] is True
    supported = config_schema()["properties"]["run"]["properties"]["show_markers"]
    assert set(supported["supported_tests"]) == {"natural", "build"}


def test_a_build_shows_its_markers_unless_switched_off():
    # A build is watched in game: where the bot heads next and the route it plans
    # show without a flag; scored natural runs keep them off.
    assert resolve_config("build")["run"]["show_markers"] is True
    off = resolve_config("build", overrides={"run": {"show_markers": False}})
    assert off["run"]["show_markers"] is False
    assert resolve_config("natural")["run"]["show_markers"] is False


@pytest.mark.parametrize("family", ["courses", "models", "battery", "companion"])
def test_non_policy_families_do_not_claim_effective_policies(family):
    config = resolve_config(family)
    assert config["planner"] is None
    assert config["body"]["awareness"] is None
    with pytest.raises(ValueError):
        resolve_config(family, overrides={"run": {"iron": True}})


def test_natural_route_validation_and_memory_policy():
    route = {"route_id": "R1", "start": [0, 64, 0], "goal": [2, 64, 0], "yaw": 0.0}
    config = resolve_config(
        "natural",
        overrides={
            "run": {
                "seed": 7,
                "route_data": [route],
                "entrants": ["segmented_goal"],
                "conditions": [False],
            },
            "planner": {"memory": {"memory_mode": "session"}},
        },
    )
    assert policies(config)["memory_policy"].memory_mode == "session"
    with pytest.raises(ValueError):
        resolve_config(
            "natural", overrides={"run": {"route_data": [{**route, "start": [1]}]}}
        )


def test_natural_non_segmented_config_round_trips():
    config = resolve_config("natural")
    assert config["planner"] is None
    assert resolve_config("natural", base=config) == config
    assert set(policies(config)) == {"awareness"}


def test_natural_rejects_unconsumed_aliases():
    with pytest.raises(ValueError):
        resolve_config("natural", overrides={"run": {"entrant": "baritone"}})


def test_course_counts_match_runner():
    assert (
        resolve_config("courses", overrides={"run": {"count": 20}})["run"]["count"]
        == 20
    )
    with pytest.raises(ValueError):
        resolve_config("courses", overrides={"run": {"count": 21}})


def test_course_episode_qualification_has_fixed_settings():
    run = {"course": "C1", "operation": "episodes"}
    assert (
        resolve_config("courses", overrides={"run": run})["run"]["operation"]
        == "episodes"
    )
    for override in (
        {"course": "C2"},
        {"count": 2},
        {"seed": 1},
        {"development": True},
        {"scored": True},
        {"entrant": "baritone"},
    ):
        with pytest.raises(ValueError):
            resolve_config("courses", overrides={"run": {**run, **override}})


def test_model_backend_metadata_and_limits(tmp_path):
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "backends.json").write_text(
        json.dumps(
            {
                "backends": [
                    {
                        "name": "local",
                        "enabled": True,
                        "model": "example",
                        "request_limit": 4,
                        "deadline_s": 1.0,
                        "root": "http://private",
                        "key_env": "SECRET",
                    }
                ]
            }
        )
    )
    config = resolve_config(
        "models",
        root=tmp_path,
        overrides={"model": {"backend": "local", "request_limit": 4}},
    )
    assert config["model"]["model"] == "example"
    assert config["model"]["request_limit"] == 4
    assert "private" not in json.dumps(config) and "SECRET" not in json.dumps(config)
    with pytest.raises(ValueError):
        resolve_config(
            "models",
            root=tmp_path,
            overrides={"model": {"backend": "local", "request_limit": 5}},
        )


def test_injected_local_catalog_preserves_local_cap_semantics():
    spec = {
        "name": "local",
        "enabled": True,
        "model": "m",
        "root": "http://localhost:8080",
        "request_limit": 4,
    }
    resolved = resolve_config(
        "models",
        backend_specs={"backends": [spec]},
        overrides={"model": {"backend": "local"}},
    )
    assert resolved["model"]["request_limit"] == 40
    spec["root"] = "https://example.com"
    with pytest.raises(ValueError, match="configured remote"):
        resolve_config(
            "models", backend_specs=[spec], overrides={"model": {"backend": "local"}}
        )


@pytest.mark.parametrize("value", [True, "4", 1, 33])
def test_natural_view_distance_error(value):
    with pytest.raises(ValueError, match="view distance"):
        resolve_config("natural", overrides={"body": {"view_distance": value}})


def test_natural_repetitions_error():
    with pytest.raises(ValueError, match="repetitions"):
        resolve_config("natural", overrides={"run": {"repetitions": 4}})


def test_baritone_settings_are_typed_and_normalized():
    result = resolve_config("natural", overrides={"run": {"baritone_parkour": True}})
    assert result["run"]["baritone_settings"] == {"allowParkour": True}
    assert resolve_config("natural", base=result) == result
    for invalid in ({"unknown": True}, {"allowSprint": 1}):
        with pytest.raises(ValueError):
            resolve_config("natural", overrides={"run": {"baritone_settings": invalid}})
    with pytest.raises(ValueError):
        resolve_config(
            "natural",
            overrides={
                "run": {
                    "entrants": ["v8_goal"],
                    "baritone_settings": {"allowSprint": True},
                }
            },
        )


@pytest.mark.parametrize(
    "field,value",
    [("planner_model", "x"), ("planner_request_limit", 4)],
)
def test_planner_settings_without_planner_backend_are_rejected(tmp_path, field, value):
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "backends.json").write_text(
        json.dumps(
            {
                "backends": [
                    {
                        "name": "local",
                        "enabled": True,
                        "model": "example",
                        "request_limit": 4,
                        "deadline_s": 1.0,
                        "root": "http://127.0.0.1",
                    }
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="requires planner_backend"):
        resolve_config(
            "models",
            root=tmp_path,
            overrides={"model": {"backend": "local", "request_limit": 4, field: value}},
        )


def test_a_build_names_its_order_and_the_zone_order_is_the_default():
    assert resolve_config("build")["run"]["order"] == "zones"
    run = resolve_config("build", overrides={"run": {"order": "storeys"}})["run"]
    assert run["order"] == "storeys"
    props = config_schema()["properties"]["run"]["properties"]
    assert props["order"]["enum"] == ["zones", "storeys"]
    assert props["order"]["supported_tests"] == ["build"]
    with pytest.raises(ValueError):
        resolve_config("build", overrides={"run": {"order": "spiral"}})
