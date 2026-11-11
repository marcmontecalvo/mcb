import sqlite3, collections, pathlib
from mclab.section_codec import decode_section

p = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z/map.sqlite3')
con = sqlite3.connect(p)
ns = collections.Counter(r[0] for r in con.execute('select namespace from sections'))
print('namespaces:', ns.most_common(5))

# house footprint: x -21..-1, z 58..71, ground_y 63
X0, X1, Z0, Z1, G = -21, -1, 58, 71, 63
found = []
n = 0
for sx, sy, sz, payload in con.execute(
        'select x,y,z,payload from sections order by x,y,z'):
    n += 1
    for c in decode_section((sx, sy, sz), payload):
        x, y, z = c['position']
        blk = str(c.get('block') or '')
        if y <= G or y > 72:
            continue
        if not any(k in blk for k in ('dirt', 'grass_block', 'scaffolding', 'cobble')):
            continue
        inside = X0 <= x <= X1 and Z0 <= z <= Z1
        found.append((x, y, z, blk, inside))
print('sections decoded:', n, 'candidate footings above ground:', len(found))
print('by block:', collections.Counter(f[3] for f in found).most_common(10))
print('inside footprint vs outside:', collections.Counter(f[4] for f in found))

outside = sorted([f for f in found if not f[4]])
print('\n--- outside-footprint elevated dirt/scaffold (first 60) ---')
for f in outside[:60]:
    print('  ', f[:4])

print('\n--- y histogram outside ---')
print(sorted(collections.Counter(f[1] for f in outside).items()))
