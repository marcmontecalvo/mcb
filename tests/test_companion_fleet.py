"""The fleet: several companions on one server, each with its own host and session."""

import threading
import time

import pytest
from test_companion_host import FakeSession, block, wait_state

from mclab import companion_intents
from mclab.companion_fleet import Fleet, Member, bot_id_for, bot_name_for
from mclab.companion_host import Host, HostError
from mclab.companion_intents import Actor
from mclab.progress_watch import BodyDied

OPERATOR = Actor("mcp")
PLAYER = Actor("chat", name="Steve", uuid="u-1")
GOTO = {"x": 1, "y": 64, "z": 1}


class Heard:
    def __init__(self):
        self.lines = []

    def hear(self, actor, text, *, engaged=True):
        self.lines.append((actor, text, engaged))


def finish(session, args, actor, progress):
    return {"arrived": True}


@pytest.fixture
def sessions():
    """Every FakeSession made, by the bot it belongs to."""
    return {}


@pytest.fixture
def fleet(sessions, monkeypatch):
    monkeypatch.setattr(companion_intents, "_allowlisted", lambda uuid, name="": True)
    closed = []

    def factory(bot_id):
        bot = {"id": bot_id, "name": bot_name_for(bot_id)}

        def session():
            made = FakeSession()
            made.behaviour["goto"] = finish
            sessions.setdefault(bot_id, []).append(made)
            return made

        host = Host(session, bot=bot, idle_s=0.01, retry_s=(0.01, 0.02))
        host.start()
        assert host.wait_ready(5)
        return Member(
            bot_id, bot, host, talker=Heard(), stop=[lambda: closed.append(bot_id)]
        )

    value = Fleet(factory, limit=3)
    value.closed = closed
    yield value
    for made in sessions.values():
        for session in made:
            session.gate.set()
    value.shutdown()


def job_of(reply):
    assert reply["ok"], reply
    return reply["result"]["job"]


