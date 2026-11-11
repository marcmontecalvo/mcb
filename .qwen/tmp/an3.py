import json, pathlib, collections

p = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z')
d = json.load(open(p / 'build.json', encoding='utf-8'))
ops = d['ops']
print('ops', len(ops))
print('sample', json.dumps(ops[0])[:300])
print('keys', sorted({k for o in ops[:400] for k in o}))

kind = collections.Counter((o.get('kind'), o.get('ok'), o.get('reason')) for o in ops)
print('\n--- kind/ok/reason (top 25) ---')
for a, b in kind.most_common(25):
    print(f'  {a}  {b}')

site_x = (-22, 0)
site_z = (57, 72)
ground = 63
def in_site(o):
    pos = o.get('pos') or o.get('target')
    if not pos: return None
    return site_x[0] <= pos[0] <= site_x[1] and site_z[0] <= pos[2] <= site_z[1]

dirt = [o for o in ops if str(o.get('item', '')).endswith(('dirt', 'cobblestone')) and o.get('ok')]
print('\ndirt/cobble placements ok:', len(dirt))
print('by y:', collections.Counter((o['pos'][1] if o.get('pos') else None) for o in dirt).most_common(12))
print('in site area:', collections.Counter(in_site(o) for o in dirt))
hi = [o for o in dirt if o.get('pos') and o['pos'][1] > 64]
print('elevated (y>64) dirt placements:', len(hi))
for o in hi[:15]:
    print('   ', o.get('pos'), o.get('item'), o.get('kind'), o.get('reason'))

refused = [o for o in ops if not o.get('ok')]
print('\nfailed ops:', len(refused))
print('by reason:', collections.Counter(o.get('reason') for o in refused).most_common(15))
print('failed in-site:', collections.Counter(in_site(o) for o in refused))
