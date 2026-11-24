"""The session's attack job: find the target, close in, fight, find it again (fakes)."""

import pytest
from test_companion_session import (  # noqa: F401 - fixtures
    SWORD,
    ZOMBIE,
    _day_row,
    _logged,
    _mob,
    _sword,
    guard,
    make,
)

from mclab import companion_session as cs
from mclab.build_runner import Result
from mclab.companion_defense import Action
from mclab.companion_intents import Actor
from mclab.follow import PLAYER

OWNER = Actor("chat", name="Marc", uuid="u-owner")
BOW = "minecraft:bow"
ARROW = "minecraft:arrow"
ROSTER_BOT = {"name": "MCBot", "dimension": "minecraft:overworld", "mode": "survival"}


def _person(name, mode="survival", **extra):
    return {
        "name": name,
        "uuid": f"u-{name}",
        "position": [40.5, 64.0, 0.5],
        "dimension": "minecraft:overworld",
        "mode": mode,
        "invulnerable": False,
        **extra,
    }


def _stack(item, count=1):
    return {"slot": 0, "item": item, "count": count}


def _foe(name="Alex", pos=(5.5, 64.0, 0.5)):
    return {"type": PLAYER, "name": name, "uuid": f"u-{name}", "position": list(pos)}


def _order(weapon=None, **target):
    return {"target": target, "weapon": weapon, "when": "now"}


def _look(bridge, *entities, health=20.0, inv=(), tick=10, **body):
    """The next observation the attack loop reads (``body``: more fields, food...)."""
    row = _day_row(*entities, health=health, inv=inv, tick=tick)
    row["body"].update(body)
    bridge.rows.append(row)


def _online(bridge, *people):
    bridge.players.append({"players": [ROSTER_BOT, *people]})


@pytest.fixture
def fighter(guard):  # noqa: F811
    session, bridge, _ = guard()
    session.reported = []
    return session, bridge


def _attack(session, weapon=None, **target):
    return session.attack(
        _order(weapon, **target), OWNER, lambda **kw: session.reported.append(kw)
    )


def test_attack_a_player_fights_them_commanded_with_the_weapon_that_suits(fighter):
    session, bridge = fighter
    _look(bridge, _foe(), inv=_sword())
    _online(bridge, _person("Alex"))
    session.body.fights = [
        Result(True, None, {"kills": 1, "killed": True, "hits": 4, "damage": 18.0,
                            "crits": 2, "ticks": 50})
    ]  # fmt: skip
    result = _attack(session, player="Alex")
    assert result == {
        "outcome": "killed", "target": "Alex", "kills": 1, "hits": 4, "damage": 18.0,
        "crits": 2, "fights": 1,
    }  # fmt: skip
    ((_, fields),) = _logged(session.body, "fight")
    assert fields["entity"] == "u-Alex" and fields["allow_player"] is True
    assert fields["weapon"] == SWORD and fields["tactic"]["style"] == "melee"
    assert fields["floor"] == session.defense_policy.floor_health
    assert fields["max_ticks"] == cs.FIGHT_TICKS and fields.get("anchor") is None
    assert fields["radius"] >= 8  # the target is 5 blocks away: all of it in reach


def test_attack_throws_a_loyalty_trident_as_the_defence_would(fighter):
    session, bridge = fighter
    trident = {**_stack("minecraft:trident"), "enchantments": {"minecraft:loyalty": 3}}
    rows = [trident, {**_stack(SWORD), "slot": 1}]
    skeleton = _mob("s1", (12.5, 64.0, 0.5), "minecraft:skeleton")
    _look(bridge, skeleton, inv=rows)
    _look(bridge, skeleton, inv=rows)
    kill = Result(True, None, {"kills": 1, "killed": True, "hits": 1})
    session.body.fights = [kill, kill]
    assert _attack(session, mob="minecraft:skeleton")["outcome"] == "killed"
    named = _attack(session, "minecraft:trident", mob="minecraft:skeleton")
    assert named["outcome"] == "killed"
    fights = _logged(session.body, "fight")
    assert [f["tactic"]["style"] for _, f in fights] == ["throw", "throw"]


RAVAGER = "minecraft:ravager"


