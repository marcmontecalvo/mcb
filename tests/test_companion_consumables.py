"""Food and potions: which one suits the moment (mclab.companion_consumables)."""

from __future__ import annotations

import math

import pytest

from mclab import companion_consumables as cons

M = "minecraft:"
FOODS = {M + "cooked_beef": {"nutrition": 8}, M + "bread": {"nutrition": 5},
         M + "golden_apple": {"nutrition": 4}}  # fmt: skip


def potion(slot, name, effect, amplifier=0, *, item="potion", count=1, duration=1):
    return {"slot": slot, "item": M + item, "count": count, "potion": M + name,
            "effects": [{"id": M + effect, "amplifier": amplifier,
                         "duration": duration}]}  # fmt: skip


HEAL = potion(10, "strong_healing", "instant_health", 1)
SPLASH_HEAL = potion(11, "strong_healing", "instant_health", 1, item="splash_potion")
HARM = potion(12, "strong_harming", "instant_damage", 1, item="splash_potion")
WEAK = potion(13, "weakness", "weakness", item="splash_potion", duration=1800)
SLOW = potion(14, "slowness", "slowness", item="splash_potion", duration=1800)
POISON = potion(15, "poison", "poison", item="splash_potion", duration=900)
STRENGTH = potion(16, "strength", "strength", duration=3600)
SPEED = potion(17, "swiftness", "speed", duration=3600)
FIRE = potion(18, "fire_resistance", "fire_resistance", duration=3600)
REGEN = potion(19, "regeneration", "regeneration", duration=900)
BEEF = {"slot": 1, "item": M + "cooked_beef", "count": 16}
GAPPLE = {"slot": 2, "item": M + "golden_apple", "count": 2}


def items(*rows):
    return cons.held(list(rows), FOODS)


def body(health=20.0, **kw):
    return cons.Body(health=health, position=(0.0, 64.0, 0.0), **kw)


STILL = (0.0, 0.0, 0.0)


def foe(kind, x, z=0.0, uuid=None, v=STILL):
    """A foe standing still unless ``v`` (blocks/s) says otherwise; None: its course
    is unknown."""
    return cons.Foe(uuid or f"{kind}-{x}-{z}", M + kind, (float(x), 64.0, float(z)),
                    velocity=v)  # fmt: skip


def decide(b, rows, foes=(), marks=None):
    return cons.decide(b, items(*rows), cons.ranged(foes, b.position), marks or {})


# --- reading the inventory -------------------------------------------------------


def test_held_reads_potions_with_their_effects_and_foods_by_the_knowledge_pack():
    got = items(HEAL, BEEF, {"slot": 3, "item": M + "stone", "count": 5})
    kinds = {(h.item, h.potion) for h in got}
    assert kinds == {(M + "potion", M + "strong_healing"), (M + "cooked_beef", None)}
    heal = next(h for h in got if h.potion)
    assert heal.effect(M + "instant_health").amplifier == 1
    assert next(h for h in got if not h.potion).nutrition == 8


def test_a_potion_without_contents_is_not_held_as_a_consumable():
    assert items({"slot": 3, "item": M + "potion", "count": 1}) == ()


def test_body_reads_effects_fire_and_saturation_from_the_observation():
    b = cons.Body.from_observation({
        "health": 12.5, "food": 17, "saturation": 2.0, "absorption": 4.0,
        "fire_ticks": 40, "position": [1, 2, 3],
        "effects": [{"id": M + "strength", "amplifier": 0, "duration": 300}],
    })  # fmt: skip
    assert (b.health, b.food, b.absorption, b.fire_ticks) == (12.5, 17, 4.0, 40)
    assert b.active(M + "strength", 200) and not b.active(M + "strength", 400)
    assert not b.active(M + "speed")


def test_ranged_sorts_foes_nearest_first_with_their_distance():
    got = cons.ranged([foe("zombie", 9), foe("husk", 3)], (0.0, 64.0, 0.0))
    assert [f.type for f in got] == [M + "husk", M + "zombie"]
    assert got[0].distance == pytest.approx(3.0)


# --- healing ---------------------------------------------------------------------


