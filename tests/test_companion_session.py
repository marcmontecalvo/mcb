"""Companion session: the host's one body in a kept world (fakes, no server)."""

import json
from types import SimpleNamespace

import pytest

from mclab import companion_session as cs
from mclab import follow as follow_mod
from mclab import navigation_segmented
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

    def _drop_ahead(self):
        self.log.append(("drop_ahead",))


class FakeMemory:
    closed = False

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

    def build(bridge=None, *, state=None, knowledge=None, clock=None):
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
            make_body=FakeBody,
            awareness=FakeAwareness(),
            say_client=say,
            knowledge=knowledge
            or (lambda: SimpleNamespace(foods={"minecraft:bread": 5})),
            clock=clock or (lambda: 0.0),
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
    assert fields["position"] == [0.5, 80, 0.5]  # no saved state, no world spawn
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
    session.bridge = FakeBridge(http_state={"world_spawn": [1, 2, 3]})
    assert session.spawn_position() == (10, 70, -4)

    other, *_ = make(state={"bot": "Someone", "position": [10, 70, 3]})
    other.bridge = FakeBridge(http_state={"world_spawn": [1.2, 65, 3.9]})
    assert other.spawn_position() == (1, 65, 3)

    broken, *_ = make(state={"bot": "MCBot", "position": ["x", 1, 2]})
    broken.bridge = FakeBridge(http_state=OSError("down"))
    assert broken.spawn_position() == cs.FALLBACK_SPAWN


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
    assert op == "spawn" and fields["position"] == [4.5, 64, 4.5]
    assert fields["keep_state"] is True
    assert session.body is not old


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

    env = SimpleNamespace(plan=None, report=GatherReport(stop="done"), gain=0, calls=[])

    def fake_plan(wanted, inventory, knowledge, stations, **kw):
        env.calls.append(("plan", dict(wanted), kw))
        return env.plan or SimpleNamespace(tasks=["t1", "t2"], missing=[])

    def fake_run(plan_result, body, knowledge, wanted, *, state, opts):
        env.calls.append(("run", dict(wanted)))
        for item in wanted:
            body.inv[item] = body.inv.get(item, 0) + env.gain
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
