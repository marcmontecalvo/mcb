import json, pathlib, collections
from mclab import build_plan as bp

run = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z')
d = json.load(open(run / 'checkpoints/final.json', encoding='utf-8'))
origin = tuple(d['origin'])
rot = d['rotation'] if not isinstance(d['rotation'], dict) else d['rotation'].get('deg', 0)
build = json.load(open('D:/mcb/tests/fixtures/schematics/house-large.build.json', encoding='utf-8'))
tmpl = bp.Template.from_build(build, origin, rotation=int(rot))
world = {tuple(c[:3]): c[3] for c in d['cells']}
print('template cells', len(tmpl.cells), 'world cells', len(world), 'origin', origin, 'rot', rot)

verdicts = collections.Counter()
prop_mismatch = []
for pos, target in tmpl.cells.items():
    cur = world.get(pos)
    v = bp._classify(target, cur, bp.DEFAULT_IGNORE, False)
    verdicts[v] += 1
    if v in ('break', 'place') and cur and bp.parse_state(target)[0] == bp.parse_state(cur)[0]:
        prop_mismatch.append((pos, target, cur))

print('\n_classify verdicts:', verdicts.most_common())
print('\nsame block id, different properties:', len(prop_mismatch))
kinds = collections.Counter(
    (bp.parse_state(t)[0], tuple(sorted(set(bp.parse_state(t)[1]) ^ set(bp.parse_state(c)[1]))))
    for _, t, c in prop_mismatch)
for a, b in kinds.most_common(15):
    print(f'  {b:5d}  {a[0].replace("minecraft:","")} differing props={a[1]}')

print('\n--- examples ---')
for pos, t, c in prop_mismatch[:20]:
    print(f'  {pos} want={t.replace("minecraft:","")}  have={c.replace("minecraft:","")}')

cnt = bp.counts if hasattr(bp, 'counts') else None
if cnt:
    print('\nlibrary counts():', cnt(tmpl, lambda *p: world.get(tuple(p))))
