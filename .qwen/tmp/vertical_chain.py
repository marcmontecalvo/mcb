import sys

sys.path.insert(0, "d:/mcb/src")
from mclab.movement_policy import MovementPolicy  # noqa: E402
from mclab.route_search import RouteSearch  # noqa: E402
from mclab.terrain_steps import TerrainPolicy  # noqa: E402

AIR, STONE, DIRT = "minecraft:air", "minecraft:stone", "minecraft:dirt"


def cell(p, block):
    return {
        "position": list(p),
        "block": block,
        "fluid": "minecraft:empty",
        "collision_boxes": [] if block == AIR else [[0, 0, 0, 1, 1, 1]],
        "properties": {"level": "0"},
    }


KIT = (
    {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
    {"slot": 3, "item": "minecraft:dirt", "count": 64},
)

# A shaft with obsidian (unbreakable) side walls so the ONLY way out is vertical
# in the same column: pillar up, and where a stone cap blocks the column above,
# break it, then pillar more. Surface at y=0; dug shaft y=-1..-4; a stone cap at
# y=1 (so the bot must break straight up, then keep rising).
cells = {}
for x in range(-2, 3):
    for z in range(-2, 3):
        for y in range(-6, 6):
            if y <= 0:
                blk = STONE if (x, z) != (0, 0) else AIR  # shaft carved below ground
            else:
                blk = STONE if (x, z) != (0, 0) else AIR  # open above
            cells[(x, y, z)] = cell((x, y, z), blk)

# make side walls unbreakable so movement is forced vertical (use obsidian)
from mclab.route_search import RouteSearch as RS  # noqa

for x in range(-2, 3):
    for z in range(-2, 3):
        for y in range(-6, 2):
            if (x, z) != (0, 0):
                cells[(x, y, z)] = cell((x, y, z), "minecraft:obsidian")
# floor of shaft unbreakable too
cells[(0, -5, 0)] = cell((0, -5, 0), "minecraft:obsidian")

import os

if os.environ.get("CAP"):
    # bot stands on solid floor at feet y=-1; dirt cap directly over its head
    cells[(0, -2, 0)] = cell((0, -2, 0), STONE)
    cells[(0, 1, 0)] = cell((0, 1, 0), DIRT)
    s_goal = (0, 3, 0)
else:
    s_goal = (0, 2, 0)

s = RS(
    cells,
    (0, -4, 0) if not os.environ.get("CAP") else (0, -1, 0),
    s_goal,
    map_revision=1,
    terrain_policy=TerrainPolicy(
        planner_tick_costs=True,
        planner_edits=True,
        allow_pillar=True,
        allow_place=True,
        allow_break=True,
        baritone_moves=True,
    ),
    inventory=KIT,
    movement_policy=MovementPolicy(),
)
while (r := s.advance(node_limit=8192, budget_ms=2000, segment_length=200)).status == "searching":
    pass
print("status:", r.status, "reason:", r.reason, "expanded:", r.expanded)
print("route:", r.route)
for st in r.steps:
    print("  step:", st.kind, st.source, "->", st.target, "breaks:", st.breaks, "places:", st.places)
