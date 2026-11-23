import json
import threading

import pytest

from mclab import companion_intents, companion_talker
from mclab.companion_intents import Actor
from mclab.llm_chat import ChatError, Reply

BOT = {
    "schema_version": 1,
    "id": "default",
    "name": "MCBot",
    "roles": {"talker": "fake"},
    "talker": {"deadline_s": 5, "max_tool_rounds": 4, "history_turns": 6},
}
OWNER = Actor("chat", "Owner", "u-owner", (1, 64, 2))
KID = Actor("chat", "Kid", "u-kid")
ASK = Actor("command", "Owner", "u-owner")


@pytest.fixture(autouse=True)
def allowlist(monkeypatch):
    monkeypatch.setattr(
        companion_intents, "_allowlisted", lambda uuid, name="": name == "Owner"
    )


class FakeHost:
    def __init__(self):
        self.calls = []
        self.notes = []
        self.results = {"status": {"ok": True, "result": {"explanation": "idle"}}}
        self.lock = threading.Lock()

    def call(self, intent, args, actor, *, key=None):
        with self.lock:
            self.calls.append((intent, args, actor))
        if intent in self.results:
            result = self.results[intent]
            if isinstance(result, Exception):
                raise result
            return result
        return {"ok": True, "result": {"done": intent}}

    def note(self, type, data):
        self.notes.append((type, data))

    def said(self):
        return [a["text"] for i, a, _ in self.calls if i == "say"]

    def intents(self):
        return [i for i, _, _ in self.calls]


class FakeBackend:
    model = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def complete(self, messages, tools=None):
        self.requests.append(([dict(m) for m in messages], tools))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def text(value):
    return Reply(text=value, model="fake-model", latency_ms=5)


def tools(*calls):
    return Reply(
        text="",
        model="fake-model",
        latency_ms=5,
        tool_calls=[
            {"id": f"c{i}", "name": n, "arguments": a, "error": e}
            for i, (n, a, e) in enumerate(calls)
        ],
    )


@pytest.fixture
def make(tmp_path):
    made = []

    def build(script, host=None, bot=BOT, **kwargs):
        host = host or FakeHost()
        backend = FakeBackend(script)
        talker = companion_talker.start(
            host,
            bot=bot,
            instance=None,
            backend=backend,
            results_dir=tmp_path,
            prompt="PROMPT",
            soul="SOUL",
            **kwargs,
        )
        made.append(talker)
        return talker, host, backend

    yield build
    for talker in made:
        talker.stop()


def log_lines(tmp_path):
    files = list((tmp_path / "companion-talker").glob("*.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text().splitlines()]


# --- addressing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["mcbot come here", "hey MCBOT!", "@mcbot hi", "can you help, MCBot?", "mcbot"],
)
def test_addressed_lines_get_a_model_turn(make, line):
    talker, host, backend = make([text("hi")])
    talker.hear(OWNER, line)
    assert talker.wait_idle(5)
    assert len(backend.requests) == 1 and host.said() == ["hi"]


@pytest.mark.parametrize("line", ["nice house", "mcbots are cool", "xmcbot"])
def test_unaddressed_lines_are_recorded_not_answered(make, line):
    talker, host, backend = make([])
    talker.hear(OWNER, line)
    assert talker.wait_idle(5)
    assert backend.requests == [] and host.calls == []
    (note,) = host.notes
    assert note[0] == "chat_in" and note[1]["text"] == line
    assert note[1]["actor"]["name"] == "Owner"


def test_mcb_ask_is_always_addressed(make):
    talker, host, backend = make([text("sure")])
    talker.hear(ASK, "what are you doing")
    assert talker.wait_idle(5)
    assert host.said() == ["sure"]


def test_every_heard_line_is_noted_even_when_answered(make):
    talker, host, _ = make([text("a")])
    talker.hear(OWNER, "mcbot hi")
    assert talker.wait_idle(5)
    assert [n[0] for n in host.notes] == ["chat_in"]


# --- stop in code -------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["stop", "Stop!", "MCBot, stop", "mcbot stop it", "@mcbot halt", "wait", "freeze"]
    + ["cancel", "enough", "stop stop", "please stop", "stop mcbot."],
)
def test_authorized_stop_never_reaches_the_model(make, line):
    talker, host, backend = make([])
    talker.hear(OWNER, line)
    assert host.intents() == ["stop", "say"]  # synchronous: no wait_idle needed
    assert host.calls[0][2] == OWNER
    assert host.calls[1][2].source == "api"
    assert backend.requests == []
    assert host.said() == [companion_talker.STOP_REPLY]


