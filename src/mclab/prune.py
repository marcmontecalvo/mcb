"""Keep ``results/`` small: raw run logs are for the runs being worked on; older runs
keep their small result files (summary.json, build.json, checkpoints, progress.json).

A run is a timestamp-named folder under ``results/<family>/``. Left alone: the newest
``KEEP_LATEST`` runs of each family, and anything younger than ``MIN_AGE_HOURS`` (a run
in progress). Of the others, inside the run:
- a run whose id appears in ``docs/`` or the top-level markdown files (the ledger) keeps
  its databases and has ``events.jsonl`` gzipped;
- any other run has ``events.jsonl`` over ``MIN_BYTES`` gzipped (companion runs:
  deleted, they are iteration noise; checkpoints and build.json say what happened) and
  its ``*.sqlite3`` (+ ``-wal``/``-shm``) deleted (map memory and per-run episode
  ledgers, derived from the same events).
The shared ``results/episodes.sqlite3`` and anything outside run folders are left
alone. Dry run unless ``apply``: ``python -m mclab.prune`` prints the plan,
``--apply`` runs it.
Also runs when an arena session ends (``recorder.exclusive_arena``).
"""

from __future__ import annotations

import gzip
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from mclab.paths import ROOT

KEEP_LATEST = 3
MIN_AGE_HOURS = 24
MIN_BYTES = 1_000_000
RUN_ID = re.compile(r"\d{8}T\d{12}Z")
DELETE_EVENTS = {"companion"}  # families whose old logs are not worth compressing
LEDGER = ("docs", "README.md", "CLAUDE.md")


@dataclass(frozen=True)
class Action:
    path: Path
    kind: str  # gzip | delete
    size: int


def cited_runs(root: Path = ROOT) -> set[str]:
    """Run ids named anywhere in the docs."""
    found: set[str] = set()
    for name in LEDGER:
        place = root / name
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
    now = time.time() if now is None else now
    cited = cited_runs(root)
    actions: list[Action] = []
    results = root / "results"
    for family in sorted(p for p in results.iterdir() if p.is_dir()):
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
                if run.name in cited:
                    if events:
                        actions.append(Action(path, "gzip", stat.st_size))
                elif events and family.name not in DELETE_EVENTS:
                    actions.append(Action(path, "gzip", stat.st_size))
                else:
                    actions.append(Action(path, "delete", stat.st_size))
    return actions


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
    actions = plan(root)
    freed = apply(actions) if run else sum(a.size for a in actions)
    return {
        "applied": run,
        "files": len(actions),
        "gzip": sum(a.kind == "gzip" for a in actions),
        "delete": sum(a.kind == "delete" for a in actions),
        "gigabytes": round(freed / 1e9, 2),
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    result = sweep(run="--apply" in args)
    print(result)
    if not result["applied"] and result["files"]:
        print("dry run; pass --apply to do it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
