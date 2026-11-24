"""In-game chat with the companion: a stop fast path in code, a model for the rest.

``start(host, bot=..., instance=...)`` returns a ``Talker``. The ingress (and
``/mcb ask``) call ``Talker.hear(actor, text)`` and never block: model turns run on
one worker thread. ``stop`` is decided in code from the words alone; a model never
sees it. The model's tool calls run with the *speaker's* actor, so authority holds;
the talker's own spoken replies go out as the operator-level ``api`` actor so an
unauthorized player's question still gets an answer.
Docs: COMPANION_LLM_PLAN_2026-11-23.md section 6.
"""

import json
import re
import threading
import time
from collections import deque
from datetime import UTC, datetime

from mclab import llm_chat
from mclab.companion_intents import SAY_MAX, Actor, authorized, tool_schemas
from mclab.llm_chat import ChatBackend, ChatError
from mclab.paths import ROOT

STOP_WORDS = frozenset({"stop", "halt", "cancel", "wait", "freeze", "enough"})
FILLER_WORDS = frozenset({"it", "please", "pls", "now"})
STOP_REPLY = "Stopped."
REFUSE_REPLY = "Sorry, I only stop for my owner."
EMPTY_REPLY = "Okay."
MAX_LINES = 2
STATUS_CHARS = 1500
RESULT_CHARS = 2000
# The bot speaking for itself: the talker's own replies, never a model tool call.
SPEAK_ACTOR = Actor("api")
STATUS_ACTOR = Actor("api")


def split_reply(text: str) -> list[str]:
    """One to two chat lines of at most 256 characters, cut at word boundaries."""
    text = " ".join(str(text).split())
    if len(text) <= SAY_MAX:
        return [text] if text else []
    lines = []
    for number in range(MAX_LINES):
        last = number == MAX_LINES - 1
        if len(text) <= SAY_MAX:
            lines.append(text)
            return lines
        room = SAY_MAX - 3 if last else SAY_MAX
        cut = text.rfind(" ", 0, room + 1)
        cut = room if cut <= 0 else cut
        lines.append(text[:cut].rstrip() + ("..." if last else ""))
        text = text[cut:].lstrip()
    return lines


def _name_pattern(name: str):
    return re.compile(
        r"(?<![A-Za-z0-9_])@?" + re.escape(name) + r"(?![A-Za-z0-9_])", re.IGNORECASE
    )


def mentions(text: str, name: str) -> bool:
    """Whether ``text`` names the bot (``MCBot`` is not named by ``MCBot2``)."""
    return bool(_name_pattern(name).search(str(text)))


