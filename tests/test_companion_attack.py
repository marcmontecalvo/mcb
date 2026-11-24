"""An owner-commanded attack: who the target is and what ends it (pure rules)."""

import pytest

from mclab import companion_attack as attack

ZOMBIE = "minecraft:zombie"
PLAYER = "minecraft:player"
BOT = "MCBot"
OVERWORLD = "minecraft:overworld"


def entity(kind, uuid, pos, name=None):
    row = {"type": kind, "uuid": uuid, "position": list(pos)}
    if name:
        row["name"] = name
    return row


def person(name, mode="survival", *, dimension=OVERWORLD, invulnerable=False, pos=None):
    return {
        "name": name,
        "uuid": f"u-{name}",
        "position": list(pos or (30.5, 64.0, 0.5)),
        "dimension": dimension,
        "mode": mode,
        "invulnerable": invulnerable,
        "fake": name == BOT,
    }


def state(*entities, at=(0.5, 64.0, 0.5)):
    return {"body": {"position": list(at)}, "visible_entities": list(entities)}


def roster(*others):
    return [person(BOT, pos=(0.5, 64.0, 0.5)), *others]


# --- a player --------------------------------------------------------------------


def test_a_visible_player_is_found_by_name_ignoring_case():
    seen = entity(PLAYER, "u-1", (5.5, 64.0, 0.5), name="FlamingChicken")
    online = roster(person("FlamingChicken"))
    found = attack.find({"player": "flamingchicken"}, state(seen), online, BOT)
    assert found == attack.Victim(
        uuid="u-1", type=PLAYER, position=(5.5, 64.0, 0.5), name="FlamingChicken"
    )
    assert found.seen


def test_a_player_out_of_sight_is_found_where_the_roster_has_them():
    found = attack.find({"player": "Alex"}, state(), roster(person("Alex")), BOT)
    assert isinstance(found, attack.Victim) and not found.seen
    assert found.uuid is None and found.position == (30.5, 64.0, 0.5)


def test_a_player_who_logged_out_is_lost():
    assert attack.find({"player": "Alex"}, state(), roster(person("Steve")), BOT) == (
        attack.LOST
    )


def test_a_player_in_another_dimension_is_lost():
    nether = person("Alex", dimension="minecraft:the_nether")
    assert attack.find({"player": "Alex"}, state(), roster(nether), BOT) == attack.LOST


@pytest.mark.parametrize(
    "other",
    [
        person("Alex", "creative"),
        person("Alex", "spectator"),
        person("Alex", invulnerable=True),
    ],
)
def test_a_player_who_cannot_be_hurt_ends_the_attack(other):
    seen = entity(PLAYER, "u-1", (5.5, 64.0, 0.5), name="Alex")
    assert attack.find({"player": "Alex"}, state(seen), roster(other), BOT) == (
        attack.INVULNERABLE
    )


def test_without_a_roster_a_seen_player_is_fought_and_an_unseen_one_is_lost():
    seen = entity(PLAYER, "u-1", (5.5, 64.0, 0.5), name="Alex")
    found = attack.find({"player": "Alex"}, state(seen), None, BOT)
    assert isinstance(found, attack.Victim)
    assert attack.find({"player": "Alex"}, state(), None, BOT) == attack.LOST


def test_the_bot_is_never_its_own_target():
    me = entity(PLAYER, "u-0", (0.5, 64.0, 0.5), name=BOT)
    assert attack.find({"player": BOT.lower()}, state(me), roster(), BOT) == attack.LOST


# --- a mob and the nearest hostile -----------------------------------------------


def test_a_mob_is_the_nearest_of_its_type():
    far = entity(ZOMBIE, "z-far", (20.5, 64.0, 0.5))
    near = entity(ZOMBIE, "z-near", (4.5, 64.0, 0.5))
    other = entity("minecraft:cow", "c", (1.5, 64.0, 0.5))
    found = attack.find({"mob": ZOMBIE}, state(far, near, other), None, BOT)
    assert found.uuid == "z-near" and found.type == ZOMBIE


def test_no_mob_of_the_type_in_sight_is_lost():
    cow = entity("minecraft:cow", "c", (1.5, 64.0, 0.5))
    assert attack.find({"mob": ZOMBIE}, state(cow), None, BOT) == attack.LOST


def test_the_nearest_hostile_skips_animals_players_and_what_is_never_engaged():
    cow = entity("minecraft:cow", "c", (1.0, 64.0, 0.5))
    friend = entity(PLAYER, "p", (2.0, 64.0, 0.5), name="Alex")
    warden = entity("minecraft:warden", "w", (3.0, 64.0, 0.5))
    zombie = entity(ZOMBIE, "z", (9.5, 64.0, 0.5))
    found = attack.find(
        {"nearest_hostile": True}, state(cow, friend, warden, zombie), None, BOT
    )
    assert found.uuid == "z"
    assert attack.find({"nearest_hostile": True}, state(cow, friend), None, BOT) == (
        attack.LOST
    )


