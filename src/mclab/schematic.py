"""Schematic files -> the MCB build file (docs/KNOWLEDGE_PACK_FORMAT.md, "Build file").

Litematica (.litematic) is read today. Sponge .schem and vanilla structure .nbt
can be added as further ``_read_*`` functions that return a ``Schematic``.
"""

import base64
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from mclab import nbt

AIR = "minecraft:air"
_AIR_IDS = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}


@dataclass(frozen=True)
class Schematic:
    """size is (x, y, z); cells[(y * size_z + z) * size_x + x] indexes palette."""

    name: str
    source: str
    data_version: int | None
    size: tuple[int, int, int]
    palette: tuple[str, ...]  # state strings, entry 0 is minecraft:air
    cells: tuple[int, ...]

    def state_at(self, x: int, y: int, z: int) -> str:
        sx, sy, sz = self.size
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            raise IndexError((x, y, z))
        return self.palette[self.cells[(y * sz + z) * sx + x]]


# ---------------------------------------------------------------- states


def format_state(name: str, properties: dict | None) -> str:
    if not properties:
        return name
    inner = ",".join(f"{k}={properties[k]}" for k in sorted(properties))
    return f"{name}[{inner}]"


def parse_state(state: str) -> tuple[str, dict[str, str]]:
    name, _, rest = state.partition("[")
    props = dict(p.split("=", 1) for p in rest.rstrip("]").split(",") if p)
    return name, props


# ------------------------------------------------------------ litematica


def unpack_bits(longs: list[int], bits: int, count: int) -> list[int]:
    """LitematicaBitArray: entries are packed back to back and may span two longs."""
    mask = (1 << bits) - 1
    words = [v & 0xFFFFFFFFFFFFFFFF for v in longs]
    out = []
    for i in range(count):
        start = i * bits
        first, shift = start >> 6, start & 63
        value = words[first] >> shift
        if shift + bits > 64:
            value |= words[first + 1] << (64 - shift)
        out.append(value & mask)
    return out


def _vec(tag: dict) -> tuple[int, int, int]:
    return tag["x"], tag["y"], tag["z"]


def _read_region(region: dict):
    """Return (min_corner, abs_size, palette_states, indices) for one region."""
    pos, size = _vec(region["Position"]), _vec(region["Size"])
    # A negative size extends from Position toward negative coordinates.
    corner = tuple(p + (s + 1 if s < 0 else 0) for p, s in zip(pos, size, strict=True))
    dims = tuple(abs(s) for s in size)
    palette = [
        format_state(e["Name"], e.get("Properties"))
        for e in region["BlockStatePalette"]
    ]
    bits = max(2, math.ceil(math.log2(len(palette)))) if len(palette) > 1 else 2
    count = dims[0] * dims[1] * dims[2]
    return corner, dims, palette, unpack_bits(region["BlockStates"], bits, count)


def _canonical(state: str) -> str:
    return AIR if parse_state(state)[0] in _AIR_IDS else state


def load_litematic(path) -> Schematic:
    root = nbt.load(path)
    version = root.get("Version")
    if version not in (5, 6, 7):
        raise ValueError(f"unsupported litematic version {version}")
    regions = [_read_region(r) for r in root["Regions"].values()]
    if not regions:
        raise ValueError("litematic has no regions")
    low = tuple(min(c[a] for c, *_ in regions) for a in range(3))
    high = tuple(max(c[a] + d[a] for c, d, *_ in regions) for a in range(3))
    sx, sy, sz = (high[a] - low[a] for a in range(3))
    palette = [AIR]
    lookup = {AIR: 0}
    cells = [0] * (sx * sy * sz)
    for corner, dims, states, indices in regions:
        remap = []
        for state in map(_canonical, states):
            remap.append(lookup.setdefault(state, len(palette)))
            if remap[-1] == len(palette):
                palette.append(state)
        ox, oy, oz = (corner[a] - low[a] for a in range(3))
        n = 0
        for y in range(dims[1]):
            for z in range(dims[2]):
                base = ((oy + y) * sz + oz + z) * sx + ox
                for x in range(dims[0]):
                    cells[base + x] = remap[indices[n]]
                    n += 1
    if len(palette) > 0xFFFF:
        raise ValueError("palette too large for uint16 indices")
    meta = root.get("Metadata", {})
    return Schematic(
        name=meta.get("Name") or Path(path).stem,
        source=Path(path).name,
        data_version=root.get("MinecraftDataVersion"),
        size=(sx, sy, sz),
        palette=tuple(palette),
        cells=tuple(cells),
    )


