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
        "night": True,
        "can_heal": True,
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


def test_an_archer_is_a_threat_as_far_as_it_shoots():
    # live (group matrix 20261124T002512Z): a bow killed two of three skeletons and
    # the guard called it clear with the third shooting from past 12 blocks
    at14 = mob("skeleton", at=(14.5, 64.0, 0.5), uuid="s")
    stray = mob("stray", at=(0.5, 64.0, 16.0), uuid="t")
    zombie = mob(at=(14.5, 64.0, 0.5), uuid="z")
    out = mob("skeleton", at=(17.5, 64.0, 0.5), uuid="out")
    found = cd.threats((at14, stray, zombie, out), HERE, POLICY)
    assert [t.uuid for t in found] == ["s", "t"]
    assert cd.ARCHER_RANGE >= 15  # a skeleton's bow reaches 15 blocks


def test_the_fight_looks_as_far_as_the_farthest_threat_seen():
    near = cd.Threat("a", M + "zombie", (4.5, 64.0, 0.5), 4.0)
    archer = cd.Threat("b", M + "skeleton", (14.7, 64.0, 0.5), 14.2)
    assert cd.fight_radius((near,), POLICY) == 12
    assert cd.fight_radius((near, archer), POLICY) == 15
    assert cd.fight_radius((), POLICY) == 12


def test_allies_are_other_players_near():
    me = player("MCBot", at=HERE)
    far = player("Far", at=(80.5, 64.0, 0.5))
    found = cd.allies((me, far, player("Marc")), "MCBot", HERE, POLICY)
    assert [a.name for a in found] == ["Marc"]


def test_best_weapon_is_the_most_damage_per_second():
    assert cd.best_weapon({}) is None
    assert cd.best_weapon({M + "stick": 3}) is None
    assert cd.best_weapon({M + "stone_axe": 1}) == M + "stone_axe"
    # iron axe crit 13.5 per 22 ticks beats wooden sword crit 6 per 12.5
    assert cd.best_weapon({M + "iron_axe": 1, M + "wooden_sword": 1}) == M + "iron_axe"
    assert (
        cd.best_weapon({M + "stone_sword": 1, M + "diamond_sword": 1})
        == M + "diamond_sword"
    )
    assert cd.best_weapon({M + "bow": 1, M + "arrow": 9}) is None  # not for melee


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
    assert action.tactic.style == "melee" and action.tactic.crit


def test_the_fight_uses_the_targets_tactic():
    skeleton = mob("skeleton", uuid="s")
    kit = {M + "bow": 1, M + "arrow": 20, M + "iron_sword": 1}
    action = cd.decide(situation(entities=(skeleton,), inventory=kit), POLICY)
    assert (action.kind, action.item, action.tactic.style) == (
        "fight",
        M + "bow",
        "snipe",
    )


def test_an_evoker_is_fought_before_nearer_mobs():
    evoker = mob("evoker", at=(9.5, 64.0, 0.5), uuid="e")
    action = cd.decide(
        situation(entities=(mob(), evoker), inventory={M + "iron_sword": 1}), POLICY
    )
    assert action.target == "e"


def test_a_mob_it_cannot_fight_with_what_it_holds_counts_as_unarmed():
    phantom = mob("phantom", uuid="p")
    sword = {M + "iron_sword": 1}
    action = cd.decide(situation(entities=(phantom,), inventory=sword), POLICY)
    assert action.kind == "burrow"  # at night, nothing to shoot it with
    assert "no weapon for it" in action.reason


def test_a_mob_its_melee_could_not_reach_is_not_fought_hand_to_hand_again():
    up = mob("skeleton", uuid="s1")  # on a ledge the fight found no way up to
    sword = {M + "iron_sword": 1}
    action = cd.decide(
        situation(entities=(up,), inventory=sword, night=False, out_of_reach={"s1"}),
        POLICY,
    )
    assert action.kind == "escape"  # by day, a skeleton shooting: get out of its sight
    assert "out of reach" in action.reason
    bow = {M + "bow": 1, M + "arrow": 16, **sword}
    shot = cd.decide(
        situation(entities=(up,), inventory=bow, night=False, out_of_reach={"s1"}),
        POLICY,
    )
    assert shot.kind == "fight" and shot.tactic.style == "snipe"  # arrows reach it
    other = mob("zombie", at=(3.5, 64.0, 0.5), uuid="z1")
    both = cd.decide(
        situation(entities=(up, other), inventory=sword, out_of_reach={"s1"}), POLICY
    )
    assert both.kind == "fight" and both.target == "z1"  # the one it can reach


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


def test_low_with_nowhere_to_dig_escapes():
    action = cd.decide(situation(health=4.0, can_burrow=False), POLICY)
    assert action.kind == "escape"


def test_by_day_an_unarmed_bot_walks_away_instead_of_digging_in():
    # by day a healthy bot may leave its hole at once, the mob is still there, and it
    # would dig in three blocks deeper every round
    far = mob(at=(8.5, 64.0, 0.5))
    action = cd.decide(situation(entities=(far,), night=False), POLICY)
    assert action.kind == "walk_away" and action.reason


