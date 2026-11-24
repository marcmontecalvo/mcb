import http.client
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from mclab import companion_api
from mclab.companion_api import ApiClient, ApiUnavailable, serve
from mclab.companion_intents import Actor

TOKEN = "secret-token"


class FakeHost:
    def __init__(self):
        self.calls = []
        self.waits = []
        self.event_calls = []
        self.jobs = {"j1": {"id": "j1", "state": "running"}}
        self.reply = {"ok": True, "result": {"hello": 1}}
        self.trials = []

    def call(self, intent, args, actor, *, key=None):
        self.calls.append((intent, args, actor, key))
        return self.reply

    def wait_job(self, job_id, timeout):
        self.waits.append((job_id, timeout))
        return self.jobs.get(job_id)

    def events(self, after=0, wait=0.0):
        self.event_calls.append((after, wait))
        return {"latest": after + 1, "events": []}

    def note(self, type, data):
        pass

    def trial(self, spec):
        self.trials.append(spec)
        return {"ok": True, "result": {"given": 1}}


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def server(host):
    srv = serve(host, port=0, token=TOKEN)
    yield srv
    srv.shutdown()


def raw(server, method, path, body=None, token=TOKEN, headers=None):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{path}",
        data=None if body is None else data,
        method=method,
    )
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


def test_shutdown_route_runs_the_hook_once_authorized(host):
    # The supervisor asks the host to stop: Windows terminate() is a hard kill
    # that would skip halting and despawning (saving) the body.
    asked = []
    srv = serve(host, port=0, token=TOKEN, on_shutdown=lambda: asked.append(1))
    try:
        assert raw(srv, "POST", "/shutdown", {}, token="wrong")[0] == 401
        assert asked == []
        assert raw(srv, "POST", "/shutdown", {}) == (202, {"ok": True})
        assert asked == [1]
        assert ApiClient(srv.port, TOKEN).shutdown() is True
        assert asked == [1, 1]
    finally:
        srv.shutdown()


def test_shutdown_reads_its_request_body(host):
    # An unread body is left in the socket: on Windows the close then resets the
    # connection under the reply (the full suite flaked on it), and on a kept-alive
    # connection the next request is read from the middle of the old body.
    import http.client

    srv = serve(host, port=0, token=TOKEN, on_shutdown=lambda: None)
    connection = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    try:
        for _ in range(2):
            connection.request(
                "POST", "/shutdown", body=b'{"why": "test"}', headers=headers
            )
            response = connection.getresponse()
            assert response.status == 202
            response.read()
    finally:
        connection.close()
        srv.shutdown()


def test_trial_route_sets_a_scene_for_the_operator(server, host):
    spec = {"clear": 24, "give": [{"item": "minecraft:bow", "count": 1}]}
    assert raw(server, "POST", "/trial", spec, token="wrong")[0] == 401
    assert raw(server, "POST", "/trial", spec) == (
        200,
        {"ok": True, "result": {"given": 1}},
    )
    assert ApiClient(server.port, TOKEN).trial({"heal": True}) == {
        "ok": True,
        "result": {"given": 1},
    }
    assert host.trials == [spec, {"heal": True}]
    assert raw(server, "POST", "/trial", [1, 2])[0] == 400


def test_shutdown_route_is_absent_without_a_hook(server):
    assert raw(server, "POST", "/shutdown", {})[0] == 404


def test_port_zero_binds_a_real_port(server):
    assert server.port > 0


def test_health_requires_token(server):
    assert raw(server, "GET", "/health")[0] == 200
    assert raw(server, "GET", "/health", token=None)[0] == 401
    assert raw(server, "GET", "/health", token="wrong")[0] == 401
    assert raw(server, "GET", "/health", token=TOKEN + "x")[0] == 401


def test_every_route_checks_token_first(server, host):
    assert raw(server, "POST", "/call", {"intent": "stop"}, token="bad")[0] == 401
    assert raw(server, "GET", "/jobs/j1", token="bad")[0] == 401
    assert raw(server, "GET", "/events", token="bad")[0] == 401
    assert host.calls == []