def _match(*mobs, state="running", next_wave=0, cleared=0):
    """An arena_status reply for the vault's waves match; a mob is (uuid, position)
    or (uuid, position, type[, velocity]), a ravager when no type is given."""
    rows = [{"uuid": m[0], "type": m[2] if len(m) > 2 else RAVAGER,
             "position": list(m[1]), "health": 100.0} for m in mobs]  # fmt: skip
    for row, m in zip(rows, mobs, strict=True):
        if len(m) > 3:
            row["velocity"] = list(m[3])
    data = {"name": "vault", "match_state": state,
            "match": {"mode": "waves", "state": state, "wave_index": cleared,
                      "waves": 2, "next_wave_in_ticks": next_wave, "killed": cleared,
                      "wave_mobs": rows}}  # fmt: skip
    return Result(True, None, data)


def _arena(session, *replies, listing=None):
    """The body answers arena_status with ``replies`` in turn (and arena_list)."""
    queue = list(replies)
    session.body.arena_status = lambda name: (
        session.body.log.append(("arena_status", name)) or queue.pop(0)
    )
    session.body.arena_list = lambda: Result(True, None, listing or {"arenas": []})


def test_an_arena_hunt_finds_every_wave_mob_wherever_it_is_and_ends_with_the_match(
    fighter,
):
    session, bridge = fighter
    far, near = ("r1", (40.5, 64.0, 0.5)), ("r1", (6.5, 64.0, 0.5))
    _arena(session, _match(far), _match(near), _match(next_wave=60, cleared=1),
           _match(state="won", cleared=2))  # fmt: skip
    _look(bridge, inv=_sword())  # 40 blocks off: out of sight, walk to it
    _look(bridge, {"type": RAVAGER, "uuid": "r1", "position": list(near[1])},
          inv=_sword())  # fmt: skip
    _look(bridge, inv=_sword())  # between waves: wait
    _look(bridge, inv=_sword())
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True, "hits": 9})]

    result = _attack(session, arena="vault")

    assert result["outcome"] == "won" and result["target"] == "vault"
    assert (result["kills"], result["hits"], result["fights"]) == (1, 9, 1)
    assert result["waves_cleared"] == 2
    walks = [e for e in session.body.log if e[0] == "walk_to"]
    assert walks and walks[0][1][0] == 40  # toward the far ravager, whatever the range
    ((_, fields),) = _logged(session.body, "fight")
    assert fields["entity"] == "r1" and not fields["allow_player"]
    assert fields["floor"] == 0  # the match is lost only by dying: no retreat from it
    assert fields["tactic"]["style"] != "avoid"


def test_an_arena_hunt_fights_a_wave_mob_in_reach_the_short_sensor_misses(fighter):
    """A pillager shoots from 15 blocks: past the body's 8-block entity sensor, but
    the arena's status places it this look and the fight op tracks it by uuid. A
    walk to where it stood would only be shot at all the way (the rung-1 deaths)."""
    session, bridge = fighter
    pillager = ("p1", (15.5, 64.0, 0.5))
    _arena(session, _match(pillager), _match(state="won", cleared=2))
    _look(bridge, inv=_sword())  # not in visible_entities
    _look(bridge, inv=_sword())
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True, "hits": 3})]

    result = _attack(session, arena="vault")

    assert result["outcome"] == "won"
    assert not [e for e in session.body.log if e[0] in ("walk_far", "walk_to")]
    ((_, fields),) = _logged(session.body, "fight")
    assert fields["entity"] == "p1" and fields["radius"] == cs.defense.FIGHT_RADIUS_MAX


def test_each_fight_of_an_attack_is_reported_as_a_step(fighter):
    """Which weapon met which mob, and what hurt the bot: the job's tally sums the
    fights, and a death leaves no result at all, so each fight is a step of its own."""
    session, bridge = fighter
    pillager = ("p1", (10.5, 64.0, 0.5))
    _arena(session, _match(pillager), _match(state="won", cleared=2))
    _look(bridge, inv=_sword())
    _look(bridge, inv=_sword())
    session.body.fights = [Result(True, None, {
        "kills": 1, "killed": True, "hits": 3, "style": "melee", "health": 14.0,
        "hurt_by": {"minecraft:arrow": 6.0}, "inventory": [{"item": "x"}],
    })]  # fmt: skip

    _attack(session, arena="vault")

    steps = [r["step"] for r in session.reported if "step" in r]
    assert steps == [{
        "action": "fight", "weapon": SWORD, "target_type": RAVAGER, "ok": True,
        "reason": None, "kills": 1, "hits": 3, "style": "melee", "health": 14.0,
        "hurt_by": {"minecraft:arrow": 6.0},
    }]  # fmt: skip - never the inventory


