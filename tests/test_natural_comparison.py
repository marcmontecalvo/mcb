from copy import deepcopy

import pytest

from mclab.natural_comparison import summarize


def cohort():
    summaries = []
    for seed in range(3):
        routes = [{"route_id": f"R{i + 1}"} for i in range(4 if seed == 0 else 3)]
        rows = []
        for route in routes:
            for repetition in range(1, 4):
                for entrant, ticks in (
                    ("segmented_goal", 150),
                    ("baritone", 100),
                    ("v8_goal", 300),
                ):
                    rows.append(
                        {
                            "route_id": route["route_id"],
                            "entrant": entrant,
                            "mobs": False,
                            "repetition": repetition,
                            "status": "success",
                            "ticks": ticks,
                        }
                    )
        summaries.append(
            {
                "seed": seed,
                "routes": routes,
                "attempts": rows,
                **{
                    key: "test-frozen-identity"
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
                },
            }
        )
    return summaries


def test_complete_paired_scores_and_three_failures_remain_in_denominator():
    data = cohort()
    for row in data[0]["attempts"]:
        if (
            row["entrant"] == "segmented_goal"
            and row["repetition"] == 3
            and row["route_id"] != "R4"
        ):
            row.update(status="failed", reason="tick_limit", ticks=6000)
    result = summarize(data)
    assert result["natural_target_met"]
    assert result["median_paired_route_time_ratio"] == 1.5
    assert result["entrant_outcomes"]["segmented_goal"]["calm"] == {
        "expected": 30,
        "statuses": {"success": 27, "failed": 3},
        "damage_taken": 0,
        "death_or_disconnect": 0,
    }
    assert not result["full_sprint_acceptance"]


@pytest.mark.parametrize("problem", ["missing", "invalid", "slow", "four_failures"])
def test_incomplete_or_missed_targets_cannot_pass(problem):
    data = cohort()
    if problem == "missing":
        data[0]["attempts"].pop()
        # Missing v8 is reported separately, not a candidate/reference criterion.
        data[0]["attempts"].pop()
    elif problem == "invalid":
        data[0]["attempts"][0]["status"] = "invalid"
    elif problem == "slow":
        for summary in data:
            for row in summary["attempts"]:
                if row["entrant"] == "segmented_goal":
                    row["ticks"] = 201
    else:
        candidates = [
            r for r in data[0]["attempts"] if r["entrant"] == "segmented_goal"
        ]
        for row in candidates[:4]:
            row["status"] = "failed"
    assert not summarize(data)["natural_target_met"]


def test_duplicate_cohorts_and_forged_success_timing_rejected():
    data = cohort()
    with pytest.raises(ValueError, match="Duplicate"):
        summarize([*data, deepcopy(data[0])])
    data[0]["attempts"][0]["ticks"] = 0
    with pytest.raises(ValueError, match="positive"):
        summarize(data)


def test_mixed_sources_or_missing_provenance_cannot_meet_target():
    data = cohort()
    data[0]["controller_sha256"] = "changed"
    with pytest.raises(ValueError, match="sources"):
        summarize(data)
    data = cohort()
    for summary in data:
        summary.pop("controller_sha256")
    result = summarize(data)
    assert not result["provenance_complete"]
    assert not result["natural_target_met"]
