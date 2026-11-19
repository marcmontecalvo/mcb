"""Keep ``results/`` small: raw run logs are for the runs being worked on; older runs
keep their small result files (summary.json, build.json, checkpoints, progress.json).

A run is a timestamp-named folder under ``results/<family>/``.

Build and gather runs (``results/companion/``) are kept per build key (see
``plan_superseded``; the key is ``mclab.run_digest.build_key``: job, build file or
gather target, start cell, seed). Per key, the newest ``keep`` ended runs (default
1) and every passing run keep everything. An older, superseded run keeps its
digest.json, summary.json, walkthrough.md, final checkpoint and other small files;
its event log (``events.*``), map and other ``*.sqlite3`` files and intermediate
checkpoints go. Its digest is written first (deep, from the event log) if missing;
a run whose digest cannot be made keeps its files. A run without a build key
(``unknown``) loses the same heavy files, without a digest. Never touched: a run
that may still be going (``run_digest.may_be_running``), a run whose id is cited
in the docs.

Every other family (navigation, natural, hills, ...): left alone are the newest
``KEEP_LATEST`` runs and anything younger than ``MIN_AGE_HOURS`` (a run in
progress). Of the others, anywhere inside the run folder:
- a run whose id appears in ``docs/`` or the top-level markdown files (the ledger)
  keeps its event log (``events.jsonl`` is gzipped) and its episode ledger; its
  other ``*.sqlite3`` (map memory, derived from the same events) is deleted;
- any other run loses every raw file: ``events.*`` and ``*.sqlite3`` (+ sidecars).
Small result files (summary.json, ...) always stay.

Episode ledgers (``results/episodes.sqlite3`` and ``results/navigation/<run>/
episodes.sqlite3``, ``trim_ledgers``) keep every run, episode and event row, but
event payloads over ``recorder.LEDGER_PAYLOAD_MAX`` of runs older than
``LEDGER_DAYS`` become ``{"type", "elided_bytes"}`` stubs (the run's own event log
holds the full record), then the file is vacuumed.

Dry run unless ``--apply``: ``python -m mclab.prune [--keep N] [--ledger-days D]
[--root DIR]`` prints the plan and what it would free. When an arena session ends
(``recorder.exclusive_arena``: every run-build, run-gather, navigation and natural
session) ``auto`` applies all of it, capped at ``AUTO_MAX_RUNS`` superseded runs
(each may need a digest pass over its log); the rest waits for the next session or
an explicit ``--apply``. ``MCLAB_NO_PRUNE=1`` turns the automatic prune off.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mclab import recorder, run_digest
from mclab.paths import ROOT

KEEP_LATEST = 3
MIN_AGE_HOURS = 24
MIN_BYTES = 1_000_000
LEDGER_DAYS = 7  # episode-ledger payloads older than this become stubs
AUTO_MAX_RUNS = 3  # superseded runs digested and pruned per automatic prune
RUN_ID = re.compile(r"\d{8}T\d{12}Z")
SIDECARS = ("-wal", "-shm", "-journal")
LEDGER = ("docs", "README.md", "CLAUDE.md")
SUPERSEDE = ("companion",)  # families kept per build key (plan_superseded)


@dataclass(frozen=True)
class Action:
    path: Path
    kind: str  # gzip | delete
    size: int


@dataclass(frozen=True)
class Verdict:
    """What the per-build-key rule decided for one run."""

    run: Path
    key: str | None
    # latest | pass | cited | active | unknown | superseded
    verdict: str
    actions: tuple[Action, ...] = ()
    has_digest: bool = False


def cited_runs(root: Path = ROOT) -> set[str]:
    """Run ids named anywhere in the docs."""
    found: set[str] = set()
    for name in LEDGER:
        place = root / name
        if not place.exists():
            continue
        files = [place] if place.is_file() else sorted(place.rglob("*.md"))
        for file in files:
            found.update(
                RUN_ID.findall(file.read_text(encoding="utf-8", errors="ignore"))
            )
    return found


def _is_raw(path: Path) -> bool:
    name = path.name
    return name.startswith("events.") or ".sqlite3" in name


def _cited_kind(name: str) -> str | None:
    """What a cited run does with one raw file: gzip, keep (None) or delete."""
    if name == "events.jsonl":
        return "gzip"
    if name.startswith(("events.", "episodes.sqlite3")):
        return None  # the event log and the episode ledger (trimmed separately)
    return "delete"  # map memory and other databases derived from the events


def _sidecars(path: Path) -> list[Path]:
    return [
        side
        for suffix in SIDECARS
        if (side := path.with_name(path.name + suffix)).is_file()
    ]


def _run_actions(run: Path, cited: bool, now: float) -> list[Action]:
    actions: list[Action] = []
    for path in sorted(run.rglob("*")):
        if not (path.is_file() and _is_raw(path)) or path.name.endswith(SIDECARS):
            continue
        stat = path.stat()
        if stat.st_size < MIN_BYTES or now - stat.st_mtime < MIN_AGE_HOURS * 3600:
            continue
        kind = _cited_kind(path.name) if cited else "delete"
        if kind is None:
            continue
        actions.append(Action(path, kind, stat.st_size))
        if kind == "delete":
            actions += [Action(s, "delete", s.stat().st_size) for s in _sidecars(path)]
    return actions


def plan(root: Path = ROOT, now: float | None = None) -> list[Action]:
    """The per-family rule, for every family except SUPERSEDE."""
    now = time.time() if now is None else now
    cited = cited_runs(root)
    actions: list[Action] = []
    results = root / "results"
    for family in sorted(p for p in results.iterdir() if p.is_dir()):
        if family.name in SUPERSEDE:
            continue
        runs = sorted(
            p for p in family.iterdir() if p.is_dir() and RUN_ID.fullmatch(p.name)
        )
        for run in runs[:-KEEP_LATEST]:
            actions += _run_actions(run, run.name in cited, now)
    return actions


# ---- build and gather runs: per build key ----------------------------------


def final_checkpoint(run: Path) -> Path | None:
    """checkpoints/final.json, else the highest-numbered checkpoint."""
    folder = run / "checkpoints"
    if (folder / "final.json").is_file():
        return folder / "final.json"
    numbered = sorted(folder.glob("[0-9]*.json"))
    return numbered[-1] if numbered else None


def heavy_files(run: Path) -> list[Path]:
    """What a superseded run loses: the event log, the map and any other sqlite
    database, and every checkpoint but the final one."""
    found = [
        p
        for p in sorted(run.iterdir())
        if p.is_file() and (p.name.startswith("events.") or ".sqlite3" in p.name)
    ]
    keep = final_checkpoint(run)
    found += [p for p in sorted(run.glob("checkpoints/*.json")) if p != keep]
    return found


def _passing(run: Path, digest: dict | None) -> bool:
    if digest is not None and digest.get("outcome") != "running":
        return digest.get("outcome") == "pass"
    return run_digest.passed(run)


def _actions(run: Path) -> tuple[Action, ...]:
    out = []
    for path in heavy_files(run):
        try:
            out.append(Action(path, "delete", path.stat().st_size))
        except OSError:
            continue  # gone meanwhile
    return tuple(out)


def plan_superseded(
    root: Path = ROOT, keep: int = 1, now: float | None = None
) -> list[Verdict]:
    """One verdict per run folder of the SUPERSEDE families, oldest first."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    now = time.time() if now is None else now
    cited = cited_runs(root)
    verdicts: list[Verdict] = []
    groups: dict[str, list[tuple[Path, bool, bool]]] = defaultdict(list)
    for family in SUPERSEDE:
        for run in run_digest.runs(root / "results" / family):
            digest = run_digest.load(run)
            key = (
                digest.get("build_key")
                if digest
                else run_digest.build_key(run_digest.identity(run))
            )
            if run_digest.may_be_running(run, now):
                verdicts.append(Verdict(run, key, "active", has_digest=bool(digest)))
            elif run.name in cited:
                verdicts.append(Verdict(run, key, "cited", has_digest=bool(digest)))
            elif key is None:  # nothing supersedes it, nothing cites it
                verdicts.append(
                    Verdict(run, None, "unknown", _actions(run), bool(digest))
                )
            else:
                groups[key].append((run, _passing(run, digest), bool(digest)))
    for key, members in groups.items():
        members.sort(key=lambda m: m[0].name)
        newest = {m[0] for m in members[-keep:]}
        for run, passed, has_digest in members:
            if run in newest:
                verdicts.append(Verdict(run, key, "latest", has_digest=has_digest))
            elif passed:
                verdicts.append(Verdict(run, key, "pass", has_digest=has_digest))
            else:
                verdicts.append(
                    Verdict(run, key, "superseded", _actions(run), has_digest)
                )
    return sorted(verdicts, key=lambda v: (v.run.parent.name, v.run.name))


