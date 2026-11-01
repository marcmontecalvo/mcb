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


def test_retarget_keeps_the_route_and_drops_stale_plans():
    from mclab.navigation_segmented import SegmentedController

    c = SimpleNamespace(
        goal=(0, 64, 0),
        plan_tail=((1, 64, 0),),
        search=object(),
        prefetch=object(),
        refreshed=((3, 64, 0), 7),
        route=[(0, 64, 0), (1, 64, 0)],
        _discard_prefetch=lambda: setattr(c, "prefetch", None),
    )
    SegmentedController.retarget(c, (9, 64.0, 2))
    assert c.goal == (9, 64.0, 2) and c.route == [(0, 64, 0), (1, 64, 0)]
    assert c.search is None and c.prefetch is None and c.refreshed is None
    assert c.plan_tail == ()


def test_leader_paths_are_validated_for_the_segmented_entrant_only():
    from mclab.natural import _validate_leader

    good = {"waypoints": [[0, 0], [10, 5]], "speed": 0.216, "pause_ticks": 40}
    _validate_leader(good, ("segmented_goal",))
    for bad, entrants in (
        (good, ("baritone",)),
        ({**good, "waypoints": [[0, 0]]}, ("segmented_goal",)),
        ({**good, "speed": 0.9}, ("segmented_goal",)),
        ({**good, "waypoints": [[0, 0], [1.5, 2]]}, ("segmented_goal",)),
    ):
        with pytest.raises(ValueError):
            _validate_leader(bad, entrants)
