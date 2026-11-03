r"""Generate shared planner cases or compare Java JSONL against Python RouteSearch.

    uv run --locked python scripts/analysis/route_differential.py generate cases.json
    # From PowerShell with the project's JDK/Gradle cache configured:
    .\mcb-body\gradlew.bat -p mcb-body --offline -q routeDifferential '-Pargs=cases.json' |
      Where-Object { $_.StartsWith('{') } | Out-File -Encoding utf8 java.jsonl
    uv run --locked python scripts/analysis/route_differential.py compare cases.json java.jsonl

Cases share the exact observed cells and policy flags. A discrepancy is reported,
not silently normalized; costs use a small floating-point tolerance.
"""

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from mclab.movement_policy import MovementPolicy
from mclab.route_search import RouteSearch
from mclab.terrain_steps import TerrainPolicy

PALETTE = [
    {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []},
    {
        "block": "minecraft:stone",
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0, 0, 1, 1, 1]],
    },
    {
        "block": "minecraft:stone_slab",
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0, 0, 1, 0.5, 1]],
    },
    {"block": "minecraft:fire", "fluid": "minecraft:empty", "collision_boxes": []},
]


def cases():
    default = dict(
        ticks=True,
        search=True,
        bdiag=True,
        diag=True,
        shapes=True,
        unknown=False,
        hazard=0,
        mob=1,
    )
    variants = {
        "base": {},
        "no_tick": {"ticks": False},
        "no_search": {"search": False},
        "no_bdiag": {"bdiag": False},
        "no_diag": {"diag": False},
        "no_shapes": {"shapes": False},
        "unknown": {"unknown": True},
        "no_survival": {"hazard": 0, "mob": 1},
        "legacy": dict(
            ticks=False, search=False, bdiag=False, diag=False, shapes=False
        ),
    }
    for scene, (name, changes) in itertools.product(
        (
            "open",
            "wall",
            "corner",
            "slab",
            "unknown",
            "interaction",
            "sight_unknown",
            "occluded",
            "hazard",
            "mob",
        ),
        variants.items(),
    ):
        opt = default | changes
        opt["sprint"] = scene != "corner"
        cells = {
            (x, y, z): (1 if y == 0 else 0)
            for x in range(-3, 15 if scene == "unknown" else 10)
            for y in range(-1, 6)
            for z in range(-4, 8)
        }
        start, goal, reach, threats = (0, 1, 0), (7, 1, 3), None, []
        if scene == "wall":
            for z in range(-2, 4):
                for y in (1, 2, 3):
                    cells[3, y, z] = 1
        if scene == "corner":
            goal = (1, 1, 1)
            cells[1, 1, 0] = 1
            cells[1, 2, 0] = 1
        if scene == "slab":
            for z in range(-3, 7):
                cells[3, 1, z] = 2
        if scene == "unknown":
            goal = (13, 1, 3)
            for x in range(10, 15):
                for y in range(-1, 6):
                    for z in range(-4, 8):
                        cells.pop((x, y, z), None)
        if scene in ("interaction", "sight_unknown", "occluded"):
            goal, reach = (7, 1, 3), 3.0
            cells[goal] = 1
        if scene == "sight_unknown":
            cells.pop((6, 2, 3))
        if scene == "occluded":
            for z in range(-1, 6):
                for y in (1, 2, 3):
                    cells[5, y, z] = 1
            cells.pop((5, 2, 5))
        if scene == "hazard":
            opt["hazard"] = 0 if name == "no_survival" else 15
            for x in range(2, 6):
                cells[x, 1, 2] = 3
        if scene == "mob":
            opt["mob"] = 1 if name == "no_survival" else 3
            threats = [[3.5, 1, 1.5]]
        yield dict(
            id=f"{scene}/{name}",
            start=start,
            goal=goal,
            reach=reach,
            threats=threats,
            options=opt,
            palette=PALETTE,
            cells=[[*p, value] for p, value in cells.items()],
        )


def python_result(case):
    opts = case["options"]
    cells = {tuple(row[:3]): case["palette"][row[3]] for row in case["cells"]}
    movement = MovementPolicy(
        allow_diagonals=opts["diag"],
        allow_sprint=opts["sprint"],
        max_drop=1,
        allow_shapes=opts["shapes"],
    )
    policy = TerrainPolicy(
        planner_tick_costs=opts["ticks"],
        baritone_search=opts["search"],
        baritone_diagonals=opts["bdiag"],
        planner_unknown=opts["unknown"],
        heuristic_weight=1,
        hazard_penalty=opts["hazard"],
        mob_coefficient=opts["mob"],
        mob_radius=3,
    )
    search = RouteSearch(
        cells,
        case["start"],
        case["goal"],
        map_revision=1,
        movement_policy=movement,
        terrain_policy=policy,
        interact_reach=case["reach"],
        threats=case["threats"],
    )
    while True:
        result = search.advance(node_limit=10000, budget_ms=1000, segment_length=10000)
        if result.status != "searching":
            break
    cost = search.costs.get(result.route[-1], 0) if result.route else 0
    return dict(
        id=case["id"],
        status=result.status,
        route=result.route,
        expanded=result.expanded,
        cost=cost,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "compare"))
    parser.add_argument("cases", type=Path)
    parser.add_argument("java_output", type=Path, nargs="?")
    args = parser.parse_args()
    if args.action == "generate":
        data = list(cases())
        args.cases.write_text(json.dumps(data), encoding="utf-8")
        print(f"Generated {len(data)} cases")
        return
    if args.java_output is None:
        parser.error("compare requires java_output")
    data = json.loads(args.cases.read_text(encoding="utf-8-sig"))
    java = [
        json.loads(line)
        for line in args.java_output.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if len(data) != len(java):
        raise SystemExit(f"Expected {len(data)} Java results, found {len(java)}")
    failures = 0
    for case, actual in zip(data, java, strict=True):
        expected = python_result(case)
        expected["route"] = [list(p) for p in expected["route"]]
        differences = [
            key
            for key in ("id", "status", "route", "expanded")
            if actual[key] != expected[key]
        ]
        if not math.isclose(
            actual["cost"], expected["cost"], rel_tol=1e-8, abs_tol=1e-8
        ):
            differences.append("cost")
        if differences:
            failures += 1
            print(
                f"{case['id']}: {', '.join(differences)}\n  Java {actual}\n  Python {expected}"
            )
    print(f"{len(data) - failures}/{len(data)} cases match")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