def test_hurt_in_melee_splashes_healing_on_itself_it_cannot_stand_and_drink():
    use = decide(body(8), [HEAL, SPLASH_HEAL, GAPPLE], [foe("vindicator", 2)])
    assert (use.op, use.potion, use.at, use.reason) == (
        "throw", M + "strong_healing", "self", "heal")


def test_hurt_in_melee_without_a_splash_eats_a_golden_apple():
    use = decide(body(8), [HEAL, GAPPLE], [foe("vindicator", 2)])
    assert (use.op, use.item) == ("eat", M + "golden_apple")


def test_a_golden_apple_is_not_eaten_again_while_its_regeneration_runs():
    b = body(8, effects={M + "regeneration": cons.Effect(M + "regeneration", 1, 60)})
    use = decide(b, [HEAL, GAPPLE], [foe("vindicator", 2)])
    assert (use.op, use.potion) == ("drink", M + "strong_healing")


def test_hurt_with_the_foes_at_a_distance_drinks_and_keeps_the_splash():
    use = decide(body(8), [HEAL, SPLASH_HEAL], [foe("pillager", 12)])
    assert (use.op, use.potion, use.reason) == ("drink", M + "strong_healing", "heal")


def test_hurt_with_no_foe_eats_to_regenerate_rather_than_spend_a_potion():
    use = decide(body(8, food=15), [HEAL, BEEF])
    assert (use.op, use.item, use.reason) == ("eat", M + "cooked_beef", "feed")


def test_hurt_with_no_foe_and_a_full_bar_waits_for_health_to_come_back():
    assert decide(body(8, food=20, saturation=5.0), [HEAL, BEEF]) is None


def test_hurt_with_no_foe_and_no_way_to_regenerate_drinks_a_potion():
    use = decide(body(8, food=10), [REGEN])
    assert (use.op, use.potion) == ("drink", M + "regeneration")


def test_healthy_enough_to_fight_on_does_not_heal():
    assert decide(body(14), [HEAL, SPLASH_HEAL], [foe("vindicator", 2)]) is None


def test_nothing_to_heal_with_returns_nothing():
    assert decide(body(4), [BEEF], [foe("vindicator", 2)]) is None


# --- fire ------------------------------------------------------------------------


def test_burning_drinks_fire_resistance():
    use = decide(body(16, fire_ticks=60), [FIRE, HEAL])
    assert (use.op, use.potion, use.reason) == ("drink", M + "fire_resistance", "fire")


def test_fire_resistance_already_running_is_not_drunk_again():
    effects = {M + "fire_resistance": cons.Effect(M + "fire_resistance", 0, 2000)}
    assert decide(body(16, fire_ticks=60, effects=effects), [FIRE]) is None


# --- throwing at foes ------------------------------------------------------------


def _from_bot(use):
    return math.dist(use.point, (0.0, 64.0, 0.0))


def test_a_group_of_living_mobs_gets_a_harming_splash_out_of_the_bots_reach():
    foes = [foe("pillager", 6), foe("vindicator", 6.5, 1)]
    use = decide(body(), [HARM], foes)
    assert (use.op, use.potion, use.reason) == ("throw", M + "strong_harming", "harm")
    assert set(use.targets) == {f.uuid for f in foes}
    # a thrown potion reaches about 5.5 blocks; its splash reaches 4
    assert cons.THROW_SAFE <= _from_bot(use) <= cons.THROW_REACH
    assert use.fields()["at"] == {"position": [round(v, 2) for v in use.point]}


def test_no_harmful_splash_at_a_group_already_inside_the_bots_own_splash():
    # The lowest arc flies 1-1.6 blocks up all the way: a foe on the line catches it,
    # and at 3-4 blocks its splash takes the bot too (vault raid 20261124T043026Z:
    # the bot's own harming took 4 and 3).
    close = [foe("pillager", 3), foe("vindicator", 4, 0.5)]
    assert decide(body(), [HARM], close) is None
    assert decide(body(), [HARM], [foe("vindicator", 3.5)]) is None


