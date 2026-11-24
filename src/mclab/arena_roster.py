"""The saved arena roster: player names the arena wizard offers, so nobody has to
retype them.

One small JSON file, ``runtime/arena-roster.json`` (``runtime/`` is git-ignored, so
it outlives dashboard and server restarts and code updates)::

    {"version": 1,
     "players": [{"name": "Dad", "label": "Dad", "pinned": true,
                  "uses": 3, "last_used": "2026-11-24T12:00:00Z"}],
     "last_match": {"at": "...", "arena": "crash_site", "game": "teams",
                    "waves": "mixed-10", "custom_waves": null,
                    "roster": [{"name": "Dad", "team": "red", "op": true}]}}

Names only: no addresses, no UUIDs, nothing looked up from outside. A name is a
Minecraft name (``[A-Za-z0-9_]{1,16}``); the same name in another case is the same
person, and the spelling already saved is kept. A write goes to a temporary file in
the same folder and is swapped in with ``os.replace``, so a crash leaves the old file
whole. A file that cannot be read is moved aside to ``<file>.corrupt`` and the
roster starts again; bad entries inside a readable file are dropped one by one.

Threads in one process are serialized; two processes saving in the same instant
could lose one of the two changes (the dashboard and the match worker both save, a
few times a day).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mclab.companion_names import BotNames
from mclab.paths import ROOT

ROSTER_FILE = ROOT / "runtime/arena-roster.json"
VERSION = 1
NAME = re.compile(r"[A-Za-z0-9_]{1,16}")
MAX_LABEL = 32
MAX_SAVED = 200  # names kept; the oldest plain ones go first
_LOCK = threading.Lock()  # one per process: every store shares the one file


def valid_name(name: object) -> str:
    """``name`` if it is a Minecraft name, else a ValueError saying so."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(
            "That is not a Minecraft name (letters, digits and _, up to 16)"
        )
    return name


