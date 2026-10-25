"""Regenerate the compact, hash-bound G4 development evidence from raw records."""

import json
import sqlite3
from collections import Counter
from pathlib import Path

from mclab.bootstrap import sha256
from mclab.paths import ROOT, read_json, write_json


def audit(path):
    summary = read_json(path)
    recording = Path(summary["recording"])
    responses, errors, policy_ticks, actions, ages, choices = [], [], set(), {}, [], []
    positions = []
    milestones = {}
    cancelled_on_close = 0
    with recording.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            payload, kind = row["payload"], row["type"]
            if kind.startswith("tactical_response"):
                responses.append(payload)
                cancelled_on_close += kind == "tactical_response_discarded_on_close"
                if "decision" in payload:
                    choices.append(payload["decision"]["choice"])
            if kind == "controller_error":
                errors.append(payload)
            if kind == "request" and payload.get("op") == "controls":
                actions[payload["sequence"]] = payload["controls"]
            if kind == "server_event":
                if payload["type"] == "course_milestone":
                    milestones.setdefault(payload["episode_id"], []).append(
                        payload["data"]
                    )
                if payload["type"] == "policy_sample":
                    policy_ticks.add(payload["tick"])
                    positions.append(payload["data"]["body"]["position"])
                if payload["type"] == "applied":
                    data = payload["data"]
                    action = actions.get(int(data["request_sequence"]))
                    if action is not None:
                        ages.append(
                            data["actual_apply_tick"] - action["observation_tick"]
                        )
    snapshot = path.parent / "sources/mclab"
    hashes_match = all(
        sha256(snapshot / filename) == summary[key]
        for filename, key in (
            ("model_execution.py", "controller_sha256"),
            ("tactical.py", "tactical_sha256"),
            ("backends.py", "backend_sha256"),
        )
    )
    expected = set()
    for episode in summary["episodes"]:
        if "start_tick" in episode and "end_tick" in episode:
            expected.update(range(episode["start_tick"], episode["end_tick"]))
    latencies = sorted(
        r["decision"]["latency_ms"] for r in responses if "decision" in r
    )
    causal_chains = {}
    for episode in summary["episodes"]:
        if episode["status"] == "success":
            chain = milestones.get(episode["episode_id"], [])
            causal_chains[episode["episode_id"]] = (
                [m["milestone"] for m in chain]
                == ["causal_drop_created", "target_broken", "target_collected"]
                and chain[0]["drop_uuid"]
                == chain[2]["drop_uuid"]
                == episode["drop_uuid"]
                and episode["picked_up"]
                and episode["target_broken"]
            )
    database = ROOT / "results/episodes.sqlite3"
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        outcomes = dict(
            db.execute(
                "SELECT episode_id,status FROM episodes WHERE run_id=?",
                (summary["run_id"],),
            )
        )
    ledger_matches = outcomes == {
        episode["episode_id"]: episode["status"] for episode in summary["episodes"]
    }
    return {
        "run_id": summary["run_id"],
        "backend": summary["backend"]["name"],
        "scored": summary["scored"],
        "summary": str(path.relative_to(ROOT)),
        "summary_sha256": sha256(path),
        "recording_sha256": sha256(recording),
        "source_snapshots_match": hashes_match,
        "controller_sha256": summary["controller_sha256"],
        "tactical_sha256": summary["tactical_sha256"],
        "episodes": summary["episodes"],
        "model_requests": summary["model_requests"],
        "recorded_responses": len(responses),
        "discarded_responses_on_close": cancelled_on_close,
        "successful_causal_chains_verified": causal_chains,
        "sqlite_outcomes_match": ledger_matches,
        "valid_responses": len(latencies),
        "choices": dict(Counter(choices)),
        "latencies_ms": latencies,
        "controller_errors": errors,
        "response_errors": [
            {k: r[k] for k in ("error", "reason") if k in r}
            for r in responses
            if "error" in r
        ],
        "policy_ticks_missing": len(expected - policy_ticks),
        "applied_control_count": len(ages),
        "applied_observation_ages": sorted(set(ages)),
        "initial_position": positions[0] if positions else None,
        "final_position": positions[-1] if positions else None,
    }


if __name__ == "__main__":
    runs = [
        audit(p)
        for p in sorted((ROOT / "results/model_execution").glob("*/summary.json"))
    ]
    proof = {
        "schema_version": 1,
        "status": "G4_partial_live_development",
        "scope": "Frozen C1 seed0, bounded visible-target tactical baselines",
        "G4_passed": False,
        "runs": runs,
        "attempted_episodes": sum(len(r["episodes"]) for r in runs),
        "successful_episodes": sum(
            e["status"] == "success" for r in runs for e in r["episodes"]
        ),
        "model_requests": sum(r["model_requests"] for r in runs),
        "actual_cost_usd": None,
    }
    write_json(ROOT / "docs/G4_LIVE_PROOF.json", proof)
    print(json.dumps(proof, indent=2))
