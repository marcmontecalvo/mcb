"""Bounded G2 reset-foundation checks; scoring and episode evaluation remain pending."""

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab import gates
from mclab.bootstrap import sha256
from mclab.bridge import Bridge
from mclab.modbuild import source_hash
from mclab.paths import MOD_SOURCES, ROOT, read_json, write_json
from mclab.recorder import arena_guard


class ResetRecorder:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("Unsupported reset-recorder schema")
        self.db.execute("""CREATE TABLE IF NOT EXISTS reset_attempts (
            run_id TEXT NOT NULL, attempt INTEGER NOT NULL CHECK(attempt > 0),
            episode_id TEXT NOT NULL, body_id TEXT NOT NULL, started_at TEXT NOT NULL,
            status TEXT NOT NULL, generation INTEGER, state_digest TEXT, error TEXT,
            event_path TEXT NOT NULL, PRIMARY KEY(run_id, attempt))""")
        self.db.execute("PRAGMA user_version=1")
        self.db.commit()

    def begin(self, run_id, attempt, episode_id, body_id, event_path):
        self.db.execute(
            "INSERT INTO reset_attempts VALUES "
            "(?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, ?)",
            (
                run_id,
                attempt,
                episode_id,
                body_id,
                datetime.now(UTC).isoformat(),
                str(event_path),
            ),
        )
        self.db.commit()

    def finish(
        self, run_id, attempt, status, generation=None, state_digest=None, error=None
    ):
        result = self.db.execute(
            "UPDATE reset_attempts SET status=?, generation=?, state_digest=?, error=? "
            "WHERE run_id=? AND attempt=? AND status='pending'",
            (status, generation, state_digest, error, run_id, attempt),
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ValueError("Reset attempt is missing or already finalized")
        self.db.commit()

    def close(self):
        self.db.close()


@arena_guard
def qualify_resets(count: int = 20) -> dict:
    if not 1 <= count <= 20:
        raise ValueError("Reset check count must be 1 to 20")
    profile = gates.require_initial_profile(
        "C1", ("cancel_expiry", "death_terminal_replacement")
    )
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError(
            "Installed mod differs from source; rebuild and install first"
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/qualification" / stamp
    client = Bridge(destination / "events.jsonl")
    client.budget = 1500  # Named reset-check budget; motor timing limits are unchanged.
    recorder = ResetRecorder(ROOT / "results/reset_checks.sqlite3")
    summary = {
        "schema_version": 1,
        "suite": "reset_foundation_v1",
        "scored": False,
        "run_id": client.run_id,
        "profile_id": profile["profile_id"],
        "launch_id": client.launch_id,
        "mod_provenance": provenance,
        "qualifier_source_sha256": sha256(Path(__file__)),
        "G2": "in_progress_reset_foundation_only",
        "requested_resets": count,
        "request_budget": client.budget,
        "checks": [],
        "resets": [],
        "recording": str(destination / "events.jsonl"),
    }
    active_attempt = None
    try:
        for relative in ("src/mclab", *MOD_SOURCES, "config/initial_body_profile.json"):
            source, target = ROOT / relative, destination / "sources" / relative
            if source.is_dir():
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        client.synchronize()
        first_digest = None
        for index in range(1, count + 1):
            client.episode_id = "reset-" + uuid4().hex[:12]
            client.body_id = "body-" + uuid4().hex[:12]
            recorder.begin(
                client.run_id,
                index,
                client.episode_id,
                client.body_id,
                destination / "events.jsonl",
            )
            active_attempt = index
            request = client.send("course_reset", profile_id=profile["profile_id"])
            client.acknowledge(request, "course_reset_started", timeout=10)
            event = client.acknowledge(request, "course_reset_complete", timeout=90)
            data = event["data"]
            first_digest = first_digest or data["state_digest"]
            passed = data["state_digest"] == first_digest and (
                not summary["resets"]
                or data["generation"] == summary["resets"][-1]["generation"] + 1
            )
            check = dict(
                name=f"reset_{index}_matches",
                expected=first_digest,
                observed=data["state_digest"],
                passed=passed,
            )
            summary["checks"].append(check)
            summary["resets"].append(data)
            client.record("check", check)
            recorder.finish(
                client.run_id,
                index,
                "passed" if passed else "failed",
                data["generation"],
                data["state_digest"],
                None if passed else "Authoritative reset digest/generation mismatch",
            )
            active_attempt = None
            write_json(destination / "summary.json", {**summary, "status": "running"})
            if not passed:
                raise RuntimeError("Authoritative reset digest/generation mismatch")
        summary["status"] = "passed_development_subset"
    except Exception as error:
        summary.update(status="invalid", error=str(error))
        if active_attempt is not None:
            recorder.finish(client.run_id, active_attempt, "invalid", error=str(error))
    finally:
        try:
            request = client.send("despawn")
            client.acknowledge(request, "received")
            client.wait(lambda state: state["state"] == "idle")
        except Exception as error:
            summary["cleanup_error"] = str(error)
            summary["status"] = "invalid"
        summary["requests"] = client.submitted
        recorder.close()
        client.close()
        write_json(destination / "summary.json", summary)
    return summary
