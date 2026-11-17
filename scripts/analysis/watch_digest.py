"""Compact digest of the newest part of a live build run, for a watcher.

    python scripts/analysis/watch_digest.py <run folder> [--seconds 120] [--site x0 x1 z0 z1 ground]

Reads only the tail of the run event log (events.sqlite3) (it grows to gigabytes) and prints what the bot
did in the last window: ops requested, results by reason, edits inside/outside the
site, the longest gap without a successful edit, repeated targets, route plans.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from mclab import event_log

TAIL_ROWS = 200_000
NOISE = {"heartbeat", "planner_map", "route_policy", "planner_request"}
EDITS = ("break_block", "place_block")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--site", type=int, nargs=5, default=(-22, 0, 57, 72, 63))
    args = ap.parse_args()
    x0, x1, z0, z1, ground = args.site
    # terrain batches and policy samples are bulky and skipped
    rows = event_log.tail(args.run, TAIL_ROWS, max_size=9000)
    if not rows:
        print("no events")
        return
    end = rows[-1]["monotonic_ns"] / 1e9
    rows = [r for r in rows if end - r["monotonic_ns"] / 1e9 <= args.seconds]
    ops, results, reasons, plans = Counter(), Counter(), Counter(), Counter()
    targets, outside, pillars, blockers = Counter(), Counter(), [], Counter()
    last_ok = first = rows[0]["monotonic_ns"] / 1e9
    worst, moves = 0.0, []
    for r in rows:
        at, p = r["monotonic_ns"] / 1e9, r.get("payload") or {}
        if r.get("type") == "request":
            op = p.get("op")
            if op in NOISE:
                continue
            ops[op] += 1
            pos = p.get("pos") or p.get("goal")
            if op in EDITS and pos:
                pos = tuple(pos)
                targets[(op, pos)] += 1
                if not (x0 <= pos[0] <= x1 and z0 <= pos[2] <= z1):
                    outside[(op, pos)] += 1
                elif op == "place_block" and pos[1] > ground:
                    pillars.append(pos)
            elif op in ("plan_route", "follow_route") and pos:
                moves.append(tuple(pos) if isinstance(pos, list) else pos)
        elif r.get("type") == "server_event":
            kind, data = p.get("type", ""), p.get("data") or {}
            if kind == "plan_finished":
                plans[f"{data.get('status')}/expanded={data.get('expanded')}"] += 1
            elif kind.endswith("_done") or kind.endswith("_failed"):
                results[kind] += 1
                if kind.endswith("_failed"):
                    reasons[f"{kind}:{data.get('reason')}"] += 1
                    if data.get("blocker_state"):
                        blockers[data["blocker_state"]] += 1
                elif kind.startswith(EDITS):
                    worst, last_ok = max(worst, at - last_ok), at
    worst = max(worst, end - last_ok)
    print(f"window {end - first:.0f}s  events {len(rows)}")
    print("ops", dict(ops.most_common(12)))
    print("results", dict(results.most_common(12)))
    print("fail reasons", dict(reasons.most_common(8)))
    print("blockers", dict(blockers.most_common(5)))
    print("plans", dict(plans.most_common(6)))
    print(f"longest gap without a successful edit: {worst:.1f}s")
    print("repeated edit targets", [(k, n) for k, n in targets.most_common(5) if n > 2])
    print("edits OUTSIDE site", len(outside), list(outside)[:8])
    print("placements above site ground (pillars)", len(pillars), pillars[:8])
    print("last route goals", moves[-4:])
    progress = args.run / "progress.json"
    if progress.exists():
        print("progress", progress.read_text(encoding="utf-8")[:300])


if __name__ == "__main__":
    main()
