import sqlite3, collections, pathlib
from mclab.section_codec import decode_section

p = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z/map.sqlite3')
con = sqlite3.connect(p)
grid = {}
for sx, sy, sz, payload in con.execute('select x,y,z,payload from sections'):
    for c in decode_section((sx, sy, sz), payload):
        x, y, z = c['position']
        if -40 <= x <= 20 and 40 <= z <= 100 and 60 <= y <= 78:
            grid[(x, y, z)] = str(c.get('block') or '')

AIRISH = ('minecraft:air', 'minecraft:cave_air', 'minecraft:water', 'flowing')
X0, X1, Z0, Z1, G = -21, -1, 58, 71, 63

rows = []
for (x, y, z), blk in grid.items():
    if y <= G + 1:
        continue
    if not any(k in blk for k in ('dirt', 'grass_block', 'cobble', 'scaffolding')):
        continue
    below = grid.get((x, y - 1, z))
    if below is None:
        continue                      # unloaded: not evidence
    if any(a in below for a in AIRISH):
        rows.append((x, y, z, blk, below))

print('elevated footings over confirmed air/fluid:', len(rows))
print('by block:', collections.Counter(r[3] for r in rows).most_common())
print('by y  :', sorted(collections.Counter(r[1] for r in rows).items()))

def zone(x, z):
    if X0 <= x <= X1 and Z0 <= z <= Z1:
        return 'footprint'
    if -22 <= x <= 0 and 57 <= z <= 72:
        return 'margin-ring'
    return 'outside'

print('by zone:', collections.Counter(zone(r[0], r[2]) for r in rows))
print('\n--- all of them, grouped by (y,z) run ---')
for r in sorted(rows, key=lambda t: (-t[1], t[2], t[0])):
    print(f'   {r[0]:4d},{r[1]:3d},{r[2]:4d}  {r[3].replace("minecraft:",""):16s} below={r[4].replace("minecraft:","")}')
