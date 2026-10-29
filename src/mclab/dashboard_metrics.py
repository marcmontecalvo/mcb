"""Human-facing comparisons derived from original run summaries."""

import math
from collections import Counter
from statistics import median

from mclab.paths import digest


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def elapsed_ticks(row):
    ticks = row.get("ticks")
    if ticks is None:
        start, end = row.get("start_tick"), row.get("end_tick")
        if number(start) and number(end):
            ticks = end - start
    return ticks if number(ticks) else None


def human(value):
    if value is None:
        return "Not recorded"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).replace("_", " ").strip().capitalize()


def status(row):
    if "status" in row:
        return row["status"]
    if type(row.get("passed")) is bool:
        return "passed" if row["passed"] else "failed"
    return "unknown"


def measures(row):
    navigation = row.get("navigation") or {}
    return {
        "ticks": elapsed_ticks(row),
        "wall_seconds": row.get("wall_seconds"),
        "damage": row.get("damage_taken"),
        "broken": row.get("blocks_broken"),
        "placed": row.get("blocks_placed"),
        "confirmed_places": navigation.get("places_confirmed"),
        "confirmed_breaks": navigation.get("breaks_confirmed"),
        "blocks_spent": row.get("kit_blocks_spent_adjusted"),
        "kit_estimate": row.get("kit_blocks_used_estimate"),
        "died": int(row["died"]) if type(row.get("died")) is bool else None,
        "disconnected": int(row["disconnected"])
        if type(row.get("disconnected")) is bool
        else None,
        "minimum_air": row.get("minimum_air"),
        "water_ticks": row.get("water_ticks"),
        "hostile_ticks": row.get("hostile_exposure_ticks_within_16"),
        "peak_hostiles": row.get("peak_hostiles_within_16"),
        "goal_distance": row.get("min_goal_distance"),
    }


def settings(value, rows):
    """Flatten useful recorded settings into scalar table cells, never JSON blobs."""
    result = {}

    def add(label, item):
        if isinstance(item, dict):
            for key, child in item.items():
                add(label + " / " + human(key), child)
        elif isinstance(item, list):
            if all(type(v) in (str, int, float, bool) for v in item):
                result[label] = ", ".join(str(v) for v in item) or "None"
        elif item is not None:
            result[label] = human(item) if isinstance(item, bool) else str(item)

    for key, label in (
        ("seed", "World seed"),
        ("seeds", "Course seeds"),
        ("repetitions", "Repetitions"),
        ("tick_limit", "Time limit (ticks)"),
        ("course_tick_limit", "Time limit (ticks)"),
        ("sensor_profile", "Sensor profile"),
        ("timing_profile", "Timing profile"),
        ("server_view_distance_chunks", "View distance (chunks)"),
        ("movement_policy", "Movement"),
        ("terrain_policy", "Terrain editing"),
        ("memory_policy", "Memory"),
    ):
        if key in {"movement_policy", "terrain_policy", "memory_policy"} and value.get(
            "entrant"
        ) in {"baritone", "v8_goal"}:
            continue
        add(label, value.get(key))
    awareness = value.get("mcb_awareness")
    if value.get("entrant") == "baritone":
        add("Awareness", value.get("baritone_awareness"))
    elif isinstance(awareness, dict):
        add("Awareness", awareness.get("config", awareness))
    control = value.get("control_profiles", {})
    if isinstance(control, dict):
        add("Control profile", control.get(value.get("entrant")))
    conditions = sorted(
        {
            "Night with mobs" if r["mobs"] else "Calm"
            for r in rows
            if type(r.get("mobs")) is bool
        }
    )
    if conditions:
        result["Recorded conditions"] = ", ".join(conditions)
    routes = sorted({r["route_id"] for r in rows if r.get("route_id")})
    if routes:
        result["Routes"] = ", ".join(routes)
    if value.get("source_commit"):
        result["Source revision"] = str(value["source_commit"])[:12]
    provenance = value.get("mod_provenance") or {}
    if provenance.get("source_hash"):
        result["Installed mod revision"] = provenance["source_hash"][:12]
    return result