def test_call_forwards_to_host_as_api_actor(server, host):
    status, body = raw(
        server,
        "POST",
        "/call",
        {"intent": "gather", "args": {"item": "oak_log", "count": 4}, "key": "k1"},
    )
    assert status == 200
    assert body == {"ok": True, "result": {"hello": 1}}
    intent, args, actor, key = host.calls[0]
    assert (intent, args, key) == ("gather", {"item": "oak_log", "count": 4}, "k1")
    assert actor == Actor("api")


def test_call_source_mcp(server, host):
    raw(server, "POST", "/call", {"intent": "status", "source": "mcp"})
    assert host.calls[0][2] == Actor("mcp")
    assert host.calls[0][1] is None or host.calls[0][1] == {}


def test_call_source_must_be_api_or_mcp(server, host):
    for bad in ("chat", "command", "", 5):
        status, body = raw(server, "POST", "/call", {"intent": "stop", "source": bad})
        assert status == 400 and body["ok"] is False
    assert host.calls == []


def test_host_error_is_still_200(server, host):
    host.reply = {"ok": False, "error": "no such place"}
    status, body = raw(server, "POST", "/call", {"intent": "goto"})
    assert status == 200 and body == host.reply


@pytest.mark.parametrize(
    "payload",
    [
        b"{not json",
        b"[]",
        b'"str"',
        b"{}",
        b'{"intent": 3}',
        b'{"intent": ""}',
        b'{"intent": "stop", "args": []}',
        b'{"intent": "stop", "key": 5}',
        b"\xff\xfe",
    ],
)
def test_call_malformed_is_400(server, host, payload):
    status, body = raw(server, "POST", "/call", payload)
    assert status == 400
    assert body["ok"] is False and body["error"]
    assert host.calls == []


def test_body_over_limit_is_413(server, host):
    big = json.dumps({"intent": "say", "args": {"text": "x" * 70_000}}).encode()
    status, body = raw(server, "POST", "/call", big)
    assert status == 413 and body["ok"] is False
    assert host.calls == []


def test_missing_or_bad_content_length_is_rejected(server):
    status, body = raw(server, "POST", "/call", None)
    assert status == 400 and body["ok"] is False


def test_unknown_routes_and_methods(server):
    assert raw(server, "GET", "/nope")[0] == 404
    assert raw(server, "POST", "/health", {})[0] == 404
    assert raw(server, "GET", "/call")[0] == 404
    assert raw(server, "PUT", "/call", {})[0] in (404, 405, 501)


@pytest.mark.parametrize("method,path", [("POST", "/health"), ("GET", "/nope")])
def test_a_refused_route_reads_its_body_so_the_connection_stays_sound(
    server, method, path
):
    # Left unread, the body resets the socket under the reply on Windows (a
    # flaky ConnectionAbortedError) and is parsed as the next kept-alive request.
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    try:
        conn.request(method, path, body=b'{"a": 1}', headers=auth)
        first = conn.getresponse()
        first.read()
        assert first.status == 404
        conn.request("GET", "/health", headers=auth)
        second = conn.getresponse()
        assert second.status == 200, second.read()
    finally:
        conn.close()


def test_job_found_and_wait_clamped(server, host):
    status, body = raw(server, "GET", "/jobs/j1?wait=500")
    assert status == 200 and body["id"] == "j1"
    assert host.waits == [("j1", 60.0)]


def test_job_wait_default_zero_and_garbage(server, host):
    raw(server, "GET", "/jobs/j1")
    raw(server, "GET", "/jobs/j1?wait=abc")
    raw(server, "GET", "/jobs/j1?wait=-5")
    assert host.waits == [("j1", 0.0)] * 3


def test_job_unknown_is_404(server):
    status, body = raw(server, "GET", "/jobs/zzz?wait=1")
    assert status == 404 and body["ok"] is False


def test_events_params(server, host):
    status, body = raw(server, "GET", "/events?after=7&wait=99")
    assert status == 200 and body == {"latest": 8, "events": []}
    raw(server, "GET", "/events")
    raw(server, "GET", "/events?after=x&wait=y")
    raw(server, "GET", "/events?after=-3&wait=2")
    assert host.event_calls == [(7, 60.0), (0, 0.0), (0, 0.0), (0, 2.0)]


