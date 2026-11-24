"""Pursuit: can the bot close on its target, and which is it not reaching (pure).

The attack job looks at its target between fight steps. A player is a target that
runs: one who moves as fast as the bot, or the bot slowed by a splash of slowness,
keeps a gap no melee fight closes. The record of the gap across the looks says so,
and the weapon choice (``companion_combat.ordered_tactic``) then turns to the bow or
a Loyalty trident's throw instead of a chase that cannot end. It is measured, not
assumed: a foe walking in, or standing still, is closed on and fought in melee.
Live series 20261124T164259Z (docs/ARENA_RUNTIME.md, "Team games").
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from mclab.companion_consumables import SLOWNESS, SPEED, Effect

TICKS_PER_S = 20
SPRINT_SPEED = 5.612  # blocks a second: a sprinting player on level ground
SPEED_PER_LEVEL = 0.20  # the speed effect's share of the base speed, per level
SLOWNESS_PER_LEVEL = 0.15  # slowness's, taken off per level (both multiply)

REACH_BAND = 4.0  # a target this near is in melee range (reach 3 and a step): no kiter
WINDOW_TICKS = 100  # the looks that count: the last five seconds
MIN_SPAN_TICKS = 30  # the span a closing rate is read over, at least (1.5 s)
EARLY_SPAN_TICKS = 10  # ...when the bot is slower than the foe, which needs no proof
MIN_CLOSING = 0.8  # blocks a second gained: less is a gap that holds, a kiter
RESUME_CLOSING = 1.6  # ...and the foe is closed on again from this (a band: no flip)
BLOCK_TICKS = 200  # a target the bot could not get at is left alone for ten seconds


def own_speed(effects: Iterable[Mapping] | None) -> float:
    """The bot's sprint speed under its effects (the body's ``effects`` rows): speed
    adds 20% a level and slowness takes 15% a level, the two multiplied (the game's
    movement-speed modifiers), never below standing still."""
    factor = 1.0
    for row in effects or ():
        effect = Effect.read(row)
        if effect is None:
            continue
        levels = effect.amplifier + 1
        if effect.id == SPEED:
            factor *= 1 + SPEED_PER_LEVEL * levels
        elif effect.id == SLOWNESS:
            factor *= max(0.0, 1 - SLOWNESS_PER_LEVEL * levels)
    return SPRINT_SPEED * factor


@dataclass(frozen=True)
class Look:
    tick: int
    gap: float
    foe_at: tuple[float, float, float]


class Pursuit:
    """The gap to one target across the looks of an attack job, and the targets the
    bot has found out of its reach for now."""

    def __init__(self) -> None:
        self.uuid: str | None = None
        self.looks: deque[Look] = deque()
        self.ranged = False  # the standing verdict: the foe cannot be closed on
        self._blocked: dict[str, int] = {}  # uuid -> the tick the block ends

    # --- the history ---------------------------------------------------------------

    def observe(self, uuid: str, tick: int, bot_at, foe_at) -> None:
        """Record a look at ``uuid``: where the bot and the foe stand at ``tick``. A
        look at another target starts a fresh history; one no later than the last
        adds nothing."""
        if uuid != self.uuid:
            self.uuid, self.ranged = uuid, False
            self.looks.clear()
        if self.looks and tick <= self.looks[-1].tick:
            return
        foe = tuple(float(v) for v in foe_at)
        self.looks.append(Look(tick, math.dist(bot_at, foe), foe))
        while tick - self.looks[0].tick > WINDOW_TICKS:
            self.looks.popleft()

    def span(self) -> int:
        """Ticks from the oldest look kept to the newest."""
        return self.looks[-1].tick - self.looks[0].tick if self.looks else 0

    def _rate(self, least: int) -> float | None:
        if self.span() < max(1, least):
            return None
        first, last = self.looks[0], self.looks[-1]
        return (first.gap - last.gap) * TICKS_PER_S / (last.tick - first.tick)

    def closing_rate(self) -> float | None:
        """Blocks a second the gap shrank over the window (negative: it grew), or
        None before the looks span MIN_SPAN_TICKS."""
        return self._rate(MIN_SPAN_TICKS)

    def foe_speed(self) -> float | None:
        """Blocks a second the foe itself moved over the window (None: too short)."""
        if self.span() < EARLY_SPAN_TICKS:
            return None
        first, last = self.looks[0], self.looks[-1]
        moved = math.dist(first.foe_at, last.foe_at)
        return moved * TICKS_PER_S / (last.tick - first.tick)

    # --- the verdict ---------------------------------------------------------------

    def cannot_close(self, speed: float) -> bool:
        """Whether the foe keeps out of melee: the gap is wider than the reach and is
        not shrinking. ``speed`` is the bot's own (``own_speed``): a bot slower than
        the foe's measured pace needs only a moment's look to know it; otherwise the
        gap must hold over MIN_SPAN_TICKS. Once decided it stays decided until the
        gap closes at RESUME_CLOSING, so the weapon does not change every look."""
        if not self.looks or self.looks[-1].gap <= REACH_BAND:
            self.ranged = False
            return False
        pace = self.foe_speed()
        slower = pace is not None and speed < pace
        rate = self._rate(EARLY_SPAN_TICKS if slower else MIN_SPAN_TICKS)
        if rate is None:
            return self.ranged
        self.ranged = rate < (RESUME_CLOSING if self.ranged else MIN_CLOSING)
        return self.ranged

    # --- targets out of reach ------------------------------------------------------

    def block(self, uuid: str, tick: int) -> None:
        """Leave ``uuid`` alone for BLOCK_TICKS: the bot found no way to it."""
        self._blocked[uuid] = tick + BLOCK_TICKS

    def clear_block(self, uuid: str) -> None:
        self._blocked.pop(uuid, None)

    def blocked(self, tick: int) -> frozenset[str]:
        """The targets still left alone at ``tick``."""
        self._blocked = {u: end for u, end in self._blocked.items() if end > tick}
        return frozenset(self._blocked)