def _potion(slot, name, effect, item="splash_potion", amplifier=1):
    return {"slot": slot, "item": "minecraft:" + item, "count": 1,
            "potion": "minecraft:" + name,
            "effects": [{"id": "minecraft:" + effect, "amplifier": amplifier,
                         "duration": 1}]}  # fmt: skip


SPLASH_HEAL = _potion(5, "strong_healing", "instant_health")
HARM = _potion(6, "strong_harming", "instant_damage")
KILL = Result(True, None, {"kills": 1, "killed": True, "hits": 3})


def _uses(session):
    return [use for _, use in _logged(session.body, "consume")]


def test_a_hurt_hunter_splashes_healing_on_itself_then_fights_on(fighter):
    session, bridge = fighter
    v = ("v1", (2.5, 64.0, 0.5))
    seen = {"type": RAVAGER, "uuid": "v1", "position": list(v[1])}
    _arena(session, _match(v), _match(v), _match(state="won", cleared=2))
    _look(bridge, seen, inv=[*_sword(), SPLASH_HEAL], health=8.0)
    _look(bridge, seen, inv=_sword(), health=15.0)
    _look(bridge, inv=_sword())
    session.body.fights = [KILL]

    assert _attack(session, arena="vault")["outcome"] == "won"

    (use,) = _uses(session)
    assert (use.op, use.at, use.reason) == ("throw", "self", "heal")
    steps = [r["step"] for r in session.reported if "step" in r]
    assert [s["action"] for s in steps] == ["throw", "fight"]
    assert steps[0]["ok"] is True and steps[0]["potion"] == "minecraft:strong_healing"


def test_a_hunt_does_not_heal_again_before_its_health_shows_the_first(fighter):
    session, bridge = fighter
    v = ("v1", (2.5, 64.0, 0.5))
    seen = {"type": RAVAGER, "uuid": "v1", "position": list(v[1])}
    _arena(session, _match(v), _match(v), _match(state="won", cleared=2))
    _look(bridge, seen, inv=[*_sword(), SPLASH_HEAL, SPLASH_HEAL], health=8.0)
    _look(bridge, seen, inv=[*_sword(), SPLASH_HEAL], health=8.0)  # not shown yet
    _look(bridge, inv=_sword())
    session.body.fights = [KILL]

    _attack(session, arena="vault")

    assert [u.reason for u in _uses(session)] == ["heal"]


def test_a_hunt_holding_a_heal_breaks_off_at_the_heal_floor_and_heals(fighter):
    """No floor in an arena means fighting to the death only once nothing heals:
    with a heal held the fight stops at HEAL_AT, the bot heals, the hunt goes on."""
    session, bridge = fighter
    v = ("v1", (2.5, 64.0, 0.5))
    seen = {"type": RAVAGER, "uuid": "v1", "position": list(v[1])}
    _arena(session, _match(v), _match(v), _match(v), _match(state="won", cleared=2))
    _look(bridge, seen, inv=[*_sword(), SPLASH_HEAL], health=20.0)
    _look(bridge, seen, inv=[*_sword(), SPLASH_HEAL], health=9.0)
    _look(bridge, seen, inv=_sword(), health=17.0)
    _look(bridge, inv=_sword())
    session.body.fights = [Result(False, "low_health", {"ticks": 40}), KILL]

    assert _attack(session, arena="vault")["outcome"] == "won"

    floors = [f["floor"] for _, f in _logged(session.body, "fight")]
    assert floors == [cs.consumables.HEAL_AT, 0]
    assert [u.reason for u in _uses(session)] == ["heal"]


def test_a_hunt_splashes_harming_on_a_group_at_range_before_closing_in(fighter):
    session, bridge = fighter
    a = ("a", (6.5, 64.0, 0.5), "minecraft:pillager")
    b = ("b", (7.0, 64.0, 1.5), RAVAGER, (0.0, 0.0, 0.0))  # standing: the status says
    _arena(session, _match(a, b), _match(a, b), _match(state="won", cleared=2))
    _look(bridge, inv=[*_sword(), HARM])
    _look(bridge, inv=_sword())
    _look(bridge, inv=_sword())
    session.body.fights = [KILL]

    _attack(session, arena="vault")

    (use,) = _uses(session)
    assert (use.op, use.reason, use.potion) == (
        "throw", "harm", "minecraft:strong_harming")
    assert set(use.targets) == {"a", "b"}
    assert "position" in use.fields()["at"]  # a point short of them: out of its reach


