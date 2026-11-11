import json, pathlib, collections, sys

p = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z')
d = json.load(open(p / 'checkpoints/final.json', encoding='utf-8'))

print('build:', json.dumps(d['build'])[:600])
print('origin:', d['origin'], 'rotation:', d['rotation'], 'stop:', json.dumps(d.get('stop'))[:300])
print('start:', d['start'], 'seed:', d['seed'])
cells = d['cells']
print('cells type', type(cells), 'len', len(cells))
if isinstance(cells, dict):
    k0 = next(iter(cells))
    print('sample key', k0, '->', json.dumps(cells[k0])[:300])
    states = collections.Counter(v.get('state') if isinstance(v, dict) else str(v) for v in cells.values())
    print('state histogram:', states)
    kinds = collections.Counter(v.get('kind') for v in cells.values() if isinstance(v, dict))
    print('kind histogram:', kinds.most_common(20))
elif isinstance(cells, list):
    print('sample', json.dumps(cells[0])[:300])
    states = collections.Counter(c.get('state') for c in cells)
    print('state histogram:', states)