def apply_superseded(verdicts: list[Verdict]) -> tuple[int, list[tuple[str, str]]]:
    """Delete superseded runs' heavy files, each run's digest written first, and
    keyless runs' heavy files. Returns (bytes freed, [(run, why kept)])."""
    freed, kept = 0, []
    for v in verdicts:
        if v.verdict not in ("superseded", "unknown") or not v.actions:
            continue
        if run_digest.may_be_running(v.run):  # it changed since the plan
            kept.append((v.run.name, "written to since the plan"))
            continue
        if v.verdict == "unknown":  # no build key: no digest to keep
            freed += _delete(v.actions)
            continue
        try:
            digest = run_digest.ensure(v.run)
        except Exception as error:  # noqa: BLE001 - reported, the run is kept whole
            kept.append((v.run.name, f"digest failed: {error!r}"))
            continue
        if not digest.get("deep"):
            kept.append(
                (v.run.name, f"digest incomplete: {digest.get('analysis_error')}")
            )
            continue
        freed += _delete(v.actions)
    return freed, kept


def _delete(actions) -> int:
    freed = 0
    for action in actions:
        try:
            action.path.unlink()
        except FileNotFoundError:
            continue
        freed += action.size
    return freed


def _kind(path: Path) -> str:
    if path.parent.name == "checkpoints":
        return "checkpoints"
    if path.name.startswith("events."):
        return "events"
    return "maps"


