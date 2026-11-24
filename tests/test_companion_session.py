"""Companion session: the host's one body in a kept world (fakes, no server)."""

import json
from types import SimpleNamespace

import pytest

from mclab import companion_session as cs
from mclab import follow as follow_mod
from mclab import navigation_segmented
from mclab.build_runner import Result
from mclab.companion_combat import Tactic
from mclab.companion_defense import Action, DefensePolicy, Threat
from mclab.companion_intents import Actor
from mclab.follow import PLAYER, FollowTarget
from mclab.gather_runner import GatherReport, GatherState
from mclab.movement_policy import MovementPolicy
from mclab.place_graph import Place
from mclab.progress_watch import BodyDied

# --- fakes ---------------------------------------------------------------------------


def _player(name, pos):
    return {"type": PLAYER, "name": name, "position": list(pos)}


def _row(pos=(0.5, 64.0, 0.5), state="ready", health=20.0, entities=(), tick=1, inv=()):
    return {
        "tick": tick,
        "state": "running" if state == "ready" else state,
        "body_state": state,
        "body": {
            "position": list(pos) if pos else None,
            "health": health,
            "food": 18,
            "inventory": list(inv),
        },
        "visible_entities": list(entities),
    }


class FakeBridge:
    def __init__(self, rows=(), http_state=None):
        self.rows = list(rows)
        self.sent, self.acks, self.trimmed = [], [], []
        self.events, self.samples = [], []
        self.http_state = http_state if http_state is not None else {"state": "running"}
        self.cancel_check = None
        self.closed = False
        self.default = _row()

    def send(self, op, **fields):
        self.sent.append((op, fields))
        return {"op": op}

    def acknowledge(self, request, kind, timeout=None):
        self.acks.append((request["op"], kind))

    def http(self, path):
        if isinstance(self.http_state, Exception):
            raise self.http_state
        return self.http_state

    def state(self):
        if self.cancel_check is not None:
            self.cancel_check()
        row = self.rows.pop(0) if self.rows else self.default
        self.samples.append(row)
        return row

    def trim(self, keep):
        self.trimmed.append(keep)

    def close(self):
        self.closed = True


class FakePlaces:
    def __init__(self):
        self.places = {}
        self.saved = None

    def listed(self, kind=None):
        return list(self.places.values())

    def named(self, name):
        return self.places.get(name)

    def add(self, name, position, *, kind="place", provenance="named"):
        place = Place(name, name, tuple(position), provenance, 1, kind=kind)
        self.places[name] = place
        return place

    def remove(self, name):
        self.places.pop(name, None)

    def load_containers(self):
        return {(1, 2, 3): {"minecraft:dirt": 4}}

    def save_containers(self, containers):
        self.saved = dict(containers)


class FakeBody:
    def __init__(self, bridge, memory, *, movement_policy, terrain_policy):
        self.bridge, self.memory = bridge, memory
        self.policies = (movement_policy, terrain_policy)
        self.places = None
        self.follow = None
        self.pos = (0, 64, 0)
        self.inv = {}
        self.log = []
        self.walk_ok = True
        self.observed = 0

    def observe(self):
        self.observed += 1

    def position(self):
        return self.pos

    def inventory(self):
        return dict(self.inv)

    def go_to(self, name):
        self.log.append(("go_to", name))
        return self.walk_ok

    def walk_far(self, goal, radius):
        self.log.append(("walk_far", tuple(goal), radius))
        if self.walk_ok:
            self.pos = tuple(goal)
        return self.walk_ok

    def walk_to(self, goal, radius=1.5):
        self.log.append(("walk_to", tuple(goal), radius, self.follow))
        return self.walk_ok

    def wait_ticks(self, ticks):
        self.log.append(("wait", ticks))

    def wear(self):
        self.log.append(("wear",))
        return Result(True, None, {"put_on": [], "worn": {}, "armor": 0})

    def _drop_ahead(self):
        self.log.append(("drop_ahead",))


class FakeMemory:
    closed = False
    cells: dict = {}

    def close(self):
        self.closed = True


class FakeAwareness:
    def payload(self):
        return {"radius": 64}


class FakeSay:
    def __init__(self):
        self.said = []

    def say(self, text, to=None):
        self.said.append((text, to))


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "await_sampled_stream", lambda bridge, timeout: {})

    def build(
        bridge=None,
        *,
        state=None,
        knowledge=None,
        clock=None,
        spawn=(0, 80, 0),
        body=FakeBody,
        **extra,
    ):
        bridge = bridge or FakeBridge()
        memory, places, say = FakeMemory(), FakePlaces(), FakeSay()
        state_file = tmp_path / "runtime" / "companion-state.json"
        if state is not None:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps(state))
        session = cs.Session(
            "MCBot",
            folder=tmp_path / "run",
            state_file=state_file,
            gather_state=GatherState(),
            open_bridge=lambda folder: bridge,
            open_memory=lambda b, folder, awareness: memory,
            make_places=lambda m: places,
            make_body=body,
            awareness=FakeAwareness(),
            say_client=say,
            knowledge=knowledge
            or (lambda: SimpleNamespace(foods={"minecraft:bread": 5})),
            clock=clock or (lambda: 0.0),
            world_spawn=lambda: spawn,
            **extra,
        )
        return session, bridge, places, say, memory

    return build


def _started(make, **kw):
    session, bridge, places, say, memory = make(**kw)
    session.start()
    return session, bridge, places, say, memory


def _ops(bridge):
    return [op for op, _ in bridge.sent]


# --- spawn, state, close ---------------------------------------------------------


def test_spawn_sends_the_kept_body_and_records_home(make):
    session, bridge, places, _, _ = _started(make)
    op, fields = bridge.sent[0]
    assert op == "spawn"
    assert fields["name"] == "MCBot"
    assert fields["keep_state"] is True
    assert fields["replace"] is True and fields["stream"] is True
    assert fields["extended_observation"] is True
    assert fields["terrain_encoding"] == "sections-v1"
    assert fields["awareness"] == {"radius": 64}
    assert fields["position"] == [0.5, 80, 0.5]  # no saved state: the world spawn
    assert ("spawn", "spawn_requested") in bridge.acks
    assert places.named("home").position == (0, 64, 0)
    assert places.named("home").kind == "home"
    assert session.gather_state.containers == {(1, 2, 3): {"minecraft:dirt": 4}}
    assert session.body.places is places
    assert session.body.observed == 1
    saved = json.loads(session.state_file.read_text())
    assert saved["bot"] == "MCBot" and saved["position"] == [0, 64, 0]


def test_spawn_position_prefers_the_saved_state_then_the_world_spawn(make):
    session, *_ = make(state={"bot": "MCBot", "position": [10.7, 70, -3.2]})
    assert session.spawn_position() == (10, 70, -4)

    other, *_ = make(
        state={"bot": "Someone", "position": [10, 70, 3]}, spawn=(1, 65, 3)
    )
    assert other.spawn_position() == (1, 65, 3)

    broken, *_ = make(state={"bot": "MCBot", "position": ["x", 1, 2]}, spawn=(4, 70, 4))
    assert broken.spawn_position() == (4, 70, 4)


def test_no_known_spawn_point_refuses_to_spawn_instead_of_guessing(make):
    """A guessed column dropped the bot into the ocean, where a body never settles
    and the session start timed out (play world, Nov 23, 2026). The host retries a
    failed start with backoff."""
    session, bridge, *_ = make(spawn=None)
    with pytest.raises(cs.JobFailed, match="world spawn"):
        session.start()
    assert bridge.sent == [] and bridge.closed


def _level(tmp_path, data):
    from mclab import nbt

    folder = tmp_path / "world"
    folder.mkdir(exist_ok=True)
    (folder / "level.dat").write_bytes(nbt.dumps({"Data": (nbt.COMPOUND, data)}))
    return folder


def test_world_spawn_reads_the_26_3_level_dat(tmp_path):
    from mclab import nbt

    spawn = {
        "pos": (nbt.INT_ARRAY, [-272, 76, -16]),
        "dimension": (nbt.STRING, "minecraft:overworld"),
        "yaw": (nbt.FLOAT, 0.0),
    }
    level = _level(tmp_path, {"spawn": (nbt.COMPOUND, spawn)})
    assert cs.world_spawn(level) == (-272, 76, -16)