def test_a_chosen_target_is_followed_by_its_uuid():
    near = entity(ZOMBIE, "z-near", (4.5, 64.0, 0.5))
    far = entity(ZOMBIE, "z-far", (20.5, 64.0, 0.5))
    found = attack.find({"mob": ZOMBIE}, state(near, far), None, BOT, chosen="z-far")
    assert found.uuid == "z-far"
    assert attack.find({"mob": ZOMBIE}, state(near), None, BOT, chosen="z-far") == (
        attack.LOST
    )


# --- the running tally -----------------------------------------------------------


def test_the_tally_adds_each_fight_and_reports_the_job():
    tally = attack.Tally()
    tally.add({"kills": 1, "hits": 3, "damage": 6.0, "crits": 1, "ticks": 40})
    tally.add({"hits": 2, "damage": 4.5, "ticks": 20})
    tally.add({})
    assert tally.result("killed", "Alex") == {
        "outcome": "killed",
        "target": "Alex",
        "kills": 1,
        "hits": 5,
        "damage": 10.5,
        "crits": 1,
        "fights": 3,
    }


def test_a_tally_belongs_to_one_job():
    tally = attack.Tally(job="j-1")
    tally.add({"hits": 1})
    assert attack.Tally.for_job(tally, "j-1") is tally
    fresh = attack.Tally.for_job(tally, "j-2")
    assert fresh is not tally and fresh.hits == 0 and fresh.job == "j-2"
    assert attack.Tally.for_job(None, None).job is None


# --- an arena hunt: every wave mob, wherever it is in the bounds ----------------------


def wave_mob(uuid, pos, kind="minecraft:ravager", health=100.0):
    return {"uuid": uuid, "type": kind, "position": list(pos), "health": health}


def match(*mobs, state="running", next_wave=0, **extra):
    return {
        "name": "vault",
        "match": {"mode": "waves", "state": state, "wave_index": 0, "waves": 3,
                  "next_wave_in_ticks": next_wave, "wave_mobs": list(mobs), **extra},
    }  # fmt: skip


def test_a_hunt_takes_the_nearest_wave_mob_however_far_it_is():
    far, near = wave_mob("r1", (60.5, 64, 0.5)), wave_mob("r2", (40.5, 64, 0.5))
    found = attack.hunt(match(far, near), state(), None)
    assert (found.uuid, found.type, found.position) == (
        "r2",
        "minecraft:ravager",
        (40.5, 64.0, 0.5),
    )
    assert not found.seen  # 40 blocks off: out of sight, so the job walks to it


def test_a_hunt_sees_a_wave_mob_the_body_sees():
    mob = wave_mob("r1", (6.5, 64, 0.5))
    seen = state(entity("minecraft:ravager", "r1", (6.4, 64, 0.5)))
    found = attack.hunt(match(mob), seen, None)
    assert found.seen and found.position == (6.4, 64.0, 0.5)  # the body's own sight


def test_a_hunt_keeps_its_mob_while_it_lives_then_takes_the_next_nearest():
    a, b = wave_mob("a", (5.5, 64, 0.5)), wave_mob("b", (9.5, 64, 0.5))
    assert attack.hunt(match(a, b), state(), "b").uuid == "b"
    assert attack.hunt(match(a), state(), "b").uuid == "a"


PILLAGER, WITCH, EVOKER = (f"minecraft:{k}" for k in ("pillager", "witch", "evoker"))


def test_a_hunt_goes_for_the_casters_first():
    # Vault raid 20261124T044539Z: three deaths in wave 4, sniping the nearest pillager
    # while two witches' harming and poison (100 of the damage) and an evoker's vexes
    # and fangs went on.
    pillager = wave_mob("p", (8.5, 64, 0.5), PILLAGER)
    witch = wave_mob("w", (12.5, 64, 0.5), WITCH)
    evoker = wave_mob("e", (-15.5, 64, 0.5), EVOKER)
    assert attack.hunt(match(pillager, witch), state(), None).uuid == "w"
    assert attack.hunt(match(pillager, evoker), state(), None).uuid == "e"


def test_a_brute_already_on_the_bot_comes_before_a_far_caster():
    vindicator = wave_mob("v", (2.5, 64, 0.5), "minecraft:vindicator")
    evoker = wave_mob("e", (-15.5, 64, 0.5), EVOKER)
    assert attack.hunt(match(vindicator, evoker), state(), None).uuid == "v"


