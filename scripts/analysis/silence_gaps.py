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
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path


def events(run: Path):
    for name in ("events.jsonl", "events.jsonl.gz"):
        f = run / name
        if f.exists():
            with (gzip.open if name.endswith(".gz") else open)(
                f, "rt", encoding="utf8"
            ) as h:
                for line in h:
                    if '"request"' in line[:400] or '"python_silence' in line[:400]:
                        yield json.loads(line)
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--min-seconds", type=float, default=1.5)
    a = ap.parse_args()
    for run in a.runs:
        t0 = prev = None
        looks: list[tuple[float, list[str]]] = []
        watched, found = False, []
        print(f"RUN {run.name}")
        for e in events(run):
            w, t, p = e["monotonic_ns"] / 1e9, e["type"], e["payload"]
            if t == "python_silence":
                watched = True
                looks.append((p["idle_s"], p["stack"]))
            elif t == "python_silence_end":
                if p["duration_s"] >= a.min_seconds:
                    found.append(p["duration_s"])
                    start = w - p["duration_s"] - (t0 or w)
                    print(f"  SILENCE {p['duration_s']:.1f} s, starting {start:.1f} s into the run")
                    for idle, stack in looks:
                        print(f"      after {idle:.1f} s: " + " <- ".join(stack[:8]))
                looks = []
            elif t == "request":
                t0 = t0 or w
                if prev and not watched and w - prev[0] >= a.min_seconds:
                    found.append(w - prev[0])
                    print(
                        f"  GAP {w - prev[0]:.1f} s, starting {prev[0] - t0:.1f} s into the run,"
                        f" between requests {prev[1]} and {p['op']}"
                    )
                prev = (w, p["op"])
        print(
            f"SILENCES: {len(found)} of {a.min_seconds:g} s or more"
            + (f", longest {max(found):.1f} s" if found else "")
        )


if __name__ == "__main__":
    main()
