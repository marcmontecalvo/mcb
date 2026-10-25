import pytest

from mclab import gates
from mclab.paths import digest, read_json, write_json


@pytest.fixture
def qualified(tmp_path, monkeypatch):
    dependencies = {"minecraft": "pinned", "carpet": "pinned"}
    profile = {
        "profile_id": "test-v1",
        "dependency_lock_hash": digest(dependencies),
        "qualified_courses": ["C1"],
        "qualified_capabilities": ["mine", "look"],
    }
    write_json(tmp_path / "config/dependencies.lock.json", dependencies)
    write_json(tmp_path / "config/initial_body_profile.json", profile)
    write_json(
        tmp_path / "docs/gates.json",
        {
            "G1": {
                "status": "passed",
                "profile_id": "test-v1",
                "profile_hash": digest(profile),
            }
        },
    )
    monkeypatch.setattr(gates, "ROOT", tmp_path)
    return tmp_path


def test_qualified_scope_can_be_used(qualified):
    assert (
        gates.require_initial_profile("C1", ("mine", "look"))["profile_id"] == "test-v1"
    )


def test_unqualified_course_and_capability_are_rejected(qualified):
    with pytest.raises(ValueError, match="outside"):
        gates.require_initial_profile("C6")
    with pytest.raises(ValueError, match="Unqualified capabilities"):
        gates.require_initial_profile("C1", ("combat_knockback",))


def test_dependency_drift_cannot_reuse_old_qualification(qualified):
    write_json(qualified / "config/dependencies.lock.json", {"minecraft": "changed"})
    with pytest.raises(ValueError, match="Physics dependencies changed"):
        gates.require_initial_profile("C1")


def test_profile_drift_cannot_add_a_capability(qualified):
    write_json(
        qualified / "config/initial_body_profile.json", {"profile_id": "test-v1"}
    )
    with pytest.raises(ValueError, match="profile has drifted"):
        gates.require_initial_profile("C1")


def test_open_gate_cannot_start_a_runner(qualified):
    write_json(qualified / "docs/gates.json", {"G1": {"status": "in_progress"}})
    with pytest.raises(ValueError, match="has not passed"):
        gates.require_initial_profile("C1")


def test_scored_execution_cannot_reuse_different_evaluator_or_sensor(qualified):
    write_json(
        qualified / "docs/gates.json",
        {
            "G2": {
                "status": "passed",
                "mod_source_hash": "reviewed",
                "sensor_profile": "sensor-v1",
            }
        },
    )
    gates.require_scored_execution("reviewed", "sensor-v1")
    with pytest.raises(ValueError, match="source changed"):
        gates.require_scored_execution("changed", "sensor-v1")
    with pytest.raises(ValueError, match="sensor profile changed"):
        gates.require_scored_execution("reviewed", "sensor-v2")


@pytest.fixture
def navigation_candidate(qualified):
    base = read_json(qualified / "config/initial_body_profile.json")
    base.update(
        motor_profile={"max_observation_age_ticks": 5},
        excluded_capabilities=["construction"],
    )
    write_json(qualified / "config/initial_body_profile.json", base)
    ledger = read_json(qualified / "docs/gates.json")
    ledger["G1"]["profile_hash"] = digest(base)
    ledger["G2"] = {
        "status": "passed",
        "mod_source_hash": "native-v1",
        "sensor_profile": "local-visibility-v1",
    }
    write_json(qualified / "docs/gates.json", ledger)
    profile = {
        "profile_id": "navigation-c2-c5-survival-v1",
        "base_profile_id": base["profile_id"],
        "base_profile_hash": digest(base),
        "dependency_lock_hash": base["dependency_lock_hash"],
        "motor_profile": base["motor_profile"],
        "qualified_capabilities": base["qualified_capabilities"],
        "excluded_capabilities": base["excluded_capabilities"],
        "sensor_profile": "local-visibility-v1",
        "candidate_courses": ["C2", "C3", "C4", "C5"],
    }
    write_json(qualified / "config/navigation_body_profile.json", profile)
    write_json(qualified / "runtime/mod-build.json", {"source_hash": "native-v1"})
    return qualified, profile


def test_navigation_candidate_does_not_widen_initial_body_gate(navigation_candidate):
    _, profile = navigation_candidate
    assert gates.require_navigation_profile("C2") == profile
    with pytest.raises(ValueError, match="outside"):
        gates.require_initial_profile("C2")
    with pytest.raises(ValueError, match="outside"):
        gates.require_navigation_profile("C6")
    with pytest.raises(ValueError, match="has not passed"):
        gates.require_navigation_profile("C2", scored=True)


def test_navigation_scoring_requires_twenty_current_resets(navigation_candidate):
    root, profile = navigation_candidate
    entry = {
        "status": "passed",
        "profile_hash": digest(profile),
        "mod_source_hash": "native-v1",
        "reset_checks_passed": 20,
    }
    path = root / "docs/NAVIGATION_QUALIFICATION.json"
    write_json(path, {"courses": {"C2": entry}})
    assert gates.require_navigation_profile("C2", scored=True) == profile
    for field, value in (
        ("reset_checks_passed", 19),
        ("mod_source_hash", "old"),
        ("profile_hash", "old"),
    ):
        write_json(path, {"courses": {"C2": {**entry, field: value}}})
        with pytest.raises(ValueError, match="missing or stale"):
            gates.require_navigation_profile("C2", scored=True)


def test_navigation_cannot_add_unqualified_physics_capabilities(navigation_candidate):
    root, profile = navigation_candidate
    profile["qualified_capabilities"] = profile["qualified_capabilities"] + [
        "construction"
    ]
    write_json(root / "config/navigation_body_profile.json", profile)
    with pytest.raises(ValueError, match="drifted"):
        gates.require_navigation_profile("C2")
