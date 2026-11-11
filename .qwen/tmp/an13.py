import io, json, pathlib

f = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z/events.jsonl')
WANT = (b'"op": "follow_gap"', b'"op":"follow_gap"')
t0 = None
found = 0
with io.open(f, 'rb') as fh:
    for raw in fh:
        if b'"type": "request"' not in raw and b'"type":"request"' not in raw:
            continue
        if not any(w in raw for w in WANT):
            continue
        e = json.loads(raw)
        pl = e['payload']
        if t0 is None:
            t0 = e['monotonic_ns']
        found += 1
        print(f'--- follow_gap #{pl.get("sequence")} t+{(e["monotonic_ns"]-t0)/1e9:.1f}s ---')
        print(json.dumps({k: v for k, v in pl.items()
                          if k not in ('schema_version', 'run_id', 'episode_id',
                                       'body_id')})[:900])
print('total follow_gap requests:', found)
