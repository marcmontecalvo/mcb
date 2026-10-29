"""Comparisons retain outcome, measurement coverage and provenance distinctions."""

import pytest

from mclab.dashboard_metrics import compare_data, elapsed_ticks


def test_speed_uses_successes_but_damage_and_block_totals_include_failures():
    rows = [
        {
            "status": "success",
            "ticks": 100,
            "damage_taken": 2,
            "blocks_broken": 3,
            "navigation": {"places_confirmed": 4},
            "kit_blocks_spent_adjusted": 5,
        },
        {
            "status": "failed",
            "ticks": 20,
            "damage_taken": 10,
            "blocks_broken": 7,
            "navigation": {"places_confirmed": 2},
            "kit_blocks_spent_adjusted": 3,
            "reason": "tick_limit",
        },
        {
            "status": "success",
            "start_tick": 50,
            "end_tick": 350,
            "damage_taken": 0,
            "blocks_broken": 0,
        },
    ]
    data = compare_data({}, rows)
    m = data["metrics"]
    assert m["success_ticks"] == {"value": 200, "samples": 2, "out_of": 3}
    assert m["ticks"]["value"] == 420
    assert m["damage"]["value"] == 12
    assert m["damage_mean"]["value"] == 4
    assert m["damage_worst"]["value"] == 10
    assert m["broken"]["value"] == 10
    assert m["confirmed_places"] == {"value": 6, "samples": 2, "out_of": 3}
    assert m["blocks_spent"]["value"] == 8
    assert m["placed"]["value"] is None
    assert data["failure_reasons"] == ["Tick limit (1)"]


def test_missing_invalid_and_sentinel_data_are_not_zero_or_estimated_placements():
    data = compare_data(
        {},
        [
            {
                "status": "failed",
                "ticks": -1,
                "start_tick": -1,
                "end_tick": 40,
                "damage_taken": float("nan"),
                "kit_blocks_spent_adjusted": -1,
                "kit_blocks_used_estimate": -3,
                "reason": "death_or_disconnect",
            },
            {
                "status": "success",
                "ticks": 120,
                "damage_taken": 0,
                "kit_blocks_spent_adjusted": 9,
                "blocks_placed": 2,
            },
        ],
    )
    m = data["metrics"]
    assert m["damage"] == {"value": 0, "samples": 1, "out_of": 2}
    assert m["blocks_spent"]["value"] == 9
    assert m["placed"]["value"] == 2
    assert m["confirmed_places"]["value"] is None
    assert m["died"]["value"] is None
    assert m["kit_estimate"]["value"] is None
    assert data["attempts"][0]["ticks"] is None


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"start_tick": 30, "end_tick": 130}, 100),
        ({"start_tick": -1, "end_tick": 100}, None),
        ({"start_tick": 100, "end_tick": 30}, None),
        ({"ticks": True}, None),
        ({"ticks": float("inf")}, None),
        ({"ticks": 0}, 0),
    ],
)
def test_elapsed_time_rejects_invalid_samples(row, expected):
    assert elapsed_ticks(row) == expected


def test_setup_values_are_readable_and_only_declared_deaths_count():
    data = compare_data(
        {
            "entrant": "segmented_goal",
            "seed": 1337,
            "control_profiles": {"segmented_goal": "dev-pipelined-v1"},
            "mcb_awareness": {"preset": "chunk", "horizontal_radius_blocks": 64},
            "movement_policy": {"allow_swim": True, "allow_sprint": False},
        },
        [
            {
                "route_id": "R1",
                "repetition": 1,
                "mobs": False,
                "status": "failed",
                "died": True,
                "disconnected": False,
                "minimum_air": 0,
            },
            {
                "route_id": "R1",
                "repetition": 1,
                "mobs": True,
                "status": "success",
                "died": False,
                "disconnected": False,
            },
        ],
    )
    assert data["settings"]["Control profile"] == "dev-pipelined-v1"
    assert data["settings"]["Movement / Allow swim"] == "Yes"
    assert data["settings"]["Movement / Allow sprint"] == "No"
    assert data["metrics"]["died"]["value"] == 1
    assert data["metrics"]["disconnected"]["value"] == 0
    assert data["metrics"]["minimum_air"]["value"] == 0
    assert data["attempts"][0]["key"] != data["attempts"][1]["key"]
    assert all(not isinstance(v, (dict, list)) for v in data["settings"].values())


def test_single_outcome_diagnostic_reuses_native_and_controller_metrics():
    from mclab.dashboard_runs import outcomes

    value = {
        "outcome": {
            "status": "success",
            "ticks": 106,
            "damage_taken": 0,
            "blocks_broken": 2,
        },
        "controller": {"places_confirmed": 3},
    }
    data = compare_data(value, outcomes(value))
    assert data["metrics"]["success_ticks"]["value"] == 106
    assert data["metrics"]["confirmed_places"]["value"] == 3
    assert data["metrics"]["broken"]["value"] == 2
    assert not data["tasks_known"]
    assert "navigation" not in value["outcome"]


def test_shared_natural_summary_does_not_assign_mcb_settings_to_baritone():
    data = compare_data(
        {
            "entrant": "baritone",
            "movement_policy": {"allow_swim": True},
            "terrain_policy": {"allow_place": False},
            "memory_policy": {"memory_mode": "persistent"},
            "mcb_awareness": {"preset": "chunk"},
            "baritone_awareness": "Native client chunks",
        },
        [],
    )
    assert data["settings"] == {"Awareness": "Native client chunks"}


def test_expected_attempts_and_task_identity():
    value = {"course_id": "C2", "seeds": [101, 102], "specs": [{"target": [0, 0, 12]}]}
    rows = [{"seed": 101, "status": "success", "ticks": 100}]
    first = compare_data(value, rows)
    assert first["expected"] == 2
    assert (
        compare_data({"conditions": {"mobs_on": "night", "mobs_off": "calm"}}, rows)[
            "expected"
        ]
        is None
    )
    assert (
        first["task_key"]
        == compare_data({**value, "controller": "other"}, rows)["task_key"]
    )
    assert (
        first["task_key"]
        != compare_data({**value, "specs": [{"target": [12, 0, 0]}]}, rows)["task_key"]
    )
