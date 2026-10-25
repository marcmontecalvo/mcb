import json
from collections import Counter

import pytest

from mclab.paths import digest
from mclab.screen_fixtures import CATEGORIES, build_fixture
from mclab.tactical import summary


def test_fixture_has_48_deterministic_distinct_synthetic_states():
    fixture = build_fixture()
    assert fixture == build_fixture()
    assert len(fixture["states"]) == 48
    assert Counter(state["category"] for state in fixture["states"]) == {
        category: 8 for category in CATEGORIES
    }
    assert len({digest(summary(s["observation"])) for s in fixture["states"]}) == 48
    assert fixture["synthetic"]
    assert not fixture["gameplay_qualified"]
    assert not fixture["training_ready"]
    assert not fixture["scored"]


def test_progress_labels_use_independent_geometry_facts():
    for state in build_fixture()["states"]:
        visible = summary(state["observation"])
        facts = state["geometry_facts"]
        if "forward_safe" in facts:
            assert visible["forward_safe"] == facts["forward_safe"], state["state_id"]
        if state["category"] in {
            "exploration",
            "obstacle_or_ledge",
            "unknown_or_stale",
        }:
            assert visible["visible_logs"] == []
            assert visible["visible_drops"] == []
        if state["category"] == "mining":
            assert visible["ray"]["position"] == facts["target"]
            assert facts["eye_to_center_distance"] < 4.5
        assert set(state["valid_actions"]) <= set(build_fixture()["candidates"])
        assert state["review_status"] == "pending_independent_geometry_review"


def test_labels_and_evaluator_metadata_never_enter_model_visible_state():
    for state in build_fixture()["states"]:
        observation = state["observation"]
        observation.update(
            expected_action="forbidden",
            evaluator_target=[1, 2, 3],
            valid_actions=state["valid_actions"],
            rationale=state["rationale"],
        )
        visible = json.dumps(summary(observation))
        for key in (
            "expected_action",
            "evaluator_target",
            "valid_actions",
            "rationale",
        ):
            assert key not in visible


def test_fixture_values_are_not_shared_between_states_or_builds():
    first = build_fixture()
    second = build_fixture()
    first["states"][0]["observation"]["visible_blocks"][0]["position"][0] = -999
    first["candidates"]["stop"] = "changed"
    assert second == build_fixture()
    assert first["states"][1] == second["states"][1]


def test_instruction_override_is_explicit_and_validated():
    assert build_fixture("frozen instructions")["instructions"] == "frozen instructions"
    for invalid in ("", 1, [], {}):
        with pytest.raises(ValueError, match="Instructions"):
            build_fixture(invalid)
