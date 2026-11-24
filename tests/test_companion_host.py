"""Companion host: jobs, queue, authority, cancel, death and events (fake session)."""

import threading
import time

import pytest

from mclab import companion_host, companion_intents
from mclab.companion_defense import Action, DefensePolicy
from mclab.companion_host import Host
from mclab.companion_intents import Actor
from mclab.companion_protocol import TERMINAL_STATES
from mclab.companion_session import JobCancelled, JobFailed
from mclab.progress_watch import BodyDied

OPERATOR = Actor("mcp")
PLAYER = Actor("chat", name="Steve", uuid="u-1", position=(4.5, 64.0, 2.5))


def block(session, args, actor, progress):
    """A long job: polls the bridge (the cancel hook) until the gate opens."""
    session.blocking.set()
    while not session.gate.is_set():
        session.poll()
        time.sleep(0.002)
    return {"blocked": True}


class FakeSession:
    def __init__(self):
        self.poll_hook = None
        self.started = False
        self.closed = None
        self.halts = 0
        self.said = []
        self.places = {"home": [0, 64, 0]}
        self.runs = []
        self.position = [0.5, 64.0, 0.5]
        self.dead = False
        self.health_value = "ok"
        self.respawns = 0
        self.gate = threading.Event()
        self.blocking = threading.Event()
        self.behaviour = {}
        self.place_threads = []
        self.progress_records = []
        self.threats = []  # actions threat() hands out, one per call
        self.threat_gate = threading.Event()  # threats are seen only once it is set
        self.threat_gate.set()
        self.defended = []
        self.defend_hook = None  # called inside defend (after a poll)
        self.defense = DefensePolicy()
        self.trials = []
        self.trial_result = {"summoned": []}

    def start(self):
        self.started = True

    def threat(self):
        if self.threats and self.threat_gate.is_set():
            return self.threats.pop(0)
        return Action("none", "no threat near")

    def defend(self, action):
        self.defended.append(action.kind)
        self.poll()
        if self.defend_hook is not None:
            self.defend_hook(self, action)
        return {"action": action.kind, "ok": True, "kills": 1}

    def trial(self, spec):
        self.trials.append((spec, threading.current_thread().name))
        if isinstance(self.trial_result, Exception):
            raise self.trial_result
        return self.trial_result

    def defense_settings(self, changes):
        if changes:
            self.defense = self.defense.changed(**changes)
        return self.defense.as_dict()

    def poll(self):
        if self.poll_hook is not None:
            self.poll_hook()

    def idle(self):
        self.poll()
        if self.dead:
            raise BodyDied("observation has no position: the body is dead")
        return self.snapshot()

    def snapshot(self):
        return {
            "tick": 1,
            "body_state": "ready",
            "position": list(self.position),
            "health": 20.0,
            "food": 20,
            "inventory": {"minecraft:dirt": 3},
            "players": [
                {"name": "Steve", "position": [4.5, 64.0, 2.5], "distance": 4.5}
            ],
        }

    def run(self, intent, args, actor, progress):
        self.runs.append((intent, args, actor))
        return self.behaviour.get(intent, block)(self, args, actor, progress)

    def halt(self):
        self.halts += 1

    def health(self):
        return self.health_value

    def respawn(self):
        self.respawns += 1
        self.dead = False
        return {"death": [9, 64, 9], "position": [0, 64, 0]}

    def say(self, text, to=None):
        self.said.append((text, to))

    def list_places(self):
        self.place_threads.append(threading.current_thread().name)
        return [{"name": n, "position": p} for n, p in self.places.items()]

    def remember_place(self, name, pos):
        self.place_threads.append(threading.current_thread().name)
        self.places[name] = list(pos or [0, 64, 0])
        return {"name": name, "position": self.places[name]}

    def record_progress(self, job):
        self.progress_records.append(job["progress"])

    def close(self, despawn=False):
        self.closed = {"despawn": despawn}


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def made():
    return []


@pytest.fixture
def host(made, monkeypatch):
    monkeypatch.setattr(companion_intents, "_allowlisted", lambda uuid, name="": True)

    def factory():
        session = FakeSession()
        made.append(session)
        return session

    value = Host(factory, bot={"name": "MCBot"}, idle_s=0.01, retry_s=(0.01, 0.02))
    value.start()
    assert value.wait_ready(5)
    yield value
    for session in made:
        session.gate.set()
    value.shutdown(timeout=5)


def submit(host, intent="goto", key=None, actor=OPERATOR, **args):
    if intent == "goto" and not args:
        args = {"x": 1, "y": 64, "z": 1}
    reply = host.call(intent, args, actor, key=key)
    assert reply["ok"], reply
    return reply["result"]["job"]