def _load_text(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Talker:
    def __init__(
        self,
        host,
        *,
        bot,
        backend,
        backend_error,
        results_dir,
        prompt,
        soul,
        max_queue=4,
    ):
        self.host = host
        self.bot = bot
        self.name = bot.get("name") or "MCBot"
        self.backend = backend
        self.backend_error = backend_error
        self.prompt, self.soul = prompt, soul
        settings = bot.get("talker") or {}
        self.max_rounds = int(settings.get("max_tool_rounds", 4))
        self.history_turns = int(settings.get("history_turns", 6))
        self.max_queue = max_queue
        self.log_dir = results_dir / "companion-talker"
        self._name = _name_pattern(self.name)
        self._history: dict[str, deque] = {}
        self._queue: deque = deque()
        self._cond = threading.Condition()
        self._busy = False
        self._closed = False
        self._log_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="companion-talker"
        )
        self._thread.start()

    # -- public ---------------------------------------------------------------

    def hear(self, actor: Actor, text: str, *, engaged: bool = True) -> None:
        """A line the bot heard. Cheap and non-blocking apart from a stop.

        ``engaged=False`` (with several bots: the line is for another one) records the
        line and still obeys a stop, but never answers or starts a reply."""
        text = str(text)
        self.host.note("chat_in", {"actor": actor.to_dict(), "text": text})
        addressed = engaged and (
            actor.source == "command" or bool(self._name.search(text))
        )
        if self._is_stop(text):
            if authorized(actor):
                self._stop(actor, text, speak=engaged)
            elif addressed:
                self._speak(REFUSE_REPLY)
            return
        if not addressed:
            return
        with self._cond:
            if self._closed:
                return
            while len(self._queue) >= self.max_queue:
                old = self._queue.popleft()
                self._log({"dropped": True, "speaker": old[0].name, "text": old[1]})
            self._queue.append((actor, text))
            self._cond.notify_all()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._queue or self._busy:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
        return True

    def stop(self) -> None:
        with self._cond:
            self._closed = True
            self._queue.clear()
            self._cond.notify_all()
        self._thread.join(2.0)

    # -- stop fast path -------------------------------------------------------

    def _is_stop(self, text: str) -> bool:
        words = re.findall(r"[a-z']+", self._name.sub(" ", text).lower())
        return (
            bool(words)
            and all(w in STOP_WORDS or w in FILLER_WORDS for w in words)
            and any(w in STOP_WORDS for w in words)
        )

    def _stop(self, actor, text, *, speak=True):
        ok, error = True, None
        try:
            result = self.host.call("stop", {}, actor)
            ok = bool(result.get("ok"))
            error = None if ok else result.get("error")
        except Exception as exc:  # the reply must still go out
            ok, error = False, type(exc).__name__
        if speak:  # with several bots one of them says it
            self._speak(STOP_REPLY)
        self._log(
            {
                "fast_path": "stop",
                "speaker": actor.name,
                "text": text,
                "authorized": True,
                "model": None,
                "tool_calls": [{"name": "stop", "args": {}, "ok": ok}],
                "reply": STOP_REPLY,
                "error": error,
            }
        )

    # -- worker ---------------------------------------------------------------

    def _run(self):
        while True:
            with self._cond:
                while not self._queue and not self._closed:
                    self._cond.wait()
                if self._closed:
                    return
                actor, text = self._queue.popleft()
                self._busy = True
            try:
                self._turn(actor, text)
            except Exception as exc:  # never let one turn kill the worker
                self._log({"speaker": actor.name, "text": text, "error": repr(exc)})
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()

    def _system(self, actor, trusted):
        try:
            status = self.host.call("status", {}, STATUS_ACTOR)
            body = status.get("result") if status.get("ok") else None
            status_text = json.dumps(body, default=str)[:STATUS_CHARS]
        except Exception:
            status_text = "unavailable"
        who = actor.name or "someone"
        rule = (
            f"{who} may command you."
            if trusted
            else f"{who} may not command you: chat and look only, no jobs."
        )
        return (
            f"{self.prompt}\n\n{self.soul}\n\nYour in-game name is {self.name}.\n\n"
            f"Live status (JSON): {status_text}\n\nSpeaking now: {who}. {rule}"
        )

    def _turn(self, actor, text):
        trusted = authorized(actor)
        entry = {
            "speaker": actor.name or actor.source,
            "uuid": actor.uuid,
            "text": text,
            "authorized": trusted,
            "model": None,
            "latency_ms": 0,
            "rounds": 0,
            "tool_calls": [],
            "reply": None,
            "error": None,
        }
        key = actor.name or actor.source
        try:
            if self.backend is None:
                raise ChatError(self.backend_error or "no model configured")
            schemas = llm_chat.openai_tools(tool_schemas(trusted=trusted))
            offered = {t["function"]["name"] for t in schemas}
            messages = [{"role": "system", "content": self._system(actor, trusted)}]
            for said, replied in self._history.get(key, ()):
                messages += [
                    {"role": "user", "content": said},
                    {"role": "assistant", "content": replied},
                ]
            messages.append({"role": "user", "content": text})
            final = ""
            for number in range(self.max_rounds + 1):
                reply = self.backend.complete(
                    messages, schemas if number < self.max_rounds else None
                )
                entry["model"] = reply.model or getattr(self.backend, "model", None)
                entry["latency_ms"] += reply.latency_ms
                entry["rounds"] = number + 1
                if not reply.tool_calls or number == self.max_rounds:
                    final = reply.text
                    break
                messages.append(self._assistant_message(reply))
                for tool in reply.tool_calls:
                    messages.append(self._run_tool(tool, actor, offered, entry))
            final = final or EMPTY_REPLY
            entry["reply"] = final
            self._history.setdefault(key, deque(maxlen=self.history_turns)).append(
                (text, final)
            )
            self._speak(final)
        except ChatError as error:
            entry["error"] = str(error)
            entry["reply"] = f"I can't think right now ({str(error)[:80]}), "
            entry["reply"] += "but stop still works."
            self._speak(entry["reply"])
        self._log(entry)

    @staticmethod
    def _assistant_message(reply):
        return {
            "role": "assistant",
            "content": reply.text or "",
            "tool_calls": [
                {
                    "id": t["id"],
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "arguments": json.dumps(t["arguments"]),
                    },
                }
                for t in reply.tool_calls
            ],
        }

    def _run_tool(self, tool, actor, offered, entry):
        name, args = tool["name"], tool["arguments"]
        if tool.get("error"):
            result = {"ok": False, "error": tool["error"]}
        elif name not in offered:
            result = {"ok": False, "error": f"tool {name!r} is not available"}
        else:
            try:
                result = self.host.call(name, args, actor)
            except Exception:
                result = {"ok": False, "error": "the tool failed unexpectedly"}
        record = {"name": name, "args": args, "ok": result.get("ok") is True}
        if not record["ok"]:
            record["error"] = result.get("error")
        entry["tool_calls"].append(record)
        content = json.dumps(result, default=str)[:RESULT_CHARS]
        return {"role": "tool", "tool_call_id": tool["id"], "content": content}

    # -- output ---------------------------------------------------------------

    def _speak(self, text):
        for line in split_reply(text):
            try:
                self.host.call("say", {"text": line, "to": None}, SPEAK_ACTOR)
            except Exception:
                return

    def _log(self, entry):
        now = datetime.now(UTC)
        entry = {"ts": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", **entry}
        try:
            with self._log_lock:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                path = self.log_dir / f"{now:%Y-%m-%d}.jsonl"
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass


def _soul_for(bot) -> str:
    """``bots/<id>/SOUL.md``; a bot with none of its own (a derived MCBot2) speaks
    with the default bot's, under its own name."""
    identity = str(bot.get("id") or "default")
    own = _load_text(ROOT / "bots" / identity / "SOUL.md")
    if own or identity == "default":
        return own
    shared = _load_text(ROOT / "bots" / "default" / "SOUL.md")
    name = str(bot.get("name") or "MCBot")
    return re.sub(r"(?<![A-Za-z0-9_])MCBot(?![A-Za-z0-9_])", lambda _: name, shared)


def start(
    host,
    *,
    bot,
    instance,
    backend=None,
    results_dir=None,
    prompt=None,
    soul=None,
    max_queue=4,
) -> Talker:
    """Build and start the talker for ``bot`` (a parsed bots/<id>/bot.json).
    ``instance`` is accepted for the host's call shape; the talker keeps no
    per-instance files other than its log under ``results_dir``."""
    backend_error = None
    if backend is None:
        role = (bot.get("roles") or {}).get("talker")
        deadline = float((bot.get("talker") or {}).get("deadline_s", 20.0))
        try:
            if not role:
                raise ChatError("no talker backend configured")
            backend = ChatBackend.from_config(role, deadline_s=deadline)
        except ChatError as error:
            backend, backend_error = None, str(error)
    if prompt is None:
        prompt = _load_text(ROOT / "prompts/companion-talker-v1.md")
    if soul is None:
        soul = _soul_for(bot)
    return Talker(
        host,
        bot=bot,
        backend=backend,
        backend_error=backend_error,
        results_dir=results_dir or ROOT / "results",
        prompt=prompt,
        soul=soul,
        max_queue=max_queue,
    )
