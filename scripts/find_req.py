import json

with open('results/companion/20261108T185320338728Z/events.jsonl', 'r', encoding='utf-8', errors='replace') as f:
    for line in f:
        if 'plan-627' in line and '"type":"request"' in line:
            r = json.loads(line)
            p = r['payload']
            print(p.get('op'), p.get('plan_id'), {k: v for k, v in p.items() if k not in ('data',)})
