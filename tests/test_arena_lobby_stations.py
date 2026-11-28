"""The lobby room's physical stations (signs and buttons) and their Java twin."""

from __future__ import annotations

import json
import re
from pathlib import Path

from mclab.arena import designs, world
from mclab.arena import plan as ap

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "mcb-body/src/main/java/com/vulpine/mcb/arena/ArenaLobbyStations.java"
STATION = re.compile(
    r'new Station\("([a-z_]+)", (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+),'
)


def by_id():
    return {s.id: s for s in designs.LOBBY_STATIONS}


def test_the_stations_are_the_contracts_table():
    stations = by_id()
    assert list(stations) == [
        "join_red",
        "join_blue",
        "join_green",
        "join_yellow",
        "ready",
        "help",
    ]
    for team, z in (("red", 7), ("blue", 11), ("green", 15), ("yellow", 19)):
        st = stations[f"join_{team}"]
        assert st.sign == (2, 3, z) and st.button == (2, 2, z)
        assert st.facing == "east"
        assert st.backing_block == f"minecraft:{team}_concrete"
        assert st.backing == ((1, 2, z - 1), (1, 4, z + 1))
        assert st.payload == {"action": "join", "team": team}
    ready = stations["ready"]
    assert ready.sign == (22, 3, 12) and ready.button == (22, 2, 12)
    assert ready.facing == "west"
    assert ready.backing_block == "minecraft:lime_concrete"
    assert ready.backing == ((23, 2, 11), (23, 4, 13))
    assert ready.payload == {"action": "ready_toggle"}
    help_ = stations["help"]
    assert help_.sign == (12, 3, 22) and help_.button is None
    assert help_.facing == "north" and help_.backing is None
    assert help_.payload is None


def test_the_lobby_is_still_a_valid_room_with_the_stations_built_in():
    plan = designs.lobby()
    assert ap.validate(plan) == []
    assert designs.LOBBY_VERSION == 2
    grid = ap.voxelize(plan)
    for st in designs.LOBBY_STATIONS:
        x, y, z = st.sign
        assert grid.block(x, y, z) == (
            f"minecraft:dark_oak_wall_sign[facing={st.facing}]"
        )
        if st.button:
            bx, by, bz = st.button
            assert grid.block(bx, by, bz) == (
                f"minecraft:stone_button[face=wall,facing={st.facing}]"
            )
        if st.backing:
            (x0, y0, z0), (x1, y1, z1) = st.backing
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    for z in range(z0, z1 + 1):
                        assert grid.block(x, y, z) == st.backing_block
    # the sign hangs on the wall behind it
    assert grid.block(12, 3, 23) == "minecraft:quartz_bricks"


def test_the_old_coloured_block_stations_and_banners_are_gone():
    blocks = {f.block for f in designs.lobby().fills}
    assert not any("banner" in b for b in blocks)
    assert not any(
        b in blocks
        for b in (
            "minecraft:emerald_block",
            "minecraft:amethyst_block",
            "minecraft:gold_block",
            "minecraft:diamond_block",
            "minecraft:netherite_block",
            "minecraft:crying_obsidian",
        )
    )


def test_the_builder_helpers_write_block_states_and_follow_rotation():
    b = designs.Builder("t", (9, 9, 9))
    b.wall_sign(3, 3, 3, "dark_oak", "east")
    b.button(3, 2, 3, "stone", "east")
    with b.rotated(1, (4, 4)):
        b.wall_sign(5, 3, 5, "oak", "north")
        b.button(5, 2, 5, "oak", "north")
    blocks = {f.block for f in b.fills}
    assert "minecraft:dark_oak_wall_sign[facing=east]" in blocks
    assert "minecraft:stone_button[face=wall,facing=east]" in blocks
    assert "minecraft:oak_wall_sign[facing=east]" in blocks  # a quarter turn clockwise
    assert "minecraft:oak_button[face=wall,facing=east]" in blocks


def test_world_lists_the_stations_in_world_coordinates():
    rows = world.lobby_stations()
    assert [r["id"] for r in rows] == list(by_id())
    ox, oy, oz = world.LOBBY_ORIGIN
    red = rows[0]
    assert red["sign_world"] == [ox + 2, oy + 3, oz + 7]
    assert red["button_world"] == [ox + 2, oy + 2, oz + 7]
    assert rows[-1]["button_world"] is None
    json.dumps(rows)


def test_python_and_java_agree_on_every_station():
    """Needs the rewritten ``ArenaLobbyStations.java`` (the mod side of the menu)."""
    found = {
        m[0]: tuple(int(v) for v in m[1:])
        for m in STATION.findall(JAVA.read_text("utf-8"))
    }
    assert set(found) == set(by_id()), (
        "Java stations differ from designs.LOBBY_STATIONS"
    )
    for sid, st in by_id().items():
        button = st.button or (-1, -1, -1)
        assert found[sid] == (*st.sign, *button), sid
