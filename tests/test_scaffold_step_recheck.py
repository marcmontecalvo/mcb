from mclab.navigation_segmented import (
    NATIVE_EDIT_KINDS,
    NativeEdit,
    executor_for,
    native_edit_matches,
    scaffold_step_matches,
)
from mclab.terrain_steps import StepPlan, TerrainPolicy

AIR = {"block": "minecraft:air", "collision_boxes": []}
STONE = {"block": "minecraft:stone", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
POLICY = TerrainPolicy(
    allow_place=True,
    material_allowlist=("minecraft:dirt",),
    scaffold_materials=("minecraft:scaffolding",),
)
STEP = StepPlan(
    (0, 1, 0), (1, 2, 0), places=((1, 1, 0),), kind="step", scaffold=((1, 1, 0),)
)
SCAFFOLD = [{"slot": 0, "item": "minecraft:scaffolding", "count": 64}]


def cells(**changes):
    world = {(1, 1, 0): AIR, (1, 2, 0): AIR, (1, 3, 0): AIR, (1, 0, 0): STONE}
    world.update(changes)
    return world


def test_a_scaffolding_step_holds_with_only_scaffolding_held():
    # The Python step rules alone would refuse: no solid block is held.
    assert scaffold_step_matches(STEP, cells(), POLICY, SCAFFOLD)


def test_a_scaffolding_step_is_refused_when_its_cell_filled_or_went_unknown():
    assert not scaffold_step_matches(
        STEP, {**cells(), (1, 1, 0): STONE}, POLICY, SCAFFOLD
    )
    world = cells()
    del world[(1, 1, 0)]
    assert not scaffold_step_matches(STEP, world, POLICY, SCAFFOLD)


def test_a_scaffolding_step_is_refused_without_scaffolding_or_onto_protected_cells():
    assert not scaffold_step_matches(STEP, cells(), POLICY, [])
    guarded = TerrainPolicy(**{**POLICY.__dict__, "protected_positions": ((1, 1, 0),)})
    assert not scaffold_step_matches(STEP, cells(), guarded, SCAFFOLD)


def test_the_reserve_holds_back_solid_blocks_not_scaffolding():
    # review of e029b74: a reserve above the solid blocks held went negative and
    # refused a step that places scaffolding alone
    from dataclasses import replace

    reserved = replace(POLICY, reserve_blocks=4)
    assert scaffold_step_matches(STEP, cells(), reserved, SCAFFOLD)
    dirt = StepPlan((0, 1, 0), (1, 2, 0), places=((1, 1, 0),), kind="step")
    held = [*SCAFFOLD, {"slot": 1, "item": "minecraft:dirt", "count": 4}]
    assert not scaffold_step_matches(dirt, cells(), reserved, held)  # all reserved
    held[1]["count"] = 5
    assert scaffold_step_matches(dirt, cells(), reserved, held)


def test_scaffold_drop_is_a_native_edit_and_validates_shaft_geometry():
    assert "scaffold_drop" in NATIVE_EDIT_KINDS
    places = tuple((2, y, 0) for y in range(-10, 0))
    plan = StepPlan(
        (1, 0, 0),
        (2, -1, 0),
        places=places,
        kind="scaffold_drop",
        scaffold=places,
    )
    assert isinstance(executor_for(plan, POLICY), NativeEdit)
    shaft = {
        (1, -1, 0): STONE,
        (1, 0, 0): AIR,
        (1, 1, 0): AIR,
        (2, -11, 0): STONE,
        **{p: AIR for p in places},
        (2, 0, 0): AIR,
        (2, 1, 0): AIR,
    }
    supported = lambda c, p: p == (1, 0, 0)  # noqa: E731
    assert native_edit_matches(plan, shaft, supported)
    assert scaffold_step_matches(plan, shaft, POLICY, SCAFFOLD)
    # Insufficient scaffolding fails inventory check.
    short = [{"slot": 0, "item": "minecraft:scaffolding", "count": 9}]
    assert not scaffold_step_matches(plan, shaft, POLICY, short)
    # Horizontal scaffolding beside rim drop cell would bridge instead of falling.
    side_scaff = {
        **shaft,
        (2, -1, 1): {"block": "minecraft:scaffolding", "collision_boxes": []},
    }
    assert not native_edit_matches(plan, side_scaff, supported)
    # Non-solid rim floor fails.
    scaff_rim = {
        **shaft,
        (1, -1, 0): {"block": "minecraft:scaffolding", "collision_boxes": []},
    }
    assert not native_edit_matches(plan, scaff_rim, supported)
    # Blocked cell inside shaft fails.
    assert not native_edit_matches(plan, {**shaft, (2, -5, 0): STONE}, supported)
    # Stacking onto existing scaffolding at shaft bottom is valid.
    partial_places = tuple((2, y, 0) for y in range(-7, 0))
    partial = StepPlan(
        (1, 0, 0),
        (2, -1, 0),
        places=partial_places,
        kind="scaffold_drop",
        scaffold=partial_places,
    )
    partial_shaft = {
        **shaft,
        (2, -8, 0): {
            "block": "minecraft:scaffolding",
            "fluid": "minecraft:empty",
            "collision_boxes": [],
        },
    }
    assert native_edit_matches(partial, partial_shaft, supported)