def wait_state(host, job_id, state, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = host.call("job_status", {"job_id": job_id}, OPERATOR)["result"]["job"]
        if job["state"] == state:
            return job
        time.sleep(0.005)
    raise AssertionError(f"{job_id} never reached {state}: {job}")


def types(host):
    return [e["type"] for e in host.events()["events"]]


# --- lifecycle ---------------------------------------------------------------------


def test_a_job_runs_to_success_with_its_progress_and_events(host, made):
    def walk(session, args, actor, progress):
        progress(stage="walking", done=1, total=2, detail="leg 1")
        return {"arrived": "1 64 1"}

    made[0].behaviour["goto"] = walk
    job = submit(host)
    assert job["id"].startswith("j-") and len(job["id"]) == 10
    assert job["state"] in ("queued", "running")
    done = host.wait_job(job["id"], 5)
    assert done["state"] == "succeeded" and done["result"] == {"arrived": "1 64 1"}
    assert done["progress"] == {
        "stage": "walking",
        "done": 1,
        "total": 2,
        "detail": "leg 1",
    }
    assert done["actor"] == {
        "source": "mcp",
        "name": None,
        "uuid": None,
        "position": None,
    }
    assert done["args"]["target"] == {"pos": (1, 64, 1)}
    assert done["started_at"] is not None and done["finished_at"] >= done["started_at"]
    kinds = types(host)
    order = [k for k in kinds if k.startswith("job_")]
    assert order == ["job_queued", "job_started", "job_progress", "job_finished"]
    assert made[0].progress_records  # progress.json is written by the session


def test_a_job_step_is_an_event_of_its_own_never_throttled(host, made):
    """A job's body steps (an attack's fights) each land as a job_step event, however
    close together: a raid's analysis reads which weapon met which mob, what hurt."""

    def fight(session, args, actor, progress):
        progress(stage="attacking", detail="pillager")
        progress(step={"weapon": "minecraft:iron_sword", "hurt_by": {"pillager": 4.0}})
        progress(step={"weapon": "minecraft:bow"})
        return {"ok": 1}

    made[0].behaviour["goto"] = fight
    job = submit(host)
    done = host.wait_job(job["id"], 5)
    assert done["state"] == "succeeded"
    assert done["progress"]["stage"] == "attacking"  # a step is not progress
    steps = [e["data"] for e in host.events()["events"] if e["type"] == "job_step"]
    assert steps == [
        {"id": job["id"], "intent": "goto", "step": {"weapon": "minecraft:iron_sword",
                                                     "hurt_by": {"pillager": 4.0}}},
        {"id": job["id"], "intent": "goto", "step": {"weapon": "minecraft:bow"}},
    ]  # fmt: skip


def test_now_cancels_the_running_job_and_drops_the_queue(host, made):
    first = submit(host)
    assert made[0].blocking.wait(5)
    queued = submit(host, when="next", x=2, y=64, z=2)
    replacing = submit(host, x=3, y=64, z=3)
    assert host.wait_job(first["id"], 5)["state"] == "cancelled"
    assert host.wait_job(queued["id"], 5)["state"] == "cancelled"
    wait_state(host, replacing["id"], "running")
    assert made[0].halts >= 1  # cancel_route + release after the cancel
    assert [r[1]["target"]["pos"] for r in made[0].runs] == [(1, 64, 1), (3, 64, 3)]


def test_next_appends_after_the_running_job(host, made):
    first = submit(host)
    assert made[0].blocking.wait(5)
    second = submit(host, when="next", x=2, y=64, z=2)
    assert (
        host.call("job_status", {"job_id": second["id"]}, OPERATOR)["result"]["job"][
            "state"
        ]
        == "queued"
    )
    status = host.call("status", {}, OPERATOR)["result"]
    assert status["job"]["id"] == first["id"]
    assert [j["id"] for j in status["queue"]] == [second["id"]]
    made[0].gate.set()
    assert host.wait_job(first["id"], 5)["state"] == "succeeded"
    assert host.wait_job(second["id"], 5)["state"] == "succeeded"


def test_the_same_key_returns_the_same_job(host, made):
    made[0].gate.set()
    first = submit(host, key="k-1")
    again = submit(host, key="k-1", x=9, y=64, z=9)
    assert again["id"] == first["id"]
    host.wait_job(first["id"], 5)
    assert len(made[0].runs) == 1


def test_stop_with_nothing_running_is_safe(host):
    reply = host.call("stop", {}, OPERATOR)
    assert reply == {"ok": True, "result": {"stopped": None, "cancelled": []}}


def test_stop_cancels_through_the_bridge_poll_and_the_worker_goes_on(host, made):
    job = submit(host, "follow", player="Steve")
    assert made[0].blocking.wait(5)
    reply = host.call("stop", {}, OPERATOR)
    assert reply["ok"] and reply["result"]["stopped"] == job["id"]
    done = host.wait_job(job["id"], 5)
    assert done["state"] == "cancelled" and done["error"] == "stopped"
    assert made[0].halts == 1 and made[0].closed is None  # the session stays
    made[0].gate.set()
    after = submit(host)
    assert host.wait_job(after["id"], 5)["state"] == "succeeded"


def test_a_cancel_never_reaches_the_next_job(host, made):
    def quick(session, args, actor, progress):
        return {"quick": True}

    made[0].behaviour["gather"] = quick
    job = submit(host, "gather", item="oak_log", count=1)
    host.wait_job(job["id"], 5)
    host.call("stop", {}, OPERATOR)
    made[0].gate.set()
    after = submit(host)
    assert host.wait_job(after["id"], 5)["state"] == "succeeded"


# --- defence -------------------------------------------------------------------------

FIGHT = Action("fight", "fighting the zombie", target="z1", item="minecraft:iron_sword")


def _until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)
    return condition()


def test_a_threat_interrupts_the_job_which_then_carries_on(host, made):
    session = made[0]
    runs = []

    def walk(session, args, actor, progress):
        runs.append(1)
        if len(runs) == 1:
            session.threats.append(FIGHT)
            session.poll()  # the guard sees the zombie here
            raise AssertionError("the guard did not interrupt the job")
        return {"arrived": True}

    session.behaviour["goto"] = walk
    job = submit(host)
    done = host.wait_job(job["id"], 5)
    assert done["state"] == "succeeded" and done["result"] == {"arrived": True}
    assert session.defended == ["fight"]
    assert len(runs) == 2  # the job ran again from the world as it is now
    assert session.halts >= 1  # the job's motion stopped before the fight
    stages = [e["data"]["progress"]["stage"] for e in host.events()["events"]
              if e["type"] == "job_progress"]  # fmt: skip
    assert "defending" in stages


def test_the_guard_keeps_going_until_nothing_threatens(host, made):
    session = made[0]
    burrow = Action("burrow", "hurt: hiding in a hole")
    session.threats.extend([FIGHT, burrow])  # seen while idle, then after the fight
    assert _until(lambda: session.defended == ["fight", "burrow"])
    said = [text for text, _ in session.said]
    assert _until(lambda: len(session.said) >= 2)
    said = [text for text, _ in session.said]
    assert "fighting the zombie" in said[0].lower()
    assert "hiding in a hole" in said[1].lower()
    kinds = [e["data"]["action"] for e in host.events()["events"]
             if e["type"] == "defense"]  # fmt: skip
    assert kinds == ["fight", "burrow"]


def test_each_defence_step_is_reported_with_its_outcome(host, made):
    session = made[0]
    session.threats.extend([FIGHT, FIGHT])

    def steps():
        return [
            e["data"] for e in host.events()["events"] if e["type"] == "defense_step"
        ]

    assert _until(lambda: len(steps()) == 2)
    assert steps()[0] == {"action": "fight", "ok": True, "kills": 1, "job": None}


def test_the_end_of_a_defence_is_reported_once_after_its_steps(host, made):
    session = made[0]
    session.threats.extend([FIGHT, FIGHT])

    def kinds():
        return [e["type"] for e in host.events()["events"]
                if e["type"].startswith("defense")]  # fmt: skip

    assert _until(lambda: "defense_end" in kinds())
    assert kinds() == ["defense", "defense_step", "defense_step", "defense_end"]
    end = next(e for e in host.events()["events"] if e["type"] == "defense_end")
    assert end["data"] == {"end": "clear", "steps": 2, "job": None}


