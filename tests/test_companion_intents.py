import pytest

from mclab import companion_intents as ci


def test_every_intent_has_a_tool_schema_with_a_description():
    tools = ci.tool_schemas()
    names = [t["name"] for t in tools]
    assert names == list(ci.INTENTS)
    for tool in tools:
        assert tool["description"]
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False


def test_job_intents_and_instant_intents_are_disjoint_and_complete():
    assert ci.JOB_INTENTS | ci.INSTANT_INTENTS == set(ci.INTENTS)
    assert not ci.JOB_INTENTS & ci.INSTANT_INTENTS
    assert ci.JOB_INTENTS == {"goto", "follow", "gather", "build", "attack"}


def test_unknown_intent_and_unknown_argument_are_refused():
    with pytest.raises(ValueError, match="Unknown intent"):
        ci.validate("dance", {})
    with pytest.raises(ValueError, match="Unknown argument"):
        ci.validate("status", {"verbose": True})


def test_goto_needs_exactly_one_target():
    assert ci.validate("goto", {"x": 1, "y": 64, "z": -3}) == {
        "target": {"pos": (1, 64, -3)},
        "radius": 2,
        "when": "now",
    }
    assert ci.validate("goto", {"player": "Marc"})["target"] == {"player": "Marc"}
    assert ci.validate("goto", {"place": "home", "radius": 4})["radius"] == 4
    with pytest.raises(ValueError, match="exactly one"):
        ci.validate("goto", {})
    with pytest.raises(ValueError, match="exactly one"):
        ci.validate("goto", {"place": "home", "player": "Marc"})
    with pytest.raises(ValueError, match="x, y and z"):
        ci.validate("goto", {"x": 1, "z": 2})


def test_numbers_must_be_integers_in_range_and_not_booleans():
    with pytest.raises(ValueError, match="x"):
        ci.validate("goto", {"x": 1.5, "y": 64, "z": 0})
    with pytest.raises(ValueError, match="x"):
        ci.validate("goto", {"x": True, "y": 64, "z": 0})
    with pytest.raises(ValueError, match="y"):
        ci.validate("goto", {"x": 0, "y": 400, "z": 0})
    with pytest.raises(ValueError, match="radius"):
        ci.validate("goto", {"place": "home", "radius": 9})
    with pytest.raises(ValueError, match="count"):
        ci.validate("gather", {"item": "oak_log", "count": 0})


def test_integral_floats_from_a_model_are_accepted():
    # JSON from a model often says 64.0 for 64
    assert ci.validate("gather", {"item": "oak_log", "count": 16.0})["count"] == 16


def test_gather_normalizes_the_item_id():
    assert ci.validate("gather", {"item": "Oak_Log", "count": 3}) == {
        "item": "minecraft:oak_log",
        "count": 3,
        "when": "now",
    }
    assert (
        ci.validate("gather", {"item": "minecraft:cobblestone", "count": 1})["item"]
        == "minecraft:cobblestone"
    )
    with pytest.raises(ValueError, match="item"):
        ci.validate("gather", {"item": "oak log", "count": 1})
    with pytest.raises(ValueError, match="item"):
        ci.validate("gather", {"item": "othermod:thing", "count": 1})


@pytest.mark.parametrize(
    ("said", "item"),
    [
        ("wood", "#minecraft:logs"),
        ("Logs", "#minecraft:logs"),
        ("log", "#minecraft:logs"),
        ("planks", "#minecraft:planks"),
        ("plank", "#minecraft:planks"),
        ("#minecraft:logs", "#minecraft:logs"),
        ("#wool", "#minecraft:wool"),
    ],
)
def test_gather_takes_a_group_of_items(said, item):
    # "get wood" means any log, not oak: a tag stands for every member
    assert ci.validate("gather", {"item": said, "count": 4})["item"] == item


def test_a_group_must_be_a_tag_id():
    with pytest.raises(ValueError, match="item"):
        ci.validate("gather", {"item": "#Not A Tag", "count": 1})
    with pytest.raises(ValueError, match="item"):
        ci.validate("gather", {"item": "#othermod:logs", "count": 1})


def test_the_gather_tool_tells_a_model_about_groups():
    (gather,) = [t for t in ci.tool_schemas() if t["name"] == "gather"]
    assert "#minecraft:logs" in gather["description"]


