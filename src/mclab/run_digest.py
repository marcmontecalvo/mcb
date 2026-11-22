"""A small, stable summary of one build or gather run: ``<run>/digest.json``.

The event log (``events.sqlite3``, up to gigabytes) and the map are for the run
being worked on. Once a newer attempt of the same build exists, the digest is what
stays (see ``mclab.prune``): outcome and duration, per-phase durations, stalls,
how far the build got, why it failed, and the commit and config it ran with.

Written when ``scripts/run-build.py`` / ``run-gather.py`` end, however they end
(``live_common.digest_on_exit``); older folders get one from ``mclab runs backfill``.

Sources, cheapest first: the small files the run writes (summary.json,
progress.json, launch_status.json, build.json, run_config.json, provenance.json),
then one pass over the event log (``deep``) for what only the log knows:
standstills (``scripts/analysis/idle_gaps.py``), Python silences
(``silence_gaps.py``), time per speed class (``time_budget.py``) and, for runs
whose build.json has no phase timings, phases inferred by ``build_stats.scan``.
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from mclab import build_stats, event_log
from mclab.companion_progress import job_name, read_json_file
from mclab.paths import ROOT

SCHEMA = 1
NAME = "digest.json"
MAX_BYTES = 5000
RUN_ID = re.compile(r"\d{8}T\d{12}Z")
STAMP = "%Y%m%dT%H%M%S%fZ"
TERMINAL = {"finished", "failed", "interrupted"}
# Any write this recent and the run may still be going, whatever its status says.
ACTIVE_S = 2 * 3600
# No terminal status and nothing written for this long: the process was killed.
ABANDONED_S = 24 * 3600
# Stages that wait, writing nothing, for a STOP file (--stage, --inspect): alive
# while quiet, up to HOLD_S (no one keeps a staged world for a week).
HOLD_STAGES = {"staged", "inspection"}
HOLD_S = 7 * 86400
# Files a run writes while it goes; their newest mtime is when it last did anything.
# Not *.gz (prune makes those later), digest.json or build_stats.json (derived), nor
# the store's -wal/-shm: a reader opening the store touches those.
RUN_WRITES = (
    "summary.json",
    "launch_status.json",
    "progress.json",
    "build.json",
    "walkthrough.md",
    "error.txt",
    "events.sqlite3",
    "events.jsonl",
)
DEFAULT_BUILD = "tests/fixtures/schematics/small-house.build.json"  # run-build.py
STANDSTILL_S, STANDSTILL_MOVE = 5.0, 0.5  # idle_gaps.py defaults
SILENCE_S = 1.5  # silence_gaps.py default
CONFIG_KEYS = (
    "supply",
    "coal",
    "empty",
    "margin",
    "rotation",
    "dx",
    "dz",
    "stall_seconds",
    "iron",
    "raw_iron",
    "scaffolding",
)
DETAIL_CHARS = 160
REASON_CHARS = 300


# ---- small helpers ---------------------------------------------------------


def _json(path: Path):
    return read_json_file(path)


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _cut(text, limit: int) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _r(value, digits: int = 1):
    return None if value is None else round(float(value), digits)


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def started_at(run: Path) -> float | None:
    """The run folder's name is its UTC start time."""
    try:
        return datetime.strptime(run.name, STAMP).replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def last_write(run: Path) -> float | None:
    """When the run last wrote anything (see RUN_WRITES), else None."""
    times = []
    for name in RUN_WRITES:
        try:
            times.append((run / name).stat().st_mtime)
        except OSError:
            pass
    times += [p.stat().st_mtime for p in run.glob("gather-*.json")]
    times += [p.stat().st_mtime for p in run.glob("checkpoints/*.json")]
    progress = _dict(_json(run / "progress.json"))
    if isinstance(progress.get("updated_at"), (int, float)):
        times.append(float(progress["updated_at"]))
    return max(times, default=None)


def launch_status(run: Path) -> str | None:
    return _dict(_json(run / "launch_status.json")).get("status")


def is_terminal(run: Path) -> bool:
    """The run said it ended: a terminal launch status, or the final summary."""
    return launch_status(run) in TERMINAL or (run / "summary.json").is_file()


def _quiet_s(run: Path, now: float) -> float:
    return now - (last_write(run) or started_at(run) or 0.0)


