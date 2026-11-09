import json
from collections import Counter
from pathlib import Path

def main():
    p = sorted(Path("results/companion").glob("*Z"))[-1]
    events_file = p / "events.jsonl"
    print(f"Inspecting run: {p.name}")
    
    last_obs = None
    ops = Counter()
    breaks = []
    places = []
    
    with open(events_file, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if '"type":"observation"' in line:
                try:
                    last_obs = json.loads(line)['payload']
                except Exception:
                    pass
            elif '"type":"request"' in line:
                try:
                    data = json.loads(line)
                    pl = data.get('payload') or {}
                    op = data.get('op') or (pl.get('op') if isinstance(pl, dict) else None)
                    ops[op] += 1
                    if op == 'break_block':
                        p_pos = pl.get('pos')
                        if p_pos:
                            breaks.append((p_pos, pl.get('tool')))
                    elif op == 'place_block':
                        p_pos = pl.get('pos')
                        if p_pos:
                            places.append((p_pos, pl.get('item'), pl.get('state')))
                except Exception:
                    pass
                    
    print("Ops summary:", ops)
    print(f"Breaks found: {len(breaks)}")
    for b in breaks:
        print(f"  Break {b[0]} tool={b[1]}")
    print(f"Places found: {len(places)}")
    for pl in places:
        print(f"  Place {pl[0]} item={pl[1]} state={pl[2]}")
        
    if last_obs:
        body = last_obs.get('body', {})
        print("Last position:", body.get('position'))
        print("Last yaw/pitch:", body.get('yaw'), body.get('pitch'))
        inv = {st['item']: st['count'] for st in body.get('inventory', [])}
        print("Inventory items count:", len(inv), inv)

if __name__ == '__main__':
    main()
