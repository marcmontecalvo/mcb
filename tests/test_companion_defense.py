"""Auto-defence decisions (docs/COMPANION_DEFENSE_PLAN_2026-11-23.md section 2)."""

import pytest

from mclab import companion_defense as cd

M = "minecraft:"
HERE = (0.5, 64.0, 0.5)


def mob(kind="zombie", at=(4.5, 64.0, 0.5), uuid=None):
    return {"type": M + kind, "position": list(at), "uuid": uuid or f"{kind}-{at[0]}"}


def player(name="Marc", at=(6.5, 64.0, 0.5)):
    return {"type": M + "player", "name": name, "position": list(at), "uuid": name}


def situation(**kw):
    base = {
        "health": 20.0,
        "position": HERE,
        "entities": (mob(),),
        "inventory": {},
        "name": "MCBot",
        "base": None,
        "base_weapons": (),
        "can_burrow": True,
    }
    return cd.Situation(**{**base, **kw})


POLICY = cd.DefensePolicy()


# --- settings -------------------------------------------------------------------------


def test_defaults_keep_three_hearts():
    assert POLICY.floor_health == 6
    assert cd.DefensePolicy.from_config(None) == POLICY
    assert cd.DefensePolicy.from_config({}) == POLICY


def test_settings_are_validated():
    assert cd.DefensePolicy.from_config({"floor_health": 10}).floor_health == 10
    for bad in (
        {"floor_health": 0},
        {"floor_health": 20},
        {"threat_radius": 0},
        {"threat_radius": 40},
        {"ally_radius": -1},
        {"base_radius": 500},
        {"floor_health": "six"},
        {"bogus": 1},
    ):
        with pytest.raises(ValueError):
            cd.DefensePolicy.from_config(bad)


def test_settings_round_trip_and_change():
    changed = POLICY.changed(floor_health=8, threat_radius=16)
    assert changed.floor_health == 8 and changed.threat_radius == 16
    assert cd.DefensePolicy.from_config(changed.as_dict()) == changed
    with pytest.raises(ValueError):
        POLICY.changed(floor_health=99)


# --- what counts --------------------------------------------------------------------


def test_threats_are_hostiles_near_and_level_sorted_by_distance():
    far = mob(at=(30.5, 64.0, 0.5), uuid="far")
    below = mob(at=(2.5, 50.0, 0.5), uuid="below")  # in a cave under us
    cow = {"type": M + "cow", "position": [1.5, 64.0, 0.5], "uuid": "cow"}
    near = mob("skeleton", at=(2.5, 64.0, 0.5), uuid="near")
    found = cd.threats((far, below, cow, mob(uuid="z"), near), HERE, POLICY)
    assert [t.uuid for t in found] == ["near", "z"]
    assert found[0].distance == pytest.approx(2.0)


def test_allies_are_other_players_near():
    me = player("MCBot", at=HERE)
    far = player("Far", at=(80.5, 64.0, 0.5))
    found = cd.allies((me, far, player("Marc")), "MCBot", HERE, POLICY)
    assert [a.name for a in found] == ["Marc"]


def test_best_weapon_prefers_a_sword_then_an_axe_by_tier():
    assert cd.best_weapon({}) is None
    assert cd.best_weapon({M + "stick": 3}) is None
    assert cd.best_weapon({M + "stone_axe": 1}) == M + "stone_axe"
    assert (
        cd.best_weapon({M + "iron_axe": 1, M + "wooden_sword": 1}) == M + "wooden_sword"
    )
    assert (
        cd.best_weapon({M + "stone_sword": 1, M + "diamond_sword": 1})
        == M + "diamond_sword"
    )


# --- decisions ----------------------------------------------------------------------


def test_nothing_to_do_without_a_threat():
    assert cd.decide(situation(entities=()), POLICY).kind == "none"


def test_armed_and_healthy_fights_the_nearest():
    near = mob(at=(2.5, 64.0, 0.5), uuid="near")
    action = cd.decide(
        situation(entities=(mob(), near), inventory={M + "stone_sword": 1}), POLICY
    )
    assert (action.kind, action.target, action.item) == (
        "fight",
        "near",
        M + "stone_sword",
    )
    assert action.anchor is None