def test_a_hunt_throws_no_harming_at_a_ravager_the_status_shows_charging(fighter):
    session, bridge = fighter
    a = ("a", (6.5, 64.0, 0.5), "minecraft:pillager")
    b = ("b", (7.0, 64.0, 1.5), RAVAGER, (-6.0, 0.0, -1.0))  # on the bot in 0.6 s
    _arena(session, _match(a, b), _match(state="won", cleared=2))
    _look(bridge, inv=[*_sword(), HARM])
    _look(bridge, inv=_sword())
    session.body.fights = [KILL]

    _attack(session, arena="vault")

    assert _uses(session) == []


def test_a_use_the_body_refuses_is_not_tried_again_at_once(fighter):
    session, bridge = fighter
    still = (0.0, 0.0, 0.0)
    a = ("a", (6.5, 64.0, 0.5), RAVAGER, still)
    b = ("b", (7.0, 64.0, 1.5), RAVAGER, still)
    _arena(session, _match(a, b), _match(a, b), _match(state="won", cleared=2))
    _look(bridge, inv=[*_sword(), HARM])
    _look(bridge, inv=[*_sword(), HARM])
    _look(bridge, inv=_sword())
    session.body.uses = [Result(False, "out_of_range", {})]

    _attack(session, arena="vault")

    assert len(_uses(session)) == 1
    assert len(_logged(session.body, "fight")) == 2  # it fought on both looks


def test_a_hunt_walks_toward_an_unreachable_mob_only_briefly(fighter):
    # Live run 20261124T040230Z: after an unreachable fight the walk toward the
    # vindicator ran 29 s while arrows took the bot from 10.6 to dead, no heal tried.
    session, bridge = fighter
    v = ("v1", (2.5, 64.0, 0.5))
    seen = {"type": RAVAGER, "uuid": "v1", "position": list(v[1])}
    _arena(session, _match(v), _match(v), _match(state="won", cleared=2))
    _look(bridge, seen, inv=_sword())
    _look(bridge, seen, inv=_sword())
    _look(bridge, inv=_sword())
    session.body.fights = [Result(False, "unreachable", {}), KILL]

    _attack(session, arena="vault")

    names = [entry[0] for entry in session.body.log]
    at = names.index("walk_to")
    assert session.body.log[at - 1] == ("walk_wall", cs.ARENA_WALK_S)
    assert names[at + 1] == "walk_wall_end"
    assert cs.ARENA_WALK_S <= 5.0  # the next look comes in seconds: heal, fight back


def test_a_hunt_chases_a_far_mob_in_short_walks(fighter):
    session, bridge = fighter
    far = ("p1", (30.5, 64.0, 0.5))
    _arena(session, _match(far), _match(state="won", cleared=2))
    _look(bridge, inv=_sword())
    _look(bridge, inv=_sword())

    _attack(session, arena="vault")

    names = [entry[0] for entry in session.body.log]
    assert "walk_far" not in names  # its legs would add up past the wall
    at = names.index("walk_to")
    assert session.body.log[at - 1] == ("walk_wall", cs.ARENA_WALK_S)


def test_between_waves_a_hungry_hunter_eats(fighter):
    session, bridge = fighter
    bread = {"slot": 3, "item": "minecraft:bread", "count": 8}
    _arena(session, _match(next_wave=60, cleared=1), _match(state="won", cleared=2))
    _look(bridge, inv=[*_sword(), bread], food=9)
    _look(bridge, inv=[*_sword(), bread])

    _attack(session, arena="vault")

    (use,) = _uses(session)
    assert (use.op, use.item, use.reason) == ("eat", "minecraft:bread", "feed")


