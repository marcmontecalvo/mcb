"""Stats for one house-building run (results/companion/<run>/), for the dashboard.

Two sources: the small JSON files the run writes (build.json, progress.json, read
every time) and one streamed pass over the event log (0.5-4 GB, so its numbers are
cached in build_stats.json beside the run and survive the log being pruned).

    python -m mclab.build_stats results/companion/<run> [...]
"""

import heapq
import json
import queue
import re
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from mclab import companion_progress, event_log

VERSION = 2
CACHE = "build_stats.json"
LAUNCH = "launch.json"  # written by the dashboard: the job a run was started with
LIVE_S = 60  # a log written this recently belongs to a running build: do not scan
# The one place that names a run's event logs. When the log is split, add
# "timeline.jsonl" (requests/results) and "blocks.jsonl" (blocks placed/broken)
# here; "seen.jsonl" (world snapshots) holds nothing these stats or the live view
# read, so it stays out. The first name found is the log.
LOG_NAMES = ("events.sqlite3", "events.jsonl", "events.jsonl.gz")
_RESULTS = frozenset(
    "place_block_done place_block_failed break_block_done break_block_failed "
    "block_broken plan_finished container_done craft_done furnace_done".split()
)
_IDLE_OPS = frozenset(("heartbeat", "stop", "despawn"))
# ponytail: phases are told apart by what was placed, not by markers (the log has
# none). Site prep only places dirt and work stations are not house blocks, so the
# first other block placed starts "building". A house made of dirt would fool
# this; the fix is run-build.py logging a phase marker, then read it here.
_NOT_HOUSE = ("dirt", "grass_block", "scaffolding", "crafting_table", "furnace")
_NOT_HOUSE += ("chest", "barrel", "shulker_box")


def event_logs(folder: Path) -> list[Path]:
    """The run's event log (the store, else plain or gzipped jsonl), if it exists."""
    for name in LOG_NAMES:
        if (folder / name).is_file():
            return [folder / name]
    return []


def iter_events(folder: Path):
    """Stream (seconds, kind, payload) for requests and the op results in _RESULTS.

    kind is "request" (payload = op name) or a server event type (payload = data).
    """
    for path in event_logs(folder):
        requests = event_log.records(path, types=["request"])
        results = event_log.records(path, types=["server_event"], kinds=_RESULTS)
        for record in heapq.merge(requests, results, key=_order):
            payload = record.get("payload")
            at = record["monotonic_ns"] / 1e9
            if record["type"] == "request":
                if isinstance(payload, dict) and isinstance(payload.get("op"), str):
                    yield at, "request", payload["op"]
            elif isinstance(payload, dict):
                yield at, payload["type"], payload.get("data") or {}


def _order(record: dict) -> int:
    return record["monotonic_ns"]


def _block(state) -> str:
    return str(state or "").split("[")[0].removeprefix("minecraft:")


def scan(folder: Path) -> dict:
    """One pass over the event log: everything the small JSON files do not hold."""
    first = last_work = None
    places, breaks, logs, station = [], [], [], []
    place_failed, break_failed, plans = Counter(), Counter(), Counter()
    expanded = 0
    for at, kind, data in iter_events(folder):
        if kind == "request":
            first = at if first is None else first
            if data not in _IDLE_OPS:
                last_work = at
        else:
            last_work = max(last_work or first or at, at)
        if kind == "place_block_done":
            places.append((at, _block(data.get("placed"))))
        elif kind == "break_block_done":
            breaks.append(at)
        elif kind == "place_block_failed":
            place_failed[str(data.get("reason"))] += 1
        elif kind == "break_block_failed":
            break_failed[str(data.get("reason"))] += 1
        elif kind == "block_broken":
            if _block(data.get("block")).endswith(("_log", "_stem")):
                logs.append(at)
        elif kind == "plan_finished":
            plans[str(data.get("status"))] += 1
            expanded = max(expanded, data.get("expanded") or 0)
        elif kind in {"container_done", "craft_done", "furnace_done"}:
            station.append(at)
    if first is None:
        return {"events": False}
    end = last_work or first
    # phase boundaries, see _NOT_HOUSE: gather | trees | site | building
    building = next((t for t, b in places if b not in _NOT_HOUSE), None)
    before = end if building is None else building
    gathered = max((t for t in station if t < before), default=first)
    trees = max((t for t in logs if gathered < t < before), default=gathered)
    phases = {"gather": gathered - first, "trees": trees - gathered}
    phases["site"] = before - trees
    phases["building"] = end - before
    placed = [
        t for t, b in places
        if building is not None and t >= before and b not in _NOT_HOUSE
    ]
    # longest wait between two edits that worked, while building (else whole run)
    start = before if building is not None else first
    edits = sorted(t for t in [*(t for t, _ in places), *breaks] if t >= start)
    gap, gap_at, previous = 0.0, 0.0, start
    for t in [*edits, end]:
        if t - previous > gap:
            gap, gap_at = t - previous, previous - first
        previous = t
    minutes = phases["building"] / 60
    return {
        "events": True,
        "total_s": round(end - first, 1),
        "phases_s": {k: round(v, 1) for k, v in phases.items()},
        "placed": len(placed),
        "per_minute": round(len(placed) / minutes, 2) if minutes > 0.5 else None,
        "places_ok": len(places),
        "breaks_ok": len(breaks),
        "place_failed": dict(place_failed.most_common()),
        "break_failed": dict(break_failed.most_common()),
        "plans": sum(plans.values()),
        "plan_status": dict(plans.most_common()),
        # RoutePlanner returns "incomplete" when it stops at its node limit with
        # frontier left; TerrainPlanner also uses it for too little known terrain
        "plans_incomplete": plans["incomplete"],
        "plan_expanded_max": expanded,
        "longest_gap_s": round(gap, 1),
        "longest_gap_at_s": round(gap_at, 1),
    }


