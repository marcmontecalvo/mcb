"""Python-side silences: stretches where Python sent nothing to the body.

    python scripts/analysis/silence_gaps.py RUN_DIR [...] [--min-seconds 1.5]

HOW TO READ THE OUTPUT
  One `SILENCE` line per stretch of --min-seconds or longer, then under it what the
  main thread was doing each time the watchdog looked (every 0.5 s), innermost call
  first. The last line per run is `SILENCES: n of 1.5 s or more, longest x s`.
  `SILENCES: 0` is good. Shorter stretches are normal waits and are not printed.
  A silence is not a standstill by itself: the body may be walking a route the mod
  drives. Use idle_gaps.py for standstills; use this to see why Python was quiet.
  Runs recorded before the watchdog existed print `GAP` lines instead (request to
  request, no stack).
  `GC PAUSES: n of 0.1 s or more` counts the times Python's garbage collector stopped
  the whole process. 0 is good. Any pause of 1 s or more is bad: nothing moves in it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mclab import event_log


def events(run: Path):
    types = ["request"]
    path = event_log.resolve(run)
    if path.suffix == ".sqlite3":
        db = event_log.connect(path)
        types += [r[0] for r in db.execute("SELECT DISTINCT type FROM events")
                  if r[0].startswith("python_")]
        db.close()
        yield from event_log.records(path, types=types)
        return
    for e in event_log.records(path):
        if e["type"] == "request" or e["type"].startswith("python_"):
            yield e


def scan(run: Path, min_seconds: float = 1.5, show=print) -> dict:
    """Silences of ``min_seconds`` or more and GC pauses in one run; ``show`` gets
    the per-silence report lines (mclab.run_digest passes a no-op)."""
    t0 = prev = None
    looks: list[tuple[float, list[str]]] = []
    watched, found, pauses = False, [], []
    for e in events(run):
        w, t, p = e["monotonic_ns"] / 1e9, e["type"], e["payload"]
        if t == "python_silence":
            watched = True
            looks.append((p["idle_s"], p["stack"]))
        elif t == "python_silence_end":
            if p["duration_s"] >= min_seconds:
                found.append(p["duration_s"])
                start = w - p["duration_s"] - (t0 or w)
                show(f"  SILENCE {p['duration_s']:.1f} s, starting {start:.1f} s into the run")
                for idle, stack in looks:
                    show(f"      after {idle:.1f} s: " + " <- ".join(stack[:8]))
            looks = []
        elif t == "python_gc":
            pauses.append(p["pause_s"])
        elif t == "request":
            t0 = t0 or w
            if prev and not watched and w - prev[0] >= min_seconds:
                found.append(w - prev[0])
                show(
                    f"  GAP {w - prev[0]:.1f} s, starting {prev[0] - t0:.1f} s into the run,"
                    f" between requests {prev[1]} and {p['op']}"
                )
            prev = (w, p["op"])
    return {"silences": found, "gc_pauses": pauses, "watchdog": watched}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--min-seconds", type=float, default=1.5)
    a = ap.parse_args()
    for run in a.runs:
        print(f"RUN {run.name}")
        result = scan(run, a.min_seconds)
        found, pauses = result["silences"], result["gc_pauses"]
        print(
            f"SILENCES: {len(found)} of {a.min_seconds:g} s or more"
            + (f", longest {max(found):.1f} s" if found else "")
        )
        print(
            f"GC PAUSES: {len(pauses)} of 0.1 s or more"
            + (f", longest {max(pauses):.1f} s" if pauses else "")
        )


if __name__ == "__main__":
    main()