def compare_data(value, rows):
    samples = [measures(row) for row in rows]
    successful = [
        m for row, m in zip(rows, samples, strict=True) if status(row) == "success"
    ]
    counts = Counter(status(row) for row in rows)
    expected = len(value["seeds"]) if isinstance(value.get("seeds"), list) else None
    if expected is None:
        expected = value.get("requested_episodes", value.get("requested"))
    if type(expected) is not int or expected < 1:
        expected = None
    metrics = {}

    def record(key, values, operation=sum):
        valid = [v for v in values if number(v)]
        metrics[key] = {
            "value": operation(valid) if valid else None,
            "samples": len(valid),
            "out_of": len(rows),
        }

    for key in (
        "damage",
        "broken",
        "placed",
        "confirmed_places",
        "confirmed_breaks",
        "blocks_spent",
        "kit_estimate",
        "died",
        "disconnected",
        "water_ticks",
        "hostile_ticks",
        "ticks",
    ):
        record(key, [m[key] for m in samples])
    record("damage_mean", [m["damage"] for m in samples], lambda vs: sum(vs) / len(vs))
    record("damage_worst", [m["damage"] for m in samples], max)
    record(
        "success_ticks",
        [m["ticks"] for m in successful if number(m["ticks"]) and m["ticks"] > 0],
        median,
    )
    record("success_wall", [m["wall_seconds"] for m in successful], median)
    record("minimum_air", [m["minimum_air"] for m in samples], min)
    record("peak_hostiles", [m["peak_hostiles"] for m in samples], max)
    record("goal_distance", [m["goal_distance"] for m in samples], min)
    attempts = []
    seen = Counter()
    for index, (row, sample) in enumerate(zip(rows, samples, strict=True), 1):
        parts = []
        if row.get("route_id"):
            parts.extend(
                [
                    row["route_id"],
                    "night" if row.get("mobs") else "calm",
                    "repeat " + str(row.get("repetition", 1)),
                ]
            )
        elif "seed" in row:
            parts.append("Seed " + str(row["seed"]))
        elif row.get("name") or row.get("check"):
            parts.append(str(row.get("name", row.get("check"))))
        else:
            parts.append("Attempt " + str(index))
        label = " / ".join(parts)
        seen[label] += 1
        attempts.append(
            {
                "key": label + " / " + str(seen[label]),
                "label": label,
                "status": human(status(row)),
                "reason": human(row.get("reason", row.get("error"))),
                **{k: v if number(v) else None for k, v in sample.items()},
            }
        )
    reasons = Counter(
        human(r.get("reason", r.get("error")))
        for r in rows
        if status(r) not in {"success", "passed"}
    )
    return {
        "metrics": metrics,
        "attempts": attempts,
        "counts": dict(counts),
        "expected": expected,
        "failure_reasons": [f"{reason} ({n})" for reason, n in reasons.items()],
        "tasks_known": bool(
            value.get("specs") or value.get("routes") or value.get("course_spec_hash")
        ),
        "task_key": digest(
            {
                "course": value.get("course_id"),
                "seed": value.get("seed"),
                "seeds": value.get("seeds"),
                "layout": value.get("specs", value.get("routes")),
                "course_spec_hash": value.get("course_spec_hash"),
                "goal_radius": value.get("goal_radius"),
                "tick_limit": value.get("tick_limit", value.get("course_tick_limit")),
                "cases": [
                    {
                        k: r.get(k)
                        for k in (
                            "seed",
                            "route_id",
                            "mobs",
                            "repetition",
                            "kind",
                            "variant",
                        )
                    }
                    for r in rows
                ],
            }
        ),
        "settings": settings(value, rows),
    }
