import json, pathlib, collections

p = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z')
d = json.load(open(p / 'build.json', encoding='utf-8'))
ops = d['ops']
print('sample ok  :', json.dumps(ops[0]))
print('sample fail:', json.dumps(next(o for o in ops if not o['ok'])))

print('\n--- op x ok x reason ---')
for a, b in collections.Counter(
        (o.get('op'), o.get('ok'), o.get('reason')) for o in ops).most_common(30):
    print(f'  {a}  {b}')

print('\n--- why values on failures ---')
for a, b in collections.Counter(
        str(o.get('why'))[:90] for o in ops if not o.get('ok')).most_common(20):
    print(f'  {b:5d}  {a}')

# where did successful terrain edits (bridge/pillar/fill) happen?
def pos_of(o):
    for k in ('target', 'pos', 'at'):
        if isinstance(o.get(k), list) and len(o[k]) == 3:
            return tuple(o[k])
    return None

terrain = [o for o in ops if o.get('op') in ('place', 'break', 'bridge', 'pillar', 'step')
           and o.get('ok')]
print('\nsuccessful terrain ops:', len(terrain), collections.Counter(o['op'] for o in terrain))
print('\n--- goto targets on failures, by y ---')
gt = [o for o in ops if not o.get('ok') and isinstance(o.get('goto'), list)]
print('failed ops carrying a goto:', len(gt))
print(collections.Counter(tuple(o['goto'])[1] for o in gt).most_common(10))
