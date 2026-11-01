# ruff: noqa: E501
"""M2 body-side live check: place_block, use_block and break_block on the lab server (development only)."""

import sys

from live_common import (
    at,
    call,
    count,
    feet_cell,
    finish,
    kit,
    open_bridge,
    results_folder,
    spawn_ready,
    state_matches,
)  # fmt: skip

from mclab.recorder import exclusive_arena

STAIRS = "minecraft:oak_stairs"
FACINGS = ["north", "east", "south", "west"]


def main() -> int:
    folder = results_folder()
    rows: list[dict] = []

    def record(case, ok, detail):
        rows.append({"case": case, "pass": bool(ok), "detail": str(detail)})

    with exclusive_arena():
        bridge = open_bridge(folder)
        try:
            feet = feet_cell(spawn_ready(bridge))
            kit(
                bridge,
                clear=[7, 4, 7],
                inventory=[
                    {"item": "minecraft:cobblestone", "count": 40},
                    {"item": STAIRS, "count": 10},
                    {"item": "minecraft:oak_log", "count": 6},
                    {"item": "minecraft:oak_slab", "count": 4},
                    {"item": "minecraft:torch", "count": 6},
                    {"item": "minecraft:oak_door", "count": 4},
                    {"item": "minecraft:oak_trapdoor", "count": 4},
                    {"item": "minecraft:wooden_pickaxe", "count": 1},
                ],
            )

            def place(state, pos, **extra):
                return call(bridge, "place_block", pos=pos, state=state, **extra)

            def reset(*offsets):
                kit(
                    bridge,
                    place=[
                        {"state": "minecraft:air", "offset": list(o)} for o in offsets
                    ],
                )

            def expect(case, state, offset, reason=None, then=None):
                """Places `state` at the offset from the feet cell and records pass/fail."""
                r = place(state, at(feet, *offset))
                if reason is None:
                    ok = r["type"] == "place_block_done" and state_matches(
                        state, r["placed"]
                    )
                    detail = f"placed {r.get('placed', r.get('reason'))} face={r.get('face')} yaw={r.get('yaw')} pitch={round(r.get('pitch', 0), 1)}"
                else:
                    ok = r.get("reason") == reason
                    detail = f"{r.get('reason')} {r.get('placed', '')}".strip()
                record(case, ok, detail)
                return r

            # Support: a cobblestone wall two blocks ahead (south), then three pillars around the body.
            wall = [(x, y, 2) for y in (0, 1) for x in range(-2, 3)]
            pillars = [(0, 0, -3), (3, 0, 0), (-3, 0, 0)]
            for off in wall + pillars:
                expect(f"support cobblestone {off}", "minecraft:cobblestone", off)

            cell = (0, 0, 1)
            for half in ("bottom", "top"):
                for facing in FACINGS:
                    expect(
                        f"stairs facing={facing} half={half}",
                        f"{STAIRS}[facing={facing},half={half},shape=straight,waterlogged=false]",
                        cell,
                    )
                    reset(cell)
            expect(
                "log axis=x without a side support",
                "minecraft:oak_log[axis=x]",
                cell,
                reason="state_unreachable",
            )
            expect("support for the x-axis log", "minecraft:cobblestone", (1, 0, 1))
            for axis in "xyz":
                expect(f"log axis={axis}", f"minecraft:oak_log[axis={axis}]", cell)
                reset(cell)
            reset((1, 0, 1))
            expect(
                "slab type=bottom",
                "minecraft:oak_slab[type=bottom,waterlogged=false]",
                cell,
            )
            reset(cell)
            expect(
                "slab type=top", "minecraft:oak_slab[type=top,waterlogged=false]", cell
            )
            reset(cell)

            expect("standing torch", "minecraft:torch", cell)
            reset(cell)
            for facing, off in (
                ("north", cell),
                ("south", (0, 0, -2)),
                ("west", (2, 0, 0)),
                ("east", (-2, 0, 0)),
            ):
                expect(
                    f"wall torch facing={facing}",
                    f"minecraft:wall_torch[facing={facing}]",
                    off,
                )
                reset(off)

            for hinge in ("left", "right"):
                expect(
                    f"door lower hinge={hinge}",
                    f"minecraft:oak_door[facing=north,half=lower,hinge={hinge},open=false,powered=false]",
                    cell,
                )
                reset(cell, (0, 1, 1))

            for half in ("bottom", "top"):
                r = expect(
                    f"trapdoor half={half}",
                    f"minecraft:oak_trapdoor[half={half},facing=north,open=false,powered=false,waterlogged=false]",
                    cell,
                )
                if r["type"] == "place_block_done":
                    u = call(bridge, "use_block", pos=at(feet, *cell))
                    record(
                        f"use_block opens trapdoor half={half}",
                        u["type"] == "use_block_done"
                        and "open=true" in u.get("after", ""),
                        f"{u.get('before')} -> {u.get('after', u.get('reason'))}",
                    )
                reset(cell)

            # Refusals
            expect(
                "place on an occupied cell",
                "minecraft:cobblestone",
                (0, 0, 2),
                reason="occupied",
            )
            expect(
                "place with no support nearby",
                "minecraft:cobblestone",
                (0, 3, 1),
                reason="no_support_face",
            )
            expect(
                "place an item not carried",
                "minecraft:diamond_block",
                cell,
                reason="missing_item",
            )
            kit(bridge, place=[{"state": "minecraft:cobblestone", "offset": [0, 0, 9]}])
            r = call(bridge, "break_block", pos=at(feet, 0, 0, 9))
            record(
                "break out of reach", r.get("reason") == "out_of_reach", r.get("reason")
            )

            # break_block
            target = at(feet, 0, 1, 2)
            r = call(bridge, "break_block", pos=target, tool="minecraft:oak_log")
            record(
                "break cobblestone with a log -> wrong_tool",
                r.get("reason") == "wrong_tool",
                r.get("reason"),
            )
            before = count(r.get("inventory", []), "minecraft:wooden_pickaxe")
            r = call(bridge, "break_block", pos=target, timeout=40)
            record(
                "break cobblestone (auto pickaxe)",
                r["type"] == "break_block_done"
                and r.get("broken") == "minecraft:cobblestone",
                f"{r.get('broken', r.get('reason'))} ticks={r.get('ticks')} pickaxes={before}",
            )
            air = call(bridge, "break_block", pos=target)
            record(
                "break air -> nothing_to_break",
                air.get("reason") == "nothing_to_break",
                air.get("reason"),
            )
        finally:
            try:
                bridge.send("despawn")
            finally:
                bridge.close()
    return finish(folder, rows, "check-placement")


if __name__ == "__main__":
    sys.exit(main())
