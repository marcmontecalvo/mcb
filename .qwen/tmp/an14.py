import sqlite3, collections, pathlib, json
from mclab.section_codec import decode_section
from mclab import build_plan as bp

run = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z')
con = sqlite3.connect(run / 'map.sqlite3')
grid = {}
for sx, sy, sz, payload in con.execute('select x,y,z,payload from sections'):
    for c in decode_section((sx, sy, sz), payload):
        x, y, z = c['position']
        if -30 <= x <= 8 and 50 <= z <= 82 and 58 <= y <= 74:
            grid[(x, y, z)] = str(c.get('block') or '')

d = json.load(open(run / 'checkpoints/final.json', encoding='utf-8'))
build = json.load(open('D:/mcb/tests/fixtures/schematics/house-large.build.json', encoding='utf-8'))
tmpl = bp.Template.from_build(build, tuple(d['origin']), 0)
cells = set(tmpl.cells)

G = 63
X0, X1, Z0, Z1 = -22, 0, 57, 72      # area = footprint + margin 1
FX0, FX1, FZ0, FZ1 = -21, -1, 58, 71  # footprint

bad_ground, junk_above, unknown = [], [], []
for z in range(Z0, Z1 + 1):
    for x in range(X0, X1 + 1):
        g = grid.get((x, G, z))
        if g is None:
            unknown.append((x, z, 'ground-unobserved'))
        elif bp.parse_state(g)[0] in bp.AIR or bp.parse_state(g)[0] in bp.FLUIDS:
            bad_ground.append((x, z, g))
        for y in range(G + 1, 70):
            if (x, y, z) in cells:
                continue
            s = grid.get((x, y, z))
            if s is None:
                continue
            i = bp.parse_state(s)[0]
            if i not in bp.AIR and i not in bp.FLUIDS:
                junk_above.append((x, y, z, s))

ring = lambda x, z: not (FX0 <= x <= FX1 and FZ0 <= z <= FZ1)
print('area cells audited:', (X1-X0+1)*(Z1-Z0+1))
print('\nNOT solid at ground y=63:', len(bad_ground))
for p in bad_ground[:25]:
    print('   ', p, '(ring)' if ring(*p[:2]) else '(footprint)')
print('\nnon-template solid blocks above ground in the area:', len(junk_above))
print('   zone split:', collections.Counter('ring' if ring(p[0], p[2]) else 'footprint' for p in junk_above))
print('   block mix :', collections.Counter(p[3].replace('minecraft:', '') for p in junk_above).most_common(10))
print('   y mix     :', sorted(collections.Counter(p[1] for p in junk_above).items()))
for p in sorted(junk_above)[:30]:
    print('   ', p, '(ring)' if ring(p[0], p[2]) else '(footprint)')
print('\nunobserved ground cells:', len(unknown))
