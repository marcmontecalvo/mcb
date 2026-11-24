"""Several companions on one server: a ``Fleet`` of per-bot ``Member`` units.

Each member is one bot: its own ``Host`` (worker thread, jobs, session), its own talker
and the adapters bound to it. The fleet adds and removes members on demand (a match
puts in the bots its roster names; nothing keeps a whole team standing by) and routes
every request by bot id. It answers the same protocol as one ``Host``, plus a ``bot``
argument: without it a request reaches the first bot, so a server with one bot behaves
as it always did. ``bot="all"`` is for ``stop`` alone.

Ids: ``default`` is MCBot (``bots/default``); ``mcbot<N>`` is MCBot<N> (``N`` from 2;
its profile is derived from ``bots/default`` unless ``bots/mcbot<N>`` exists).
Docs: ARENA_RUNTIME.md, "Several bots".
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from mclab.companion_host import DEFAULT_BOT, Host, HostError

log = logging.getLogger(__name__)

ALL = "all"
MAX_BOTS = 16  # what the mod's body hub holds
DEFAULT_ID = "default"
BOT_ID = re.compile(r"mcbot([2-9]|[1-9][0-9])")


def bot_id_for(number: int) -> str:
    """The id of the ``number``-th bot (1 is the default one)."""
    return DEFAULT_ID if number == 1 else f"mcbot{number}"


def bot_name_for(bot_id: str) -> str:
    """The in-game name that goes with a derived bot id."""
    if bot_id == DEFAULT_ID:
        return DEFAULT_BOT["name"]
    found = BOT_ID.fullmatch(bot_id)
    if found is None:
        raise ValueError(f"bot id {bot_id!r} is not 'default' or 'mcbot<N>'")
    return f"{DEFAULT_BOT['name']}{found.group(1)}"


def bot_id_for_name(name: str) -> str | None:
    """The derived id of an in-game name (``MCBot`` and ``MCBot<N>``, any case), or
    None for any other name (a human, or a bot whose profile names it otherwise)."""
    base = DEFAULT_BOT["name"].lower()
    lowered = name.lower()
    if lowered == base:
        return DEFAULT_ID
    candidate = "mcbot" + lowered[len(base) :] if lowered.startswith(base) else ""
    return candidate if BOT_ID.fullmatch(candidate) else None


@dataclass
class Member:
    """One bot of the fleet. ``stop`` are the callables that wind its adapters down
    (talker, ...), run before its host is shut down."""

    id: str
    bot: dict
    host: Host
    talker: object | None = None
    stop: list[Callable[[], None]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return str(self.bot.get("name") or DEFAULT_BOT["name"])

    def close(self) -> None:
        """Adapters first, then the host (it halts and despawns the body)."""
        for halt in reversed(self.stop):
            try:
                halt()
            except Exception:  # noqa: BLE001 - stop the rest anyway
                log.exception("stopping %s failed", self.id)
        self.host.shutdown()


class Fleet:
    def __init__(self, factory: Callable[[str], Member | None], *, limit=MAX_BOTS):
        """``factory(bot_id)`` builds and starts one member (or raises)."""
        self._factory = factory
        self.limit = limit
        self._lock = threading.RLock()
        self._members: dict[str, Member] = {}
        self._pending: set[str] = set()  # ids being built right now
        self._stopping = False

    # --- who is here ----------------------------------------------------------------
    def ids(self) -> list[str]:
        with self._lock:
            return list(self._members)

    def default_id(self) -> str | None:
        with self._lock:
            return next(iter(self._members), None)

    def member(self, bot_id: str | None = None) -> Member:
        """The named bot, or the first one. Raises ``HostError`` saying who is here."""
        with self._lock:
            if not self._members:
                raise HostError("No bot is running; add one first")
            if bot_id is None:
                return next(iter(self._members.values()))
            if bot_id not in self._members:
                raise HostError(f"No bot {bot_id}; here: {', '.join(self._members)}")
            return self._members[bot_id]

    def bots(self) -> list[dict]:
        rows = []
        for member in self._snapshot():
            status = member.host.status()
            job = status.get("job")
            rows.append(
                {
                    "id": member.id,
                    "name": member.name,
                    "session": status.get("session"),
                    "job": job.get("intent") if isinstance(job, dict) else None,
                    "default": False,
                }
            )
        if rows:
            rows[0]["default"] = True
        return rows

    def _snapshot(self) -> list[Member]:
        with self._lock:
            return list(self._members.values())

    # --- add and remove -----------------------------------------------------------
    def next_id(self) -> str:
        """The lowest id not in use: a gap a departed bot left is filled first."""
        with self._lock:
            taken = set(self._members) | self._pending
            number = 1
            while bot_id_for(number) in taken:
                number += 1
            return bot_id_for(number)

    def add(self, bot_id: str | None = None) -> dict:
        """Put a bot in (the next free one by default); one already here stays as is."""
        with self._lock:
            if self._stopping:
                raise HostError("The fleet is stopping")
            if bot_id is not None and bot_id in self._members:
                return self._described(self._members[bot_id])
            if len(self._members) + len(self._pending) >= self.limit:
                raise HostError(f"A server holds at most {self.limit} bots")
            if bot_id is None:
                bot_id = self.next_id()
            elif bot_id in self._pending:
                raise HostError(f"{bot_id} is being added")
            self._pending.add(bot_id)
        try:
            member = self._factory(bot_id)
        except BaseException:
            with self._lock:
                self._pending.discard(bot_id)
            raise
        with self._lock:
            self._pending.discard(bot_id)
            if member is not None and not self._stopping:
                self._members[bot_id] = member
                return self._described(member)
        if member is not None:  # the fleet began stopping while this one was built
            member.close()
        raise HostError("The fleet is stopping")

    def remove(self, bot_id: str) -> dict:
        with self._lock:
            if bot_id not in self._members:
                here = ", ".join(self._members) or "none"
                raise HostError(f"No bot {bot_id}; here: {here}")
            member = self._members.pop(bot_id)
        member.close()
        return self._described(member)

    @staticmethod
    def _described(member: Member) -> dict:
        return {"id": member.id, "name": member.name}

    def shutdown(self) -> None:
        """Stop every bot, in parallel (each halts and despawns its body)."""
        with self._lock:
            self._stopping = True
            members, self._members = list(self._members.values()), {}
        if not members:
            return
        with ThreadPoolExecutor(max_workers=len(members)) as pool:
            list(pool.map(Member.close, members))

    # --- the protocol, routed ------------------------------------------------------
    def call(self, intent, args, actor, *, key=None, bot=None) -> dict:
        if bot == ALL:
            if intent != "stop":
                return {"ok": False, "error": f'Only stop may name every bot ("{ALL}")'}
            return self._stop_all(args, actor)
        try:
            member = self.member(bot)
        except HostError as error:
            return {"ok": False, "error": str(error)}
        return member.host.call(intent, args, actor, key=key)

    def _stop_all(self, args, actor) -> dict:
        members = self._snapshot()
        if not members:
            return {"ok": False, "error": "No bot is running; add one first"}
        with ThreadPoolExecutor(max_workers=len(members)) as pool:
            replies = list(
                pool.map(lambda m: m.host.call("stop", args, actor), members)
            )
        for reply in replies:
            if not reply.get("ok"):
                return reply
        results = {m.id: r["result"] for m, r in zip(members, replies, strict=True)}
        return {
            "ok": True,
            "result": {
                "stopped": [r["stopped"] for r in results.values() if r["stopped"]],
                "cancelled": [j for r in results.values() for j in r["cancelled"]],
                "bots": results,
            },
        }

    def status(self, bot=None) -> dict:
        return self.member(bot).host.status()

    def events(self, after: int = 0, wait: float = 0.0, *, bot=None) -> dict:
        return self.member(bot).host.events(after, wait)

    def wait_job(self, job_id: str, timeout: float, *, bot=None) -> dict | None:
        """The job from the named bot, or from whichever bot has it."""
        if bot is not None:
            return self.member(bot).host.wait_job(job_id, timeout)
        for member in self._snapshot():
            if member.host.knows(job_id):
                return member.host.wait_job(job_id, timeout)
        return None

    def trial(self, spec, *, bot=None) -> dict:
        try:
            return self.member(bot).host.trial(spec)
        except HostError as error:
            return {"ok": False, "error": str(error)}

    # --- chat ----------------------------------------------------------------------
    def hear(self, actor, text) -> None:
        """A line of chat: every bot hears it, and the bots it names answer; a line
        naming nobody is the first bot's to answer. (A stop line stops them all.)"""
        from mclab.companion_talker import mentions

        members = self._snapshot()
        named = {m.id for m in members if mentions(text, m.name)}
        first = members[0].id if members else None
        for member in members:
            talker = member.talker
            if talker is None:
                continue
            engaged = member.id in named if named else member.id == first
            try:
                talker.hear(actor, text, engaged=engaged)
            except Exception:  # noqa: BLE001 - one bot's failure is not the others'
                log.exception("%s could not hear a line", member.id)


__all__ = [
    "ALL",
    "Fleet",
    "MAX_BOTS",
    "Member",
    "bot_id_for",
    "bot_name_for",
]