def test_the_guard_heals_before_it_fights_and_breaks_off_to_heal(guard):  # noqa: F811
    session, bridge, _ = guard()
    zombie = _mob(pos=(2.5, 64.0, 0.5))
    bridge.samples.append(_day_row(zombie, inv=[*_sword(), SPLASH_HEAL], health=7.0))
    out = session.defend(session.threat())
    assert (out["action"], out["purpose"], out["ok"]) == ("throw", "heal", True)
    assert not _logged(session.body, "fight")

    bridge.samples.append(
        _day_row(zombie, inv=[*_sword(), SPLASH_HEAL], health=20.0, tick=11)
    )
    session.defend(session.threat())
    ((_, fields),) = _logged(session.body, "fight")
    assert fields["floor"] == max(
        cs.consumables.HEAL_AT, int(session.defense_policy.floor_health)
    )


def test_an_arena_hunt_stands_down_the_guard_while_its_match_runs(fighter):
    session, bridge = fighter
    seen = []
    _arena(session, _match(("r1", (6.5, 64.0, 0.5))), _match(state="lost"))
    _look(bridge, {"type": RAVAGER, "uuid": "r1", "position": [6.5, 64.0, 0.5]},
          inv=_sword(), health=3.0)  # fmt: skip
    _look(bridge, inv=_sword(), health=3.0)

    def fight(**fields):
        seen.append(session.threat().kind)  # a guarded poll mid-fight
        return Result(True, None, {"ticks": 200})

    session.body.fight = fight
    assert _attack(session, arena="vault")["outcome"] == "lost"
    assert seen == ["none"]  # the hunt is the defence: no escape, shelter or burrow
    assert not session.hunting  # the guard is back once the job ends


def test_an_arena_hunt_with_no_arena_named_takes_the_one_the_bot_stands_in(fighter):
    session, bridge = fighter
    box = {"name": "duel", "dimension": "minecraft:overworld", "match_state": "running",
           "bounds": {"min": [-10, 60, -10], "max": [10, 70, 10]}}  # fmt: skip
    _arena(session, _match(state="won"), listing={"arenas": [box]})
    _look(bridge, inv=_sword())
    assert _attack(session, arena=True)["target"] == "duel"
    assert ("arena_status", "duel") in session.body.log


def test_an_arena_hunt_outside_any_arena_or_match_fails_plainly(fighter):
    session, bridge = fighter
    _arena(session)
    _look(bridge, inv=_sword())
    with pytest.raises(cs.JobFailed, match="not in an arena"):
        _attack(session, arena=True)
    _arena(session, Result(True, None, {"name": "vault", "match_state": "none"}))
    _look(bridge, inv=_sword())
    with pytest.raises(cs.JobFailed, match="no wave match"):
        _attack(session, arena="vault")
    _arena(session, Result(False, "unknown_arena", {}))
    _look(bridge, inv=_sword())
    with pytest.raises(cs.JobFailed, match="unknown_arena"):
        _attack(session, arena="vault")


def test_attack_a_mob_never_allows_a_player_target(fighter):
    session, bridge = fighter
    _look(bridge, _mob(), inv=_sword())
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True, "hits": 2})]
    assert _attack(session, mob=ZOMBIE)["outcome"] == "killed"
    ((_, fields),) = _logged(session.body, "fight")
    assert fields["entity"] == "z1" and not fields["allow_player"]


def test_attack_the_nearest_hostile_picks_it_and_follows_that_one(fighter):
    session, bridge = fighter
    near, far = _mob("near", (4.5, 64.0, 0.5)), _mob("far", (9.5, 64.0, 0.5))
    cow = _mob("cow", (1.5, 64.0, 0.5), "minecraft:cow")
    _look(bridge, far, cow, near, inv=_sword())
    _look(bridge, far, near, inv=_sword())
    session.body.fights = [
        Result(True, None, {"hits": 1, "ticks": 200}),
        Result(True, None, {"kills": 1, "killed": True, "hits": 1}),
    ]
    assert _attack(session, nearest_hostile=True)["outcome"] == "killed"
    assert [f["entity"] for _, f in _logged(session.body, "fight")] == ["near", "near"]


def test_attack_walks_to_a_player_out_of_sight_and_closes_in_before_it_fights(fighter):
    session, bridge = fighter
    _look(bridge, inv=_sword())  # nobody in sight: the roster says where they are
    _online(bridge, _person("Alex"))
    _look(bridge, _foe(pos=(40.5, 64.0, 0.5)), inv=_sword())  # in sight, far
    _look(bridge, _foe(pos=(6.5, 64.0, 0.5)), inv=_sword())
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True})]
    assert _attack(session, player="Alex")["outcome"] == "killed"
    walks = _logged(session.body, "walk_far")
    assert [w[1] for w in walks] == [(40, 64, 0), (40, 64, 0)]
    assert len(_logged(session.body, "fight")) == 1


