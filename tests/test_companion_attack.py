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
        "r2", "minecraft:ravager", (40.5, 64.0, 0.5)
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


def test_a_hunt_waits_between_waves_and_ends_with_the_match():
    assert attack.hunt(match(next_wave=80), state(), None) == attack.WAITING
    assert attack.hunt(match(state="won"), state(), None) == "won"
    assert attack.hunt(match(state="lost"), state(), None) == "lost"
    assert attack.hunt(match(state="stopped"), state(), None) == "stopped"
    assert attack.hunt({"name": "vault"}, state(), None) == attack.NO_MATCH
    teams = match()
    teams["match"]["mode"] = "teams"
    assert attack.hunt(teams, state(), None) == attack.NO_MATCH  # waves only


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
