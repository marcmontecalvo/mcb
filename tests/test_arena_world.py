"""The arena server's world: a floating lobby at spawn, arenas in slots beside it."""

import pytest

from mclab.arena import client as arena
from mclab.arena import designs, world
from mclab.arena import plan as ap


def test_the_lobby_is_a_valid_sealed_room_with_a_pad_for_every_player():
    lobby = designs.lobby()
    assert ap.validate(lobby) == []
    assert lobby.name == world.LOBBY
    (team,) = lobby.teams.values()
    assert len(team.pads) >= 16  # an 8v8 roster waits here before the match
    assert lobby.capacity.mobs == 0 and not lobby.mob_spawns


def test_the_spawn_stands_on_the_lobby_entry_in_world_coordinates():
    lobby = designs.lobby()
    (team,) = lobby.teams.values()
    x, y, z = world.LOBBY_ORIGIN
    assert world.lobby_spawn(lobby) == (
        x + team.entry[0],
        y + team.entry[1],
        z + team.entry[2],
    )


def test_slots_sit_apart_beyond_the_largest_footprint():
    first, second = world.slot_origin(0), world.slot_origin(1)
    assert second[0] - first[0] >= ap.MAX_FOOTPRINT + 32
    assert first[0] - world.LOBBY_ORIGIN[0] >= ap.MAX_FOOTPRINT
    assert first[1] == second[1] == world.LOBBY_ORIGIN[1]


def _listed(name, origin, state="built", size=(41, 15, 47)):
    return {"name": name, "origin": list(origin), "size": list(size), "state": state}


def test_an_arena_keeps_its_registered_origin():
    listed = [_listed("vault", world.slot_origin(3))]
    assert world.origin_for("vault", listed) == world.slot_origin(3)


def test_a_new_arena_takes_the_first_free_slot():
    listed = [
        _listed(world.LOBBY, world.LOBBY_ORIGIN, size=(25, 9, 25)),
        _listed("vault", world.slot_origin(0)),
        _listed("duel", world.slot_origin(2)),
    ]
    assert world.origin_for("hall", listed) == world.slot_origin(1)


def test_a_slot_overlapped_by_an_arena_built_elsewhere_is_not_free():
    stray = world.slot_origin(0)
    listed = [_listed("odd", (stray[0] + 10, stray[1], stray[2] + 10))]
    assert world.origin_for("hall", listed) == world.slot_origin(1)


class FakeArenas:
    """The ArenaClient calls prepare makes, against an in-memory registry."""

    def __init__(self, listed=()):
        self.arenas = {a["name"]: dict(a) for a in listed}
        self.calls = []
        self.outcome = "arena_built"

    def list(self):
        self.calls.append(("list",))
        return {"arenas": list(self.arenas.values())}

    def build(self, plan, origin, *, name=None, dimension=None):
        name = name or plan["name"]
        self.calls.append(("build", name, tuple(origin)))
        self.arenas[name] = _listed(name, origin, "building", tuple(plan["size"]))
        return {"name": name}

    def remove(self, name, *, restore=False):
        self.calls.append(("remove", name, restore))
        del self.arenas[name]
        return {"name": name, "restoring": restore}

    def wait_for(self, name, kinds, *, timeout, on_event=None):
        self.calls.append(("wait", name))
        assert self.outcome in kinds
        on_event and on_event(
            {"type": "arena_build_progress", "data": {"name": name, "percent": 50}}
        )
        self.arenas[name]["state"] = "built"
        return {
            "type": self.outcome,
            "data": {"name": name, "reason": "chunks_not_loaded"},
        }

    def spawn(self, position):
        self.calls.append(("spawn", tuple(position)))
        return {"position": list(position)}


def _plans(name):
    return {"schema": 1, "name": name, "size": [9, 6, 9]}


