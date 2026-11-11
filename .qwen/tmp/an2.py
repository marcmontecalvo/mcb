import json, pathlib

p = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z')
d = json.load(open(p / 'build.json', encoding='utf-8'))

for k in ('policy', 'ticks', 'needed', 'shortfall', 'gather', 'site', 'stalls',
          'stall_reason', 'counts', 'unknown', 'skipped', 'scaffolded', 'failures',
          'final_diff', 'policies', 'refills', 'job_stop', 'stop', 'surveyed'):
    v = d.get(k)
    s = json.dumps(v, indent=1)
    if len(s) > 2500:
        s = s[:2500] + f'\n... [truncated, total {len(s)} chars]'
    print(f'===== {k} =====')
    print(s)
    print()