def test_an_axe_is_a_weapon():
    assert cd.decide(situation(inventory={M + "wooden_axe": 1}), POLICY).kind == "fight"


def test_with_an_ally_the_fight_stays_beside_them():
    action = cd.decide(
        situation(entities=(mob(), player()), inventory={M + "iron_sword": 1}), POLICY
    )
    assert action.kind == "fight"
    assert action.anchor == (6.5, 64.0, 0.5)
    assert action.leash == cd.ALLY_LEASH


def test_unarmed_fetches_a_weapon_from_the_base():
    action = cd.decide(
        situation(
            base=(20, 64, 0),
            base_weapons=(((21, 64, 0), M + "iron_axe"),),
        ),
        POLICY,
    )
    assert (action.kind, action.where, action.item) == (
        "arm",
        (21, 64, 0),
        M + "iron_axe",
    )


def test_unarmed_with_an_ally_regroups():
    action = cd.decide(situation(entities=(mob(), player())), POLICY)
    assert (action.kind, action.where) == ("regroup", (6.5, 64.0, 0.5))


def test_unarmed_alone_goes_home_else_burrows():
    assert cd.decide(situation(base=(20, 64, 0)), POLICY).kind == "shelter"
    assert cd.decide(situation(), POLICY).kind == "burrow"


def test_a_base_too_far_away_is_not_nearby():
    far = (500, 64, 0)
    assert cd.decide(situation(base=far), POLICY).kind == "burrow"
    armless = situation(base=far, base_weapons=(((501, 64, 0), M + "iron_axe"),))
    assert cd.decide(armless, POLICY).kind == "burrow"


@pytest.mark.parametrize("health", [6.0, 4.0])
def test_at_the_floor_it_never_fights(health):
    armed = {M + "diamond_sword": 1}
    assert cd.decide(situation(health=health, inventory=armed), POLICY).kind == "burrow"
    home = situation(health=health, inventory=armed, base=(20, 64, 0))
    assert cd.decide(home, POLICY).kind == "shelter"
    friend = situation(health=health, inventory=armed, entities=(mob(), player()))
    assert cd.decide(friend, POLICY).kind == "regroup"
    # the owner: "if there's another player or bot, stay with them during combat"
    both = situation(
        health=health, inventory=armed, entities=(mob(), player()), base=(20, 64, 0)
    )
    assert cd.decide(both, POLICY).kind == "regroup"


def test_the_floor_is_adjustable():
    armed = {M + "iron_sword": 1}
    careful = POLICY.changed(floor_health=14)
    assert cd.decide(situation(health=12.0, inventory=armed), careful).kind == "burrow"
    assert cd.decide(situation(health=12.0, inventory=armed), POLICY).kind == "fight"


def test_low_with_nowhere_to_dig_flees():
    action = cd.decide(situation(health=4.0, can_burrow=False), POLICY)
    assert action.kind == "flee"


def test_creepers_and_wardens_are_avoided_not_fought():
    armed = {M + "netherite_sword": 1}
    creeper = mob("creeper", at=(3.5, 64.0, 0.5), uuid="c")
    action = cd.decide(situation(entities=(creeper,), inventory=armed), POLICY)
    assert action.kind == "avoid"
    assert [t.uuid for t in action.threats] == ["c"]
    warden = mob("warden", uuid="w")
    assert cd.decide(situation(entities=(warden,), inventory=armed), POLICY).kind == (
        "avoid"
    )


def test_a_fight_skips_the_creeper_and_takes_the_zombie():
    armed = {M + "iron_sword": 1}
    creeper = mob("creeper", at=(2.5, 64.0, 0.5), uuid="c")
    zombie = mob(at=(5.5, 64.0, 0.5), uuid="z")
    action = cd.decide(situation(entities=(creeper, zombie), inventory=armed), POLICY)
    assert (action.kind, action.target) == ("fight", "z")


def test_every_action_says_why():
    for s in (
        situation(),
        situation(inventory={M + "iron_sword": 1}),
        situation(base=(20, 64, 0)),
        situation(health=2.0, can_burrow=False),
    ):
        assert cd.decide(s, POLICY).reason
