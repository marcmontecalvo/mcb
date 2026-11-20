"""No-progress watchdog for the build loops (stall = >5 s of wall clock with no
movement and no successful edit while the body is working).

``WatchedBody`` wraps a ``BuildBody`` and times every working op (stance, walk, place,
break, use). Progress is a successful op or the feet moving :data:`MOVE_BLOCKS` (a
failed walk counts only if it ended nearer its goal, within the stall window). When an
op ends and :attr:`ProgressWatch.limit` seconds have passed without progress, the stall
is logged and recorded, the body's optional ``escape_hole`` is tried (a trapped body is
the usual cause), and after :attr:`max_stalls` stalls in a row with no progress between
them :class:`StallError` stops the work with an actionable reason. Bounded waits
(``wait_ticks``, ``collect_drops``) are not working ops and are never counted.
"""

from __future__ import annotations

import contextlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

STALL_SECONDS = 5.0
DEAD_SECONDS = 30.0  # one pause this long ends the run
MAX_STALLS = 3
SPOT_STALLS = 4  # each stall is 40-100 s of failed tries (run 20261110T115340557226Z)
MOVE_BLOCKS = 0.75
_WALKS = ("goto_stance", "walk_to")
_OPS = ("goto_stance", "walk_to", "place_block", "break_block", "use_block")


class StallError(RuntimeError):
    """Work made no progress through repeated recovery; the message says where."""


class BodyDied(RuntimeError):
    """The body is dead: its observation carries no position."""


def _json_pos(pos):
    if pos is None:
        return None
    if isinstance(pos, (list, tuple)):
        return [round(v, 2) if isinstance(v, float) else v for v in pos]
    return pos


def _got_nearer(target, before, after) -> bool:
    """The feet ended at least MOVE_BLOCKS closer to ``target`` than they started."""
    try:
        goal = tuple(target)
        if len(goal) != len(before):
            return False
        return math.dist(before, goal) - math.dist(after, goal) >= MOVE_BLOCKS
    except (TypeError, ValueError):
        return False