def test_defense_takes_hearts_and_radii():
    assert ci.validate("defense", {}) == {}
    assert ci.validate("defense", {"floor_hearts": 5}) == {"floor_health": 10}
    assert ci.validate("defense", {"floor_hearts": 2.5}) == {"floor_health": 5}
    assert ci.validate("defense", {"threat_radius": 16, "base_radius": 0}) == {
        "threat_radius": 16,
        "base_radius": 0,
    }
    for bad in ({"floor_hearts": 0}, {"floor_hearts": 10}, {"floor_hearts": 2.2}):
        with pytest.raises(ValueError, match="floor_hearts"):
            ci.validate("defense", bad)
    with pytest.raises(ValueError, match="threat_radius"):
        ci.validate("defense", {"threat_radius": 99})
    assert "defense" in ci.INSTANT_INTENTS
    assert "defense" not in ci.UNTRUSTED_INTENTS


def test_follow_defaults_and_bounds():
    assert ci.validate("follow", {"player": "Marc"}) == {
        "player": "Marc",
        "distance": 3,
        "when": "now",
    }
    with pytest.raises(ValueError, match="player"):
        ci.validate("follow", {"player": "no spaces allowed"})
    with pytest.raises(ValueError, match="distance"):
        ci.validate("follow", {"player": "Marc", "distance": 1})


def test_build_template_name_and_optional_origin():
    assert ci.validate("build", {"template": "house-small"}) == {
        "template": "house-small",
        "origin": None,
        "rotation": 0,
        "when": "now",
    }
    value = ci.validate(
        "build", {"template": "house-small", "x": 5, "y": 64, "z": 9, "rotation": 90}
    )
    assert value["origin"] == (5, 64, 9) and value["rotation"] == 90
    for bad in ("../secrets", "a/b", "", "x" * 81):
        with pytest.raises(ValueError, match="template"):
            ci.validate("build", {"template": bad})
    with pytest.raises(ValueError, match="rotation"):
        ci.validate("build", {"template": "house-small", "rotation": 45})
    with pytest.raises(ValueError, match="x, y and z"):
        ci.validate("build", {"template": "house-small", "x": 5})


def test_when_must_be_now_or_next_and_only_on_jobs():
    assert (
        ci.validate("gather", {"item": "dirt", "count": 1, "when": "next"})["when"]
        == "next"
    )
    with pytest.raises(ValueError, match="when"):
        ci.validate("gather", {"item": "dirt", "count": 1, "when": "later"})
    with pytest.raises(ValueError, match="Unknown argument"):
        ci.validate("status", {"when": "now"})


def test_say_text_length_and_control_characters():
    assert ci.validate("say", {"text": "hi"}) == {"text": "hi", "to": None}
    assert ci.validate("say", {"text": " hi\nthere ", "to": "Marc"}) == {
        "text": "hi there",
        "to": "Marc",
    }
    with pytest.raises(ValueError, match="text"):
        ci.validate("say", {"text": ""})
    with pytest.raises(ValueError, match="text"):
        ci.validate("say", {"text": "x" * 257})


def test_remember_place_name_and_position():
    assert ci.validate("remember_place", {"name": "Mine Entrance"}) == {
        "name": "mine entrance",
        "pos": None,
    }
    assert ci.validate("remember_place", {"name": "base", "x": 1, "y": 2, "z": 3})[
        "pos"
    ] == (1, 2, 3)
    with pytest.raises(ValueError, match="name"):
        ci.validate("remember_place", {"name": "!"})


def test_job_status_needs_a_job_id():
    assert ci.validate("job_status", {"job_id": "j-1a2b3c"}) == {"job_id": "j-1a2b3c"}
    with pytest.raises(ValueError, match="job_id"):
        ci.validate("job_status", {"job_id": "no spaces"})


def test_operator_actors_are_trusted_and_players_go_through_the_allowlist(monkeypatch):
    seen = []

    def allowed(uuid, name=""):
        seen.append((uuid, name))
        return name == "Marc"

    monkeypatch.setattr(ci, "_allowlisted", allowed)
    assert ci.authorized(ci.Actor("mcp"))
    assert ci.authorized(ci.Actor("api"))
    assert ci.authorized(ci.Actor("chat", name="Marc", uuid="u1"))
    assert not ci.authorized(ci.Actor("chat", name="Stranger", uuid="u2"))
    assert not ci.authorized(ci.Actor("command", name="Stranger", uuid="u2"))
    assert seen == [("u1", "Marc"), ("u2", "Stranger"), ("u2", "Stranger")]


def test_unknown_actor_source_is_refused():
    with pytest.raises(ValueError, match="source"):
        ci.Actor("telepathy")