def activity(run: Path, now: float | None = None) -> str:
    """ "ended" (it said so), "running" (wrote within ACTIVE_S, or holds for a
    STOP file) or "abandoned" (no terminal status and quiet: killed)."""
    # may_be_running() is the wider test anything that deletes must use.
    if is_terminal(run):
        return "ended"
    now = time.time() if now is None else now
    quiet = _quiet_s(run, now)
    stage = _dict(_json(run / "progress.json")).get("stage")
    if quiet < ACTIVE_S or (stage in HOLD_STAGES and quiet < HOLD_S):
        return "running"
    return "abandoned"


def may_be_running(run: Path, now: float | None = None) -> bool:
    """For anything that deletes, wider than ``activity``: also a run with no
    terminal status quiet for less than ABANDONED_S, and any run with a file
    (the store's -wal included) written within ACTIVE_S, whatever its status."""
    now = time.time() if now is None else now
    if activity(run, now) == "running":
        return True
    if not is_terminal(run) and _quiet_s(run, now) < ABANDONED_S:
        return True
    derived = {NAME, build_stats.CACHE}  # written later by readers, not the run
    try:
        newest = max(
            (
                p.stat().st_mtime
                for p in run.rglob("*")
                if p.name not in derived and p.suffix != ".tmp"
            ),
            default=0.0,
        )
    except OSError:
        return True
    return now - newest < ACTIVE_S


# ---- identity: which build a run attempted ---------------------------------


def _argv_identity(argv) -> dict:
    """run-build.py's own argv (build.json "argv"), for runs without run_config."""
    argv = [str(a) for a in argv or []]

    def after(flag, n=1):
        if flag not in argv:
            return None
        values = argv[argv.index(flag) + 1 : argv.index(flag) + 1 + n]
        return values if len(values) == n else None

    start = after("--start", 3)
    seed = after("--seed")
    build = after("--build")
    resume = after("--resume")
    start_from = after("--from")
    until = after("--until")
    return {
        "build": build[0].replace("\\", "/") if build else DEFAULT_BUILD,
        "start": [int(v) for v in start] if start else None,
        "seed": int(seed[0]) if seed else None,
        "resume": resume[0] if resume else None,
        "start_from": start_from[0] if start_from else None,
        "until": until[0] if until else None,
        "mode": "supply"
        if "--supply" in argv
        else "empty"
        if "--empty" in argv
        else "resume"
        if resume
        else "from"
        if start_from
        else "kit",
    }


def _gather_target(run_section: dict) -> str:
    if run_section.get("raw_iron"):
        return f"raw_iron{run_section['raw_iron']}"
    return "iron" if run_section.get("iron") else "stone"


def identity(run: Path) -> dict:
    """job, build, start, seed (+ gather target, mode, resume) and its source."""
    config = _dict(_dict(_json(run / "run_config.json")).get("run"))
    if config.get("test"):
        job = config["test"]
        return {
            "job": job,
            "build": config.get("build") if job != "gather" else None,
            "target": _gather_target(config) if job == "gather" else None,
            "start": config.get("start"),
            "seed": config.get("seed"),
            "mode": "resume"
            if config.get("resume")
            else "from"
            if config.get("start_from")
            else "supply"
            if config.get("supply")
            else "empty"
            if config.get("empty")
            else "kit",
            "resume": config.get("resume"),
            "start_from": config.get("start_from"),
            "until": config.get("until"),
            "source": "run_config",
        }
    job = job_name(run)
    if job == "gather":  # old gather folders do not record their start
        return {"job": "gather", "start": None, "source": "folder"}
    build = _dict(_json(run / "build.json"))
    if build.get("argv"):
        return {
            "job": job or "build",
            **_argv_identity(build["argv"]),
            "source": "argv",
        }
    found = build_stats.identity(run)  # the dashboard's launch.json or a checkpoint
    if found.get("build"):
        return {
            "job": job or "build",
            **{k: found.get(k) for k in ("build", "start", "seed")},
            "source": "launch"
            if (run / build_stats.LAUNCH).is_file()
            else "checkpoint",
        }
    return {"job": job, "start": None, "source": None}


