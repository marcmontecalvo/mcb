"""Combat tactics and weapon choice (docs/COMPANION_COMBAT_PLAN_2026-11-23.md s2)."""

import pytest

from mclab import companion_combat as cc
from mclab.profiles import HOSTILE

M = "minecraft:"


def inv(*items, arrows=0):
    out = {M + item: 1 for item in items}
    if arrows:
        out[M + "arrow"] = arrows
    return out


# --- weapon stats (COMBAT_MECHANICS_26.3.md section 3) ------------------------------


@pytest.mark.parametrize(
    "item, kind, damage, cooldown",
    [
        ("wooden_sword", "sword", 4, 12.5),
        ("netherite_sword", "sword", 8, 12.5),
        ("stone_axe", "axe", 9, 25.0),
        ("iron_axe", "axe", 9, 20 / 0.9),
        ("netherite_axe", "axe", 10, 20.0),
        ("iron_spear", "spear", 3, 19.0),
        ("trident", "trident", 9, 20 / 1.1),
        ("bow", "bow", 6, 20.0),
    ],
)
def test_weapon_stats_come_from_the_game(item, kind, damage, cooldown):
    w = cc.weapon(M + item)
    assert (w.kind, w.damage) == (kind, damage)
    assert w.cooldown == pytest.approx(cooldown)


def test_unknown_items_are_not_weapons():
    assert cc.weapon(M + "stick") is None
    assert cc.weapon(M + "diamond_pickaxe") is None


def test_only_swing_weapons_crit():
    assert cc.weapon(M + "iron_sword").crits
    assert cc.weapon(M + "iron_axe").crits
    assert cc.weapon(M + "trident").crits
    assert not cc.weapon(M + "iron_spear").crits  # a jab is its own code path
    assert not cc.weapon(M + "bow").crits


# --- profiles -----------------------------------------------------------------------


def test_every_hostile_has_a_profile():
    missing = sorted(t for t in HOSTILE if t not in cc.PROFILES)
    assert not missing


def test_bosses_and_wardens_are_never_engaged():
    for boss in ("warden", "ender_dragon", "wither"):
        armed = inv("netherite_sword", "bow", arrows=64)
        assert cc.tactic(M + boss, armed).style == "avoid"
        assert cc.never_engaged(M + boss)
    assert not cc.never_engaged(M + "zombie")
    assert not cc.never_engaged(M + "creeper")


def test_a_ravager_is_shot_from_range_and_never_met_in_melee():
    # 100 health and a 12-point bite: a raid's ravager is shot as it comes, the
    # bow's swing weapon only for one that closes in; without arrows, run
    t = cc.tactic(M + "ravager", inv("iron_sword", "bow", arrows=64))
    assert (t.style, t.weapon, t.melee) == ("snipe", M + "bow", M + "iron_sword")
    assert cc.tactic(M + "ravager", inv("netherite_sword")).style == "avoid"
    assert not cc.never_engaged(M + "ravager")
    assert cc.mob_health(M + "ravager") == 100


def test_an_evoker_is_shot_first_since_it_runs_from_melee():
    # it flees a player inside 8 blocks: in melee the bot landed 0 or 1 hits in
    # 10 trials while its vexes wore it down (20261124T014222Z); still first
    kit = inv("iron_sword", "iron_axe", "bow", arrows=64)
    t = cc.tactic(M + "evoker", kit)
    assert (t.style, t.weapon, t.melee) == ("snipe", M + "bow", M + "iron_axe")
    assert cc.tactic(M + "evoker", inv("iron_sword")).style == "melee"
    assert cc.first(M + "evoker")


def test_a_loyal_trident_is_thrown_where_the_bow_would_shoot_and_there_is_no_bow():
    # a Loyalty trident comes back: thrown from the bow's band, the best other
    # swing weapon (never the trident, which is out) meets one that closes in
    kit = inv("iron_sword", "trident")
    t = cc.tactic(M + "skeleton", kit, loyal=True)
    assert (t.style, t.weapon, t.melee) == ("throw", M + "trident", M + "iron_sword")
    assert t.keep == cc.SNIPE_KEEP
    # without Loyalty it would not come back: swung as before
    assert cc.tactic(M + "skeleton", kit).style == "melee"
    # the bow first while there are arrows
    archer = cc.tactic(M + "skeleton", inv("trident", "bow", arrows=64), loyal=True)
    assert (archer.style, archer.melee) == ("snipe", M + "trident")
    # alone, nothing to meet it with up close
    assert cc.tactic(M + "blaze", inv("trident"), loyal=True).melee is None


