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


def observation(tick, x):
    return {
        "body_id": "b",
        "monotonic_ns": int((T0 + tick / 20) * 1e9),
        "type": "observation",
        "payload": {"tick": tick, "body": {"position": [x, 64.0, 0.0]}},
    }


def run(tmp_path, events, min_s=5.0):
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events), encoding="utf8")
    return [r for r in ig.analyse(f, min_s, 0.5) if r["kind"] != "META"]


def still(a, b, x=0.0):
    return [sample(t, x) for t in range(a, b)]


def test_violation_detected(tmp_path):
    r = run(tmp_path, [ev("course_started", 0)] + still(0, 200))
    assert [w["kind"] for w in r] == ["VIOLATION"] and r[0]["unjustified_s"] > 9


def test_observations_alone_show_idle(tmp_path):
    r = run(tmp_path, [observation(t, 0) for t in range(110)])
    assert [w["kind"] for w in r] == ["VIOLATION"]
    assert r[0]["wall_s"] > 5


def test_mixed_samples_deduplicate_and_keep_sparse_gaps(tmp_path):
    f = tmp_path / "events.jsonl"
    events = [observation(0, 2), sample(0, 0), observation(1, 0),
              observation(200, 0), sample(200, 1)]  # a 10 s hole: cannot be judged
    f.write_text("\n".join(json.dumps(e) for e in events), encoding="utf8")
    body = ig.load(f)[(None, None, "b", 0)]
    assert [s[0] for s in body["samples"]] == [0, 0, 1, 200, 200]
    meta = next(r for r in ig.analyse(f, 5, 0.5) if r["kind"] == "META")
    assert len(meta["unknown"]) == 1


def test_overlapping_anchor_finds_idle_after_subthreshold_drift(tmp_path):
    events = [ev("course_started", 0)]
    for tick in range(181):
        x = 0 if tick < 80 else 0.49 if tick == 80 else 0.51
        events.append(observation(tick, x))
    result = run(tmp_path, events)
    assert any(r["kind"] == "VIOLATION" and r["start"] >= 4 for r in result)


def test_policy_and_observation_delivery_order_do_not_mingle(tmp_path):
    events = [ev("course_started", 0)]
    for tick in range(120):
        events.append(observation(tick, tick))
        policy = sample(tick, tick)
        policy["monotonic_ns"] += 25_000_000
        events.append(policy)
    events.append(ev("stopped", 119))
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(map(json.dumps, events)), encoding="utf8")
    meta = next(r for r in ig.analyse(f, 5, 0.5) if r["kind"] == "META")
    assert meta["stream"] == "policy"
    assert not meta["incomplete"]


def test_same_timestamp_different_stream_follower_states_sort(tmp_path):
    a = observation(0, 0)
    b = sample(0, 0)
    b["payload"]["data"]["route_follower"] = {"state": "idle"}
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(map(json.dumps, [a, b])), encoding="utf8")
    assert len(next(iter(ig.load(f).values()))["samples"]) == 2


def test_no_samples_cli_is_inconclusive(tmp_path, monkeypatch, capsys):
    import sys

    import pytest

    (tmp_path / "events.jsonl").write_text(json.dumps(ev("course_started", 0)))
    monkeypatch.setattr(sys, "argv", ["idle_gaps.py", str(tmp_path)])
    with pytest.raises(SystemExit) as result:
        ig.main()
    assert result.value.code == 2
    assert "INCONCLUSIVE" in capsys.readouterr().err


def test_reused_ticks_are_separate_per_episode_and_run(tmp_path):
    events = []
    for run_id, episode_id in (("r1", "e1"), ("r1", "e2"), ("r2", "e1")):
        for tick in (0, 1):
            e = observation(tick, len(events))
            e.update(run_id=run_id, episode_id=episode_id)
            events.append(e)
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(map(json.dumps, events)), encoding="utf8")
    assert sorted(len(b["samples"]) for b in ig.load(f).values()) == [2, 2, 2]


def test_server_launch_change_separates_reused_ticks(tmp_path):
    events = []
    for launch in ("one", "two"):
        e = ev("body_state", 0, {"state": "ready"})
        e["payload"]["launch_id"] = launch
        events.extend([e, observation(0, 0), observation(1, 0)])
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(map(json.dumps, events)), encoding="utf8")
    assert [len(b["samples"]) for b in ig.load(f).values()] == [2, 2]