def test_stop_from_ask_command(make):
    talker, host, backend = make([])
    talker.hear(ASK, "stop")
    assert host.intents() == ["stop", "say"] and backend.requests == []


def test_unauthorized_stop_is_refused_politely(make):
    talker, host, backend = make([])
    talker.hear(KID, "mcbot stop")
    assert host.intents() == ["say"]
    assert host.said() == [companion_talker.REFUSE_REPLY]
    assert backend.requests == []


def test_unauthorized_unaddressed_stop_does_nothing(make):
    talker, host, _ = make([])
    talker.hear(KID, "stop")
    assert host.calls == []


def test_stop_only_phrases_must_be_only_stop_words(make):
    talker, host, backend = make([text("ok"), text("ok")])
    talker.hear(OWNER, "mcbot stop digging that hole")
    talker.hear(OWNER, "mcbot please")  # filler only: not a stop
    assert talker.wait_idle(5)
    assert "stop" not in host.intents()
    assert len(backend.requests) == 2


def test_failing_stop_still_replies(make):
    host = FakeHost()
    host.results["stop"] = RuntimeError("boom")
    talker, host, _ = make([], host=host)
    talker.hear(OWNER, "stop")
    assert host.said() == [companion_talker.STOP_REPLY]


# --- the model turn -----------------------------------------------------------


def test_prompt_status_and_speaker_in_the_system_message(make):
    talker, host, backend = make([text("hi")])
    talker.hear(OWNER, "mcbot hi")
    assert talker.wait_idle(5)
    messages, offered = backend.requests[0]
    system = messages[0]["content"]
    assert system.startswith("PROMPT") and "SOUL" in system
    assert "idle" in system  # the status explanation
    assert "Owner" in system and "may command" in system
    assert messages[-1] == {"role": "user", "content": "mcbot hi"}
    assert {t["function"]["name"] for t in offered} >= {"gather", "stop"}
    assert host.calls[0][0] == "status"


def test_untrusted_speaker_gets_the_untrusted_tool_list(make):
    talker, host, backend = make([text("hello")])
    talker.hear(KID, "mcbot hello")
    assert talker.wait_idle(5)
    messages, offered = backend.requests[0]
    assert [t["function"]["name"] for t in offered] == ["status", "list_places"]
    assert "may not command" in messages[0]["content"]
    assert host.said() == ["hello"]  # still answered, by the operator-level actor


def test_tool_loop_executes_with_the_speaker_and_feeds_back_json(make):
    args = {"item": "oak_log", "count": 16}
    talker, host, backend = make(
        [tools(("gather", args, None)), text("On it: job j-1. Say stop any time.")]
    )
    host.results["gather"] = {"ok": True, "result": {"job": {"id": "j-1"}}}
    talker.hear(OWNER, "mcbot get 16 oak logs")
    assert talker.wait_idle(5)
    (gather,) = [c for c in host.calls if c[0] == "gather"]
    assert gather[1] == args and gather[2] == OWNER
    second, _ = backend.requests[1]
    assert second[-2]["role"] == "assistant"
    assert second[-2]["tool_calls"][0]["function"]["name"] == "gather"
    assert json.loads(second[-2]["tool_calls"][0]["function"]["arguments"]) == args
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c0"
    assert json.loads(second[-1]["content"])["result"]["job"]["id"] == "j-1"
    assert host.said() == ["On it: job j-1. Say stop any time."]


def test_bad_json_arguments_and_tool_errors_go_back_to_the_model(make):
    host = FakeHost()
    host.results["goto"] = {"ok": False, "error": "give all of x, y and z"}
    talker, host, backend = make(
        [
            tools(
                ("gather", {}, "arguments were not valid JSON"),
                ("goto", {"x": 1}, None),
                ("nonsense", {}, None),
                ("stop", {}, None),
            ),
            text("I need all three coordinates."),
        ],
        host=host,
    )
    talker.hear(OWNER, "mcbot go to x 1")
    assert talker.wait_idle(5)
    results = [json.loads(m["content"]) for m in backend.requests[1][0][-4:]]
    assert results[0] == {"ok": False, "error": "arguments were not valid JSON"}
    assert results[1]["error"] == "give all of x, y and z"
    assert "not available" in results[2]["error"]
    assert "gather" not in host.intents() and "nonsense" not in host.intents()
    assert host.said() == ["I need all three coordinates."]


def test_a_host_exception_becomes_a_tool_error(make):
    host = FakeHost()
    host.results["goto"] = RuntimeError("secret internals")
    talker, host, backend = make(
        [tools(("goto", {"player": "Owner"}, None)), text("It failed.")], host=host
    )
    talker.hear(OWNER, "mcbot come")
    assert talker.wait_idle(5)
    result = json.loads(backend.requests[1][0][-1]["content"])
    assert result["ok"] is False and "secret" not in result["error"]


