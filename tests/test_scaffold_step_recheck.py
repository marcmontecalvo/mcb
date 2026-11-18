from mclab.navigation_segmented import scaffold_step_matches
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