def test_world_spawn_reads_the_older_level_dat(tmp_path):
    from mclab import nbt

    data = {k: (nbt.INT, v) for k, v in (("SpawnX", 5), ("SpawnY", 70), ("SpawnZ", -9))}
    assert cs.world_spawn(_level(tmp_path, data)) == (5, 70, -9)


def test_world_spawn_is_none_when_unknown(tmp_path):
    from mclab import nbt

    assert cs.world_spawn(tmp_path / "missing") is None
    nether = {
        "pos": (nbt.INT_ARRAY, [1, 2, 3]),
        "dimension": (nbt.STRING, "minecraft:the_nether"),
    }
    assert cs.world_spawn(_level(tmp_path, {"spawn": (nbt.COMPOUND, nether)})) is None
    (tmp_path / "world" / "level.dat").write_bytes(b"not nbt")
    assert cs.world_spawn(tmp_path / "world") is None


def test_home_is_kept_when_already_known(make):
    session, bridge, places, *_ = make()
    places.add("home", (5, 5, 5), kind="home")
    session.start()
    assert places.named("home").position == (5, 5, 5)


def test_a_failed_start_closes_what_it_opened(make, monkeypatch):
    session, bridge, _, _, memory = make()

    def boom(*_a, **_k):
        raise RuntimeError("stream never sampled")

    monkeypatch.setattr(cs, "await_sampled_stream", boom)
    with pytest.raises(RuntimeError):
        session.start()
    assert bridge.closed and memory.closed
    assert "despawn" not in _ops(bridge)


def test_bridge_polls_run_the_hosts_poll_hook(make):
    session, bridge, *_ = _started(make)
    calls = []
    session.poll_hook = lambda: calls.append(1)
    bridge.state()
    assert calls == [1]
    session.poll_hook = None
    bridge.state()  # no hook: nothing happens


def test_write_atomic_replaces_and_read_state_tolerates_garbage(tmp_path):
    path = tmp_path / "a" / "s.json"
    cs.write_atomic(path, {"x": 1})
    cs.write_atomic(path, {"x": 2})
    assert cs.read_state(path) == {"x": 2}
    assert not path.with_name("s.json.tmp").exists()
    path.write_text("[1, 2]")
    assert cs.read_state(path) == {}
    path.write_text("{nope")
    assert cs.read_state(path) == {}
    assert cs.read_state(tmp_path / "missing.json") == {}


def test_close_with_despawn_saves_and_logs_out(make):
    session, bridge, _, _, memory = _started(make)
    bridge.cancel_check = lambda: pytest.fail("never cancelled half way out")
    session.close(despawn=True)
    assert ("despawn", "despawned") in bridge.acks
    assert bridge.closed and memory.closed
    assert session.body is None


def test_close_without_despawn_leaves_the_body(make):
    session, bridge, _, _, memory = _started(make)
    session.close(despawn=False)
    assert "despawn" not in _ops(bridge)
    assert bridge.closed and memory.closed


def test_close_goes_on_when_a_step_fails(make):
    session, bridge, _, _, memory = _started(make)

    def bad_close():
        raise OSError("disk")

    memory.close = bad_close
    session.close()
    assert bridge.closed


def test_halt_cancels_the_route_and_releases(make):
    session, bridge, *_ = _started(make)
    session.body.follow = FollowTarget("Steve")
    session.halt()
    assert session.body.follow is None
    assert ("drop_ahead",) in session.body.log
    assert bridge.acks[-2:] == [
        ("cancel_route", "route_cancelled"),
        ("release", "released"),
    ]


# --- idle, health, snapshot ------------------------------------------------------


@pytest.mark.parametrize("state", sorted(cs.GONE_STATES))
def test_idle_raises_body_died_once_the_body_is_gone(make, state):
    session, bridge, *_ = _started(make)
    bridge.rows = [_row(state=state)]
    with pytest.raises(BodyDied):
        session.idle()


def test_idle_feeds_memory_keeps_the_position_and_trims(make):
    now = [0.0]
    session, bridge, *_ = _started(make, clock=lambda: now[0])
    observed = session.body.observed
    now[0] = 1.0
    bridge.rows = [
        _row(pos=(3.5, 64, 4.5), entities=[_player("Steve", (6.5, 64, 4.5))])
    ]
    snap = session.idle()
    assert session.body.observed == observed  # too soon to observe again
    assert bridge.trimmed == [cs.KEEP_EVENTS]
    assert session.last_good == (3, 64, 4)
    assert snap["position"] == [3.5, 64, 4.5]
    assert snap["players"] == [
        {"name": "Steve", "position": [6.5, 64, 4.5], "distance": 3.0}
    ]
    now[0] = 2.0
    bridge.rows = [_row(pos=(9.5, 64, 4.5))]
    session.idle()
    assert json.loads(session.state_file.read_text())["position"] == [
        0,
        64,
        0,
    ]  # throttled
    now[0] = 20.0
    bridge.rows = [_row(pos=(9.5, 64, 4.5))]
    session.idle()
    assert session.body.observed == observed + 1
    assert json.loads(session.state_file.read_text())["position"] == [9, 64, 4]


def test_an_unsafe_position_is_not_remembered(make):
    session, bridge, *_ = _started(make)
    bridge.rows = [_row(pos=(30.5, 10, 0.5), health=4.0)]
    session.idle()
    assert session.last_good == (0, 64, 0)
    bridge.rows = [_row(pos=None)]
    session.idle()
    assert session.last_good == (0, 64, 0)


def test_a_wet_or_airborne_position_is_not_remembered(make):
    """The play bot saved a spot in shallow water as good; every later start there
    drowned (Nov 23, 2026). Only a dry stance on the ground is a place to return to."""
    session, bridge, *_ = _started(make)
    wet = _row(pos=(30.5, 62, 0.5))
    wet["body"]["survival"] = {"in_water": True, "eye_in_water": False}
    falling = _row(pos=(31.5, 70, 0.5))
    falling["body"]["on_ground"] = False
    for row in (wet, falling):
        bridge.rows = [row]
        session.idle()
        assert session.last_good == (0, 64, 0)
    dry = _row(pos=(32.5, 64, 0.5))
    dry["body"].update(on_ground=True, survival={"in_water": False})
    bridge.rows = [dry]
    session.idle()
    assert session.last_good == (32, 64, 0)


def test_a_failed_start_from_the_saved_position_forgets_it(make, monkeypatch):
    """The host retries a failed start; the same saved spot failed the same way
    every time. The next start uses the world spawn."""
    session, *_ = make(state={"bot": "MCBot", "position": [-175, 63, -62]})

    def boom(*_a, **_k):
        raise RuntimeError("stream never sampled")

    monkeypatch.setattr(cs, "await_sampled_stream", boom)
    with pytest.raises(RuntimeError):
        session.start()
    assert "position" not in json.loads(session.state_file.read_text())
    assert session.spawn_position() == (0, 80, 0)