def _signature(folder: Path) -> list:
    return [
        [p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in event_logs(folder)
    ]


def cached(folder: Path) -> dict | None:
    """The cached log scan if it is still good: same version, and the same log
    files as when it was made (or no log files left: the prune removed them)."""
    value = companion_progress.read_json_file(folder / CACHE)
    if not isinstance(value, dict) or value.get("version") != VERSION:
        return None
    try:
        now = _signature(folder)
    except OSError:
        return None
    return value if not now or now == value.get("logs") else None


def is_live(folder: Path) -> bool:
    try:
        return any(
            time.time()
            - (event_log.mtime(p) if p.suffix == ".sqlite3" else p.stat().st_mtime)
            < LIVE_S
            for p in event_logs(folder)
        )
    except OSError:
        return True


def compute(folder: Path) -> dict:
    """Scan the log and write the cache. Minutes on a multi-GB log."""
    logs = _signature(folder)
    value = {"version": VERSION, "logs": logs, "computed_at": time.time()}
    value.update(scan(folder))
    if _signature(folder) == logs:  # a log that grew meanwhile is not cached
        temporary = folder / (CACHE + ".tmp")
        temporary.write_text(json.dumps(value, indent=1), encoding="utf-8")
        temporary.replace(folder / CACHE)
    return value


def _detail(text) -> dict:
    """progress.json's detail line: "site=ready cleared=0 filled=260 stalls=3"."""
    return dict(p.split("=", 1) for p in str(text or "").split() if "=" in p)


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def identity(folder: Path) -> dict:
    """Which build a run made. The dashboard records it at launch; a run started
    by hand only says so in its checkpoints (head of the file, before the cells)."""
    launch = companion_progress.read_json_file(folder / LAUNCH)
    if isinstance(launch, dict) and launch.get("build"):
        return {k: launch.get(k) for k in ("build", "seed", "start", "args")}
    for path in sorted(folder.glob("checkpoints/*.json"))[:1]:
        try:
            with open(path, "rb") as file:
                head = file.read(4096).decode("utf-8", "replace")
        except OSError:
            break
        build = re.search(r'"build":\s*"([^"]+)"', head)
        seed = re.search(r'"seed":\s*(-?\d+)', head)
        start = re.search(r'"start":\s*(\[[^\]]*\])', head)
        return {
            "build": build[1].replace("\\\\", "/") if build else None,
            "seed": int(seed[1]) if seed else None,
            "start": json.loads(start[1]) if start else None,
        }
    return {"build": None, "seed": None, "start": None}


def is_build(folder: Path) -> bool:
    if (folder / "build.json").is_file() or (folder / "checkpoints").is_dir():
        return True
    return (companion_progress.read_progress(folder) or {}).get("job") == "build"


def report(folder: Path) -> dict:
    """What the run's own small files say. Cheap: read on every request."""
    build = companion_progress.read_json_file(folder / "build.json")
    progress = companion_progress.read_progress(folder) or {}
    detail = _detail(progress.get("detail"))
    value: dict = {"finished": isinstance(build, dict), "stage": progress.get("stage")}
    if isinstance(build, dict):
        site = build.get("site") or {}
        stalls = build.get("stalls") or []
        blocked = site.get("blocked") or {}
        value.update(
            result=build.get("stop") or build.get("job_stop"),
            job_stop=build.get("job_stop"),
            stall_reason=build.get("stall_reason"),
            ticks=build.get("ticks"),
            cells=build.get("counts"),
            steps=build.get("steps"),
            site={
                "stop": site.get("stop"),
                "cleared": site.get("cleared"),
                "filled": site.get("filled"),
                "blocked": len(blocked) if hasattr(blocked, "__len__") else blocked,
                "trees": site.get("trees"),
            }
            if site
            else None,
            stalls=len(stalls),
            recoveries=dict(
                Counter(str(s.get("recovery")) for s in stalls if isinstance(s, dict))
            ),
            failures=dict(
                Counter(
                    str(f[1])
                    for f in build.get("failures") or []
                    if isinstance(f, list) and len(f) > 1
                )
            ),
            refills=len(build.get("refills") or []),
            shortfall=build.get("shortfall") or {},
            gather_stop=(build.get("gather") or {}).get("stop"),
            supplied=build.get("supplied"),
        )
    else:
        # killed or still running: progress.json and the operator's marker files
        note = next(
            (
                p.stem
                for p in sorted(folder.glob("*.txt"))
                if p.name.startswith(("KILLED", "ENDED"))
            ),
            None,
        )
        error = (folder / "error.txt").is_file()
        value.update(
            result="killed" if note else "error" if error else None,
            note=note,
            site={
                "stop": detail.get("site"),
                "cleared": _number(detail.get("cleared")),
                "filled": _number(detail.get("filled")),
                "blocked": None,
                "trees": None,
            }
            if "site" in detail
            else None,
            stalls=_number(detail.get("stalls")),
            steps=_number(detail.get("steps")),
            placed_progress=progress.get("done"),
        )
    return value


def stats(folder: Path) -> dict:
    """Everything known now: the files, plus the log scan if it is cached."""
    log = cached(folder)
    state = (
        "ready"
        if log
        else "no_log"
        if not event_logs(folder)
        else "running"
        if is_live(folder)
        else "pending"
    )
    value = {**report(folder), **(log or {}), "state": state}
    if value.get("result") is None:
        value["result"] = "running" if state == "running" else "no result"
    return value


def runs(base: Path) -> list[dict]:
    """Every build run, newest first, each with the run before it of the same
    build; unknown identities have no automatic baseline."""
    rows = []
    for folder in sorted(base.glob("*"), reverse=True):
        if folder.is_dir() and is_build(folder):
            rows.append({"run": folder.name, **identity(folder), "folder": folder})
    for index, row in enumerate(rows):
        older = rows[index + 1 :]
        same = [r for r in older if r["build"] == row["build"]]
        row["previous"] = same[0]["run"] if same and row["build"] else None
    return rows


class Scanner:
    """Scans logs one at a time on a background thread so pages never wait."""

    def __init__(self):
        self.wanted: queue.Queue = queue.Queue()
        self.queued: set[Path] = set()
        self.errors: dict[str, str] = {}
        self.lock = threading.Lock()
        self.thread = None

    def want(self, folder: Path) -> None:
        with self.lock:
            if folder in self.queued or folder.name in self.errors:
                return
            self.queued.add(folder)
            self.wanted.put(folder)
            if self.thread is None:
                self.thread = threading.Thread(target=self._work, daemon=True)
                self.thread.start()

    def _work(self) -> None:
        while True:
            folder = self.wanted.get()
            try:
                if not cached(folder) and not is_live(folder):
                    compute(folder)
            except (OSError, ValueError, EOFError) as error:  # e.g. a cut-off gzip
                self.errors[folder.name] = str(error)
            finally:
                with self.lock:
                    self.queued.discard(folder)


def main() -> None:
    for name in sys.argv[1:]:
        folder = Path(name)
        started = time.time()
        if not cached(folder):
            compute(folder)
        value = {k: v for k, v in stats(folder).items() if k != "logs"}
        print(json.dumps({"run": folder.name, **identity(folder), **value}, indent=1))
        print(f"{folder.name}: {time.time() - started:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
