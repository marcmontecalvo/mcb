import io
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from mclab import companion_intents, mcb_mcp
from mclab.companion_api import ApiUnavailable, serve
from mclab.mcb_mcp import PROTOCOL_VERSIONS, Server


class FakeClient:
    def __init__(self):
        self.calls = []
        self.waits = []
        self.reply = {"ok": True, "result": {"x": 1}}
        self.job = {"id": "j1", "state": "succeeded"}
        self.down = False

    def call(self, intent, args=None, key=None, source="mcp"):
        if self.down:
            raise ApiUnavailable(
                "companion host is not running; start the play server with "
                "`uv run --locked mcplay server start`"
            )
        self.calls.append((intent, args, key, source))
        return self.reply

    def wait_job(self, job_id, wait=0.0):
        if self.down:
            raise ApiUnavailable("companion host is not running")
        self.waits.append((job_id, wait))
        return self.job if job_id == "j1" else None


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def server(client):
    return Server(client, io.StringIO(), io.StringIO())


def rpc(server, method, params=None, id=1):
    message = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def test_initialize_echoes_supported_versions(server):
    for version in PROTOCOL_VERSIONS:
        res = rpc(server, "initialize", {"protocolVersion": version})["result"]
        assert res["protocolVersion"] == version


def test_initialize_falls_back_to_latest(server):
    for params in ({"protocolVersion": "1999-01-01"}, {}, None, {"protocolVersion": 3}):
        res = rpc(server, "initialize", params)["result"]
        assert res["protocolVersion"] == "2025-06-18"


def test_initialize_shape(server):
    res = rpc(server, "initialize", {"protocolVersion": "2025-06-18"})
    assert res["jsonrpc"] == "2.0" and res["id"] == 1
    result = res["result"]
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "mcb-companion"
    assert result["serverInfo"]["version"]
    assert "status" in result["instructions"] and "stop" in result["instructions"]


def test_notifications_get_no_response(server):
    assert (
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    )
    assert server.handle({"jsonrpc": "2.0", "method": "whatever"}) is None
    assert server.handle({"jsonrpc": "2.0", "method": "tools/list"}) is None


def test_ping(server):
    assert rpc(server, "ping")["result"] == {}


def test_tools_list_matches_intents(server):
    tools = rpc(server, "tools/list")["result"]["tools"]
    expected = companion_intents.tool_schemas()
    assert [t["name"] for t in tools][:-1] == [t["name"] for t in expected]
    for tool, intent in zip(tools, expected, strict=False):
        assert set(tool) == {"name", "description", "inputSchema"}
        schema = dict(
            tool["inputSchema"], properties=dict(tool["inputSchema"]["properties"])
        )
        schema["properties"].pop("key", None)  # MCP adds the idempotency key
        assert schema == intent["input_schema"]
        assert tool["description"] == intent["description"]


def test_tools_list_has_wait_job(server):
    tools = {t["name"]: t for t in rpc(server, "tools/list")["result"]["tools"]}
    schema = tools["wait_job"]["inputSchema"]
    assert schema["required"] == ["job_id"]
    assert schema["properties"]["job_id"]["type"] == "string"
    timeout = schema["properties"]["timeout_s"]
    assert (timeout["type"], timeout["minimum"], timeout["maximum"]) == (
        "integer",
        1,
        60,
    )
    assert timeout["default"] == 30
    assert schema["additionalProperties"] is False


def test_tool_call_forwards_and_formats(server, client):
    out = rpc(
        server,
        "tools/call",
        {"name": "gather", "arguments": {"item": "oak_log", "count": 3}},
    )["result"]
    assert client.calls == [("gather", {"item": "oak_log", "count": 3}, None, "mcp")]
    assert out["isError"] is False
    assert out["structuredContent"] == {"ok": True, "result": {"x": 1}}
    (block,) = out["content"]
    assert block["type"] == "text"
    assert block["text"] == '{"ok":true,"result":{"x":1}}'


def test_job_tools_take_an_idempotency_key(server, client):
    tools = {t["name"]: t for t in rpc(server, "tools/list")["result"]["tools"]}
    for name in companion_intents.JOB_INTENTS:
        assert tools[name]["inputSchema"]["properties"]["key"]["type"] == "string"
    for name in companion_intents.INSTANT_INTENTS:
        assert "key" not in tools[name]["inputSchema"]["properties"]
    rpc(
        server,
        "tools/call",
        {"name": "gather", "arguments": {"item": "dirt", "count": 2, "key": "k1"}},
    )
    assert client.calls == [("gather", {"item": "dirt", "count": 2}, "k1", "mcp")]