def test_prepare_builds_the_lobby_sets_the_spawn_and_builds_the_arena():
    fake, lines = FakeArenas(), []
    out = world.prepare(fake, "vault", load_plan=_plans, echo=lines.append)
    lobby = designs.lobby()
    assert fake.calls == [
        ("list",),
        ("build", world.LOBBY, world.LOBBY_ORIGIN),
        ("wait", world.LOBBY),
        ("spawn", world.lobby_spawn(lobby)),
        ("build", "vault", world.slot_origin(0)),
        ("wait", "vault"),
    ]
    assert out == {
        "arena": "vault",
        "origin": world.slot_origin(0),
        "spawn": world.lobby_spawn(lobby),
        "built": [world.LOBBY, "vault"],
    }
    assert any("lobby" in line for line in lines)
    assert any("50%" in line for line in lines)


def test_prepare_leaves_built_arenas_alone():
    lobby = designs.lobby()
    fake = FakeArenas(
        [
            _listed(world.LOBBY, world.LOBBY_ORIGIN, size=lobby.size),
            _listed("vault", world.slot_origin(4)),
        ]
    )
    out = world.prepare(fake, "vault", load_plan=_plans, echo=lambda _: None)
    assert fake.calls == [("list",), ("spawn", world.lobby_spawn(lobby))]
    assert out["origin"] == world.slot_origin(4) and out["built"] == []


def test_prepare_waits_for_a_build_in_progress_and_redoes_an_incomplete_one():
    fake = FakeArenas(
        [
            _listed(world.LOBBY, world.LOBBY_ORIGIN, state="building"),
            _listed("vault", world.slot_origin(1), state="incomplete"),
        ]
    )
    world.prepare(fake, "vault", load_plan=_plans, echo=lambda _: None)
    assert fake.calls[1] == ("wait", world.LOBBY)
    assert fake.calls[3:] == [
        ("remove", "vault", False),
        ("build", "vault", world.slot_origin(1)),
        ("wait", "vault"),
    ]


def test_prepare_refuses_an_arena_that_is_being_cleared():
    fake = FakeArenas([_listed("vault", world.slot_origin(0), state="clearing")])
    with pytest.raises(arena.ArenaError) as busy:
        world.prepare(fake, "vault", load_plan=_plans, echo=lambda _: None)
    assert busy.value.reason == "arena_busy"


def test_prepare_says_why_a_build_stopped():
    fake = FakeArenas()
    fake.outcome = "arena_build_aborted"
    with pytest.raises(arena.ArenaError) as aborted:
        world.prepare(fake, "vault", load_plan=_plans, echo=lambda _: None)
    assert aborted.value.reason == "chunks_not_loaded"
    assert world.LOBBY in aborted.value.detail


def test_ensure_lobby_builds_it_once_and_sets_the_spawn():
    fake, lines = FakeArenas(), []
    lobby = designs.lobby()
    assert world.ensure_lobby(fake, echo=lines.append) == {
        "spawn": world.lobby_spawn(lobby),
        "built": True,
    }
    assert fake.calls == [
        ("list",),
        ("build", world.LOBBY, world.LOBBY_ORIGIN),
        ("wait", world.LOBBY),
        ("spawn", world.lobby_spawn(lobby)),
    ]
    fake.calls.clear()
    assert world.ensure_lobby(fake, echo=lines.append)["built"] is False
    assert fake.calls == [("list",), ("spawn", world.lobby_spawn(lobby))]


def test_the_bots_world_spawn_on_the_arena_server_is_the_lobby_built_first():
    # The void has nothing under the level's spawn: the bot spawned there before
    # the first match fell out of the world at every retry.
    fake = FakeArenas()
    spawn = world.lobby_world_spawn(client_factory=lambda: fake)
    assert spawn == world.lobby_spawn(designs.lobby())
    assert ("build", world.LOBBY, world.LOBBY_ORIGIN) in fake.calls
    assert fake.calls[-1] == ("spawn", spawn)


def test_the_bots_world_spawn_fails_loudly_when_the_lobby_cannot_be_built():
    fake = FakeArenas()
    fake.outcome = "arena_build_aborted"
    with pytest.raises(arena.ArenaError):
        world.lobby_world_spawn(client_factory=lambda: fake)


def test_the_lobby_cannot_be_played_in():
    with pytest.raises(arena.ArenaError) as lobby:
        world.prepare(FakeArenas(), world.LOBBY, load_plan=_plans, echo=lambda _: None)
    assert lobby.value.reason == "bad_request"
