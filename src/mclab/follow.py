"""Follow a named player (companion track): the goal moves with them.

Baritone FollowProcess, reduced: the goal is the player's feet cell within the
controller's radius, moved once the player is RETARGET_BLOCKS from it. The player
is found among the entities the body can see (no x-ray); out of sight, the bot
keeps heading for where they were last seen. The native driver calls ``update``
with every observation of a walk whose controller carries ``follow``.
"""

import math

RETARGET_BLOCKS = 2.0
PLAYER = "minecraft:player"


def player_stance(position):
    """The standing node under a player's feet (fractional heights kept: slabs)."""
    x, y, z = position
    return (math.floor(x), round(y, 3), math.floor(z))


def feet_cell(position):
    """The integer feet cell, as ``CompanionBody.position`` names the body's own
    (a player on a slab top stands in the slab's cell). Route goals are cells."""
    x, y, z = position
    return (math.floor(x), math.floor(y + 1e-6), math.floor(z))


def find_player(entities, name):
    """The position of the visible player called ``name`` (Minecraft names ignore
    case), else None."""
    wanted = name.lower()
    for entity in entities or ():
        named = (entity.get("name") or "").lower()
        if entity.get("type") == PLAYER and named == wanted:
            position = entity.get("position")
            if position is not None:
                return tuple(position)
    return None


class FollowTarget:
    def __init__(self, name):
        if not isinstance(name, str) or not 1 <= len(name) <= 16:
            raise ValueError("Follow target must be a player name")
        self.name = name
        self.last_seen = None  # (position, tick)

    def locate(self, observation):
        return find_player(observation.get("visible_entities"), self.name)

    def update(self, controller, observation):
        """Move the controller's goal to the player once they move away from it."""
        seen = self.locate(observation)
        if seen is None:
            return False
        self.last_seen = (seen, observation.get("tick"))
        goal = feet_cell(seen)
        if math.dist(goal, controller.goal) < RETARGET_BLOCKS:
            return False
        controller.retarget(goal)
        return True
