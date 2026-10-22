"""Gate-aware bootstrap reporting. No episode success is inferred from compilation."""

import csv
from datetime import UTC, datetime

from mclab import gates, server
from mclab.paths import ROOT, SERVER, lock, read_json, write_json


def verify_bootstrap() -> dict:
    state = read_json(server.STATE)
    evidence = read_json(SERVER / "mclab-bootstrap-evidence.json")
    spec = read_json(SERVER / "world/datapacks/mclab/evaluator/active.json")
    if server.status()["state"] != "running":
        raise RuntimeError("Bootstrap verification requires a running lab server")
    if evidence.get("launch_id") != state["launch_id"]:
        raise ValueError("Stale bootstrap evidence from a different server launch")
    if evidence["spec_hash"] != spec["spec_hash"]:
        raise ValueError("Bootstrap evidence does not match the active specification")
    if evidence["mismatches"] or evidence["checked_blocks"] != 49 * 41 * 49:
        raise ValueError(f"Arena mismatch/incomplete verification: {evidence}")
    return {
        "status": "bootstrap_geometry_verified",
        "evidence": evidence,
        "qualification": "G0 geometry only; no body/reset/scoring qualification",
        "human_inspection_confirmed": gates.inspected(spec["spec_hash"]),
    }


def report() -> dict:
    result = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "minecraft": lock()["minecraft"],
        "server": server.status(),
        "episodes_attempted": 0,
        "episodes_succeeded": 0,
        "success_rate": None,
        "provider_requests": 0,
        "gates": gates.statuses(),
        "G0": gates.statuses()["G0"],
    }
    attempts = []
    for path in sorted((ROOT / "results/qualification").glob("*/summary.json")):
        value = read_json(path)
        attempts.append(
            {
                "run_id": value["run_id"],
                "suite": value.get("suite", "body_mechanics_v1"),
                "status": value["status"],
                "checks_passed": sum(c["passed"] for c in value["checks"]),
                "checks_total": len(value["checks"]),
                "requests": value.get("requests", 0),
                "error": value.get("error", ""),
                "evidence": str(path.relative_to(ROOT)),
            }
        )
    result["development_qualification_attempts"] = attempts
    result["human_references"] = [
        {"evidence": str(path.relative_to(ROOT)), **read_json(path)}
        for path in sorted((ROOT / "results/references").glob("*/summary.json"))
    ]
    try:
        result["bootstrap"] = verify_bootstrap()
    except (OSError, ValueError, RuntimeError) as error:
        result["bootstrap"] = {"status": "not_verified", "error": str(error)}
    destination = ROOT / "results/reports"
    write_json(destination / "initial.json", result)
    with (destination / "qualification_attempts.csv").open(
        "w", newline="", encoding="utf-8"
    ) as output:
        fields = [
            "run_id",
            "suite",
            "status",
            "checks_passed",
            "checks_total",
            "requests",
            "error",
            "evidence",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(attempts)
    with (destination / "initial.csv").open(
        "w", newline="", encoding="utf-8"
    ) as output:
        writer = csv.writer(output)
        writer.writerow(["minecraft", "G0", "episodes_attempted", "success_rate"])
        writer.writerow([result["minecraft"], result["G0"], 0, "unavailable"])
    (destination / "initial.md").write_text(
        "# Initial laboratory report\n\n"
        f"Generated: {result['generated_at_utc']}\n\n"
        f"Server: {result['server']['state']} on Minecraft {result['minecraft']}.\n\n"
        f"Geometry: {result['bootstrap']['status']}.\n\n"
        f"Gates: {result['gates']}.\n\n"
        "Scored episodes: 0. Success rate: unavailable. Provider requests: 0.\n\n"
        f"Development qualification attempts recorded: {len(attempts)}. "
        "All recorded outcomes are retained in qualification_attempts.csv.\n\n"
        "Basic body proofs and human movement references are development evidence. "
        "Full G1 qualification remains open. No reset reliability, scored model skill "
        "or baseline performance has been established.\n",
        encoding="utf-8",
    )
    return {"reports": str(destination), "summary": result}