def until(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "condition never held"
        time.sleep(0.005)


def finished(fleet, bot_id, job_id, state="succeeded"):
    return wait_state(fleet.member(bot_id).host, job_id, state)


def hold(sessions, *bot_ids):
    """Make these bots' goto a long job."""
    for bot_id in bot_ids:
        sessions[bot_id][0].behaviour["goto"] = block


# --- names ----------------------------------------------------------------------------


def test_bot_ids_and_names_follow_one_pattern():
    assert [bot_id_for(n) for n in (1, 2, 8)] == ["default", "mcbot2", "mcbot8"]
    assert [bot_name_for(i) for i in ("default", "mcbot2", "mcbot12")] == [
        "MCBot",
        "MCBot2",
        "MCBot12",
    ]
    with pytest.raises(ValueError, match="bot id"):
        bot_name_for("pal")


# --- adding and removing --------------------------------------------------------------


def test_the_first_bot_added_is_the_default_one(fleet):
    assert fleet.ids() == []
    added = fleet.add()
    assert added["id"] == "default" and added["name"] == "MCBot"
    assert fleet.add()["id"] == "mcbot2"
    assert fleet.ids() == ["default", "mcbot2"]


def test_adding_a_bot_that_is_already_here_changes_nothing(fleet):
    fleet.add("mcbot2")
    again = fleet.add("mcbot2")
    assert again["id"] == "mcbot2" and fleet.ids() == ["mcbot2"]


def test_the_next_free_name_fills_a_gap_a_departed_bot_left(fleet):
    fleet.add()
    fleet.add()
    fleet.add()
    fleet.remove("mcbot2")
    assert fleet.add()["id"] == "mcbot2"


def test_a_fleet_holds_at_most_its_limit(fleet):
    for _ in range(3):
        fleet.add()
    with pytest.raises(HostError, match="at most 3"):
        fleet.add()
    assert len(fleet.ids()) == 3


def test_a_removed_bot_is_stopped_and_gone(fleet):
    fleet.add()
    fleet.add()
    removed = fleet.remove("mcbot2")
    assert removed["id"] == "mcbot2"
    assert fleet.closed == ["mcbot2"] and fleet.ids() == ["default"]
    with pytest.raises(HostError, match="No bot mcbot2"):
        fleet.remove("mcbot2")


def test_a_factory_that_refuses_a_bot_leaves_the_fleet_as_it_was():
    calls = []

    def factory(bot_id):
        calls.append(bot_id)
        raise ValueError(f"No bot {bot_id}")

    fleet = Fleet(factory, limit=3)
    with pytest.raises(ValueError, match="No bot pal"):
        fleet.add("pal")
    with pytest.raises(ValueError, match="No bot pal"):
        fleet.add("pal")  # the failed id was released, so it is tried again
    assert fleet.ids() == [] and calls == ["pal", "pal"]


def test_the_default_bot_is_the_first_one_present(fleet):
    fleet.add("mcbot3")
    fleet.add("mcbot2")
    assert fleet.default_id() == "mcbot3"
    fleet.remove("mcbot3")
    assert fleet.default_id() == "mcbot2"


def test_a_fleet_that_is_shut_down_adds_no_more(fleet):
    fleet.add()
    fleet.shutdown()
    assert fleet.closed == ["default"]
    with pytest.raises(HostError, match="stopping"):
        fleet.add()
    fleet.shutdown()  # twice is harmless
    assert fleet.closed == ["default"]


def test_two_adds_at_once_make_two_different_bots(monkeypatch):
    monkeypatch.setattr(companion_intents, "_allowlisted", lambda uuid, name="": True)
    entered = threading.Barrier(2, timeout=5)

    def factory(bot_id):
        entered.wait()  # both factories run at the same time
        bot = {"id": bot_id, "name": bot_name_for(bot_id)}
        host = Host(FakeSession, bot=bot, idle_s=0.01)
        return Member(bot_id, bot, host, talker=None, stop=[])

    fleet = Fleet(factory, limit=4)
    ids = []
    threads = [
        threading.Thread(target=lambda: ids.append(fleet.add()["id"])) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(ids) == ["default", "mcbot2"]


# --- routing --------------------------------------------------------------------------


def test_a_call_without_a_bot_reaches_the_first_bot(fleet, sessions):
    fleet.add()
    fleet.add()
    job = job_of(fleet.call("goto", GOTO, OPERATOR))
    finished(fleet, "default", job["id"])
    assert [r[0] for r in sessions["default"][0].runs] == ["goto"]
    assert sessions["mcbot2"][0].runs == []


def test_a_call_names_the_bot_it_is_for(fleet, sessions):
    fleet.add()
    fleet.add()
    job = job_of(fleet.call("goto", {**GOTO, "x": 5}, OPERATOR, bot="mcbot2"))
    finished(fleet, "mcbot2", job["id"])
    assert sessions["default"][0].runs == []
    assert sessions["mcbot2"][0].runs[0][1] == job["args"]


def test_a_call_for_a_bot_that_is_not_here_says_so(fleet):
    fleet.add()
    reply = fleet.call("status", {}, OPERATOR, bot="mcbot9")
    assert reply == {"ok": False, "error": "No bot mcbot9; here: default"}
    empty = Fleet(lambda bot_id: None, limit=1)
    assert empty.call("status", {}, OPERATOR)["error"].startswith("No bot is running")


def test_each_bot_has_its_own_jobs_events_and_status(fleet):
    fleet.add()
    fleet.add()
    first = job_of(fleet.call("goto", GOTO, OPERATOR))
    finished(fleet, "default", first["id"])
    assert fleet.status("mcbot2")["job"] is None
    assert fleet.status("mcbot2")["bot"] == "MCBot2"
    assert fleet.status("mcbot2")["bot_id"] == "mcbot2"
    assert fleet.status()["bot"] == "MCBot"
    mine = [e["type"] for e in fleet.events(0, bot="default")["events"]]
    theirs = [e["type"] for e in fleet.events(0, bot="mcbot2")["events"]]
    assert "job_finished" in mine and "job_finished" not in theirs


def test_a_job_is_found_whichever_bot_runs_it(fleet):
    fleet.add()
    fleet.add()
    job = job_of(fleet.call("goto", {**GOTO, "x": 5}, OPERATOR, bot="mcbot2"))
    found = fleet.wait_job(job["id"], 5.0)
    assert found["id"] == job["id"]
    assert fleet.wait_job(job["id"], 0.0, bot="default") is None
    assert fleet.wait_job("j-nope", 0.0) is None


def test_stop_all_stops_every_bot(fleet, sessions):
    fleet.add()
    fleet.add()
    hold(sessions, "default", "mcbot2")
    jobs = {
        bot_id: job_of(fleet.call("goto", GOTO, OPERATOR, bot=bot_id))
        for bot_id in ("default", "mcbot2")
    }
    for bot_id in jobs:
        until(lambda b=bot_id: sessions[b][0].blocking.is_set())
    reply = fleet.call("stop", {}, OPERATOR, bot="all")
    assert reply["ok"]
    assert sorted(reply["result"]["stopped"]) == sorted(j["id"] for j in jobs.values())
    assert set(reply["result"]["bots"]) == {"default", "mcbot2"}
    for bot_id, job in jobs.items():
        finished(fleet, bot_id, job["id"], "cancelled")


def test_only_stop_may_name_every_bot(fleet):
    fleet.add()
    reply = fleet.call("goto", GOTO, OPERATOR, bot="all")
    assert not reply["ok"] and "stop" in reply["error"]


def test_a_stop_for_one_bot_leaves_the_other_running(fleet, sessions):
    fleet.add()
    fleet.add()
    hold(sessions, "default", "mcbot2")
    running = {
        bot_id: job_of(fleet.call("goto", GOTO, OPERATOR, bot=bot_id))
        for bot_id in ("default", "mcbot2")
    }
    for bot_id in running:
        until(lambda b=bot_id: sessions[b][0].blocking.is_set())
    assert fleet.call("stop", {}, OPERATOR, bot="mcbot2")["ok"]
    finished(fleet, "mcbot2", running["mcbot2"]["id"], "cancelled")
    assert fleet.status("default")["job"]["id"] == running["default"]["id"]
    assert fleet.status("default")["job"]["state"] == "running"


def test_the_trial_is_set_for_the_bot_named(fleet, sessions):
    fleet.add()
    fleet.add()
    assert fleet.trial({"heal": True}, bot="mcbot2")["ok"]
    assert [t[0] for t in sessions["mcbot2"][0].trials] == [{"heal": True}]
    assert sessions["default"][0].trials == []


# --- what the bots are, listed -------------------------------------------------------


def test_the_bots_are_listed_with_what_each_is_doing(fleet, sessions):
    fleet.add()
    fleet.add()
    hold(sessions, "mcbot2")
    job_of(fleet.call("goto", GOTO, OPERATOR, bot="mcbot2"))
    until(lambda: sessions["mcbot2"][0].blocking.is_set())
    rows = {row["id"]: row for row in fleet.bots()}
    assert rows["default"] == {
        "id": "default",
        "name": "MCBot",
        "session": "ready",
        "job": None,
        "default": True,
    }
    assert rows["mcbot2"]["job"] == "goto" and rows["mcbot2"]["default"] is False
    assert rows["mcbot2"]["name"] == "MCBot2"


# --- chat ----------------------------------------------------------------------------


def lines(fleet, bot_id):
    return fleet.member(bot_id).talker.lines


def test_every_bot_hears_the_chat_and_the_first_answers_what_names_nobody(fleet):
    fleet.add()
    fleet.add()
    fleet.hear(PLAYER, "come here")
    assert lines(fleet, "default") == [(PLAYER, "come here", True)]
    assert lines(fleet, "mcbot2") == [(PLAYER, "come here", False)]


def test_a_line_that_names_a_bot_is_for_that_bot(fleet):
    fleet.add()
    fleet.add()
    fleet.hear(PLAYER, "hello MCBot2")
    assert lines(fleet, "default")[0][2] is False  # MCBot is not MCBot2
    assert lines(fleet, "mcbot2")[0][2] is True


def test_a_line_naming_both_is_for_both(fleet):
    fleet.add()
    fleet.add()
    fleet.hear(PLAYER, "@MCBot and mcbot2, line up")
    assert [lines(fleet, b)[0][2] for b in ("default", "mcbot2")] == [True, True]


def test_a_bot_with_no_talker_does_not_stop_the_others_hearing(fleet):
    fleet.add()
    fleet.add()
    fleet.member("default").talker = None
    fleet.hear(PLAYER, "hi mcbot2")
    assert lines(fleet, "mcbot2") == [(PLAYER, "hi mcbot2", True)]


def test_a_talker_that_fails_does_not_stop_the_others_hearing(fleet):
    fleet.add()
    fleet.add()

    class Broken:
        def hear(self, actor, text, *, engaged=True):
            raise RuntimeError("down")

    fleet.member("default").talker = Broken()
    fleet.hear(PLAYER, "hi")
    assert lines(fleet, "mcbot2") == [(PLAYER, "hi", False)]


# --- isolation under a shared server -------------------------------------------------


def test_one_bot_dying_does_not_touch_the_other(fleet, sessions):
    fleet.add()
    fleet.add()
    sessions["default"][0].dead = True
    until(lambda: sessions["default"][0].respawns >= 1)
    assert fleet.status("mcbot2")["session"] == "ready"
    assert sessions["mcbot2"][0].respawns == 0


def test_two_bots_that_die_and_respawn_together_each_respawn_once(fleet, sessions):
    fleet.add()
    fleet.add()
    for bot_id in ("default", "mcbot2"):
        sessions[bot_id][0].dead = True
    for bot_id in ("default", "mcbot2"):
        until(lambda b=bot_id: sessions[b][0].respawns >= 1)
    for bot_id in ("default", "mcbot2"):
        assert sessions[bot_id][0].respawns == 1
        assert len(sessions[bot_id]) == 1  # the same session, brought back
    assert fleet.status("default")["session"] == "ready"
    assert fleet.status("mcbot2")["session"] == "ready"


def test_a_job_that_kills_one_bot_fails_only_that_bots_job(fleet, sessions):
    def die(session, args, actor, progress):
        raise BodyDied("observation has no position: the body is dead")

    fleet.add()
    fleet.add()
    sessions["default"][0].behaviour["goto"] = die
    hold(sessions, "mcbot2")
    dying = job_of(fleet.call("goto", GOTO, OPERATOR, bot="default"))
    staying = job_of(fleet.call("goto", GOTO, OPERATOR, bot="mcbot2"))
    finished(fleet, "default", dying["id"], "failed")
    until(lambda: sessions["mcbot2"][0].blocking.is_set())
    assert fleet.status("mcbot2")["job"]["id"] == staying["id"]
    assert fleet.status("mcbot2")["job"]["state"] == "running"
    assert sessions["mcbot2"][0].respawns == 0


def test_sessions_hold_their_own_bot_identity(fleet, sessions):
    fleet.add()
    fleet.add()
    assert fleet.member("default").host.bot["name"] == "MCBot"
    assert fleet.member("mcbot2").host.bot["name"] == "MCBot2"
    assert sessions["default"][0] is not sessions["mcbot2"][0]