def test_host_exception_is_500_without_leak(server, host):
    def boom(*a, **k):
        raise RuntimeError("internal detail")

    host.call = boom
    status, body = raw(server, "POST", "/call", {"intent": "stop"})
    assert status == 500 and body["ok"] is False
    assert "internal detail" not in json.dumps(body)


def test_host_exception_traceback_goes_to_the_host_log(server, host, caplog):
    # the caller sees the type only; without the log, a 500 could not be traced
    def boom(*a, **k):
        raise AttributeError("'NoneType' object has no attribute 'trial'")

    host.trial = boom
    with caplog.at_level("ERROR", logger="mclab.companion_api"):
        status, _ = raw(server, "POST", "/trial", {"give": []})
    assert status == 500
    assert any(
        r.exc_info and "no attribute 'trial'" in str(r.exc_info[1])
        for r in caplog.records
    )


def test_shutdown_stops_serving(host):
    srv = serve(host, port=0, token=TOKEN)
    port = srv.port
    srv.shutdown()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)


def test_concurrent_long_poll_does_not_block_others(server, host):
    release = threading.Event()
    original = host.wait_job

    def slow(job_id, timeout):
        release.wait(3)
        return original(job_id, timeout)

    host.wait_job = slow
    results = []
    t = threading.Thread(
        target=lambda: results.append(raw(server, "GET", "/jobs/j1?wait=5"))
    )
    t.start()
    assert raw(server, "GET", "/health")[0] == 200
    release.set()
    t.join(5)
    assert results[0][0] == 200


# --- client ---------------------------------------------------------------------


@pytest.fixture
def client(server):
    return ApiClient(server.port, TOKEN)


def test_client_call_defaults_to_mcp_source(client, host):
    out = client.call("status")
    assert out == {"ok": True, "result": {"hello": 1}}
    assert host.calls[0][2] == Actor("mcp")


def test_client_call_passes_args_key_source(client, host):
    client.call("stop", {"a": 1}, key="k", source="api")
    intent, args, actor, key = host.calls[0]
    assert (intent, args, actor, key) == ("stop", {"a": 1}, Actor("api"), "k")


def test_client_wait_job_and_unknown(client, host):
    assert client.wait_job("j1", 5)["id"] == "j1"
    assert client.wait_job("nope", 0) is None


def test_client_events(client, host):
    assert client.events(3, 2)["latest"] == 4
    assert host.event_calls == [(3, 2.0)]


# --- several bots -------------------------------------------------------------------


class FakeFleet(FakeHost):
    """A host that answers for several bots (the fleet's shape)."""

    def __init__(self):
        super().__init__()
        self.routed = []
        self.rows = [{"id": "default", "name": "MCBot"}]
        self.refuse = None

    def call(self, intent, args, actor, *, key=None, bot=None):
        self.routed.append(("call", bot))
        return super().call(intent, args, actor, key=key)

    def wait_job(self, job_id, timeout, *, bot=None):
        self.routed.append(("job", bot))
        return super().wait_job(job_id, timeout)

    def events(self, after=0, wait=0.0, *, bot=None):
        self.routed.append(("events", bot))
        return super().events(after, wait)

    def trial(self, spec, *, bot=None):
        self.routed.append(("trial", bot))
        return super().trial(spec)

    def bots(self):
        return list(self.rows)

    def add(self, bot_id=None):
        if self.refuse:
            raise self.refuse
        self.routed.append(("add", bot_id))
        row = {"id": bot_id or "mcbot2", "name": "MCBot2"}
        self.rows.append(row)
        return row

    def remove(self, bot_id):
        if self.refuse:
            raise self.refuse
        self.routed.append(("remove", bot_id))
        return {"id": bot_id, "name": "MCBot2"}

    def restart(self, bot_id):
        if self.refuse:
            raise self.refuse
        self.routed.append(("restart", bot_id))
        return {"id": bot_id, "name": "Rex"}


@pytest.fixture
def fleet():
    return FakeFleet()


@pytest.fixture
def fleet_server(fleet):
    srv = serve(fleet, port=0, token=TOKEN)
    yield srv
    srv.shutdown()


