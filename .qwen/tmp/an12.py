import io, json, collections, pathlib

f = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z/events.jsonl')
t0 = None
ops = collections.Counter()
rows = []
with io.open(f, 'rb') as fh:
    for raw in fh:
        if b'"type": "request"' not in raw and b'"type":"request"' not in raw:
            continue
        try:
            e = json.loads(raw)
        except Exception:
            continue
        pl = e['payload']
        if t0 is None:
            t0 = e['monotonic_ns']
        ops[pl.get('op')] += 1
        seq = pl.get('sequence', e.get('sequence'))
        rows.append(((e['monotonic_ns'] - t0) / 1e9, seq, pl))

print('request ops:')
for a, b in ops.most_common(30):
    print(f'  {b:7d}  {a}')

lo, hi = 14300, 15800
sel = [r for r in rows if r[1] is not None and lo <= r[1] <= hi]
print(f'\nrequests seq {lo}..{hi}: {len(sel)}')
for t, seq, pl in sel:
    keep = {k: v for k, v in pl.items()
            if k in ('op', 'goal', 'target', 'pos', 'item', 'name', 'task', 'why',
                     'result', 'ok', 'reason', 'kind', 'stance', 'at', 'body')}
    s = json.dumps(keep)
    if len(s) > 220:
        s = s[:220] + '...'
    print(f'  t+{t:7.1f}s #{seq} {s}')
