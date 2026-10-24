"""Gate-aware bootstrap reporting. No episode success is inferred from compilation."""

import csv
import json
import sqlite3
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
    episodes = []
    database = ROOT / "results/episodes.sqlite3"
    if database.exists():
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
            for row in db.execute(
                "SELECT e.run_id,e.episode_id,e.attempt,e.status,e.reason,"
                "r.configuration "
                "FROM episodes e JOIN runs r ON r.run_id=e.run_id ORDER BY e.started_at"
            ):
                run_id, episode_id, attempt, status, reason, configuration = row
                configuration = json.loads(configuration)
                episodes.append(
                    {
                        "run_id": run_id,
                        "episode_id": episode_id,
                        "attempt": attempt,
                        "status": status,
                        "reason": reason,
                        "scored": configuration.get("scored", False),
                        "controller": configuration.get(
                            "controller", configuration.get("suite", "unknown")
                        ),
                    }
                )
    scored = [e for e in episodes if e["scored"]]
    successes = sum(e["status"] == "success" for e in scored)
    probes = {}
    for path in (ROOT / "results/provider_probes").glob("*.json"):
        probe = read_json(path)
        if probe.get("run_id"):
            probes[probe["run_id"]] = probe
    result = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "minecraft": lock()["minecraft"],
        "server": server.status(),
        "episodes_attempted": len(scored),
        "episodes_succeeded": successes,
        "success_rate": successes / len(scored) if scored else None,
        "episode_ledger": episodes,
        "unscored_development_attempts": len(episodes) - len(scored),
        "provider_requests": sum(
            b.get("provenance", {}).get("requests", 0)
            for p in probes.values()
            for b in p.get("backends", [])
        ),
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
    with (destination / "episodes.csv").open(
        "w", newline="", encoding="utf-8"
    ) as output:
        fields = [
            "run_id",
            "episode_id",
            "attempt",
            "status",
            "reason",
            "scored",
            "controller",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(episodes)
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
        writer.writerow(
            [result["minecraft"], result["G0"], len(scored), result["success_rate"]]
        )
    (destination / "initial.md").write_text(
        "# Initial laboratory report\n\n"
        f"Generated: {result['generated_at_utc']}\n\n"
        f"Server: {result['server']['state']} on Minecraft {result['minecraft']}.\n\n"
        f"Geometry: {result['bootstrap']['status']}.\n\n"
        f"Gates: {result['gates']}.\n\n"
        f"Scored episodes: {len(scored)}. Successes: {successes}. "
        f"Success rate: {result['success_rate']}. "
        f"Provider requests: {result['provider_requests']}.\n\n"
        "Unscored development/adversarial attempts: "
        f"{result['unscored_development_attempts']}. "
        "All outcomes, including intentional adversarial failures and invalid "
        "attempts, appear in episodes.csv.\n\n"
        f"Development qualification attempts recorded: {len(attempts)}. "
        "All recorded outcomes are retained in qualification_attempts.csv.\n\n"
        "Gate status comes from the reviewed ledger. Deterministic C1 execution "
        "does not establish model skill, later-course navigation, learned control "
        "or external baseline performance. "
        "See per-run provenance before comparing results.\n",
        encoding="utf-8",
    )
    return {"reports": str(destination), "summary": result}
