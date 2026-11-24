"""Loopback control API for the companion host, and its client.

Routes (all need ``Authorization: Bearer <token>``; JSON in and out):

- ``POST /call`` ``{intent, args?, key?, source?, bot?}``  ->  the host's ``call`` dict
- ``GET /jobs/<id>?wait=<s>``  ->  the job (long-polled, wait capped at 60 s) or 404
- ``GET /events?after=<n>&wait=<s>``  ->  the host's events
- ``GET /health``  ->  ``{"ok": true}``
- ``POST /trial`` ``{clear?, clear_inventory?, give?, wear?, heal?, time?,
  summon?}``  ->  as ``/call``; sets the scene for an operator's combat trial
  (never an MCP tool)

With several bots (a ``Fleet``) every route above takes the bot's id (``bot`` in the
``/call`` body, ``?bot=`` otherwise); none means the first bot, and ``stop`` alone
takes ``"all"``. And:

- ``GET /bots``  ->  ``{"ok": true, "bots": [{id, name, session, job, default}]}``
- ``POST /bots`` ``{id?}``  ->  ``{"ok": true, "bot": {id, name}}``: put a bot in
  (the next free name by default) or ``{"ok": false, "error"}``
- ``DELETE /bots/<id>``  ->  as ``POST /bots``: the bot halts and leaves
- ``POST /shutdown``  ->  202; the host halts and despawns the body and exits (the
  supervisor's stop; Windows ``terminate()`` would skip that)

The host publishes ``{port, token, pid}`` in its runtime file;
``ApiClient.from_runtime`` reads it.
Docs: COMPANION_LLM_PLAN_2026-11-23.md section 4.
"""

import hmac
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mclab.companion_intents import Actor
from mclab.companion_protocol import HostError

log = logging.getLogger(__name__)
MAX_BODY = 64 * 1024
MAX_DISCARD = 1024 * 1024  # an oversize body read and dropped so the 413 arrives
MAX_WAIT = 60.0
SOURCES = ("api", "mcp")
RUNTIME_FILE = "companion-host.json"
DEFAULT_INSTANCE = "play"
HOST_DOWN = (
    "companion host is not running; start the play server with "
    "`uv run --env-file .env --locked mcplay server start`"
)


class ApiUnavailable(RuntimeError):
    """The control API cannot be reached or refused our credentials."""


def runtime_file() -> Path:
    """Where this instance's host writes its port and token."""
    from mclab import paths

    name = os.environ.get("MCLAB_INSTANCE") or DEFAULT_INSTANCE
    return paths.instance_paths(name).runtime / RUNTIME_FILE


def _pid_alive(pid: int) -> bool:
    from mclab.server import pid_alive

    return pid_alive(pid)


def _number(query: dict, key: str, cast, default):
    try:
        return cast(query.get(key, [default])[0])
    except (TypeError, ValueError):
        return default


