import pytest

from mclab import gates
from mclab.paths import digest, write_json


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
