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
    for boss in ("warden", "ender_dragon", "wither", "ravager"):
        armed = inv("netherite_sword", "bow", arrows=64)
        assert cc.tactic(M + boss, armed).style == "avoid"
        assert cc.never_engaged(M + boss)
    assert not cc.never_engaged(M + "zombie")
    assert not cc.never_engaged(M + "creeper")


# --- tactic and weapon choice -------------------------------------------------------


def test_a_zombie_gets_crit_melee_with_the_best_damage_per_second():
    t = cc.tactic(M + "zombie", inv("iron_axe", "iron_sword"))
    # sword 6 x 1.5 every 12.5 ticks beats axe 9 x 1.5 every 22.2
    assert (t.style, t.weapon, t.crit) == ("melee", M + "iron_sword", True)


def test_a_lone_spear_pokes_from_outside_a_zombies_reach():
    t = cc.tactic(M + "zombie", inv("iron_spear"))
    assert (t.style, t.weapon, t.crit) == ("poke", M + "iron_spear", False)
    assert t.keep == cc.POKE_KEEP
    # a sword still out-damages it
    assert cc.tactic(M + "zombie", inv("iron_spear", "wooden_sword")).style == "melee"


def test_archers_are_shot_back_with_a_bow_and_arrows():
    t = cc.tactic(M + "skeleton", inv("bow", "iron_sword", arrows=16))
    assert (t.style, t.weapon) == ("snipe", M + "bow")
    assert t.keep == cc.SNIPE_KEEP
    assert t.melee == M + "iron_sword"  # for when it closes in
    # no arrows: the bow is useless, close in with the sword
    t = cc.tactic(M + "skeleton", inv("bow", "iron_sword"))
    assert (t.style, t.weapon) == ("melee", M + "iron_sword")


def test_a_creeper_is_shot_else_hit_and_left_else_avoided():
    assert cc.tactic(M + "creeper", inv("bow", arrows=5)).style == "snipe"
    t = cc.tactic(M + "creeper", inv("stone_sword"))
    assert (t.style, t.weapon) == ("hit_and_back", M + "stone_sword")
    assert t.crit is False  # the sprint hit's knockback is the point
    assert cc.tactic(M + "creeper", inv("iron_spear")).style == "avoid"
    assert cc.tactic(M + "creeper", {}).style == "avoid"


def test_fliers_and_ghasts_need_a_bow():
    for kind in ("phantom", "blaze", "ghast"):
        assert cc.tactic(M + kind, inv("bow", arrows=8)).style == "snipe"
        assert cc.tactic(M + kind, inv("diamond_sword")).style == "avoid"


def test_a_breeze_deflects_arrows_so_it_is_fought_in_melee():
    t = cc.tactic(M + "breeze", inv("bow", "stone_axe", arrows=8))
    assert (t.style, t.weapon) == ("melee", M + "stone_axe")


def test_an_enderman_is_fought_in_melee_and_never_stared_at():
    t = cc.tactic(M + "enderman", inv("iron_sword"))
    assert t.style == "melee" and t.no_gaze


def test_animals_are_hunted_for_the_biggest_single_hit():
    t = cc.tactic(M + "cow", inv("iron_sword", "iron_axe", "bow", arrows=8))
    # an iron axe crit (13.5) one-shots a cow; the sword (9) does not; no arrows wasted
    assert (t.style, t.weapon, t.crit) == ("hunt", M + "iron_axe", True)
    assert t.sweep is False
    # bare hands still hunt
    assert cc.tactic(M + "pig", {}) == cc.Tactic("hunt", None, crit=True, sweep=False)


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