def build_key(ident: dict, segment: bool = True) -> str | None:
    """What makes two runs attempts at "the same build": the job, the build file
    (or the gather target), the start cell and the world seed, then the segment of a
    stage lab (``+from=site_ready+until=structure``: not comparable with a whole
    build). ``segment=False``: the whole build's key, as the stage library files
    it. None when the run does not say (prune then leaves it alone)."""
    job, start = ident.get("job"), ident.get("start")
    if not job or not isinstance(start, list) or len(start) != 3:
        return None
    if job == "gather":
        what = ident.get("target") or "?"
    else:
        what = Path(str(ident.get("build") or "?")).name.removesuffix(".build.json")
    seed = ident.get("seed")
    key = f"{job}:{what}@{start[0]},{start[1]},{start[2]}"
    if seed is not None:
        key += f"#{seed}"
    if segment:
        for name, label in (("start_from", "from"), ("until", "until")):
            if ident.get(name):
                key += f"+{label}={ident[name]}"
    return key


# ---- what happened ---------------------------------------------------------


def outcome(summary, status: str | None, crashed: bool, state: str) -> str:
    """pass | fail | crashed | interrupted | aborted (killed, no result) | running."""
    if state == "running":
        return "running"
    if status == "interrupted":
        return "interrupted"
    if not isinstance(summary, list):  # no summary: it died before finish()
        return "crashed" if crashed or status == "failed" else "aborted"
    if crashed:
        return "crashed"
    return "pass" if summary and all(r.get("pass") for r in summary) else "fail"


def _crashed(run: Path, summary) -> bool:
    return (run / "error.txt").is_file() or any(
        isinstance(r, dict) and r.get("case") == "driver crashed" for r in summary or []
    )


def passed(run: Path) -> bool:
    """The run ended with every summary case passing (cheap: small files only)."""
    summary = _json(run / "summary.json")
    return (
        outcome(summary, launch_status(run), _crashed(run, summary), "ended") == "pass"
    )


