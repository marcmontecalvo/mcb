import io, json, collections, pathlib, re

f = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z/events.jsonl')
reqs = []
t0 = None
n = 0
with io.open(f, 'rb') as fh:
    for raw in fh:
        n += 1
        if b'"type": "request"' not in raw and b'"type":"request"' not in raw:
            continue
        try:
            e = json.loads(raw)
        except Exception:
            continue
        reqs.append(e)
print('lines', n, 'requests', len(reqs))
if not reqs:
    raise SystemExit('no request events matched; inspect a raw line instead')

names = collections.Counter(
    r['payload'].get('action', {}).get('name') if isinstance(r['payload'].get('action'), dict)
    else r['payload'].get('action') or r['payload'].get('kind')
    for r in reqs)
print('\nrequest action names:')
for a, b in names.most_common(25):
    print(f'  {b:7d}  {a}')

print('\nsample payload:', json.dumps(reqs[0]['payload'])[:400])

t0 = reqs[0]['monotonic_ns']
BRIDGES = [(-33, 66, 76), (-22, 67, 74), (-33, 67, 70), (-19, 67, 97), (-9, 66, 93)]

def find_positions(r):
    return re.findall(r'\[(-?\d+),\s*(-?\d+),\s*(-?\d+)\]', json.dumps(r['payload']))

hits = []
for r in reqs:
    blob = json.dumps(r['payload'])
    if 'dirt' not in blob:
        continue
    for x, y, z in find_positions(r):
        if -40 <= int(x) <= -15 and 65 <= int(y) <= 68 and 65 <= int(z) <= 100:
            hits.append(((int(x), int(y), int(z)), (r['monotonic_ns'] - t0) / 1e9, blob[:160]))
            break
print(f'\ndirt-carrying requests in the bridge zone: {len(hits)}')
for h in hits[:40]:
    print(f'  t+{h[1]:8.1f}s  {h[0]}  {h[2]}')
