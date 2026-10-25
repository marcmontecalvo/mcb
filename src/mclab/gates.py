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


def require_navigation_profile(
    course: str, capabilities: tuple[str, ...] = (), *, scored: bool = False
) -> dict:
    """Navigation has separate source-bound evidence; the C1 scope stays frozen."""
    base = require_initial_profile("C1", capabilities)
    profile = read_json(ROOT / "config/navigation_body_profile.json")
    if (
        profile["profile_id"] != "navigation-c2-c5-survival-v1"
        or profile["base_profile_id"] != base["profile_id"]
        or profile["base_profile_hash"] != digest(base)
        or profile["dependency_lock_hash"] != base["dependency_lock_hash"]
        or profile["motor_profile"] != base["motor_profile"]
        or profile["qualified_capabilities"] != base["qualified_capabilities"]
        or profile["excluded_capabilities"] != base["excluded_capabilities"]
        or profile["sensor_profile"] != "local-visibility-v1"
    ):
        raise ValueError("Navigation inherited body profile has drifted")
    if (
        course not in {"C2", "C3", "C4", "C5"}
        or course not in profile["candidate_courses"]
    ):
        raise ValueError("Course is outside the navigation profile")
    if scored:
        path = ROOT / "docs/NAVIGATION_QUALIFICATION.json"
        if not path.exists():
            raise ValueError("Navigation reset qualification has not passed")
        entry = read_json(path).get("courses", {}).get(course, {})
        installed = read_json(ROOT / "runtime/mod-build.json")
        require_scored_execution(installed["source_hash"], profile["sensor_profile"])
        if (
            entry.get("status") != "passed"
            or entry.get("profile_hash") != digest(profile)
            or entry.get("mod_source_hash") != installed["source_hash"]
            or entry.get("reset_checks_passed", 0) < 20
        ):
            raise ValueError("Navigation reset qualification is missing or stale")
    return profile
