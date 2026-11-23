"""MCP server (stdio, newline-delimited JSON-RPC 2.0) for the companion bot.

Tools are the companion intents plus ``wait_job``; every call goes to the host's
control API (``companion_api``). Only JSON-RPC goes to stdout; logs go to stderr.
Docs: COMPANION_LLM_PLAN_2026-11-23.md section 5.
"""

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata

from mclab import companion_api, companion_intents
from mclab.companion_api import ApiUnavailable

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "mcb-companion"
WAIT_DEFAULT_S = 30
WAIT_MAX_S = 60
KEY_MAX = 80
# tools/call runs off the read loop so a long wait_job never holds up a stop.
CALL_WORKERS = 8

INSTRUCTIONS = (
    "Drives one Minecraft companion bot. Call status first to see what it is "
    "doing. Job tools (goto, follow, gather, build) return a job id: follow it "
    "with wait_job until the state is succeeded, failed or cancelled, and do not "
    "submit the same job again while it runs. stop is always safe."
)

WAIT_JOB_TOOL = {
    "name": "wait_job",
    "description": (
        "Wait up to timeout_s seconds for a job to finish and return it with its "
        "progress and result. Call again if it is still running."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "the id a job tool returned"},
            "timeout_s": {
                "type": "integer",
                "minimum": 1,
                "maximum": WAIT_MAX_S,
                "default": WAIT_DEFAULT_S,
            },
        },
        "required": ["job_id"],
        "additionalProperties": False,
    },
}

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND = -32700, -32600, -32601
INVALID_PARAMS, INTERNAL_ERROR = -32602, -32603


def _log(text: str) -> None:
    print(f"mcb-mcp: {text}", file=sys.stderr, flush=True)


def _version() -> str:
    try:
        return metadata.version("mclab")
    except metadata.PackageNotFoundError:
        return "0.0.0"


class _InvalidParams(Exception):
    pass


KEY_PROPERTY = {
    "type": "string",
    "minLength": 1,
    "maxLength": KEY_MAX,
    "description": "Idempotency key: the same key returns the job already made "
    "instead of starting a second one. Use one per request you mean once.",
}


def _schema(tool: dict) -> dict:
    schema = tool["input_schema"]
    if tool["name"] not in companion_intents.JOB_INTENTS:
        return schema
    return {**schema, "properties": {**schema["properties"], "key": KEY_PROPERTY}}


def _tools() -> list[dict]:
    tools = [
        {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": _schema(tool),
        }
        for tool in companion_intents.tool_schemas()
    ]
    return [*tools, WAIT_JOB_TOOL]


def _tool_result(value: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(value, separators=(",", ":"))}],
        "structuredContent": value,
        "isError": value.get("ok") is False,
    }


def _failure(message: str) -> dict:
    return _tool_result({"ok": False, "error": message})


class RuntimeClient:
    """An API client that re-reads the host's runtime file on every call, so the
    MCP server survives the host starting late or restarting on a new port."""

    def call(self, intent, args=None, key=None, source="mcp"):
        return companion_api.ApiClient.from_runtime().call(intent, args, key, source)

    def wait_job(self, job_id, wait=0.0):
        return companion_api.ApiClient.from_runtime().wait_job(job_id, wait)


class Server:
    def __init__(self, client, stdin=None, stdout=None):
        self.client = client
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self._write_lock = threading.Lock()

    # -- transport

    def serve(self) -> None:
        """Read until EOF; return once every request has been answered."""
        with ThreadPoolExecutor(CALL_WORKERS, thread_name_prefix="mcp-call") as pool:
            for line in self.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    self._write(self._error(None, PARSE_ERROR, "Parse error"))
                    continue
                if isinstance(message, dict) and message.get("method") == "tools/call":
                    pool.submit(lambda m=message: self._write(self.handle(m)))
                else:
                    self._write(self.handle(message))

    def _write(self, response) -> None:
        if response is None:
            return
        line = json.dumps(response, separators=(",", ":")) + "\n"
        with self._write_lock:
            self.stdout.write(line)
            self.stdout.flush()

    # -- dispatch

    def handle(self, message):
        """The response for one message (or batch), None for notifications."""
        if isinstance(message, list):
            if not message:
                return self._error(None, INVALID_REQUEST, "Empty batch")
            responses = [r for r in map(self.handle, message) if r is not None]
            return responses or None
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            return self._error(
                message.get("id") if isinstance(message, dict) else None,
                INVALID_REQUEST,
                "Invalid request",
            )
        method, request_id = message["method"], message.get("id")
        if "id" not in message:
            return None  # a notification: never answered
        params = message.get("params")
        try:
            result = self._dispatch(method, params)
        except _InvalidParams as error:
            return self._error(request_id, INVALID_PARAMS, str(error))
        except LookupError:
            return self._error(request_id, METHOD_NOT_FOUND, f"Unknown method {method}")
        except Exception as error:
            _log(f"{method} failed: {type(error).__name__}: {error}")
            return self._error(request_id, INTERNAL_ERROR, "Internal error")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _dispatch(self, method: str, params):
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": _tools()}
        if method == "tools/call":
            return self._call_tool(params)
        raise LookupError(method)

    @staticmethod
    def _error(request_id, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    # -- methods

    @staticmethod
    def _initialize(params) -> dict:
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
            "instructions": INSTRUCTIONS,
        }

    def _call_tool(self, params) -> dict:
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            raise _InvalidParams("tools/call needs a tool name")
        name, arguments = params["name"], params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise _InvalidParams("arguments must be an object")
        if name != "wait_job" and name not in companion_intents.INTENTS:
            raise _InvalidParams(f"Unknown tool {name!r}")
        key = None
        if name in companion_intents.JOB_INTENTS and "key" in arguments:
            arguments = dict(arguments)
            key = arguments.pop("key")
            if not isinstance(key, str) or not 1 <= len(key) <= KEY_MAX:
                return _failure(f"key must be a string of 1 to {KEY_MAX} characters")
        try:
            if name == "wait_job":
                return self._wait_job(arguments)
            return _tool_result(self.client.call(name, arguments, key))
        except ApiUnavailable as error:
            return _failure(str(error))

    def _wait_job(self, arguments: dict) -> dict:
        job_id = arguments.get("job_id")
        timeout = arguments.get("timeout_s", WAIT_DEFAULT_S)
        extra = sorted(set(arguments) - {"job_id", "timeout_s"})
        if extra:
            return _failure(f"Unknown argument {extra[0]!r} for wait_job")
        if not isinstance(job_id, str) or not job_id:
            return _failure("job_id must be the id string a job tool returned")
        if isinstance(timeout, float) and timeout.is_integer():
            timeout = int(timeout)  # a model's 30.0
        if type(timeout) is not int or not 1 <= timeout <= WAIT_MAX_S:
            return _failure(f"timeout_s must be an integer from 1 to {WAIT_MAX_S}")
        job = self.client.wait_job(job_id, timeout)
        if job is None:
            return _failure(f"Unknown job {job_id!r}; check status for current jobs")
        return _tool_result({"ok": True, "result": {"job": job}})


def main() -> int:
    try:
        Server(RuntimeClient()).serve()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