def test_untrusted_speaker_cannot_call_job_tools(make):
    talker, host, backend = make(
        [tools(("gather", {"item": "x", "count": 1}, None)), text("No.")]
    )
    talker.hear(KID, "mcbot gather stuff")
    assert talker.wait_idle(5)
    assert "gather" not in host.intents()
    result = json.loads(backend.requests[1][0][-1]["content"])
    assert "not available" in result["error"]


def test_tool_round_limit(make, tmp_path):
    loop = tools(("status", {}, None))
    script = [loop, loop, loop, loop, text("giving up")]
    talker, host, backend = make(script)
    talker.hear(OWNER, "mcbot loop")
    assert talker.wait_idle(5)
    assert len(backend.requests) == 5
    assert backend.requests[4][1] in (None, [])  # last round offers no tools
    assert host.said() == ["giving up"]


def test_empty_final_text_after_tools_still_says_something(make):
    talker, host, _ = make([tools(("status", {}, None)), text("")])
    talker.hear(OWNER, "mcbot hm")
    assert talker.wait_idle(5)
    assert host.said() == [companion_talker.EMPTY_REPLY]


# --- failures -----------------------------------------------------------------


def test_model_down_replies_once_and_does_not_retry(make, tmp_path):
    talker, host, backend = make([ChatError("provider unreachable (OSError)")])
    talker.hear(OWNER, "mcbot hello")
    assert talker.wait_idle(5)
    (line,) = host.said()
    assert line.startswith("I can't think right now (provider unreachable")
    assert line.endswith("but stop still works.")
    assert len(backend.requests) == 1
    entry = log_lines(tmp_path)[0]
    assert "unreachable" in entry["error"] and entry["reply"] == line


def test_no_backend_configured(tmp_path):
    host = FakeHost()
    bot = {**BOT, "roles": {"talker": "no-such-backend"}}
    talker = companion_talker.start(
        host, bot=bot, instance=None, results_dir=tmp_path, prompt="P", soul="S"
    )
    try:
        talker.hear(OWNER, "mcbot hi")
        assert talker.wait_idle(5)
        assert "I can't think right now (unknown backend" in host.said()[0]
        talker.hear(OWNER, "stop")
        assert host.intents().count("stop") == 1  # stop still works
    finally:
        talker.stop()


def test_failed_say_does_not_crash_the_worker(make):
    host = FakeHost()
    host.results["say"] = {"ok": False, "error": "no body"}
    talker, host, _ = make([text("a"), text("b")], host=host)
    talker.hear(OWNER, "mcbot one")
    talker.hear(OWNER, "mcbot two")
    assert talker.wait_idle(5)
    assert host.said() == ["a", "b"]


# --- reply shaping ------------------------------------------------------------


def test_short_reply_collapses_whitespace():
    assert companion_talker.split_reply("a\n\n  b  c") == ["a b c"]


def test_long_reply_splits_on_words_into_at_most_two_lines():
    words = " ".join(f"word{i}" for i in range(120))
    lines = companion_talker.split_reply(words)
    assert len(lines) == 2
    assert all(len(line) <= 256 for line in lines)
    assert lines[1].endswith("...") or len(" ".join(lines)) >= len(words) - 1


def test_one_unbroken_word_is_hard_cut():
    lines = companion_talker.split_reply("x" * 900)
    assert len(lines) == 2 and all(len(line) <= 256 for line in lines)


def test_two_lines_are_said_in_order(make):
    talker, host, _ = make([text(" ".join(["abcdefgh"] * 40))])
    talker.hear(OWNER, "mcbot talk")
    assert talker.wait_idle(5)
    assert len(host.said()) == 2 and all(len(s) <= 256 for s in host.said())


# --- history, queue, logging --------------------------------------------------


def test_history_window_per_player(make):
    script = [text(f"r{i}") for i in range(8)] + [text("other")]
    talker, host, backend = make(script)
    for i in range(8):
        talker.hear(OWNER, f"mcbot line {i}")
        assert talker.wait_idle(5)
    messages, _ = backend.requests[7]
    history = messages[1:-1]
    assert len(history) == 12  # 6 exchanges, 2 messages each
    assert history[0] == {"role": "user", "content": "mcbot line 1"}
    assert history[1] == {"role": "assistant", "content": "r1"}
    talker.hear(KID, "mcbot hello")
    assert talker.wait_idle(5)
    assert len(backend.requests[8][0]) == 2  # system + user: Kid has no history


