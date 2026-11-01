import json
import math
import sys

import pytest

from mclab import cli, nbt, schematic
from mclab.schematic import AIR, load_litematic, state_materials


def pack_independent(indices, bits):
    """Straightforward packing: one big integer, entry i at bit offset i*bits."""
    big = 0
    for i, value in enumerate(indices):
        big |= value << (i * bits)
    total = -(-len(indices) * bits // 64)
    longs = [(big >> (64 * k)) & (2**64 - 1) for k in range(total)]
    return [v - 2**64 if v >= 2**63 else v for v in longs]  # signed like NBT


def vec(x, y, z):
    return (
        nbt.COMPOUND,
        {k: (nbt.INT, v) for k, v in zip("xyz", (x, y, z), strict=True)},
    )


def region(pos, size, states, indices):
    palette = []
    for name, props in states:
        entry = {"Name": (nbt.STRING, name)}
        if props:
            entry["Properties"] = (
                nbt.COMPOUND,
                {k: (nbt.STRING, v) for k, v in props.items()},
            )
        palette.append(entry)
    bits = max(2, math.ceil(math.log2(len(states))))
    return (
        nbt.COMPOUND,
        {
            "Position": vec(*pos),
            "Size": vec(*size),
            "BlockStatePalette": (nbt.LIST, (nbt.COMPOUND, palette)),
            "BlockStates": (nbt.LONG_ARRAY, pack_independent(indices, bits)),
        },
    )


def write_litematic(path, regions, version=6):
    root = {
        "Version": (nbt.INT, version),
        "MinecraftDataVersion": (nbt.INT, 4671),
        "Metadata": (nbt.COMPOUND, {"Name": (nbt.STRING, "Test")}),
        "Regions": (nbt.COMPOUND, regions),
    }
    path.write_bytes(nbt.dumps(root))
    return path


STATES = [(AIR, None)] + [(f"minecraft:block_{i}", None) for i in range(1, 11)]


def test_bit_spanning_matches_independent_packer(tmp_path):
    # 11 palette entries -> 4 bits; 27 cells * 4 = 108 bits, spans longs
    indices = [(i * 7 + 3) % 11 for i in range(27)]
    path = write_litematic(
        tmp_path / "a.litematic", {"r": region((0, 0, 0), (3, 3, 3), STATES, indices)}
    )
    s = load_litematic(path)
    assert s.size == (3, 3, 3)
    for y in range(3):
        for z in range(3):
            for x in range(3):
                want = STATES[indices[(y * 3 + z) * 3 + x]][0]
                assert s.state_at(x, y, z) == want


def test_odd_bit_width_spans_longs(tmp_path):
    # 5 bits (17..32 entries): the 13th entry straddles the first long boundary
    states = [(AIR, None)] + [(f"minecraft:b{i}", None) for i in range(1, 20)]
    indices = [i % 20 for i in range(60)]
    path = write_litematic(
        tmp_path / "b.litematic", {"r": region((0, 0, 0), (60, 1, 1), states, indices)}
    )
    s = load_litematic(path)
    assert [s.state_at(x, 0, 0) for x in range(60)] == [states[i][0] for i in indices]
    assert any((i * 5) % 64 + 5 > 64 for i in range(60))  # the test really spans


def test_negative_size_and_two_regions_merge(tmp_path):
    stone, dirt = ("minecraft:stone", None), ("minecraft:dirt", None)
    # a: Position (10,5,0), size (-2,1,1) covers x 9..10
    a = region((10, 5, 0), (-2, 1, 1), [(AIR, None), stone], [1, 1])
    # b: Position (12,5,0), size (1,1,-2) covers x 12, z -1..0
    b = region((12, 5, 0), (1, 1, -2), [(AIR, None), dirt], [1, 1])
    s = load_litematic(write_litematic(tmp_path / "m.litematic", {"a": a, "b": b}))
    assert s.size == (4, 1, 2)  # x 9..12, z -1..0; min corner (9, 5, -1)
    assert s.state_at(0, 0, 1) == "minecraft:stone"
    assert s.state_at(1, 0, 1) == "minecraft:stone"
    assert s.state_at(3, 0, 0) == "minecraft:dirt"
    assert s.state_at(3, 0, 1) == "minecraft:dirt"
    assert s.state_at(2, 0, 0) == AIR  # gap between regions


def test_states_sorted_and_air_variants_normalised(tmp_path):
    states = [
        ("minecraft:cave_air", None),
        ("minecraft:oak_stairs", {"half": "bottom", "facing": "north"}),
    ]
    path = write_litematic(
        tmp_path / "s.litematic", {"r": region((0, 0, 0), (2, 1, 1), states, [0, 1])}
    )
    s = load_litematic(path)
    assert s.palette[0] == AIR
    assert s.state_at(1, 0, 0) == "minecraft:oak_stairs[facing=north,half=bottom]"
    assert s.state_at(0, 0, 0) == AIR


def test_unsupported_version(tmp_path):
    p = write_litematic(
        tmp_path / "v.litematic",
        {"r": region((0, 0, 0), (1, 1, 1), STATES, [0])},
        version=3,
    )
    with pytest.raises(ValueError):
        load_litematic(p)


def mat(state, blocks=None):
    items, flag = state_materials(state, blocks)
    return dict(items), flag


def test_special_cases():
    assert mat("minecraft:oak_slab[type=double]")[0] == {"minecraft:oak_slab": 2}
    assert mat("minecraft:oak_slab[type=top]")[0] == {"minecraft:oak_slab": 1}
    assert mat("minecraft:oak_door[half=lower]")[0] == {"minecraft:oak_door": 1}
    assert mat("minecraft:oak_door[half=upper]")[0] == {}
    assert mat("minecraft:lilac[half=upper]")[0] == {}
    assert mat("minecraft:oak_stairs[half=top]")[0] == {"minecraft:oak_stairs": 1}
    assert mat("minecraft:red_bed[part=foot]")[0] == {"minecraft:red_bed": 1}
    assert mat("minecraft:red_bed[part=head]")[0] == {}
    assert mat("minecraft:candle[candles=3]")[0] == {"minecraft:candle": 3}
    assert mat("minecraft:red_candle[candles=2]")[0] == {"minecraft:red_candle": 2}
    assert mat("minecraft:sea_pickle[pickles=4]")[0] == {"minecraft:sea_pickle": 4}
    assert mat("minecraft:snow[layers=5]")[0] == {"minecraft:snow": 5}
    assert mat("minecraft:wall_torch[facing=north]")[0] == {"minecraft:torch": 1}
    assert mat("minecraft:soul_wall_torch")[0] == {"minecraft:soul_torch": 1}
    assert mat("minecraft:redstone_wall_torch")[0] == {"minecraft:redstone_torch": 1}
    assert mat("minecraft:oak_wall_sign")[0] == {"minecraft:oak_sign": 1}
    assert mat("minecraft:oak_wall_hanging_sign")[0] == {
        "minecraft:oak_hanging_sign": 1
    }
    assert mat("minecraft:white_wall_banner")[0] == {"minecraft:white_banner": 1}
    assert mat("minecraft:skeleton_wall_skull")[0] == {"minecraft:skeleton_skull": 1}
    assert mat("minecraft:player_wall_head")[0] == {"minecraft:player_head": 1}
    assert mat("minecraft:cobblestone_wall")[0] == {"minecraft:cobblestone_wall": 1}
    assert mat("minecraft:water[level=0]")[0] == {"minecraft:water_bucket": 1}
    assert mat("minecraft:water[level=3]")[0] == {}
    assert mat("minecraft:lava[level=0]")[0] == {"minecraft:lava_bucket": 1}
    for air in ("air", "cave_air", "void_air"):
        assert mat("minecraft:" + air) == ({}, None)
    assert mat("minecraft:stone[waterlogged=true]")[0] == {"minecraft:stone": 1}


def test_flags():
    for block in (
        "piston_head",
        "moving_piston",
        "fire",
        "nether_portal",
        "end_portal",
        "bubble_column",
        "attached_melon_stem",
    ):
        items, flag = mat("minecraft:" + block)
        assert items == {} and flag["reason"] == "no_item"
    items, flag = mat("minecraft:farmland[moisture=0]")
    assert items == {"minecraft:dirt": 1} and "hoe" in flag["how"]
    items, flag = mat("minecraft:dirt_path")
    assert items == {"minecraft:dirt": 1} and "shovel" in flag["how"]


def test_blocks_json_item_field_wins():
    blocks = {
        "minecraft:weird": {"item": "minecraft:strange"},
        "minecraft:ghost": {"item": None},
    }
    assert mat("minecraft:weird", blocks)[0] == {"minecraft:strange": 1}
    assert mat("minecraft:ghost", blocks)[1]["reason"] == "no_item"


def test_build_json_round_trip_and_cli(tmp_path, capsys, monkeypatch):
    states = [
        (AIR, None),
        ("minecraft:oak_slab", {"type": "double"}),
        ("minecraft:farmland", {"moisture": "0"}),
        ("minecraft:stone", None),
    ]
    indices = [1, 2, 3, 0, 1, 3, 3, 0]
    src = write_litematic(
        tmp_path / "h.litematic", {"r": region((0, 0, 0), (2, 2, 2), states, indices)}
    )
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "blocks.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "blocks": {"minecraft:stone": {"item": "minecraft:cobblestone"}},
            }
        )
    )
    out = tmp_path / "h.build.json"
    argv = [
        "mclab",
        "schematic",
        "convert",
        str(src),
        "-o",
        str(out),
        "--knowledge",
        str(pack),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 0
    assert "minecraft:cobblestone" in capsys.readouterr().out
    data = json.loads(out.read_text())
    assert data["palette"][0] == AIR and data["size"] == [2, 2, 2]
    assert data["materials"] == {
        "minecraft:oak_slab": 4,
        "minecraft:cobblestone": 3,
        "minecraft:dirt": 1,
    }
    assert data["flags"][0]["count"] == 1
    back = schematic.from_build_json(data)
    original = load_litematic(src)
    assert back == original
    assert back.state_at(1, 1, 1) == AIR  # index 7 is air


HOUSE = "tests/fixtures/schematics/house-154fc7cd.litematic"


def test_real_house_matches_file_metadata_and_counts():
    schem = load_litematic(HOUSE)
    total = nbt.load(HOUSE)["Metadata"]["TotalBlocks"]
    materials, flags = schematic.material_list(schem)

    assert list(schem.size) == [21, 15, 14]
    assert sum(1 for i in schem.cells if schem.palette[i] != AIR) == total == 1227
    assert materials["minecraft:red_bed"] == 2  # foot halves only
    assert materials["minecraft:spruce_door"] == 2  # lower halves only
    assert materials["minecraft:oak_sign"] == 4  # wall signs
    assert materials["minecraft:flower_pot"] == 2
    assert materials["minecraft:blue_orchid"] == 1
    assert materials["minecraft:lily_of_the_valley"] == 1
    assert flags == []


def test_potted_plant_counts_pot_and_plant():
    assert mat("minecraft:potted_azalea_bush")[0] == {
        "minecraft:flower_pot": 1,
        "minecraft:azalea": 1,
    }
