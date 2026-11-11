"""Synthetic-event tests for scripts/analysis/idle_gaps.py."""

import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "idle_gaps",
    Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "idle_gaps.py",
)
ig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ig)

T0 = 1000.0


def ev(typ, tick, data=None):
    return {
        "body_id": "b",
        "monotonic_ns": int((T0 + tick / 20) * 1e9),
        "type": "server_event",
        "payload": {"type": typ, "tick": tick, "data": data or {}},
    }


def sample(tick, x):
    return ev("policy_sample", tick, {"body": {"position": [x, 64.0, 0.0]}})


def run(tmp_path, events, min_s=5.0):
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events), encoding="utf8")
    return [r for r in ig.analyse(f, min_s, 0.5) if r["kind"] != "META"]


def still(a, b, x=0.0):
    return [sample(t, x) for t in range(a, b)]


def test_violation_detected(tmp_path):
    r = run(tmp_path, [ev("course_started", 0)] + still(0, 200))
    assert [w["kind"] for w in r] == ["VIOLATION"] and r[0]["unjustified_s"] > 9


def test_break_in_progress_exempt(tmp_path):
    evs = (
        [ev("course_started", 0)]
        + still(0, 200)
        + [
            ev(
                "dig_packet",
                5,
                {"action": "START_DESTROY_BLOCK", "position": [1, 1, 1]},
            ),
            ev("block_broken", 195, {"position": [1, 1, 1]}),
        ]
    )
    assert [w["kind"] for w in run(tmp_path, evs)] == ["EXEMPT"]


def test_movement_resets(tmp_path):
    evs = [ev("course_started", 0)] + [sample(t, t * 0.05) for t in range(0, 300)]
    assert run(tmp_path, evs) == []


def test_prespawn_and_after_stop_ignored(tmp_path):
    evs = (
        still(0, 200)
        + [ev("course_started", 200)]
        + [sample(t, t * 0.05) for t in range(200, 300)]
        + [ev("stopped", 300)]
        + still(300, 600, 50.0)
    )
    assert run(tmp_path, evs) == []