def test_a_chosen_mob_gives_way_only_to_a_worse_threat_nearer_by_its_weight():
    pillager = wave_mob("p", (8.5, 64, 0.5), PILLAGER)
    witch = wave_mob("w", (12.5, 64, 0.5), WITCH)
    assert attack.hunt(match(pillager, witch), state(), "p").uuid == "w"
    near = wave_mob("p2", (4.5, 64, 0.5), PILLAGER)
    assert attack.hunt(match(near, witch), state(), "w").uuid == "w"  # kept


def test_a_hunt_waits_between_waves_and_ends_with_the_match():
    assert attack.hunt(match(next_wave=80), state(), None) == attack.WAITING
    assert attack.hunt(match(state="won"), state(), None) == "won"
    assert attack.hunt(match(state="lost"), state(), None) == "lost"
    assert attack.hunt(match(state="stopped"), state(), None) == "stopped"
    assert attack.hunt({"name": "vault"}, state(), None) == attack.NO_MATCH
    odd = match()
    odd["match"]["mode"] = "tag"
    assert attack.hunt(odd, state(), None) == attack.NO_MATCH


def fighter(name, team, pos, alive=True):
    return {"uuid": f"u-{name}", "name": name, "team": team, "alive": alive,
            "position": list(pos)}  # fmt: skip


def contest(*people, mobs=(), mode="teams"):
    status = match(*mobs, participants=list(people))
    status["match"]["mode"] = mode
    return status


def test_a_team_match_hunts_the_nearest_live_enemy_never_a_teammate():
    me = fighter("MCBot", "red", (0.5, 64, 0.5))
    mate = fighter("Ann", "red", (2.5, 64, 0.5))
    far = fighter("Bob", "blue", (30.5, 64, 0.5))
    near = fighter("Cat", "blue", (10.5, 64, 0.5))
    dead = fighter("Dan", "blue", (3.5, 64, 0.5), alive=False)
    found = attack.hunt(contest(me, mate, far, near, dead), state(), None, "MCBot")
    assert (found.uuid, found.type, found.name) == ("u-Cat", PLAYER, "Cat")
    assert not found.seen


def test_a_team_match_with_waves_weighs_enemies_and_mobs_together():
    me = fighter("mcbot", "red", (0.5, 64, 0.5))  # names match in any case
    enemy = fighter("Bob", "blue", (20.5, 64, 0.5))
    mob = wave_mob("z", (5.5, 64, 0.5), "minecraft:zombie")
    match = contest(me, enemy, mobs=[mob])
    assert attack.hunt(match, state(), None, "MCBot").uuid == "z"
    seen = state(entity(PLAYER, "u-Bob", (19.5, 64, 0.5), name="Bob"))
    found = attack.hunt(contest(me, enemy), seen, None, "MCBot")
    assert found.seen and found.position == (19.5, 64.0, 0.5)


def test_a_race_or_a_bot_outside_the_teams_hunts_only_wave_mobs():
    me = fighter("MCBot", "red", (0.5, 64, 0.5))
    rival = fighter("Bob", "blue", (2.5, 64, 0.5))
    mob = wave_mob("z", (30.5, 64, 0.5), "minecraft:zombie")
    race = contest(me, rival, mobs=[mob], mode="race")
    assert attack.hunt(race, state(), None, "MCBot").uuid == "z"
    assert attack.hunt(contest(rival, mobs=[mob]), state(), None, "MCBot").uuid == "z"
    mate = fighter("Ann", "red", (1.5, 64, 1.5))
    assert attack.hunt(contest(me, mate), state(), None, "MCBot") == attack.WAITING


def test_the_foes_of_a_match_are_its_wave_mobs_and_live_enemies():
    me = fighter("MCBot", "red", (0.5, 64, 0.5))
    enemy = fighter("Bob", "blue", (9.5, 64, 0.5))
    offline = {"uuid": "x", "name": "Eve", "team": "blue", "alive": True}
    mob = wave_mob("z", (5.5, 64, 0.5), "minecraft:zombie")
    rows = attack.foes(contest(me, enemy, offline, mobs=[mob])["match"], "MCBot")
    assert [r["uuid"] for r in rows] == ["z", "u-Bob"]
    assert rows[1]["type"] == PLAYER and rows[1]["name"] == "Bob"
    assert attack.foes({"mode": "waves", "wave_mobs": [mob]}, "MCBot") == [mob]


