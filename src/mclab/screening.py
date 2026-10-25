"""Frozen synthetic tactical screening; labels never reach provider requests."""

import copy
import random
import shutil
import statistics
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab.backends import BackendError, ChoiceRequest, HttpBackend
from mclab.bootstrap import sha256
from mclab.experiments import freeze
from mclab.paths import ROOT, digest, read_json, write_json
from mclab.tactical import CANDIDATES, INSTRUCTIONS, summary


def build(destination=None):
    from mclab.screen_fixtures import build_fixture

    value = build_fixture(instructions=INSTRUCTIONS)
    return freeze(
        destination or ROOT / "results/experiments/tactical_screen_v1.json", value
    )


def cases(fixture):
    """Each state appears under original/reversed/seeded order; no label on wire."""
    result = []
    for state_index, state in enumerate(fixture["states"]):
        original = list(CANDIDATES)
        shuffled = original.copy()
        random.Random(421 + state_index).shuffle(shuffled)
        for order_index, order in enumerate(
            (original, list(reversed(original)), shuffled)
        ):
            result.append({"state": state, "order": order, "order_index": order_index})
    return result


def run(backend_name, fixture_path: Path, *, offset=0, limit=36):
    if (
        type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= 40
    ):
        raise ValueError("Screening requires offset>=0 and batch limit1..40")
    fixture = read_json(fixture_path)
    review = read_json(ROOT / "docs/G4_SCREEN_REVIEW.json")
    if review["status"] != "passed" or review["fixture_hash"] != digest(fixture):
        raise ValueError(
            "Screening labels require matching independent geometry review"
        )
    if fixture["candidates"] != CANDIDATES or fixture["instructions"] != INSTRUCTIONS:
        raise ValueError(
            "Screening action profile differs from current tactical profile"
        )
    selected_cases = cases(fixture)[offset : offset + limit]
    if not selected_cases:
        raise ValueError("Screening offset is outside the frozen matrix")
    specs = read_json(ROOT / "config/backends.json")["backends"]
    selected = [s for s in specs if s["name"] == backend_name and s.get("enabled")]
    if len(selected) != 1:
        raise ValueError("Select exactly one enabled backend")
    spec = {**selected[0], "request_limit": len(selected_cases)}
    backend = HttpBackend(spec)
    if not backend.local and len(selected_cases) > selected[0]["request_limit"]:
        backend.close()
        raise ValueError("Screening exceeds configured remote batch limit")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/screening" / stamp
    destination.mkdir(parents=True)
    result = {
        "schema_version": 1,
        "run_id": "screen-" + uuid4().hex[:12],
        "backend": backend_name,
        "fixture_hash": digest(fixture),
        "review_sha256": sha256(ROOT / "docs/G4_SCREEN_REVIEW.json"),
        "runner_sha256": sha256(Path(__file__)),
        "backend_sha256": sha256(ROOT / "src/mclab/backends.py"),
        "configuration": spec,
        "configuration_hash": digest(spec),
        "scope": "synthetic offline useful-action screen; not live accuracy or holdout",
        "scored": False,
        "offset": offset,
        "requested": len(selected_cases),
        "matrix_size": len(cases(fixture)),
        "outcomes": [],
        "status": "running",
    }
    for module in ("screening.py", "backends.py", "tactical.py", "screen_fixtures.py"):
        (destination / "sources").mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "src/mclab" / module, destination / "sources" / module)
    shutil.copyfile(fixture_path, destination / "fixture.json")
    write_json(destination / "summary.json", result)
    try:
        for index, case in enumerate(selected_cases, offset):
            state, order = case["state"], case["order"]
            exchange_start = len(backend.exchanges)
            requests_before = backend.requests
            outcome = {
                "case_index": index,
                "state_id": state["state_id"],
                "category": state["category"],
                "order_index": case["order_index"],
                "candidate_order": order,
                "valid_actions": state["valid_actions"],
                "status": "pending",
                "correct": False,
            }
            result["outcomes"].append(outcome)
            write_json(destination / "summary.json", result)
            try:
                decision = backend.decide(
                    ChoiceRequest(
                        summary(state["observation"]),
                        {key: fixture["candidates"][key] for key in order},
                        fixture["instructions"],
                    )
                )
                outcome.update(
                    status="valid",
                    decision=asdict(decision),
                    correct=decision.choice in state["valid_actions"],
                )
            except BackendError as error:
                outcome.update(status="backend_error", error=str(error), correct=False)
            # Preserve a failed/late response as diagnostic evidence. Every case
            # remains in the denominator; no retry or automatic extra batch.
            outcome["exchanges"] = copy.deepcopy(backend.exchanges[exchange_start:])
            outcome["admitted"] = backend.requests > requests_before
            result["provenance"] = backend.provenance()
            write_json(destination / "summary.json", result)
            if outcome["status"] == "backend_error" and any(
                reason in outcome["error"].lower()
                for reason in ("deadline", "outstanding")
            ):
                result["status"] = "stopped_backend_unready"
                break
        else:
            result["status"] = "completed_development_screen"
    except BaseException as error:
        result.update(status="interrupted", error=type(error).__name__)
        for outcome in result["outcomes"]:
            if outcome["status"] == "pending":
                outcome.update(
                    status="interrupted",
                    error=type(error).__name__,
                    exchanges=copy.deepcopy(backend.exchanges[exchange_start:]),
                    admitted=backend.requests > requests_before,
                )
        raise
    finally:
        backend.close()
        result["provenance"] = backend.provenance()
        result["attempted"] = len(result["outcomes"])
        result["correct"] = sum(o["correct"] for o in result["outcomes"])
        result["errors"] = sum(o["status"] != "valid" for o in result["outcomes"])
        write_json(destination / "summary.json", result)
    return {"artifact": str(destination / "summary.json"), **result}


