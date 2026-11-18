"""Run the live regression checks one at a time and write one verdict per check.

    uv run --locked python scripts/run-battery.py --out results/<battery>/<round>
    uv run --locked python scripts/run-battery.py --out <dir> --only iron-xray iron-mine
    uv run --locked python scripts/run-battery.py --list

HOW TO READ THE OUTPUT. Each check prints exactly one line starting with RESULT:

    RESULT <name>: PASS   | check: PASS | standstill: NONE | <run folder>
    RESULT <name>: FAIL   | check: FAIL (...) | standstill: VIOLATION ... | <run folder>
    RESULT <name>: UNSURE | check: PASS | standstill: UNSURE (...) | <run folder>

  PASS    good. The check passed and the bot never stood still for 5 s or more.
          Each RESULT line also carries ``search cap: NONE`` or ``VIOLATION``: the
          Java planner hit its node cap toward a goal within 64 blocks (see
          mclab.search_caps). Far cap hits are listed but do not fail.
  FAIL    bad. The check failed, timed out, or the bot stood still for 5 s or more
          while not breaking, placing or crafting. The lines under it say which.
  UNSURE  not proven. The check passed but the recording has holes, so a standstill
          cannot be ruled out. Report it as UNSURE; it is neither a pass nor a fail.

The last line is ``BATTERY: n PASS, n FAIL, n UNSURE``. The exit code is 0 only when
every check is PASS. The same lines are in ``<out>/SUMMARY.md``; ``<out>/<name>.log``
is the check's full output and ``<out>/<name>.idle.txt`` the standstill analysis.
Nothing else needs to be read to report the result.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
HILL = ["--collision-shapes", "--control-profile", "dev-native-route-v1"]
IRON = ["scripts/run-gather.py", "--start", "68", "71", "-64", "--iron"]
IRON += ["--seed", "42424242"]

# name: (arguments after ``python``, timeout in seconds). The modes are fixed here
# because a small house run without --supply is a different, unscaffolded job.
CHECKS: dict[str, tuple[list[str], int]] = {
    "wading": (["scripts/check-companion-wading.py"], 300),
    "wading-trapped": (["scripts/check-companion-wading.py", "--trapped"], 300),
    "scaffold": (["scripts/check-companion-scaffold.py"], 300),
    "scaffold-course": (["scripts/check-companion-scaffold-course.py"], 900),
    "hill-default": (["scripts/check-hill-navigation.py", "--variants", "2"], 600),
    "hill-water": (
        [
            "scripts/check-hill-navigation.py",
            "--kind",
            "water",
            "--variants",
            "2",
            "--swim",
            *HILL,
        ],
        600,
    ),
    "hill-ladder": (
        [
            "scripts/check-hill-navigation.py",
            "--kind",
            "ladder",
            "--variants",
            "2",
            "--climb",
            *HILL,
        ],
        600,
    ),
    "animals": (["scripts/check-animals.py"], 600),
    "natural-ops": (["scripts/check-natural-ops.py"], 300),
    "site-fill": (["scripts/check-site-fill.py"], 600),
    # Same seed and start: ore vision (sensed buried ore) against strip mining blind.
    "iron-xray": (IRON, 900),
    "iron-mine": ([*IRON, "--no-ore-vision"], 900),
    # Mining only: stone pickaxes, no smelting or iron tools.
    "iron-raw": (
        [a for a in IRON if a != "--iron"] + ["--raw-iron", "3", "--no-ore-vision"],
        900,
    ),
    "small-house": (
        [
            "scripts/run-build.py",
            "--start",
            "-24",
            "64",
            "56",
            "--supply",
            "--seed",
            "31415926",
        ],
        900,
    ),
}

ENV = os.environ | {
    "MCLAB_NO_PRUNE": "1",  # keep the evidence of every run
    "PYTHONUNBUFFERED": "1",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}
UV = ["uv", "run", "--locked"]


def run(cmd: list[str], log: Path, timeout: int) -> int | None:
    """Exit code of ``cmd`` with its output in ``log``; None when it timed out."""
    with log.open("w", encoding="utf-8", errors="replace") as out:
        out.write(f"COMMAND: {' '.join(cmd)}\n")
        out.flush()
        proc = subprocess.Popen(cmd, cwd=ROOT, env=ENV, stdout=out, stderr=out)
        try:
            return proc.wait(timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(  # the whole tree: uv's python child holds the body
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
            proc.wait()
            return None


def run_folders() -> set[Path]:
    return {
        p.parent
        for depth in ("*/*", "*/*/*")
        for name in ("events.sqlite3", "events.jsonl")
        for p in RESULTS.glob(f"{depth}/{name}")
    }


def server(*words: str) -> str:
    done = subprocess.run(
        [*UV, "mclab", "server", *words],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout + done.stderr


def restart_server() -> bool:
    server("stop")
    for _ in range(60):
        if '"state": "stopped"' in server("status"):
            break
        time.sleep(2)
    server("start")
    for _ in range(60):  # the status says "running" some seconds after the start
        if '"state": "running"' in server("status"):
            return True
        time.sleep(2)
    return False


def check_verdict(code: int | None, log: Path, timeout: int) -> tuple[str, list[str]]:
    """(``PASS`` or ``FAIL (...)``, the log lines that explain a failure)."""
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    if code == 0:
        return "PASS", []
    why = [ln.strip() for ln in lines if ln.lstrip().startswith("FAIL")]
    why += [ln.strip() for ln in lines[-3:] if ln.strip() and ln.strip() not in why]
    if code is None:
        return f"FAIL (TIMEOUT: still running after {timeout} s, killed)", why[-6:]
    return f"FAIL (exit code {code})", why[-6:]


def standstill_verdict(folders: list[Path], out: Path) -> tuple[str, list[str]]:
    """(``NONE``, ``VIOLATION ...`` or ``UNSURE (...)``, detail lines)."""
    if not folders:
        return "UNSURE (the check recorded no run folder)", []
    done = subprocess.run(
        [*UV, "python", "scripts/analysis/idle_gaps.py", "--json", *map(str, folders)],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    out.write_text(done.stderr + done.stdout, encoding="utf-8")
    try:
        runs = json.loads(done.stdout)
    except json.JSONDecodeError:
        return "UNSURE (the standstill analysis crashed; see the .idle.txt file)", []
    return standstill(runs, done.returncode)


def standstill(runs: dict, code: int) -> tuple[str, list[str]]:
    """The verdict for idle_gaps.py's ``--json`` output and exit code."""
    gaps = [
        (name, r)
        for name, rows in runs.items()
        for r in rows
        if r["kind"] == "VIOLATION"
    ]
    if gaps:
        longest = max(r["unjustified_s"] for _, r in gaps)
        detail = [
            f"stood still {r['unjustified_s']:.1f} s at {r['start']:.1f} s into run "
            f"{name}, position {r['pos']}, last request {r['last_op']}"
            for name, r in sorted(gaps, key=lambda g: -g[1]["unjustified_s"])[:5]
        ]
        return f"VIOLATION {len(gaps)} standstills, longest {longest:.1f} s", detail
    if code != 0:
        holes = sorted(
            {
                why
                for rows in runs.values()
                for r in rows
                if r["kind"] == "META"
                for why in r["incomplete"]
            }
        ) or ["a run had no usable recording"]
        return f"UNSURE ({'; '.join(holes)})", []
    return "NONE", []


