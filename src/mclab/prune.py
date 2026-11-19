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
a run whose digest cannot be made keeps its files. Never touched: a run that may
still be going (``run_digest.may_be_running``), a run without a build key, a run
whose id is cited in the docs.

Every other family: left alone are the newest ``KEEP_LATEST`` runs and anything
younger than ``MIN_AGE_HOURS`` (a run in progress). Of the others, inside the run:
- a run whose id appears in ``docs/`` or the top-level markdown files (the ledger) keeps
  its databases and has ``events.jsonl`` gzipped;
- any other run has ``events.jsonl`` over ``MIN_BYTES`` gzipped (``events.sqlite3``
  is kept: already compressed) and its other ``*.sqlite3`` (+ ``-wal``/``-shm``)
  deleted (map memory and per-run episode ledgers, derived from the same events).
The shared ``results/episodes.sqlite3`` and anything outside run folders are left
alone.

Dry run unless ``--apply``: ``python -m mclab.prune [--keep N] [--root DIR]`` prints
the plan and what it would free. When an arena session ends
(``recorder.exclusive_arena``) ``sweep`` runs the per-family rule only: superseded
companion runs are pruned only by an explicit ``--apply``.
"""

from __future__ import annotations

import argparse
import gzip
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from mclab import run_digest
from mclab.paths import ROOT

KEEP_LATEST = 3
MIN_AGE_HOURS = 24
MIN_BYTES = 1_000_000
RUN_ID = re.compile(r"\d{8}T\d{12}Z")
DELETE_EVENTS: set[str] = set()  # families whose old logs are not worth compressing
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
    return name == "events.jsonl" or ".sqlite3" in name


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
        latest = {p.name for p in runs[-KEEP_LATEST:]}
        for run in runs:
            if run.name in latest:
                continue
            for path in sorted(run.rglob("*")):
                if not (path.is_file() and _is_raw(path)):
                    continue
                stat = path.stat()
                if (
                    stat.st_size < MIN_BYTES
                    or now - stat.st_mtime < MIN_AGE_HOURS * 3600
                ):
                    continue
                events = path.name == "events.jsonl"
                # events.sqlite3 is the event log and is already compressed: kept
                # for cited runs, and for families whose logs are not deleted.
                store = path.name.startswith("events.sqlite3")
                if run.name in cited:
                    if events:
                        actions.append(Action(path, "gzip", stat.st_size))
                elif store and family.name not in DELETE_EVENTS:
                    continue
                elif events and family.name not in DELETE_EVENTS:
                    actions.append(Action(path, "gzip", stat.st_size))
                else:
                    actions.append(Action(path, "delete", stat.st_size))
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
            elif key is None:
                verdicts.append(Verdict(run, None, "unknown", has_digest=bool(digest)))
            elif run.name in cited:
                verdicts.append(Verdict(run, key, "cited", has_digest=bool(digest)))
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
    """Delete superseded runs' heavy files, each run's digest written first.
    Returns (bytes freed, [(run, why kept)])."""
    freed, kept = 0, []
    for v in verdicts:
        if v.verdict != "superseded" or not v.actions:
            continue
        if run_digest.may_be_running(v.run):  # it changed since the plan
            kept.append((v.run.name, "written to since the plan"))
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
        for action in v.actions:
            try:
                action.path.unlink()
            except FileNotFoundError:
                continue
            freed += action.size
    return freed, kept


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
    }


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
        f"  superseded would free {summary['files']} files, {summary['gigabytes']} GB"
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
    elif result["files"] or summary["files"]:
        print("dry run; pass --apply to do it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