def test_failed_turn_is_not_remembered(make):
    talker, host, backend = make([ChatError("timed out after 5s"), text("ok")])
    talker.hear(OWNER, "mcbot one")
    assert talker.wait_idle(5)
    talker.hear(OWNER, "mcbot two")
    assert talker.wait_idle(5)
    assert len(backend.requests[1][0]) == 2


def test_queue_is_bounded_and_drops_the_oldest(make, tmp_path):
    gate, entered = threading.Event(), threading.Event()

    class Slow(FakeBackend):
        def complete(self, messages, tools=None):
            entered.set()
            gate.wait(5)
            return super().complete(messages, tools)

    host = FakeHost()
    backend = Slow([text(str(i)) for i in range(10)])
    talker = companion_talker.start(
        host,
        bot=BOT,
        instance=None,
        backend=backend,
        results_dir=tmp_path,
        prompt="P",
        soul="S",
        max_queue=2,
    )
    try:
        talker.hear(OWNER, "mcbot first")
        assert entered.wait(5)
        for i in range(2, 6):
            talker.hear(OWNER, f"mcbot line {i}")
        gate.set()
        assert talker.wait_idle(5)
        asked = [r[0][-1]["content"] for r in backend.requests]
        assert "mcbot line 2" not in asked and "mcbot line 3" not in asked
        assert asked[-1] == "mcbot line 5"
        dropped = [e for e in log_lines(tmp_path) if e.get("dropped")]
        assert len(dropped) == 2
    finally:
        talker.stop()


def test_stop_command_is_not_queued_behind_a_slow_turn(make, tmp_path):
    gate = threading.Event()

    class Slow(FakeBackend):
        def complete(self, messages, tools=None):
            gate.wait(5)
            return super().complete(messages, tools)

    host = FakeHost()
    talker = companion_talker.start(
        host,
        bot=BOT,
        instance=None,
        backend=Slow([text("late")]),
        results_dir=tmp_path,
        prompt="P",
        soul="S",
    )
    try:
        talker.hear(OWNER, "mcbot tell me a long story")
        talker.hear(OWNER, "stop")
        assert "stop" in host.intents()
        gate.set()
        assert talker.wait_idle(5)
    finally:
        talker.stop()


def test_turn_log_content_has_no_secrets(make, tmp_path, monkeypatch):
    monkeypatch.setenv("ATOM_API_KEY", "sk-do-not-log")
    talker, host, _ = make(
        [tools(("status", {}, None)), text("All good.")],
    )
    talker.hear(OWNER, "mcbot how are you")
    assert talker.wait_idle(5)
    raw = (next((tmp_path / "companion-talker").glob("*.jsonl"))).read_text()
    assert "sk-do-not-log" not in raw
    (entry,) = [json.loads(line) for line in raw.splitlines()]
    assert entry["speaker"] == "Owner" and entry["text"] == "mcbot how are you"
    assert entry["authorized"] is True
    assert entry["model"] == "fake-model" and entry["latency_ms"] >= 0
    assert entry["tool_calls"] == [{"name": "status", "args": {}, "ok": True}]
    assert entry["reply"] == "All good." and entry["error"] is None
    assert entry["ts"].endswith("Z")


def test_log_records_tool_errors(make, tmp_path):
    host = FakeHost()
    host.results["goto"] = {"ok": False, "error": "nope"}
    talker, host, _ = make(
        [tools(("goto", {"player": "Owner"}, None)), text("x")], host
    )
    talker.hear(OWNER, "mcbot come")
    assert talker.wait_idle(5)
    (call,) = log_lines(tmp_path)[0]["tool_calls"]
    assert call["ok"] is False and call["error"] == "nope"


def test_stop_fast_path_is_logged(make, tmp_path):
    talker, host, _ = make([])
    talker.hear(OWNER, "stop")
    entry = log_lines(tmp_path)[0]
    assert entry["fast_path"] == "stop" and entry["model"] is None


def test_talker_stop_ends_the_worker(make):
    talker, host, backend = make([])
    talker.stop()
    talker.hear(OWNER, "mcbot hi")  # after stop: noted, nothing runs
    assert backend.requests == []
    assert not talker._thread.is_alive()


def test_defaults_come_from_the_bot_file(tmp_path):
    host = FakeHost()
    talker = companion_talker.start(
        host,
        bot={"name": "Z", "roles": {"talker": "x"}},
        instance=None,
        backend=FakeBackend([]),
        results_dir=tmp_path,
    )
    try:
        assert talker.max_rounds == 4 and talker.history_turns == 6
        assert "MCB" in talker.prompt or talker.prompt  # real prompt file loads
    finally:
        talker.stop()