def test_complete_ready_and_stop_cli_passes(tmp_path, monkeypatch):
    import sys

    import pytest

    events = [ev("body_state", 0, {"state": "ready"})]
    events += [{"body_id": "b", "monotonic_ns": int(T0 * 1e9),
                "type": "request", "payload": {"op": "follow_route"}}]
    events += [observation(t, t) for t in range(3)]
    events += [ev("stopped", 2)]
    (tmp_path / "events.jsonl").write_text("\n".join(map(json.dumps, events)))
    monkeypatch.setattr(sys, "argv", ["idle_gaps.py", str(tmp_path)])
    with pytest.raises(SystemExit) as result:
        ig.main()
    assert result.value.code == 0


def test_frozen_tick_with_sparse_wall_gap_is_inconclusive(
    tmp_path, monkeypatch, capsys
):
    import sys

    import pytest

    a, b, c = observation(0, 0), observation(0, 0), observation(1, 1)
    b["monotonic_ns"] = int((T0 + 9) * 1e9)
    c["monotonic_ns"] = int((T0 + 10) * 1e9)
    end = ev("stopped", 1)
    end["monotonic_ns"] = c["monotonic_ns"]
    events = [ev("course_started", 0), a, b, c, end]
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(map(json.dumps, events)), encoding="utf8")
    body = ig.load(f)[(None, None, "b", 0)]
    assert [s[0] for s in body["samples"]] == [0, 0, 1]
    monkeypatch.setattr(sys, "argv", ["idle_gaps.py", str(tmp_path)])
    with pytest.raises(SystemExit) as result:
        ig.main()
    assert result.value.code == 2
    assert "sparse sample gaps" in capsys.readouterr().err


def test_unconfirmed_stop_request_is_inconclusive(tmp_path, monkeypatch, capsys):
    import sys

    import pytest

    events = [ev("course_started", 0)] + [observation(t, t) for t in range(3)]
    events += [{"body_id": "b", "monotonic_ns": int((T0 + 0.1) * 1e9),
                "type": "request", "payload": {"op": "stop"}}]
    (tmp_path / "events.jsonl").write_text("\n".join(map(json.dumps, events)))
    monkeypatch.setattr(sys, "argv", ["idle_gaps.py", str(tmp_path)])
    with pytest.raises(SystemExit) as result:
        ig.main()
    assert result.value.code == 2
    assert "active interval boundaries unavailable" in capsys.readouterr().err


def test_incomplete_coverage_cli(tmp_path, monkeypatch, capsys):
    import sys

    import pytest

    for events in (
        [observation(0, 0), observation(1, 0)],
        [ev("body_state", 0, {"state": "ready"}), observation(0, 0),
         observation(1, 0), ev("stopped", 1)],
        [ev("course_started", 0), observation(0, 0), observation(100, 0),
         ev("stopped", 100)],
        [ev("course_started", 0), observation(20, 0), observation(21, 0),
         ev("stopped", 21)],
    ):
        (tmp_path / "events.jsonl").write_text("\n".join(map(json.dumps, events)))
        monkeypatch.setattr(sys, "argv", ["idle_gaps.py", str(tmp_path)])
        with pytest.raises(SystemExit) as result:
            ig.main()
        assert result.value.code == 2
        assert "INCONCLUSIVE" in capsys.readouterr().err


def test_completed_interactions_exempt_bounded_time(tmp_path):
    evs = [ev("course_started", 0)] + still(0, 200) + [
        ev("received", 10, {"op": "attack_entity", "request_sequence": 1}),
        ev("attack_entity_done", 110, {"request_sequence": 1}),
        ev("received", 110, {"op": "eat", "request_sequence": 2}),
        ev("eat_done", 150, {"request_sequence": 2}),
    ]
    result = run(tmp_path, evs)[0]
    assert result["kind"] == "EXEMPT"
    assert result["wall_s"] > 9
    assert result["evidence"] == {"attack_entity": 1, "eat": 1}


def test_incomplete_or_slow_interactions_do_not_hide_idle(tmp_path):
    evs = [ev("course_started", 0)] + still(0, 300) + [
        ev("received", 0, {"op": "attack_entity", "request_sequence": 1}),
        ev("attack_entity_done", 290, {"request_sequence": 1}),
        ev("received", 290, {"op": "eat", "request_sequence": 2}),
    ]
    result = run(tmp_path, evs)[0]
    assert result["kind"] == "VIOLATION"
    assert result["unjustified_s"] > 9


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


