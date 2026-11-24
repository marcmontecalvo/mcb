"""Which in-game names are companion bots, and their fleet ids."""

import pytest

from mclab.companion_names import (
    bot_id_for,
    bot_id_for_name,
    bot_name_for,
    is_bot_name,
)


def test_bot_ids_and_names_follow_one_pattern():
    assert [bot_id_for(n) for n in (1, 2, 8)] == ["default", "mcbot2", "mcbot8"]
    assert [bot_name_for(i) for i in ("default", "mcbot2", "mcbot12")] == [
        "MCBot",
        "MCBot2",
        "MCBot12",
    ]
    with pytest.raises(ValueError, match="bot id"):
        bot_name_for("pal")


def test_a_player_name_maps_back_to_its_derived_bot_id():
    assert bot_id_for_name("MCBot") == "default"
    assert bot_id_for_name("mcbot") == "default"
    assert bot_id_for_name("MCBot2") == "mcbot2"
    assert bot_id_for_name("MCBOT12") == "mcbot12"
    for other in ("Ann", "MCBot1", "MCBot0", "MCBotX", "MCBot2b", "xMCBot2", ""):
        assert bot_id_for_name(other) is None


def test_a_derived_bot_name_is_told_from_a_human_one():
    assert is_bot_name("MCBot") and is_bot_name("MCBot16")
    assert not is_bot_name("Ann") and not is_bot_name("MCBot1")
