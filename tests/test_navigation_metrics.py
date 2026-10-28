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


def test_processing_trace_only_aggregates_declared_high_resolution_samples():
    summary = {"status": "error", "attempts": []}
    result = summarize(
        summary,
        [
            {"type": "control_processing", "payload": {"before_send_ms": 999}},
            {
                "type": "control_processing",
                "payload": {
                    "clock": "perf_counter_ns",
                    "tick": 10,
                    "before_send_ms": 12,
                    "ack_wait_ms": 2000,
                },
            },
            {
                "type": "navigation_tick",
                "payload": {"tick": 10, "motor_ms": 7, "motor_cpu_ms": 2},
            },
        ],
    )
    assert result["control_processing"]["before_send_ms"]["p95"] == 12
    assert result["control_processing"]["ack_wait_ms"]["max"] == 2000
    assert result["motor_cpu_ms"]["p95"] == 2
    assert "tick" not in result["control_processing"]


def test_controller_cancel_does_not_hide_timing_failure_in_historical_logs():
    result = summarize(
        {"status": "error", "attempts": [{"status": "failed"}]},
        [
            {
                "type": "controller_error",
                "episode_id": "hill-one",
                "payload": {
                    "category": "decision",
                    "error": "Controller action rejected: stale_queued_request",
                },
            }
        ],
    )
    assert result["controller_error_attempts"] == result["timing_error_attempts"] == 1


def test_expected_refusals_are_distinct_from_goal_arrivals():
    result = summarize(
        {
            "status": "passed",
            "expected_outcome": "refusal",
            "attempts": [
                {"status": "failed", "refusal_verified": True},
                {"status": "failed", "refusal_verified": False},
            ],
        },
        [],
    )
    assert result["expected_outcome"] == "refusal"
    assert result["configured_refusals_verified"] == 1
    assert result["routes_reached"] == 0