def test_a_request_names_the_bot_it_is_for(fleet_server, fleet):
    raw(fleet_server, "POST", "/call", {"intent": "status", "bot": "mcbot2"})
    raw(fleet_server, "POST", "/call", {"intent": "status"})
    raw(fleet_server, "GET", "/jobs/j1?bot=mcbot2")
    raw(fleet_server, "GET", "/events?after=1&bot=mcbot3")
    raw(fleet_server, "POST", "/trial?bot=mcbot2", {"heal": True})
    assert fleet.routed == [
        ("call", "mcbot2"),
        ("call", None),
        ("job", "mcbot2"),
        ("events", "mcbot3"),
        ("trial", "mcbot2"),
    ]


def test_a_host_of_one_bot_is_never_asked_for_a_bot(server, host):
    # a plain host takes no bot argument: the key is passed only when named
    assert raw(server, "POST", "/call", {"intent": "status"})[0] == 200
    assert raw(server, "GET", "/events")[0] == 200
    assert raw(server, "GET", "/jobs/j1")[0] == 200
    assert raw(server, "GET", "/bots")[0] == 404


def test_a_bot_must_be_a_name(fleet_server, fleet):
    status, body = raw(fleet_server, "POST", "/call", {"intent": "status", "bot": 5})
    assert status == 400 and "bot" in body["error"]
    assert fleet.routed == []


def test_the_bots_are_listed(fleet_server):
    assert raw(fleet_server, "GET", "/bots") == (
        200,
        {"ok": True, "bots": [{"id": "default", "name": "MCBot"}]},
    )
    assert raw(fleet_server, "GET", "/bots", token="wrong")[0] == 401


def test_a_bot_is_added_and_removed(fleet_server, fleet):
    assert raw(fleet_server, "POST", "/bots", {}) == (
        200,
        {"ok": True, "bot": {"id": "mcbot2", "name": "MCBot2"}},
    )
    assert (
        raw(fleet_server, "POST", "/bots", {"id": "mcbot3"})[1]["bot"]["id"] == "mcbot3"
    )
    assert raw(fleet_server, "DELETE", "/bots/mcbot3") == (
        200,
        {"ok": True, "bot": {"id": "mcbot3", "name": "MCBot2"}},
    )
    assert fleet.routed == [("add", None), ("add", "mcbot3"), ("remove", "mcbot3")]
    assert raw(fleet_server, "POST", "/bots", {"id": 4})[0] == 400
    assert raw(fleet_server, "DELETE", "/bots/x", token="wrong")[0] == 401
    assert raw(fleet_server, "DELETE", "/call")[0] == 405


def test_a_bot_is_restarted_in_its_place(fleet_server, fleet):
    assert raw(fleet_server, "POST", "/bots/mcbot2/restart") == (
        200,
        {"ok": True, "bot": {"id": "mcbot2", "name": "Rex"}},
    )
    assert fleet.routed == [("restart", "mcbot2")]
    assert raw(fleet_server, "POST", "/bots//restart")[0] == 404
    assert raw(fleet_server, "POST", "/bots/mcbot2/restart", token="wrong")[0] == 401
    from mclab.companion_protocol import HostError

    fleet.refuse = HostError("No bot mcbot5; here: default")
    assert raw(fleet_server, "POST", "/bots/mcbot5/restart") == (
        200,
        {"ok": False, "error": "No bot mcbot5; here: default"},
    )


def test_a_host_of_one_bot_has_no_restart(server):
    assert raw(server, "POST", "/bots/default/restart")[0] == 404


def test_a_bot_that_cannot_be_added_says_why(fleet_server, fleet):
    from mclab.companion_protocol import HostError

    fleet.refuse = HostError("A server holds at most 3 bots")
    assert raw(fleet_server, "POST", "/bots", {}) == (
        200,
        {"ok": False, "error": "A server holds at most 3 bots"},
    )
    fleet.refuse = ValueError("No bot profile bots/pal/bot.json")
    assert raw(fleet_server, "DELETE", "/bots/pal")[1]["ok"] is False


