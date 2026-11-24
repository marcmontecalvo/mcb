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


def _look(bridge, *entities, health=20.0, inv=(), tick=10):
    """The next observation the attack loop reads."""
    bridge.rows.append(_day_row(*entities, health=health, inv=inv, tick=tick))


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
