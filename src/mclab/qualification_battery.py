"""One command for every live qualification check: ``mclab qual``.

The battery (``scripts/run-battery.py``) runs the checks one after another. This module
holds what is testable without a server: which checks to run, which outputs to keep
(failures and anything that looked bad) or delete (clean passes), the summary files and
the final table.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from mclab import prune
from mclab.paths import ROOT

# ``--fast`` leaves these out: the experiments (Q3), 15 minutes each.
FAST_EXCLUDE = (
    "iron-xray",
    "iron-mine",
    "iron-raw",
    "small-house",
    "scaffold-course",
    "scaffold-challenges",
)
SLOW_FRACTION = 0.75  # a check this close to its timeout is kept even when it passed


def default_out(now: datetime | None = None) -> Path:
    """results/battery/<UTC stamp>, relative to the repo root."""
    now = now or datetime.now(UTC)
    return Path("results/battery") / now.strftime("%Y%m%dT%H%M%SZ")


def select_checks(
    all_names: list[str], only: list[str] | None, fast: bool
) -> list[str]:
    """``only`` as given, else every check in order (minus FAST_EXCLUDE when fast)."""
    if only:
        return list(only)
    return [n for n in all_names if not (fast and n in FAST_EXCLUDE)]


def keep_reasons(
    *,
    verdict: str,
    standstill: str,
    search_cap: str,
    duration_s: float,
    timeout_s: float,
) -> list[str]:
    """Why a check's outputs must be kept; empty means a clean pass to discard."""
    why = []
    if verdict != "PASS":
        why.append(verdict)
    if standstill != "NONE":
        why.append(f"standstill: {standstill}")
    if search_cap != "NONE":
        why.append(f"search cap: {search_cap}")
    if duration_s > SLOW_FRACTION * timeout_s:
        why.append(
            f"slow: {duration_s:.0f} s of {timeout_s:.0f} s timeout "
            f"(over {SLOW_FRACTION:.0%})"
        )
    return why


def discard_outputs(root: Path, log: Path, folders: list[Path]) -> dict:
    """Delete a clean check's log, idle file and run folders.

    A run folder whose id the docs cite stays (the prune policy). Only folders at
    ``results/<family>/<run>`` or deeper are ever removed.
    """
    cited = prune.cited_runs(root)
    results = (root / "results").resolve()
    done: dict[str, list[Path]] = {"deleted": [], "cited": []}
    for path in (log, log.with_name(f"{log.stem}.idle.txt")):
        path.unlink(missing_ok=True)
    for folder in folders:
        inside = folder.resolve()
        if not folder.is_dir() or not inside.is_relative_to(results):
            continue
        if len(inside.relative_to(results).parts) < 2:
            continue  # a whole family folder is never a check's run
        if folder.name in cited:
            done["cited"].append(folder)
            continue
        shutil.rmtree(folder, ignore_errors=True)
        done["deleted"].append(folder)
    return done


def exit_code(records: list[dict]) -> int:
    return 0 if records and all(r["verdict"] == "PASS" for r in records) else 1


def tally(records: list[dict]) -> dict[str, int]:
    return {
        v: sum(r["verdict"] == v for r in records) for v in ("PASS", "FAIL", "UNSURE")
    }


def retention_line(rec: dict) -> str:
    if rec["kept"]:
        return f"    retention: kept ({'; '.join(rec['why']) or 'keep-all'})"
    return "    retention: discarded (clean pass)"


def summary_payload(out: Path, records: list[dict], *, keep_all: bool) -> dict:
    code = exit_code(records)
    return {
        "run_id": out.name,
        "status": "completed_battery" if code == 0 else "failed",
        "returncode": code,
        "checks": [r["name"] for r in records],
        "tally": tally(records),
        "keep_all": keep_all,
        # ``block`` is the SUMMARY.md text, not data
        "results": [{k: v for k, v in r.items() if k != "block"} for r in records],
    }


def battery_line(records: list[dict]) -> str:
    t = tally(records)
    return f"BATTERY: {t['PASS']} PASS, {t['FAIL']} FAIL, {t['UNSURE']} UNSURE"


def write_summary(
    out: Path, records: list[dict], *, keep_all: bool, header: list[str]
) -> None:
    """SUMMARY.md (the readable lines) and summary.json (the machine-readable twin)."""
    lines = list(header)
    for rec in records:
        lines += rec.get("block", []) + [retention_line(rec)]
    lines += ["", battery_line(records)]
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = summary_payload(out, records, keep_all=keep_all)
    (out / "summary.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def render_table(records: list[dict]) -> list[str]:
    """Name, verdict, duration and (kept checks only) the log, then the QUAL line."""
    width = max([len(r["name"]) for r in records] + [4])
    lines = [f"{'NAME':{width}}  {'VERDICT':7}  {'TIME':>7}  LOG", "-" * (width + 24)]
    for r in records:
        log = r["log"] if r["kept"] and r["log"] else ""
        lines.append(
            f"{r['name']:{width}}  {r['verdict']:7}  {r['duration_s']:6.0f}s  {log}"
        )
    t = tally(records)
    lines.append(f"QUAL: {t['PASS']} PASS, {t['FAIL']} FAIL, {t['UNSURE']} UNSURE")
    return lines


def qual(argv: list[str], runner=subprocess.call, root: Path = ROOT) -> int:
    """``mclab qual``: run the battery, print the table; exit 0 only if all PASS."""
    ap = argparse.ArgumentParser(
        prog="mclab qual",
        description="Run every live qualification check; keep the evidence of the "
        "bad ones, delete the clean passes.",
    )
    ap.add_argument("--only", nargs="+", metavar="NAME", help="just these checks")
    ap.add_argument(
        "--fast", action="store_true", help="skip " + ", ".join(FAST_EXCLUDE)
    )
    ap.add_argument(
        "--keep-all", action="store_true", help="keep the outputs of passes too"
    )
    ap.add_argument("--out", type=Path, help="default results/battery/<UTC stamp>")
    ap.add_argument("--no-restart", action="store_true", help="use the server as is")
    ap.add_argument("--timeout", type=int, help="override every check's timeout")
    a = ap.parse_args(argv)
    out = a.out or root / default_out()
    cmd = ["uv", "run", "--locked", "python", "scripts/run-battery.py"]
    cmd += ["--out", str(out)]
    if a.only:
        cmd += ["--only", *a.only]
    for flag, on in (
        ("--fast", a.fast),
        ("--keep-all", a.keep_all),
        ("--no-restart", a.no_restart),
    ):
        if on:
            cmd.append(flag)
    if a.timeout is not None:
        cmd += ["--timeout", str(a.timeout)]
    code = runner(cmd, cwd=root)
    summary = Path(out) / "summary.json"
    try:
        records = json.loads(summary.read_text(encoding="utf-8"))["results"]
    except (OSError, ValueError, KeyError):
        print(f"QUAL: NOT RUN (no {summary}; battery exit code {code})")
        return 2
    print()
    print("\n".join(render_table(records)))
    return exit_code(records)


def main(argv: list[str] | None = None) -> int:
    return qual(sys.argv[1:] if argv is None else argv)
