from dataclasses import replace

import pytest
from test_staircase_steps import staircase_map, stock

from mclab.pillar_steps import PillarExecutor, prepare_pillar
from mclab.terrain_route import TerrainRouteSearch
from mclab.terrain_steps import TerrainPolicy


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


def test_pillar_forecast_contains_three_vertical_placements_and_known_exit():
    cells = staircase_map(1)
    search = TerrainRouteSearch(
        cells,
        (0, 0, 0),
        (0, 4, 5),
        map_revision=1,
        policy=policy(),
        inventory=stock(3),
    )
    for _ in range(500):
        result = search.advance(node_limit=2, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == "segment" and len(result.steps) == 4
    assert result.route == ((0, 0, 0), (0, 1, 0), (0, 2, 0), (0, 3, 0), (0, 4, 1))
    assert [s.places for s in result.steps] == [
        ((0, 0, 0),),
        ((0, 1, 0),),
        ((0, 2, 0),),
        (),
    ]
    assert cells[0, 0, 0]["block"] == "minecraft:air"


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


@pytest.mark.parametrize("value", [0, 4, True, 1.5])
def test_invalid_pillar_limits_rejected(value):
    with pytest.raises(ValueError):
        TerrainPolicy(max_pillar_height=value)


@pytest.mark.parametrize("variant", range(10))
def test_rolling_pillar_forecast_accounts_for_each_fixture_and_exit(variant):
    cliff, height = 1 + variant // 2, 3 + variant % 2
    cells, inventory = staircase_map(cliff), stock(height - 1)
    if height == 3:
        for z in range(cliff, cliff + 5):
            cells[0, 3, z].update(block="minecraft:air", collision_boxes=[])
    node, placed = (0, 0, 0), set()
    for _ in range(6):
        search = TerrainRouteSearch(
            cells,
            node,
            (0, height, cliff + 4),
            map_revision=1,
            policy=policy(),
            inventory=inventory,
            protected=placed,
        )
        for _ in range(500):
            result = search.advance(node_limit=2, budget_ms=100)
            if result.status != "searching":
                break
        assert result.status in {"reached", "segment"}
        assert len(result.steps) == 4 or result.status == "reached"
        for step in result.steps:
            assert not step.breaks
            for point in step.places:
                assert step.kind == "pillar" and point not in placed
                placed.add(point)
                inventory[0]["count"] -= 1
                cells[point].update(
                    block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
                )
        node = result.steps[-1].target
        if result.status == "reached":
            break
    assert node == (0, height, cliff + 4)
    assert placed == {(0, y, cliff - 1) for y in range(height - 1)}
    assert inventory[0]["count"] == 0
