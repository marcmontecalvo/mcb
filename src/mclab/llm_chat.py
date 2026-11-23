"""A small OpenAI-compatible ``/chat/completions`` client with tool calling.

HTTP hygiene follows ``backends.HttpBackend``: no redirects, a response byte limit,
the key from the environment, and errors that never echo a provider body. One
request at a time per call, no retries; the deadline is a hard wall-clock limit.
Docs: COMPANION_LLM_PLAN_2026-11-23.md section 6.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from mclab.paths import ROOT, read_json

CONFIG = ROOT / "config/backends.json"
MAX_RESPONSE_BYTES = 1_048_576
MAX_TOKENS = 400
THINK = re.compile(r"<think>.*?</think>|<think>.*\Z", re.DOTALL)
EFFORTS = {"none", "minimal", "low", "medium", "high"}


class ChatError(Exception):
    """A chat request failed. The message is safe to show: no provider body, no key."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ChatError("provider redirect rejected")


@dataclass
class Reply:
    text: str
    tool_calls: list[dict] = field(default_factory=list)  # id, name, arguments, error
    latency_ms: int = 0
    model: str = ""
    usage: dict = field(default_factory=dict)


def openai_tools(schemas: list[dict]) -> list[dict]:
    """``companion_intents.tool_schemas`` entries as OpenAI function tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in schemas
    ]


def strip_thinking(text: str) -> str:
    return THINK.sub("", text or "").strip()


def _parse_arguments(raw) -> tuple[dict, str | None]:
    """(arguments, error). A model may send an object, a JSON string or garbage."""
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}, "arguments were not valid JSON; send one JSON object"
    if not isinstance(raw, dict):
        return {}, "arguments must be a JSON object"
    return raw, None


def _tool_calls(raw) -> list[dict]:
    calls = []
    for number, item in enumerate(raw or []):
        function = item.get("function") if isinstance(item, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not name:
            continue
        arguments, error = _parse_arguments(function.get("arguments"))
        calls.append(
            {
                "id": str(item.get("id") or f"call_{number}"),
                "name": name,
                "arguments": arguments,
                "error": error,
            }
        )
    return calls


class ChatBackend:
    def __init__(
        self,
        *,
        name,
        url,
        model,
        key=None,
        enable_thinking=None,
        reasoning_effort=None,
        deadline_s=20.0,
    ):
        self.name, self.url, self.model = name, url, model
        self._key = key
        self.enable_thinking = enable_thinking
        self.reasoning_effort = reasoning_effort
        self.deadline_s = float(deadline_s)

    def __repr__(self):
        return f"ChatBackend({self.name!r}, {self.url!r}, {self.model!r})"

    @classmethod
    def from_config(cls, name, *, deadline_s=20.0, config_path=None, env=None):
        env = os.environ if env is None else env
        try:
            specs = read_json(config_path or CONFIG)["backends"]
        except (OSError, ValueError, KeyError):
            raise ChatError("backend config unreadable") from None
        spec = next((s for s in specs if s.get("name") == name), None)
        if spec is None:
            raise ChatError(f"unknown backend {name!r}")
        if not spec.get("enabled", True):
            raise ChatError(f"backend {name!r} is disabled")
        if spec.get("kind") != "chat":
            raise ChatError(f"backend {name!r} is not a chat backend")
        effort = spec.get("reasoning_effort")
        if effort is not None and effort not in EFFORTS:
            raise ChatError("unsupported reasoning_effort")
        key = None
        if spec.get("key_env"):
            key = env.get(spec["key_env"])
            if not key:
                raise ChatError("configured credential is unavailable")
        return cls(
            name=name,
            url=spec["root"].rstrip("/") + "/chat/completions",
            model=spec["model"],
            key=key,
            enable_thinking=spec.get("enable_thinking"),
            reasoning_effort=effort,
            deadline_s=deadline_s,
        )

    def _payload(self, messages, tools):
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.4,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # Same switches as backends.HttpBackend so the Atom gateway behaves alike.
        if self.enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _post(self, payload):
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = "Bearer " + self._key
        try:
            body = json.dumps(payload, allow_nan=False).encode()
        except ValueError:
            raise ChatError("request was not serializable") from None
        request = urllib.request.Request(
            self.url, data=body, headers=headers, method="POST"
        )
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(request, timeout=self.deadline_s + 1) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except ChatError:
            raise
        except urllib.error.HTTPError as error:
            raise ChatError(f"provider HTTP {error.code}") from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, ChatError):
                raise error.reason from None
            raise ChatError(
                f"provider unreachable ({type(error.reason).__name__})"
            ) from None
        except OSError as error:
            raise ChatError(
                f"provider transport error ({type(error).__name__})"
            ) from None
        if len(data) > MAX_RESPONSE_BYTES:
            raise ChatError("response exceeded byte limit")
        try:
            return json.loads(data)
        except ValueError:
            raise ChatError("provider response was not JSON") from None

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        """One round trip. ``tools`` are OpenAI function tools (``openai_tools``)."""
        payload = self._payload(messages, tools)
        box: dict = {}

        def work():
            try:
                box["value"] = self._post(payload)
            except BaseException as error:  # handed to the caller below
                box["error"] = error

        started = time.monotonic()
        thread = threading.Thread(target=work, daemon=True, name="llm-chat")
        thread.start()
        thread.join(self.deadline_s)
        if thread.is_alive():
            raise ChatError(f"timed out after {self.deadline_s:g}s")
        if "error" in box:
            error = box["error"]
            raise error if isinstance(error, ChatError) else ChatError("request failed")
        latency = int((time.monotonic() - started) * 1000)
        try:
            body = box["value"]
            choice = body["choices"][0]["message"]
            content = choice.get("content")
            usage = body.get("usage")
            return Reply(
                text=strip_thinking(content if isinstance(content, str) else ""),
                tool_calls=_tool_calls(choice.get("tool_calls")),
                latency_ms=latency,
                model=str(body.get("model") or self.model),
                usage=usage if isinstance(usage, dict) else {},
            )
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ChatError("malformed response") from None
