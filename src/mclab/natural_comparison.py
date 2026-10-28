"""Paired natural-route results with explicit failures, invalids and missing trials."""

import json
import math
from collections import Counter, defaultdict
from statistics import median


def summarize(
    summaries, *, candidate="segmented_goal", reference="baritone", repetitions=3
):
    if type(repetitions) is not int or not 1 <= repetitions <= 3:
        raise ValueError("Invalid comparison repetitions")
    attempts, routes = {}, set()
    identity = None
    provenance_complete = True
    for summary in summaries:
        source = {
            key: summary.get(key)
            for key in (
                "mod_provenance",
                "segmented_source_sha256",
                "controller_sha256",
                "runner_sha256",
                "navigation_sha256",
                "mcb_awareness",
                "movement_policy",
                "memory_policy",
                "terrain_policy",
                "goal_radius",
                "tick_limit",
                "kit",
            )
        }
        provenance_complete = provenance_complete and all(
            v is not None for v in source.values()
        )
        encoded = json.dumps(source, sort_keys=True)
        if identity is not None and encoded != identity:
            raise ValueError("Comparison sources or resolved profiles differ")
        identity = encoded
        seed = summary["seed"]
        for route in summary["routes"]:
            routes.add((seed, route["route_id"]))
        for row in summary["attempts"]:
            if (
                type(row.get("mobs")) is not bool
                or type(row.get("repetition", 1)) is not int
                or row.get("status") not in {"success", "failed", "invalid"}
            ):
                raise ValueError("Invalid comparison attempt fields")
            key = (
                seed,
                row["route_id"],
                row["mobs"],
                row["entrant"],
                row.get("repetition", 1),
            )
            if key in attempts:
                raise ValueError(
                    "Duplicate comparison attempt; choose one frozen cohort"
                )
            if (seed, row["route_id"]) not in routes or not 1 <= key[-1] <= repetitions:
                raise ValueError("Attempt outside comparison manifest")
            if row.get("status") == "success" and (
                type(row.get("ticks")) is not int or row["ticks"] <= 0
            ):
                raise ValueError("Successful comparison requires positive game ticks")
            attempts[key] = row
    entrants = sorted({key[3] for key in attempts} | {candidate, reference})
    counts, ratios = {}, defaultdict(list)
    for entrant in entrants:
        counts[entrant] = {}
        for mobs, condition in ((False, "calm"), (True, "night")):
            # Night diagnostics can be a separately selected subset of routes.
            selected = (
                routes if not mobs else {(key[0], key[1]) for key in attempts if key[2]}
            )
            statuses = Counter()
            damage, deaths = 0.0, 0
            for seed, route in selected:
                for repetition in range(1, repetitions + 1):
                    row = attempts.get((seed, route, mobs, entrant, repetition))
                    statuses["missing" if row is None else row["status"]] += 1
                    if row is not None:
                        value = row.get("damage_taken", 0)
                        if (
                            type(value) not in (int, float)
                            or not math.isfinite(value)
                            or value < 0
                        ):
                            raise ValueError("Invalid damage telemetry")
                        damage += value
                        deaths += row.get("reason") == "death_or_disconnect"
            counts[entrant][condition] = {
                "expected": len(selected) * repetitions,
                "statuses": dict(statuses),
                "damage_taken": damage,
                "death_or_disconnect": deaths,
            }
    route_results = []
    for seed, route in sorted(routes):
        paired = []
        for repetition in range(1, repetitions + 1):
            a = attempts.get((seed, route, False, candidate, repetition))
            b = attempts.get((seed, route, False, reference, repetition))
            if a and b and a["status"] == b["status"] == "success":
                paired.append(a["ticks"] / b["ticks"])
        value = median(paired) if paired else None
        if value is not None:
            ratios["calm"].append(value)
        route_results.append(
            {
                "seed": seed,
                "route_id": route,
                "paired_successes": len(paired),
                "median_time_ratio": value,
            }
        )
    aggregate = median(ratios["calm"]) if ratios["calm"] else None
    complete = all(
        counts[e]["calm"]["expected"] == 30
        and not counts[e]["calm"]["statuses"].get("missing", 0)
        and not counts[e]["calm"]["statuses"].get("invalid", 0)
        for e in (candidate, reference)
    )
    target_met = (
        len(routes) == 10
        and len({r[0] for r in routes}) >= 3
        and repetitions == 3
        and complete
        and provenance_complete
        and counts[candidate]["calm"]["statuses"].get("success", 0) >= 27
        and len(ratios["calm"]) == 10
        and aggregate <= 2
    )
    return {
        "schema_version": 1,
        "development_only": True,
        "full_sprint_acceptance": False,
        "candidate": candidate,
        "reference": reference,
        "routes": route_results,
        "entrant_outcomes": counts,
        "median_paired_route_time_ratio": aggregate,
        "natural_target_met": target_met,
        "provenance_complete": provenance_complete,
        "limits": [
            "Aggregation does not establish frozen source, fresh seeds, equal "
            "awareness, restoration or capability qualification.",
            "death_or_disconnect retains the native combined reason; "
            "it is not a proven death count.",
        ],
    }