def test_a_bad_key_or_a_key_on_an_instant_tool_is_refused(server, client):
    out = rpc(
        server,
        "tools/call",
        {"name": "gather", "arguments": {"item": "dirt", "count": 2, "key": 5}},
    )
    assert out["result"]["isError"] is True
    rpc(server, "tools/call", {"name": "status", "arguments": {"key": "k"}})
    # status has no key: the host's validation reports the unknown argument
    assert client.calls == [("status", {"key": "k"}, None, "mcp")]


def test_wait_job_accepts_an_integral_float_timeout(server, client):
    rpc(
        server,
        "tools/call",
        {"name": "wait_job", "arguments": {"job_id": "j1", "timeout_s": 7.0}},
    )
    assert client.waits == [("j1", 7)]


def test_tool_call_without_arguments(server, client):
    out = rpc(server, "tools/call", {"name": "status"})["result"]
    assert client.calls[0][:2] == ("status", {})
    assert out["isError"] is False


def test_host_error_maps_to_is_error(server, client):
    client.reply = {"ok": False, "error": "no place named x"}
    out = rpc(server, "tools/call", {"name": "goto", "arguments": {"place": "x"}})
    out = out["result"]
    assert out["isError"] is True
    assert out["structuredContent"]["error"] == "no place named x"
    assert "no place named x" in out["content"][0]["text"]


def test_host_down_message(server, client):
    client.down = True
    out = rpc(server, "tools/call", {"name": "status"})["result"]
    assert out["isError"] is True
    text = out["content"][0]["text"]
    assert "companion host is not running" in text
    assert "mcplay server start" in text
    assert out["structuredContent"]["ok"] is False


def test_wait_job_default_timeout(server, client):
    out = rpc(server, "tools/call", {"name": "wait_job", "arguments": {"job_id": "j1"}})
    out = out["result"]
    assert client.waits == [("j1", 30)]
    assert out["isError"] is False
    assert out["structuredContent"] == {"ok": True, "result": {"job": client.job}}


def test_wait_job_explicit_timeout(server, client):
    rpc(
        server,
        "tools/call",
        {"name": "wait_job", "arguments": {"job_id": "j1", "timeout_s": 7}},
    )
    assert client.waits == [("j1", 7)]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"job_id": 5},
        {"job_id": ""},
        {"job_id": "j1", "timeout_s": 0},
        {"job_id": "j1", "timeout_s": 61},
        {"job_id": "j1", "timeout_s": "5"},
        {"job_id": "j1", "timeout_s": True},
        {"job_id": "j1", "extra": 1},
    ],
)
def test_wait_job_bad_arguments(server, client, arguments):
    out = rpc(server, "tools/call", {"name": "wait_job", "arguments": arguments})
    assert out["result"]["isError"] is True
    assert client.waits == []


def test_wait_job_unknown_job(server):
    out = rpc(server, "tools/call", {"name": "wait_job", "arguments": {"job_id": "zz"}})
    out = out["result"]
    assert out["isError"] is True
    assert "zz" in out["content"][0]["text"]


def test_wait_job_host_down(server, client):
    client.down = True
    out = rpc(server, "tools/call", {"name": "wait_job", "arguments": {"job_id": "j1"}})
    assert out["result"]["isError"] is True


def test_unknown_tool_is_invalid_params(server):
    out = rpc(server, "tools/call", {"name": "fly"})
    assert out["error"]["code"] == -32602
    assert "fly" in out["error"]["message"]


@pytest.mark.parametrize(
    "params",
    [None, {}, {"name": 5}, {"name": "status", "arguments": []}, []],
)
def test_bad_call_params(server, params):
    out = rpc(server, "tools/call", params)
    assert out["error"]["code"] == -32602


def test_unknown_method(server):
    out = rpc(server, "resources/list")
    assert out["error"]["code"] == -32601


@pytest.mark.parametrize(
    "message", [5, "x", None, [], {"id": 1}, {"id": 1, "method": 3}]
)
def test_invalid_requests_never_crash(server, message):
    out = server.handle(message)
    assert out is None or out["error"]["code"] == -32600


def test_unexpected_client_exception_is_internal_error(server, client):
    def boom(*a, **k):
        raise RuntimeError("boom")

    client.call = boom
    out = rpc(server, "tools/call", {"name": "status"})
    assert out["error"]["code"] == -32603


def test_batch(server):
    out = server.handle(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]
    )
    assert [r["id"] for r in out] == [1, 2]
    assert server.handle([{"jsonrpc": "2.0", "method": "notifications/x"}]) is None
    assert server.handle([])["error"]["code"] == -32600