def test_a_trident_is_loyal_when_a_carried_one_has_loyalty():
    plain = {"slot": 0, "item": M + "trident", "count": 1}
    loyal = {**plain, "slot": 1, "enchantments": {M + "loyalty": 3}}
    sword = {"slot": 2, "item": M + "iron_sword", "count": 1,
             "enchantments": {M + "loyalty": 1}}  # fmt: skip
    assert cc.loyal([plain, loyal])
    assert not cc.loyal([plain, sword]) and not cc.loyal(None)


def test_a_creeper_takes_the_bow_then_the_charge_or_throw_then_the_swing():
    # the owner's order: bow, then the spear's charge or the trident's throw,
    # then sword or axe
    creeper, kit = M + "creeper", inv("iron_sword", "trident")
    assert cc.tactic(creeper, kit, loyal=True).style == "throw"
    spear = cc.tactic(creeper, inv("iron_spear", "trident"), loyal=True)
    assert spear.style == "lance"
    assert cc.tactic(creeper, kit).style == "hit_and_back"


def test_a_ravager_is_met_behind_a_shield_when_the_bot_carries_one():
    # a blocked bite can stun it (the opening to strike); the shield in the off
    # hand is the mod's to raise, the tactic only picks the swing weapon
    kit = inv("iron_sword", "iron_axe", "bow", "shield", arrows=64)
    t = cc.tactic(M + "ravager", kit)
    assert (t.style, t.weapon) == ("melee", M + "iron_axe")
    no_bow = cc.tactic(M + "ravager", inv("iron_sword", "shield"))
    assert (no_bow.style, no_bow.weapon) == ("melee", M + "iron_sword")
    # a shield and nothing to swing: the bow as before, and with nothing, run
    assert cc.tactic(M + "ravager", inv("shield", "bow", arrows=64)).style == "snipe"
    assert cc.tactic(M + "ravager", inv("shield")).style == "avoid"


def test_raiders_that_shoot_are_shot_back():
    for kind in ("pillager", "illusioner"):
        t = cc.tactic(M + kind, inv("iron_sword", "bow", arrows=64))
        assert (t.style, t.weapon) == ("snipe", M + "bow")
        assert M + kind in cc.RANGED
    assert cc.mob_health(M + "illusioner") == 32


# --- tactic and weapon choice -------------------------------------------------------


def test_melee_takes_the_weapon_that_kills_this_mob_soonest():
    # The first crit lands on arrival, so a kill takes (hits - 1) cycles. A zombie
    # (20): the iron axe's two crits (13.5 each) in one 22.2-tick cycle beat the
    # sword's three (9 each) in two 12.5-tick cycles, as measured (10x matrix
    # 20261123T235754Z: zombie axe 2.3 s, sword 2.7; skeleton 2.2 against 2.6).
    t = cc.tactic(M + "zombie", inv("iron_axe", "iron_sword"))
    assert (t.style, t.weapon, t.crit) == ("melee", M + "iron_axe", True)
    assert cc.kill_ticks(cc.weapon(M + "iron_axe"), 20) == pytest.approx(20 / 0.9)
    assert cc.kill_ticks(cc.weapon(M + "iron_sword"), 20) == pytest.approx(25.0)
    # a vex (14): two sword crits (18) in one cycle; the axe's two (27) take longer
    vex = cc.tactic(M + "vex", inv("iron_axe", "iron_sword"))
    assert vex.weapon == M + "iron_sword"
    # a tie goes to the higher damage per second (the next mob comes sooner)
    assert cc.tactic(M + "silverfish", inv("iron_axe", "iron_sword")).weapon == (
        M + "iron_sword"
    )


def test_hit_and_back_takes_the_weapon_with_the_fewest_hits():
    # each hit is a sprint in and a retreat: no crits, and the round trip is the cost
    t = cc.tactic(M + "creeper", inv("iron_axe", "iron_sword"))
    assert (t.style, t.weapon) == ("hit_and_back", M + "iron_axe")  # 3 hits, not 4


def test_a_snipers_hand_off_is_the_quickest_swing_weapon():
    t = cc.tactic(M + "pillager", inv("bow", "iron_sword", "iron_axe", arrows=8))
    assert (t.style, t.melee) == ("snipe", M + "iron_axe")  # 24: two crits, not three
    # a spear is the hand-off only with no swing weapon
    t = cc.tactic(M + "pillager", inv("bow", "iron_spear", arrows=8))
    assert t.melee == M + "iron_spear"


def test_a_lone_spear_charges():
    t = cc.tactic(M + "zombie", inv("iron_spear"))
    assert (t.style, t.weapon, t.melee) == ("lance", M + "iron_spear", None)
    assert t.wire() == {"style": "lance", "crit": False, "sweep": True}


