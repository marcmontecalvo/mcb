"""Per-run event log in SQLite: ``<run>/events.sqlite3``.

A run used to write ``events.jsonl``; a large run made gigabytes that tools had to
scan line by line. The store keeps the same records (``records`` yields dicts equal
to the old jsonl lines) with the payload zlib-compressed and a few indexed columns
so tools can ask for just the types, kinds, plans or ticks they need.
"""

import argparse
import gzip
import json
import re
import sqlite3
import sys
import threading
import time
import zlib
from collections import deque
from collections.abc import Iterator
from pathlib import Path

STORE_NAME = "events.sqlite3"
JSONL_NAME = "events.jsonl"
USER_VERSION = 1
FLUSH_SECONDS = 0.5
FLUSH_ROWS = 256
COMPRESSION_LEVEL = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    sequence INTEGER PRIMARY KEY,
    monotonic_ns INTEGER,
    type TEXT,
    kind TEXT,
    tick INTEGER,
    plan_id TEXT,
    run_id TEXT,
    episode_id TEXT,
    body_id TEXT,
    schema_version INTEGER,
    size INTEGER,
    payload BLOB
);
CREATE INDEX IF NOT EXISTS events_type_kind ON events(type, kind);
CREATE INDEX IF NOT EXISTS events_plan ON events(plan_id);
CREATE INDEX IF NOT EXISTS events_tick ON events(tick);
"""
INSERT = "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"


def store_path(path) -> Path:
    """The sqlite store an ``events.jsonl`` path stands for (callers pass either)."""
    path = Path(path)
    return path.with_name(STORE_NAME) if path.name == JSONL_NAME else path


def _nested(payload, key):
    if not isinstance(payload, dict):
        return None
    if key in payload:
        return payload[key]
    data = payload.get("data")
    return data.get(key) if isinstance(data, dict) else None


def _kind_of(payload) -> str:
    if isinstance(payload, dict):
        for key in ("type", "op"):
            if isinstance(payload.get(key), str):
                return payload[key]
    return ""


def _row(record: dict, payload_json: str | None = None) -> tuple:
    payload = record.get("payload")
    if payload_json is None:
        payload_json = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    raw = payload_json.encode()
    tick = _nested(payload, "tick")
    plan_id = _nested(payload, "plan_id")
    return (
        record.get("sequence"),
        record.get("monotonic_ns"),
        record.get("type"),
        _kind_of(payload),
        tick if isinstance(tick, int) and not isinstance(tick, bool) else None,
        plan_id if isinstance(plan_id, str) else None,
        record.get("run_id"),
        record.get("episode_id"),
        record.get("body_id"),
        record.get("schema_version"),
        len(raw),
        zlib.compress(raw, COMPRESSION_LEVEL),
    )


def _open(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=30)
    return sqlite3.connect(path, timeout=30, check_same_thread=False)


class EventWriter:
    """Batching, thread-safe writer. ``add`` only queues; a timer thread commits.

    A crash loses at most the rows not yet committed (about FLUSH_SECONDS).
    """

    def __init__(self, path):
        self.path = store_path(path)
        if self.path.exists():
            raise FileExistsError(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = _open(self.path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._db.execute(f"PRAGMA user_version={USER_VERSION}")
        self._db.commit()
        self._lock = threading.Lock()
        self._pending: list[tuple] = []
        self._closed = False
        self._stop = threading.Event()
        self._timer = threading.Thread(target=self._tick, daemon=True)
        self._timer.start()

    def add(self, record: dict, payload_json: str | None = None) -> None:
        """Queue a record; ``payload_json`` skips re-encoding when the caller has it."""
        row = _row(record, payload_json)
        with self._lock:
            if self._closed:
                raise ValueError("event store is closed")
            self._pending.append(row)
            full = len(self._pending) >= FLUSH_ROWS
        if full:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            self._commit()

    def _commit(self) -> None:
        if self._closed or not self._pending:
            return
        rows, self._pending = self._pending, []
        try:
            self._db.executemany(INSERT, rows)
            self._db.commit()
        except Exception:
            self._pending = rows + self._pending
            raise

    def _tick(self) -> None:
        while not self._stop.wait(FLUSH_SECONDS):
            try:
                self.flush()
            except Exception:  # noqa: BLE001  # surfaced by the next flush/close
                pass

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            if self._closed:
                return
            try:
                self._commit()
            finally:
                self._closed = True
                self._db.close()

    @property
    def name(self) -> str:
        return str(self.path)


def locate(run_dir) -> Path | None:
    run_dir = Path(run_dir)
    for name in (STORE_NAME, JSONL_NAME, JSONL_NAME + ".gz"):
        if (run_dir / name).is_file():
            return run_dir / name
    return None


def resolve(source) -> Path:
    source = Path(source)
    if source.is_dir():
        found = locate(source)
        if found is None:
            raise FileNotFoundError(f"no event log in {source}")
        return found
    if not source.exists() and source.name == JSONL_NAME:
        for sibling in (STORE_NAME, JSONL_NAME + ".gz"):
            if source.with_name(sibling).is_file():
                return source.with_name(sibling)
    if not source.exists():
        raise FileNotFoundError(source)
    return source


def mtime(source) -> float:
    """Last write time; a WAL store changes its -wal file before the main file."""
    path = resolve(source)
    times = [path.stat().st_mtime]
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        times.append(wal.stat().st_mtime)
    return max(times)


def connect(source) -> sqlite3.Connection:
    """Read-only connection to a run's store, for ad-hoc SQL."""
    path = resolve(source)
    if path.suffix != ".sqlite3":
        raise ValueError(f"not a sqlite event store: {path}")
    return _open(path, readonly=True)


