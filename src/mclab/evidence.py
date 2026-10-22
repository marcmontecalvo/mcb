"""Gate-aware bootstrap reporting. No episode success is inferred from compilation."""

import csv
from datetime import UTC, datetime

from mclab import server
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
        "human_inspection_confirmed": False,
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
        "G0": "pending_human_join_and_inspection",
        "G1-G8": "not_run",
    }
    try:
        result["bootstrap"] = verify_bootstrap()
    except (OSError, ValueError, RuntimeError) as error:
        result["bootstrap"] = {"status": "not_verified", "error": str(error)}
    destination = ROOT / "results/reports"
    write_json(destination / "initial.json", result)
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
        "G0 awaits a human join and C1 inspection. G1-G8 have not run.\n\n"
        "Scored episodes: 0. Success rate: unavailable. Provider requests: 0.\n\n"
        "No body fidelity, reset reliability, model skill, or baseline performance "
        "has been established.\n",
        encoding="utf-8",
    )
    return {"reports": str(destination), "summary": result}