def test_attack_chases_again_when_a_fight_ends_with_the_target_alive(fighter):
    session, bridge = fighter
    for _ in range(3):
        _look(bridge, _foe(), inv=_sword())
    _online(bridge, _person("Alex"))
    session.body.fights = [
        Result(True, None, {"hits": 2, "damage": 4.0, "ticks": 200}),
        Result(False, "timeout", {"hits": 1, "damage": 2.0}),
        Result(True, None, {"kills": 1, "killed": True, "hits": 3, "damage": 12.0}),
    ]
    result = _attack(session, player="Alex")
    assert (result["outcome"], result["hits"], result["damage"], result["fights"]) == (
        "killed", 6, 18.0, 3,
    )  # fmt: skip


def test_attack_a_player_who_logs_out_ends_lost(fighter):
    session, bridge = fighter
    _look(bridge, _foe(), inv=_sword())
    _look(bridge, inv=_sword())
    _online(bridge, _person("Alex"))
    _online(bridge)  # gone
    session.body.fights = [Result(True, None, {"hits": 2})]
    result = _attack(session, player="Alex")
    assert (result["outcome"], result["hits"]) == ("lost", 2)


@pytest.mark.parametrize("mode", ["creative", "spectator"])
def test_attack_a_player_who_cannot_be_hurt_ends_without_a_fight(fighter, mode):
    session, bridge = fighter
    _look(bridge, _foe(), inv=_sword())
    _online(bridge, _person("Alex", mode))
    assert _attack(session, player="Alex")["outcome"] == "invulnerable"
    assert not _logged(session.body, "fight")


def test_attack_ends_invulnerable_when_the_body_says_so(fighter):
    session, bridge = fighter
    _look(bridge, _foe(), inv=_sword())
    _online(bridge, _person("Alex"))
    session.body.fights = [Result(False, "invulnerable", {"hits": 1})]
    result = _attack(session, player="Alex")
    assert (result["outcome"], result["hits"]) == ("invulnerable", 1)


def test_attack_ends_at_the_health_floor_and_never_starts_below_it(fighter):
    session, bridge = fighter
    floor = session.defense_policy.floor_health
    _look(bridge, _foe(), inv=_sword(), health=floor)
    _online(bridge, _person("Alex"))
    assert _attack(session, player="Alex")["outcome"] == "low_health"
    assert not _logged(session.body, "fight")
    _look(bridge, _foe(), inv=_sword())
    session.body.fights = [Result(False, "low_health", {"hits": 3, "damage": 6.0})]
    result = _attack(session, player="Alex")
    assert (result["outcome"], result["hits"]) == ("low_health", 3)


def test_attack_ends_when_the_bodys_fight_was_cancelled_by_stop(fighter):
    session, bridge = fighter
    _look(bridge, _foe(), inv=_sword())
    _online(bridge, _person("Alex"))
    session.body.fights = [Result(False, "cancelled:player_stop", {})]
    with pytest.raises(cs.JobCancelled):
        _attack(session, player="Alex")


def test_attack_with_a_named_weapon_that_is_not_held_fails_plainly(fighter):
    session, bridge = fighter
    _look(bridge, _foe(), inv=_sword())
    _online(bridge, _person("Alex"))
    with pytest.raises(cs.JobFailed, match="bow"):
        _attack(session, BOW, player="Alex")
    _look(bridge, _foe(), inv=[_stack(BOW)])  # a bow with no arrows is no weapon
    with pytest.raises(cs.JobFailed, match="arrows"):
        _attack(session, BOW, player="Alex")
    assert not _logged(session.body, "fight")


def test_attack_with_a_named_bow_snipes(fighter):
    session, bridge = fighter
    _look(bridge, _foe(), inv=[_stack(BOW), _stack(ARROW, 8), _stack(SWORD)])
    _online(bridge, _person("Alex"))
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True})]
    _attack(session, BOW, player="Alex")
    ((_, fields),) = _logged(session.body, "fight")
    assert fields["tactic"]["style"] == "snipe" and fields["weapon"] == BOW
    assert fields["tactic"]["melee"] == SWORD


