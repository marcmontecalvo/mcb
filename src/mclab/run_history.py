"""``mclab runs``: attempts of the same build side by side, from their digests.

    mclab runs [KEY] [--last N] [--unknown] [--json]   table per build key
    mclab runs show RUN                                  one run's digest
    mclab runs backfill [RUN ...] [--shallow] [--force]  write missing digest.json

KEY is any part of a build key (``build:house-large@-24,64,56#31415926``). A run
without digest.json is summarized on the fly from its small files (no event-log
pass: its standstill column shows "?"); ``backfill`` writes the full digest.
Attempt 1 is the oldest run of a key. Columns: outcome, wall duration, phases
(longest first, under 5 s left out), stalls (the build's own stall watchdog:
count/time), still (idle_gaps standstills: count/time), progress (cells correct
of the template, or targets met), commit (* = dirty tree).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from mclab import run_digest
from mclab.paths import ROOT

FAMILY = "results/companion"
PHASE_NAMES = {
    "setup_arena": "setup",
    "chests_setup": "chests",
    "gathering": "gather",
    "clearing_and_site_prep": "site",
    "building_structure": "struct",
    "placing_details": "detail",
    "refills": "refill",
    "building": "build",
    "stone-pickaxe": "stone",  # gather targets (gather-<target>.json)
    "iron-pickaxe": "iron",
    "raw-iron": "raw_iron",
}
MIN_PHASE_S = 5.0
MAX_PHASES = 5


def clock(seconds) -> str:
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def phases_cell(phases) -> str:
    if not phases:
        return "-"
    shown = [
        (PHASE_NAMES.get(name, name[:6]), s)
        for name, s in phases.items()
        if isinstance(s, (int, float)) and s >= MIN_PHASE_S
    ]
    shown = sorted(shown, key=lambda p: -p[1])[:MAX_PHASES]
    order = list(phases)  # back into run order
    names = {PHASE_NAMES.get(n, n[:6]): i for i, n in enumerate(order)}
    shown.sort(key=lambda p: names.get(p[0], 0))
    return " ".join(f"{name} {clock(s)}" for name, s in shown) or "-"


def stops_cell(stops) -> str:
    if stops is None:
        return "?"
    if not stops.get("count"):
        return "0"
    return f"{stops['count']}/{clock(stops.get('total_s'))}"


def progress_cell(progress) -> str:
    placed, total = progress.get("placed"), progress.get("total")
    if placed is None or not total:
        text = "-"
    else:
        text = f"{placed}/{total} {round(100 * placed / total)}%"
    last = progress.get("stop") or progress.get("stage")
    return f"{text} {last}" if last else text


def commit_cell(git) -> str:
    sha = (git or {}).get("commit")
    return "-" if not sha else sha[:7] + ("*" if git.get("dirty") else "")


def row(attempt: int, d: dict) -> list[str]:
    stalls = d.get("stalls") or {}
    started = (d.get("started_at") or "")[5:16].replace("T", " ")
    return [
        str(attempt),
        started or d["run"][:13],
        d.get("outcome") or "?",
        clock(d.get("duration_s")),
        phases_cell(d.get("phases_s")),
        stops_cell(stalls.get("watchdog")) if d.get("job") != "gather" else "-",
        stops_cell(stalls.get("standstills")),
        progress_cell(d.get("progress") or {}),
        commit_cell(d.get("git")),
    ]


HEADER = [
    "#",
    "started",
    "outcome",
    "dur",
    "phases",
    "stalls",
    "still",
    "progress",
    "commit",
]


def table(rows: list[list[str]]) -> list[str]:
    widths = [max(len(r[i]) for r in [HEADER, *rows]) for i in range(len(HEADER))]
    return [
        "  "
        + "  ".join(cell.ljust(w) for cell, w in zip(r, widths, strict=True)).rstrip()
        for r in [HEADER, *rows]
    ]


def digests(base: Path) -> list[dict]:
    """Each run's digest.json, or a shallow one made now (not written)."""
    out = []
    for run in run_digest.runs(base):
        value = run_digest.load(run)
        if value is None or value.get("outcome") == "running":
            value = run_digest.build(run, deep=False)
        out.append(value)
    return out


