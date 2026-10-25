import copy
import math

import pytest

from mclab.navigation import NavigationController, path_to, walkable_nodes


def cell(x, y, z, block="minecraft:air", tick=100):
    return {
        "position": [x, y, z],
        "block": block,
        "observed_tick": tick,
        "collision_boxes": [] if block == "minecraft:air" else [[0, 0, 0, 1, 1, 1]],
        "fluid": "minecraft:empty",
    }


def world(radius=7):
    return {
        (x, y, z): cell(x, y, z, "minecraft:stone" if y == 79 else "minecraft:air")
        for x in range(-radius, radius + 1)
        for z in range(-radius, radius + 1)
        for y in range(79, 85)
    }


def observation(cells=None, **body_changes):
    body = {
        "position": [0.5, 80, 0.5],
        "yaw": 0,
        "pitch": 0,
        "ray": {},
        "on_ground": True,
        "inventory": [],
    }
    body.update(body_changes)
    return {
        "sensor_profile": "local-visibility-v1",
        "tick": 100,
        "body": body,
        "visible_blocks": list((world() if cells is None else cells).values()),
        "visible_entities": [],
    }


def test_known_grid_routes_around_two_block_wall_without_climbing_it():
    cells = world()
    for x in (-1, 0, 1):
        for z in (3, 4):
            for y in (80, 81):
                cells[x, y, z] = cell(x, y, z, "minecraft:stone")
    route = path_to(cells, (0, 80, 0), {(0, 80, 6)})
    assert route and route[-1] == (0, 80, 6)
    assert any(abs(n[0]) >= 2 for n in route)
    assert all(n[1] == 80 for n in route)


def test_full_width_crest_requires_one_block_up_and_down_edges():
    cells = world()
    for x in range(-7, 8):
        for z in (3, 4):
            cells[x, 80, z] = cell(x, 80, z, "minecraft:stone")
    route = path_to(cells, (0, 80, 0), {(0, 80, 6)})
    assert route and any(n[1] == 81 for n in route)
    assert route[-1][1] == 80
    assert all(abs(a[1] - b[1]) <= 1 for a, b in zip(route, route[1:], strict=False))


def test_unknown_and_low_ceiling_do_not_supply_walkable_or_jump_nodes():
    cells = world()
    del cells[0, 79, 1]
    assert (0, 80, 1) not in walkable_nodes(cells)
    for x in range(-7, 8):
        cells[x, 80, 3] = cell(x, 80, 3, "minecraft:stone")
        cells[x, 83, 2] = cell(x, 83, 2, "minecraft:stone")
        cells[x, 83, 3] = cell(x, 83, 3, "minecraft:stone")
    assert not path_to(cells, (0, 80, 0), {(0, 80, 6)})


def test_target_is_discovered_only_from_visible_cells_and_state_is_per_episode():
    controller = NavigationController()
    controller.decide({**observation(), "evaluator_target": [12, 80, 0]})
    assert controller.target is None
    cells = world()
    cells[0, 80, 6] = cell(0, 80, 6, "minecraft:oak_log")
    controller.decide(observation(cells))
    assert controller.target == (0, 80, 6)
    assert NavigationController().target is None
    assert not NavigationController().cells


def test_mining_requires_current_log_ray_and_fully_aligned_aim():
    cells = world()
    cells[0, 80, 3] = cell(0, 80, 3, "minecraft:oak_log")
    pitch = math.degrees(math.atan2(1.12, 3))
    controller = NavigationController()
    o = observation(
        cells, pitch=pitch, ray={"block": "minecraft:oak_log", "position": [0, 80, 3]}
    )
    assert controller.decide(o)["mine"]
    o["body"]["ray"] = {}
    assert not controller.decide(o)["mine"]
    o["body"]["ray"] = {"block": "minecraft:oak_log", "position": [0, 80, 3]}
    o["body"]["yaw"] = 90
    assert not controller.decide(o)["mine"]


def test_occluded_target_uses_navigation_instead_of_heading_only_reacquisition():
    controller = NavigationController()
    controller.target = (0, 80, -3)
    result = controller.decide(observation())
    assert controller.mode == "approaching"
    assert not result.get("mine")


def test_aligned_but_occluded_remembered_target_does_not_stall():
    controller = NavigationController()
    controller.target = (0, 80, 3)
    controller.decide(observation(pitch=math.degrees(math.atan2(1.12, 3))))
    assert controller.mode == "approaching"