def test_the_arena_here_is_the_one_whose_bounds_hold_the_bot():
    def summary(name, lo, hi, match_state="running"):
        return {"name": name, "dimension": OVERWORLD, "match_state": match_state,
                "bounds": {"min": list(lo), "max": list(hi)}}  # fmt: skip

    listing = {"arenas": [summary("duel", (100, 60, 100), (132, 72, 138)),
                          summary("vault", (-20, 60, -20), (20, 74, 26))]}  # fmt: skip
    assert attack.arena_here(listing, (0.5, 64.0, 0.5)) == "vault"
    assert attack.arena_here(listing, (20.9, 64.0, 0.5)) == "vault"  # the max cell
    assert attack.arena_here(listing, (21.5, 64.0, 0.5)) is None
    assert attack.arena_here({"arenas": []}, (0.5, 64.0, 0.5)) is None


# --- two enemy players: commit to one, leave it only for a clear reason ---------------
# Live series 20261124T164259Z: the dad and son on red against the bot. The bot held the
# one it first picked for the whole match (a kiter it never reached) while the other, 4
# to 9 blocks off, did the damage; nothing in the pick could ever say "leave it".


def duo(ann, bob, held_pick=None, *, held=None, stuck=frozenset(), me=(0.5, 64, 0.5)):
    """A teams match with the bot on blue and Ann and Bob on red, ``ann`` and ``bob``
    blocks off along the x axis; the foe found, by uuid."""
    people = [
        fighter("MCBot", "blue", me),
        fighter("Ann", "red", (me[0] + ann, 64, 0.5)),
        fighter("Bob", "red", (me[0] - bob, 64, 0.5)),
    ]
    found = attack.hunt(
        contest(*people), state(at=me), held_pick, "MCBot", held=held, stuck=stuck
    )
    return found.uuid


def test_with_nothing_chosen_the_nearer_enemy_is_picked():
    assert duo(10, 14) == "u-Ann" and duo(14, 10) == "u-Bob"


def test_a_slightly_nearer_enemy_does_not_take_the_target():
    assert duo(12, 10, "u-Ann", held=200) == "u-Ann"
    assert duo(10, 12, "u-Bob", held=200) == "u-Bob"


def test_the_pick_does_not_flip_flop_as_the_nearer_enemy_changes_every_look():
    chosen = "u-Ann"
    for look in range(40):  # two players circling: first one nearer, then the other
        ann, bob = (9.0, 11.0) if look % 2 else (11.0, 9.0)
        chosen = duo(ann, bob, chosen, held=look * 60)
    assert chosen == "u-Ann"


def test_an_enemy_pressing_in_comes_before_a_chosen_one_further_than_keep_far():
    # the same rule as the fight op's for a hostile mob: the one in the body's face
    assert duo(14, 3.5, "u-Ann", held=0) == "u-Bob"
    assert duo(14, 5.5, "u-Ann", held=0) == "u-Ann"  # near, not yet pressing
    assert duo(5.0, 3.5, "u-Ann", held=0) == "u-Ann"  # the chosen one is close too


def test_a_much_nearer_enemy_takes_the_target_after_the_hold():
    assert duo(20, 8, "u-Ann", held=attack.HOLD_TICKS) == "u-Bob"
    assert duo(20, 11, "u-Ann", held=attack.HOLD_TICKS) == "u-Ann"  # not twice as near


def test_a_target_just_picked_is_held_for_a_while():
    assert duo(20, 8, "u-Ann", held=attack.HOLD_TICKS - 1) == "u-Ann"
    assert duo(20, 8, "u-Ann", held=None) == "u-Bob"  # no record of the pick: free


def test_a_chosen_enemy_the_bot_cannot_get_at_is_left_for_the_other():
    assert duo(20, 40, "u-Ann", held=0, stuck={"u-Ann"}) == "u-Bob"
    # both out of reach: stay with the one it has rather than swap between them
    assert duo(20, 40, "u-Ann", held=0, stuck={"u-Ann", "u-Bob"}) == "u-Ann"


def test_a_blocked_enemy_is_not_taken_for_being_much_nearer():
    assert duo(20, 8, "u-Ann", held=attack.HOLD_TICKS, stuck={"u-Bob"}) == "u-Ann"


def test_the_chosen_enemy_dying_hands_over_to_the_other_at_once():
    people = [
        fighter("MCBot", "blue", (0.5, 64, 0.5)),
        fighter("Ann", "red", (9.5, 64, 0.5), alive=False),
        fighter("Bob", "red", (-30.5, 64, 0.5)),
    ]
    found = attack.hunt(contest(*people), state(), "u-Ann", "MCBot", held=0)
    assert found.uuid == "u-Bob"


def test_wave_mobs_keep_the_old_rule_only_a_worse_threat_displaces_the_chosen():
    far = wave_mob("z1", (20.5, 64, 0.5), ZOMBIE)
    near = wave_mob("z2", (6.5, 64, 0.5), ZOMBIE)
    kept = attack.hunt(match(far, near), state(), "z1", held=attack.HOLD_TICKS)
    assert kept.uuid == "z1"