@pytest.mark.parametrize(
    "entities, why",
    [
        ((mob(at=(4.5, 64.0, 0.5)),), "a mob within 6 blocks"),
        ((mob(at=(8.5, 64.0, 0.5)), mob(at=(0.5, 64.0, 9.5), uuid="b")), "two mobs"),
        ((mob("skeleton", at=(11.5, 64.0, 0.5)),), "an archer in view"),
        ((mob("creeper", at=(6.8, 64.0, 0.5)),), "a creeper inside its 7 blocks"),
    ],
)
def test_by_day_it_escapes_instead_of_walking_when_it_is_pressed(entities, why):
    action = cd.decide(situation(entities=entities, night=False), POLICY)
    assert action.kind == "escape", why


def test_hurt_by_day_it_hides_only_while_it_can_heal():
    assert cd.decide(situation(health=4.0, night=False), POLICY).kind == "burrow"
    starving = situation(health=4.0, night=False, can_heal=False)
    assert cd.decide(starving, POLICY).kind == "escape"
    # at night the hole waits for day, healing or not
    assert cd.decide(situation(health=4.0, can_heal=False), POLICY).kind == "burrow"


def test_a_creeper_is_hit_and_left_with_a_sword():
    armed = {M + "netherite_sword": 1}
    creeper = mob("creeper", at=(3.5, 64.0, 0.5), uuid="c")
    action = cd.decide(situation(entities=(creeper,), inventory=armed), POLICY)
    assert (action.kind, action.target) == ("fight", "c")
    assert action.tactic.style == "hit_and_back"


def test_a_creeper_is_charged_from_past_its_fuse_with_a_spear():
    creeper = mob("creeper", at=(8.5, 64.0, 0.5), uuid="c")
    spear = {M + "iron_spear": 1, M + "iron_sword": 1}
    action = cd.decide(situation(entities=(creeper,), inventory=spear), POLICY)
    assert (action.kind, action.target, action.item) == ("fight", "c", M + "iron_spear")
    assert (action.tactic.style, action.tactic.melee) == ("lance", None)


def test_an_unarmed_bot_escapes_a_creeper_rather_than_digging_beside_it():
    creeper = mob("creeper", at=(3.5, 64.0, 0.5), uuid="c")
    action = cd.decide(situation(entities=(creeper,)), POLICY)
    assert action.kind == "escape"
    assert [t.uuid for t in action.threats] == ["c"]


def test_a_warden_or_boss_in_view_means_escape_whatever_it_holds():
    armed = {M + "netherite_sword": 1}
    warden = mob("warden", at=(10.5, 64.0, 0.5), uuid="w")
    both = situation(entities=(mob(), warden), inventory=armed)
    action = cd.decide(both, POLICY)
    assert action.kind == "escape" and "warden" in action.reason


def test_the_fight_takes_the_nearest_target_it_can_fight():
    armed = {M + "iron_sword": 1}
    phantom = mob("phantom", at=(2.5, 64.0, 0.5), uuid="p")  # needs a bow
    zombie = mob(at=(5.5, 64.0, 0.5), uuid="z")
    action = cd.decide(situation(entities=(phantom, zombie), inventory=armed), POLICY)
    assert (action.kind, action.target) == ("fight", "z")


def test_every_reason_names_the_nearest_mob():
    # live, Nov 23: "No weapon: walking away." left the owner asking from what
    for s in (
        situation(),
        situation(night=False),
        situation(night=False, entities=(mob(at=(9.5, 64.0, 0.5)),)),
        situation(health=4.0),
        situation(health=4.0, can_burrow=False),
        situation(can_burrow=False),
        situation(base=(20, 64, 0)),
        situation(health=4.0, base=(20, 64, 0)),
        situation(entities=(mob(), player())),
        situation(health=4.0, entities=(mob(), player())),
        situation(base=(20, 64, 0), base_weapons=(((21, 64, 0), M + "iron_axe"),)),
    ):
        assert "a zombie" in cd.decide(s, POLICY).reason
    evoker = situation(entities=(mob("evoker"),))
    assert "an evoker" in cd.decide(evoker, POLICY).reason


def test_every_action_says_why():
    for s in (
        situation(),
        situation(inventory={M + "iron_sword": 1}),
        situation(base=(20, 64, 0)),
        situation(health=2.0, can_burrow=False),
    ):
        assert cd.decide(s, POLICY).reason


def test_auto_defence_never_fights_a_player():
    # only an owner's attack command (allow_player) may hit a player: the guard sees
    # hostile types only, whoever stands near and whatever the bot holds
    sword = {M + "iron_sword": 1}
    for kind_health in (20.0, 3.0):
        action = cd.decide(
            situation(
                entities=(player("Marc", at=(2.5, 64.0, 0.5)),),
                inventory=sword,
                health=kind_health,
            ),
            POLICY,
        )
        assert action.kind == "none"
    fight = cd.decide(
        situation(entities=(player(), mob()), inventory=sword), POLICY
    )
    assert fight.kind == "fight" and fight.target == mob()["uuid"]  # the zombie, not Marc