def test_untrusted_tools_are_read_only():
    names = {t["name"] for t in ci.tool_schemas(trusted=False)}
    assert names == ci.UNTRUSTED_INTENTS
    assert not names & ci.JOB_INTENTS
    assert "stop" not in names and "say" not in names


def test_actor_round_trips_through_a_dict():
    actor = ci.Actor("chat", name="Marc", uuid="u1", position=(1.5, 64.0, -2.25))
    assert ci.Actor.from_dict(actor.to_dict()) == actor


# --- attack ------------------------------------------------------------------------


def test_attack_names_exactly_one_target():
    assert ci.validate("attack", {"player": "flamingchicken"}) == {
        "target": {"player": "flamingchicken"},
        "weapon": None,
        "when": "now",
    }
    assert ci.validate("attack", {"mob": "zombie"})["target"] == {
        "mob": "minecraft:zombie"
    }
    assert ci.validate("attack", {"mob": "Minecraft:Cow"})["target"] == {
        "mob": "minecraft:cow"
    }
    assert ci.validate("attack", {"nearest_hostile": True})["target"] == {
        "nearest_hostile": True
    }
    for none in ({}, {"nearest_hostile": False}):
        with pytest.raises(ValueError, match="exactly one"):
            ci.validate("attack", none)
    with pytest.raises(ValueError, match="exactly one"):
        ci.validate("attack", {"player": "Marc", "mob": "zombie"})
    with pytest.raises(ValueError, match="exactly one"):
        ci.validate("attack", {"mob": "zombie", "nearest_hostile": True})


def test_attack_target_values_are_checked():
    with pytest.raises(ValueError, match="player"):
        ci.validate("attack", {"player": "no spaces allowed"})
    with pytest.raises(ValueError, match="mob"):
        ci.validate("attack", {"mob": "Zom bie!"})
    with pytest.raises(ValueError, match="use player"):
        ci.validate("attack", {"mob": "player"})  # a player is named, not a mob kind
    with pytest.raises(ValueError, match="use player"):
        ci.validate("attack", {"mob": "minecraft:player"})
    with pytest.raises(ValueError, match="nearest_hostile"):
        ci.validate("attack", {"nearest_hostile": "yes"})


def test_attack_takes_a_weapon_and_when():
    got = ci.validate(
        "attack", {"mob": "zombie", "weapon": "Iron_Sword", "when": "next"}
    )
    assert got["weapon"] == "minecraft:iron_sword" and got["when"] == "next"
    assert ci.validate("attack", {"mob": "zombie", "weapon": "minecraft:bow"})[
        "weapon"
    ] == "minecraft:bow"
    with pytest.raises(ValueError, match="weapon"):
        ci.validate("attack", {"mob": "zombie", "weapon": "#minecraft:swords"})
    with pytest.raises(ValueError, match="when"):
        ci.validate("attack", {"mob": "zombie", "when": "later"})
    with pytest.raises(ValueError, match="Unknown argument"):
        ci.validate("attack", {"mob": "zombie", "speed": 3})


def test_attack_is_a_trusted_job_intent():
    assert "attack" in ci.JOB_INTENTS and "attack" not in ci.UNTRUSTED_INTENTS
    assert "attack" not in {t["name"] for t in ci.tool_schemas(trusted=False)}


def test_attack_tool_says_how_it_ends_and_who_may_use_it():
    (attack,) = [t for t in ci.tool_schemas() if t["name"] == "attack"]
    text = attack["description"]
    for word in ("killed", "lost", "invulnerable", "owner"):
        assert word in text
    assert set(attack["input_schema"]["properties"]) == {
        "player",
        "mob",
        "nearest_hostile",
        "weapon",
        "when",
    }


def test_hunting_is_a_gather_and_the_tool_says_so():
    (gather,) = [t for t in ci.tool_schemas() if t["name"] == "gather"]
    assert "hunt" in gather["description"] and "porkchop" in gather["description"]


def test_hitting_a_player_needs_the_operator_or_a_named_owner(monkeypatch):
    seen = []

    def listed(uuid, name=""):
        seen.append((uuid, name))
        return name == "Marc"

    monkeypatch.setattr(ci, "_listed", listed)
    assert ci.may_attack_players(ci.Actor("mcp"))
    assert ci.may_attack_players(ci.Actor("api"))
    assert ci.may_attack_players(ci.Actor("command", name="Marc", uuid="u1"))
    assert not ci.may_attack_players(ci.Actor("chat", name="Stranger", uuid="u2"))
    assert seen == [("u1", "Marc"), ("u2", "Stranger")]
