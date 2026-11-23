import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mclab import llm_chat
from mclab.companion_intents import tool_schemas
from mclab.llm_chat import ChatBackend, ChatError

KEY = "sk-secret-test-key"


class FakeModel:
    """A local OpenAI-compatible server returning scripted responses."""

    def __init__(self, script):
        self.script = list(script)  # each: dict body | (status, bytes) | callable
        self.requests = []
        self.headers = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                owner.requests.append(json.loads(self.rfile.read(length)))
                owner.headers.append(dict(self.headers))
                item = owner.script.pop(0)
                if callable(item):
                    item()
                    item = {"choices": [{"message": {"content": "late"}}]}
                status, body = item if isinstance(item, tuple) else (200, item)
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def make():
    servers = []

    def build(script, **kwargs):
        server = FakeModel(script)
        servers.append(server)
        backend = ChatBackend(
            name="fake",
            url=server.url + "/chat/completions",
            model="m1",
            key=kwargs.pop("key", KEY),
            deadline_s=kwargs.pop("deadline_s", 5.0),
            **kwargs,
        )
        return server, backend

    yield build
    for server in servers:
        server.close()


def message(content=None, tool_calls=None):
    value = {"role": "assistant", "content": content}
    if tool_calls is not None:
        value["tool_calls"] = tool_calls
    return {
        "choices": [{"message": value}],
        "model": "served-m1",
        "usage": {"total_tokens": 9},
    }


def call(name, arguments, id="c1"):
    return {
        "id": id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def test_plain_text_reply_and_request_shape(make):
    server, backend = make([message("hi there")])
    reply = backend.complete([{"role": "user", "content": "yo"}], [])
    assert reply.text == "hi there"
    assert reply.tool_calls == []
    assert reply.model == "served-m1"
    assert reply.usage == {"total_tokens": 9}
    assert reply.latency_ms >= 0
    sent = server.requests[0]
    assert sent["model"] == "m1"
    assert "tools" not in sent
    assert server.headers[0]["Authorization"] == "Bearer " + KEY


def test_tools_use_openai_function_format(make):
    server, backend = make([message("ok")])
    backend.complete([], llm_chat.openai_tools(tool_schemas(trusted=False)))
    tools = server.requests[0]["tools"]
    assert [t["function"]["name"] for t in tools] == ["status", "list_places"]
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["parameters"]["type"] == "object"
    assert "input_schema" not in tools[0]["function"]


def test_tool_call_arguments_as_string_or_object(make):
    _, backend = make(
        [
            message(
                None,
                [
                    call("gather", '{"item": "oak_log", "count": 16}', "a"),
                    call("goto", {"player": "Kid"}, "b"),
                ],
            )
        ]
    )
    reply = backend.complete([], [])
    assert reply.text == ""
    assert reply.tool_calls == [
        {
            "id": "a",
            "name": "gather",
            "arguments": {"item": "oak_log", "count": 16},
            "error": None,
        },
        {"id": "b", "name": "goto", "arguments": {"player": "Kid"}, "error": None},
    ]


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '"text"', 5])
def test_bad_arguments_become_a_tool_error(make, bad):
    _, backend = make([message(None, [call("stop", bad)])])
    (tool,) = backend.complete([], []).tool_calls
    assert tool["arguments"] == {}
    assert "arguments" in tool["error"]


def test_empty_arguments_string_is_an_empty_object(make):
    _, backend = make([message(None, [call("stop", "")])])
    (tool,) = backend.complete([], []).tool_calls
    assert tool["arguments"] == {} and tool["error"] is None


def test_missing_id_and_nameless_calls(make):
    raw = [{"function": {"name": "stop", "arguments": "{}"}}, {"function": {}}]
    _, backend = make([message(None, raw)])
    calls = backend.complete([], []).tool_calls
    assert len(calls) == 1 and calls[0]["id"] and calls[0]["name"] == "stop"


def test_think_blocks_are_stripped(make):
    _, backend = make([message("<think>secret\nplan</think>\n\nHello!")])
    assert backend.complete([], []).text == "Hello!"
    _, backend = make([message("Hi<think>never closed")])
    assert backend.complete([], []).text == "Hi"


