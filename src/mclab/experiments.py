"""Bounded development planning and frozen observation screening exports."""

import hashlib
import json
import random
from pathlib import Path

from mclab.courses import generate
from mclab.paths import ROOT, config, digest, read_json

PRIMITIVES = {
    "stop": "Release all controls",
    "forward": "Walk forward for at most five ticks",
    "left": "Strafe left for at most five ticks",
    "right": "Strafe right for at most five ticks",
    "turn_left": "Turn left by at most 15 degrees",
    "turn_right": "Turn right by at most 15 degrees",
    "jump_forward": "Jump while moving forward for at most five ticks",
    "sneak_forward": "Sneak forward for at most five ticks",
    "mine": "Hold mining at the current crosshair for at most five ticks",
}


def layout_hash(spec: dict) -> str:
    # Changing only the seed or metadata does not create an unseen layout.
    return digest(
        {
            k: spec[k]
            for k in (
                "course_id",
                "geometry",
                "spawn",
                "yaw",
                "pitch",
                "target",
                "initial_inventory",
            )
        }
    )


def freeze(path: Path, value: dict) -> dict:
    if path.exists():
        if read_json(path) != value:
            raise ValueError("Frozen artifact already exists with different content")
        return value
    # Exclusive creation avoids replacing an existing experimental definition.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, sort_keys=True, indent=2, allow_nan=False)
        output.write("\n")
    return value


def development_plan(destination: Path | None = None) -> dict:
    profiles = read_json(ROOT / "courses/profiles.json")
    planned = []
    for course in ("C2", "C3", "C4", "C5"):
        if course not in profiles["courses"]:
            planned.append({"course_id": course, "status": "generator_missing"})
            continue
        unique = {}
        for seed in range(2000):
            spec = generate(course, seed, config()["origin"])
            key = layout_hash(spec)
            unique.setdefault(
                key, {"seed": seed, "layout_hash": key, "spec_hash": spec["spec_hash"]}
            )
            if len(unique) >= 25:
                break
        variants = list(unique.values())
        planned.append(
            {
                "course_id": course,
                "status": "ready_for_future_qualification"
                if len(variants) >= 25
                else "insufficient_layout_diversity",
                "unique_layouts_found": len(variants),
                "smoke": variants[:5],
                "paired_development": variants[5:25],
                "tick_limit": profiles["courses"][course]["tick_limit"],
                "qualified": False,
            }
        )
    value = {
        "schema_version": 1,
        "generator_hash": digest(profiles),
        "courses": planned,
        "runs_started": 0,
        "holdouts": {
            "status": "not_frozen",
            "reason": "Need sufficient layout diversity and a qualified runner "
            "before freezing fresh unseen layouts",
        },
        "training": {
            "status": "blocked_on_synchronized_demonstrations",
            "collection_limit": 50,
            "training_iterations": 2,
            "split": "layout_disjoint_not_adjacent_frames",
        },
        "provider_request_limit_per_episode": 60,
        "provider_cost_ceiling": "must_be_explicitly_configured_before_remote_runs",
    }
    return freeze(
        destination or ROOT / "results/experiments/development_plan.json", value
    )


def export_screening(recording: Path, destination: Path, limit: int = 60) -> dict:
    """Export only actual permitted observations; labels await independent review."""
    if not 1 <= limit <= 60:
        raise ValueError("Screening export requires limit 1..60")
    rng = random.Random(421)
    states = []
    seen = set()
    with recording.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if event["type"] != "policy_observation":
                continue
            observation = event["payload"]
            # Allowlist prevents evaluator data entering decision backends.
            permitted = {
                k: observation[k]
                for k in (
                    "schema_version",
                    "sensor_profile",
                    "tick",
                    "body",
                    "visible_blocks",
                    "visible_entities",
                )
                if k in observation
            }
            key = digest(permitted)
            if key in seen:
                continue
            seen.add(key)
            original = list(PRIMITIVES)
            shuffled = original.copy()
            rng.shuffle(shuffled)
            states.append(
                {
                    "state_id": key,
                    "observation": permitted,
                    "candidate_orders": [original, list(reversed(original)), shuffled],
                    "valid_actions": None,
                    "review_status": "pending_geometry_or_human_review",
                }
            )
            if len(states) == limit:
                break
    if not states:
        raise ValueError("Recording has no policy observations")
    value = {
        "schema_version": 1,
        "source_recording_sha256": hashlib.sha256(recording.read_bytes()).hexdigest(),
        "candidates": PRIMITIVES,
        "states": states,
        "status": "unlabeled_development_export",
        "training_ready": False,
        "client_server_input_alignment": "unavailable",
    }
    freeze(destination, value)
    return {
        "status": value["status"],
        "states": len(states),
        "artifact": str(destination),
        "artifact_hash": digest(value),
    }