def _record(row) -> dict:
    seq, mono, type_, run_id, episode_id, body_id, version, payload = row
    return {
        "schema_version": version,
        "run_id": run_id,
        "episode_id": episode_id,
        "body_id": body_id,
        "sequence": seq,
        "monotonic_ns": mono,
        "type": type_,
        "payload": json.loads(zlib.decompress(payload)),
    }


def _sql_records(
    path,
    types,
    kinds,
    plan_ids,
    since_seq,
    until_seq,
    limit,
    reverse,
    kind_like=None,
    max_size=None,
):
    where, args = [], []
    for column, values in (("type", types), ("kind", kinds), ("plan_id", plan_ids)):
        if values is not None:
            values = list(values)
            where.append(f"{column} IN ({','.join('?' * len(values))})")
            args += values
    if kind_like:
        where.append("(" + " OR ".join("kind LIKE ?" for _ in kind_like) + ")")
        args += list(kind_like)
    if max_size is not None:
        where.append("size <= ?")
        args.append(max_size)
    if since_seq is not None:
        where.append("sequence >= ?")
        args.append(since_seq)
    if until_seq is not None:
        where.append("sequence <= ?")
        args.append(until_seq)
    sql = (
        "SELECT sequence, monotonic_ns, type, run_id, episode_id, body_id, "
        "schema_version, payload FROM events"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY sequence " + ("DESC" if reverse else "ASC")
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    connection = _open(path, readonly=True)
    try:
        for row in connection.execute(sql, args):
            yield _record(row)
    finally:
        connection.close()


def _jsonl_records(path, types, kinds, plan_ids, since_seq, until_seq, max_size=None):
    # Cheap substring pre-filter: a wanted string must appear in the raw line.
    needles = [list(group) for group in (kinds, plan_ids) if group is not None]
    if types is not None:
        needles.append([t for t in types])
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip() or (max_size is not None and len(line) > max_size):
                continue
            if any(not any(n in line for n in group) for group in needles):
                continue
            record = json.loads(line)
            if types is not None and record.get("type") not in types:
                continue
            payload = record.get("payload")
            if kinds is not None and _kind_of(payload) not in kinds:
                continue
            if plan_ids is not None and _nested(payload, "plan_id") not in plan_ids:
                continue
            seq = record.get("sequence")
            if since_seq is not None and seq is not None and seq < since_seq:
                continue
            if until_seq is not None and seq is not None and seq > until_seq:
                continue
            yield record


def records(
    source,
    *,
    types=None,
    kinds=None,
    plan_ids=None,
    since_seq=None,
    until_seq=None,
    limit=None,
    reverse=False,
    kind_like=None,
    max_size=None,
) -> Iterator[dict]:
    """Yield event records as dicts identical to the old jsonl lines.

    ``kind_like`` is a list of SQL LIKE patterns on ``kind`` (``%``/``_`` wildcards);
    ``max_size`` skips records whose compact JSON payload is longer than that.
    """
    path = resolve(source)
    types = None if types is None else frozenset(types)
    kinds = None if kinds is None else frozenset(kinds)
    plan_ids = None if plan_ids is None else frozenset(plan_ids)
    if path.suffix == ".sqlite3":
        yield from _sql_records(
            path,
            types,
            kinds,
            plan_ids,
            since_seq,
            until_seq,
            limit,
            reverse,
            kind_like,
            max_size,
        )
        return
    stream = _jsonl_records(
        path, types, kinds, plan_ids, since_seq, until_seq, max_size
    )
    if kind_like:
        patterns = [
            re.compile(
                "".join(
                    ".*" if c == "%" else "." if c == "_" else re.escape(c)
                    for c in like
                )
                + r"\Z",
                re.S,
            )
            for like in kind_like
        ]
        stream = (
            r for r in stream
            if any(m.match(_kind_of(r.get("payload"))) for m in patterns)
        )
    if reverse:
        stream = iter(reversed(list(stream)))
    for count, record in enumerate(stream):
        if limit is not None and count >= limit:
            return
        yield record


def tail(source, n: int, **filters) -> list[dict]:
    """The last ``n`` records, oldest first. Cheap on SQLite, bounded on jsonl."""
    path = resolve(source)
    if path.suffix == ".sqlite3":
        return list(reversed(list(records(path, limit=n, reverse=True, **filters))))
    return list(deque(records(path, **filters), maxlen=n))


def convert(source, *, delete: bool = False) -> Path:
    """Build ``events.sqlite3`` beside an ``events.jsonl[.gz]``; streaming."""
    source = Path(source)
    target = source.with_name(STORE_NAME)
    if target.exists():
        raise FileExistsError(target)
    opener = gzip.open if source.suffix == ".gz" else open
    writer = EventWriter(target)
    try:
        with opener(source, "rt", encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if line.strip():
                    record = json.loads(line)
                    record.setdefault("sequence", number)
                    writer.add(record)
        writer.close()
    except BaseException:
        writer.close()
        for suffix in ("", "-wal", "-shm"):
            target.with_name(target.name + suffix).unlink(missing_ok=True)
        raise
    if delete:
        source.unlink()
    return target


def stats(source) -> list[tuple]:
    """(type, kind, count, uncompressed payload bytes), biggest first."""
    path = resolve(source)
    if path.suffix == ".sqlite3":
        connection = _open(path, readonly=True)
        try:
            return connection.execute(
                "SELECT type, kind, COUNT(*), SUM(size) FROM events "
                "GROUP BY type, kind ORDER BY SUM(size) DESC"
            ).fetchall()
        finally:
            connection.close()
    totals: dict[tuple, list] = {}
    for record in records(path):
        payload = record.get("payload")
        entry = totals.setdefault((record.get("type"), _kind_of(payload)), [0, 0])
        entry[0] += 1
        entry[1] += len(json.dumps(payload, separators=(",", ":")))
    return sorted(
        ((t, k, c, s) for (t, k), (c, s) in totals.items()), key=lambda r: -r[3]
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mclab.event_log")
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show")
    show.add_argument("source")
    show.add_argument("--type", action="append")
    show.add_argument("--kind", action="append")
    show.add_argument("--plan", action="append")
    show.add_argument("--limit", type=int)
    show.add_argument("--tail", type=int)
    stat = sub.add_parser("stats")
    stat.add_argument("source")
    conv = sub.add_parser("convert")
    conv.add_argument("source")
    conv.add_argument("--delete", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "show":
        filters = {"types": args.type, "kinds": args.kind, "plan_ids": args.plan}
        if args.tail:
            rows = tail(args.source, args.tail, **filters)
        else:
            rows = records(args.source, limit=args.limit, **filters)
        for record in rows:
            print(json.dumps(record, separators=(",", ":")))
    elif args.command == "stats":
        print(f"{'type':24} {'kind':28} {'count':>9} {'bytes':>13}")
        for type_, kind, count, size in stats(args.source):
            print(f"{type_ or '':24} {kind or '':28} {count:9} {size or 0:13}")
    else:
        started = time.perf_counter()
        target = convert(args.source, delete=args.delete)
        elapsed = time.perf_counter() - started
        size = target.stat().st_size
        print(f"{target} {size} bytes in {elapsed:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
