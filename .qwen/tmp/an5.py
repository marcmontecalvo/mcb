import io, json, collections, pathlib

f = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z/events.jsonl')
size = f.stat().st_size
with io.open(f, 'rb') as fh:
    fh.seek(max(0, size - 40_000_000))
    fh.readline()
    tail = fh.read().decode('utf-8', 'replace')

lines = tail.splitlines()[1:]
print('tail lines', len(lines))
evs = []
for ln in lines:
    try:
        evs.append(json.loads(ln))
    except Exception:
        pass
print('parsed', len(evs))
print('--- event names ---')
for a, b in collections.Counter(e.get('name') or e.get('kind') or e.get('type') for e in evs).most_common(20):
    print(f'  {b:6d}  {a}')
print('\n--- first tail event ---')
print(json.dumps(evs[0])[:400])
print('\n--- last 25 events ---')
for e in evs[-25:]:
    print(json.dumps(e)[:230])
