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


def test_a_creeper_is_shot_else_hit_and_left_else_outreached_else_avoided():
    assert cc.tactic(M + "creeper", inv("bow", arrows=5)).style == "snipe"
    t = cc.tactic(M + "creeper", inv("stone_sword"))
    assert (t.style, t.weapon) == ("hit_and_back", M + "stone_sword")
    assert t.crit is False  # the sprint hit's knockback is the point
    # a sword's sprint hit lands sooner than a jab's long charge
    assert cc.tactic(M + "creeper", inv("iron_spear", "stone_sword")).style == (
        "hit_and_back"
    )
    assert cc.tactic(M + "creeper", {}).style == "avoid"


def test_a_lone_spear_jabs_a_creeper_from_past_its_fuse():
    t = cc.tactic(M + "creeper", inv("iron_spear"))
    assert (t.style, t.weapon, t.crit) == ("poke", M + "iron_spear", False)
    assert t.keep == cc.FUSE_KEEP
    near, far = cc.FUSE_KEEP
    # measured eye to box: the creeper's feet are a half-width (0.3) farther still,
    # and it lights only with the body's feet inside 3 blocks; room is left for the
    # body to drift on while it turns round (live 20261123T205225Z)
    assert near + 0.3 - cc.CREEPER_FUSE >= 0.8
    assert far < cc.JAB_REACH  # every held distance is within a jab
    assert t.wire()["keep"] == [near, far]


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


def test_a_spear_alone_hunts_with_its_jab():
    t = cc.tactic(M + "pig", inv("iron_spear"))
    assert (t.style, t.weapon, t.keep) == ("poke", M + "iron_spear", cc.POKE_KEEP)


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