def test_a_trial_runs_on_the_worker_and_is_never_a_tool(host, made):
    assert host.wait_ready(5)
    session = made[0]
    session.trial_result = {"summoned": [{"type": "minecraft:zombie", "uuid": "u1"}]}
    reply = host.trial({"clear": 8})
    assert reply == {"ok": True, "result": session.trial_result}
    assert session.trials == [({"clear": 8}, "companion-worker")]
    # set-up for operator trials only: neither an intent nor an MCP tool
    assert "trial" not in companion_intents.INTENTS
    assert "trial" not in {t["name"] for t in companion_intents.tool_schemas()}


def test_a_failed_or_malformed_trial_says_why(host, made):
    assert host.wait_ready(5)
    made[0].trial_result = ValueError("trial failed: unknown_item")
    assert host.trial({"give": []}) == {
        "ok": False,
        "error": "trial failed: unknown_item",
    }
    assert host.trial(["clear"]) == {"ok": False, "error": "a trial is a JSON object"}


def test_the_same_action_again_is_announced_once(host, made):
    session = made[0]
    session.threats.extend([FIGHT, FIGHT, FIGHT])
    assert _until(lambda: len(session.defended) == 3)
    assert _until(lambda: not session.threats)
    assert len(session.said) == 1


def test_stop_ends_a_defence_and_the_guard_rests_a_while(host, made):
    session = made[0]
    entered = threading.Event()

    def hold(session, action):
        entered.set()
        while True:  # a long fight: only a stop ends it
            session.poll()
            time.sleep(0.002)

    session.defend_hook = hold
    session.threats.append(FIGHT)
    assert entered.wait(5)
    host.call("stop", {}, OPERATOR)
    assert _until(lambda: host.status()["defense"]["active"] is None)
    ends = [e["data"] for e in host.events()["events"] if e["type"] == "defense_end"]
    assert ends == [{"end": "stopped", "steps": 0, "job": None}]
    resting = host.status()["defense"]["resting"]
    assert 0 < resting <= companion_host.DEFENSE_REST_S
    session.defend_hook = None
    session.threats.append(FIGHT)
    time.sleep(0.1)
    assert session.defended == ["fight"]  # resting after the stop
    assert session.threats == [FIGHT]


def test_a_stop_with_no_defence_under_way_leaves_the_guard_awake(host, made):
    session = made[0]
    assert host.wait_ready(5)
    host.call("stop", {}, OPERATOR)  # stops a job, or nothing: not a fight
    assert host.status()["defense"] == {"active": None, "resting": 0.0}
    session.threats.append(FIGHT)
    assert _until(lambda: session.defended == ["fight"])


def test_status_shows_the_defence_under_way(host, made):
    session = made[0]
    entered, release = threading.Event(), threading.Event()

    def hold(session, action):
        entered.set()
        while not release.is_set():
            session.poll()
            time.sleep(0.002)

    session.defend_hook = hold
    session.threats.append(FIGHT)
    assert entered.wait(5)
    status = host.status()
    assert status["defense"]["active"] == "fighting the zombie"
    assert "fighting the zombie" in status["explanation"].lower()
    release.set()
    assert _until(lambda: host.status()["defense"]["active"] is None)


def test_death_in_a_fight_respawns(host, made):
    session = made[0]

    def die(session, action):
        session.dead = True
        raise BodyDied("observation has no position: the body is dead")

    session.defend_hook = die
    session.threats.append(FIGHT)
    assert _until(lambda: session.respawns == 1)
    ends = [e["data"] for e in host.events()["events"] if e["type"] == "defense_end"]
    assert ends == [{"end": "died", "steps": 0, "job": None}]


def test_the_defense_intent_reads_and_changes_the_settings(host, made):
    reply = host.call("defense", {}, OPERATOR)
    assert reply["ok"] and reply["result"]["defense"]["floor_health"] == 6
    reply = host.call("defense", {"floor_hearts": 5, "threat_radius": 16}, OPERATOR)
    assert reply["ok"], reply
    assert reply["result"]["defense"]["floor_health"] == 10
    assert made[0].defense.threat_radius == 16
    bad = host.call("defense", {"floor_hearts": 50}, OPERATOR)
    assert bad["ok"] is False and "floor_hearts" in bad["error"]


# --- authority and validation -----------------------------------------------------


def test_unauthorized_players_may_only_look(host, monkeypatch):
    monkeypatch.setattr(companion_intents, "_allowlisted", lambda uuid, name="": False)
    stranger = Actor("chat", name="Griefer", uuid="u-9")
    for intent, args in (
        ("goto", {"player": "Griefer"}),
        ("stop", {}),
        ("say", {"text": "hi"}),
        ("remember_place", {"name": "mine"}),
    ):
        reply = host.call(intent, args, stranger)
        assert reply["ok"] is False and reply["error"].startswith("Not allowed")
    assert host.call("status", {}, stranger)["ok"]
    assert host.call("list_places", {}, stranger)["ok"]


def test_bad_requests_get_a_readable_error(host):
    assert "Unknown intent" in host.call("fly", {}, OPERATOR)["error"]
    assert "exactly one target" in host.call("goto", {}, OPERATOR)["error"]
    assert "No job" in host.call("job_status", {"job_id": "j-none"}, OPERATOR)["error"]
    assert host.call("status", {}, "not an actor")["ok"] is False
    reply = host.call("goto", {"x": 1, "y": 64, "z": 1}, OPERATOR, key="")
    assert reply["ok"] is False and "key" in reply["error"]


# --- failures, death and lost sessions ----------------------------------------------


def test_the_worker_survives_a_job_exception(host, made):
    def boom(session, args, actor, progress):
        raise RuntimeError("planner exploded")

    made[0].behaviour["gather"] = boom
    job = submit(host, "gather", item="oak_log", count=4)
    done = host.wait_job(job["id"], 5)
    assert done["state"] == "failed" and "planner exploded" in done["error"]
    assert made[0].halts == 1
    status = host.call("status", {}, OPERATOR)["result"]
    assert "planner exploded" in status["last_failure"]
    made[0].gate.set()
    assert host.wait_job(submit(host)["id"], 5)["state"] == "succeeded"


def test_a_job_failure_keeps_its_message(host, made):
    def fail(session, args, actor, progress):
        raise JobFailed("I can't see Alex")

    made[0].behaviour["goto"] = fail
    job = submit(host, player="Alex")
    assert host.wait_job(job["id"], 5)["error"] == "I can't see Alex"


