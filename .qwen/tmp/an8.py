import sqlite3, collections, pathlib
from mclab.section_codec import decode_section

p = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z/map.sqlite3')
con = sqlite3.connect(p)

RX0, RX1, RZ0, RZ1, RY0, RY1 = -40, 20, 40, 95, 58, 76
grid = {}
for sx, sy, sz, payload in con.execute('select x,y,z,payload from sections'):
    for c in decode_section((sx, sy, sz), payload):
        x, y, z = c['position']
        if RX0 <= x <= RX1 and RZ0 <= z <= RZ1 and RY0 <= y <= RY1:
            grid[(x, y, z)] = str(c.get('block') or '')
print('cells loaded:', len(grid))

def at(x, y, z):
    return grid.get((x, y, z))

AIRISH = ('minecraft:air', 'minecraft:cave_air', 'minecraft:water', 'minecraft:flowing_water')
X0, X1, Z0, Z1 = -21, -1, 58, 71   # house footprint
G = 63                              # ground_y

susp = []
for (x, y, z), blk in grid.items():
    if blk is None or any(a in blk for a in AIRISH):
        continue
    if not any(k in blk for k in ('dirt', 'grass_block', 'cobble', 'scaffolding')):
        continue
    below = at(x, y - 1, z)
    if below is None or any(a in below for a in AIRISH):
        susp.append((x, y, z, blk, below))

print('\nartificial footings (solid with air/fluid/unknown below):', len(susp))
infp = [s for s in susp if X0 <= s[0] <= X1 and Z0 <= s[2] <= Z1]
ring = [s for s in susp if not (X0 <= s[0] <= X1 and Z0 <= s[2] <= Z1)
        and -23 <= s[0] <= 1 and 56 <= s[2] <= 73]
far = [s for s in susp if s not in infp and s not in ring]
print('  inside footprint:', len(infp), ' in 1-block margin ring:', len(ring), ' elsewhere:', len(far))
print('\n  block mix elsewhere:', collections.Counter(s[3] for s in far).most_common(6))
print('  y mix elsewhere  :', sorted(collections.Counter(s[1] for s in far).items()))

print('\n--- margin-ring footings (protected ground is at y=63) ---')
for s in sorted(ring):
    print('   ', s)

print('\n--- outside-ring footings near house, sorted ---')
for s in sorted(far)[:70]:
    print('   ', s)