def test_observed_air_after_mining_switches_to_pickup_without_evaluator_state():
    cells = world()
    cells[0, 80, 3] = cell(0, 80, 3, "minecraft:oak_log")
    controller = NavigationController()
    controller.decide(
        observation(
            cells,
            pitch=math.degrees(math.atan2(1.12, 3)),
            ray={"block": "minecraft:oak_log", "position": [0, 80, 3]},
        )
    )
    cells[0, 80, 3] = cell(0, 80, 3)
    o = observation(cells)
    o["visible_entities"] = [
        {
            "uuid": "drop-visible",
            "item": "minecraft:oak_log",
            "position": [0.5, 80.1, 3.5],
        }
    ]
    controller.decide(o)
    assert controller.broken and controller.mode == "collecting"
    assert controller.drop == [0.5, 80.1, 3.5]
    o["body"]["inventory"] = [{"item": "minecraft:oak_log", "count": 1}]
    assert controller.decide(o) == {}


def test_collection_stays_at_a_valid_pickup_cell_instead_of_exploring():
    controller = NavigationController()
    controller.cells = world()
    controller.broken = True
    controller.drop = [0.6, 80.1, 0.7]
    assert controller._route(observation()["body"]) == [(0, 80, 0)]


def test_collection_approaches_known_crest_edge_to_reveal_the_drop_floor():
    controller = NavigationController()
    cells = world()
    for x in range(-7, 8):
        for z in (2, 3):
            cells[x, 80, z] = cell(x, 80, z, "minecraft:stone")
        for z in range(4, 8):
            del cells[x, 79, z]
    controller.cells = cells
    controller.broken = True
    controller.drop = [0.5, 80.1, 5.2]
    body = observation(position=[0.5, 81, 2.5])["body"]
    route = controller._route(body)
    assert route[-1] == (0, 81, 3)
    assert all(node[2] < 4 for node in route)  # Unknown support never supplies a step.
    for x in range(-7, 8):
        for z in range(4, 8):
            cells[x, 79, z] = cell(x, 79, z, "minecraft:stone")
    body["position"] = [0.5, 81, 3.5]
    landing = controller._route(body)[-1]
    assert landing[1] == 80 and landing[2] >= 4
    assert abs(landing[2] + 0.5 - controller.drop[2]) < 0.8


def test_collection_goal_on_the_platform_above_a_drop_is_rejected():
    controller = NavigationController()
    cells = world()
    for z in (0, 1):
        cells[0, 80, z] = cell(0, 80, z, "minecraft:stone")
    controller.cells = cells
    controller.broken = True
    controller.drop = [0.5, 80.0, 2.1]
    body = observation(position=[0.5, 81, 0.5])["body"]
    assert controller._route(body)[-1] == (0, 80, 2)


def test_stale_support_cannot_move_even_when_old_memory_can_plan():
    cells = world()
    controller = NavigationController()
    fresh = observation(cells)
    controller._observe(fresh)
    waypoint = [0.5, 80, 1.5]
    assert controller._move(fresh, waypoint)["forward"] > 0
    stale = copy.deepcopy(fresh)
    stale["tick"] = 106
    assert not controller._move(stale, waypoint).get("forward")


def test_jump_command_requires_fresh_landing_and_headroom():
    cells = world()
    cells[0, 80, 1] = cell(0, 80, 1, "minecraft:stone")
    controller = NavigationController()
    o = observation(cells)
    controller._observe(o)
    result = controller._move(o, [0.5, 81, 1.5])
    assert result["jump"] and result["forward"] > 0
    del controller.cells[0, 83, 1]
    assert not controller._move(o, [0.5, 81, 1.5]).get("forward")


def test_unsupported_sensor_rejected_before_map_mutation():
    controller = NavigationController()
    with pytest.raises(ValueError, match="sensor"):
        controller.decide({"sensor_profile": "camera"})
    assert not controller.cells


def test_exploration_prefers_the_frontier_that_reveals_more_unseen_floor():
    cells = {
        p: c
        for p, c in world(12).items()
        if not (p[0] > 7 and p[1] == 79)  # east floor beyond sight is unseen
        and p[0] not in (-8, -9)  # west strip unseen but narrow
    }
    controller = NavigationController()
    controller.cells = cells
    controller.explore_heading = math.pi / 2  # bias west
    nodes = walkable_nodes(cells)
    route = controller._frontier(nodes, (0, 80, 0))
    assert route[-1][0] > 0


def test_route_is_reused_until_waypoint_arrival_and_invalidated_by_a_new_target(
    monkeypatch,
):
    controller = NavigationController()
    calls = []

    def route(_body):
        calls.append(True)
        return [(0, 80, 0), (0, 80, 1), (0, 80, 2)]

    monkeypatch.setattr(controller, "_route", route)
    assert controller.decide(observation()).get("forward")
    assert controller.decide(observation(position=[0.5, 80, 0.7])).get("forward")
    assert controller.decide(observation(position=[0.5, 80, 1.6])).get("forward")
    assert len(calls) == 1 and controller.last_plan[0] == (0, 80, 1)
    cells = world()
    cells[0, 80, 6] = cell(0, 80, 6, "minecraft:oak_log")
    controller.decide(observation(cells))
    assert len(calls) == 2