def test_no_throw_over_a_foe_right_in_front_it_would_break_on():
    assert decide(body(), [HARM], [foe("vindicator", 1.5), foe("pillager", 6),
                                   foe("pillager", 6.5, 1)]) is None  # fmt: skip
    # another way round is clear
    use = decide(body(), [HARM], [foe("vindicator", 1.5), foe("pillager", -6),
                                   foe("pillager", -6.5, 1)])  # fmt: skip
    assert use is not None and use.point[0] < 0


def test_a_charging_brute_would_meet_the_potion_beside_the_bot_so_none_is_thrown():
    charging = foe("vindicator", 6.5, v=(-5.0, 0.0, 0.0))  # 3.5 off when it lands
    assert decide(body(), [HARM], [charging]) is None
    standing = foe("vindicator", 6.5)
    use = decide(body(), [HARM], [standing])
    assert use is not None and use.reason == "harm"


def test_a_melee_mob_of_unknown_course_is_taken_to_be_charging():
    assert decide(body(), [HARM], [foe("vindicator", 6.5, v=None)]) is None
    archers = [foe("pillager", 6, v=None), foe("pillager", 6.5, 1, v=None)]
    assert decide(body(), [HARM], archers) is not None  # they keep their distance


def test_a_brute_backing_off_is_led_to_where_it_will_be():
    leaving = foe("vindicator", 5.0, v=(3.0, 0.0, 0.0))  # 6.8 off when it lands
    use = decide(body(), [HARM], [leaving])
    assert use is not None and use.point[0] > 0
    assert cons.THROW_SAFE <= _from_bot(use) <= cons.THROW_REACH


def test_no_harmful_splash_while_a_vex_flies_near():
    # vexes dart about faster than any lead: one caught the bot's own harming
    group = [foe("pillager", 6), foe("pillager", 6.5, 1)]
    assert decide(body(), [HARM], [*group, foe("vex", -3, 2)]) is None
    assert decide(body(), [HARM], [*group, foe("vex", -12, 2)]) is not None


def test_foes_read_from_rows_carry_their_velocity():
    rows = [{"uuid": "v1", "type": M + "vindicator", "position": [5, 64, 0],
             "velocity": [-4.0, 0.0, 0.5]},
            {"uuid": "p1", "type": M + "pillager", "position": [7, 64, 0]}]  # fmt: skip
    got = {f.uuid: f for f in cons.foes_from(rows, (0.0, 64.0, 0.0))}
    assert got["v1"].velocity == (-4.0, 0.0, 0.5) and got["p1"].velocity is None
    assert cons.ranged(list(got.values()), (0.0, 64.0, 0.0))[0].velocity is not None


def test_a_group_out_of_reach_of_any_splash_is_left_for_later():
    group = [foe("pillager", 10), foe("vindicator", 10.5)]
    assert decide(body(), [HARM], group) is None


def test_no_harmful_splash_breaks_beside_a_friend():
    foes = [foe("pillager", 6), foe("vindicator", 6.5, 1)]
    b = body()
    got = cons.decide(b, items(HARM), cons.ranged(foes, b.position), {},
                      friends=[(6.0, 64.0, 2.0)])  # fmt: skip
    assert got is None


def test_harming_is_not_thrown_at_undead_it_heals():
    assert decide(body(), [HARM], [foe("zombie", 6), foe("skeleton", 6.5)]) is None


def test_undead_get_a_healing_splash_while_one_is_kept_for_the_bot():
    splash = dict(SPLASH_HEAL, count=2)
    use = decide(body(), [splash], [foe("zombie", 6), foe("husk", 6.5)])
    assert (use.op, use.potion, use.reason) == ("throw", M + "strong_healing", "harm")
    assert decide(body(), [SPLASH_HEAL], [foe("zombie", 6), foe("husk", 6.5)]) is None


def test_a_witch_shrugs_off_harming_so_it_is_not_counted():
    assert decide(body(), [HARM], [foe("witch", 6), foe("witch", 6.5)]) is None


def test_a_lone_brute_is_worth_a_harming_splash():
    use = decide(body(), [HARM], [foe("vindicator", 5.5)])
    assert use.reason == "harm"


