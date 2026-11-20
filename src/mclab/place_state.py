"""Does a click on one face, at one height, give the requested block state?

The Python twin of the click-dependent part of vanilla ``getStateForPlacement``,
which the server's place_block runs for every visible hit point and four yaws
(``BodyBuild.place``). The yaw is the server's to turn, so a property that comes
from the player's facing (stairs, furnaces, doors) takes any click. What the yaw
cannot change is checked here:

- slab ``type`` and stair or trapdoor ``half``: bottom on a floor (the ``up`` face),
  top under a ceiling (``down``), and on a side face by the half of it clicked;
- a trapdoor clicked on a side face faces that face's direction;
- a pillar's ``axis`` (logs, chains) is the clicked face's axis;
- a hopper faces away from the clicked face, ``down`` from a floor or a ceiling.

Stance choice uses this so the body does not walk to where only the wrong half of a
face shows (run 20261120T083111010944Z: 28 wrong_state failures, 20 of them stairs
and slabs placed from stances whose visible points all made the other half).
"""

from __future__ import annotations

AXIS = {
    "up": "y",
    "down": "y",
    "north": "z",
    "south": "z",
    "east": "x",
    "west": "x",
}
OPPOSITE = {
    "up": "down",
    "down": "up",
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
}
# A side click this close to a face's middle is a float away from either half
# (``y - pos.y > 0.5``): neither half is promised.
MIDDLE = 1e-6


def parse(state: str) -> tuple[str, dict[str, str]]:
    """``"minecraft:oak_slab[type=top]"`` gives ``("minecraft:oak_slab",
    {"type": "top"})``."""
    ident, _, rest = state.partition("[")
    props = {}
    for pair in rest.rstrip("]").split(","):
        key, sep, value = pair.partition("=")
        if sep:
            props[key.strip()] = value.strip()
    return ident.strip(), props


def _half(face: str, frac_y: float) -> str | None:
    """The half a click makes: SlabBlock, StairBlock and TrapDoorBlock agree."""
    if face == "up":
        return "bottom"
    if face == "down":
        return "top"
    if abs(frac_y - 0.5) < MIDDLE:
        return None
    return "top" if frac_y > 0.5 else "bottom"


def click_gives(state: str | None, face: str, frac_y: float) -> bool:
    """Can a click on ``face`` (the clicked block's face toward the target, e.g.
    ``up`` for the floor under it) at height ``frac_y`` (hit y minus target y) give
    ``state``, the server turning the yaw as it needs? None or a state no click
    decides: True."""
    if not state:
        return True
    ident, props = parse(state)
    name = ident.rpartition(":")[2]
    if name.endswith("_slab"):
        kind = props.get("type")
        if kind in ("top", "bottom") and _half(face, frac_y) != kind:
            return False
    elif name.endswith("_stairs") or name.endswith("_trapdoor"):
        half = props.get("half")
        if half in ("top", "bottom") and _half(face, frac_y) != half:
            return False
        facing = props.get("facing")
        if name.endswith("_trapdoor") and AXIS[face] != "y" and facing != face:
            return False
    elif name == "hopper":
        facing = props.get("facing")
        made = "down" if AXIS[face] == "y" else OPPOSITE[face]
        if facing and facing != made:
            return False
    axis = props.get("axis")
    if axis in ("x", "y", "z") and AXIS[face] != axis:
        return False
    return True
