"""Durable episode ledger with immutable terminal outcomes and raw event records."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

from mclab.paths import ROOT


@contextmanager
def exclusive_run(path: Path):
    """OS lock protects recovery and releases automatically if the process dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            def lock():
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

            def unlock():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def lock():
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock():
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

        try:
            lock()
        except OSError:
            raise RuntimeError("Another execution runner is active") from None
        try:
            yield
        finally:
            unlock()


@contextmanager
def exclusive_arena():
    with exclusive_run(ROOT / "results/.arena.lock"):
        yield


def arena_guard(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with exclusive_arena():
            return function(*args, **kwargs)

    return guarded


def episode_databases(root: Path):
    """Legacy shared ledger plus isolated navigation-run ledgers."""
    legacy = root / "results/episodes.sqlite3"
    return ([legacy] if legacy.exists() else []) + sorted(
        (root / "results/navigation").glob("*/episodes.sqlite3")
    )


class EpisodeRecorder:
    def __init__(self, path: Path):
        self.path = path
        self.batching = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("Unsupported episode recorder schema")
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                configuration TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs,
                attempt INTEGER NOT NULL, body_id TEXT NOT NULL,
                started_at TEXT NOT NULL, status TEXT NOT NULL,
                reason TEXT, terminal TEXT, event_path TEXT NOT NULL,
                UNIQUE(run_id, attempt));
            CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL REFERENCES runs, sequence INTEGER NOT NULL,
                episode_id TEXT NOT NULL, kind TEXT NOT NULL,
                monotonic_ns INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(run_id, sequence));
            CREATE INDEX IF NOT EXISTS events_by_episode ON events(episode_id, kind);
            PRAGMA user_version=1;
        """)
        self.db.commit()

    def begin_run(self, run_id: str, configuration: dict):
        self.db.execute(
            "INSERT INTO runs VALUES (?, ?, ?, 'running')",
            (run_id, datetime.now(UTC).isoformat(), json.dumps(configuration)),
        )
        self.db.commit()

    def begin(self, run_id, attempt, episode_id, body_id, event_path):
        self.db.execute(
            "INSERT INTO episodes VALUES (?, ?, ?, ?, ?, 'pending', NULL, NULL, ?)",
            (
                episode_id,
                run_id,
                attempt,
                body_id,
                datetime.now(UTC).isoformat(),
                str(event_path),
            ),
        )
        self.db.commit()

    def append(self, run_id, sequence, episode_id, kind, monotonic_ns, payload):
        # Requests, responses, actions, snapshots, milestones, resource samples and
        # errors retain their complete versioned payload rather than lossy columns.
        self.db.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                sequence,
                episode_id,
                kind,
                monotonic_ns,
                json.dumps(payload, allow_nan=False, separators=(",", ":")),
            ),
        )
        if not self.batching:
            self.db.commit()

    @contextmanager
    def batch(self):
        """Commit a bounded writer batch atomically before acknowledging it."""
        if self.batching:
            raise RuntimeError("Nested recorder batches are unsupported")
        self.batching = True
        try:
            with self.db:
                yield
        finally:
            self.batching = False

    def finish(self, episode_id: str, status: str, reason: str, terminal: dict):
        if status not in {"success", "failed", "invalid", "interrupted"}:
            raise ValueError("Invalid episode outcome")
        result = self.db.execute(
            "UPDATE episodes SET status=?, reason=?, terminal=? "
            "WHERE episode_id=? AND status='pending'",
            (status, reason, json.dumps(terminal, allow_nan=False), episode_id),
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ValueError("Episode is missing or already finalized")
        self.db.commit()

    def recover_interrupted(self) -> int:
        """Explicit startup recovery: a pending attempt never becomes a success."""
        cursor = self.db.execute(
            "UPDATE episodes SET status='interrupted', reason='runner_interrupted' "
            "WHERE status='pending'"
        )
        self.db.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
        self.db.commit()
        return cursor.rowcount

    def finish_run(self, run_id, status):
        self.db.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))
        self.db.commit()

    def close(self):
        self.db.close()
