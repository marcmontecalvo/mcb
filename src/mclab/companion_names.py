"""Which in-game names are companion bots, and the fleet id behind each.

``default`` is MCBot (``bots/default``); ``mcbot<N>`` is MCBot<N> (``N`` from 2 to
99; its profile is derived from ``bots/default`` unless ``bots/mcbot<N>`` exists).
Any bot can be renamed (``BotNames``, the instance's ``bot-names.json``); its id and
everything kept under it (profile, saved state, memory) stay. No host here: the
dashboard, the arena scripts and the fleet all read it.
Docs: ARENA_RUNTIME.md, "Several bots".
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_ID = "default"
DEFAULT_NAME = "MCBot"
BOT_ID = re.compile(r"mcbot([2-9]|[1-9][0-9])")
PLAYER_NAME = re.compile(r"[A-Za-z0-9_]{3,16}")  # what a bot's profile may be called
NAMES_FILE = "bot-names.json"


def bot_id_for(number: int) -> str:
    """The id of the ``number``-th bot (1 is the default one)."""
    return DEFAULT_ID if number == 1 else f"mcbot{number}"


def bot_name_for(bot_id: str) -> str:
    """The in-game name that goes with a derived bot id."""
    if bot_id == DEFAULT_ID:
        return DEFAULT_NAME
    found = BOT_ID.fullmatch(bot_id)
    if found is None:
        raise ValueError(f"bot id {bot_id!r} is not 'default' or 'mcbot<N>'")
    return f"{DEFAULT_NAME}{found.group(1)}"


def bot_id_for_name(name: str) -> str | None:
    """The derived id of an in-game name (``MCBot`` and ``MCBot<N>``, any case), or
    None for any other name (a human, or a bot whose profile names it otherwise)."""
    base = DEFAULT_NAME.lower()
    lowered = name.lower()
    if lowered == base:
        return DEFAULT_ID
    candidate = "mcbot" + lowered[len(base) :] if lowered.startswith(base) else ""
    return candidate if BOT_ID.fullmatch(candidate) else None


def is_bot_name(name: str) -> bool:
    """Whether the name is one of the derived bot names (MCBot, MCBot2, ...)."""
    return bot_id_for_name(name) is not None


def _check_id(bot_id: str) -> str:
    bot_name_for(bot_id)  # raises for an id that is no slot
    return bot_id


def _check_name(name: object) -> str:
    if not isinstance(name, str) or not PLAYER_NAME.fullmatch(name):
        raise ValueError(
            f"a bot's name must be 3-16 letters, digits or _, not {name!r}"
        )
    return name


class BotNames:
    """The fleet's names on one server: each slot's derived name (MCBot, MCBot2 ...)
    unless the operator renamed it. Kept in ``bot-names.json`` in the instance's
    runtime folder; the host spawns a bot under it, and the dashboard and the arena
    scripts tell a bot from a person by it. A slot's derived name still means that
    bot after a rename (a roster saved before it never waits for a human MCBot2).
    Immutable: ``rename`` returns a new book."""

    def __init__(self, renamed: dict[str, str] | None = None):
        self._renamed = {
            _check_id(bot_id): _check_name(name)
            for bot_id, name in (renamed or {}).items()
        }
        self._ids = {name.lower(): bot_id for bot_id, name in self._renamed.items()}
        if len(self._ids) != len(self._renamed):
            raise ValueError(f"two bots share a name: {self._renamed}")

    @property
    def renamed(self) -> dict[str, str]:
        """The renamed slots, id -> name (the others keep their derived name)."""
        return dict(self._renamed)

    def name_for(self, bot_id: str) -> str:
        return self._renamed.get(bot_id) or bot_name_for(bot_id)

    def id_for(self, name: str) -> str | None:
        """The slot a name means: a renamed bot's name, or a derived one; else None."""
        return self._ids.get(name.lower()) or bot_id_for_name(name)

    def is_bot(self, name: str) -> bool:
        return self.id_for(name) is not None

    def rename(self, bot_id: str, name: str) -> BotNames:
        """A book with ``bot_id`` called ``name``; its own derived name drops the
        entry. A name another bot answers to is a ValueError."""
        _check_id(bot_id)
        _check_name(name)
        holder = self.id_for(name)
        if holder not in (None, bot_id):
            raise ValueError(f"{name} is already another bot's name")
        renamed = {i: n for i, n in self._renamed.items() if i != bot_id}
        if name.lower() != bot_name_for(bot_id).lower():
            renamed[bot_id] = name
        return BotNames(renamed)

    @classmethod
    def load(cls, runtime: Path) -> BotNames:
        """The book in ``runtime`` (none there: nobody was renamed)."""
        path = Path(runtime) / NAMES_FILE
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as error:
            raise ValueError(f"{path}: {error}") from None
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected an object of id -> name")
        return cls(value)

    def save(self, runtime: Path) -> None:
        path = Path(runtime) / NAMES_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(self._renamed, indent=1, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, path)
