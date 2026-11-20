from dataclasses import replace

import pytest
from test_staircase_steps import staircase_map, stock

from mclab.pillar_steps import PillarExecutor, prepare_pillar
from mclab.terrain_steps import StepPlan, TerrainPolicy


def policy():
    return TerrainPolicy(allow_break=False, allow_pillar=True)


def test_pillar_requires_permission_supply_known_headroom_and_solid_exit():
    cells = staircase_map(1)
    plan = prepare_pillar(cells, (0, 0, 0), (0, 4, 1), policy(), stock(3))
    assert plan.target == (0, 1, 0) and plan.places == ((0, 0, 0),)
    assert plan.kind == "pillar" and plan.exit == (0, 4, 1)
    for forbidden in (
        replace(policy(), allow_pillar=False),
        replace(policy(), allow_place=False),
        replace(policy(), reserve_blocks=3),
        replace(policy(), max_pillar_height=2),
        replace(policy(), protected_positions=((0, 0, 0),)),
    ):
        assert prepare_pillar(cells, (0, 0, 0), (0, 4, 1), forbidden, stock(3)) is None
    del cells[0, 3, 0]
    assert prepare_pillar(cells, (0, 0, 0), (0, 4, 1), policy(), stock(3)) is None


def test_pillar_jump_window_and_confirmed_landing():
    cells = staircase_map(1)
    plan = prepare_pillar(cells, (0, 0, 0), (0, 4, 1), policy(), stock(3))
    executor = PillarExecutor(plan, policy())
    body = {
        "position": [0.5, 0, 0.5],
        "on_ground": True,
        "yaw": 0,
        "pitch": 90,
        "velocity": [0, 0, 0],
        "inventory": stock(3),
        "survival": {"eye_height": 1.62},
        "building_pickups": {},
        "ray": {"position": [0, -1, 0], "face": "UP"},
    }
    obs = {"tick": 1, "body": body}
    assert executor.decide(obs, cells)["jump"]
    body.update(on_ground=False, position=[0.5, 0.75, 0.5], velocity=[0, 0.33, 0])
    obs["tick"] = 4
    assert not executor.decide(obs, cells).get("use_pulse")
    body.update(position=[0.5, 1.16, 0.5], velocity=[0, 0.165, 0])
    obs["tick"] = 7
    assert executor.decide(obs, cells)["use_pulse"]
    body["inventory"][0]["count"] -= 1
    obs["tick"] = 10
    assert not executor.decide(obs, cells).get("use_pulse")
    assert executor.places_confirmed == 0
    cells[0, 0, 0].update(
        block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
    )
    obs["tick"] = 13
    executor.decide(obs, cells)
    assert executor.places_confirmed == 1 and executor.state != "ready"
    body.update(on_ground=True, position=[0.5, 1, 0.5], velocity=[0, 0, 0])
    obs["tick"] = 16
    executor.decide(obs, cells)
    assert executor.state == "ready"


@pytest.mark.parametrize("velocity", [-0.15, 0.0])
def test_descending_or_late_jump_window_never_places(velocity):
    cells = staircase_map(1)
    plan = prepare_pillar(cells, (0, 0, 0), (0, 4, 1), policy(), stock())
    executor = PillarExecutor(plan, policy())
    executor.launch_tick = 1
    body = {
        "position": [0.5, 1.15, 0.5],
        "on_ground": False,
        "yaw": 0,
        "pitch": 90,
        "velocity": [0, velocity, 0],
        "inventory": stock(),
        "survival": {"eye_height": 1.62},
        "ray": {"position": [0, -1, 0], "face": "UP"},
    }
    assert not executor.decide({"tick": 7, "body": body}, cells).get("use_pulse")


@pytest.mark.parametrize("change", ["exit", "headroom", "stock", "velocity", "face"])
def test_changed_pillar_preconditions_cannot_launch_or_place(change):
    cells = staircase_map(1)
    plan = prepare_pillar(cells, (0, 0, 0), (0, 4, 1), policy(), stock())
    executor = PillarExecutor(plan, policy())
    body = {
        "position": [0.5, 0, 0.5],
        "on_ground": True,
        "yaw": 0,
        "pitch": 90,
        "velocity": [0, 0, 0],
        "inventory": stock(),
        "survival": {"eye_height": 1.62},
        "ray": {"position": [0, -1, 0], "face": "UP"},
    }
    if change == "exit":
        cells[0, 3, 1].update(block="minecraft:air", collision_boxes=[])
    elif change == "headroom":
        cells[0, 3, 0].update(
            block="minecraft:stone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
        )
    elif change == "stock":
        body["inventory"] = []
    elif change == "velocity":
        body.pop("velocity")
    else:
        body["ray"]["face"] = "SOUTH"
    command = executor.decide({"tick": 1, "body": body}, cells)
    assert not command.get("jump") and not command.get("use_pulse")


def test_pillar_executor_rejects_unconfirmed_geometry_and_displacement():
    cells = staircase_map(1)
    plan = prepare_pillar(cells, (0, 0, 0), (0, 4, 1), policy(), stock())
    body = {
        "position": [0.5, 1, 0.5],
        "on_ground": True,
        "inventory": stock(),
        "building_pickups": {},
    }
    executor = PillarExecutor(plan, policy())
    executor.issue_tick = 1
    executor.item, executor.initial_count = "minecraft:cobblestone", 4
    executor.decide({"tick": 20, "body": body}, cells)
    assert executor.state == "failed" and executor.places_confirmed == 0
    executor = PillarExecutor(plan, policy())
    body["position"][0] = 1.1
    assert executor.decide({"tick": 1, "body": body}, cells) == {}
    assert executor.state == "failed"