def test_death_fails_the_job_respawns_and_says_so(host, made):
    def die(session, args, actor, progress):
        raise BodyDied("observation has no position: the body is dead")

    made[0].behaviour["build"] = die
    job = submit(host, "build", template="hut")
    done = host.wait_job(job["id"], 5)
    assert done["state"] == "failed" and done["error"] == "died"
    deadline = time.monotonic() + 5
    while not made[0].said and time.monotonic() < deadline:
        time.sleep(0.005)
    assert made[0].respawns == 1
    assert "died" in made[0].said[0][0] and "9 64 9" in made[0].said[0][0]
    states = [
        e["data"]["state"] for e in host.events()["events"] if e["type"] == "session"
    ]
    assert states[-2:] == ["died", "ready"]
    assert "chat_out" in types(host)


def test_an_exception_on_a_dead_body_is_a_death(host, made):
    def crash(session, args, actor, progress):
        session.health_value = "dead"
        raise RuntimeError("Policy admission closed before terminal result")

    made[0].behaviour["goto"] = crash
    job = submit(host)
    assert host.wait_job(job["id"], 5)["error"] == "died"
    deadline = time.monotonic() + 5
    while made[0].respawns == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert made[0].respawns == 1


def test_death_while_idle_respawns(host, made):
    made[0].dead = True
    deadline = time.monotonic() + 5
    while made[0].respawns == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert made[0].respawns == 1 and not made[0].dead


def test_a_lost_session_is_closed_and_opened_again(host, made):
    def lose(session, args, actor, progress):
        session.health_value = "lost"
        raise ConnectionError("server went away")

    made[0].behaviour["goto"] = lose
    job = submit(host)
    done = host.wait_job(job["id"], 5)
    assert done["state"] == "failed" and "server went away" in done["error"]
    assert host.wait_ready(5)
    assert len(made) == 2 and made[0].closed == {"despawn": False}
    states = [
        e["data"]["state"] for e in host.events()["events"] if e["type"] == "session"
    ]
    assert "lost" in states and states[-1] == "ready"


def test_a_session_that_will_not_start_is_retried(monkeypatch):
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("server not up yet")
        return FakeSession()

    value = Host(factory, bot={"name": "MCBot"}, idle_s=0.01, retry_s=(0.01, 0.02))
    value.start()
    try:
        assert value.wait_ready(5)
        assert len(attempts) == 2
    finally:
        value.shutdown(timeout=5)


def test_queued_jobs_wait_for_a_session(monkeypatch):
    gate = threading.Event()

    def factory():
        gate.wait(5)
        session = FakeSession()
        session.gate.set()
        return session

    value = Host(factory, bot={"name": "MCBot"}, idle_s=0.01)
    value.start()
    try:
        status = value.call("status", {}, OPERATOR)["result"]
        assert status["session"] == "starting"
        assert "Starting" in status["explanation"]
        job = submit(value)
        gate.set()
        assert value.wait_job(job["id"], 5)["state"] == "succeeded"
    finally:
        value.shutdown(timeout=5)


# --- instant intents ------------------------------------------------------------------


def test_places_are_read_and_written_on_the_worker_thread(host, made):
    reply = host.call(
        "remember_place", {"name": "Mine", "x": 5, "y": 12, "z": 5}, OPERATOR
    )
    assert reply["result"]["place"] == {"name": "mine", "position": [5, 12, 5]}
    listed = host.call("list_places", {}, OPERATOR)["result"]["places"]
    assert {"name": "mine", "position": [5, 12, 5]} in listed
    assert set(made[0].place_threads) == {"companion-worker"}


def test_places_are_answered_while_a_job_runs(host, made):
    submit(host)
    assert made[0].blocking.wait(5)
    reply = host.call("list_places", {}, OPERATOR)
    assert reply["ok"] and reply["result"]["places"][0]["name"] == "home"


def test_say_goes_to_the_session_and_the_event_log(host, made):
    reply = host.call("say", {"text": "hello  there", "to": "Steve"}, PLAYER)
    assert reply == {"ok": True, "result": {"said": "hello there", "to": "Steve"}}
    assert made[0].said == [("hello there", "Steve")]
    event = host.events()["events"][-1]
    assert event["type"] == "chat_out" and event["data"] == {
        "text": "hello there",
        "to": "Steve",
    }


def test_status_is_a_snapshot_with_an_explanation(host, made):
    status = host.call("status", {}, OPERATOR)["result"]
    assert status["session"] == "ready" and status["bot"] == "MCBot"
    assert status["position"] == [0.5, 64.0, 0.5] and status["food"] == 20
    assert status["inventory"] == {"minecraft:dirt": 3}
    assert status["players"][0]["name"] == "Steve"
    assert status["job"] is None and status["queue"] == []
    assert status["explanation"].startswith("Idle at 0 64 0")
    status["inventory"]["minecraft:dirt"] = 99  # a copy: the host's view is unchanged
    assert host.status()["inventory"] == {"minecraft:dirt": 3}


def test_status_explains_a_running_and_a_stalled_job(made, monkeypatch):
    clock = Clock()
    value = Host(made_factory(made), bot={"name": "MCBot"}, idle_s=0.01, clock=clock)
    value.start()
    try:
        assert value.wait_ready(5)

        def walk(session, args, actor, progress):
            progress(stage="walking", done=1, total=3)
            return block(session, args, actor, progress)

        made[0].behaviour["goto"] = walk
        submit(value)
        assert made[0].blocking.wait(5)
        assert value.status()["explanation"] == "Running goto: walking 1/3."
        clock.now += companion_host.STALL_SECONDS + 5
        text = value.status()["explanation"]
        assert text.startswith("Stalled on goto (walking 1/3), no progress for 25s")
    finally:
        made[0].gate.set()
        value.shutdown(timeout=5)


def made_factory(made):
    def factory():
        session = FakeSession()
        made.append(session)
        return session

    return factory


def test_status_reads_are_thread_safe(host, made):
    errors = []

    def reader():
        try:
            for _ in range(200):
                status = host.status()
                assert status["session"] == "ready"
        except Exception as error:  # noqa: BLE001 - collected for the assert
            errors.append(error)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


# --- events --------------------------------------------------------------------------


