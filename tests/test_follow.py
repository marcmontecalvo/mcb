"""Follow a named player (companion track, step 2)."""

from types import SimpleNamespace

import pytest

from mclab.follow import RETARGET_BLOCKS, FollowTarget, player_stance


def observation(*entities, tick=10):
    return {"tick": tick, "visible_entities": list(entities)}


def leader(position, name="Leader"):
    return {"type": "minecraft:player", "name": name, "position": list(position)}


def controller(goal):
    c = SimpleNamespace(goal=goal, retargeted=[])
    c.retarget = lambda g: (c.retargeted.append(g), setattr(c, "goal", g))
    return c


def test_goal_moves_to_the_named_player_once_they_move_away():
    c = controller((0, 64, 0))
    target = FollowTarget("Leader")
    cow = {"type": "minecraft:cow", "position": [5.5, 64.0, 5.5]}
    other = leader((9.5, 64.0, 0.5), name="Someone")
    assert not target.update(c, observation(cow, other))  # not seen: keep going
    assert not target.update(c, observation(leader((1.2, 64.0, 0.7))))  # still close
    assert target.update(c, observation(leader((6.4, 65.0, -3.2)), tick=40))
    assert c.retargeted == [(6, 65.0, -4)] and target.last_seen[1] == 40


def test_out_of_sight_keeps_the_last_seen_goal():
    c = controller((0, 64, 0))
    target = FollowTarget("Leader")
    target.update(c, observation(leader((10.5, 64.0, 0.5))))
    assert not target.update(c, observation())
    assert c.goal == (10, 64.0, 0)


def test_stance_keeps_slab_heights_and_retarget_threshold():
    assert player_stance((-3.2, 70.5, 4.9)) == (-4, 70.5, 4)
    assert RETARGET_BLOCKS == 2.0
    with pytest.raises(ValueError):
        FollowTarget("")