def test_pillar_going_on_up_exits_straight_above_onto_the_next_block():
    # StepMotor's contract: a pillar whose next move is another pillar exits
    # straight above, a cell that stands only once the next block is under it.
    # Requiring a standable side exit refused every dirt tower of two or more
    # (house-large run 20261120T113050800552Z: 733 refused edits, ~13 min).
    cells = staircase_map(1)
    plan = prepare_pillar(cells, (0, 0, 0), (0, 2, 0), policy(), stock(3))
    assert plan is not None and plan.exit == (0, 2, 0) and plan.target == (0, 1, 0)


def test_tower_top_pillar_exits_onto_its_own_target():
    cells = staircase_map(1)
    plan = prepare_pillar(cells, (0, 0, 0), (0, 1, 0), policy(), stock(3))
    assert plan is not None and plan.exit == (0, 1, 0)


@pytest.mark.parametrize("exit_node", [(0, 3, 0), (0, 0, 0)])
def test_straight_up_exit_is_the_target_or_the_cell_above_it(exit_node):
    cells = staircase_map(1)
    assert prepare_pillar(cells, (0, 0, 0), exit_node, policy(), stock(3)) is None


def test_straight_up_exit_needs_a_clear_body_column():
    cells = staircase_map(1)
    cells[0, 3, 0].update(
        block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
    )
    assert prepare_pillar(cells, (0, 0, 0), (0, 2, 0), policy(), stock(3)) is None


def test_pillar_exit_may_stand_on_a_stair_top():
    # The roof's stairs are the way off a tower (StepMotor: "the exit may stand on
    # a stair top or slab"); a full-cube-only exit refused them.
    cells = staircase_map(1)
    cells[0, 3, 1].update(
        block="minecraft:oak_stairs",
        collision_boxes=[[0, 0, 0, 1, 0.5, 1], [0, 0.5, 0.5, 1, 1, 1]],
    )
    plan = prepare_pillar(cells, (0, 0, 0), (0, 4, 1), policy(), stock(3))
    assert plan is not None and plan.exit == (0, 4, 1)


def test_dirt_pillar_from_a_scaffolding_top_is_accepted():
    # The live case: the body on top of a 4-high scaffolding column, out of
    # scaffolding, pillaring up with dirt toward the roof.
    cells = staircase_map(1)
    for y in range(0, 4):
        cells[0, y, 0].update(block="minecraft:scaffolding", collision_boxes=[])
    plan = prepare_pillar(cells, (0, 4, 0), (0, 6, 0), policy(), stock(3))
    assert plan is not None and plan.places == ((0, 4, 0),)


def _route_edit_nav(cells, route, steps, tick=100):
    from mclab.navigation_segmented import SegmentedController

    fresh = {p: dict(c, observed_tick=tick) for p, c in cells.items()}
    nav = SegmentedController.__new__(SegmentedController)
    nav.route = list(route)
    nav.route_steps = {(s.source, s.target): s for s in steps}
    nav.terrain_queue, nav.terrain_forecast = [], ()
    nav.breaks_reserved = nav.places_reserved = 0
    nav.terrain = nav.terrain_search = nav.prefetch = None
    nav.failed_edges, nav.own_footholds, nav.own_breaks = set(), set(), {}
    nav.events, nav.search = [], object()
    nav.memory = type("Memory", (), {"cells": fresh})()
    nav._remaining_policy = lambda: replace(
        policy(), material_allowlist=("minecraft:cobblestone",)
    )
    nav._fresh = lambda now: fresh
    nav._begin_route_edit(route[0], {"tick": tick, "body": {"inventory": stock(3)}})
    return nav


def test_a_solid_pillar_tower_starts_instead_of_replanning():
    # Out of scaffolding, the planner pillars with dirt; each plan was refused at
    # its first block and replanned from wherever the body had got to (the
    # 311 s up-and-down on a scaffolding column of run 20261120T113050800552Z).
    cells = staircase_map(1)
    up = [
        StepPlan((0, y, 0), (0, y + 1, 0), places=((0, y, 0),), kind="pillar")
        for y in (0, 1)
    ]
    nav = _route_edit_nav(cells, [(0, 0, 0), (0, 1, 0), (0, 2, 0)], up)
    assert nav.terrain is not None and nav.terrain.plan.exit == (0, 2, 0)
    assert (up[0].source, up[0].target) not in nav.failed_edges


def test_a_refused_route_edit_is_recorded_with_its_plan():
    cells = staircase_map(1)
    cells[0, 3, 0].update(
        block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
    )
    up = StepPlan((0, 0, 0), (0, 1, 0), places=((0, 0, 0),), kind="pillar")
    nav = _route_edit_nav(cells, [(0, 0, 0), (0, 1, 0)], [up])
    assert nav.terrain is None and ((0, 0, 0), (0, 1, 0)) in nav.failed_edges
    refused = [data for kind, data in nav.events if kind == "terrain_step_refused"]
    assert refused == [
        {
            "source": (0, 0, 0),
            "target": (0, 1, 0),
            "kind": "pillar",
            "breaks": (),
            "places": ((0, 0, 0),),
            "scaffold": (),
            "exit": (0, 1, 0),
            "node": (0, 0, 0),
        }
    ]


@pytest.mark.parametrize("value", [0, 4, True, 1.5])
def test_invalid_pillar_limits_rejected(value):
    with pytest.raises(ValueError):
        TerrainPolicy(max_pillar_height=value)
