"""In-game viewing aid for a running session (mcb-lab NaturalRun draws it, every
client sees it): the planned route as red dust (native_driver.publish_path), a green
ring and short beam at the stance the body is heading for, and a yellow outline round
the block it is working on. Switched on and off while the session runs; a long build
is watched for minutes at a time, so it is a switch, not a launch option only."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

Pos = tuple[int, int, int]
_KEEP = object()


def file_switch(path: Path | str) -> Callable[[], bool]:
    """On while ``path`` exists (run-build: ``<run folder>/MARKERS``)."""
    flag = Path(path)
    return flag.exists


def run_switch(folder: Path | str, *, on: bool) -> Callable[[], bool]:
    """The switch of a run folder: ``<folder>/MARKERS``, created when the run starts
    with its markers on; create or delete the file to flip them while it runs."""
    flag = Path(folder) / "MARKERS"
    if on:
        flag.touch()
    return file_switch(flag)


class Markers:
    """Sends ``natural_markers`` when the switch flips and when the aim changes;
    nothing at all while off. ``client.show_path`` follows the switch, so the route
    is published only while the markers show."""

    def __init__(self, client, switch: Callable[[], bool]):
        self.client, self.switch = client, switch
        self.on = False
        self.goal: Pos | None = None
        self.target: Pos | None = None
        self._sent: dict = {}

    def aim(self, goal=_KEEP, target=_KEEP) -> None:
        """Point the markers at ``goal`` (the stance) and ``target`` (the block);
        an argument left out keeps its value, None clears it."""
        if goal is not _KEEP:
            self.goal = None if goal is None else tuple(goal)
        if target is not _KEEP:
            self.target = None if target is None else tuple(target)
        on = bool(self.switch())
        if on != self.on:
            self.on = on
            self.client.show_path = on
            self.client.send("natural_markers", show=on)
        if not on:
            return
        # What the lab holds: absent from _sent = nothing yet (the lab's own default).
        # Hidden markers keep their goal and target in the lab, so _sent outlives a
        # switch off and on: what changed meanwhile, a clear included, goes now.
        changed = {
            key: value
            for key, value in (("goal", self.goal), ("target", self.target))
            if self._sent.get(key) != value
        }
        if changed:
            self.client.send(
                "natural_markers",
                **{k: None if v is None else list(v) for k, v in changed.items()},
            )
            self._sent.update(changed)