def test_a_lone_ordinary_mob_is_not_worth_one():
    assert decide(body(), [HARM], [foe("pillager", 5.5)]) is None


def test_melee_brutes_coming_get_weakness_unless_they_have_it():
    v = foe("vindicator", 5.5)
    use = decide(body(), [WEAK], [v])
    assert (use.potion, use.reason, use.effect) == (
        M + "weakness", "weaken", M + "weakness")
    assert decide(body(), [WEAK], [v], {v.uuid: {M + "weakness"}}) is None


def test_weakness_is_wasted_on_archers():
    assert decide(body(), [WEAK], [foe("pillager", 6), foe("skeleton", 6.5)]) is None


def test_a_melee_pack_gets_slowness_and_living_packs_poison():
    pack = [foe("zombie", 6), foe("vindicator", 6.5)]
    assert decide(body(), [SLOW], pack).reason == "slow"
    living = [foe("pillager", 6), foe("vindicator", 6.5)]
    assert decide(body(), [POISON], living).reason == "poison"
    assert decide(body(), [POISON], [foe("zombie", 6), foe("husk", 6.5)]) is None
    spiders = [foe("spider", 6), foe("cave_spider", 6.5)]
    assert decide(body(), [POISON], spiders) is None


def test_harming_comes_before_weakness():
    foes = [foe("vindicator", 6), foe("vindicator", 6.5, 1)]
    assert decide(body(), [WEAK, HARM], foes).reason == "harm"


# --- buffs -----------------------------------------------------------------------


def test_a_group_coming_gets_strength_drunk_before_it_arrives():
    foes = [foe("pillager", 12), foe("vindicator", 13)]
    use = decide(body(), [STRENGTH], foes)
    assert (use.op, use.potion, use.reason) == ("drink", M + "strength", "buff")


def test_a_running_buff_is_not_drunk_again():
    effects = {M + "strength": cons.Effect(M + "strength", 0, 2000)}
    foes = [foe("pillager", 12), foe("vindicator", 13)]
    assert decide(body(effects=effects), [STRENGTH], foes) is None


def test_no_buff_is_drunk_with_a_foe_too_close_to_stand_still():
    foes = [foe("vindicator", 3), foe("pillager", 12)]
    assert decide(body(), [STRENGTH], foes) is None


def test_archers_in_the_group_get_swiftness_to_close_the_gap():
    foes = [foe("pillager", 12), foe("pillager", 13, 2)]
    assert decide(body(), [SPEED], foes).potion == M + "swiftness"


def test_a_single_weak_mob_is_no_reason_to_drink():
    assert decide(body(), [STRENGTH], [foe("zombie", 12)]) is None


# --- food ------------------------------------------------------------------------


def test_with_no_foe_a_hungry_bot_eats_its_best_plain_food_never_a_golden_apple():
    use = decide(body(20, food=10), [GAPPLE, BEEF, {"slot": 4, "item": M + "bread",
                                                     "count": 3}])  # fmt: skip
    assert (use.op, use.item) == ("eat", M + "cooked_beef")
    assert decide(body(20, food=10), [GAPPLE]) is None


def test_a_full_healthy_bot_does_not_eat():
    assert decide(body(20, food=19), [BEEF]) is None


def test_too_hungry_to_sprint_it_eats_even_with_foes_at_a_distance():
    use = decide(body(20, food=6), [BEEF], [foe("pillager", 12)])
    assert (use.op, use.reason) == ("eat", "hungry")
    assert decide(body(20, food=6), [BEEF], [foe("vindicator", 3)]) is None


# --- the fight floor -------------------------------------------------------------


def test_the_fight_breaks_off_to_heal_while_a_heal_is_held():
    assert cons.fight_floor(body(20), items(SPLASH_HEAL), 0) == cons.HEAL_AT
    assert cons.fight_floor(body(20), items(GAPPLE), 0) == cons.HEAL_AT


def test_the_floor_stays_under_the_health_left_so_a_fight_still_runs():
    assert cons.fight_floor(body(7), items(GAPPLE), 0) == 5