def _wait(query: dict) -> float:
    value = _number(query, "wait", float, 0.0)
    if value != value:  # NaN
        return 0.0
    return min(max(value, 0.0), MAX_WAIT)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    host = None
    token = b""
    on_shutdown = None

    def log_message(self, format, *args):  # never log request lines or headers
        pass

    # -- plumbing

    def _send(self, status: int, body) -> None:
        data = json.dumps(body, separators=(",", ":"), default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if status in (400, 413):
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(data)

    def _fail(self, status: int, error: str) -> None:
        self._send(status, {"ok": False, "error": error})

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, given = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(given.strip().encode(), self.token)

    def _guard(self, handler) -> None:
        if not self._authorized():
            self._drain()
            self.close_connection = True
            return self._fail(401, "missing or wrong bearer token")
        try:
            handler()
        except HostError as error:  # a bot that is not here, on a read
            self._fail(404, str(error))
        except Exception as error:  # the host must never take the API down
            # the caller gets the type only; the host log keeps the traceback
            log.exception("control API %s %s failed", self.command, self.path)
            self.close_connection = True
            self._fail(500, f"internal error: {type(error).__name__}")

    def _json_body(self):
        """The parsed request object, or None after replying with an error."""
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._fail(400, "Content-Length is required")
            return None
        if length < 0:
            self._fail(400, "bad Content-Length")
        elif length > MAX_BODY:
            self._discard(length)
            self._fail(413, f"request body over {MAX_BODY} bytes")
        else:
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._fail(400, "body is not valid JSON")
                return None
            if isinstance(value, dict):
                return value
            self._fail(400, "body must be a JSON object")
        return None

    def _drain(self) -> None:
        """Read a body the route ignores. Left in the socket, it resets the
        connection under the reply on Windows and starts the next kept-alive
        request; one that cannot be read whole closes the connection instead."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if 0 <= length <= MAX_BODY:
            self.rfile.read(length)
        else:
            self.close_connection = True

    def _discard(self, length: int) -> None:
        """Read and drop an oversize body (up to MAX_DISCARD) before refusing it:
        unread bytes make Windows reset the socket under the 413, so the client
        sees a connection abort instead of the reply."""
        left = min(length, MAX_DISCARD)
        while left > 0:
            chunk = self.rfile.read(min(left, 64 * 1024))
            if not chunk:
                break
            left -= len(chunk)

    # -- routes

    def do_GET(self):
        self._guard(self._get)

    def do_POST(self):
        self._guard(self._post)

    def _other(self):
        self._drain()
        self.close_connection = True
        self._fail(405, "method not allowed")

    do_PUT = do_PATCH = _other

    def do_DELETE(self):
        self._guard(self._delete)

    @staticmethod
    def _route(bot) -> dict:
        """The ``bot=`` argument, only when a bot was named: a host of one bot takes
        none, and a fleet answers for its first bot without it."""
        return {} if bot is None else {"bot": bot}

    @staticmethod
    def _query_bot(query):
        return (query.get("bot") or [None])[0] or None

    def _fleet(self) -> bool:
        return hasattr(self.host, "bots")

    def _get(self):
        self._drain()  # no GET route reads a body
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        route = self._route(self._query_bot(query))
        if url.path == "/health":
            return self._send(200, {"ok": True})
        if url.path == "/events":
            after = max(_number(query, "after", int, 0), 0)
            return self._send(200, self.host.events(after, _wait(query), **route))
        if url.path == "/bots" and self._fleet():
            return self._send(200, {"ok": True, "bots": self.host.bots()})
        if url.path.startswith("/jobs/") and len(url.path) > 6:
            job_id = urllib.parse.unquote(url.path[6:])
            job = self.host.wait_job(job_id, _wait(query), **route)
            if job is None:
                return self._fail(404, f"unknown job {job_id!r}")
            return self._send(200, job)
        self._fail(404, "not found")

    def _post(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/shutdown" and self.on_shutdown is not None:
            self._drain()
            self.on_shutdown()  # sets a flag; the host winds down on its own thread
            return self._send(202, {"ok": True})
        if url.path == "/trial":
            spec = self._json_body()
            if spec is not None:
                route = self._route(self._query_bot(query))
                self._send(200, self.host.trial(spec, **route))
            return
        if url.path == "/bots" and self._fleet():
            return self._add_bot()
        if url.path != "/call":
            self._drain()
            return self._fail(404, "not found")
        body = self._json_body()
        if body is None:
            return
        intent, args, key = body.get("intent"), body.get("args"), body.get("key")
        source = body.get("source", "api")
        bot = body.get("bot", self._query_bot(query))
        if not isinstance(intent, str) or not intent:
            return self._fail(400, "intent must be a non-empty string")
        if args is not None and not isinstance(args, dict):
            return self._fail(400, "args must be an object")
        if key is not None and not isinstance(key, str):
            return self._fail(400, "key must be a string")
        if bot is not None and not isinstance(bot, str):
            return self._fail(400, "bot must be a string")
        if source not in SOURCES:
            return self._fail(400, f"source must be one of {', '.join(SOURCES)}")
        reply = self.host.call(intent, args, Actor(source), key=key, **self._route(bot))
        self._send(200, reply)

    def _add_bot(self):
        body = self._json_body()
        if body is None:
            return
        bot_id = body.get("id")
        if bot_id is not None and not isinstance(bot_id, str):
            return self._fail(400, "id must be a string")
        self._bot_reply(lambda: self.host.add(bot_id))

    def _delete(self):
        path = urllib.parse.urlsplit(self.path).path
        if not (path.startswith("/bots/") and len(path) > 6 and self._fleet()):
            return self._other()
        self._drain()
        bot_id = urllib.parse.unquote(path[6:])
        self._bot_reply(lambda: self.host.remove(bot_id))

    def _bot_reply(self, change) -> None:
        """A bot added or removed, or why it could not be (a 200 like ``/call``)."""
        try:
            self._send(200, {"ok": True, "bot": change()})
        except (HostError, ValueError, LookupError) as error:
            self._send(200, {"ok": False, "error": str(error).strip("'\"")})


class Server:
    """A running control API: ``.port`` and ``.shutdown()``."""

    def __init__(self, httpd: ThreadingHTTPServer):
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(
            target=lambda: httpd.serve_forever(poll_interval=0.05),
            name="companion-api",
            daemon=True,
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def serve(host, *, port: int, token: str, on_shutdown=None) -> Server:
    """``on_shutdown``: called by ``POST /shutdown`` (the supervisor's polite stop;
    without it the route does not exist). It must return at once."""
    if not token:
        raise ValueError("a bearer token is required")
    attrs = {
        "host": host,
        "token": token.encode(),
        "on_shutdown": staticmethod(on_shutdown) if on_shutdown else None,
    }
    handler = type("Handler", (_Handler,), attrs)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    return Server(httpd)


# --- client -----------------------------------------------------------------------


class ApiClient:
    def __init__(self, port: int, token: str, timeout: float = 10.0):
        self.port = port
        self._token = token
        self.timeout = timeout

    @classmethod
    def from_runtime(cls, path: Path | str | None = None, **kwargs) -> "ApiClient":
        path = Path(path) if path else runtime_file()
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ApiUnavailable(HOST_DOWN) from None
        except (OSError, ValueError) as error:
            raise ApiUnavailable(f"cannot read {path.name}: {error}") from None
        port = info.get("port") if isinstance(info, dict) else None
        token = info.get("token") if isinstance(info, dict) else None
        if type(port) is not int or not isinstance(token, str) or not token:
            raise ApiUnavailable(f"{path.name} has no usable port and token")
        pid = info.get("pid")
        if type(pid) is int and not _pid_alive(pid):
            # A crashed host's stale file: answer now instead of dialing a dead
            # port (a refused loopback connect costs about 2 s on Windows).
            raise ApiUnavailable(HOST_DOWN)
        return cls(port, token, **kwargs)

    def _timeout_for(self, wait: float) -> float:
        return self.timeout + max(wait, 0.0)

    def _request(self, method: str, path: str, body=None, wait: float = 0.0):
        """(status, parsed JSON). 4xx/5xx with a JSON body are returned, not raised."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method
        )
        req.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_for(wait)) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        except (urllib.error.URLError, OSError):
            raise ApiUnavailable(HOST_DOWN) from None
        if status == 401:
            raise ApiUnavailable(
                "companion host refused the token; restart the MCP server so it "
                "re-reads the runtime file"
            )
        try:
            return status, json.loads(raw)
        except ValueError:
            raise ApiUnavailable(
                f"companion host sent a non-JSON reply ({status})"
            ) from None

    def call(
        self,
        intent: str,
        args: dict | None = None,
        key: str | None = None,
        source: str = "mcp",
        bot: str | None = None,
    ) -> dict:
        """``bot``: which bot (a server with several; none is the first one)."""
        body = {"intent": intent, "args": args or {}, "source": source}
        if key is not None:
            body["key"] = key
        if bot is not None:
            body["bot"] = bot
        return self._request("POST", "/call", body)[1]

    @staticmethod
    def _bot_query(bot: str | None, joiner: str = "&") -> str:
        return "" if bot is None else f"{joiner}bot={urllib.parse.quote(bot, safe='')}"

    def trial(self, spec: dict, bot: str | None = None) -> dict:
        """Set the scene for a combat trial (scripts/combat-trial.py); as ``call``."""
        path = "/trial" + self._bot_query(bot, "?")
        return self._request("POST", path, spec)[1]

    def bots(self) -> list[dict]:
        """The bots in the world, the first (default) one first."""
        return self._request("GET", "/bots")[1].get("bots") or []

    def add_bot(self, bot_id: str | None = None) -> dict:
        """Put a bot in (the next free name by default): ``{"ok", "bot"}`` or
        ``{"ok": False, "error"}``."""
        return self._request("POST", "/bots", {} if bot_id is None else {"id": bot_id})[
            1
        ]

    def remove_bot(self, bot_id: str) -> dict:
        """Take a bot out (it halts and leaves the world); shaped as ``add_bot``."""
        quoted = urllib.parse.quote(str(bot_id), safe="")
        return self._request("DELETE", f"/bots/{quoted}")[1]

    def shutdown(self) -> bool:
        """Ask the host to wind down (halt and despawn the body, then exit)."""
        return self._request("POST", "/shutdown", {})[0] == 202

    def health(self) -> bool:
        """The host answers (``GET /health``); no bot state is read."""
        return self._request("GET", "/health")[0] == 200

    def wait_job(
        self, job_id: str, wait: float = 0.0, bot: str | None = None
    ) -> dict | None:
        quoted = urllib.parse.quote(str(job_id), safe="")
        path = f"/jobs/{quoted}?wait={wait}{self._bot_query(bot)}"
        status, body = self._request("GET", path, wait=wait)
        return None if status == 404 else body

    def events(self, after: int = 0, wait: float = 0.0, bot: str | None = None) -> dict:
        path = f"/events?after={int(after)}&wait={wait}{self._bot_query(bot)}"
        return self._request("GET", path, wait=wait)[1]
