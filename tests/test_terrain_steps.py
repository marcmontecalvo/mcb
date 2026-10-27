from dataclasses import replace

import pytest

from mclab.terrain_steps import StepExecutor, StepPlan, TerrainPolicy, prepare_step


def cell(p, solid):
    return {
        "position": list(p),
        "block": "minecraft:stone" if solid else "minecraft:air",
        "collision_boxes": [[0, 0, 0, 1, 1, 1]] if solid else [],
    }


def terrain():
    return {
        (x, y, z): cell((x, y, z), y < 0)
        for x in range(-3, 4)
        for y in range(-1, 7)
        for z in range(-2, 7)
    }


def inventory():
    return [
        {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
        {"slot": 3, "item": "minecraft:cobblestone", "count": 64},
    ]


def test_foothold_requires_permission_stock_and_known_support():
    cells = terrain()
    policy = TerrainPolicy(allow_break=False)
    args = (cells, (0, 0, 0), (0, 1, 1))
    step = prepare_step(*args, policy, inventory())
    assert step.places == ((0, 0, 1),)
    assert step.breaks == ()
    assert prepare_step(*args, replace(policy, allow_place=False), inventory()) is None
    assert prepare_step(*args, replace(policy, reserve_blocks=64), inventory()) is None
    assert prepare_step(*args, policy, []) is None
    del cells[0, -1, 1]
    assert prepare_step(*args, policy, inventory()) is None


def test_cut_step_rejects_unknown_fluid_gravity_and_wrong_tool():
    cells = terrain()
    for y in range(3):
        cells[0, y, 1] = cell((0, y, 1), True)
    policy = TerrainPolicy(allow_place=False)
    args = (cells, (0, 0, 0), (0, 1, 1))
    step = prepare_step(*args, policy, inventory())
    assert set(step.breaks) == {(0, 1, 1), (0, 2, 1)}
    assert prepare_step(*args, replace(policy, break_budget=1), inventory()) is None
    assert prepare_step(*args, replace(policy, allow_break=False), inventory()) is None
    assert prepare_step(*args, policy, []) is None
    for dangerous in ({"block": "minecraft:sand"}, {"fluid": "minecraft:water"}, None):
        neighbor = cells[1, 1, 1]
        if dangerous is None:
            del cells[1, 1, 1]
        else:
            cells[1, 1, 1] = {**neighbor, **dangerous}
        assert prepare_step(*args, policy, inventory()) is None
        cells[1, 1, 1] = neighbor


@pytest.mark.parametrize(
    "policy",
    [
        TerrainPolicy(allow_place=False),
        TerrainPolicy(place_budget=0),
    ],
)
def test_executor_rejects_a_foothold_plan_outside_the_policy(policy):
    plan = StepPlan((0, 0, 0), (0, 1, 1), places=((0, 0, 1),))
    with pytest.raises(ValueError, match="permissions/budgets"):
        StepExecutor(plan, policy)


def test_planned_head_cut_can_occlude_foot_cut_and_both_are_counted():
    from mclab.navigation import rotation

    cells = terrain()
    for y in (0, 1):
        cells[0, y, 1] = cell((0, y, 1), True)
    policy = TerrainPolicy(allow_place=False)
    plan = prepare_step(cells, (0, 0, 0), (0, 0, 1), policy, inventory())
    executor = StepExecutor(plan, policy)
    body = {
        "position": [0.5, 0, 0.5],
        "yaw": 0,
        "pitch": 0,
        "inventory": inventory(),
        "ray": {"position": [0, 1, 1]},
    }
    body["pitch"] = rotation([0.5, 0.5, 1.5], body)["pitch_delta"]
    body["pitch"] += rotation([0.5, 0.5, 1.5], body)["pitch_delta"]
    obs = {"tick": 1, "body": body}
    assert executor.decide(obs, cells)["mine"]
    cells[0, 1, 1] = cell((0, 1, 1), False)
    body["ray"]["position"] = [0, 0, 1]
    obs["tick"] = 6
    assert executor.decide(obs, cells)["mine"]
    assert executor.breaks_confirmed == 1
    cells[0, 0, 1] = cell((0, 0, 1), False)
    for tick in (11, 12, 13):
        obs["tick"] = tick
        executor.decide(obs, cells)
    assert executor.state == "ready" and executor.breaks_confirmed == 2


def test_place_executor_checks_ray_face_stock_and_fresh_confirmation():
    from mclab.navigation import rotation

    cells = terrain()
    policy = TerrainPolicy(allow_break=False)
    step = prepare_step(cells, (0, 0, 0), (0, 1, 1), policy, inventory())
    executor = StepExecutor(step, policy)
    body = {
        "position": [0.5, 0, 0.5],
        "yaw": 0,
        "pitch": 0,
        "inventory": inventory(),
        "ray": {"position": [0, -1, 1], "face": "UP"},
    }
    body["pitch"] = rotation([0.5, -0.001, 1.5], body)["pitch_delta"]
    # rotation is bounded to 45 degrees; finish aiming before the pulse.
    body["pitch"] += rotation([0.5, -0.001, 1.5], body)["pitch_delta"]
    obs = {"tick": 1, "body": body}
    body["ray"]["face"] = "NORTH"
    assert not executor.decide(obs, cells).get("use_pulse")
    body["ray"]["face"] = "up"  # Pinned Minecraft wire representation.
    assert executor.decide(obs, cells)["use_pulse"]
    assert executor.state == "editing"
    obs["tick"] = 6
    assert not executor.decide(obs, {}).get("forward")
    cells[0, 0, 1] = {**cell((0, 0, 1), True), "block": "minecraft:cobblestone"}
    body["inventory"][1]["count"] -= 1
    executor.decide(obs, cells)
    executor.decide(obs, cells)
    assert executor.state == "ready"
    assert executor.places_confirmed == 1


@pytest.mark.parametrize("wrong_stock", [True, False])
def test_placement_failure_and_cancellation_never_imply_a_foothold(wrong_stock):
    cells = terrain()
    policy = TerrainPolicy(allow_break=False)
    step = prepare_step(cells, (0, 0, 0), (0, 1, 1), policy, inventory())
    executor = StepExecutor(step, policy)
    executor.issued = True
    executor.slot, executor.initial_count = 3, 64
    cells[0, 0, 1] = {**cell((0, 0, 1), True), "block": "minecraft:cobblestone"}
    body = {"position": [0.5, 0, 0.5], "inventory": inventory()}
    if not wrong_stock:
        body["inventory"][1]["count"] = 63
        body["position"][0] = 3  # Knockback during the interaction.
    assert executor.decide({"tick": 1, "body": body}, cells) == {}
    assert executor.state == "failed"
    assert executor.places_confirmed == 0


@pytest.mark.parametrize("pickup_ledger", [False, True])
def test_pickup_between_inventory_debit_and_geometry_refresh_is_reconciled(
    pickup_ledger,
):
    from mclab.navigation import rotation

    cells = terrain()
    policy = TerrainPolicy(allow_break=False)
    plan = prepare_step(cells, (0, 0, 0), (0, 1, 1), policy, inventory())
    executor = StepExecutor(plan, policy)
    body = {
        "position": [0.5, 0, 0.5],
        "yaw": 0,
        "pitch": 0,
        "inventory": inventory(),
        "ray": {"position": [0, -1, 1], "face": "UP"},
    }
    if pickup_ledger:
        body["building_pickups"] = {"minecraft:cobblestone": 0}
    for _ in range(2):
        body["pitch"] += rotation([0.5, -0.001, 1.5], body)["pitch_delta"]
    obs = {"tick": 1, "body": body}
    assert executor.decide(obs, cells)["use_pulse"]
    assert executor.issued_places == {(0, 0, 1)}
    body["inventory"][1]["count"] = 63
    obs["tick"] = 4
    if not pickup_ledger:
        # Older callers can observe the debit before the map has refreshed.
        executor.decide(obs, {})
    body["inventory"][1]["count"] = 64
    if pickup_ledger:
        # Native cumulative transfers also cover a debit hidden between samples.
        body["building_pickups"]["minecraft:cobblestone"] = 1
    cells[0, 0, 1] = {**cell((0, 0, 1), True), "block": "minecraft:cobblestone"}
    obs["tick"] = 7
    executor.decide(obs, cells)
    executor.decide(obs, cells)
    assert executor.state == "ready" and executor.places_confirmed == 1


def test_pickup_ledger_cannot_confirm_a_placement_without_an_actual_inventory_debit():
    cells = terrain()
    policy = TerrainPolicy(allow_break=False)
    plan = prepare_step(cells, (0, 0, 0), (0, 1, 1), policy, inventory())
    executor = StepExecutor(plan, policy)
    executor.issued = True
    executor.slot, executor.initial_count = 3, 64
    executor.placement_item = "minecraft:cobblestone"
    executor.initial_item_total, executor.initial_pickups = 64, 0
    cells[0, 0, 1] = {**cell((0, 0, 1), True), "block": "minecraft:cobblestone"}
    body = {
        "position": [0.5, 0, 0.5],
        "inventory": inventory(),
        "building_pickups": {"minecraft:cobblestone": 0},
    }
    executor.decide({"tick": 2, "body": body}, cells)
    assert executor.state == "failed" and executor.places_confirmed == 0
