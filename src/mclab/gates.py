"""Reviewed gate evidence is separate from volatile server presence snapshots."""

from mclab.paths import ROOT, digest, read_json


def statuses() -> dict:
    ledger = read_json(ROOT / "docs/gates.json")
    return {
        name: value["status"]
        for name, value in ledger.items()
        if name.startswith("G") and isinstance(value, dict)
    }


def inspected(spec_hash: str) -> bool:
    gate = read_json(ROOT / "docs/gates.json")["G0"]
    return (
        gate["status"] == "passed"
        and gate["spec_hash"] == spec_hash
        and gate["human_join_observed"]
        and bool(gate["human_inspection_quote"])
    )


def require_initial_profile(course: str, capabilities: tuple[str, ...] = ()) -> dict:
    """Only the frozen, reviewed scope may be used by subsequent C1 runners."""
    gate = read_json(ROOT / "docs/gates.json")["G1"]
    if gate["status"] != "passed":
        raise ValueError("G1 initial profile has not passed")
    profile = read_json(ROOT / "config/initial_body_profile.json")
    if profile["profile_id"] != gate.get("profile_id") or digest(profile) != gate.get(
        "profile_hash"
    ):
        raise ValueError("Qualified body profile has drifted; requalification required")
    dependencies = read_json(ROOT / "config/dependencies.lock.json")
    if digest(dependencies) != profile["dependency_lock_hash"]:
        raise ValueError("Physics dependencies changed; requalification required")
    if course not in profile["qualified_courses"]:
        raise ValueError(f"Course {course} is outside the qualified body profile")
    missing = set(capabilities) - set(profile["qualified_capabilities"])
    if missing:
        raise ValueError(f"Unqualified capabilities: {', '.join(sorted(missing))}")
    return profile


def require_scored_execution(mod_source_hash: str, sensor_profile: str) -> None:
    gate = read_json(ROOT / "docs/gates.json")["G2"]
    if gate["status"] != "passed":
        raise ValueError("G2 must pass before scored execution")
    if gate.get("mod_source_hash") != mod_source_hash:
        raise ValueError("G2 evaluator/reset source changed; requalification required")
    if sensor_profile != gate.get("sensor_profile"):
        raise ValueError("G2 sensor profile changed; requalification required")
