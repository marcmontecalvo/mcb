import sys

sys.path.insert(0, "d:/mcb/src")
from mclab.movement_policy import MovementPolicy  # noqa: E402
from mclab.route_search import RouteSearch  # noqa: E402
from mclab.terrain_steps import TerrainPolicy  # noqa: E402

AIR, STONE = "minecraft:air", "minecraft:stone"


def cell(p, block, fluid="minecraft:empty"):
    boxes = [] if block == AIR else [[0, 0, 0, 1, 1, 1]]
    return {
        "position": list(p),
        "block": block,
        "fluid": fluid,
        "collision_boxes": boxes,
        "properties": {"level": "0"},
    }


KIT = (
    {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
    {"slot": 3, "item": "minecraft:cobblestone", "count": 64},
)


def world(sealed):
    cells = {}
    for x in range(-2, 3):
        for z in range(-2, 3):
            for y in range(-1, 6):
                solid = y <= 1
                cells[(x, y, z)] = cell((x, y, z), STONE if solid else AIR)
    # the hole: column (0,0) broken from y=1 and y=0 (bot dug down twice)
    cells[(0, 1, 0)] = cell((0, 1, 0), AIR)
    cells[(0, 0, 0)] = cell((0, 0, 0), AIR)
    if sealed:
        cells[(0, 2, 0)] = cell((0, 2, 0), STONE)  # the seal at ground level
    return cells


def run(cells, start, goal):
    s = RouteSearch(
        cells,
        start,
        goal,
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
    while (
        r := s.advance(node_limit=4096, budget_ms=1000, segment_length=200)
    ).status == "searching":
        pass
    return r, s


for sealed in (False, True):
    r, s = run(world(sealed), (0, 0, 0), (0, 2, 2))
    print("sealed" if sealed else "open  ", "status:", r.status, "expanded:", r.expanded,
          "reason:", r.reason)
    print("   route:", r.route)
    for st in r.steps:
        print("   step:", st.kind, st.source, "->", st.target,
              "breaks:", st.breaks, "places:", st.places)