# --- transport -------------------------------------------------------------------


def run_lines(client, lines):
    stdout = io.StringIO()
    Server(client, io.StringIO("".join(line + "\n" for line in lines)), stdout).serve()
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_serve_over_pipes(client):
    out = run_lines(
        client,
        [
            json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            "",
            "{broken",
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "status"},
                }
            ),
        ],
    )
    assert [r.get("id") for r in out] == [1, None, 2]
    assert out[1]["error"]["code"] == -32700
    assert out[2]["result"]["isError"] is False


class BlockingClient(FakeClient):
    """wait_job blocks until stop arrives (or 5 s pass)."""

    def __init__(self):
        super().__init__()
        self.stopped = threading.Event()

    def call(self, intent, args=None, key=None, source="mcp"):
        if intent == "stop":
            self.stopped.set()
        return super().call(intent, args, key, source)

    def wait_job(self, job_id, wait=0.0):
        self.waits.append((job_id, wait, self.stopped.wait(5)))
        return self.job


def test_a_long_wait_job_does_not_hold_up_stop():
    client = BlockingClient()
    started = time.monotonic()
    out = run_lines(
        client,
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "wait_job", "arguments": {"job_id": "j1"}},
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "stop"},
                }
            ),
        ],
    )
    # stop answered first; the wait saw it; serve returned only after both replied
    assert [r["id"] for r in out] == [2, 1]
    assert client.waits == [("j1", 30, True)]
    assert time.monotonic() - started < 4


def test_serve_returns_on_eof():
    Server(FakeClient(), io.StringIO(""), io.StringIO()).serve()


def test_responses_are_single_line(client):
    stdout = io.StringIO()
    client.reply = {"ok": True, "result": {"text": "a\nb"}}
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "say"}}
    Server(client, io.StringIO(json.dumps(msg) + "\n"), stdout).serve()
    assert len(stdout.getvalue().splitlines()) == 1


# --- lazy runtime client ---------------------------------------------------------


def test_runtime_client_reads_runtime_file_each_call(monkeypatch, tmp_path):
    class Host:
        def call(self, intent, args, actor, *, key=None):
            return {"ok": True, "result": {"intent": intent, "actor": actor.source}}

        def wait_job(self, job_id, timeout):
            return None

        def events(self, after=0, wait=0.0):
            return {}

        def note(self, type, data):
            pass

    path = tmp_path / "companion-host.json"
    monkeypatch.setattr("mclab.companion_api.runtime_file", lambda: path)
    client = mcb_mcp.RuntimeClient()
    with pytest.raises(ApiUnavailable):  # host not started yet
        client.call("status")
    srv = serve(Host(), port=0, token="t")
    try:
        path.write_text(
            json.dumps({"port": srv.port, "token": "t", "pid": os.getpid()})
        )
        out = client.call("status")
        assert out["result"] == {"intent": "status", "actor": "mcp"}
        assert client.wait_job("x", 0) is None
    finally:
        srv.shutdown()


def test_main_runs_server(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    )
    assert mcb_mcp.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {},
    }


def test_end_to_end_subprocess_with_fake_host(tmp_path):
    class Host:
        def call(self, intent, args, actor, *, key=None):
            return {"ok": True, "result": {"echo": intent, "actor": actor.source}}

        def wait_job(self, job_id, timeout):
            return {"id": job_id, "state": "succeeded"}

        def events(self, after=0, wait=0.0):
            return {"latest": 0, "events": []}

        def note(self, type, data):
            pass

    srv = serve(Host(), port=0, token="tok")
    try:
        runtime = tmp_path / "runtime/instances/play"
        runtime.mkdir(parents=True)
        (runtime / "companion-host.json").write_text(
            json.dumps({"port": srv.port, "token": "tok", "pid": os.getpid()})
        )
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "status"},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "wait_job", "arguments": {"job_id": "abc"}},
            },
        ]
        file = str(runtime / "companion-host.json")
        code = (
            "import sys, pathlib\n"
            "from mclab import companion_api\n"
            f"companion_api.runtime_file = lambda: pathlib.Path({file!r})\n"
            "from mclab import mcb_mcp\n"
            "sys.exit(mcb_mcp.main())\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            input="".join(json.dumps(r) + "\n" for r in requests),
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        srv.shutdown()
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[1]["result"]["structuredContent"]["result"] == {
        "echo": "status",
        "actor": "mcp",
    }
    assert replies[2]["result"]["structuredContent"]["result"]["job"]["id"] == "abc"
