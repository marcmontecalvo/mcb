"""Follow a named player (companion track): the goal moves with them.

Baritone FollowProcess, reduced: the goal is the player's stance within the
controller's radius, moved once the player is RETARGET_BLOCKS from it. The player
is found among the entities the body can see (no x-ray); out of sight, the bot
keeps heading for where they were last seen.
"""

import math

RETARGET_BLOCKS = 2.0


def player_stance(position):
    """The standing node under a player's feet (fractional heights kept: slabs)."""
    x, y, z = position
    return (math.floor(x), round(y, 3), math.floor(z))


class FollowTarget:
    def __init__(self, name):
        if not isinstance(name, str) or not 1 <= len(name) <= 16:
            raise ValueError("Follow target must be a player name")
        self.name = name
        self.last_seen = None  # (position, tick)

    def locate(self, observation):
        for entity in observation.get("visible_entities") or ():
            if (
                entity.get("type") == "minecraft:player"
                and entity.get("name") == self.name
            ):
                return tuple(entity["position"])
        return None

    def update(self, controller, observation):
        """Move the controller's goal to the player once they move away from it."""
        seen = self.locate(observation)
        if seen is None:
            return False
        self.last_seen = (seen, observation.get("tick"))
        goal = player_stance(seen)
        if math.dist(goal, controller.goal) < RETARGET_BLOCKS:
            return False
        controller.retarget(goal)
        return True
