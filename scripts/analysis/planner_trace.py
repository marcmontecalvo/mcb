"""Planner state changes per attempt from navigation_tick (Oct 31 trace fields).

Usage: uv run --locked python scripts/analysis/planner_trace.py <attempt>/events.jsonl
Prints tick, (mode, [status, reason, expanded, route len], search start, token,
searched ms) and lane serial whenever any of them changes, then the episode end.
A search token below the lane serial with "pending" = superseded search.
"""

import json
import sys

prev = None
for line in open(sys.argv[1]):
    h = line[:600]
    if '"course_episode_complete"' in h:
        d = json.loads(line)["payload"]["data"]
        print("END", d.get("status"), d.get("reason"), d.get("ticks"))
        continue
    if '"navigation_tick"' not in h:
        continue
    d = json.loads(line)["payload"]
    k = (
        d["mode"],
        str(d.get("result")),
        d.get("search_start"),
        d.get("search_token"),
        d.get("searched_ms"),
    )
    if k != prev:
        print(d["tick"], k, d.get("lane_serial"))
        prev = k
