from mclab.navigation_metrics import percentiles, summarize


def test_percentiles_match_controller_and_empty_samples_are_explicit():
    assert percentiles([])["p95"] is None
    assert percentiles(list(range(101)))["p95"] == 95


def test_native_refreshes_are_unique_and_failures_are_retained():
    sample = {
        "map_update": True,
        "awareness": {"coverage": {"sample_tick": 10, "sample_ms": 2}},
        "resources": {"average_mspt": 3},
    }
    event = {
        "type": "server_event",
        "payload": {"type": "policy_sample", "episode_id": "hill-one", "data": sample},
    }
    events = [
        event,
        event,
        {
            "type": "policy_observation",
            "payload": {
                "tick": 10,
                "terrain_decode_ms": 3,
                "terrain_decode_clock": "perf_counter_ns",
            },
        },
        {"type": "navigation_tick", "payload": {"tick": 10, "motor_ms": 7}},
        {"type": "discarded_stale_action", "payload": {}},
    ]
    summary = {
        "status": "error",
        "lab_restored": True,
        "attempts": [{"status": "success"}, {"status": "invalid"}],
    }
    result = summarize(summary, events)
    assert result["native_sample_ms"]["samples"] == 1
    assert result["motor_ms"]["p95"] == 7
    assert result["decode_plus_motor_ms"]["p95"] == 10
    assert result["invalid_attempts"] == 1
    assert result["discarded_stale_actions"] == 1
    assert result["cohort_status"] == "error"
    assert not result["full_sprint_acceptance"]