def summarize_superseded(verdicts: list[Verdict]) -> dict:
    gone = [a for v in verdicts for a in v.actions]
    by_kind: Counter[str] = Counter()
    for a in gone:
        by_kind[_kind(a.path)] += a.size
    keys: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for v in verdicts:
        if v.key is None:
            continue
        row = keys[v.key]
        row[0] += 1
        row[1] += v.verdict == "superseded"
        row[2] += sum(a.size for a in v.actions)
    return {
        "runs": len(verdicts),
        "verdicts": dict(Counter(v.verdict for v in verdicts)),
        "superseded_already_small": sum(
            v.verdict == "superseded" and not v.actions for v in verdicts
        ),
        "need_digest": sum(
            v.verdict == "superseded" and bool(v.actions) and not v.has_digest
            for v in verdicts
        ),
        "files": len(gone),
        "gigabytes": round(sum(a.size for a in gone) / 1e9, 2),
        "gigabytes_by_kind": {k: round(n / 1e9, 2) for k, n in by_kind.items()},
        "keys": {k: tuple(v) for k, v in sorted(keys.items())},
        "keyless_gigabytes": round(
            sum(a.size for v in verdicts if v.verdict == "unknown" for a in v.actions)
            / 1e9,
            2,
        ),
    }


# ---- episode ledgers ---------------------------------------------------------


