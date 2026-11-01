"""Per-tick speed class x controller state, plus a position timeline every 40 ticks.

Usage: uv run --locked python scripts/analysis/time_budget.py <attempt>/events.jsonl
Classes: stopped <0.05, slow <0.19, walk <0.245, sprint (blocks/tick). State is
the controller mode, or native:ground/air while the body follows a native route.
"""

"""Per tick: speed class x controller mode / native route state; plus height timeline."""
import collections
import json
import math
import sys

pos, mode, rstate = {}, {}, {}
for line in open(sys.argv[1]):
    h = line[:600]
    if '"policy_sample"' in h:
        p = json.loads(line)["payload"]["data"]
        t = p.get("tick")
        b = p.get("body") or {}
        if "position" in b:
            pos[t] = (
                b["position"],
                b.get("on_ground"),
                (b.get("survival") or {}).get("in_water"),
            )
            rstate[t] = (p.get("route_follower") or {}).get("state")
    elif '"navigation_tick"' in h:
        d = json.loads(line)["payload"]
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
    v = math.hypot(q[0] - p[0], q[2] - p[2])
    cls = (
        "stopped"
        if v < 0.05
        else "slow"
        if v < 0.19
        else "walk"
        if v < 0.245
        else "sprint"
    )
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