def listing(values: list[dict], key: str | None, last: int, unknown: bool) -> list[str]:
    groups: dict[str | None, list[dict]] = defaultdict(list)
    for d in values:
        groups[d.get("build_key")].append(d)
    lines = []
    keyed = [k for k in groups if k is not None and (key is None or key in k)]
    keyed.sort(key=lambda k: groups[k][-1]["run"], reverse=True)  # recent first
    for k in keyed:
        runs = groups[k]
        shown = runs[-last:] if last else runs
        first = len(runs) - len(shown) + 1
        lines.append(
            f"{k}  ({len(runs)} runs"
            + (f", last {len(shown)}" if len(shown) < len(runs) else "")
            + ")"
        )
        lines += table([row(first + i, d) for i, d in enumerate(shown)])
        lines.append("")
    loose = groups.get(None, [])
    if loose and key is None:
        if unknown:
            lines.append("(no build key: run_config/argv/checkpoint do not say)")
            lines += table([row(i + 1, d) for i, d in enumerate(loose[-last:])])
        else:
            lines.append(f"{len(loose)} runs have no build key (--unknown lists them)")
    return lines


def backfill(runs: list[Path], *, deep: bool, force: bool) -> int:
    written = 0
    for run in runs:
        if run_digest.may_be_running(run):
            print(f"skip  {run.name}: may still be running")
            continue
        old = run_digest.load(run)
        if old is not None and not force and (old.get("deep") or not deep):
            continue
        started = time.perf_counter()
        try:
            value = run_digest.write(run, deep=deep)
        except Exception as error:  # noqa: BLE001 - one bad folder must not stop the rest
            print(f"FAIL  {run.name}: {error!r}", file=sys.stderr)
            continue
        written += 1
        print(
            f"wrote {run.name}: {value['outcome']} {value['build_key']}"
            f" ({time.perf_counter() - started:.1f}s)",
            flush=True,
        )
    return written


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root_parser = argparse.ArgumentParser(add_help=False)
    root_parser.add_argument(
        "--root", type=Path, default=ROOT, help="repo root holding results/"
    )
    if argv[:1] == ["backfill"]:
        parser = argparse.ArgumentParser(
            prog="mclab runs backfill", parents=[root_parser]
        )
        parser.add_argument("runs", nargs="*", type=Path)
        parser.add_argument(
            "--shallow", action="store_true", help="skip the event-log pass"
        )
        parser.add_argument(
            "--force", action="store_true", help="rewrite existing digests"
        )
        args = parser.parse_args(argv[1:])
        runs = args.runs or run_digest.runs(args.root / FAMILY)
        written = backfill(runs, deep=not args.shallow, force=args.force)
        print(f"{written} digests written")
        return 0
    if argv[:1] == ["show"]:
        parser = argparse.ArgumentParser(prog="mclab runs show", parents=[root_parser])
        parser.add_argument("run")
        args = parser.parse_args(argv[1:])
        run = Path(args.run)
        if not run.is_dir():
            run = args.root / FAMILY / args.run
        value = run_digest.load(run) or run_digest.build(run, deep=False)
        print(run_digest.dumps(value), end="")
        return 0
    parser = argparse.ArgumentParser(
        prog="mclab runs", description=__doc__.split("\n\n")[0], parents=[root_parser]
    )
    parser.add_argument("key", nargs="?", help="part of a build key")
    parser.add_argument(
        "--last", type=int, default=10, help="attempts per key (0: all)"
    )
    parser.add_argument(
        "--unknown", action="store_true", help="list runs without a key"
    )
    parser.add_argument(
        "--json", action="store_true", help="the digests as one JSON list"
    )
    args = parser.parse_args(argv)
    values = digests(args.root / FAMILY)
    if args.json:
        chosen = [
            d
            for d in values
            if args.key is None or args.key in (d.get("build_key") or "")
        ]
        print(json.dumps(chosen))
        return 0
    print("\n".join(listing(values, args.key, args.last, args.unknown)).rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
