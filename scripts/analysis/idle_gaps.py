"""Independent "the bot stood still" detector. Does not read the STALL watchdog.

    python scripts/analysis/idle_gaps.py RUN_DIR [RUN_DIR ...] [--min-seconds 5.0] [--move 0.5] [--json]

Data: events.jsonl[.gz] holds a server_event/policy_sample for every game tick
(body position, ~20 Hz) stamped with monotonic_ns (receive time, ~0.1 s steps).
A window is a maximal stretch of wall time >= --min-seconds in which every
sample stays < --move blocks (3D) from the window's first sample. It is EXEMPT
only if the longest stretch NOT covered by evidence of allowed activity is
shorter than --min-seconds. Evidence (wall time):
  break  : dig_packet START_DESTROY_BLOCK until block_broken / STOP_DESTROY_BLOCK /
           break_block_done|failed for that block, or a later START elsewhere (cap 10 s)
  place  : place_block_done, or growth of step_progress.placed_positions; +-0.5 s each (chained if < 1 s apart)
  craft  : craft/furnace request received until its *_done / *_failed (same request_sequence)
Thinking, planning, map uploads, rejected routes, retries are never evidence.
Samples with a tick gap > 5 and wall gap > 1 s are "unknown" and cut windows.
Exit code 1 if any VIOLATION.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path

PARSE = {
    "server_event",
    "observation",
    "request",
    "controller_error",
    "native_route_edge_rejected",
    "native_route_finished",
    "native_step_confirmed",
    "route_prefetch_started",
    "route_prefetch_adopted",
    "python_silence",
    "python_silence_end",
}
NOISE = {
    "route_progress",
    "planner_map_accepted",
    "route_policy_accepted",
    "req:planner_map",
    "req:route_policy",
    "dig_packet",
    "step_progress",
    "req:cancel_plan",
    "req:cancel_route",
    "plan_accepted",
    "plan_finished",
    "plan_cancelled",
    "route_cancelled",
    "route_accepted",
    "req:plan_route",
    "req:follow_route",
    "planner_request_accepted",
    "req:planner_request",
}
COUNTED = (
    "req:planner_request",
    "req:plan_route",
    "req:planner_map",
    "req:route_policy",
    "native_route_edge_rejected",
    "rejected",
    "route_cancelled",
    "route_stopped",
    "break_block_failed",
    "place_block_failed",
    "controller_error",
)
END_EVENTS = ("stopped", "despawned", "course_episode_complete")
PLACE_PAD, BREAK_CAP, SPARSE_TICKS, SPARSE_WALL = 0.5, 10.0, 5, 1.0
TYPE_RE = re.compile(r'"type": ?"(\w+)"')


def lines(path: Path):
    with (
        gzip.open(path, "rt", encoding="utf8")
        if path.suffix == ".gz"
        else open(path, encoding="utf8")
    ) as f:
        yield from f


def short(d: dict) -> str:
    keys = (
        "op",
        "pos",
        "position",
        "blocker",
        "reason",
        "status",
        "recipe",
        "state",
        "start",
        "goal",
        "target",
        "source",
        "error",
        "duration_s",
        "stack",
    )
    return " ".join(f"{k}={json.dumps(d[k])[:60]}" for k in keys if k in d)[:150]


def load(path: Path) -> dict:
    """-> {body_id: dict(samples, events, ready, end, craft, breaks, places)}; wall in seconds."""
    bodies: dict = {}
    launches: dict = {}
    for ln in lines(path):
        m = TYPE_RE.search(ln[:500])
        if not m or m.group(1) not in PARSE:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        identity = (e.get("run_id"), e.get("episode_id"), e.get("body_id"))
        launch = e.get("launch_id") or (e.get("payload") or {}).get("launch_id")
        previous, generation = launches.get(identity, (None, 0))
        if launch is not None and previous is not None and launch != previous:
            generation += 1
        launches[identity] = (launch or previous, generation)
        b = bodies.setdefault(
            (*identity, generation),
            dict(
                samples=[],
                events=[],
                ready=None,
                end=None,
                ready_wall=None,
                end_wall=None,
                active_request=False,
                open_craft={},
                craft=[],
                open_actions={},
                actions=[],
                breaks=[],
                places=[],
                seq_op={},
                last_tick=0,
                placed_n=0,
                open_break=None,
            ),
        )
        w, p, t = e["monotonic_ns"] / 1e9, e["payload"], e["type"]
        if t == "observation":
            bd, tick = p.get("body") or {}, p.get("tick")
            if bd.get("position") and isinstance(tick, (int, float)):
                b["samples"].append(
                    (tick, w, tuple(bd["position"]),
                     (p.get("route_follower") or {}).get("state"),
                     (p.get("step_follower") or {}).get("state"), 0)
                )
                b["last_tick"] = tick
            continue
        if t == "request":
            op = p.get("op")
            if op not in (None, "spawn", "despawn", "stop", "heartbeat", "kit"):
                b["active_request"] = True
            b["seq_op"][p.get("sequence")] = op
            if op != "heartbeat":
                b["events"].append((w, b["last_tick"], "req:" + op, short(p)))
            continue
        if t != "server_event":
            b["events"].append((w, b["last_tick"], t, short(p)))
            continue
        k, d, tick = p["type"], p.get("data", {}), p.get("tick", b["last_tick"])
        b["last_tick"] = tick
        if k == "policy_sample":
            bd = d.get("body", {})
            if bd.get("position"):
                b["samples"].append(
                    (
                        tick,
                        w,
                        tuple(bd["position"]),
                        (d.get("route_follower") or {}).get("state"),
                        (d.get("step_follower") or {}).get("state"),
                        1,  # prefer the policy sample when both report a tick
                    )
                )
            continue
        if k == "received":
            op, rs = d.get("op"), d.get("request_sequence")
            if op in ("craft", "furnace"):
                b["open_craft"][rs] = (w, op)
            if op in ("attack_entity", "eat"):
                b["open_actions"][rs] = (w, tick, op)
            continue
        if k == "course_started" and b["ready"] is None:
            b["ready"] = tick
            b["ready_wall"] = w
        if k == "course_started":
            b["active_request"] = True
        if k == "body_state" and d.get("state") == "ready" and b["ready"] is None:
            b["ready"] = tick
            b["ready_wall"] = w
        if k in END_EVENTS and b["end"] is None:
            b["end"] = tick
            b["end_wall"] = w
        rs = d.get("request_sequence")
        if rs in b["open_actions"] and k in ("attack_entity_done", "eat_done"):
            w0, tick0, op = b["open_actions"].pop(rs)
            if k == op + "_done" and isinstance(tick, (int, float)):
                duration = max(0, min(w - w0, (tick - tick0) / 20,
                                      5.0 if op == "attack_entity" else 2.5))
                b["actions"].append((w0, w0 + duration, op))
        elif rs in b["open_actions"] and k in ("attack_entity_failed", "eat_failed"):
            b["open_actions"].pop(rs)
        if rs in b["open_craft"] and k.split("_")[-1] in ("done", "failed"):
            w0, op = b["open_craft"].pop(rs)
            b["craft"].append((w0, w, op))
        pos = d.get("position") or d.get("pos")
        if k == "dig_packet" and d.get("action") == "START_DESTROY_BLOCK":
            ob = b["open_break"]
            if ob and ob[1] != d.get("position"):
                b["breaks"].append((ob[0], w))
            b["open_break"] = (w, d.get("position"))
        elif b["open_break"] and (
            (k in ("block_broken", "break_block_done") and pos == b["open_break"][1])
            or (
                k == "dig_packet"
                and d.get("action") == "STOP_DESTROY_BLOCK"
                and pos == b["open_break"][1]
            )
            or k == "break_block_failed"
        ):
            b["breaks"].append((b["open_break"][0], w))
            b["open_break"] = None
        if k == "place_block_done":
            b["places"].append(w)
        elif k == "step_progress":
            n = len(d.get("placed_positions") or [])
            if n > b["placed_n"]:
                b["places"].append(w)
            b["placed_n"] = n
        b["events"].append((w, tick, k, short(d)))
    for b in bodies.values():
        if b["open_break"]:
            b["breaks"].append((b["open_break"][0], b["open_break"][0] + BREAK_CAP))
        b["breaks"] = [(a, min(z, a + BREAK_CAP)) for a, z in b["breaks"]]
        for w0, op in b[
            "open_craft"
        ].values():  # never finished: covers until run end only
            b["craft"].append((w0, w0, op))
        # Only collapse records within the same stream, tick and wall time.
        # Later observations of a frozen tick still carry coverage evidence.
        by_tick_and_time = {}
        for sample in b["samples"]:
            key = (*sample[:2], sample[5])
            old = by_tick_and_time.get(key)
            if old is None or sample[5] > old[5]:
                by_tick_and_time[key] = sample
        b["samples"] = sorted(by_tick_and_time.values(), key=lambda s: (s[0], s[1], s[5]))
        b["events"].sort(key=lambda x: x[0])
    return bodies


def find_windows(samples, min_s, move):
    """Find non-contained anchored windows, flagging gaps and bounded work."""
    out, n = [], len(samples)
    unknown = [(a[1], b[1], a[0], b[0]) for a, b in zip(samples, samples[1:], strict=False)
               if b[1] - a[1] > SPARSE_WALL]
    checks = 0
    for i in range(n):
        j, p0 = i, samples[i][2]
        while j + 1 < n:
            a, b = samples[j], samples[j + 1]
            if b[1] - a[1] > SPARSE_WALL:
                break
            checks += 1
            if checks > 2_000_000:
                unknown.append((samples[i][1], samples[-1][1], samples[i][0], samples[-1][0]))
                return out, unknown
            if math.dist(p0, b[2]) >= move:
                break
            j += 1
        if samples[j][1] - samples[i][1] >= min_s and (not out or j > out[-1][1]):
            out.append((i, j))
        if j == n - 1:
            break
    return out, unknown


def choose_stream(samples, b):
    """Choose a single independently recorded stream with the fewest coverage gaps."""
    def rank(stream):
        if len(stream) < 2:
            return (float("inf"), 0)
        gaps = sum(z[1] - a[1] > SPARSE_WALL or z[1] < a[1]
                   for a, z in zip(stream, stream[1:], strict=False))
        for bound, sample, sign in ((b["ready_wall"], stream[0][1], 1),
                                    (b["end_wall"], stream[-1][1], -1)):
            if bound is not None and sign * (sample - bound) > SPARSE_WALL:
                gaps += 1
        return (gaps, -len(stream))

    ordinary = [s for s in samples if s[5] == 0]
    policy = [s for s in samples if s[5] == 1]
    if not policy:
        return ordinary, "observation"
    if not ordinary:
        return policy, "policy"
    if rank(policy)[0] <= rank(ordinary)[0]:
        return policy, "policy"
    return ordinary, "observation"


def merge(iv):
    res = []
    for a, z in sorted(iv):
        if res and a <= res[-1][1]:
            res[-1][1] = max(res[-1][1], z)
        else:
            res.append([a, z])
    return res


def classify(b, w0, w1):
    ev = (
        [(a, z, "break") for a, z in b["breaks"]]
        + [(w - PLACE_PAD, w + PLACE_PAD, "place") for w in b["places"]]
        + [(a, max(z, a), "craft") for a, z, _ in b["craft"]]
        + [(a, z, op) for a, z, op in b["actions"]]
    )
    ev = [(max(a, w0), min(z, w1), k) for a, z, k in ev if z > w0 and a < w1]
    cov = merge([(a, z) for a, z, _ in ev])
    gaps, cur = [], w0
    for a, z in cov:
        gaps.append(a - cur)
        cur = z
    gaps.append(w1 - cur)
    return max(gaps), sum(z - a for a, z in cov), Counter(k for *_, k in ev)


def analyse(path: Path, min_s, move):
    bodies, res = load(path), []
    for bid, b in bodies.items():
        s = [
            x
            for x in b["samples"]
            if (b["ready"] is None or x[0] >= b["ready"])
            and (b["end"] is None or x[0] <= b["end"])
        ]
        s, stream = choose_stream(s, b)
        reasons = []
        if b["ready"] is None or b["end"] is None:
            reasons.append("active interval boundaries unavailable")
        if not b["active_request"]:
            reasons.append("no active work observed")
        if len(s) < 2:
            res.append(dict(body=bid, kind="META", samples=len(s), unknown=[],
                            incomplete=reasons + ["fewer than two usable samples"],
                            median_step=None, hiccups=0, tick_span=None))
            continue
        t0, ws, unknown = s[0][1], *find_windows(s, min_s, move)
        if b["ready"] is not None and s[0][0] - b["ready"] > SPARSE_TICKS:
            reasons.append("unobserved start of active interval")
        if b["end"] is not None and b["end"] - s[-1][0] > SPARSE_TICKS:
            reasons.append("unobserved end of active interval")
        if b["ready_wall"] is not None and s[0][1] - b["ready_wall"] > SPARSE_WALL:
            reasons.append("unobserved time after active start")
        if b["end_wall"] is not None and b["end_wall"] - s[-1][1] > SPARSE_WALL:
            reasons.append("unobserved time before active end")
        if any(a[1] > z[1] for a, z in zip(s, s[1:], strict=False)):
            reasons.append("samples received out of tick order")
        if unknown:
            reasons.append("sparse sample gaps")
        wall = sorted(x[1] for x in s)
        steps = sorted(y - x for x, y in zip(wall, wall[1:], strict=False))
        ev_w = [e[0] for e in b["events"]]
        for i, j in ws:
            a, z = s[i], s[j]
            unj, active, ev = classify(b, a[1], z[1])
            lo, hi = bisect_left(ev_w, a[1]), bisect_right(ev_w, z[1])
            inside, dist = (
                b["events"][lo:hi],
                [e for e in b["events"] if e[2] not in NOISE],
            )
            dlo, dhi = (
                bisect_left([e[0] for e in dist], a[1]),
                bisect_right([e[0] for e in dist], z[1]),
            )
            din = dist[dlo:dhi]
            res.append(
                dict(
                    body=bid,
                    kind="VIOLATION" if unj >= min_s else "EXEMPT",
                    start=a[1] - t0,
                    end=z[1] - t0,
                    wall_s=z[1] - a[1],
                    unjustified_s=unj,
                    active_work_s=active,
                    ticks=[a[0], z[0]],
                    pos=[round(c, 2) for c in a[2]],
                    evidence=dict(ev),
                    followers=dict(route=a[3], step=a[4]),
                    counts={
                        k: v
                        for k, v in Counter(e[2] for e in inside).items()
                        if k in COUNTED
                    },
                    last_op=next(
                        (
                            (e[2], e[3])
                            for e in reversed(b["events"][:lo])
                            if e[2].startswith("req:") and e[2] not in NOISE
                        ),
                        None,
                    ),
                    before=[
                        (e[0] - t0, e[1], e[2], e[3])
                        for e in dist[max(0, dlo - 3) : dlo]
                    ],
                    inside=[
                        (e[0] - t0, e[1], e[2], e[3])
                        for e in (din if len(din) <= 6 else din[:3] + din[-3:])
                    ],
                    after=[(e[0] - t0, e[1], e[2], e[3]) for e in dist[dhi : dhi + 3]],
                )
            )
        res_meta = dict(
            median_step=(wall[-1] - wall[0]) / (len(wall) - 1),
            unknown=unknown,
            incomplete=reasons,
            hiccups=sum(1 for x in steps if x > 1.0),
            observed_wall_s=wall[-1] - wall[0],
            stream=stream,
        )
        res.append(
            dict(
                body=bid,
                kind="META",
                **res_meta,
                samples=len(s),
                tick_span=[s[0][0], s[-1][0]],
            )
        )
    return res


def show(r):
    def fmt(e):
        return f"      {e[0]:8.2f}s t{e[1]:<6} {e[2]} {e[3]}"

    print(
        f"  {r['kind']:9} {r['start']:8.2f}s..{r['end']:8.2f}s  wall={r['wall_s']:.1f}s active_work={r['active_work_s']:.1f}s max_unexplained={r['unjustified_s']:.1f}s "
        f"ticks={r['ticks'][0]}..{r['ticks'][1]} ({r['ticks'][1] - r['ticks'][0]}t) pos={r['pos']}"
    )
    print(
        f"    followers={r['followers']} evidence={r['evidence']} counts={r['counts']} last_op={r['last_op']}"
    )
    for tag in ("before", "inside", "after"):
        print(f"    {tag}:")
        for e in r[tag]:
            print(fmt(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--min-seconds", type=float, default=5.0)
    ap.add_argument("--move", type=float, default=0.5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    bad, inconclusive, dump = False, False, {}
    for run in a.runs:
        f = next(
            (
                run / n
                for n in ("events.jsonl", "events.jsonl.gz")
                if (run / n).exists()
            ),
            None,
        )
        if f is None:
            print(f"RUN {run.name}: no events.jsonl[.gz]", file=sys.stderr)
            inconclusive = True
            continue
        res = analyse(f, a.min_seconds, a.move)
        wins = [r for r in res if r["kind"] != "META"]
        meta = [r for r in res if r["kind"] == "META"]
        dump[run.name] = res
        if not meta:
            print(f"RUN {run.name}: INCONCLUSIVE (fewer than two usable samples)",
                  file=sys.stderr)
            inconclusive = True
            continue
        for m in meta:
            if m["incomplete"]:
                inconclusive = True
                print(f"RUN {run.name} body {m['body']}: INCONCLUSIVE "
                      f"({', '.join(m['incomplete'])})", file=sys.stderr)
        v = [r for r in wins if r["kind"] == "VIOLATION"]
        bad |= bool(v)
        if not a.json:
            for m in meta:
                if m["median_step"] is None:
                    continue
                print(
                    f"{run.name} body {m['body']}: {m['samples']} samples, ticks {m['tick_span']}, mean step "
                    f"{m['median_step']:.3f}s, {m['hiccups']} steps >1s, "
                    f"observed active span {m['observed_wall_s']:.2f}s, "
                    f"stream={m['stream']}"
                )
                for u in m["unknown"]:
                    print(
                        f"  UNKNOWN (sparse samples) wall {u[0]:.1f}..{u[1]:.1f} ticks {u[2]}..{u[3]}"
                    )
            for r in wins:
                show(r)
        res_s = max((m["median_step"] for m in meta if m["median_step"] is not None),
                    default=float("nan"))
        unk = sum(len(m["unknown"]) for m in meta)
        line = (
            f"RUN {run.name}: {len(v)} violations (max {max((r['unjustified_s'] for r in v), default=0):.1f} s), "
            f"{len(wins) - len(v)} exempt, sample resolution {res_s:.3f} s"
            + (f", {unk} UNKNOWN sparse gaps" if unk else "")
        )
        print(line, file=sys.stderr if a.json else sys.stdout)
    if a.json:
        print(json.dumps(dump, default=list))
    sys.exit(1 if bad else 2 if inconclusive else 0)


if __name__ == "__main__":
    main()