def test_events_long_poll_until_something_happens(host):
    latest = host.events()["latest"]
    found = {}

    def poll():
        found.update(host.events(after=latest, wait=5))

    thread = threading.Thread(target=poll)
    thread.start()
    time.sleep(0.05)
    host.note("chat_in", {"actor": PLAYER.to_dict(), "text": "come here"})
    thread.join(5)
    assert [e["type"] for e in found["events"]] == ["chat_in"]
    assert found["latest"] == latest + 1 and found["events"][0]["seq"] == latest + 1


def test_events_without_news_return_after_the_wait(host):
    latest = host.events()["latest"]
    started = time.monotonic()
    assert host.events(after=latest, wait=0.05)["events"] == []
    assert time.monotonic() - started >= 0.04


def test_note_takes_only_protocol_event_types(host):
    with pytest.raises(ValueError):
        host.note("hack", {})


def test_the_event_ring_is_bounded(made):
    value = Host(made_factory(made), bot={"name": "MCBot"}, event_limit=5)
    for n in range(12):
        value.note("chat_in", {"text": str(n)})
    events = value.events()["events"]
    assert len(events) == 5 and events[-1]["seq"] == 12


def test_wait_job_returns_none_for_unknown_and_running_on_timeout(host, made):
    assert host.wait_job("j-unknown", 0.01) is None
    job = submit(host)
    assert made[0].blocking.wait(5)
    assert host.wait_job(job["id"], 0.02)["state"] == "running"


# --- shutdown -----------------------------------------------------------------------


def test_shutdown_cancels_the_job_and_despawns_the_body(made):
    value = Host(made_factory(made), bot={"name": "MCBot"}, idle_s=0.01)
    value.start()
    assert value.wait_ready(5)
    job = submit(value, "follow", player="Steve")
    assert made[0].blocking.wait(5)
    value.shutdown(timeout=5)
    assert value.wait_job(job["id"], 0)["state"] in TERMINAL_STATES
    assert made[0].closed == {"despawn": True}


def test_cancelled_is_a_base_exception():
    # runners catch Exception; a stop must unwind through them
    assert not issubclass(JobCancelled, Exception)


# --- idle failures, respawn failure, mailbox, explanations ----------------------------


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class FlakyIdle(FakeSession):
    def __init__(self):
        super().__init__()
        self.idle_error = None
        self.idle_failures = 0

    def idle(self):
        if self.idle_error is not None:
            self.idle_failures += 1
            raise self.idle_error
        return super().idle()


def test_an_idle_error_is_classified_by_the_session_health(made):
    sessions = []

    def factory():
        session = FlakyIdle()
        sessions.append(session)
        return session

    value = Host(factory, bot={"name": "MCBot"}, idle_s=0.01, retry_s=(0.01, 0.02))
    value.start()
    try:
        assert value.wait_ready(5)
        first = sessions[0]
        first.idle_error = TimeoutError("slow tick")  # healthy: keep going
        assert until(lambda: first.idle_failures >= 3)
        assert first.respawns == 0 and len(sessions) == 1
        first.health_value = "dead"  # an error on a dead body is a death
        assert until(lambda: first.respawns >= 1)
        first.health_value = "lost"
        assert until(lambda: len(sessions) == 2)
        assert first.closed == {"despawn": False}
        assert value.wait_ready(5)
    finally:
        value.shutdown(timeout=5)


def test_a_failed_respawn_opens_a_new_session(made):
    class NoRespawn(FakeSession):
        def respawn(self):
            raise RuntimeError("spawn_timeout")

    sessions = []

    def factory():
        session = NoRespawn() if not sessions else FakeSession()
        sessions.append(session)
        return session

    value = Host(factory, bot={"name": "MCBot"}, idle_s=0.01, retry_s=(0.01, 0.02))
    value.start()
    try:
        assert value.wait_ready(5)
        sessions[0].dead = True
        assert until(lambda: len(sessions) == 2)
        assert sessions[0].closed == {"despawn": False}
        detail = [
            e["data"]["detail"]
            for e in value.events()["events"]
            if e["type"] == "session"
        ]
        assert any("respawn failed" in (d or "") for d in detail)
    finally:
        value.shutdown(timeout=5)


def test_a_mailbox_request_times_out_while_the_worker_is_stuck(made):
    stuck = threading.Event()

    def factory():
        stuck.wait(5)
        return FakeSession()

    value = Host(factory, bot={"name": "MCBot"}, idle_s=0.01, mailbox_timeout=0.05)
    value.start()
    try:
        reply = value.call("list_places", {}, OPERATOR)
        assert reply == {"ok": False, "error": "The bot is busy; try again in a moment"}
        reply = value.call("say", {"text": "hi"}, OPERATOR)
        assert reply["ok"] is False and "not in the world" in reply["error"]
    finally:
        stuck.set()
        value.shutdown(timeout=5)


def test_places_without_a_session_say_so(made):
    def factory():
        raise OSError("server down")

    value = Host(factory, bot={"name": "MCBot"}, idle_s=0.01, retry_s=(0.01, 0.02))
    value.start()
    try:
        assert until(
            lambda: value.status()["explanation"].startswith(
                "Lost the body (could not start: server down)"
            )
        )
        reply = value.call("list_places", {}, OPERATOR)
        assert reply["ok"] is False and "not in the world" in reply["error"]
    finally:
        value.shutdown(timeout=5)
    assert value.status()["explanation"] == "Not running."


def test_place_errors_reach_the_caller(host, made):
    def broken(name, pos):
        raise ValueError("Place names are 1-32 characters")

    made[0].remember_place = broken
    reply = host.call("remember_place", {"name": "mine"}, OPERATOR)
    assert reply == {"ok": False, "error": "Place names are 1-32 characters"}


def test_an_internal_error_is_reported_not_raised(host, monkeypatch):
    monkeypatch.setattr(host, "status", lambda: 1 / 0)
    reply = host.call("status", {}, OPERATOR)
    assert reply["ok"] is False and reply["error"].startswith("Internal error")


def test_explanations_name_the_queue_the_last_failure_and_a_death(host, made):
    def fail(session, args, actor, progress):
        raise JobFailed("I can't see Alex")

    made[0].behaviour["goto"] = fail
    host.wait_job(submit(host, player="Alex")["id"], 5)
    assert host.status()["explanation"].endswith(
        "Last failure: goto: I can't see Alex."
    )
    made[0].behaviour.pop("goto")
    submit(host)
    assert made[0].blocking.wait(5)
    submit(host, when="next", x=2, y=64, z=2)
    with host._cond:
        job = host._jobs[host._active]
        value = {**host.status(), "job": None}
        assert "; 1 queued." in host._explain(value, None)
        assert (
            host._explain({**value, "session": "died"}, job) == "I died; coming back."
        )