def test_a_short_sample_gap_does_not_make_the_run_inconclusive(tmp_path):
    # wading r10 (20261112T180101456799Z): one 1.2 s hole between two samples made
    # a 5 s run UNSURE. A hole shorter than the limit cannot hide a standstill of
    # the limit on its own: the body is taken as standing still across it.
    moving = [sample(t, t * 0.2) for t in range(0, 40)]
    after = [sample(t, t * 0.2) for t in range(70, 110)]  # 1.5 s hole, body moved
    f = tmp_path / "events.jsonl"
    events = [ev("course_started", 0)] + moving + after
    f.write_text("\n".join(json.dumps(e) for e in events), encoding="utf8")
    rows = ig.analyse(f, 5.0, 0.5)
    meta = next(r for r in rows if r["kind"] == "META")
    assert meta["unknown"] == [] and "sparse sample gaps" not in meta["incomplete"]
    assert [r for r in rows if r["kind"] == "VIOLATION"] == []


def test_a_standstill_across_a_short_sample_gap_is_still_a_violation(tmp_path):
    events = [ev("course_started", 0)] + still(0, 60) + still(90, 150)  # 7.5 s
    r = run(tmp_path, events)
    assert [w["kind"] for w in r] == ["VIOLATION"] and r[0]["unjustified_s"] > 7


def _smelt_note(tick, ticks=200, why="no_open_face_blocks_within_24_or_drops"):
    return {
        "body_id": "b",
        "monotonic_ns": int((T0 + tick / 20) * 1e9),
        "type": "gather_note",
        "payload": {
            "gather": "smelt_wait",
            "item": "minecraft:iron_ingot",
            "ticks": ticks,
            "why": why,
        },
    }


def test_corroborated_smelt_wait_is_exempt(tmp_path):
    evs = sorted(
        [ev("course_started", 0)]
        + still(0, 220)
        + [
            _smelt_note(5),
            ev("received", 205, {"op": "furnace", "request_sequence": 1}),
            ev("furnace_done", 206, {"request_sequence": 1}),
        ],
        key=lambda e: e["monotonic_ns"],
    )
    result = run(tmp_path, evs)[0]
    assert result["kind"] == "EXEMPT"
    assert result["evidence"].get("smelt_wait") == 1


def test_uncorroborated_or_missing_reason_smelt_wait_is_violation(tmp_path):
    # No furnace_done after smelt_wait: still a violation.
    evs = sorted(
        [ev("course_started", 0)] + still(0, 220) + [_smelt_note(5)],
        key=lambda e: e["monotonic_ns"],
    )
    assert [w["kind"] for w in run(tmp_path, evs)] == ["VIOLATION"]
    # Missing why reason: not exempt.
    evs2 = sorted(
        [ev("course_started", 0)]
        + still(0, 220)
        + [_smelt_note(5, why=""), ev("furnace_done", 206, {"request_sequence": 1})],
        key=lambda e: e["monotonic_ns"],
    )
    assert [w["kind"] for w in run(tmp_path, evs2)] == ["VIOLATION"]


def _drop_note(tick, ticks=40):
    return {
        "body_id": "b",
        "monotonic_ns": int((T0 + tick / 20) * 1e9),
        "type": "gather_note",
        "payload": {"gather": "drop_wait", "ticks": ticks},
    }


def _req(tick, op):
    return {
        "body_id": "b",
        "monotonic_ns": int((T0 + tick / 20) * 1e9),
        "type": "request",
        "payload": {"op": op, "sequence": tick},
    }


def test_drop_wait_is_work_and_last_op_is_request_in_flight(tmp_path):
    base = [ev("course_started", 0), _req(1, "kit"), _req(5, "break_block")]
    base += still(0, 240)
    # 12 s still; the drop wait at 1-3.5 s leaves 8.5 s (12 s without the note)
    r = run(tmp_path, base + [_drop_note(20)])[0]
    assert r["evidence"].get("drop_wait") == 1 and r["unjustified_s"] < 10
    # a later request does not relabel a gap that began before it
    r = run(tmp_path, base + [_req(200, "craft"), _drop_note(20)])[0]
    assert r["last_op"][0] == "req:break_block"