def load_schematic(path) -> Schematic:
    if Path(path).suffix.lower() == ".litematic":
        return load_litematic(path)
    raise ValueError("only .litematic is supported (Sponge .schem / .nbt not yet)")


# ------------------------------------------------------------- materials

_NO_ITEM = {
    "minecraft:piston_head": "pistons are placed, not built",
    "minecraft:moving_piston": "transient block",
    "minecraft:fire": "use flint and steel",
    "minecraft:soul_fire": "use flint and steel on soul sand/soil",
    "minecraft:nether_portal": "light an obsidian frame",
    "minecraft:end_portal": "no item; unbuildable",
    "minecraft:end_gateway": "no item; unbuildable",
    "minecraft:bubble_column": "created by soul sand/magma under water",
    "minecraft:attached_melon_stem": "plant melon seeds and let it grow",
    "minecraft:attached_pumpkin_stem": "plant pumpkin seeds and let it grow",
}
# Built-in fallback when no blocks.json: block id -> item id (when they differ).
_ITEM_OF = {
    "redstone_wire": "redstone",
    "tripwire": "string",
    "wheat": "wheat_seeds",
    "carrots": "carrot",
    "potatoes": "potato",
    "beetroots": "beetroot_seeds",
    "melon_stem": "melon_seeds",
    "pumpkin_stem": "pumpkin_seeds",
    "cocoa": "cocoa_beans",
    "sweet_berry_bush": "sweet_berries",
    "kelp_plant": "kelp",
    "tall_seagrass": "seagrass",
    "bamboo_sapling": "bamboo",
    "cave_vines": "glow_berries",
    "cave_vines_plant": "glow_berries",
    "weeping_vines_plant": "weeping_vines",
    "twisting_vines_plant": "twisting_vines",
    "water_cauldron": "cauldron",
    "lava_cauldron": "cauldron",
    "powder_snow_cauldron": "cauldron",
    "redstone_ore": "redstone_ore",
    "lit_redstone_ore": "redstone_ore",
    "water": "water_bucket",
    "lava": "lava_bucket",
    "farmland": "dirt",
    "dirt_path": "dirt",
}
_WALL_ITEM = re.compile(
    r"(^|_)wall_(?=(torch|sign|hanging_sign|banner|head|skull|fan)$)"
)
_LOWER_HALF = re.compile(
    r"_door$|^(tall_grass|large_fern|sunflower|lilac|rose_bush|peony|tall_seagrass|pitcher_plant)$"
)
_POTTED_PLANT = {"azalea_bush": "azalea", "flowering_azalea_bush": "flowering_azalea"}
_COUNT_PROP = {"sea_pickle": "pickles", "turtle_egg": "eggs", "snow": "layers"}
_HOW = {
    "farmland": "use hoe on dirt",
    "dirt_path": "use shovel on grass_block or dirt",
}


def _item_for(name: str, blocks: dict | None) -> str | None:
    """Item id (with namespace) for a block id; None if the pack says no item."""
    short = name.removeprefix("minecraft:")
    if blocks is not None and name in blocks:
        return blocks[name].get("item")
    short = _WALL_ITEM.sub(r"\1", short)
    return "minecraft:" + _ITEM_OF.get(short, short)


