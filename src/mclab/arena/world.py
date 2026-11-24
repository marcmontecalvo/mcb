"""The arena server's world: a void with a floating lobby at spawn and arenas in slots beside it.

Players join the arena server in the lobby (the world spawn), so nobody needs to be an
operator to reach a fight: a match sends its players from wherever they stand to the
arena's pads and back to the spawn when it ends (the start's ``exit``).  Each arena
keeps the slot it was first built in; a new one takes the first slot nothing overlaps.
See docs/ARENA_RUNTIME.md ("The arena server").
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from mclab.arena import client as arena
from mclab.arena import designs
from mclab.arena import plan as ap

log = logging.getLogger(__name__)

LOBBY = "lobby"
LOBBY_ORIGIN: ap.Cell = (
    -12,
    100,
    -12,
)  # its entry, the world spawn, lands on (0, 102, 0)
SLOT_STRIDE = ap.MAX_FOOTPRINT + 64
BUILD_TIMEOUT = 900.0


def slot_origin(index: int) -> ap.Cell:
    """The min corner of arena slot ``index``: east of the lobby, one stride apart."""
    x, y, z = LOBBY_ORIGIN
    return (x + SLOT_STRIDE * (index + 1), y, z)


def lobby_spawn(lobby: ap.Plan) -> ap.Cell:
    (team,) = lobby.teams.values()
    return tuple(o + e for o, e in zip(LOBBY_ORIGIN, team.entry, strict=True))


def _box(origin, size) -> tuple[ap.Cell, ap.Cell]:
    lo = tuple(int(v) for v in origin)
    return lo, tuple(a + int(s) - 1 for a, s in zip(lo, size, strict=True))


def _overlaps(a, b) -> bool:
    return all(a[0][i] <= b[1][i] and b[0][i] <= a[1][i] for i in range(3))


def origin_for(name: str, listed: list[dict]) -> ap.Cell:
    """Where arena ``name`` stands: its registered origin, else the first free slot."""
    for entry in listed:
        if entry["name"] == name:
            return tuple(entry["origin"])
    taken = [_box(e["origin"], e["size"]) for e in listed]
    span = (ap.MAX_FOOTPRINT, ap.MAX_HEIGHT, ap.MAX_FOOTPRINT)
    index = 0
    while any(_overlaps(_box(slot_origin(index), span), box) for box in taken):
        index += 1
    return slot_origin(index)


def _ensure(
    client,
    listed: dict[str, dict],
    plan: Callable[[], dict],
    name: str,
    origin: ap.Cell,
    echo: Callable[[str], None],
    timeout: float,
) -> bool:
    """Build ``name`` at ``origin`` unless it stands there already; True if it was built now."""
    state = listed.get(name, {}).get("state")
    if state == "built":
        return False
    if state == "clearing":
        raise arena.ArenaError(
            "arena_busy", f"{name} is being cleared; build it again once it is gone"
        )
    if state == "incomplete":  # the server stopped mid-build: start it over in place
        echo(f"{name}: the last build was cut short; building it again")
        client.remove(name)
        state = None
    if state is None:
        echo(f"building {name} at {list(origin)}")
        client.build(plan(), origin, name=name)
    else:  # building
        echo(f"waiting for {name} to finish building")

    def progress(event):
        percent = event["data"].get("percent")
        if percent is not None:
            echo(f"{name}: {percent}%")

    event = client.wait_for(
        name,
        {"arena_built", "arena_build_aborted"},
        timeout=timeout,
        on_event=progress,
    )
    if event["type"] == "arena_build_aborted":
        reason = str(event["data"].get("reason", "aborted"))
        raise arena.ArenaError(reason, f"building {name} stopped: {reason}")
    return True


def _lobby(client, listed, echo, timeout) -> dict:
    lobby = designs.lobby()
    built = _ensure(
        client, listed, lambda: ap.to_dict(lobby), LOBBY, LOBBY_ORIGIN, echo, timeout
    )
    spawn = lobby_spawn(lobby)
    client.spawn(spawn)
    return {"spawn": spawn, "built": built, "size": lobby.size}


def ensure_lobby(
    client, *, echo: Callable[[str], None], timeout: float = BUILD_TIMEOUT
) -> dict:
    """Build the lobby unless it stands, and make its entry the world spawn."""
    listed = {entry["name"]: entry for entry in client.list()["arenas"]}
    lobby = _lobby(client, listed, echo, timeout)
    return {"spawn": lobby["spawn"], "built": lobby["built"]}


def lobby_world_spawn(*, client_factory: Callable[[], object] | None = None) -> ap.Cell:
    """The arena server's world spawn for the bot: the lobby, built first.

    The bot joins as soon as the server is up, long before any match; under the
    level's spawn there is only void (it fell out of the world at every retry).
    Raises when the lobby cannot be made, so the host retries the session."""
    factory = client_factory or arena.ArenaClient.from_state
    return ensure_lobby(factory(), echo=log.info)["spawn"]


def prepare(
    client,
    name: str,
    *,
    load_plan: Callable[[str], dict] = arena.load_plan,
    echo: Callable[[str], None],
    timeout: float = BUILD_TIMEOUT,
) -> dict:
    """Make the lobby, the spawn and arena ``name`` ready for a match."""
    if name == LOBBY:
        raise arena.ArenaError(
            "bad_request", "the lobby is where players wait, not an arena"
        )
    listed = {entry["name"]: entry for entry in client.list()["arenas"]}
    lobby = _lobby(client, listed, echo, timeout)
    built = [LOBBY] if lobby["built"] else []
    spawn = lobby["spawn"]
    origin = origin_for(
        name,
        [e for e in listed.values() if e["name"] != LOBBY]
        + [{"name": LOBBY, "origin": LOBBY_ORIGIN, "size": lobby["size"]}],
    )
    if _ensure(client, listed, lambda: load_plan(name), name, origin, echo, timeout):
        built.append(name)
    return {"arena": name, "origin": origin, "spawn": spawn, "built": built}