def test_cached_route_still_rejects_stale_support_and_requests_replanning(monkeypatch):
    controller = NavigationController()
    monkeypatch.setattr(
        controller, "_route", lambda _: [(0, 80, 0), (0, 80, 1), (0, 80, 2)]
    )
    assert controller.decide(observation()).get("forward")
    stale = observation({})
    stale["tick"] = 106
    assert not controller.decide(stale).get("forward")
    assert controller.last_plan == []


def test_newly_observed_void_reconsiders_an_exploration_goal(monkeypatch):
    controller = NavigationController()
    calls = []

    def route(_body):
        calls.append(True)
        return [(0, 80, 0), (0, 80, 1), (0, 80, 6)]

    monkeypatch.setattr(controller, "_route", route)
    cells = world()
    del cells[2, 79, 6]  # This column starts unknown, not known to be solid.
    controller.decide(observation(cells))
    cells[2, 79, 6] = cell(2, 79, 6)
    cells[2, 78, 6] = cell(2, 78, 6)
    controller.decide(observation(cells))
    assert len(calls) == 2


def test_straight_level_lookahead_uses_qualified_full_walking_speed(monkeypatch):
    controller = NavigationController()
    monkeypatch.setattr(controller, "_route", lambda _: [(0, 80, z) for z in range(4)])
    decision = controller.decide(observation())
    assert decision["forward"] == 1 and not decision["jump"]


def test_lookahead_does_not_cut_a_corner(monkeypatch):
    controller = NavigationController()
    monkeypatch.setattr(
        controller,
        "_route",
        lambda _: [(0, 80, 0), (0, 80, 1), (1, 80, 1), (2, 80, 1)],
    )
    decision = controller.decide(observation())
    assert decision["yaw_delta"] == 0 and 0 < decision["forward"] < 1


def test_lookahead_preserves_short_jump_waypoints(monkeypatch):
    controller = NavigationController()
    monkeypatch.setattr(
        controller, "_route", lambda _: [(0, 80, 0), (0, 81, 1), (0, 81, 2)]
    )
    cells = world()
    for z in (1, 2):
        cells[0, 80, z] = cell(0, 80, z, "minecraft:stone")
    decision = controller.decide(observation(cells))
    assert decision["jump"] and 0 < decision["forward"] <= 0.7


def test_unseen_distant_waypoint_falls_back_to_fresh_nearer_support(monkeypatch):
    controller = NavigationController()
    controller.cells = world()
    controller.cells[0, 80, 3]["observed_tick"] = 90
    cells = world()
    del cells[0, 80, 3]
    monkeypatch.setattr(controller, "_route", lambda _: [(0, 80, z) for z in range(4)])
    original = controller._move
    attempted = []

    def move(observed, waypoint):
        attempted.append(waypoint)
        return original(observed, waypoint)

    monkeypatch.setattr(controller, "_move", move)
    assert controller.decide(observation(cells))["forward"] == 1
    assert [p[2] for p in attempted] == [3.5, 2.5]


@pytest.mark.parametrize(
    "below,expected", [(None, 0), ("minecraft:stone", 0), ("minecraft:air", 1)]
)
def test_exploration_void_penalty_preserves_unknowns_and_one_block_descents(
    below, expected
):
    controller = NavigationController()
    controller.cells = world()
    controller.cells[2, 79, 0] = cell(2, 79, 0)
    if below is not None:
        controller.cells[2, 78, 0] = cell(2, 78, 0, below)
    assert controller._void_exposure((0, 80, 0)) == expected


@pytest.mark.parametrize(
    "below,expected_edge",
    [(None, math.inf), ("minecraft:stone", math.inf), ("minecraft:air", 7)],
)
def test_only_observed_two_block_void_limits_speculative_edge_gain(
    monkeypatch, below, expected_edge
):
    cells = world()
    cells[8, 79, 0] = cell(8, 79, 0)
    if below is not None:
        cells[8, 78, 0] = cell(8, 78, 0, below)
    controller = NavigationController()
    controller.cells = cells
    controller.explore_heading = 0
    bounds = []

    def gain(*_args, **kwargs):
        bounds.append(kwargs["bounds"])
        return 0

    monkeypatch.setattr(controller, "_unseen_floor", gain)
    controller._frontier(walkable_nodes(cells), (0, 80, 0))
    assert bounds and all(b[1] == expected_edge for b in bounds)