def _label(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("A label is text")
    value = value.strip()
    if len(value) > MAX_LABEL or not value.isprintable():
        raise ValueError(f"A label is up to {MAX_LABEL} plain characters")
    return value


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry(raw: object) -> dict | None:
    """One saved player from a file, repaired, or None when it is not one."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return None
    try:
        label = _label(raw.get("label", ""))
    except ValueError:
        label = ""
    uses = raw.get("uses")
    used = raw.get("last_used")
    return {
        "name": name,
        "label": label,
        "pinned": raw.get("pinned") is True,
        "uses": uses if isinstance(uses, int) and uses > 0 else 0,
        "last_used": used if isinstance(used, str) else None,
    }


def _slot(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name, team = raw.get("name"), raw.get("team")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return None
    return {
        "name": name,
        "team": team if isinstance(team, str) else None,
        "op": raw.get("op") is True,
    }


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _last_match(raw: object) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("roster"), list):
        return None
    roster = [s for s in map(_slot, raw["roster"]) if s]
    if not roster:
        return None
    return {
        "at": _text_or_none(raw.get("at")),
        "arena": _text_or_none(raw.get("arena")),
        "game": _text_or_none(raw.get("game")),
        "waves": _text_or_none(raw.get("waves")),
        "custom_waves": _text_or_none(raw.get("custom_waves")),
        "roster": roster,
    }


def _ordered(players: list[dict]) -> list[dict]:
    """Pinned first, then the most recently used, then by name (stable sorts, last
    key first)."""
    rows = sorted(players, key=lambda p: p["name"].lower())
    rows.sort(key=lambda p: p["last_used"] or "", reverse=True)
    rows.sort(key=lambda p: not p["pinned"])
    return rows


class RosterStore:
    """The saved roster file. Every call reads the file afresh, so a second store (or
    another process) on the same path sees the latest save."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.path = Path(path) if path is not None else ROSTER_FILE
        self._clock = clock

    # -- reading
    def _read(self) -> tuple[list[dict], dict | None]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return [], None
        except UnicodeDecodeError:
            return self._set_aside()
        try:
            data = json.loads(text)
        except ValueError:
            return self._set_aside()
        if not isinstance(data, dict) or not isinstance(data.get("players"), list):
            return self._set_aside()
        players: dict[str, dict] = {}
        for raw in data["players"]:
            entry = _entry(raw)
            if entry and entry["name"].lower() not in players:
                players[entry["name"].lower()] = entry
        return list(players.values()), _last_match(data.get("last_match"))

    def _set_aside(self) -> tuple[list[dict], None]:
        """Keep an unreadable file as ``<file>.corrupt`` and start an empty roster."""
        with contextlib.suppress(OSError):
            os.replace(self.path, self.path.with_name(self.path.name + ".corrupt"))
        return [], None

    def players(self) -> list[dict]:
        """The saved names: pinned first, then the most recently used, then by name."""
        return _ordered(self._read()[0])

    def last_match(self) -> dict | None:
        """The setup of the last match that started, or None."""
        return self._read()[1]

    # -- writing
    def _write(self, players: list[dict], last: dict | None) -> None:
        if len(players) > MAX_SAVED:
            plain = sorted(
                (p for p in players if not p["pinned"] and not p["label"]),
                key=lambda p: p["last_used"] or "",
            )
            drop = {id(p) for p in plain[: len(players) - MAX_SAVED]}
            players = [p for p in players if id(p) not in drop]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            prefix=".roster-", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(
                    {"version": VERSION, "players": players, "last_match": last},
                    stream,
                    indent=2,
                    allow_nan=False,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def upsert(
        self,
        name: object,
        *,
        label: object = None,
        pinned: object = None,
    ) -> dict:
        """Save ``name`` (a new one, or change one already saved). ``label`` and
        ``pinned`` left as None are not changed; an empty label clears it. The
        spelling already saved is kept. Returns the saved entry."""
        valid_name(name)
        label = None if label is None else _label(label)
        if pinned is not None and not isinstance(pinned, bool):
            raise ValueError("Pinned is true or false")
        with _LOCK:
            players, last = self._read()
            entry = next(
                (p for p in players if p["name"].lower() == name.lower()), None
            )
            if entry is None:
                entry = _entry({"name": name})
                players.append(entry)
            if label is not None:
                entry["label"] = label
            if pinned is not None:
                entry["pinned"] = pinned
            self._write(players, last)
        return dict(entry)

    def remove(self, name: object) -> bool:
        """Forget ``name``; whether it was saved."""
        valid_name(name)
        with _LOCK:
            players, last = self._read()
            kept = [p for p in players if p["name"].lower() != name.lower()]
            if len(kept) == len(players):
                return False
            self._write(kept, last)
        return True

    def record_match(
        self,
        roster: list,
        *,
        arena: str | None = None,
        game: str | None = None,
        waves: str | None = None,
        custom_waves: str | None = None,
        bots: BotNames | None = None,
    ) -> None:
        """A match is starting: save every name in it but the bots (MCBot, MCBot2 ...
        and the names ``bots`` renamed them to: companion_names), count the use, and
        keep the setup as the last match. Entries that are not a player with a
        Minecraft name are skipped; an empty match changes nothing."""
        slots = [s for s in map(_slot, roster) if s]
        if not slots:
            return
        bots = bots or BotNames()
        with _LOCK:
            players, _ = self._read()
            now = _stamp(self._clock())
            by_name = {p["name"].lower(): p for p in players}
            counted = set()
            for one in slots:
                key = one["name"].lower()
                if key in counted or bots.is_bot(key):
                    continue
                counted.add(key)
                entry = by_name.get(key)
                if entry is None:
                    entry = by_name[key] = _entry({"name": one["name"]})
                    players.append(entry)
                entry["name"] = one["name"]  # the spelling the match worked with
                entry["uses"] += 1
                entry["last_used"] = now
            last = {
                "at": now,
                "arena": arena,
                "game": game,
                "waves": waves,
                "custom_waves": custom_waves,
                "roster": slots,
            }
            self._write(players, last)