def test_gateway_thinking_flags(make):
    server, backend = make([message("a")], enable_thinking=False)
    backend.complete([], [])
    assert server.requests[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in server.requests[0]
    server, backend = make([message("a")], reasoning_effort="none")
    backend.complete([], [])
    assert server.requests[0]["reasoning_effort"] == "none"
    assert "chat_template_kwargs" not in server.requests[0]


def test_http_error_never_echoes_body_or_key(make):
    _, backend = make([(500, f"boom {KEY}".encode())])
    with pytest.raises(ChatError) as caught:
        backend.complete([], [])
    assert "500" in str(caught.value)
    assert KEY not in str(caught.value) and "boom" not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [b"not json", {"choices": []}, {"nope": 1}, {"choices": [{"message": 3}]}],
)
def test_malformed_response(make, body):
    _, backend = make([(200, body)])
    with pytest.raises(ChatError, match="malformed|JSON"):
        backend.complete([], [])


def test_oversized_response(make):
    _, backend = make([(200, b" " * 1_100_000)])
    with pytest.raises(ChatError, match="byte limit"):
        backend.complete([], [])


def test_deadline_is_enforced(make):
    server, backend = make([lambda: time.sleep(1.0)], deadline_s=0.2)
    started = time.monotonic()
    with pytest.raises(ChatError, match="timed out"):
        backend.complete([], [])
    assert time.monotonic() - started < 0.9


def test_connection_refused_is_a_chat_error():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    backend = ChatBackend(
        name="x", url=f"http://127.0.0.1:{port}/v1/c", model="m", deadline_s=5
    )
    with pytest.raises(ChatError, match="unreachable|transport"):
        backend.complete([], [])


def test_redirect_rejected():
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.send_response(307)
            self.send_header("Location", "http://127.0.0.1:9/")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        backend = ChatBackend(
            name="x",
            url=f"http://127.0.0.1:{server.server_address[1]}/c",
            model="m",
            deadline_s=2,
        )
        with pytest.raises(ChatError, match="redirect"):
            backend.complete([], [])
    finally:
        server.shutdown()
        server.server_close()


def write_config(tmp_path, **spec):
    base = {
        "name": "t",
        "kind": "chat",
        "model": "qwen",
        "root": "http://10.0.0.1:4000/v1",
        "key_env": "TEST_CHAT_KEY",
        "enable_thinking": False,
    }
    path = tmp_path / "backends.json"
    path.write_text(json.dumps({"backends": [{**base, **spec}]}))
    return path


def test_from_config(tmp_path):
    path = write_config(tmp_path)
    backend = ChatBackend.from_config(
        "t", deadline_s=7, config_path=path, env={"TEST_CHAT_KEY": KEY}
    )
    assert backend.url == "http://10.0.0.1:4000/v1/chat/completions"
    assert backend.model == "qwen" and backend.deadline_s == 7
    assert backend.enable_thinking is False
    assert KEY not in repr(backend)


def test_from_config_errors(tmp_path):
    path = write_config(tmp_path)
    with pytest.raises(ChatError, match="credential"):
        ChatBackend.from_config("t", config_path=path, env={})
    with pytest.raises(ChatError, match="unknown"):
        ChatBackend.from_config("nope", config_path=path, env={})
    off = write_config(tmp_path, enabled=False)
    with pytest.raises(ChatError, match="disabled"):
        ChatBackend.from_config("t", config_path=off, env={"TEST_CHAT_KEY": "k"})
    kind = write_config(tmp_path, kind="systemone")
    with pytest.raises(ChatError, match="chat"):
        ChatBackend.from_config("t", config_path=kind, env={"TEST_CHAT_KEY": "k"})


def test_from_config_real_atom_entry():
    backend = ChatBackend.from_config("atom-qwen-chat", env={"ATOM_API_KEY": "k"})
    assert backend.url.endswith("/v1/chat/completions")
    assert backend.enable_thinking is False
    nokey = ChatBackend.from_config("gpu-docker-qwen3", env={})
    assert nokey.reasoning_effort == "none"