def test_a_failed_start_from_the_world_spawn_writes_no_state(make, monkeypatch):
    session, *_ = make()
    monkeypatch.setattr(
        cs, "await_sampled_stream", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(OSError):
        session.start()
    assert not session.state_file.exists()


def test_health_classifies_the_bridge(make):
    session, bridge, *_ = _started(make)
    assert session.health() == "ok"
    bridge.http_state = {"state": "dead", "body_state": "dead"}
    assert session.health() == "dead"
    bridge.http_state = ConnectionRefusedError()
    assert session.health() == "lost"


def test_snapshot_counts_stacks_and_uses_the_latest_sample(make):
    session, bridge, *_ = _started(make)
    bridge.samples.append(
        _row(
            inv=[
                {"item": "minecraft:dirt", "count": 3},
                {"item": "minecraft:dirt", "count": 2},
                {"item": "minecraft:stick", "count": 1},
            ],
            entities=[{"type": "minecraft:cow", "position": [1, 64, 1]}],
            tick=77,
        )
    )
    snap = session.snapshot()
    assert snap["tick"] == 77
    assert snap["body_state"] == "ready"
    assert snap["inventory"] == {"minecraft:dirt": 5, "minecraft:stick": 1}
    assert snap["players"] == []
    assert snap["health"] == 20.0 and snap["food"] == 18
    bridge.samples.clear()
    assert session.snapshot()["position"] is None


def test_record_progress_writes_progress_json(make):
    session, *_ = _started(make)
    session.record_progress(
        {
            "intent": "goto",
            "id": "j-12345678",
            "state": "running",
            "progress": {"stage": "walking", "done": 1, "total": 3, "detail": None},
        }
    )
    value = json.loads((session.folder / "progress.json").read_text())
    assert value["job"] == "goto" and value["stage"] == "walking" and value["done"] == 1


def test_say_uses_the_say_client(make):
    session, _, _, say, _ = make()
    session.say("hello", "Steve")
    assert say.said == [("hello", "Steve")]
    session._say_client = None
    with pytest.raises(RuntimeError):
        session.say("x")


# --- places ----------------------------------------------------------------------


def test_places_list_and_remember(make):
    session, _, places, *_ = _started(make)
    session.body.pos = (7, 65, 8)
    assert session.remember_place("mine") == {
        "name": "mine",
        "kind": "place",
        "position": [7, 65, 8],
    }
    session.remember_place("mine", (1, 2, 3))
    assert places.named("mine").position == (1, 2, 3)
    names = [p["name"] for p in session.list_places()]
    assert names == ["home", "mine"]


# --- death ---------------------------------------------------------------------------


def test_respawn_remembers_death_from_the_body_died_event(make):
    session, bridge, places, *_ = _started(make)
    session.last_good = (4, 64, 4)
    places.add("death", (0, 0, 0), kind="death")
    bridge.events.append(
        {"type": "body_died", "data": {"name": "MCBot", "position": [9.3, 64, 9.8]}}
    )
    session.body.follow = FollowTarget("Steve")
    old = session.body
    bridge.sent.clear()
    info = session.respawn()
    assert info["death"] == [9, 64, 9]
    assert places.named("death").position == (9, 64, 9)
    assert places.named("death").kind == "death"
    assert old.follow is None and ("drop_ahead",) in old.log
    op, fields = bridge.sent[0]
    # vanilla: a dead player comes back at the world spawn, not beside what killed
    # it (play world, Nov 23, 2026: respawned at the death spot next to the zombie)
    assert op == "spawn" and fields["position"] == [0.5, 80, 0.5]
    assert fields["keep_state"] is True
    assert session.body is not old


def test_a_body_that_left_without_dying_comes_back_where_it_was(make):
    session, bridge, places, *_ = _started(make)
    session.last_good = (4, 64, 4)
    bridge.samples.append(_row(pos=(4.5, 64, 4.5)))
    bridge.sent.clear()
    info = session.respawn()
    assert info["death"] is None and places.named("death") is None
    assert bridge.sent[0][1]["position"] == [4.5, 64, 4.5]


def test_a_handled_death_is_not_counted_again(make):
    session, bridge, places, *_ = _started(make)
    session.last_good = (4, 64, 4)
    bridge.events.append({"type": "body_died", "data": {"position": [9, 64, 9]}})
    session.respawn()
    places.remove("death")
    session.last_good = (4, 64, 4)  # the respawn set it to the new feet
    bridge.sent.clear()
    assert session.respawn()["death"] is None  # the old event is still in the log
    assert places.named("death") is None
    assert bridge.sent[0][1]["position"] == [4.5, 64, 4.5]


def test_respawn_falls_back_to_the_last_sample(make):
    session, bridge, places, *_ = _started(make)
    bridge.events.append({"type": "body_died", "data": {}})
    bridge.samples.append(_row(pos=(-2.5, 70, 3.5)))
    assert session.respawn()["death"] == [-3, 70, 3]
    bridge.events.clear()
    bridge.samples.clear()
    assert session.respawn()["death"] is None


# --- jobs ---------------------------------------------------------------------------


def _progress():
    calls = []
    return calls, lambda **kw: calls.append(kw)


def _goto(target, radius=1.5):
    return {"target": target, "radius": radius, "when": "now"}


def test_goto_a_position_walks_far(make):
    session, *_ = _started(make)
    calls, progress = _progress()
    result = session.run(
        "goto", _goto({"pos": (10, 64, 10)}, 2.0), Actor("api"), progress
    )
    assert result == {"arrived": "10 64 10", "position": [10, 64, 10]}
    assert session.body.log[-1] == ("walk_far", (10, 64, 10), 2.0)
    assert calls[0]["stage"] == "walking"


def test_goto_a_failed_walk_says_where_it_stopped(make):
    session, *_ = _started(make)
    session.body.walk_ok = False
    with pytest.raises(
        cs.JobFailed, match="I could not get to 10 64 10; I stopped at 0 64 0"
    ):
        session.run("goto", _goto({"pos": (10, 64, 10)}), Actor("api"), _progress()[1])


def test_goto_a_place(make):
    session, *_ = _started(make)
    assert (
        session.run("goto", _goto({"place": "home"}), Actor("api"), _progress()[1])[
            "arrived"
        ]
        == "home"
    )
    assert session.body.log[-1] == ("go_to", "home")
    session.body.walk_ok = False
    with pytest.raises(cs.JobFailed, match="could not get to home"):
        session.run("goto", _goto({"place": "home"}), Actor("api"), _progress()[1])


def test_goto_an_unknown_place_lists_the_known_ones(make):
    session, *_ = _started(make)
    with pytest.raises(
        cs.JobFailed, match="I don't know a place called 'shop'; known: home"
    ):
        session.run("goto", _goto({"place": "shop"}), Actor("api"), _progress()[1])


def test_goto_a_visible_player_walks_to_their_feet(make):
    session, bridge, *_ = _started(make)
    bridge.rows = [_row(entities=[_player("Steve", (5.7, 64.5, -1.2))])]
    result = session.run(
        "goto", _goto({"player": "steve"}), Actor("api"), _progress()[1]
    )
    assert result["arrived"] == "steve"
    assert session.body.log[-1][:2] == ("walk_far", (5, 64, -2))


def test_goto_the_asking_player_uses_their_own_position(make):
    session, *_ = _started(make)
    actor = Actor("chat", name="Steve", uuid="u", position=(20.5, 70, 3.5))
    session.run("goto", _goto({"player": "STEVE"}), actor, _progress()[1])
    assert session.body.log[-1][:2] == ("walk_far", (20, 70, 3))


def test_goto_an_unseen_player_fails(make):
    session, *_ = _started(make)
    actor = Actor("chat", name="Alex", uuid="u", position=(20.5, 70, 3.5))
    with pytest.raises(cs.JobFailed, match="I can't see Steve"):
        session.run("goto", _goto({"player": "Steve"}), actor, _progress()[1])


def test_run_refuses_a_non_job(make):
    session, *_ = _started(make)
    with pytest.raises(cs.JobFailed, match="not a job"):
        session.run("say", {}, Actor("api"), _progress()[1])


class Stop(BaseException):
    pass


def test_follow_walks_when_far_waits_when_close_and_ends_only_when_stopped(make):
    session, bridge, *_ = _started(make)
    steve = [_player("Steve", (10.5, 64, 0.5))]
    near = [_player("Steve", (1.5, 64, 0.5))]
    bridge.rows = [
        _row(entities=steve),  # first sight
        _row(entities=steve),  # far: walk
        _row(entities=near),  # close: wait
        _row(entities=[]),  # lost sight, last seen near: wait there
        _row(entities=steve),  # far again, the walk fails: retry wait
    ]
    polls = [0]

    def hook():
        polls[0] += 1
        if polls[0] > 5:
            raise Stop

    session.poll_hook = hook
    calls, progress = _progress()
    session.body.walk_ok = True

    walks = []

    def walk_to(goal, radius=1.5):
        walks.append((goal, radius, session.body.follow))
        return len(walks) == 1

    session.body.walk_to = walk_to
    with pytest.raises(Stop):
        session.run(
            "follow",
            {"player": "Steve", "distance": 3, "when": "now"},
            Actor("api"),
            progress,
        )
    assert [w[0] for w in walks] == [(10, 64, 0), (10, 64, 0)]
    assert walks[0][1] == 3 and walks[0][2].name == "Steve"
    stages = [c["stage"] for c in calls]
    assert stages == ["following", "waiting", "lost sight", "following"]
    waits = [entry for entry in session.body.log if entry[0] == "wait"]
    assert waits == [
        ("wait", cs.FOLLOW_WAIT_TICKS),
        ("wait", cs.FOLLOW_WAIT_TICKS),
        ("wait", cs.FOLLOW_RETRY_TICKS),
    ]
    assert session.body.follow is None


def test_follow_an_unseen_player_fails_and_a_dead_body_raises(make):
    session, bridge, *_ = _started(make)
    with pytest.raises(cs.JobFailed, match="I can't see Steve"):
        session.run(
            "follow", {"player": "Steve", "distance": 3}, Actor("api"), _progress()[1]
        )
    bridge.rows = [_row(entities=[_player("Steve", (1, 64, 1))]), _row(pos=None)]
    with pytest.raises(BodyDied):
        session.run(
            "follow", {"player": "Steve", "distance": 3}, Actor("api"), _progress()[1]
        )
    assert session.body.follow is None


# --- gather and build ---------------------------------------------------------------


@pytest.fixture
def gather_env(monkeypatch):
    from mclab import gather_plan, gather_runner

    env = SimpleNamespace(
        plan=None, report=GatherReport(stop="done"), gain=0, adds=None, calls=[]
    )

    def fake_plan(wanted, inventory, knowledge, stations, **kw):
        env.calls.append(("plan", dict(wanted), kw))
        return env.plan or SimpleNamespace(tasks=["t1", "t2"], missing=[])

    def fake_run(plan_result, body, knowledge, wanted, *, state, opts):
        env.calls.append(("run", dict(wanted)))
        for item, n in (env.adds or dict.fromkeys(wanted, env.gain)).items():
            body.inv[item] = body.inv.get(item, 0) + n
        return env.report

    monkeypatch.setattr(gather_plan, "plan", fake_plan)
    monkeypatch.setattr(gather_runner, "run_gather", fake_run)
    return env


def _gather(item="minecraft:oak_log", count=4):
    return {"item": item, "count": count, "when": "now"}


def test_gather_already_held_returns_without_planning(make, gather_env):
    session, *_ = _started(make)
    session.body.inv = {"minecraft:oak_log": 9}
    assert session.run("gather", _gather(), Actor("api"), _progress()[1]) == {
        "item": "minecraft:oak_log",
        "held": 9,
        "gathered": 0,
    }
    assert gather_env.calls == []
    assert session.body.foods == {"minecraft:bread": 5}


def test_gather_runs_the_plan_and_saves_containers(make, gather_env):
    session, _, places, *_ = _started(make)
    gather_env.gain = 4
    gather_env.report = GatherReport(stop="done", done=["a", "b"])
    calls, progress = _progress()
    result = session.run("gather", _gather(), Actor("api"), progress)
    assert result == {"item": "minecraft:oak_log", "held": 4, "gathered": 4, "tasks": 2}
    assert gather_env.calls[0][2]["starter_tools"] is True
    assert places.saved is not None  # flushed
    assert [c["stage"] for c in calls] == ["planning", "gathering"]


def test_gather_without_a_way_fails_readably(make, gather_env):
    session, *_ = _started(make)
    gather_env.plan = SimpleNamespace(
        tasks=[], missing=[SimpleNamespace(item="minecraft:diamond_pickaxe")]
    )
    with pytest.raises(
        cs.JobFailed, match="I don't know how to get oak log: no way to diamond"
    ):
        session.run("gather", _gather(), Actor("api"), _progress()[1])


def test_gather_short_and_died(make, gather_env):
    session, *_ = _started(make)
    gather_env.gain = 1
    gather_env.report = GatherReport(
        stop="stalled", failures=[("t", "no trees in sight")]
    )
    with pytest.raises(
        cs.JobFailed, match=r"I have 1/4 oak log; .*stalled: no trees in sight"
    ):
        session.run("gather", _gather(), Actor("api"), _progress()[1])
    gather_env.report = GatherReport(stop="died")
    with pytest.raises(BodyDied):
        session.run("gather", _gather(), Actor("api"), _progress()[1])


LOGS = "#minecraft:logs"


def _logs_knowledge():
    return SimpleNamespace(
        foods={},
        item_tags={
            LOGS: ("minecraft:oak_log", "minecraft:spruce_log", "minecraft:birch_log")
        },
    )


def test_a_group_counts_every_member_held(make, gather_env):
    session, *_ = _started(make, knowledge=_logs_knowledge)
    session.body.inv = {"minecraft:spruce_log": 3, "minecraft:birch_log": 2}
    result = session.run("gather", _gather(LOGS), Actor("api"), _progress()[1])
    assert result == {"item": LOGS, "held": 5, "gathered": 0}
    assert gather_env.calls == []


def test_a_group_is_planned_as_the_group(make, gather_env):
    session, *_ = _started(make, knowledge=_logs_knowledge)
    session.body.inv = {"minecraft:oak_log": 1}
    gather_env.adds = {"minecraft:spruce_log": 2, "minecraft:birch_log": 1}
    result = session.run("gather", _gather(LOGS), Actor("api"), _progress()[1])
    assert gather_env.calls[0][1] == {LOGS: 4}
    assert gather_env.calls[1] == ("run", {LOGS: 4})
    assert result["held"] == 4
    assert result["gathered"] == 3


def test_a_group_short_reads_as_words(make, gather_env):
    session, *_ = _started(make, knowledge=_logs_knowledge)
    gather_env.adds = {"minecraft:spruce_log": 1}
    gather_env.report = GatherReport(stop="stalled", failures=[("t", "none left")])
    with pytest.raises(cs.JobFailed, match=r"I have 1/4 logs; "):
        session.run("gather", _gather(LOGS), Actor("api"), _progress()[1])


def test_an_unknown_group_fails_before_planning(make, gather_env):
    session, *_ = _started(make, knowledge=_logs_knowledge)
    with pytest.raises(cs.JobFailed, match="no group of items called no such tag"):
        session.run(
            "gather", _gather("#minecraft:no_such_tag"), Actor("api"), _progress()[1]
        )
    assert gather_env.calls == []


def test_a_failed_container_flush_does_not_hide_the_outcome(make, gather_env):
    session, _, places, *_ = _started(make)

    def broken(_):
        raise OSError("db locked")

    places.save_containers = broken
    gather_env.gain = 4
    assert session.run("gather", _gather(), Actor("api"), _progress()[1])["held"] == 4


@pytest.fixture
def build_env(monkeypatch):
    from mclab import companion_jobs

    env = SimpleNamespace(job=None, calls=[])

    def fake_build(path, origin, rotation, body, knowledge, **kw):
        env.calls.append((path, origin, rotation, kw))
        kw["on_stage"]("placing", detail="x")
        kw["on_progress"](SimpleNamespace(placed=[1, 2], stop="", steps=3), False)
        return env.job

    monkeypatch.setattr(companion_jobs, "build_job", fake_build)
    monkeypatch.setattr(cs, "template_path", lambda name: f"builds/{name}.build.json")
    return env


def _job(stop, build_stop="done", gather=None, shortfall=None):
    return SimpleNamespace(
        stop=stop,
        starter=None,
        gather=gather,
        refills=[],
        build=SimpleNamespace(stop=build_stop, placed=[1, 2, 3]),
        shortfall=shortfall or {},
    )


def test_build_defaults_the_origin_next_to_the_feet(make, build_env):
    session, *_ = _started(make)
    build_env.job = _job("built")
    calls, progress = _progress()
    result = session.run(
        "build",
        {"template": "small-house", "origin": None, "rotation": 90, "when": "now"},
        Actor("api"),
        progress,
    )
    assert result == {"template": "small-house", "origin": [3, 64, 2], "placed": 3}
    path, origin, rotation, kw = build_env.calls[0]
    assert path == "builds/small-house.build.json" and origin == (3, 64, 2)
    assert rotation == 90 and kw["state"] is session.gather_state
    assert calls[0] == {"stage": "placing", "done": None, "total": None, "detail": "x"}
    assert calls[1]["done"] == 2 and calls[1]["stage"] is None


def test_build_failures_name_the_shortfall_and_death(make, build_env):
    session, *_ = _started(make)
    args = {"template": "small-house", "origin": (1, 2, 3), "rotation": 0}
    build_env.job = _job("gather_failed", shortfall={"minecraft:oak_planks": 12})
    with pytest.raises(
        cs.JobFailed, match=r"stopped: gather_failed \(short of 12 oak planks\)"
    ):
        session.run("build", args, Actor("api"), _progress()[1])
    build_env.job = _job("not_built", build_stop="died")
    with pytest.raises(BodyDied):
        session.run("build", args, Actor("api"), _progress()[1])
    build_env.job = _job("gather_failed", gather=SimpleNamespace(stop="died"))
    with pytest.raises(BodyDied):
        session.run("build", args, Actor("api"), _progress()[1])


def test_template_path_finds_builds_and_lists_the_known_ones():
    path = cs.template_path("small-house")
    assert path.name == "small-house.build.json"
    assert cs.template_path("small-house.build.json") == path
    with pytest.raises(
        cs.JobFailed, match="No build template called 'castle'; known: .*small-house"
    ):
        cs.template_path("castle")


# --- say client ----------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def read(self):
        return b"{}"


def test_say_client_posts_with_the_token(monkeypatch):
    seen = {}

    class Connection:
        def __init__(self, host, port, timeout):
            seen["where"] = (host, port)

        def request(self, method, path, body, headers):
            seen.update(
                method=method, path=path, body=json.loads(body), headers=headers
            )

        def getresponse(self):
            return FakeResponse(seen.get("status", 200))

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(cs.http.client, "HTTPConnection", Connection)
    client = cs.SayClient(8765, "tok")
    client.say("hi", "Steve")
    assert seen["where"] == ("127.0.0.1", 8765)
    assert seen["method"] == "POST" and seen["path"] == "/say"
    assert seen["body"] == {"text": "hi", "to": "Steve"}
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["closed"]
    client.say("all")
    assert seen["body"] == {"text": "all"}
    seen["status"] = 400
    with pytest.raises(RuntimeError, match="HTTP 400"):
        client.say("bad")


# --- the follow hook: HostBody, retarget, native driver ------------------------------


def test_host_body_puts_the_follow_target_on_its_walks(monkeypatch):
    made = SimpleNamespace()
    monkeypatch.setattr(
        cs.CompanionBody, "_goto_controller", lambda self, *a, **k: made, raising=True
    )
    body = cs.HostBody.__new__(cs.HostBody)
    body.follow = FollowTarget("Steve")
    assert body._goto_controller((0, 0, 0), 1, 1, False, 1).follow is body.follow
    body.follow = None
    assert body._goto_controller((0, 0, 0), 1, 1, False, 1).follow is None


def test_retarget_moves_the_goal_without_a_new_revision():
    from mclab import body_search

    controller = navigation_segmented.SegmentedController.__new__(
        navigation_segmented.SegmentedController
    )
    cancelled, discarded = [], []
    search = body_search.BodySearch.__new__(body_search.BodySearch)
    search.cancel = lambda: cancelled.append(True)
    controller.goal = (0, 64, 0)
    controller.goal_revision = 3
    controller.plan_tail = ((1, 64, 0),)
    controller.search = search
    controller._discard_prefetch = lambda: discarded.append(True)
    controller.refreshed = object()
    controller.avoid_goal_stances = {(0, 64, 0)}
    controller.retarget([5, 64, 5])
    assert controller.goal == (5, 64, 5)
    assert controller.goal_revision == 3
    assert controller.plan_tail == () and controller.search is None
    assert cancelled and discarded
    assert controller.refreshed is None and not controller.avoid_goal_stances
    controller.search = "remote"  # not a BodySearch: just dropped
    controller.retarget((6, 64, 6))
    assert controller.search is None


def test_follow_target_retargets_only_after_the_player_moves():
    target = FollowTarget("Steve")
    controller = SimpleNamespace(goal=(0, 64, 0), moved=[])
    controller.retarget = lambda goal: (
        controller.moved.append(goal),
        setattr(controller, "goal", goal),
    )
    assert not target.update(controller, {"visible_entities": []})
    assert not target.update(
        controller, {"tick": 1, "visible_entities": [_player("steve", (1.5, 64, 0.5))]}
    )
    assert target.update(
        controller,
        {"tick": 2, "visible_entities": [_player("Steve", (4.5, 64.5, 0.5))]},
    )
    assert controller.moved == [(4, 64, 0)]
    assert target.last_seen == ((4.5, 64.5, 0.5), 2)
    with pytest.raises(ValueError):
        FollowTarget("")
    assert follow_mod.find_player([{"type": PLAYER, "name": "Steve"}], "Steve") is None


def test_native_driver_updates_the_follow_goal_before_the_stop_check(monkeypatch):
    from mclab import control_loop, native_driver

    seen = []

    class Recorder:
        def update(self, controller, observation):
            seen.append(("follow", observation.get("tick")))

    rows = iter(
        [
            {"tick": 1, "route_follower": {"state": "idle"}},
            {
                "tick": 2,
                "route_follower": {"route_id": "native-1", "state": "following"},
            },
            {
                "tick": 3,
                "route_follower": {"route_id": "native-1", "state": "following"},
            },
        ]
    )
    client = SimpleNamespace(
        send=lambda op, **kw: {"sequence": 1},
        acknowledge=lambda *_: None,
        wait=lambda predicate, **kw: predicate(
            {"route_follower": {"route_id": "native-1"}}
        ),
        record=lambda *args: None,
    )

    def stop_reason(observation):
        seen.append(("stop?", observation.get("tick")))
        return "arrived" if observation.get("tick") == 3 else None

    def decide(observation, *, native=False):
        controller.route = [(0, 1, 0), (1, 1, 0)]

    controller = SimpleNamespace(
        route=[],
        mode="following",
        terrain=None,
        gap_jump=None,
        failed_edges=set(),
        movement_policy=MovementPolicy(),
        committed_step=None,
        own_footholds=set(),
        edits_broken=0,
        edits_placed=0,
        search=None,
        _admit=lambda _: None,
        _discard_prefetch=lambda: None,
        _clear_terrain_forecast=lambda: None,
        drain_events=lambda: [],
        decide=decide,
        stop_reason=stop_reason,
        follow=Recorder(),
    )
    monkeypatch.setattr(
        native_driver,
        "policy_state",
        lambda _c: {"body": {"position": [0.5, 1, 0.5]}, **next(rows)},
    )
    monkeypatch.setattr(native_driver.time, "sleep", lambda _: None)
    monkeypatch.setattr(control_loop, "_running", lambda _c, row: row.get("terminal"))
    end = native_driver.drive_native(client, controller, 10)
    assert end == {"type": "navigation_stopped", "data": {"reason": "arrived"}}
    follows = [tick for kind, tick in seen if kind == "follow"]
    assert follows and follows[-1] == 3
    assert seen.index(("follow", 3)) < seen.index(("stop?", 3))


# --- defence ------------------------------------------------------------------------

ZOMBIE = "minecraft:zombie"
SWORD = "minecraft:iron_sword"
NIGHT, DAY = 18000, 1000


def _mob(uuid="z1", pos=(4.5, 64.0, 0.5), kind=ZOMBIE):
    return {"type": kind, "uuid": uuid, "position": list(pos)}


def _sword():
    return [{"slot": 0, "item": SWORD, "count": 1}]


def _day_row(*entities, health=20.0, inv=(), tick=10, time=NIGHT, food=18):
    row = _row(health=health, entities=entities, tick=tick, inv=inv)
    row["body"].update(time_of_day=time, food=food)
    return row


def _seen_now(bridge, *entities, **kw):
    """The sample the guard reads: the newest one the bridge holds."""
    bridge.samples.append(_day_row(*entities, **kw))


class Plan:
    """A stand-in burrow plan (``companion_burrow.Burrow`` is patched to this)."""


class DefenseBody(FakeBody):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fights, self.containers, self.escapes, self.trials = [], [], [], []
        self.stance_ok = True
        self.ate = 0

    def fight(self, **fields):
        self.log.append(("fight", fields))
        return self.fights.pop(0) if self.fights else Result(True, None, {"ticks": 200})

    def escape(self, **fields):
        self.log.append(("escape", fields))
        return self.escapes.pop(0) if self.escapes else Result(True, None, {})

    def trial(self, **fields):
        self.log.append(("trial", fields))
        return self.trials.pop(0)

    def goto_stance(self, pos, reach, avoid=()):
        self.log.append(("stance", tuple(pos), reach))
        return self.stance_ok

    def container(self, pos, action, items=None):
        self.log.append(("container", tuple(pos), action, items))
        return self.containers.pop(0)

    def eat_when_needed(self):
        self.ate += 1
        return False


@pytest.fixture
def guard(make):
    def build(**kw):
        session, bridge, places, _, _ = make(body=DefenseBody, **kw)
        session.start()
        session.body.log.clear()
        return session, bridge, places

    return build


def _logged(body, kind):
    return [e for e in body.log if e[0] == kind]


def test_no_hostile_means_no_threat(guard):
    session, bridge, _ = guard()
    _seen_now(bridge, _player("Marc", (3, 64, 0)))
    assert session.threat().kind == "none"


def test_no_threat_while_the_body_is_not_in_the_world(guard):
    session, bridge, _ = guard()
    row = _day_row(_mob())
    row["body"]["position"] = None
    bridge.samples.append(row)
    assert session.threat().kind == "none"


def test_threat_reads_the_newest_sample_and_never_polls(guard):
    # the guard runs inside bridge.state(): a poll from threat() would recurse
    session, bridge, _ = guard()
    _seen_now(bridge, _mob(), inv=_sword())
    count = len(bridge.samples)
    action = session.threat()
    assert (action.kind, action.target, action.item) == ("fight", "z1", SWORD)
    assert len(bridge.samples) == count


def test_a_loyal_trident_in_the_inventory_is_thrown_at_a_skeleton(guard):
    session, bridge, _ = guard()
    trident = {"slot": 1, "item": "minecraft:trident", "count": 1,
               "enchantments": {"minecraft:loyalty": 3}}  # fmt: skip
    skeleton = _mob("s1", kind="minecraft:skeleton")
    _seen_now(bridge, skeleton, inv=[*_sword(), trident])
    action = session.threat()
    assert (action.kind, action.tactic.style) == ("fight", "throw")


def test_threat_is_decided_once_per_tick(guard, monkeypatch):
    session, bridge, _ = guard()
    calls = []
    real = cs.defense.decide
    monkeypatch.setattr(
        cs.defense, "decide", lambda s, p: calls.append(1) or real(s, p)
    )
    _seen_now(bridge, _mob(), inv=_sword(), tick=10)
    session.threat()
    session.threat()
    _seen_now(bridge, _mob(), inv=_sword(), tick=11)
    session.threat()
    assert len(calls) == 2


def test_the_spawn_home_is_no_base_but_a_named_home_or_base_is(guard, monkeypatch):
    session, bridge, places = guard()
    monkeypatch.setattr(cs.burrow, "plan_burrow", lambda *a: "open floor: a cave")
    _seen_now(bridge, _mob(), health=4, tick=10)
    assert session.threat().kind == "escape"  # home is only where it first spawned
    places.add("home", (-20, 64, 0), kind="home", provenance="named")
    _seen_now(bridge, _mob(), health=4, tick=11)
    assert session.threat().where == (-20, 64, 0)
    places.add("base", (20, 64, 0))
    _seen_now(bridge, _mob(), health=4, tick=12)
    action = session.threat()
    assert (action.kind, action.where) == ("shelter", (20, 64, 0))


def test_low_health_burrows_where_the_ground_allows(guard, monkeypatch):
    session, bridge, _ = guard()
    seen = []
    monkeypatch.setattr(
        cs.burrow,
        "plan_burrow",
        lambda cells, feet, inv, k: seen.append(feet) or Plan(),
    )
    monkeypatch.setattr(cs.burrow, "Burrow", Plan)
    _seen_now(bridge, _mob(), health=4, tick=10)
    assert session.threat().kind == "burrow"
    assert seen == [(0, 64, 0)]


def test_threat_knows_the_day_and_whether_the_bot_can_heal(guard, monkeypatch):
    session, bridge, _ = guard()
    monkeypatch.setattr(cs.burrow, "plan_burrow", lambda *a: Plan())
    monkeypatch.setattr(cs.burrow, "Burrow", Plan)
    _seen_now(bridge, _mob(pos=(9.5, 64.0, 0.5)), tick=10, time=DAY)
    assert session.threat().kind == "walk_away"  # by day: walk away, no hole
    _seen_now(bridge, _mob(), tick=11, time=DAY, health=4, food=10)
    assert (
        session.threat().kind == "escape"
    )  # hungry with no food: a hole heals nothing
    bread = [{"slot": 1, "item": "minecraft:bread", "count": 2}]
    _seen_now(bridge, _mob(), tick=12, time=DAY, health=4, food=10, inv=bread)
    assert session.threat().kind == "burrow"
    row = _day_row(_mob(), tick=13, health=4, food=10)
    del row["body"]["time_of_day"]  # an older body: no clock, taken as day
    bridge.samples.append(row)
    assert session.threat().kind == "escape"


def test_a_weapon_in_a_chest_at_the_base_is_fetched(guard):
    session, bridge, places = guard()
    places.add("base", (20, 64, 0))
    session.gather_state.containers[(21, 64, 1)] = {SWORD: 1}
    # a better sword, near the bot but not at the base: not the base's
    session.gather_state.containers[(-40, 64, 0)] = {"minecraft:diamond_sword": 1}
    _seen_now(bridge, _mob(), tick=10)
    action = session.threat()
    assert (action.kind, action.where, action.item) == ("arm", (21, 64, 1), SWORD)


def test_defend_fights_with_the_policys_floor_and_radius(guard):
    session, _, _ = guard(defense=DefensePolicy(floor_health=8, threat_radius=10))
    session.defend(
        Action("fight", "fighting", target="z1", item=SWORD, anchor=(3.5, 64, 0.5),
               leash=6.0)
    )  # fmt: skip
    ((_, fields),) = _logged(session.body, "fight")
    assert {k: v for k, v in fields.items() if v is not None} == {
        "entity": "z1",
        "weapon": SWORD,
        "radius": 10,
        "floor": 8,
        "max_ticks": cs.FIGHT_TICKS,
        "anchor": (3.5, 64, 0.5),
        "leash": 6,
    }
    assert not _logged(session.body, "wait")


def test_defend_widens_the_fight_to_an_archer_past_the_radius(guard):
    session, _, _ = guard(defense=DefensePolicy(threat_radius=10))
    archer = Threat("s1", "minecraft:skeleton", (14.5, 64.0, 0.5), 14.4)
    session.defend(
        Action("fight", "fighting", target="s1", item=SWORD, threats=(archer,))
    )
    ((_, fields),) = _logged(session.body, "fight")
    assert fields["radius"] == 15


def test_defend_passes_the_tactic_to_the_body(guard):
    session, _, _ = guard()
    plan = Tactic("snipe", "minecraft:bow", keep=(8.0, 15.0), melee=SWORD)
    session.defend(
        Action("fight", "fighting", target="s1", item=plan.weapon, tactic=plan)
    )
    ((_, fields),) = _logged(session.body, "fight")
    assert fields["tactic"] == plan.wire() and fields["weapon"] == "minecraft:bow"


def test_a_fight_that_ends_at_once_waits_but_low_health_does_not(guard):
    session, _, _ = guard()
    body = session.body
    body.fights = [
        Result(True, None, {"ticks": 2}),
        Result(False, "busy", {}),
        Result(False, "low_health", {}),
        Result(False, "avoid", {}),
    ]
    for _ in range(4):
        session.defend(Action("fight", "fighting", target="z1", item=SWORD))
    assert _logged(body, "wait") == [
        ("wait", cs.DEFEND_WAIT_TICKS - 2),
        ("wait", cs.DEFEND_WAIT_TICKS),
    ]


def test_a_mob_the_fight_could_not_reach_is_left_alone_for_a_while(guard):
    session, bridge, _ = guard()
    now = [100.0]
    session.clock = lambda: now[0]
    session.body.fights = [Result(False, "unreachable", {})]
    session.defend(Action("fight", "fighting", target="z1", item=SWORD))
    assert not _logged(session.body, "wait")  # decide again at once
    _seen_now(bridge, _mob(), inv=_sword(), tick=11)
    action = session.threat()
    assert action.kind != "fight" and "out of reach" in action.reason
    now[0] += cs.OUT_OF_REACH_S + 1  # it may have come down since
    _seen_now(bridge, _mob(), inv=_sword(), tick=12)
    assert session.threat().kind == "fight"


def test_defend_reports_the_fight_outcome_without_the_inventory(guard):
    session, _, _ = guard()
    session.body.fights = [
        Result(True, None, {"kills": 1, "hits": 3, "crits": 2, "shots": 0,
                            "damage": 20.0, "style": "melee", "ticks": 80,
                            "health": 18.0, "end": "clear", "flees": 1,
                            "closest": 3.9, "inventory": [{"slot": 0}]})
    ]  # fmt: skip
    outcome = session.defend(Action("fight", "fighting", target="z1", item=SWORD))
    assert outcome == {
        "action": "fight", "weapon": SWORD, "ok": True, "reason": None, "kills": 1,
        "hits": 3,
        "crits": 2, "shots": 0, "damage": 20.0, "style": "melee", "ticks": 80,
        "health": 18.0, "end": "clear", "flees": 1, "closest": 3.9,
    }  # fmt: skip


def test_defend_reports_a_spear_charges_evidence(guard):
    session, _, _ = guard()
    hit = {"tick": 31, "eye": 3.9, "dealt": 8.0, "speed": 5.6, "after": "charge"}
    session.body.fights = [
        Result(True, None, {"kills": 1, "style": "lance", "lance": {"charge": 9},
                            "charge_log": [hit], "flee_log": [{"tick": 1}]})
    ]  # fmt: skip
    outcome = session.defend(Action("fight", "fighting", target="z1", item=SWORD))
    assert (outcome["lance"], outcome["charge_log"]) == ({"charge": 9}, [hit])
    assert "flee_log" not in outcome  # the step event stays small


CHEST = {"slot": 4, "item": "minecraft:iron_chestplate", "count": 1}
SHIELD = {"slot": 5, "item": "minecraft:shield", "count": 1}


def test_idle_puts_on_armor_and_a_shield_the_bot_carries_once(make):
    # armor given, picked up or crafted is worn: a full iron set halves a zombie's
    # hits, and the shield goes in the off hand
    session, bridge, *_ = make()
    session.start()
    session.body.log.clear()
    bridge.default = _row(inv=[CHEST, SHIELD])

    session.idle()
    session.idle()  # the same gear carried: asked once (a worse piece stays carried)

    assert session.body.log.count(("wear",)) == 1
    boots = {**CHEST, "slot": 6, "item": "minecraft:iron_boots"}
    bridge.default = _row(inv=[CHEST, SHIELD, boots])
    session.idle()
    assert session.body.log.count(("wear",)) == 2


def test_idle_with_nothing_to_put_on_never_asks(make):
    session, bridge, *_ = make()
    session.start()
    session.body.log.clear()
    bridge.default = _row(inv=[{**CHEST, "slot": 38}])  # already worn
    session.idle()
    assert ("wear",) not in session.body.log


def test_defend_dresses_before_the_fight(guard):
    session, _, _ = guard()
    session.body.rows = lambda: [CHEST]
    session.defend(Action("fight", "fighting", target="z1", item=SWORD))
    kinds = [entry[0] for entry in session.body.log]
    assert kinds.index("wear") < kinds.index("fight")


def test_defend_reports_what_a_fight_step_fought_with_what_and_killed(guard):
    # a raid trial reads which weapon met which mob, and an evoker's vexes are
    # counted as vexes, not as the evoker
    session, _, _ = guard()
    session.body.fights = [
        Result(True, None, {"kills": 2, "style": "melee",
                            "kill_types": {"minecraft:vex": 2}})
    ]  # fmt: skip
    evoker = Threat("e1", "minecraft:evoker", (5.5, 64.0, 0.5), 5.0)
    action = Action("fight", "fighting", target="e1", item=SWORD, threats=(evoker,))

    outcome = session.defend(action)

    assert outcome["kill_types"] == {"minecraft:vex": 2}
    assert (outcome["target_type"], outcome["weapon"]) == ("minecraft:evoker", SWORD)
    # a target the scan did not list (engaged by uuid alone) has no type to report
    session.body.fights = [Result(True, None, {"kills": 0})]
    bare = session.defend(Action("fight", "fighting", target="z9", item=SWORD))
    assert "target_type" not in bare and bare["weapon"] == SWORD


def test_defend_reports_the_damage_a_fight_took_and_what_killed_the_body(guard):
    session, _, _ = guard()
    hurt = {"minecraft:ravager": 24.0}
    session.body.fights = [
        Result(False, "died", {"hurt_by": hurt, "killed_by": "minecraft:ravager",
                               "raises": 4, "block_ticks": 52})
    ]  # fmt: skip
    outcome = session.defend(Action("fight", "fighting", target="r1", item=SWORD))
    assert outcome["reason"] == "died"
    assert (outcome["raises"], outcome["block_ticks"]) == (4, 52)  # the shield's use
    assert (outcome["hurt_by"], outcome["killed_by"]) == (hurt, "minecraft:ravager")


def test_defend_reports_an_escape_and_a_step_with_no_body_op(guard):
    session, _, _ = guard()
    data = {"end": "clear", "jumps": 9, "distance": 21.5, "nearest": 17.0, "ticks": 90}
    session.body.escapes = [Result(True, None, data), Result(False, "cornered", {})]
    assert session.defend(Action("escape", "getting away")) == {
        "action": "escape",
        "ok": True,
        "reason": None,
        **data,
    }
    cornered = session.defend(Action("escape", "getting away"))
    assert (cornered["ok"], cornered["reason"]) == (False, "cornered")
    session.body.walk_ok = True
    assert session.defend(Action("walk_away", "walking away")) == {
        "action": "walk_away"
    }


def test_trial_sets_the_scene_through_the_body(guard):
    session, _, _ = guard()
    body = session.body
    summoned = [{"type": ZOMBIE, "uuid": "u1"}]
    body.trials = [
        Result(True, None, {"summoned": summoned, "given": 1, "inventory": []}),
        Result(False, "unknown_item", {}),
    ]
    spec = {"clear": 24, "give": [{"item": SWORD, "count": 1}], "time": 6000}
    assert session.trial(spec) == {"summoned": summoned, "given": 1}
    assert _logged(body, "trial") == [("trial", spec)]
    with pytest.raises(ValueError, match="unknown_item"):
        session.trial({"give": [{"item": "minecraft:nope", "count": 1}]})
    session.body = None  # respawning: refused, not a 500
    with pytest.raises(ValueError, match="body_not_ready"):
        session.trial(spec)


def test_a_target_the_body_refuses_is_fought_as_the_nearest_instead(guard):
    session, _, _ = guard()
    body = session.body
    body.fights = [Result(False, "not_allowed", {}), Result(True, None, {"ticks": 50})]
    session.defend(Action("fight", "fighting", target="z1", item=SWORD))
    fights = [fields for _, fields in _logged(body, "fight")]
    assert fights[0]["entity"] == "z1" and fights[1]["entity"] is None


def test_walk_away_walks_from_the_threats_and_tries_other_headings(guard):
    session, _, _ = guard()
    body = session.body  # feet (0, 64, 0)
    threat = Threat("z1", ZOMBIE, (5.5, 64.0, 0.5), 5.0)
    session.defend(Action("walk_away", "walking away", threats=(threat,)))
    ((_, goal, radius, _),) = _logged(body, "walk_to")
    assert goal == (-cs.FLEE_DISTANCE, 64, 0) and radius == cs.FLEE_RADIUS
    body.log.clear()
    body.walk_ok = False
    session.defend(Action("walk_away", "walking away", threats=(threat,)))
    goals = [g for _, g, _, _ in _logged(body, "walk_to")]
    assert len(goals) == len(cs.FLEE_TURNS) == len(set(goals))
    assert _logged(body, "wait") == [("wait", cs.DEFEND_WAIT_TICKS)]
    assert not _logged(body, "escape")


def test_escape_runs_the_bodys_sprinting_escape(guard):
    session, _, _ = guard(defense=DefensePolicy(threat_radius=10))
    threat = Threat("z1", ZOMBIE, (5.5, 64.0, 0.5), 5.0)
    session.defend(Action("escape", "running", threats=(threat,)))
    ((_, fields),) = _logged(session.body, "escape")
    assert fields == {
        "radius": cs.ESCAPE_SCAN,
        "clear": cs.ESCAPE_CLEAR,
        "max_ticks": cs.ESCAPE_TICKS,
    }
    assert not _logged(session.body, "walk_to")


def test_a_cornered_escape_falls_back_to_a_planned_walk(guard):
    # the per-tick escape only looks one cell ahead; the route planner sees further
    session, _, _ = guard()
    body = session.body
    body.escapes = [Result(False, "cornered", {})]
    threat = Threat("z1", ZOMBIE, (5.5, 64.0, 0.5), 5.0)
    session.defend(Action("escape", "running", threats=(threat,)))
    assert [g for _, g, _, _ in _logged(body, "walk_to")][0] == (
        -cs.FLEE_DISTANCE,
        64,
        0,
    )


def test_regroup_walks_to_the_ally_then_stays_beside_them(guard):
    session, _, _ = guard()
    body = session.body
    ally = Action("regroup", "staying with Marc", where=(10.5, 64.0, 0.5))
    session.defend(ally)
    assert _logged(body, "walk_to") == [("walk_to", (10, 64, 0), cs.REGROUP_NEAR, None)]
    body.log.clear()
    body.pos = (9, 64, 0)
    session.defend(ally)
    assert body.log == [("wait", cs.DEFEND_WAIT_TICKS)] and body.ate == 1


def test_shelter_walks_home_then_waits_there(guard):
    session, _, _ = guard()
    body = session.body
    home = Action("shelter", "going home", where=(30, 64, 0))
    session.defend(home)
    assert body.log == [("walk_far", (30, 64, 0), cs.BASE_NEAR)]
    body.log.clear()
    session.defend(home)  # the walk put it there
    assert body.log == [("wait", cs.DEFEND_WAIT_TICKS)] and body.ate == 1


def test_arm_takes_the_weapon_and_learns_what_the_chest_holds(guard):
    session, _, places = guard()
    body = session.body
    chest = (21, 64, 1)
    session.gather_state.containers[chest] = {SWORD: 1}
    left = [{"slot": 0, "item": "minecraft:bread", "count": 2}]
    body.containers = [Result(True, None, {"contents": left})]
    session.defend(Action("arm", "fetching", item=SWORD, where=chest))
    assert _logged(body, "stance") == [("stance", chest, cs.STATION_REACH)]
    assert _logged(body, "container") == [
        ("container", chest, "withdraw", [{"item": SWORD, "count": 1}])
    ]
    assert session.gather_state.containers[chest] == {"minecraft:bread": 2}
    assert places.saved[chest] == {"minecraft:bread": 2}


def test_a_chest_that_failed_is_left_until_the_threat_is_gone(guard):
    session, bridge, places = guard()
    body = session.body
    places.add("base", (20, 64, 0))
    chest = (21, 64, 1)
    session.gather_state.containers[chest] = {SWORD: 1}
    body.containers = [Result(False, "cannot_open", {})]
    _seen_now(bridge, _mob(), tick=10)
    session.defend(session.threat())
    _seen_now(bridge, _mob(), tick=11)
    assert session.threat().kind == "shelter"
    _seen_now(bridge, tick=12)  # gone: the next defence may try that chest again
    assert session.threat().kind == "none"
    _seen_now(bridge, _mob(), tick=13)
    assert session.threat().kind == "arm"
    body.containers = [Result(False, "not_a_container", {})]
    session.defend(session.threat())
    assert chest not in session.gather_state.containers


def test_arm_that_cannot_reach_the_chest_leaves_it(guard):
    session, bridge, places = guard()
    body = session.body
    body.stance_ok = False
    places.add("base", (20, 64, 0))
    session.gather_state.containers[(21, 64, 1)] = {SWORD: 1}
    _seen_now(bridge, _mob(), tick=10)
    session.defend(session.threat())
    assert not _logged(body, "container")
    _seen_now(bridge, _mob(), tick=11)
    assert session.threat().kind == "shelter"


@pytest.fixture
def hole(monkeypatch):
    steps = []
    monkeypatch.setattr(cs.burrow, "plan_burrow", lambda cells, feet, inv, k: Plan())
    monkeypatch.setattr(cs.burrow, "Burrow", Plan)
    monkeypatch.setattr(
        cs.burrow, "dig_in", lambda body, plan: steps.append("in") or Result(True)
    )
    monkeypatch.setattr(
        cs.burrow, "dig_out", lambda body, plan: steps.append("out") or Result(True)
    )
    return steps


def test_burrow_waits_sealed_until_healed_and_day(guard, hole):
    session, bridge, _ = guard()
    bridge.rows = [
        _day_row(health=4),
        _day_row(health=14),  # healed, but unarmed at night: stay
        _day_row(health=14, time=DAY),
    ]
    session.defend(Action("burrow", "hiding"))
    assert hole == ["in", "out"]
    assert len(_logged(session.body, "wait")) == 2 and session.body.ate == 2


def test_an_armed_bot_leaves_the_hole_once_healed_even_at_night(guard, hole):
    session, bridge, _ = guard()
    bridge.rows = [_day_row(health=14, inv=_sword())]
    session.defend(Action("burrow", "hiding"))
    assert hole == ["in", "out"] and not _logged(session.body, "wait")


def test_a_bot_that_cannot_heal_leaves_at_day_anyway(guard, hole):
    session, bridge, _ = guard()
    bridge.rows = [_day_row(health=4, food=10, time=DAY)]  # hungry, no food held
    session.defend(Action("burrow", "hiding"))
    assert hole == ["in", "out"]


def test_a_body_that_dies_in_the_hole_raises(guard, hole):
    session, bridge, _ = guard()
    bridge.rows = [_row(state="dead")]
    with pytest.raises(BodyDied):
        session.defend(Action("burrow", "hiding"))


def test_a_burrow_that_failed_is_not_planned_at_that_spot_again(guard, monkeypatch):
    session, bridge, _ = guard()
    monkeypatch.setattr(cs.burrow, "plan_burrow", lambda cells, feet, inv, k: Plan())
    monkeypatch.setattr(cs.burrow, "Burrow", Plan)
    monkeypatch.setattr(
        cs.burrow, "dig_in", lambda body, plan: Result(False, "did_not_drop")
    )
    session.defend(Action("burrow", "hiding"))
    _seen_now(bridge, _mob(), health=4, tick=20)
    assert session.threat().kind == "escape"
    _seen_now(bridge, _mob(), health=4, tick=21)  # somewhere else may still do
    bridge.samples[-1]["body"]["position"] = [5.5, 64.0, 5.5]
    assert session.threat().kind == "burrow"


def test_defense_settings_change_persist_and_reload(make, tmp_path):
    saved = tmp_path / "defense.json"
    session, *_ = make(defense=DefensePolicy(threat_radius=16), defense_file=saved)
    assert session.defense_settings({}) == {
        "floor_health": 6,
        "floor_hearts": 3.0,
        "threat_radius": 16,
        "ally_radius": 32,
        "base_radius": 64,
    }
    assert session.defense_settings({"floor_health": 10})["floor_hearts"] == 5.0
    assert session.defense_policy.floor_health == 10
    again, *_ = make(defense=DefensePolicy(threat_radius=16), defense_file=saved)
    assert again.defense_policy == DefensePolicy(floor_health=10, threat_radius=16)


def test_defense_settings_default_file_and_bad_saved_settings(make):
    session, *_ = make()
    assert session.defense_file == session.state_file.with_name(
        "companion-defense.json"
    )
    for bad in ("{not json", '{"hearts": 3}', '{"floor_health": 99}'):
        session.defense_file.parent.mkdir(parents=True, exist_ok=True)
        session.defense_file.write_text(bad)
        again, *_ = make()
        assert again.defense_policy == DefensePolicy()
