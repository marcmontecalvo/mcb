import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mclab import companion_ingress
from mclab.companion_ingress import Ingress
from mclab.companion_intents import Actor

BOT = {"name": "MCBot"}
UUID = "11111111-2222-3333-4444-555555555555"


class FakeHost:
    def __init__(self):
        self.calls = []
        self.results = {}

    def call(self, intent, args, actor, *, key=None):
        self.calls.append((intent, args, actor))
        return self.results.get(intent, {"ok": True, "result": {}})

    def tells(self):
        return [(a["to"], a["text"]) for i, a, _ in self.calls if i == "say"]

    def non_say(self):
        return [c for c in self.calls if c[0] != "say"]


class FakeTalker:
    def __init__(self):
        self.heard = []

    def hear(self, actor, text):
        self.heard.append((actor, text))


class Source:
    """events_source(after) -> batch; scripted batches, records the cursors asked."""

    def __init__(self, *batches):
        self.batches = list(batches)
        self.asked = []

    def __call__(self, after):
        self.asked.append(after)
        if not self.batches:
            return {"latest_sequence": 0, "gap": False, "events": []}
        item = self.batches.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def batch(events=(), latest=None, gap=False, launch_id="L1"):
    events = list(events)
    if latest is None:
        latest = events[-1]["sequence"] if events else 0
    return {
        "latest_sequence": latest,
        "gap": gap,
        "launch_id": launch_id,
        "events": events,
    }


def command(seq, sub, **data):
    return {
        "sequence": seq,
        "type": "player_command",
        "data": {
            "command": sub,
            "player_uuid": UUID,
            "player_name": "Owner",
            "position": [1.5, 64.0, -3.5],
            **data,
        },
    }


def make(*batches, host=None):
    host, talker = host or FakeHost(), FakeTalker()
    source = Source(batch(latest=10), *batches)
    ingress = Ingress(host, talker, source, bot=BOT)
    ingress.poll_once()  # the head: nothing replayed
    return ingress, host, talker, source


def test_starts_at_the_head_and_never_replays():
    host, talker = FakeHost(), FakeTalker()
    old = batch([command(9, "stop")], latest=9)
    source = Source(old, batch([command(10, "status")]))
    ingress = Ingress(host, talker, source, bot=BOT)
    ingress.poll_once()
    assert host.calls == [] and source.asked == [2**62]
    ingress.poll_once()
    assert source.asked[1] == 9
    assert [c[0] for c in host.non_say()] == ["status"]


def test_cursor_advances_and_events_are_handled_once():
    ingress, host, _, source = make(
        batch([command(11, "stop")], latest=11), batch([], latest=11)
    )
    ingress.poll_once()
    ingress.poll_once()
    assert source.asked[1:] == [10, 11]
    assert len(host.non_say()) == 1


def test_non_matching_events_are_skipped_but_advance():
    noise = {"sequence": 11, "type": "sample", "data": {}}
    ingress, host, _, source = make(batch([noise], latest=15), batch())
    ingress.poll_once()
    ingress.poll_once()
    assert host.calls == [] and source.asked[-1] == 15


def test_gap_is_logged_and_events_still_handled(capsys):
    ingress, host, _, _ = make(batch([command(900, "stop")], gap=True))
    ingress.poll_once()
    assert ingress.gaps == 1 and "gap" in capsys.readouterr().out
    assert [c[0] for c in host.non_say()] == ["stop"]


def test_relaunch_restarts_the_cursor():
    ingress, host, _, source = make(
        batch([], latest=3, launch_id="L2"),
        batch([command(1, "stop")], latest=1, launch_id="L2"),
    )
    ingress.poll_once()
    assert source.asked[1:] == [10, 0]
    assert [c[0] for c in host.non_say()] == ["stop"]
    assert ingress.cursor == 1


def test_stop_command():
    ingress, host, _, _ = make(batch([command(11, "stop")]))
    ingress.poll_once()
    intent, args, actor = host.non_say()[0]
    assert (intent, args) == ("stop", {})
    assert actor == Actor("command", "Owner", UUID, (1.5, 64.0, -3.5))
    assert host.tells() == [("Owner", "Stopped.")]
    assert host.calls[-1][2].source == "api"


