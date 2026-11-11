import sys

sys.path.insert(0, "d:/mcb/src")
from mclab.movement_policy import MovementPolicy  # noqa: E402
from mclab.route_search import RouteSearch  # noqa: E402
from mclab.terrain_steps import TerrainPolicy  # noqa: E402

AIR, STONE, DIRT = "minecraft:air", "minecraft:stone", "minecraft:dirt"


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
    {"slot": 3, "item": "minecraft:dirt", "count": 64},
)


def shaft(sealed, depth):
    """Vertical 1x1 shaft at (0,0): bot feet at y=0..; solid rock all around at
    y<=0 and walls (stone) on the four sides up to ground. Ground top y=0. Bot
    dug DOWN so feet at y=-(depth). Head y=-(depth)+1. Opening at y=0 sealed."""
    cells = {}
    for x in range(-2, 3):
        for z in range(-2, 3):
            for y in range(-6, 4):
                cells[(x, y, z)] = cell((x, y, z), STONE if y < 0 else AIR)
    # carve the shaft column from floor to just below ground
    for yy in range(-depth, 0):
        cells[(0, yy, 0)] = cell((0, yy, 0), AIR)
    cells[(0, 0, 0)] = cell((0, 0, 0), DIRT if sealed else AIR)
    # the shaft walls remain stone; bot stands at (0,-depth,0)
    feet = (0, -depth, 0)
    return cells, feet


def run(cells, start, goal, **pol):
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
            **pol,
        ),
        inventory=KIT,
        movement_policy=MovementPolicy(),
    )
    while (
        r := s.advance(node_limit=8192, budget_ms=2000, segment_length=200)
    ).status == "searching":
        pass
    return r


for sealed in (True, False):
    for depth in (2,):
        cells, feet = shaft(sealed, depth)
        r = run(cells, feet, (0, 0, 2))
        print(
            "sealed" if sealed else "open  ",
            "depth",
            depth,
            "status:",
            r.status,
            "reason:",
            r.reason,
            "expanded:",
            r.expanded,
        )
        print("   route:", r.route)
        for st in r.steps:
            print(
                "   step:",
                st.kind,
                st.source,
                "->",
                st.target,
                "breaks:",
                st.breaks,
                "places:",
                st.places,
            )