def _error_line(run: Path) -> str | None:
    try:
        text = (run / "error.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else None


def failure(run, result, summary, build, progress) -> dict | None:
    if result in ("pass", "running"):
        return None
    failed = [r for r in summary or [] if isinstance(r, dict) and not r.get("pass")]
    case = failed[0] if failed else {}
    if result == "crashed":
        reason = _error_line(run) or case.get("detail")
    elif build.get("stall_reason") and build.get("stop") not in (None, "done"):
        reason = build["stall_reason"]
    elif case:
        reason = f"{case.get('case')}: {case.get('detail')}"
    else:
        reason = (
            f"no result; last stage {progress.get('stage')!r}"
            f" ({progress.get('detail') or 'no detail'})"
        )
    by_reason = _dict(build.get("failure_summary")).get("build_by_reason")
    if not by_reason and isinstance(build.get("failures"), list):
        by_reason = Counter(
            str(f[1]) for f in build["failures"] if isinstance(f, list) and len(f) > 1
        )
    top = dict(Counter(by_reason or {}).most_common(6))
    return {
        "reason": _cut(reason, REASON_CHARS),
        "case": case.get("case"),
        "stop": build.get("stop") or build.get("job_stop"),
        "failures_by_reason": top or None,
    }


def progress_of(job, summary, build, progress) -> dict:
    """How far it got: template cells correct of all, else the live counter."""
    value = {
        "stage": progress.get("stage"),
        "stop": build.get("stop") or build.get("job_stop"),
        "site": _dict(build.get("site")).get("stop"),
        "placed": None,
        "total": None,
    }
    counts = _dict(build.get("counts"))
    if counts:
        value.update(
            placed=counts.get("correct"),
            total=sum(v for v in counts.values() if isinstance(v, int)),
            wrong=counts.get("wrong"),
            missing=counts.get("missing"),
        )
    elif job == "gather" and isinstance(summary, list):
        value.update(
            placed=sum(bool(r.get("pass")) for r in summary), total=len(summary)
        )
    else:
        value.update(placed=progress.get("done"), total=progress.get("total"))
    if value["placed"] is not None and value["total"]:
        value["fraction"] = round(value["placed"] / value["total"], 3)
    return value


def _seconds(mapping) -> dict | None:
    out = {
        str(k): _r(v)
        for k, v in _dict(mapping).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    return out or None


def phases_of(run: Path, job, build: dict, started: float | None, deep: bool):
    """(phases_s, stages_s, source). Phases come from the runner's own clock when
    build.json has it; else from the event log (build_stats.scan: gather | trees |
    site | building, split where the first house block was placed); a gather run's
    targets end when their gather-<target>.json was written."""
    if build.get("phases_s"):
        return _seconds(build["phases_s"]), _seconds(build.get("stages_s")), "runner"
    if job == "gather":
        files = sorted(run.glob("gather-*.json"), key=lambda p: p.stat().st_mtime)
        if files and started is not None:
            phases, previous = {}, started
            for path in files:
                end = path.stat().st_mtime
                phases[path.stem.removeprefix("gather-")] = _r(end - previous)
                previous = end
            return phases, None, "file_times"
        return None, None, None
    log = build_stats.cached(run)
    if not log and deep and event_log.locate(run) is not None:
        log = build_stats.scan(run)
    if log and log.get("events"):
        return _seconds(log.get("phases_s")), None, "events"
    return None, None, None


def _stops(items, key) -> dict | None:
    values = [
        float(i[key])
        for i in items or []
        if isinstance(i, dict) and isinstance(i.get(key), (int, float))
    ]
    if not items:
        return None
    return {
        "count": len(items),
        "total_s": _r(sum(values)),
        "max_s": _r(max(values, default=0.0)),
    }


def _analysis(name: str):
    """A scripts/analysis module, loaded once (they are scripts, not a package)."""
    key = f"mclab_analysis_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            key, ROOT / "scripts" / "analysis" / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[key] = module
    return sys.modules[key]


def _motion(bodies: dict) -> dict | None:
    """Share of ticks per speed class (time_budget.py), from the policy samples."""
    speed_class = _analysis("time_budget").speed_class
    counts: Counter[str] = Counter()
    for body in bodies.values():
        by_tick = {s[0]: s[2] for s in body["samples"] if s[5] == 1}
        ticks = sorted(by_tick)
        for a, b in zip(ticks, ticks[1:], strict=False):
            if b - a == 1:
                p, q = by_tick[a], by_tick[b]
                counts[speed_class(math.hypot(q[0] - p[0], q[2] - p[2]))] += 1
    total = sum(counts.values())
    if not total:
        return None
    return {
        k: round(counts[k] / total, 3) for k in ("stopped", "slow", "walk", "sprint")
    }


def log_analysis(run: Path) -> dict:
    """The one pass over the event log: standstills, silences, motion."""
    source = event_log.locate(run)
    if source is None:
        return {}
    idle = _analysis("idle_gaps")
    bodies = idle.load(source)
    windows = idle.analyse(source, STANDSTILL_S, STANDSTILL_MOVE, bodies)
    violations = [w for w in windows if w["kind"] == "VIOLATION"]
    meta = [w for w in windows if w["kind"] == "META"]
    # idle_gaps reports overlapping windows (one per anchor sample): a standstill
    # is their union
    spans = idle.merge([(w["start"], w["end"]) for w in violations])
    silences = _analysis("silence_gaps").scan(run, SILENCE_S, show=lambda *_: None)
    found = silences["silences"]
    return {
        "standstills": {
            "count": len(spans),
            "total_s": _r(sum(z - a for a, z in spans)),
            "max_s": _r(max((z - a for a, z in spans), default=0.0)),
            "exempt": sum(w["kind"] == "EXEMPT" for w in windows),
            "inconclusive": any(m["incomplete"] for m in meta) or not meta,
        },
        "silences": {
            "count": len(found),
            "total_s": _r(sum(found)),
            "max_s": _r(max(found, default=0.0)),
            "gc_pauses": len(silences["gc_pauses"]),
        },
        "motion": _motion(bodies),
    }


def _config(run: Path, build: dict) -> dict:
    config = _dict(_json(run / "run_config.json"))
    section = _dict(config.get("run"))
    body = _dict(config.get("body"))
    value = {k: section[k] for k in CONFIG_KEYS if section.get(k) not in (None, False)}
    if body.get("control_profile"):
        value["control_profile"] = body["control_profile"]
    preset = _dict(body.get("awareness")).get("preset")
    if preset:
        value["awareness"] = preset
    if not config and build.get("argv"):
        value["argv"] = _cut(" ".join(map(str, build["argv"])), 240)
    return value


def _git(run: Path) -> dict:
    provenance = _dict(_json(run / "provenance.json"))
    git = _dict(provenance.get("git"))
    return {
        "commit": git.get("commit"),
        "dirty": git.get("dirty"),
        "config_sha256": provenance.get("config_sha256"),
    }


def _bytes(run: Path) -> dict:
    events = total = 0
    for path in run.rglob("*"):
        try:
            size = path.stat().st_size if path.is_file() else 0
        except OSError:
            continue
        total += size
        if path.parent == run and path.name.startswith("events."):
            events += size
    return {"events": events, "total": total}


def _cases(summary) -> list | None:
    if not isinstance(summary, list):
        return None
    rows = []
    for row in summary[:12]:
        if not isinstance(row, dict):
            continue
        item = {"case": _cut(row.get("case"), 60), "pass": bool(row.get("pass"))}
        if not item["pass"]:
            item["detail"] = _cut(row.get("detail"), DETAIL_CHARS)
        rows.append(item)
    return rows


# ---- the digest ------------------------------------------------------------


def build(run: Path, *, deep: bool = True, ended: bool = False, now=None) -> dict:
    """The digest of one run folder. ``deep`` reads the event log (seconds to a
    minute on a big run); ``ended`` says the caller knows the run is over (the
    run's own exit hook), whatever the file times say."""
    run = Path(run)
    now = time.time() if now is None else now
    summary = _json(run / "summary.json")
    progress = _dict(_json(run / "progress.json"))
    build_json = _dict(_json(run / "build.json"))
    status = launch_status(run)
    state = "ended" if ended else activity(run, now)
    ident = identity(run)
    job = ident.get("job")
    result = outcome(summary, status, _crashed(run, summary), state)
    started = started_at(run)
    last = last_write(run)
    phases, stages, phase_source = phases_of(run, job, build_json, started, deep)
    stalls = {
        "watchdog": _stops(build_json.get("stalls"), "idle_s"),
        "long_stops": _stops(build_json.get("long_stops"), "duration_s"),
    }
    analysis_error = None
    if deep:
        try:
            stalls.update(log_analysis(run))
        except Exception as error:  # noqa: BLE001 - recorded in the digest
            analysis_error = _cut(f"{type(error).__name__}: {error}", 200)
            print(
                f"digest {run.name}: event log analysis failed: {analysis_error}",
                file=sys.stderr,
            )
    value = {
        "schema": SCHEMA,
        "run": run.name,
        "family": run.parent.name,
        "job": job,
        "build_key": build_key(ident),
        "identity": {k: v for k, v in ident.items() if v is not None},
        "outcome": result,
        "status": status,
        "started_at": _iso(started),
        "ended_at": _iso(last),
        "duration_s": _r(last - started) if last and started else None,
        "job_s": _r(build_json.get("job_duration_s")),
        "ticks": _dict(build_json.get("ticks")) or None,
        "phases_s": phases,
        "stages_s": stages,
        "phase_source": phase_source,
        "progress": progress_of(job, summary, build_json, progress),
        "failure": failure(run, result, summary, build_json, progress),
        "stalls": stalls,
        "cases": _cases(summary),
        "git": _git(run),
        "config": _config(run, build_json),
        "bytes": _bytes(run),
        "deep": deep and analysis_error is None,
        "analysis_error": analysis_error,
        "computed_at": _iso(now),
    }
    return fit(value)


def fit(value: dict) -> dict:
    """Keep the digest under MAX_BYTES: drop the bulkiest optional parts first."""
    for drop in ("cases", "stages_s", "config", "identity"):
        if len(dumps(value)) <= MAX_BYTES:
            break
        value[drop] = None
    return value


def dumps(value: dict) -> str:
    return json.dumps(value, indent=1, allow_nan=False) + "\n"


def write(run: Path, **options) -> dict:
    value = build(run, **options)
    target = Path(run) / NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(dumps(value), encoding="utf-8")
    temporary.replace(target)
    return value


def load(run: Path) -> dict | None:
    value = _json(Path(run) / NAME)
    return value if isinstance(value, dict) and value.get("schema") == SCHEMA else None


def ensure(run: Path) -> dict:
    """The run's digest, written now (deep) if missing or made before the run ended."""
    value = load(run)
    if value is None or value.get("outcome") == "running" or not value.get("deep"):
        value = write(run, deep=True)
    return value


def runs(base: Path) -> list[Path]:
    """Run folders under a results family, oldest first."""
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and RUN_ID.fullmatch(p.name))