def state_materials(state: str, blocks: dict | None = None):
    """Return (items: Counter, flag: dict|None) for ONE cell of this state."""
    name, props = parse_state(state)
    short = name.removeprefix("minecraft:")
    items: Counter = Counter()
    if name in _AIR_IDS:
        return items, None
    if name in _NO_ITEM:
        return items, {"reason": "no_item", "how": _NO_ITEM[name]}
    if short in ("water", "lava"):  # only source blocks hold a bucket
        if props.get("level", "0") == "0":
            items["minecraft:" + _ITEM_OF[short]] += 1
        return items, None
    if props.get("half") == "upper" and _LOWER_HALF.search(short):
        return items, None
    if props.get("part") == "head" and short.endswith("_bed"):
        return items, None
    if short.startswith("potted_"):  # Litematica counts the pot and the plant
        plant = _POTTED_PLANT.get(short[7:], short[7:])
        items["minecraft:flower_pot"] += 1
        items["minecraft:" + plant] += 1
        return items, None
    item = _item_for(name, blocks)
    flag = None
    if item is None:
        return items, {"reason": "no_item", "how": "no item for this block"}
    if short in _HOW:  # farmland / dirt_path: counts dirt, but cannot be placed
        item = "minecraft:dirt"
        flag = {"reason": "no_item", "how": _HOW[short]}
    count = 1
    if props.get("type") == "double" and short.endswith("_slab"):
        count = 2
    elif short.endswith("candle"):
        count = int(props.get("candles", 1))
    elif short in _COUNT_PROP:
        count = int(props.get(_COUNT_PROP[short], 1))
    items[item] += count
    return items, flag


def material_list(schem: Schematic, blocks_json: dict | None = None):
    """Return (materials Counter, flags list) for the whole schematic."""
    blocks = blocks_json.get("blocks") if blocks_json else None
    uses = Counter(schem.cells)
    materials: Counter = Counter()
    flags = []
    for index, cells in sorted(uses.items()):
        items, flag = state_materials(schem.palette[index], blocks)
        for item, per_cell in items.items():
            materials[item] += per_cell * cells
        if flag:
            flags.append({"state": schem.palette[index], "count": cells, **flag})
    return materials, flags


# ------------------------------------------------------------- build.json


def to_build_json(schem: Schematic, blocks_json: dict | None = None) -> dict:
    materials, flags = material_list(schem, blocks_json)
    raw = b"".join(c.to_bytes(2, "little") for c in schem.cells)
    return {
        "schema": 1,
        "name": schem.name,
        "source": schem.source,
        "data_version": schem.data_version,
        "size": list(schem.size),
        "palette": list(schem.palette),
        "blocks": base64.b64encode(raw).decode("ascii"),
        "materials": dict(sorted(materials.items(), key=lambda kv: (-kv[1], kv[0]))),
        "flags": flags,
    }


def from_build_json(data: dict) -> Schematic:
    if data.get("schema") != 1:
        raise ValueError("unsupported build file schema")
    raw = base64.b64decode(data["blocks"])
    sx, sy, sz = data["size"]
    if len(raw) != 2 * sx * sy * sz:
        raise ValueError("build file block data does not match its size")
    palette = tuple(data["palette"])
    if not palette or palette[0] != AIR:
        raise ValueError("build file palette entry 0 must be minecraft:air")
    cells = tuple(
        int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)
    )
    if cells and max(cells) >= len(palette):
        raise ValueError("build file block index outside the palette")
    return Schematic(
        name=data.get("name", ""),
        source=data.get("source", ""),
        data_version=data.get("data_version"),
        size=(sx, sy, sz),
        palette=palette,
        cells=cells,
    )


def convert(path, output=None, knowledge=None) -> dict:
    """Convert a schematic file; write the build file when output is given."""
    blocks_json = None
    if knowledge:
        blocks_json = json.loads((Path(knowledge) / "blocks.json").read_text("utf-8"))
    result = to_build_json(load_schematic(path), blocks_json)
    if output:
        Path(output).write_text(json.dumps(result), encoding="utf-8")
    return result