def _started(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


_STUB = (
    "CASE WHEN json_valid(payload) AND json_type(payload, '$.type') = 'text' "
    "THEN json_object('type', json_extract(payload, '$.type'), "
    "'elided_bytes', length(payload)) "
    "ELSE json_object('elided_bytes', length(payload)) END"
)


def _trim_one(path: Path, cutoff: float, run: bool) -> int:
    """Stub the big payloads of runs started before ``cutoff``; rows stay."""
    db = sqlite3.connect(path, timeout=2)
    try:
        old = [
            run_id
            for run_id, started in db.execute("SELECT run_id, started_at FROM runs")
            if (at := _started(started)) is not None and at < cutoff
        ]
        if not old:
            return 0
        where = f"run_id IN ({','.join('?' * len(old))}) AND length(payload) > ?"
        params = (*old, recorder.LEDGER_PAYLOAD_MAX)
        count = db.execute(f"SELECT count(*) FROM events WHERE {where}", params)
        rows = count.fetchone()[0]
        if run and rows:
            with db:
                db.execute(f"UPDATE events SET payload = {_STUB} WHERE {where}", params)
            db.execute("VACUUM")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # WAL mode: shrink now
        return rows
    finally:
        db.close()


def trim_ledgers(
    root: Path = ROOT,
    *,
    days: float = LEDGER_DAYS,
    now: float | None = None,
    run: bool = True,
) -> dict:
    """Elide old event payloads in every episode ledger (see the module doc)."""
    cutoff = (time.time() if now is None else now) - days * 86400
    rows, databases, skipped, before, after = 0, 0, [], 0, 0
    for path in recorder.episode_databases(root):
        size = path.stat().st_size
        try:
            rows += _trim_one(path, cutoff, run)
        except sqlite3.Error:  # locked by a live run, or not a ledger
            skipped.append(str(path))
            continue
        databases += 1
        before += size
        after += path.stat().st_size
    return {
        "applied": run,
        "databases": databases,
        "rows": rows,
        "gigabytes_before": round(before / 1e9, 2),
        "gigabytes_after": round(after / 1e9, 2),
        "skipped": skipped,
    }


# ---- automatic prune -------------------------------------------------------


def auto(
    root: Path = ROOT,
    *,
    max_runs: int = AUTO_MAX_RUNS,
    ledger_days: float = LEDGER_DAYS,
) -> dict:
    """Everything above, applied, with the digest work capped at ``max_runs``
    superseded runs (oldest first). Run at the end of every arena session."""
    if os.environ.get("MCLAB_NO_PRUNE"):
        return {"skipped": "MCLAB_NO_PRUNE"}
    result: dict = {"sweep": sweep(root)}
    verdicts = plan_superseded(root)
    superseded = [v for v in verdicts if v.verdict == "superseded" and v.actions]
    keyless = [v for v in verdicts if v.verdict == "unknown" and v.actions]
    chosen = superseded[:max_runs]
    freed, kept = apply_superseded(chosen + keyless)
    result |= {
        "superseded_runs": len(chosen),
        "deferred": len(superseded) - len(chosen),
        "keyless_runs": len(keyless),
        "gigabytes": round(freed / 1e9, 2),
        "kept": len(kept),
        "ledger": trim_ledgers(root, days=ledger_days),
    }
    return result


# ---- the per-family rule's mechanics ---------------------------------------


def _compress(path: Path) -> None:
    target = path.with_name(path.name + ".gz")
    with path.open("rb") as source, gzip.open(target, "wb", compresslevel=3) as out:
        shutil.copyfileobj(source, out, 1 << 20)
    path.unlink()  # only after the copy finished; a failure leaves the original


def apply(actions: list[Action]) -> int:
    """Carry out the plan; returns bytes of the originals removed (gzip results are
    smaller, so this is an upper bound on what was freed)."""
    freed = 0
    for action in actions:
        if action.kind == "gzip":
            _compress(action.path)
        else:
            action.path.unlink()
        freed += action.size
    return freed


def sweep(root: Path = ROOT, *, run: bool = True) -> dict:
    """The per-family rule (the arena-end hook); SUPERSEDE families untouched."""
    actions = plan(root)
    freed = apply(actions) if run else sum(a.size for a in actions)
    return {
        "applied": run,
        "files": len(actions),
        "gzip": sum(a.kind == "gzip" for a in actions),
        "delete": sum(a.kind == "delete" for a in actions),
        "gigabytes": round(freed / 1e9, 2),
    }


def _print_superseded(summary: dict, keep: int) -> None:
    v = summary["verdicts"]
    print(
        f"build/gather runs (per build key: newest {keep} + passing runs keep all):"
        f" {summary['runs']} runs: "
        + ", ".join(
            f"{name} {v.get(name, 0)}"
            for name in ("latest", "pass", "cited", "active", "unknown", "superseded")
        )
    )
    kinds = ", ".join(
        f"{k} {n} GB" for k, n in sorted(summary["gigabytes_by_kind"].items())
    )
    print(
        f"  superseded and keyless runs would free {summary['files']} files, "
        f"{summary['gigabytes']} GB (keyless {summary['keyless_gigabytes']} GB)"
        + (f" ({kinds})" if kinds else "")
        + f"; {summary['superseded_already_small']} superseded runs already small"
    )
    if summary["need_digest"]:
        print(
            f"  {summary['need_digest']} superseded runs have no digest.json yet:"
            " --apply writes it from the event log before deleting"
            " (`mclab runs backfill` does it ahead)"
        )
    rows = [(k, *r) for k, r in summary["keys"].items() if r[1]]
    if rows:
        width = max(len(r[0]) for r in rows)
        print(f"  {'build key':<{width}}  runs  superseded      GB")
        for key, total, superseded, size in sorted(rows, key=lambda r: -r[3]):
            print(f"  {key:<{width}}  {total:>4}  {superseded:>10}  {size / 1e9:6.2f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mclab.prune", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--apply", action="store_true", help="do it (default: dry run)")
    parser.add_argument(
        "--keep",
        type=int,
        default=1,
        metavar="N",
        help="build/gather runs: keep the N newest ended runs per build key whole",
    )
    parser.add_argument(
        "--ledger-days",
        type=float,
        default=LEDGER_DAYS,
        metavar="D",
        help="episode ledgers: stub the big event payloads of runs older than D days",
    )
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repo root holding results/"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    result = sweep(args.root, run=args.apply)
    print("other families:", result)
    verdicts = plan_superseded(args.root, keep=args.keep)
    summary = summarize_superseded(verdicts)
    _print_superseded(summary, args.keep)
    if args.apply:
        freed, kept = apply_superseded(verdicts)
        print(f"  freed {freed / 1e9:.2f} GB")
        for name, why in kept:
            print(f"  kept {name}: {why}")
    ledgers = trim_ledgers(args.root, days=args.ledger_days, run=args.apply)
    print("episode ledgers:", ledgers)
    if not args.apply and (result["files"] or summary["files"] or ledgers["rows"]):
        print("dry run; pass --apply to do it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