def report():
    groups = {}
    for path in sorted((ROOT / "results/screening").glob("*/summary.json")):
        run = read_json(path)
        key = (
            run["backend"],
            run["fixture_hash"],
            run["backend_sha256"],
            run["runner_sha256"],
            run["configuration_hash"],
        )
        group = groups.setdefault(
            key,
            {
                "backend": key[0],
                "fixture_hash": key[1],
                "backend_sha256": key[2],
                "runner_sha256": key[3],
                "configuration_hash": key[4],
                "matrix_size": run["matrix_size"],
                "runs": [],
                "outcomes": [],
                "duplicate_cases": [],
            },
        )
        group["runs"].append(
            {"artifact": str(path.relative_to(ROOT)), "sha256": sha256(path)}
        )
        seen = {o["case_index"] for o in group["outcomes"]}
        for outcome in run["outcomes"]:
            if outcome["case_index"] in seen:
                group["duplicate_cases"].append(outcome["case_index"])
            group["outcomes"].append(
                {
                    **{k: v for k, v in outcome.items() if k != "exchanges"},
                    "admitted": outcome.get("admitted", bool(outcome.get("exchanges"))),
                }
            )
            seen.add(outcome["case_index"])
    for group in groups.values():
        outcomes = group["outcomes"]
        group["attempted"] = len(outcomes)
        group["correct"] = sum(o["correct"] for o in outcomes)
        group["backend_errors"] = sum(o["status"] != "valid" for o in outcomes)
        unique = {o["case_index"] for o in outcomes}
        group["unique_cases"] = len(unique)
        group["admitted_requests"] = sum(o["admitted"] for o in outcomes)
        group["missing_cases"] = sorted(set(range(group["matrix_size"])) - unique)
        group["matrix_complete"] = (
            not group["missing_cases"]
            and not group["duplicate_cases"]
            and group["admitted_requests"] == group["matrix_size"]
        )
        latencies = [
            o["decision"]["latency_ms"] for o in outcomes if o["status"] == "valid"
        ]
        group["latency_median_ms"] = statistics.median(latencies) if latencies else None
        group["by_category"] = {}
        for outcome in outcomes:
            category = group["by_category"].setdefault(
                outcome["category"], {"attempted": 0, "correct": 0}
            )
            category["attempted"] += 1
            category["correct"] += outcome["correct"]
    result = {
        "schema_version": 1,
        "scope": "synthetic development screen",
        "groups": list(groups.values()),
    }
    write_json(ROOT / "results/reports/tactical_screening.json", result)
    return result
