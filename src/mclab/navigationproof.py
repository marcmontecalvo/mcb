"""Audit navigation evidence without promoting gates from summary counts alone."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from mclab.bootstrap import sha256
from mclab.experiments import layout_hash
from mclab.paths import MODS, ROOT, digest, read_json, write_json


def _native_hash(snapshot):
    hasher = hashlib.sha256()
    for mod in MODS:
        for path in sorted((snapshot / mod / "src").rglob("*")):
            if path.is_file():
                hasher.update(path.relative_to(snapshot).as_posix().encode())
                hasher.update(path.read_bytes())
    return hasher.hexdigest()


def audit(path):
    path = Path(path)
    summary = read_json(path)
    recording = path.parent / "events.jsonl"
    snapshot = path.parent / "sources"
    episodes = {e["episode_id"]: e for e in summary["episodes"]}
    specs = {s["seed"]: s for s in summary["specs"]}
    ticks, milestones, resets = defaultdict(set), defaultdict(list), {}
    actions, ages, terminals = {}, [], {}
    applied_counts = Counter()
    errors, discarded = [], []
    cleanup = summary.get("cleanup", {})
    checks = {
        "navigation_suite": (
            summary.get("suite") == "navigation_execution_v1"
            and summary["course_id"] in {"C2", "C3", "C4", "C5"}
            and summary["profile_id"] == "navigation-c2-c5-survival-v1"
        ),
        "completed_run": summary["status"] == "completed_navigation_execution",
        "record_sequences_contiguous": True,
        "run_and_body_identities_match": True,
        "applied_controls_match_timing": True,
        "cleanup_native_acknowledged": False,
    }
    records = 0
    with recording.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            records += 1
            checks["record_sequences_contiguous"] &= row["sequence"] == records
            checks["run_and_body_identities_match"] &= (
                row["run_id"] == summary["run_id"]
            )
            kind, payload = row["type"], row["payload"]
            if kind == "controller_error":
                errors.append({"episode_id": row["episode_id"], **payload})
            if kind == "discarded_stale_action":
                discarded.append({"episode_id": row["episode_id"], **payload})
            if kind == "request" and payload.get("op") == "controls":
                actions[payload["sequence"]] = payload["controls"]
            if kind != "server_event" or payload.get("run_id") != summary["run_id"]:
                continue
            event, data = payload["type"], payload["data"]
            episode_id = payload.get("episode_id")
            if episode_id == cleanup.get("episode_id"):
                if event == "course_reset_complete":
                    checks["cleanup_native_acknowledged"] = (
                        payload.get("body_id") == cleanup.get("body_id")
                        and data.get("spec_hash") == cleanup.get("spec_hash")
                        and cleanup.get("status") == "restored_initial_C1"
                    )
                continue
            if episode_id not in episodes:
                continue
            checks["run_and_body_identities_match"] &= (
                payload.get("body_id") == episodes[episode_id]["body_id"]
            )
            if event == "policy_sample":
                ticks[episode_id].add(payload["tick"])
            elif event == "course_milestone":
                milestones[episode_id].append(data)
            elif event == "course_reset_complete":
                resets[episode_id] = data
            elif event == "course_episode_complete":
                terminals[episode_id] = data
            elif event == "applied":
                action = actions.get(int(data["request_sequence"]))
                if action is None:
                    checks["applied_controls_match_timing"] = False
                    continue
                age = data["actual_apply_tick"] - action["observation_tick"]
                ages.append(age)
                applied_counts[episode_id] += 1
                checks["applied_controls_match_timing"] &= (
                    age == 3
                    and action["duration"] == 5
                    and action["intended_apply_tick"] == data["actual_apply_tick"]
                    and action["expires_tick"] == action["intended_apply_tick"] + 5
                )
    episode_audits = []
    for identity, episode in episodes.items():
        spec = specs[episode["seed"]]
        reset = resets.get(identity, {})
        body = reset.get("body_state", {})
        terminal = terminals.get(identity, {})
        expected = set(range(episode["start_tick"], episode["end_tick"]))
        chain = [
            m
            for m in milestones[identity]
            if m["milestone"]
            in {"causal_drop_created", "target_broken", "target_collected"}
        ]
        causal = episode["status"] != "success" or (
            [m["milestone"] for m in chain]
            == ["causal_drop_created", "target_broken", "target_collected"]
            and chain[0]["drop_uuid"] == chain[2]["drop_uuid"] == episode["drop_uuid"]
            and chain[1]["position"]
            == [a + b for a, b in zip(spec["origin"], spec["target"], strict=True)]
            and episode["picked_up"]
            and episode["target_broken"]
        )
        episode_audits.append(
            {
                "episode_id": identity,
                "body_id": episode["body_id"],
                "seed": episode["seed"],
                "spec_hash": spec["spec_hash"],
                "status": episode["status"],
                "reason": episode["reason"],
                "duration_ticks": episode["end_tick"] - episode["start_tick"],
                "policy_ticks_missing": len(expected - ticks[identity]),
                "applied_controls": applied_counts[identity],
                "control_activity_recorded": (
                    episode["status"] != "success" or applied_counts[identity] > 0
                ),
                "causal_chain_verified": causal,
                "terminal_matches": all(
                    terminal.get(k) == episode.get(k)
                    for k in ("status", "reason", "start_tick", "end_tick", "drop_uuid")
                ),
                "reset_verified": (
                    reset.get("spec_hash") == spec["spec_hash"]
                    and bool(reset.get("state_digest"))
                    and body.get("position")
                    == [
                        a + b
                        for a, b in zip(spec["origin"], spec["spawn"], strict=True)
                    ]
                    and body.get("yaw") == spec["yaw"]
                    and body.get("pitch") == spec["pitch"]
                    and body.get("inventory") == []
                ),
            }
        )
    database = path.parent / "episodes.sqlite3"
    if not database.exists():
        database = ROOT / "results/episodes.sqlite3"
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        ledger = dict(
            (row[0], row[1:])
            for row in db.execute(
                "SELECT episode_id,status,reason,body_id FROM episodes WHERE run_id=?",
                (summary["run_id"],),
            )
        )
        count, first, last = db.execute(
            "SELECT count(*),min(sequence),max(sequence) FROM events "
            "WHERE run_id=? AND sequence>1",
            (summary["run_id"],),
        ).fetchone()
    checks.update(
        {
            "sqlite_outcomes_match": ledger
            == {
                identity: (e["status"], e["reason"], e["body_id"])
                for identity, e in episodes.items()
            },
            "critical_record_counts_match": count == records - 1
            and first == 2
            and last == records,
            "source_snapshots_match": (
                sha256(snapshot / "src/mclab/navigation.py")
                == summary["controller_sha256"]
                and sha256(snapshot / "src/mclab/navigation_runner.py")
                == summary["runner_sha256"]
                and _native_hash(snapshot) == summary["mod_provenance"]["source_hash"]
                and digest(read_json(snapshot / "config/navigation_body_profile.json"))
                == summary["profile_hash"]
                and digest(read_json(snapshot / "courses/profiles.json"))
                == summary["course_profiles_hash"]
            ),
            "all_episode_evidence_complete": all(
                a["policy_ticks_missing"] == 0
                and a["causal_chain_verified"]
                and a["control_activity_recorded"]
                and a["terminal_matches"]
                and a["reset_verified"]
                for a in episode_audits
            ),
            "fresh_body_identities": len({e["body_id"] for e in episodes.values()})
            == len(episodes),
            "declared_episode_counts_match": (
                summary["attempted_episodes"] == len(episodes)
                and summary["successes"]
                == sum(e["status"] == "success" for e in episodes.values())
            ),
        }
    )
    valid = all(checks.values()) and bool(episode_audits)
    successes = sum(e["status"] == "success" for e in episodes.values())
    unique_layouts = len({layout_hash(spec) for spec in summary["specs"]})
    return {
        "run_id": summary["run_id"],
        "course_id": summary["course_id"],
        "summary": str(path.resolve().relative_to(ROOT)),
        "summary_sha256": sha256(path),
        "recording_sha256": sha256(recording),
        "source_commit": summary["source_commit"],
        "controller_sha256": summary["controller_sha256"],
        "execution_stack_sha256": digest(
            {
                name: sha256(snapshot / "src/mclab" / name)
                for name in (
                    "bridge.py",
                    "execution.py",
                    "model_execution.py",
                    "recorder.py",
                )
            }
        ),
        "runner_sha256": summary["runner_sha256"],
        "mod_source_hash": summary["mod_provenance"]["source_hash"],
        "profile_hash": summary["profile_hash"],
        "course_profiles_hash": summary["course_profiles_hash"],
        "generator_sha256": sha256(snapshot / "src/mclab/courses.py"),
        "timing_profile": summary["timing_profile"],
        "checks": checks,
        "valid_recording": valid,
        "attempts": len(episodes),
        "successes": successes,
        "seeds": summary["seeds"],
        "unique_layouts": unique_layouts,
        "applied_control_count": len(ages),
        "applied_observation_ages": sorted(set(ages)),
        "controller_errors": errors,
        "discarded_stale_actions": discarded,
        "episodes": episode_audits,
        "gate_eligible": (
            valid
            and summary["scored"] is True
            and len(episodes) == 20
            and len(set(summary["seeds"])) == 20
            and unique_layouts == 20
            and [e["seed"] for e in episodes.values()] == summary["seeds"]
            and successes >= 18
        ),
    }


def report(paths, output):
    runs = [audit(path) for path in paths]
    courses = {run["course_id"]: run for run in runs}
    if len(courses) != len(runs):
        raise ValueError("Select only one development batch per course")
    frozen = (
        len(
            {
                tuple(
                    run[k]
                    for k in (
                        "controller_sha256",
                        "execution_stack_sha256",
                        "runner_sha256",
                        "mod_source_hash",
                        "profile_hash",
                        "course_profiles_hash",
                        "generator_sha256",
                        "timing_profile",
                    )
                )
                for run in runs
            }
        )
        == 1
    )
    passed = (
        set(courses) == {"C2", "C3", "C4", "C5"}
        and frozen
        and all(run["gate_eligible"] for run in runs)
    )
    proof = {
        "schema_version": 1,
        "auditor_sha256": sha256(Path(__file__)),
        "status": (
            "invalid_evidence"
            if any(not run["valid_recording"] for run in runs)
            else "passed_development_navigation"
            if passed
            else "in_progress"
        ),
        "scope": (
            "Deterministic C2-C5 development navigation; "
            "no model, learned policy or holdout claim"
        ),
        "uniform_controller_and_timing": frozen,
        "G5_passed": passed,
        "attempted_episodes": sum(r["attempts"] for r in runs),
        "successful_episodes": sum(r["successes"] for r in runs),
        "outcomes": dict(Counter(e["status"] for r in runs for e in r["episodes"])),
        "courses": courses,
    }
    write_json(Path(output), proof)
    return proof


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    proof = report(args.summaries, args.output)
    concise = {k: v for k, v in proof.items() if k != "courses"}
    concise["courses"] = {
        course: {
            "attempts": run["attempts"],
            "successes": run["successes"],
            "unique_layouts": run["unique_layouts"],
            "valid_recording": run["valid_recording"],
            "gate_eligible": run["gate_eligible"],
            "failed_checks": [name for name, ok in run["checks"].items() if not ok],
        }
        for course, run in proof["courses"].items()
    }
    print(json.dumps(concise, indent=2))
    return 1 if proof["status"] == "invalid_evidence" else 0


if __name__ == "__main__":
    raise SystemExit(main())
