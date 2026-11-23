"""Group targets: "get wood" means any log, whichever is closest (Nov 23, 2026: in
a spruce-only biome an oak_log gather swept 7 minutes and found nothing)."""

import pytest

from mclab.companion_jobs import load_vanilla_pack
from mclab.gather_plan import Gather, group_members, plan

LOGS = "#minecraft:logs"
M = "minecraft:"


@pytest.fixture(scope="module")
def k():
    return load_vanilla_pack()


def _gathers(result):
    return [t for t in result.tasks if isinstance(t, Gather)]


def test_a_group_target_gathers_any_member_and_searches_them_all(k):
    result = plan({LOGS: 4}, {}, k)
    (gather,) = _gathers(result)
    assert gather.item in k.item_tags[LOGS]
    searched = {gather.block, *(block for _, block in gather.alternatives)}
    assert {M + "spruce_log", M + "birch_log", M + "oak_log"} <= searched
    assert gather.item_count == 4


def test_held_members_of_any_kind_count_toward_the_group(k):
    assert not plan({LOGS: 4}, {M + "birch_log": 4}, k).tasks
    (gather,) = _gathers(plan({LOGS: 4}, {M + "spruce_log": 3}, k))
    assert gather.item_count == 1


def test_a_member_seen_nearby_leads_the_search(k):
    result = plan({LOGS: 2}, {}, k, known_blocks={M + "spruce_log"})
    assert _gathers(result)[0].item == M + "spruce_log"


def test_a_group_never_picks_a_member_that_is_not_found_in_the_world(k):
    # stripped logs and hyphae exist only once someone strips or crafts them
    (gather,) = _gathers(plan({LOGS: 1}, {}, k))
    assert not gather.item.startswith(M + "stripped_")


def test_a_crafted_group_is_made_from_any_log(k):
    result = plan({"#minecraft:planks": 8}, {M + "birch_log": 2}, k)
    assert not result.missing
    # the held birch logs become planks; nothing is chopped
    assert not _gathers(result)
    (craft,) = [t for t in result.tasks if type(t).__name__ == "Craft"]
    assert craft.result == M + "birch_planks"


def test_a_crafted_group_chops_any_log_when_none_is_held(k):
    result = plan({"#minecraft:planks": 4}, {}, k, known_blocks={M + "spruce_log"})
    (gather,) = _gathers(result)
    searched = {gather.block, *(block for _, block in gather.alternatives)}
    assert {M + "spruce_log", M + "birch_log", M + "oak_log"} <= searched


def test_an_unknown_group_is_reported_missing(k):
    result = plan({"#minecraft:no_such_tag": 3}, {}, k)
    assert not result.tasks
    assert [m.item for m in result.missing] == ["#minecraft:no_such_tag"]


def test_group_members_lists_the_tag(k):
    assert M + "oak_log" in group_members(k, LOGS)
    assert group_members(k, M + "oak_log") == (M + "oak_log",)
    assert group_members(k, "#minecraft:no_such_tag") == ()
