"""Per-attempt facts for classifying segmented MCB natural-run failures (Phase 0).

Usage: python scripts/classify-natural-attempts.py results/natural/<stamp>
Prints one JSON line per segmented attempt. The category itself is assigned by a
reviewer from these facts; the `hint` field is only a first guess.
"""

import json
import math
import sys
from collections import Counter
from pathlib import Path

PROGRESS_EPSILON = 1.0  # blocks of goal-distance improvement that count as progress


def facts(folder: Path, attempt: dict, route: dict) -> dict:
    goal = route["goal"]
    modes, step_states = Counter(), Counter()
    best, best_tick, longest_stall, first_tick, last_tick = (
        math.inf,
        None,
        0,
        None,
        None,
    )
    planned = 0
    for line in (folder / "events.jsonl").open(encoding="utf-8"):
        if '"navigation_tick"' in line[:400]:
            p = json.loads(line)["payload"]
            modes[p["mode"]] += 1
        elif '"terrain_step_planned"' in line[:400]:
            planned += 1
        elif '"terrain_step_finished"' in line[:400]:
            p = json.loads(line)["payload"]
            step_states[
                f"{p['state']}:{p['reason']}" if p["reason"] else p["state"]
            ] += 1
        elif '"policy_observation"' in line[:400]:
            p = json.loads(line)["payload"]
            position = p.get("body", {}).get("position")
            if not position or p.get("state") != "running":
                continue
            tick = p["tick"]
            first_tick = tick if first_tick is None else first_tick
            last_tick = tick
            distance = math.dist(position[::2], goal[::2])
            if distance < best - PROGRESS_EPSILON:
                if best_tick is not None:
                    longest_stall = max(longest_stall, tick - best_tick)
                best, best_tick = distance, tick
    if best_tick is not None and last_tick is not None:
        longest_stall = max(longest_stall, last_tick - best_tick)
    navigation = attempt.get("navigation") or {}
    length = math.dist(route["start"][::2], goal[::2])
    result = {
        "route": attempt["route_id"],
        "condition": "night" if attempt["mobs"] else "calm",
        "status": attempt["status"],
        "reason": attempt.get("reason"),
        "error": attempt.get("controller_error") or attempt.get("error"),
        "ticks": attempt.get("ticks"),
        "route_blocks": round(length),
        "progress_blocks": round(length - attempt.get("min_goal_distance", length)),
        "longest_no_progress_ticks": longest_stall,
        "nav_ticks_by_mode": dict(modes),
        "terrain_steps_planned": planned,
        "terrain_step_results": dict(step_states),
        "breaks": navigation.get("breaks_confirmed"),
        "places": navigation.get("places_confirmed"),
        "final_mode": navigation.get("mode"),
        "final_reason": navigation.get("reason"),
        "damage": attempt.get("damage_taken"),
        "died": attempt.get("died"),
        "peak_hostiles_within_16": attempt.get("peak_hostiles_within_16"),
    }
    result["hint"] = hint(result)
    return result


def hint(r: dict) -> str:
    if r["status"] == "success":
        return "success"
    if r["status"] == "invalid":
        return "other (harness/timing)"
    if r["died"] or r["reason"] == "death_or_disconnect":
        return "survival"
    if r["places"] is not None and r["places"] >= 8 or (r["breaks"] or 0) >= 8:
        return "edit-vs-detour misjudgment (budget exhausted)"
    planning = r["nav_ticks_by_mode"].get("planning", 0)
    if planning > 0.5 * sum(r["nav_ticks_by_mode"].values()):
        return "planner blind spot or stall (mostly planning)"
    return "motor stall/stop time"


def main(folder: str) -> None:
    root = Path(folder)
    summary = json.loads((root / "summary.json").read_text())
    routes = {r["route_id"]: r for r in summary["routes"]}
    for attempt in summary["attempts"]:
        if attempt["entrant"] != "segmented_goal":
            continue
        name = (
            f"{attempt['route_id']}-{'mobs' if attempt['mobs'] else 'calm'}-"
            f"segmented_goal-{attempt['repetition']}"
        )
        if not (root / name / "events.jsonl").is_file():
            print(json.dumps({"route": attempt["route_id"], "missing": name}))
            continue
        print(json.dumps(facts(root / name, attempt, routes[attempt["route_id"]])))


if __name__ == "__main__":
    main(sys.argv[1])
