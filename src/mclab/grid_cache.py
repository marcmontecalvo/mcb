"""Cell classification shared by the movement and terrain helpers.

``classify`` turns a cell dict into flag bits. Callers that hold a memo table
(``cells.cflags``) look it up with ``getattr``; plain dicts classify per call.
"""

AIR_FLUID = "minecraft:empty"
FALLING = {
    "minecraft:sand",
    "minecraft:red_sand",
    "minecraft:gravel",
    "minecraft:suspicious_sand",
    "minecraft:suspicious_gravel",
}
HAZARDS = {
    "minecraft:fire",
    "minecraft:soul_fire",
    "minecraft:cactus",
    "minecraft:magma_block",
    "minecraft:campfire",
    "minecraft:soul_campfire",
    "minecraft:powder_snow",
    "minecraft:sweet_berry_bush",
    "minecraft:wither_rose",
    "minecraft:bubble_column",  # magma-driven columns pull the body down
}
CLIMBABLE = {
    "minecraft:ladder",
    "minecraft:vine",
    "minecraft:scaffolding",
    "minecraft:twisting_vines",
    "minecraft:twisting_vines_plant",
    "minecraft:weeping_vines",
    "minecraft:weeping_vines_plant",
    "minecraft:cave_vines",
    "minecraft:cave_vines_plant",
}

# Flag bits of one cell.
KNOWN = 1  # cell is not None
EMPTY = 2  # navigation.empty
FULL = 4  # navigation.full_cube
SAFE = 8  # collision_geometry.safe_cell
SOLID = 16  # collision_boxes is non-empty (truthy)
LAVA = 32  # lava fluid or hazard block (an edge onto it is refused)
WET = 64  # fluid other than empty
FALLS = 128  # gravity block
CLIMB = 256  # block in CLIMBABLE
HAS_BOXES = 512  # "collision_boxes" key present (clear_body)


def classify(cell):
    """Flag bits of a cell dict (0 for an unknown cell)."""
    if cell is None:
        return 0
    fluid = cell.get("fluid", AIR_FLUID)
    block = cell.get("block")
    boxes = cell.get("collision_boxes")
    dry = fluid == AIR_FLUID
    flags = KNOWN
    if dry:
        if block == "minecraft:air" or boxes == []:
            flags |= EMPTY
        if block not in HAZARDS:
            flags |= SAFE
        if block in CLIMBABLE:
            flags |= CLIMB
        for box in boxes or ():
            if box == [0, 0, 0, 1, 1, 1]:
                flags |= FULL
                break
    else:
        flags |= WET
    if boxes:
        flags |= SOLID
    if "collision_boxes" in cell:
        flags |= HAS_BOXES
    if "lava" in str(cell.get("fluid", "")) or block in HAZARDS:
        flags |= LAVA
    if block in FALLING:
        flags |= FALLS
    return flags

