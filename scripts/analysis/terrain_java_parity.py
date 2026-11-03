"""Compare Java's TerrainPlannerTest parity output with Python's terrain search.

Run the Java verification class with argument ``parity``, redirecting its stdout
to a file, then run from the repository root:

    uv run --locked python scripts/analysis/terrain_java_parity.py path/to/java-output.txt

The matching cases exercise stock, reservation, unknown anchors and protected
supports. They compare status, expansion count, route and ordered placements.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mclab.terrain_route import TerrainRouteSearch
from mclab.terrain_steps import TerrainPolicy


def point(position):
    return ",".join(str(value) for value in position)


def cases():
    for count in range(5):
        for reserve in range(2):
            for mode in range(3):
                cells = {}
                for x in range(-2, 3):
                    for y in range(-3, 6):
                        for z in range(-1, 7):
                            solid = (y < 0 and not (1 <= z <= 3 and y >= -2)) or (
                                abs(x) == 1 and y >= 0
                            )
                            name = "bedrock" if y >= 0 else "stone"
                            cells[x, y, z] = {
                                "block": "minecraft:" + name if solid else "minecraft:air",
                                "collision_boxes": [[0, 0, 0, 1, 1, 1]] if solid else [],
                            }
                if mode == 1:
                    del cells[0, -2, 2]
                policy = TerrainPolicy(
                    allow_break=False,
                    allow_bridge=True,
                    reserve_blocks=reserve,
                    protected_positions=((0, -1, 2),) if mode == 2 else (),
                )
                search = TerrainRouteSearch(
                    cells,
                    (0, 0, 0),
                    (0, 0, 4),
                    map_revision=1,
                    policy=policy,
                    inventory=[{"slot": 3, "item": "minecraft:cobblestone", "count": count}],
                )
                while True:
                    result = search.advance(node_limit=2, budget_ms=1000)
                    if result.status != "searching":
                        break
                route = ";".join(point(p) for p in result.route)
                steps = ";".join(
                    point(step.target) + ":" + "/".join(point(p) for p in step.places)
                    for step in result.steps
                )
                yield f"{count}|{reserve}|{mode}|{result.status}|{result.expanded}|{route}|{steps}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("java_output", type=Path)
    args = parser.parse_args()
    actual = args.java_output.read_text(encoding="utf-8-sig").splitlines()
    expected = list(cases())
    if actual != expected:
        for index in range(max(len(actual), len(expected))):
            java = actual[index] if index < len(actual) else "<missing>"
            python = expected[index] if index < len(expected) else "<missing>"
            if java != python:
                print(f"case {index}:\n  Java:   {java}\n  Python: {python}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Terrain planner parity: {len(expected)} cases matched")


if __name__ == "__main__":
    main()
