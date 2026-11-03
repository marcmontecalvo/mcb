# ruff: noqa: E501
"""Find a build site where every gathered material is within reach.

Usage: python scripts/analysis/site_scan.py WORLD_DIR [--radius-chunks 6]
WORLD_DIR is a level folder (with region/ and entities/, or dimensions/minecraft/overworld/).
Reads every generated chunk (anvil format, zlib), counts the blocks and animals below
per chunk, then scores each chunk as a centre by the scarcest need within the radius.
Prints the best centres with the counts and a surface feet cell to start from.
"""

import math
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from mclab import nbt  # noqa: E402

M = "minecraft:"
# need: (blocks that count, how many are wanted within the radius)
NEEDS = {
    "oak_log": ({M + "oak_log"}, 400),
    "spruce_log": ({M + "spruce_log"}, 260),
    "stone": ({M + "stone", M + "cobblestone"}, 400),
    "andesite": ({M + "andesite"}, 20),
    "iron": ({M + "iron_ore", M + "deepslate_iron_ore"}, 130),
    "coal": ({M + "coal_ore", M + "deepslate_coal_ore"}, 40),
    "redstone": ({M + "redstone_ore", M + "deepslate_redstone_ore"}, 2),
    "sugar_cane": ({M + "sugar_cane"}, 50),
    "clay": ({M + "clay"}, 12),  # 4 balls a block
    "sand": ({M + "sand"}, 10),
    "vine": ({M + "vine"}, 40),
    "poppy": ({M + "poppy"}, 2),
    "lily_of_the_valley": ({M + "lily_of_the_valley"}, 1),
    "blue_orchid": ({M + "blue_orchid"}, 1),
}
ANIMALS = {"cow": (M + "cow", 8), "sheep": (M + "sheep", 3)}  # leather 0-2 a cow; wool 1 a sheep (shears)
WATCH = {b: name for name, (blocks, _) in NEEDS.items() for b in blocks}


def chunks(region: Path):
    data = region.read_bytes()
    if len(data) < 8192:
        return
    for i in range(1024):
        offset = int.from_bytes(data[i * 4 : i * 4 + 3], "big")
        if offset == 0:
            continue
        start = offset * 4096
        length, kind = struct.unpack(">IB", data[start : start + 5])
        raw = data[start + 5 : start + 4 + length]
        if kind == 2:
            yield nbt.loads(zlib.decompress(raw))


def unpack(longs, bits, count):
    per = 64 // bits
    mask = (1 << bits) - 1
    out = []
    for word in longs:
        word &= 0xFFFFFFFFFFFFFFFF
        for k in range(per):
            out.append((word >> (k * bits)) & mask)
            if len(out) == count:
                return out
    return out + [0] * (count - len(out))


def surface(root):
    maps = root.get("Heightmaps", {}).get("MOTION_BLOCKING")
    if not maps:
        return None
    heights = unpack(maps, 9, 256)
    return heights[8 * 16 + 8] + root.get("yPos", -4) * 16  # the chunk's middle column


def scan(world: Path):
    over = world / "dimensions/minecraft/overworld"
    base = over if (over / "region").exists() else world
    counts, biomes, tops = {}, {}, {}
    for region in sorted((base / "region").glob("*.mca")):
        for root in chunks(region):
            if root.get("Status", "") not in ("minecraft:full", "full"):
                continue
            key = (root["xPos"], root["zPos"])
            c = Counter()
            names = set()
            for section in root.get("sections", []):
                states = section.get("block_states", {})
                palette = [  # plain names, or compounds with properties
                    p if isinstance(p, str) else p.get("Name", p.get(""))
                    for p in states.get("palette", [])
                ]
                if any(n in WATCH for n in palette):
                    if len(palette) == 1:
                        c[WATCH[palette[0]]] += 4096
                    else:
                        bits = max(4, math.ceil(math.log2(len(palette))))
                        for idx in unpack(states["data"], bits, 4096):
                            name = WATCH.get(palette[idx])
                            if name:
                                c[name] += 1
                names.update(section.get("biomes", {}).get("palette", []))
            counts[key], biomes[key], tops[key] = c, names, surface(root)
    animals = {}
    for region in sorted((base / "entities").glob("*.mca")):
        for root in chunks(region):
            pos = root.get("Position")
            if not pos:
                continue
            a = Counter(e.get("id") for e in root.get("Entities", []))
            animals[(pos[0], pos[1])] = a
    return counts, biomes, tops, animals


def best(counts, biomes, tops, animals, radius):
    scored = []
    for (cx, cz) in counts:
        total, beasts, kinds = Counter(), Counter(), set()
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if dx * dx + dz * dz > radius * radius:
                    continue
                k = (cx + dx, cz + dz)
                total.update(counts.get(k, {}))
                beasts.update(animals.get(k, {}))
                kinds |= biomes.get(k, set())
        ratios = [min(1.0, total[n] / want) for n, (_, want) in NEEDS.items()]
        ratios += [min(1.0, beasts[i] / want) for i, want in ANIMALS.values()]
        scored.append((min(ratios), sum(ratios), (cx, cz), total, beasts, kinds))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return scored


def main():
    world = Path(sys.argv[1])
    radius = int(sys.argv[sys.argv.index("--radius-chunks") + 1]) if "--radius-chunks" in sys.argv else 6
    counts, biomes, tops, animals = scan(world)
    print(f"{len(counts)} chunks, {sum(len(a) for a in animals.values())} animal chunks")
    for score, _, (cx, cz), total, beasts, kinds in best(counts, biomes, tops, animals, radius)[:5]:
        x, z = cx * 16 + 8, cz * 16 + 8
        y = tops.get((cx, cz))
        short = [n for n, (_, w) in NEEDS.items() if total[n] < w] + [i.split(":")[1] for i, w in ANIMALS.values() if beasts[i] < w]
        print(f"score {score:.2f} centre ({x}, {y}, {z}) short: {short or 'none'}")
        print("   ", {n: total[n] for n in NEEDS}, {i.split(':')[1]: beasts[i] for i, _ in ANIMALS.values()})
        print("    biomes:", sorted(b.replace(M, "") for b in kinds))


if __name__ == "__main__":
    main()