def test_attack_with_nothing_to_wield_uses_the_fists(fighter):
    session, bridge = fighter
    _look(bridge, _foe())
    _online(bridge, _person("Alex"))
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True})]
    _attack(session, player="Alex")
    ((_, fields),) = _logged(session.body, "fight")
    assert fields["tactic"] == {"style": "melee", "crit": False, "sweep": False}
    assert fields["weapon"] is None


def test_attack_refuses_a_target_that_is_never_engaged(fighter):
    session, bridge = fighter
    _look(bridge, _mob("w1", kind="minecraft:warden"), inv=_sword())
    with pytest.raises(cs.JobFailed, match="warden"):
        _attack(session, mob="minecraft:warden")
    assert not _logged(session.body, "fight")


def test_attack_a_mob_not_in_sight_ends_lost(fighter):
    session, bridge = fighter
    _look(bridge, inv=_sword())
    assert _attack(session, mob=ZOMBIE)["outcome"] == "lost"
    _look(bridge, inv=_sword())
    assert _attack(session, nearest_hostile=True)["outcome"] == "lost"


def test_attack_fails_plainly_when_the_body_may_not_hit_that(fighter):
    session, bridge = fighter
    _look(bridge, _mob("v1", kind="minecraft:villager"), inv=_sword())
    session.body.fights = [Result(False, "not_allowed", {})]
    with pytest.raises(cs.JobFailed, match="may not"):
        _attack(session, mob="minecraft:villager")


def test_attack_without_a_roster_still_fights_a_player_it_can_see(fighter):
    session, bridge = fighter
    bridge.players = RuntimeError("no /players on this body")
    _look(bridge, _foe(), inv=_sword())
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True})]
    assert _attack(session, player="Alex")["outcome"] == "killed"
    _look(bridge, inv=_sword())
    assert _attack(session, player="Alex")["outcome"] == "lost"


def test_attack_keeps_trying_when_no_walk_reaches_the_target(fighter):
    # no failure counter ends it: only a kill, a stop, the floor or losing them does
    session, bridge = fighter
    for _ in range(6):
        _look(bridge, _foe(), inv=_sword())
    _online(bridge, _person("Alex"))
    session.body.fights = [Result(False, "unreachable", {})] * 5 + [
        Result(True, None, {"kills": 1, "killed": True})
    ]
    assert _attack(session, player="Alex")["outcome"] == "killed"
    assert len(_logged(session.body, "fight")) == 6


def test_a_defence_in_the_middle_does_not_reset_the_jobs_tally(fighter):
    session, bridge = fighter
    session.current_job = "j-1"
    for _ in range(3):
        _look(bridge, _foe(), inv=_sword())
    _online(bridge, _person("Alex"))
    session.body.fights = [Result(True, None, {"hits": 2, "damage": 4.0})]
    real = session.body.fight

    def interrupted(**fields):
        if len(_logged(session.body, "fight")) == 1:
            session.body.log.append(("fight", fields))
            raise cs.Defend(Action("fight", "a zombie"))
        return real(**fields)

    session.body.fight = interrupted
    with pytest.raises(cs.Defend):
        _attack(session, player="Alex")
    session.body.fight = real
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True, "hits": 3})]
    result = _attack(session, player="Alex")
    assert (result["hits"], result["fights"]) == (5, 2)
    session.current_job = "j-2"  # the next job starts from nothing
    _look(bridge, _foe(), inv=_sword())
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True, "hits": 1})]
    assert _attack(session, player="Alex")["hits"] == 1


def test_attack_reports_what_it_is_doing(fighter):
    session, bridge = fighter
    _look(bridge, _foe(), inv=_sword())
    _online(bridge, _person("Alex"))
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True})]
    _attack(session, player="Alex")
    first = session.reported[0]
    assert first["stage"] == "attacking" and "Alex" in first["detail"]


def test_run_dispatches_attack_as_a_job(fighter):
    session, bridge = fighter
    _look(bridge, _mob(), inv=_sword())
    session.body.fights = [Result(True, None, {"kills": 1, "killed": True})]
    result = session.run("attack", _order(mob=ZOMBIE), OWNER, lambda **kw: None)
    assert result["outcome"] == "killed"
