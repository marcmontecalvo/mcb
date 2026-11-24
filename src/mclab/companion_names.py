"""Which in-game names are companion bots, and the fleet id behind each.

``default`` is MCBot (``bots/default``); ``mcbot<N>`` is MCBot<N> (``N`` from 2 to
99; its profile is derived from ``bots/default`` unless ``bots/mcbot<N>`` exists).
Pure naming, no host: the dashboard, the arena scripts and the fleet all read it.
Docs: ARENA_RUNTIME.md, "Several bots".
"""

from __future__ import annotations

import re

DEFAULT_ID = "default"
DEFAULT_NAME = "MCBot"
BOT_ID = re.compile(r"mcbot([2-9]|[1-9][0-9])")


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
