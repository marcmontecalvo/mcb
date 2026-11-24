"""The companion host as its adapters see it (control API, MCP, talker, /mcb).

``call`` is the only way to make the bot do anything. Shapes:

- ``call(...)`` returns ``{"ok": True, "result": {...}}`` or
  ``{"ok": False, "error": "<message a model can act on>"}``; never raises for a
  bad request. A job intent's result is ``{"job": <job>}``.
- a job: ``{"id", "intent", "args", "actor", "key", "state", "created_at",
  "started_at", "finished_at", "progress": {"stage", "done", "total", "detail"},
  "result", "error"}``; ``state`` in JOB_STATES; times are UNIX seconds or None.
- ``events(after, wait)`` returns ``{"latest": n, "events": [{"seq", "at", "type",
  "data"}]}`` with ``type`` in EVENT_TYPES, oldest first, ``seq`` > ``after``.
- A server with several bots answers through a ``Fleet`` (companion_fleet.py): the
  same calls plus ``bot=<id>`` (none: the first bot; ``"all"``: ``stop`` only).

Docs: COMPANION_LLM_PLAN_2026-11-23.md.
"""

from typing import Protocol

from mclab.companion_intents import Actor

JOB_STATES = ("queued", "running", "succeeded", "failed", "cancelled")
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
EVENT_TYPES = (
    "job_queued",
    "job_started",
    "job_progress",
    # one body step of a job (an attack's fight): {"id", "intent", "step": {"action",
    # "weapon", "target_type", "ok", "reason", and the fight op's scalars}}
    "job_step",
    "job_finished",  # data: the job (state succeeded / failed / cancelled)
    "chat_in",  # data: {"actor": {...}, "text": ...}  a player's line the bot saw
    "chat_out",  # data: {"text": ..., "to": name | None}  what the bot said
    "session",  # data: {"state": "starting" | "ready" | "died" | "lost", "detail"}
    "defense",  # data: {"action": "fight" | "burrow" | ..., "reason", "job": id | None}
    # after each defence step: {"action", "job", "ok"?, "reason"?, and for fight and
    # escape their scalars: "kills", "hits", "crits", "shots", "end", "jumps", ...}
    "defense_step",
    # once a defence is over: {"end": "clear" | "stopped" | "died" | "error",
    # "steps": n, "job": id | None}
    "defense_end",
)


class HostError(Exception):
    """A request the host cannot serve now; the message is for the caller."""


class Host(Protocol):
    def call(
        self, intent: str, args: dict | None, actor: Actor, *, key: str | None = None
    ) -> dict: ...

    def wait_job(self, job_id: str, timeout: float) -> dict | None:
        """The job once terminal or when ``timeout`` (s) ends; None if unknown."""
        ...

    def events(self, after: int = 0, wait: float = 0.0) -> dict: ...

    def note(self, type: str, data: dict) -> None:
        """Append an adapter's event (``chat_in``) to the host event log."""
        ...