def test_stop_error_is_told():
    host = FakeHost()
    host.results["stop"] = {"ok": False, "error": "not allowed"}
    ingress, host, _, _ = make(batch([command(11, "stop")]), host=host)
    ingress.poll_once()
    assert host.tells() == [("Owner", "Can't do that: not allowed")]


def test_status_tells_the_explanation():
    host = FakeHost()
    host.results["status"] = {"ok": True, "result": {"explanation": "Mining at 1 2 3"}}
    ingress, host, _, _ = make(batch([command(11, "status")]), host=host)
    ingress.poll_once()
    assert host.tells() == [("Owner", "Mining at 1 2 3")]


def test_status_without_an_explanation():
    ingress, host, _, _ = make(batch([command(11, "status")]))
    ingress.poll_once()
    assert host.tells() == [("Owner", "Status is not available.")]


def test_goto():
    host = FakeHost()
    host.results["goto"] = {"ok": True, "result": {"job": {"id": "j-ab12"}}}
    ingress, host, _, _ = make(
        batch([command(11, "goto", x=10, y=64, z=-5)]), host=host
    )
    ingress.poll_once()
    assert host.non_say()[0][:2] == ("goto", {"x": 10, "y": 64, "z": -5})
    assert host.tells() == [("Owner", "Started job j-ab12: goto 10 64 -5")]


def test_gather():
    host = FakeHost()
    host.results["gather"] = {"ok": True, "result": {"job": {"id": "j-1"}}}
    ingress, host, _, _ = make(
        batch([command(11, "gather", item="oak_log", count=16)]), host=host
    )
    ingress.poll_once()
    assert host.non_say()[0][:2] == ("gather", {"item": "oak_log", "count": 16})
    assert host.tells() == [("Owner", "Started job j-1: gather 16 oak_log")]


def test_build():
    host = FakeHost()
    host.results["build"] = {"ok": True, "result": {"job": {"id": "j-2"}}}
    ingress, host, _, _ = make(batch([command(11, "build", name="hut")]), host=host)
    ingress.poll_once()
    assert host.non_say()[0][:2] == ("build", {"template": "hut"})
    assert host.tells() == [("Owner", "Started job j-2: build hut")]


def test_job_without_an_id_still_acknowledges():
    ingress, host, _, _ = make(batch([command(11, "build", name="hut")]))
    ingress.poll_once()
    assert host.tells() == [("Owner", "Started: build hut")]


def test_validation_error_is_told_briefly():
    host = FakeHost()
    host.results["gather"] = {"ok": False, "error": "item must be " + "x" * 400}
    ingress, host, _, _ = make(
        batch([command(11, "gather", item="??", count=1)]), host=host
    )
    ingress.poll_once()
    (_, text) = host.tells()[0]
    assert text.startswith("Can't do that: item must be") and len(text) <= 256


def test_ask_goes_to_the_talker_without_a_reply():
    ingress, host, talker, _ = make(batch([command(11, "ask", text="what now?")]))
    ingress.poll_once()
    assert host.calls == []
    ((actor, text),) = talker.heard
    assert text == "what now?" and actor.source == "command"
    assert actor.name == "Owner" and actor.uuid == UUID


def test_unknown_subcommand_and_missing_fields():
    ingress, host, talker, _ = make(
        batch(
            [
                command(11, "dance"),
                command(12, "goto", x=1),
                command(13, "ask"),
                {"sequence": 14, "type": "player_command", "data": {"command": "stop"}},
            ]
        )
    )
    ingress.poll_once()
    assert host.non_say() == [] and talker.heard == []
    assert host.tells()[0] == ("Owner", "Unknown command.")
    assert host.tells()[1][1].startswith("Can't do that")