@dataclass
class ProgressWatch:
    limit: float = STALL_SECONDS
    max_stalls: int = MAX_STALLS
    clock: Callable[[], float] = time.monotonic
    log: Callable[[str], None] = lambda m: print(m, flush=True)
    events: list[dict] = field(default_factory=list)  # every stall and its recovery
    long_stops: list[dict] = field(default_factory=list)  # stalls and >limit ops
    _last: float | None = None
    _at: tuple | None = None
    _streak: int = 0
    _key: tuple | None = None
    _spot: tuple | None = None  # feet cell of the last stall
    _spot_n: int = 0  # stalls in a row there, whatever the target

    def progress(self, at) -> None:
        self._last, self._at, self._streak, self._key = self.clock(), tuple(at), 0, None
        self._spot_n = 0

    def reset(self) -> None:
        """Forget the streak (the caller gave up on the stalled operation)."""
        self._streak, self._key, self._last = 0, None, self.clock()

    def check(
        self,
        body,
        op: str,
        target,
        ok: bool,
        *,
        elapsed: float | None = None,
        start_pos: tuple | None = None,
    ) -> None:
        """Called after each working op: record progress or handle a stall."""
        now, here = self.clock(), tuple(body.position())
        if self._last is None:
            # the clock starts when the first op did, not when it ended
            self._last = now - (elapsed or 0.0)
            self._at = tuple(start_pos) if start_pos is not None else here
        moved = any(
            abs(a - b) >= MOVE_BLOCKS for a, b in zip(here, self._at, strict=True)
        )
        feet_moved = moved
        if not ok and op in _WALKS:
            # A failed walk is progress only when it got nearer its goal, and not
            # when it took longer than the stall window to do so: moving the feet
            # is not the objective (run 20261119T133618892664Z: a 31.7 s failed
            # goto_stance across the yard logged as a recovered slow_op).
            began = start_pos if start_pos is not None else self._at
            moved = (
                _got_nearer(target, began, here)
                and (elapsed is None or elapsed <= self.limit)
            )
        if ok or moved:
            if elapsed is not None and elapsed > self.limit:
                self.long_stops.append(
                    {
                        "kind": "slow_op",
                        "op": op,
                        "target": _json_pos(target),
                        "at": _json_pos(start_pos if start_pos is not None else here),
                        "end": _json_pos(here),
                        "duration_s": round(elapsed, 1),
                        "ok": ok,
                    }
                )
            return self.progress(here)
        idle = now - self._last
        if idle <= self.limit:
            return
        ended = getattr(body, "episode_ended", None)
        if ended is not None and ended():
            raise StallError(
                f"episode ended: the lab episode's tick limit was reached at {here} "
                f"during {op} {target}"
            )
        key = (op, tuple(target) if target is not None else None)
        # consecutive means the same operation on the same target: different
        # unreachable targets are bounded failures, not one retry loop
        self._streak = self._streak + 1 if key == self._key else 1
        self._key = key
        # a different target each time from the same spot is still one stuck body
        # (run 20261110T075452397123Z: 174 stalls at one cell, never the same target)
        spot = tuple(int(v // 1) for v in here)
        # only failed walks count: a refused break is a bounded skip, not a trap
        walk = op in ("goto_stance", "walk_to")
        self._spot_n = (self._spot_n + walk) if spot == self._spot else int(walk)
        self._spot = spot
        event = {"op": op, "target": target, "at": here, "idle_s": round(idle, 1)}
        escape = getattr(body, "escape_hole", None)
        recovered = False
        event["recovery"] = "not_tried"
        if escape is not None and target is not None and self._streak < self.max_stalls:
            recovered = bool(escape(tuple(target)))
            event["recovery"] = "succeeded" if recovered else "failed"
        self.events.append(event)
        self.long_stops.append(
            {
                "kind": "stall",
                "op": op,
                "target": _json_pos(target),
                "at": _json_pos(here),
                "duration_s": round(idle, 1),
                "recovery": event["recovery"],
            }
        )
        self.log(f"STALL {idle:.1f}s no progress: {event}")
        self._last = self.clock()
        if recovered:
            self.progress(body.position())
            return
        if idle >= DEAD_SECONDS and not feet_moved:  # a long walk is not standing still
            # Stop for analysis instead of carrying on: a body that stood still
            # this long is useless in play, and what follows is more of the same.
            raise StallError(
                f"dead: {idle:.0f}s without movement or a successful edit at {here} "
                f"during {op} {target}"
            )
        if self._spot_n >= SPOT_STALLS:
            raise StallError(
                f"trapped: {self._spot_n} stalls in a row at {here} with no movement "
                f"and no successful edit (last: {op} {target})"
            )
        if self._streak >= self.max_stalls:
            raise StallError(
                f"no progress for {self._streak} consecutive {self.limit:.0f}s windows "
                f"at {here} during {op} {target}"
            )


class WatchedBody:
    """Delegates everything to ``body``; working ops are reported to the watch."""

    def __init__(self, body, watch: ProgressWatch):
        object.__setattr__(self, "_body", body)
        object.__setattr__(self, "_watch", watch)

    def __getattr__(self, name):
        attr = getattr(self._body, name)
        if name not in _OPS:
            return attr
        watch, body = self._watch, self._body

        def run(target, *args, **kw):
            started = watch.clock()
            start_pos = tuple(body.position())
            result = attr(target, *args, **kw)
            elapsed = watch.clock() - started
            ok = bool(getattr(result, "ok", result))
            watch.check(body, name, target, ok, elapsed=elapsed, start_pos=start_pos)
            return result

        return run

    def __setattr__(self, name, value):  # precise_stance, required_support, ...
        setattr(self._body, name, value)


def walking_with_edits(body, **options):
    """Walk the way a player does (pillar, bridge, place dirt); bodies without it
    use no_placing. Blocks it places are in ``body.stray_footholds`` for cleanup."""
    make = getattr(body, "walking_with_edits", None)
    return make(**options) if make else no_placing(body, **options)


def no_placing(body, **options):
    """The body's no-placing movement context, or a no-op for bodies without one."""
    make = getattr(body, "no_placing", None)
    return make(**options) if make else contextlib.nullcontext()
