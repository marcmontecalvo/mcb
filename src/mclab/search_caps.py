"""Find route plans that stopped at the planner's node cap.

    python -m mclab.search_caps <events.jsonl> [...]

A plan_finished event with status "incomplete" and expanded >= the request's
node_limit is a cap hit. Hitting the cap is only acceptable when the goal is far
away; a "near" hit (Manhattan distance <= NEAR_MANHATTAN) is a bug to investigate.
Exit code 1 when any near hit is found.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

NEAR_MANHATTAN = 64  # blocks; a cap hit at or under this distance is a violation
EDIT_KEYS = (
    "allow_place",
    "allow_bridge",
    "allow_pillar",
    "break_budget",
    "place_budget",
)


@dataclass(frozen=True)
class CapHit:
    plan_id: str
    seconds: float
    start: tuple
    goal: tuple
    straight: float
    manhattan: float
    expanded: int
    length: int
    edits: dict

    @property
    def near(self) -> bool:
        return self.manhattan <= NEAR_MANHATTAN

    def describe(self) -> str:
        e = ", ".join(f"{k}={v}" for k, v in self.edits.items())
        return (
            f"{self.plan_id} at {self.seconds:.1f} s: {list(self.start)} -> "
            f"{list(self.goal)}, {self.manhattan:g} Manhattan / {self.straight:.1f} "
            f"straight, expanded {self.expanded}, route length {self.length}, "
            f"edits [{e}] ({'near' if self.near else 'far'})"
        )


def cap_hits(events: Path) -> list[CapHit]:
    """Every plan that ended incomplete with expanded at its request's node cap."""
    requests: dict[str, dict] = {}
    by_seq: dict[int, dict] = {}
    accepted: dict[str, int] = {}
    hits: list[CapHit] = []
    t0: int | None = None
    with Path(events).open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"plan_route"' in line:
                rec = json.loads(line)
                p = rec.get("payload", {})
                if rec.get("type") == "request" and p.get("op") == "plan_route":
                    if "plan_id" in p:
                        requests[p["plan_id"]] = p
                    by_seq[p.get("sequence")] = p
            elif '"plan_accepted"' in line:
                d = json.loads(line)["payload"].get("data", {})
                accepted[d.get("plan_id")] = d.get("request_sequence")
            elif '"plan_finished"' in line:
                rec = json.loads(line)
                d = rec["payload"].get("data", {})
                if d.get("status") != "incomplete":
                    continue
                pid = d.get("plan_id")
                req = requests.get(pid) or by_seq.get(accepted.get(pid))
                if not req or d.get("expanded", 0) < req.get("node_limit", math.inf):
                    continue
                if t0 is None:
                    t0 = _first_ns(events)
                s, g = req["start"], req["goal"]
                diff = [a - b for a, b in zip(g, s, strict=True)]
                edits = req.get("edits") or {}
                hits.append(
                    CapHit(
                        pid,
                        (rec.get("monotonic_ns", t0) - t0) / 1e9,
                        tuple(s),
                        tuple(g),
                        math.dist(s, g),
                        sum(abs(x) for x in diff),
                        d["expanded"],
                        d.get("length", 0),
                        {k: edits[k] for k in EDIT_KEYS if k in edits}
                        | {"node_limit": req.get("node_limit")},
                    )
                )
    return hits


def _first_ns(events: Path) -> int:
    with Path(events).open(encoding="utf-8", errors="replace") as f:
        return json.loads(f.readline()).get("monotonic_ns", 0)


def verdict(hits: list[CapHit]) -> tuple[str, list[str]]:
    """(``NONE``, ``VIOLATION n near cap hits`` or ``far only``, one line per hit)."""
    near = [h for h in hits if h.near]
    detail = [h.describe() for h in sorted(hits, key=lambda h: not h.near)]
    if near:
        return f"VIOLATION {len(near)} near cap hits", detail
    if hits:
        return f"NONE ({len(hits)} far cap hits listed)", detail
    return "NONE", []


def main(argv: list[str] | None = None) -> int:
    paths = (sys.argv[1:] if argv is None else argv) or ["-h"]
    if paths[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    code = 0
    for p in paths:
        text, detail = verdict(cap_hits(Path(p)))
        print(f"{p}: search cap: {text}")
        for ln in detail:
            print(f"    {ln}")
        code |= text.startswith("VIOLATION")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