def test_old_finished_jobs_and_their_keys_are_pruned(made):
    value = Host(made_factory(made), bot={"name": "MCBot"}, idle_s=0.01, job_limit=2)
    value.start()
    try:
        assert value.wait_ready(5)
        made[0].gate.set()
        first = submit(value, key="a")
        value.wait_job(first["id"], 5)
        for n in range(3):
            value.wait_job(submit(value, key=f"k{n}")["id"], 5)
        assert value.wait_job(first["id"], 0) is None
        again = submit(value, key="a")
        assert again["id"] != first["id"]
    finally:
        value.shutdown(timeout=5)


def test_progress_events_are_throttled_but_stage_changes_always_announce(made):
    clock = Clock()
    value = Host(made_factory(made), bot={"name": "MCBot"}, idle_s=0.01, clock=clock)
    value.start()
    try:
        assert value.wait_ready(5)

        def chatty(session, args, actor, progress):
            for n in range(5):
                progress(stage="walking", done=n, total=5)
            progress(stage="arrived")
            clock.now += 2
            progress(done=5)
            return {}

        made[0].behaviour["goto"] = chatty
        job = submit(value)
        assert value.wait_job(job["id"], 5)["progress"]["done"] == 5
        stages = [
            e["data"]["progress"]["stage"]
            for e in value.events()["events"]
            if e["type"] == "job_progress"
        ]
        assert stages == ["walking", "arrived", "arrived"]
    finally:
        value.shutdown(timeout=5)


def test_a_progress_record_failure_is_only_logged(host, made):
    def broken(job):
        raise OSError("disk full")

    made[0].record_progress = broken
    made[0].gate.set()
    assert host.wait_job(submit(host)["id"], 5)["state"] == "succeeded"


def test_a_failing_halt_and_close_do_not_stop_the_worker(made):
    class Stubborn(FakeSession):
        def halt(self):
            raise ConnectionError("bridge gone")

        def close(self, despawn=False):
            raise ConnectionError("bridge gone")

    value = Host(lambda: Stubborn(), bot={"name": "MCBot"}, idle_s=0.01)
    value.start()
    assert value.wait_ready(5)
    value.shutdown(timeout=5)
    assert value.status()["session"] == "stopped"


def test_wait_ready_times_out():
    value = Host(lambda: FakeSession(), bot="MCBot")
    assert value.bot == {"name": "MCBot"}
    assert value.wait_ready(0.02) is False


# --- the process ----------------------------------------------------------------------


def test_load_bot_reads_the_profile_and_checks_the_name(tmp_path):
    folder = tmp_path / "bots" / "pal"
    folder.mkdir(parents=True)
    (folder / "bot.json").write_text('{"name": "Pal_1", "roles": {"talker": true}}')
    assert (
        companion_host.load_bot({"companion": {"bot": "pal"}}, tmp_path)["name"]
        == "Pal_1"
    )
    assert companion_host.load_bot({}, tmp_path) == {"name": "MCBot"}
    (folder / "bot.json").write_text('{"name": "no spaces"}')
    with pytest.raises(ValueError, match="3-16"):
        companion_host.load_bot({"companion": {"bot": "pal"}}, tmp_path)


def test_a_renamed_bot_is_loaded_under_its_new_name(tmp_path):
    from mclab.companion_names import BotNames

    own = tmp_path / "bots" / "mcbot3"
    own.mkdir(parents=True)
    (own / "bot.json").write_text('{"name": "Scout"}')
    names = BotNames({"default": "Bolt", "mcbot2": "Rex", "mcbot3": "Dash"})
    assert companion_host.load_bot({}, tmp_path, None, names=names)["name"] == "Bolt"
    second = companion_host.load_bot({}, tmp_path, "mcbot2", names=names)
    assert second == {"name": "Rex", "id": "mcbot2"}
    # the book wins over a profile's own name too: the operator renamed it last
    assert (
        companion_host.load_bot({}, tmp_path, "mcbot3", names=names)["name"] == "Dash"
    )
    assert (
        companion_host.load_bot({}, tmp_path, "mcbot4", names=names)["name"] == "MCBot4"
    )


def test_the_repo_default_bot_is_mcbot():
    assert companion_host.load_bot({"companion": {"bot": "default"}})["name"] == "MCBot"


def test_more_bots_are_derived_from_the_default_profile(tmp_path):
    folder = tmp_path / "bots" / "default"
    folder.mkdir(parents=True)
    (folder / "bot.json").write_text(
        '{"id": "default", "name": "MCBot", "roles": {"talker": "q"},'
        ' "talker": {"history_turns": 3}}'
    )
    second = companion_host.load_bot({}, tmp_path, "mcbot2")
    assert second["id"] == "mcbot2" and second["name"] == "MCBot2"
    assert second["roles"] == {"talker": "q"} and second["talker"]["history_turns"] == 3
    # a profile of its own wins over the derived one
    own = tmp_path / "bots" / "mcbot3"
    own.mkdir()
    (own / "bot.json").write_text('{"name": "Scout"}')
    assert companion_host.load_bot({}, tmp_path, "mcbot3") == {
        "name": "Scout",
        "id": "mcbot3",
    }
    # the first bot is the default one unless the config says otherwise
    assert companion_host.load_bot({}, tmp_path, "default")["id"] == "default"
    # the derived profile is a copy: changing it leaves the default alone
    second["talker"]["history_turns"] = 9
    assert (
        companion_host.load_bot({}, tmp_path, "mcbot4")["talker"]["history_turns"] == 3
    )


def test_a_bot_that_is_neither_a_profile_nor_derived_is_refused(tmp_path):
    with pytest.raises(ValueError, match="No bot profile bots/pal"):
        companion_host.load_bot({}, tmp_path, "pal")


def test_derived_bots_follow_the_configured_profile(tmp_path):
    folder = tmp_path / "bots" / "pal"
    folder.mkdir(parents=True)
    (folder / "bot.json").write_text('{"name": "Pal_1", "roles": {"talker": "q"}}')
    settings = {"companion": {"bot": "pal"}}
    assert companion_host.load_bot(settings, tmp_path)["name"] == "Pal_1"
    assert companion_host.load_bot(settings, tmp_path, "default")["name"] == "Pal_1"
    second = companion_host.load_bot(settings, tmp_path, "mcbot2")
    assert second["name"] == "MCBot2" and second["roles"] == {"talker": "q"}