def test_chat_line_goes_to_the_talker():
    event = {
        "sequence": 11,
        "type": "player_chat",
        "data": {
            "player_name": "Kid",
            "player_uuid": "u-kid",
            "text": "MCBot come here",
            "position": {"x": 1, "y": 2, "z": 3},
        },
    }
    ingress, host, talker, _ = make(batch([event]))
    ingress.poll_once()
    ((actor, text),) = talker.heard
    assert actor == Actor("chat", "Kid", "u-kid", (1.0, 2.0, 3.0))
    assert text == "MCBot come here" and host.calls == []


def test_chat_without_text_or_name_is_ignored():
    events = [
        {"sequence": 11, "type": "player_chat", "data": {"player_name": "Kid"}},
        {"sequence": 12, "type": "player_chat", "data": {"text": "hi"}},
        {"sequence": 13, "type": "player_chat", "data": "junk"},
    ]
    ingress, host, talker, _ = make(batch(events))
    ingress.poll_once()
    assert talker.heard == []


def test_a_raising_host_does_not_stop_the_batch():
    class Boom(FakeHost):
        def call(self, intent, args, actor, *, key=None):
            if intent == "stop":
                raise RuntimeError("x")
            return super().call(intent, args, actor)

    ingress, host, _, _ = make(
        batch([command(11, "stop"), command(12, "status")]), host=Boom()
    )
    ingress.poll_once()
    assert ("Owner", "Something went wrong.") in host.tells()
    assert [c[0] for c in host.non_say()] == ["status"]


def test_bad_position_is_dropped():
    event = command(11, "stop")
    event["data"]["position"] = "here"
    ingress, host, _, _ = make(batch([event]))
    ingress.poll_once()
    assert host.non_say()[0][2].position is None


def test_run_loop_survives_source_errors(capsys):
    host, talker = FakeHost(), FakeTalker()
    seen = threading.Event()
    stopper = threading.Event()

    class Flaky(Source):
        def __call__(self, after):
            if not self.batches:
                seen.set()
            return super().__call__(after)

    source = Flaky(
        OSError("down"),
        batch(latest=5),
        OSError("down"),
        OSError("down"),
        batch([command(6, "stop")]),
    )
    ingress = Ingress(host, talker, source, bot=BOT, poll_s=0.01)
    ingress.start()
    try:
        assert seen.wait(5)
        stopper.wait(0.1)
    finally:
        ingress.stop()
    out = capsys.readouterr().out
    assert out.count("OSError") == 2  # once per streak: a repeat is not logged
    assert [c[0] for c in host.non_say()] == ["stop"]
    assert not ingress._thread.is_alive()


# --- the real HTTP source -------------------------------------------------------


@pytest.fixture
def bridge():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path.startswith("/big"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"x" * 9_000_000)
                return
            body = json.dumps(
                {"latest_sequence": 4, "gap": False, "events": [], "launch_id": "L"}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], seen
    server.shutdown()
    server.server_close()


def test_bridge_source_sends_the_token_and_cursor(bridge):
    port, seen = bridge
    source = companion_ingress.bridge_events_source(
        port=lambda: port, token=lambda: "tok"
    )
    assert source(7)["latest_sequence"] == 4
    assert seen == [("/events?after=7", "Bearer tok")]


def test_bridge_source_errors_are_plain_oserrors(bridge):
    port, _ = bridge
    source = companion_ingress.bridge_events_source(
        port=lambda: port, token=lambda: "tok", path="/big"
    )
    with pytest.raises(OSError, match="byte limit"):
        source(1)
    down = companion_ingress.bridge_events_source(port=lambda: 1, token=lambda: "t")
    with pytest.raises(OSError):
        down(1)


def test_default_source_reads_instance_state(monkeypatch, bridge, tmp_path):
    port, seen = bridge
    from mclab import paths, server

    state = tmp_path / "supervisor.json"
    state.write_text(json.dumps({"token": "abc"}))
    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(paths, "config", lambda: {"bridge_port": port})
    companion_ingress.bridge_events_source()(2)
    assert seen[0] == ("/events?after=2", "Bearer abc")
