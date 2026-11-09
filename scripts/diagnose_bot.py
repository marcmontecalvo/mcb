import json

import json
import pathlib

def main():
    p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
    events_file = p / "events.jsonl"
    print(f"Inspecting run: {p.name}")
    
    events = []
    with open(events_file, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if '"type":"request"' in line or '"type":"response"' in line or '"type":"observation"' in line:
                try:
                    events.append(json.loads(line))
                except:
                    pass
                    
    print(f"Total events: {len(events)}")
    # Find all place/break requests and their corresponding responses
    for i, ev in enumerate(events):
        pl = ev.get('payload', {})
        if ev.get('type') == 'request':
            op = pl.get('op')
            if op in ('place_block', 'break_block'):
                pos = pl.get('pos')
                item = pl.get('item') or pl.get('tool') or pl.get('state')
                # Look for matching response
                resp = None
                for j in range(i + 1, min(i + 10, len(events))):
                    if events[j].get('type') == 'response':
                        resp = events[j].get('payload')
                        break
                print(f"REQ {op} {pos} {item} -> RESP: ok={resp.get('ok') if resp else '?'} reason={resp.get('reason') if resp else '?'}")

    reqs = [e['payload'] for e in events if e.get('type') == 'request' and e.get('payload', {}).get('op') not in ('heartbeat', 'planner_map')]
    print(f"\nTotal non-heartbeat requests: {len(reqs)}")
    for r in reqs[-50:]:
        op = r.get('op')
        pos = r.get('pos') or r.get('goal') or r.get('item')
        print(f"  {op:18} {pos}")

if __name__ == '__main__':
    main()