def search_cap_verdict(folders: list[Path]) -> tuple[str, list[str]]:
    """Verdict and one line per planner node-cap hit in the runs' events."""
    from mclab.search_caps import cap_hits, verdict

    hits = [h for f in folders for h in cap_hits(f)]
    return verdict(hits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, help="folder for logs and SUMMARY.md")
    ap.add_argument("--only", nargs="+", choices=list(CHECKS), metavar="NAME")
    ap.add_argument("--list", action="store_true", help="print the checks and stop")
    ap.add_argument(
        "--no-restart",
        action="store_true",
        help="use the lab server as it is (default: restart it first, which also "
        "clears a body left by an earlier run)",
    )
    from mclab.run_launch import parse_battery_args

    a = parse_battery_args(ap)
    if a.list:
        for name, (cmd, timeout) in CHECKS.items():
            print(f"{name:15} timeout {timeout:4} s  python {' '.join(cmd)}")
        return 0
    if a.out is None:
        ap.error("--out is required")
    out = a.out if a.out.is_absolute() else ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    from mclab.run_launch import launch_record, record_outcome

    with launch_record(a.run_config, folder=out):
        result = run_battery(a, out)
        record_outcome(result)
        return result


def run_battery(a, out):
    if not a.no_restart and not restart_server():
        print("BATTERY: NOT RUN. The lab server did not start; no check was run.")
        return 2
    if a.no_restart and '"state": "running"' not in server("status"):
        print("BATTERY: NOT RUN. The lab server is not running; no check was run.")
        return 2
    head = subprocess.run(
        ["git", "log", "-1", "--format=%h %s"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    summary = [
        f"# Battery {out.name}",
        "",
        f"Code: `{head}`",
        "",
        "PASS = check passed, no standstill of 5 s or more, and no planner node-cap "
        "hit toward a goal within 64 blocks. FAIL = check failed, timed out, or "
        "either of those was found. UNSURE = check passed but the "
        "recording has holes, so a standstill cannot be ruled out.",
        "",
    ]
    tally = {"PASS": 0, "FAIL": 0, "UNSURE": 0}
    for name in a.only or CHECKS:
        cmd, timeout = CHECKS[name]
        timeout = a.timeout if a.timeout is not None else timeout
        before = run_folders()
        code = run([*UV, "python", *cmd], out / f"{name}.log", timeout)
        folders = sorted(run_folders() - before)
        check, why = check_verdict(code, out / f"{name}.log", timeout)
        still, gaps = standstill_verdict(folders, out / f"{name}.idle.txt")
        cap, cap_lines = search_cap_verdict(folders)
        overall = (
            "FAIL"
            if check != "PASS"
            or still.startswith("VIOLATION")
            or cap.startswith("VIOLATION")
            else "PASS"
            if still == "NONE"
            else "UNSURE"
        )
        tally[overall] += 1
        where = ", ".join(f.relative_to(ROOT).as_posix() for f in folders) or "no run"
        block = [
            f"RESULT {name}: {overall} | check: {check} | "
            f"standstill: {still} | search cap: {cap} | {where}",
            *(f"    check said: {ln[:200]}" for ln in why),
            *(f"    {ln}" for ln in gaps),
            *(f"    cap hit {ln}" for ln in cap_lines),
        ]
        print("\n".join(block), flush=True)
        summary += block
        (out / "SUMMARY.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
        if code is None:  # a killed check leaves its body in the world
            restart_server()
    last = (
        f"BATTERY: {tally['PASS']} PASS, {tally['FAIL']} FAIL, {tally['UNSURE']} UNSURE"
    )
    print(last)
    (out / "SUMMARY.md").write_text(
        "\n".join([*summary, "", last]) + "\n", encoding="utf-8"
    )
    code = 0 if tally["FAIL"] == tally["UNSURE"] == 0 else 1
    (out / "summary.json").write_text(
        json.dumps(
            {
                "run_id": out.name,
                "status": "completed_battery" if code == 0 else "failed",
                "returncode": code,
                "checks": list(a.only or CHECKS),
                "tally": tally,
            },
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
