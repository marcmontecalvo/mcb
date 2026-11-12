"""Python-side silences: stretches where no request went to the body.

    python scripts/analysis/silence_gaps.py RUN_DIR [...] [--min-seconds 1.0]

With `python_silence` events (bridge watchdog) each silence prints its length and the
main thread's stack (innermost first). For older runs without them it lists the
request-to-request gaps, so the stretch can still be placed against idle_gaps.py.
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
    ap.add_argument("--min-seconds", type=float, default=1.0)
    a = ap.parse_args()
    for run in a.runs:
        t0 = prev = None
        stacks = 0
        print(f"RUN {run.name}")
        for e in events(run):
            w, t, p = e["monotonic_ns"] / 1e9, e["type"], e["payload"]
            if t == "python_silence":
                stacks += 1
                print(
                    f"  {w - (t0 or w):8.2f}s idle {p['idle_s']:.2f}s  "
                    + " <- ".join(p["stack"])
                )
            elif t == "python_silence_end":
                print(
                    f"  {w - (t0 or w):8.2f}s SILENCE ENDED after {p['duration_s']:.2f}s ({p['stacks']} stacks)"
                )
            elif t == "request":
                t0 = t0 or w
                if prev and w - prev[0] >= a.min_seconds and not stacks:
                    print(
                        f"  {prev[0] - t0:8.2f}s gap {w - prev[0]:.2f}s {prev[1]} -> {p['op']}"
                    )
                prev, stacks = (w, p["op"]), 0


if __name__ == "__main__":
    main()
