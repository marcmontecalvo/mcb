"""Planner state changes per attempt from navigation_tick (Oct 31 trace fields).

Usage: uv run --locked python scripts/analysis/planner_trace.py <attempt>/events.sqlite3
Prints tick, (mode, [status, reason, expanded, route len], search start, token,
searched ms) and lane serial whenever any of them changes, then the episode end.
A search token below the lane serial with "pending" = superseded search.
"""

import sys

from mclab import event_log

prev = None
for row in event_log.records(sys.argv[1], types=["navigation_tick"]):
    d = row["payload"]
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
for row in event_log.records(sys.argv[1], types=["server_event"], kinds=["course_episode_complete"]):
    d = row["payload"]["data"]
    print("END", d.get("status"), d.get("reason"), d.get("ticks"))
