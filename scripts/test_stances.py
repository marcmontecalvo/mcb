import json
import pathlib
from mclab import build_plan as bp
from mclab.companion_body import CompanionBody, standable, interacts, sees_support
from mclab.spatial_memory import SpatialMemory

# Load checkpoint
cp = json.load(open('results/companion/20261109T023323003795Z/checkpoints/0045_recovered.json', encoding='utf-8'))
cells_dict = {}
for c in cp['cells']:
    pos = (c[0], c[1], c[2])
    block = c[3].split('[')[0]
    cells_dict[pos] = {
        'block': block,
        'state': c[3],
        'collision_boxes': [] if 'air' in block else [[0, 0, 0, 1, 1, 1]]
    }

print("Total cells loaded:", len(cells_dict))

target = (-18, 65, 69)
print(f"Target: {target}")

# Check candidates around target:
tx, ty, tz = target
reach = 4.0
r = 4

cand_nodes = []
for dx in range(-r, r + 1):
    for dz in range(-r, r + 1):
        for dy in range(-r, 2):
            node = (tx + dx, ty + dy, tz + dz)
            if node in ((tx, ty, tz), (tx, ty - 1, tz)):
                continue
            is_stand = standable(cells_dict, node)
            is_inter = interacts(cells_dict, node, target, reach)
            is_see = sees_support(cells_dict, node, target, None)
            if is_stand and is_inter and is_see:
                cand_nodes.append(node)
            elif dy == -1 or dy == 0:
                # Let's inspect why ground nodes fail
                pass

print(f"Valid stances for {target}:", len(cand_nodes))
for c in cand_nodes:
    print("  stance:", c)

print("\nInspect ground nodes at Y=64:")
for dx in range(-2, 3):
    for dz in range(-2, 3):
        node = (tx + dx, 64, tz + dz)
        feet = cells_dict.get(node, {}).get('block', 'unknown')
        head = cells_dict.get((node[0], 65, node[2]), {}).get('block', 'unknown')
        ground = cells_dict.get((node[0], 63, node[2]), {}).get('block', 'unknown')
        is_stand = standable(cells_dict, node)
        print(f"  node {node}: ground(63)={ground}, feet(64)={feet}, head(65)={head} -> standable={is_stand}")