def test_the_client_names_bots(fleet_server, fleet):
    client = ApiClient(fleet_server.port, TOKEN)
    client.call("status", bot="mcbot2")
    client.wait_job("j1", 0, bot="mcbot2")
    client.events(0, 0, bot="mcbot2")
    client.trial({"heal": True}, bot="mcbot2")
    assert client.bots() == [{"id": "default", "name": "MCBot"}]
    assert client.add_bot()["bot"]["id"] == "mcbot2"
    assert client.add_bot("mcbot4")["bot"]["id"] == "mcbot4"
    assert client.remove_bot("mcbot4")["bot"]["id"] == "mcbot4"
    assert client.restart_bot("mcbot4")["bot"]["name"] == "Rex"
    assert [r for r in fleet.routed if r[1] == "mcbot2"] == [
        ("call", "mcbot2"),
        ("job", "mcbot2"),
        ("events", "mcbot2"),
        ("trial", "mcbot2"),
    ]
    fleet.refuse = ValueError("no")
    assert client.add_bot() == {"ok": False, "error": "no"}


def test_the_client_of_one_bot_sends_no_bot(client, host):
    client.call("status")
    assert host.calls[0][:2] == ("status", {})


def test_client_wrong_token_is_error(server):
    bad = ApiClient(server.port, "bad")
    with pytest.raises(ApiUnavailable, match="token"):
        bad.call("status")


def test_a_refusal_reads_the_body_so_the_client_sees_the_reply(server):
    # A refused request's unread body reset the connection under the 401 on
    # Windows: the client said "host is not running" for a wrong token.
    big = {"intent": "status", "args": {"pad": "x" * 60_000}}
    for _ in range(100):
        assert raw(server, "POST", "/call", big, token="bad")[0] == 401
        assert raw(server, "PUT", "/call", big)[0] == 405


def test_client_http_timeout_covers_wait(server):
    c = ApiClient(server.port, TOKEN, timeout=10)
    assert c._timeout_for(30) >= 30 + 10 - 1


def test_client_400_returns_error_body(client):
    out = client.call("")
    assert out["ok"] is False


def test_client_host_down(host):
    srv = serve(host, port=0, token=TOKEN)
    port = srv.port
    srv.shutdown()
    with pytest.raises(ApiUnavailable, match="not running"):
        ApiClient(port, TOKEN).call("status")
    with pytest.raises(ApiUnavailable):
        ApiClient(port, TOKEN).wait_job("j1", 0)
    with pytest.raises(ApiUnavailable):
        ApiClient(port, TOKEN).events(0, 0)


def test_client_non_json_response_is_unavailable(monkeypatch):
    import http.server

    class Junk(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>")

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Junk)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with pytest.raises(ApiUnavailable):
            ApiClient(httpd.server_address[1], TOKEN).call("status")
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- runtime file ---------------------------------------------------------------


def test_from_runtime_reads_file(tmp_path, server):
    path = tmp_path / "companion-host.json"
    path.write_text(
        json.dumps({"port": server.port, "token": TOKEN, "pid": os.getpid()}),
        encoding="utf-8",
    )
    client = ApiClient.from_runtime(path)
    assert client.call("status")["ok"] is True


def test_from_runtime_with_a_dead_host_pid_is_down_at_once(tmp_path, server):
    # A stale runtime file from a crashed host: say so without dialing the port
    # (a refused loopback connection costs about 2 s on Windows).
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    path = tmp_path / "companion-host.json"
    path.write_text(
        json.dumps({"port": server.port, "token": TOKEN, "pid": gone.pid}),
        encoding="utf-8",
    )
    started = time.monotonic()
    with pytest.raises(ApiUnavailable, match="not running"):
        ApiClient.from_runtime(path)
    assert time.monotonic() - started < 1.0


def test_from_runtime_missing_file(tmp_path):
    with pytest.raises(ApiUnavailable, match="not running"):
        ApiClient.from_runtime(tmp_path / "missing.json")


@pytest.mark.parametrize("text", ["{bad", "[]", '{"port": 1}', '{"token": "t"}'])
def test_from_runtime_bad_file(tmp_path, text):
    path = tmp_path / "companion-host.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ApiUnavailable):
        ApiClient.from_runtime(path)


def test_runtime_file_default_uses_instance(monkeypatch):
    monkeypatch.setenv("MCLAB_INSTANCE", "play")
    path = companion_api.runtime_file()
    assert path.name == "companion-host.json"
    assert path.parent.name == "play"


def test_runtime_file_defaults_to_play_when_unset(monkeypatch):
    monkeypatch.delenv("MCLAB_INSTANCE", raising=False)
    assert companion_api.runtime_file().parent.name == "play"
