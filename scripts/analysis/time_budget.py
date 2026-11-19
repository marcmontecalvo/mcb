"""Per-tick speed class x controller state, plus a position timeline every 40 ticks.

Usage: uv run --locked python scripts/analysis/time_budget.py <attempt>/events.sqlite3
Classes: stopped <0.05, slow <0.19, walk <0.245, sprint (blocks/tick). State is
the controller mode, or native:ground/air while the body follows a native route.
``speed_class`` is shared with ``mclab.run_digest`` (the run digest's motion share).
"""

import collections
import math
import sys

from mclab import event_log

# upper bounds in blocks per tick, horizontal; anything faster is "sprint"
SPEED_CLASSES = (("stopped", 0.05), ("slow", 0.19), ("walk", 0.245))


def speed_class(v: float) -> str:
    return next((name for name, bound in SPEED_CLASSES if v < bound), "sprint")


def main(source) -> None:
    pos, mode, rstate = {}, {}, {}
    for row in event_log.records(
        source, types=["server_event"], kinds=["policy_sample"]
    ):
        p = row["payload"]["data"]
        t = p.get("tick")
        b = p.get("body") or {}
        if "position" in b:
            pos[t] = (
                b["position"],
                b.get("on_ground"),
                (b.get("survival") or {}).get("in_water"),
            )
            rstate[t] = (p.get("route_follower") or {}).get("state")
    for row in event_log.records(source, types=["navigation_tick"]):
        d = row["payload"]
        mode[d["tick"]] = d["mode"]
    ts = sorted(pos)
    m = None
    table = collections.Counter()
    timeline = []
    for a, b in zip(ts, ts[1:], strict=False):
        m = mode.get(a, m)
        if b - a != 1:
            continue
        p, q = pos[a][0], pos[b][0]
        cls = speed_class(math.hypot(q[0] - p[0], q[2] - p[2]))
        key = (
            m
            if rstate.get(a) != "following"
            else "native:" + ("air" if not pos[a][1] else "ground")
        )
        table[(cls, key)] += 1
        if a % 40 == 0:
            timeline.append((a, [round(c) for c in p], cls, key))
    for k, n in sorted(table.items(), key=lambda kv: -kv[1])[:16]:
        print(n, k)
    for row in timeline:
        print(*row)


if __name__ == "__main__":
    main(sys.argv[1])