def test_the_host_knows_its_own_jobs_and_names_its_bot_id(made):
    value = Host(
        made_factory(made), bot={"id": "mcbot2", "name": "MCBot2"}, idle_s=0.01
    )
    value.start()
    try:
        assert value.wait_ready(5)
        job = submit(value)
        assert value.knows(job["id"]) and not value.knows("j-nope")
        assert value.status()["bot_id"] == "mcbot2"
    finally:
        made[0].gate.set()
        value.shutdown(timeout=5)


def test_session_factory_makes_sessions_sharing_one_gather_state(tmp_path):
    make = companion_host.session_factory(
        {"name": "MCBot"}, results=tmp_path / "results", runtime=tmp_path / "rt"
    )
    first, second = make(), make()
    assert first.bot_name == "MCBot"
    assert first.gather_state is second.gather_state
    assert first.state_file == tmp_path / "rt" / "companion-state.json"
    assert first.folder.parent == tmp_path / "results"


def test_the_first_bot_keeps_the_legacy_files_and_the_others_get_their_own(tmp_path):
    results, runtime = tmp_path / "results", tmp_path / "rt"
    first = companion_host.session_factory(
        {"name": "MCBot", "id": "default"},
        results=results,
        runtime=runtime,
        slot="default",
    )()
    second = companion_host.session_factory(
        {"name": "MCBot2", "id": "mcbot2"},
        results=results,
        runtime=runtime,
        slot="mcbot2",
    )()
    assert first.state_file == runtime / "companion-state.json"
    assert first.defense_file == runtime / "companion-defense.json"
    assert first.memory_root is None and first.folder.parent == results
    assert second.bot_name == "MCBot2"
    assert second.state_file == runtime / "bots" / "mcbot2" / "companion-state.json"
    assert second.defense_file == runtime / "bots" / "mcbot2" / "companion-defense.json"
    assert second.memory_root == runtime / "bots" / "mcbot2" / "world-memory"
    assert second.folder.parent == results / "mcbot2"
    assert second.gather_state is not first.gather_state


def test_the_world_spawn_is_the_level_spawn_unless_the_instance_names_the_lobby():
    from mclab.arena import world
    from mclab.companion_session import level_spawn

    assert companion_host.world_spawn_for({}) is level_spawn
    assert companion_host.world_spawn_for({"companion": {"bot": "x"}}) is level_spawn
    lobby = companion_host.world_spawn_for({"companion": {"spawn": "lobby"}})
    assert lobby is world.lobby_world_spawn
    with pytest.raises(ValueError, match="spawn"):
        companion_host.world_spawn_for({"companion": {"spawn": "moon"}})


def test_session_factory_passes_the_world_spawn_to_every_session(tmp_path):
    def spawn():
        return (0, 102, 0)

    make = companion_host.session_factory(
        {"name": "MCBot"},
        results=tmp_path / "results",
        runtime=tmp_path / "rt",
        world_spawn=spawn,
    )
    assert make()._world_spawn is spawn
    default = companion_host.session_factory(
        {"name": "MCBot"}, results=tmp_path / "results", runtime=tmp_path / "rt"
    )
    from mclab.companion_session import level_spawn

    assert default()._world_spawn is level_spawn


def test_session_factory_applies_the_bots_defense_settings(tmp_path):
    make = companion_host.session_factory(
        {"name": "MCBot", "defense": {"floor_health": 8}},
        results=tmp_path / "results",
        runtime=tmp_path / "rt",
    )
    session = make()
    assert session.defense_policy.floor_health == 8
    assert session.defense_file == tmp_path / "rt" / "companion-defense.json"
    # a typo in bot.json stops the host at start, not at the first zombie
    with pytest.raises(ValueError, match="unknown defense"):
        companion_host.session_factory({"name": "MCBot", "defense": {"hearts": 3}})


class Adapter:
    def __init__(self, log, name):
        self.log, self.name, self.port = log, name, 8777

    def stop(self):
        self.log.append(("stop", self.name))

    shutdown = stop

    def start(self):
        self.log.append(("start", self.name))


def _fake_adapters(monkeypatch, log, *, fail=()):
    import sys
    import types as pytypes

    from mclab import paths

    def module(name, **attrs):
        value = pytypes.ModuleType(name)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, f"mclab.{name}", value)
        import mclab

        monkeypatch.setattr(mclab, name, value, raising=False)

    def serve(host, *, port, token, on_shutdown=None):
        if "api" in fail:
            raise OSError("port in use")
        log.append(("serve", port, token))
        log.append(("on_shutdown", on_shutdown))
        return Adapter(log, "api")

    def start(host, *, bot, instance, **kwargs):
        if "talker" in fail:
            raise RuntimeError("no model")
        log.append(("talker", bot["name"], instance, sorted(kwargs)))
        return Adapter(log, "talker")

    def ingress(host, talker, source, *, players, stop_all):
        log.append(("ingress", host is talker, source, players, stop_all))
        return Adapter(log, "ingress")

    module("companion_api", serve=serve)
    module("companion_talker", start=start)
    module(
        "companion_ingress",
        Ingress=ingress,
        bridge_events_source=lambda: "events-source",
        bridge_players_source=lambda: "players-source",
    )
    monkeypatch.setattr(
        paths, "INSTANCE", pytypes.SimpleNamespace(name="play"), raising=False
    )


def test_adapters_are_wired_and_the_handshake_written(tmp_path, monkeypatch):
    import json

    log = []
    _fake_adapters(monkeypatch, log)

    def hook():
        pass

    stops = companion_host._start_adapters(
        "fleet", {"companion_port": 8777}, "tok", tmp_path, on_shutdown=hook
    )
    assert log == [
        ("serve", 8777, "tok"),
        ("on_shutdown", hook),
        # one ingress for the fleet: the fleet is its host and its talker, and a
        # stop names every bot
        ("ingress", True, "events-source", "players-source", True),
        ("start", "ingress"),
    ]
    handshake = json.loads((tmp_path / "companion-host.json").read_text())
    assert handshake["port"] == 8777 and handshake["token"] == "tok"
    assert handshake["pid"] > 0
    assert len(stops) == 2