def test_a_swing_weapon_carried_with_a_spear_fights_alone():
    # Opening with the charge then swapping was measured slower (10x matrix
    # 20261123T235754Z: zombie 3.5 s against the sword's 2.7, skeleton 2.9 against
    # 2.6). The charge opens only when it kills sooner: alone, the first crit lands
    # on arrival ((n - 1) cycles); after a charge the swap resets the cooldown, so
    # each of the m swings left waits a full cycle (m cycles).
    t = cc.tactic(M + "skeleton", inv("iron_spear", "iron_axe", "iron_sword"))
    assert (t.style, t.weapon, t.melee) == ("melee", M + "iron_axe", None)
    # a netherite charge (7) beats a wooden sword's crit (6), and saves one swing on
    # a zombie (20: four crits, or the charge and three): a tie, so the sword alone
    t = cc.tactic(M + "zombie", inv("netherite_spear", "wooden_sword"))
    assert t == cc.Tactic("melee", M + "wooden_sword", crit=True)
    assert (
        cc.charge_first(
            cc.weapon(M + "netherite_spear"), cc.weapon(M + "wooden_sword"), 20.0
        )
        is False
    )
    # the rule itself: a charge that saves two swings opens the fight
    big = cc.Weapon(M + "x_spear", "spear", 3, 19, charge=2.2)  # 1 + floor(12.3)
    assert cc.charge_first(big, cc.weapon(M + "wooden_sword"), 20.0) is True
    assert cc.mob_health(M + "zombie") == 20 and cc.mob_health(M + "x") == 20


def test_a_spear_charge_is_modelled_by_its_speed_not_its_jab():
    # 1 + floor(5.6 b/s sprint x the tier's multiplier) once a pass (contact cooldown
    # 10 ticks, then closing in again); the jab's 3 is the item's attack damage
    iron = cc.weapon(M + "iron_spear")
    assert iron.damage == 3 and iron.hit() == 6
    assert iron.cycle() == cc.CHARGE_CYCLE
    assert cc.weapon(M + "netherite_spear").hit() == 7
    assert cc.weapon(M + "wooden_spear").hit() == 4


def test_archers_are_shot_back_with_a_bow_and_arrows():
    t = cc.tactic(M + "skeleton", inv("bow", "iron_sword", arrows=16))
    assert (t.style, t.weapon) == ("snipe", M + "bow")
    assert t.keep == cc.SNIPE_KEEP
    assert t.melee == M + "iron_sword"  # for when it closes in
    # no arrows: the bow is useless, close in with the sword
    t = cc.tactic(M + "skeleton", inv("bow", "iron_sword"))
    assert (t.style, t.weapon) == ("melee", M + "iron_sword")


def test_a_creeper_is_shot_else_charged_else_hit_and_left_else_avoided():
    # The owner's order: the bow, then the spear's charge from past its fuse, and
    # only then in close with a sword or axe.
    assert cc.tactic(M + "creeper", inv("bow", "iron_spear", arrows=5)).style == "snipe"
    t = cc.tactic(M + "creeper", inv("iron_spear", "stone_sword"))
    # never handed to the sword: the mod outruns it after each charge hit
    assert (t.style, t.weapon, t.melee) == ("lance", M + "iron_spear", None)
    assert cc.tactic(M + "creeper", inv("iron_spear")) == t
    t = cc.tactic(M + "creeper", inv("stone_sword"))
    assert (t.style, t.weapon) == ("hit_and_back", M + "stone_sword")
    assert t.crit is False  # the sprint hit's knockback is the point
    assert cc.tactic(M + "creeper", {}).style == "avoid"


def test_fliers_and_ghasts_need_a_bow():
    for kind in ("phantom", "blaze", "ghast"):
        assert cc.tactic(M + kind, inv("bow", arrows=8)).style == "snipe"
        assert cc.tactic(M + kind, inv("diamond_sword")).style == "avoid"


def test_a_bow_alone_shoots_a_melee_mob_rather_than_running():
    t = cc.tactic(M + "zombie", inv("bow", arrows=16))
    assert (t.style, t.weapon, t.melee) == ("snipe", M + "bow", None)
    for kind in ("spider", "witch", "vindicator", "slime"):
        assert cc.tactic(M + kind, inv("bow", arrows=16)).style == "snipe"
    # a melee weapon held: melee stays first
    assert cc.tactic(M + "zombie", inv("bow", "iron_sword", arrows=16)).style == "melee"


def test_arrows_never_go_at_a_breeze_or_an_enderman():
    # the breeze deflects them; an enderman teleports out of their way
    for kind in ("breeze", "enderman"):
        assert cc.tactic(M + kind, inv("bow", arrows=16)).style == "avoid"


