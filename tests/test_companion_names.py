"""Which in-game names are companion bots, and their fleet ids."""

import pytest

from mclab.companion_names import (
    NAMES_FILE,
    BotNames,
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


# --- the name book: bots the operator renamed --------------------------------------


def test_a_bot_nobody_renamed_keeps_its_derived_name():
    names = BotNames()
    assert names.name_for("default") == "MCBot"
    assert names.name_for("mcbot3") == "MCBot3"
    assert names.id_for("mcbot3") == "mcbot3"
    assert names.id_for("Ann") is None
    assert names.renamed == {}


def test_a_renamed_bot_answers_to_its_new_name_and_its_slot_name():
    names = BotNames().rename("mcbot2", "Rex")
    assert names.name_for("mcbot2") == "Rex"
    assert names.id_for("rex") == "mcbot2"
    # A roster saved before the rename still means that bot, never a human.
    assert names.id_for("MCBot2") == "mcbot2"
    assert names.is_bot("REX") and names.is_bot("MCBot2") and not names.is_bot("Ann")
    assert names.renamed == {"mcbot2": "Rex"}


def test_renaming_returns_a_new_book_and_back_to_the_slot_name_drops_the_entry():
    before = BotNames({"default": "Bolt"})
    after = before.rename("default", "mcbot")
    assert before.renamed == {"default": "Bolt"}
    assert after.renamed == {}
    assert after.name_for("default") == "MCBot"


@pytest.mark.parametrize(
    ("bot_id", "name", "problem"),
    [
        ("mcbot2", "Bolt", "already another bot.s name"),
        ("mcbot2", "MCBot3", "already another bot.s name"),
        ("mcbot2", "ab", "3-16 letters"),
        ("mcbot2", "has space", "3-16 letters"),
        ("pal", "Rex", "bot id"),
    ],
)
def test_a_name_another_bot_has_or_a_bad_one_is_refused(bot_id, name, problem):
    with pytest.raises(ValueError, match=problem):
        BotNames({"default": "Bolt"}).rename(bot_id, name)


def test_the_book_is_saved_and_loaded_beside_the_instance(tmp_path):
    BotNames().rename("mcbot2", "Rex").save(tmp_path)
    assert (tmp_path / NAMES_FILE).is_file()
    assert BotNames.load(tmp_path).name_for("mcbot2") == "Rex"


def test_a_missing_book_is_empty_and_a_broken_one_says_where(tmp_path):
    assert BotNames.load(tmp_path).renamed == {}
    (tmp_path / NAMES_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match=NAMES_FILE):
        BotNames.load(tmp_path)
    (tmp_path / NAMES_FILE).write_text('{"mcbot2": "x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="3-16 letters"):
        BotNames.load(tmp_path)