def test_a_failing_adapter_does_not_stop_the_others(tmp_path, monkeypatch):
    log = []
    _fake_adapters(monkeypatch, log, fail=("api",))
    stops = companion_host._start_adapters(
        "fleet", {"companion_port": 8777}, "tok", tmp_path
    )
    assert log == [
        ("ingress", True, "events-source", "players-source", True),
        ("start", "ingress"),
    ]
    assert not (tmp_path / "companion-host.json").exists()
    assert len(stops) == 1


def test_a_member_is_a_host_and_a_talker_under_the_bots_own_name(tmp_path, monkeypatch):
    log = []
    _fake_adapters(monkeypatch, log)
    started = []

    class FakeHost:
        def __init__(self, factory, *, bot):
            self.bot = bot
            started.append(("host", bot["id"], bot["name"]))

        def start(self):
            started.append(("start",))

    monkeypatch.setattr(companion_host, "Host", FakeHost)
    monkeypatch.setattr(
        companion_host,
        "load_bot",
        lambda settings, root, fleet_id, names: {
            "name": names.renamed.get(fleet_id) or f"Bot_{fleet_id}"
        },
    )
    # The name book beside the instance's files is read for every bot built.
    from mclab.companion_names import BotNames

    BotNames({"mcbot2": "Rex"}).save(tmp_path)
    monkeypatch.setattr(
        companion_host,
        "session_factory",
        lambda bot, **kwargs: started.append(("session", kwargs["slot"])),
    )
    make = companion_host.member_factory({}, tmp_path, None)
    first, second = make("default"), make("mcbot2")
    assert (first.id, first.bot["id"], second.bot["id"]) == (
        "default",
        "default",
        "mcbot2",
    )
    assert [e for e in started if e[0] == "session"] == [
        ("session", "default"),
        ("session", "mcbot2"),
    ]
    # the first bot logs where it always did; the others under their own folder
    talkers = [e for e in log if e[0] == "talker"]
    assert talkers == [
        ("talker", "Bot_default", "play", []),
        ("talker", "Rex", "play", ["results_dir"]),
    ]
    assert len(first.stop) == 1 and first.talker is not None


def test_a_talker_that_will_not_start_leaves_the_bot_playing(tmp_path, monkeypatch):
    log = []
    _fake_adapters(monkeypatch, log, fail=("talker",))

    class FakeHost:
        def __init__(self, factory, *, bot):
            pass

        def start(self):
            log.append("host start")

    monkeypatch.setattr(companion_host, "Host", FakeHost)
    monkeypatch.setattr(
        companion_host,
        "load_bot",
        lambda settings, root, fleet_id, names: {"name": "MCBot"},
    )
    monkeypatch.setattr(companion_host, "session_factory", lambda bot, **kw: None)
    member = companion_host.member_factory({}, tmp_path, None)("default")
    assert log == ["host start"]
    assert member.talker is None and member.stop == []


def test_main_runs_until_a_signal_then_cleans_up(tmp_path, monkeypatch):
    from mclab import companion_fleet, companion_session, paths

    log, handlers = [], {}
    made = []

    class FakeFleet:
        def __init__(self, factory):
            made.append((self, factory))

        def add(self):
            log.append("fleet add")

        def shutdown(self):
            log.append("fleet shutdown")

    def fake_adapters(fleet, settings, token, runtime, *, on_shutdown):
        (runtime / "companion-host.json").write_text("{}")
        assert "SIGTERM" in handlers and fleet is made[0][0]
        on_shutdown()  # the supervisor asks over the control API
        return [lambda: log.append("adapter stop"), lambda: 1 / 0]

    monkeypatch.setattr(companion_fleet, "Fleet", FakeFleet)
    monkeypatch.setattr(companion_host, "_start_adapters", fake_adapters)
    monkeypatch.setattr(companion_host, "member_factory", lambda *args: "factory")
    monkeypatch.setattr(paths, "config", lambda: {"companion_port": 8777})
    monkeypatch.setattr(companion_session, "instance_runtime", lambda: tmp_path)
    monkeypatch.setattr(
        companion_host.signal,
        "signal",
        lambda number, handler: handlers.setdefault(
            {getattr(companion_host.signal, "SIGTERM", None): "SIGTERM"}.get(
                number, number
            ),
            handler,
        ),
    )
    assert companion_host.main([]) == 0
    assert log == ["fleet add", "adapter stop", "fleet shutdown"]
    assert not (tmp_path / "companion-host.json").exists()
    assert made[0][1] == "factory"


# --- attack: who may hit whom -------------------------------------------------------


@pytest.fixture
def listed(monkeypatch):
    """Whom the config names, for the stricter player-attack rule."""
    names = set()
    monkeypatch.setattr(
        companion_intents,
        "_listed",
        lambda uuid, name="": bool({uuid, name.lower()} & names),
    )
    return names


def attack(host, actor, **args):
    return host.call("attack", args, actor)


def test_an_attack_on_a_player_needs_the_operator_or_a_listed_name(host, listed):
    stranger = Actor("chat", name="Alex", uuid="u-9")
    refused = attack(host, stranger, player="Bob")
    assert not refused["ok"] and "player" in refused["error"]
    assert host.status()["job"] is None
    assert attack(host, PLAYER, player="Bob")["ok"] is False  # Steve is not listed
    listed.add("steve")
    assert attack(host, PLAYER, player="Bob")["ok"]


def test_the_operator_may_attack_a_player_without_a_config_entry(host, listed):
    assert attack(host, OPERATOR, player="Bob")["ok"]


def test_mobs_and_the_nearest_hostile_need_only_the_ordinary_authority(host, listed):
    assert attack(host, PLAYER, mob="zombie")["ok"]
    assert attack(host, PLAYER, nearest_hostile=True)["ok"]


def test_the_bot_may_not_be_ordered_to_attack_itself(host, listed):
    for name in ("MCBot", "mcbot"):
        reply = attack(host, OPERATOR, player=name)
        assert not reply["ok"] and "itself" in reply["error"]
    assert host.status()["job"] is None


def test_the_owner_may_attack_themselves_only_by_asking_for_it(host, listed):
    # decision: the target is whoever is named; the owner is not special-cased
    listed.add("steve")
    assert attack(host, PLAYER, player="Steve")["ok"]


def test_a_running_job_knows_its_id_so_a_replan_keeps_its_stats(host, made):
    seen = []

    def note(session, args, actor, progress):
        seen.append(session.current_job)
        return {}

    made[0].behaviour["goto"] = note
    job = submit(host)
    host.wait_job(job["id"], 5)
    assert seen == [job["id"]]