def test_with_no_heal_the_fight_keeps_the_callers_floor():
    assert cons.fight_floor(body(20), items(BEEF), 0) == 0
    assert cons.fight_floor(body(20), items(BEEF), 6) == 6


# --- marks -----------------------------------------------------------------------


def test_marks_remember_a_thrown_effect_on_its_targets_until_it_wears_off():
    marks = cons.Marks()
    use = cons.Use("throw", M + "splash_potion", "weaken", potion=M + "weakness",
                   at="v1", effect=M + "weakness", targets=("v1", "v2"),
                   duration=1800)  # fmt: skip
    marks.record(use, now=0.0)
    assert marks.active(now=10.0) == {"v1": {M + "weakness"}, "v2": {M + "weakness"}}
    assert marks.active(now=10_000.0) == {}


def _harm_on(*targets):
    return cons.Use("throw", M + "splash_potion", "harm", potion=M + "strong_harming",
                    point=(5.0, 64.0, 0.0), effect=M + "instant_damage",
                    targets=targets)  # fmt: skip


def test_a_splash_in_flight_marks_its_targets_until_it_has_landed():
    marks = cons.Marks()
    marks.record(_harm_on("v1"), now=0.0)
    assert marks.active(now=0.5) == {"v1": {cons.IN_FLIGHT}}
    assert marks.active(now=cons.FLIGHT_S + 0.1) == {}  # instant: no mark left


def test_no_second_splash_goes_at_a_foe_the_first_has_not_reached_yet():
    # Live run 20261124T040230Z: two harming and a weakness thrown at one vindicator in
    # 0.2 s, before the first had landed.
    vindicator = foe("vindicator", 5.5, uuid="v1")
    marks = cons.Marks()
    marks.record(_harm_on("v1"), now=0.0)
    flying = decide(body(), [HARM, WEAK], [vindicator], marks.active(now=0.2))
    assert flying is None
    landed = decide(body(), [HARM, WEAK], [vindicator], marks.active(now=2.0))
    assert landed is not None and landed.reason == "harm"  # it lived: throw again


def test_a_splash_in_flight_still_counts_against_the_undead_it_would_heal():
    pillagers = [foe("pillager", 6.0, 0.0, "p1"), foe("pillager", 6.0, 1.0, "p2")]
    assert decide(body(), [HARM], pillagers) is not None  # worth a harming alone
    group = [*pillagers, foe("zombie", 6.5, -0.5, "z1")]
    assert decide(body(), [HARM], group, {"z1": {cons.IN_FLIGHT}}) is None


def test_no_second_heal_follows_one_before_the_health_shows_it():
    # Live run 20261124T041629Z: two healing splashes on itself back to back, then a
    # golden apple straight after a third, each look still reading the old health.
    marks = cons.Marks()
    splash = cons.Use("throw", M + "splash_potion", "heal",
                      potion=M + "strong_healing", at="self")  # fmt: skip
    marks.record(splash, now=0.0)
    close = [foe("vindicator", 3)]
    soon = decide(body(8.0), [SPLASH_HEAL, GAPPLE], close, marks.active(0.3))
    assert soon is None or soon.reason != "heal"
    later = decide(body(8.0), [SPLASH_HEAL, GAPPLE], close, marks.active(2.0))
    assert later is not None and later.reason == "heal"


def test_eating_for_a_heal_marks_it_too():
    marks = cons.Marks()
    marks.record(cons.Use("eat", M + "golden_apple", "heal"), now=0.0)
    assert marks.active(0.5) == {cons.SELF: {cons.IN_FLIGHT}}


def test_a_use_goes_to_the_body_op_as_its_fields():
    splash = M + "splash_potion"
    throw = cons.Use("throw", splash, "heal", potion=M + "healing", at="self")
    assert throw.fields() == {"potion": M + "healing", "item": splash, "at": "self"}
    at_mob = cons.Use("throw", splash, "harm", potion=M + "harming", at="u1")
    assert at_mob.fields()["at"] == {"entity": "u1"}
    assert cons.Use("drink", M + "potion", "heal", potion=M + "healing").fields() == {
        "potion": M + "healing"}
    assert cons.Use("eat", M + "bread", "feed").fields() == {"item": M + "bread"}