def test_a_breeze_deflects_arrows_so_it_is_fought_in_melee():
    t = cc.tactic(M + "breeze", inv("bow", "stone_axe", arrows=8))
    assert (t.style, t.weapon) == ("melee", M + "stone_axe")


def test_an_enderman_is_fought_in_melee_and_never_stared_at():
    t = cc.tactic(M + "enderman", inv("iron_sword"))
    assert t.style == "melee" and t.no_gaze


def test_animals_are_hunted_with_the_bow_when_it_has_arrows():
    # The owner's rule: bow and arrows first; it is shot, never swung, so a pig
    # that wanders in close is still shot (no melee backup), and it is never fled.
    t = cc.tactic(M + "cow", inv("iron_sword", "iron_axe", "bow", arrows=8))
    assert t == cc.Tactic("snipe", M + "bow", keep=cc.HUNT_KEEP)
    assert t.keep[0] == 0 and t.melee is None


def test_a_bow_without_arrows_is_never_swung_at_an_animal():
    t = cc.tactic(M + "pig", inv("bow"))
    assert t == cc.Tactic("hunt", None, crit=True, sweep=False)  # punch it
    assert cc.tactic(M + "pig", {}) == t  # bare hands still hunt


def test_animals_are_hunted_with_the_quickest_kill():
    # an iron axe crit (13.5) one-shots a cow (10 HP); the sword's (9) does not
    t = cc.tactic(M + "cow", inv("iron_sword", "iron_axe"))
    assert (t.style, t.weapon, t.crit) == ("hunt", M + "iron_axe", True)
    assert t.sweep is False
    # both one-shot: the sword wears one use a hit, the axe two
    assert cc.tactic(M + "cow", inv("diamond_sword", "iron_axe")).weapon == (
        M + "diamond_sword"
    )
    # a chicken (4 HP) dies to any crit: the cheapest wear wins
    assert cc.tactic(M + "chicken", inv("wooden_sword", "iron_axe")).weapon == (
        M + "wooden_sword"
    )


def test_a_hunt_spares_a_weapon_the_kill_would_break():
    # the axe has one use left and spends two a hit
    worn = cc.tactic(M + "pig", inv("iron_axe", "iron_sword"), {M + "iron_axe": [1]})
    assert worn.weapon == M + "iron_sword"
    # a piece with uses left is enough
    fresh = {M + "iron_axe": [1, 200]}
    assert cc.tactic(M + "pig", inv("iron_axe", "iron_sword"), fresh).weapon == (
        M + "iron_axe"
    )
    # nothing that survives the kill: punch it
    gone = cc.tactic(M + "pig", inv("iron_sword"), {M + "iron_sword": [1]})
    assert gone.weapon is None


def test_a_spear_alone_hunts_with_its_charge():
    t = cc.tactic(M + "pig", inv("iron_spear"))
    assert t == cc.Tactic("lance", M + "iron_spear")
    # a sword crit (9) kills a pig in as many hits, and sooner (12.5 ticks, not 20)
    t = cc.tactic(M + "pig", inv("iron_spear", "iron_sword"))
    assert (t.style, t.weapon) == ("hunt", M + "iron_sword")


def test_a_hunt_weapon_is_picked_for_a_generic_animal():
    assert cc.hunt_weapon(None, inv("iron_sword", "iron_axe")).item == M + "iron_axe"
    assert cc.hunt_weapon(None, inv("bow", "iron_axe", arrows=1)).item == M + "bow"
    assert cc.hunt_weapon(None, inv("bow")) is None


def test_unarmed_against_a_mob_has_no_tactic():
    assert cc.tactic(M + "zombie", {}).style == "avoid"
    assert cc.tactic(M + "skeleton", inv("bow")).style == "avoid"  # no arrows


def test_melee_weapons_rank_best_first():
    ranked = cc.MELEE_RANKED
    assert ranked[0] == M + "netherite_sword"
    # by damage per second with crits: netherite axe 15/20 ticks beats wood sword 6/12.5
    assert ranked.index(M + "netherite_axe") < ranked.index(M + "wooden_sword")
    assert ranked.index(M + "iron_sword") < ranked.index(M + "iron_axe")
    assert ranked.index(M + "wooden_axe") < ranked.index(M + "netherite_spear")
    assert M + "bow" not in ranked


def test_a_tactic_serialises_for_the_fight_op():
    t = cc.tactic(M + "skeleton", inv("bow", "iron_sword", arrows=16))
    assert t.wire() == {
        "style": "snipe",
        "crit": False,
        "sweep": True,
        "keep": list(cc.SNIPE_KEEP),
        "melee": M + "iron_sword",
    }
    hunt = cc.tactic(M + "cow", inv("iron_axe"))
    assert hunt.wire() == {"style": "hunt", "crit": True, "sweep": False}
