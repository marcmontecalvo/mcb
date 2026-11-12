import json
import pathlib

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
events_file = p / "events.jsonl"
places = []
breaks = []
with open(events_file, encoding="utf-8", errors="replace") as f:
    for line in f:
        if '"op":"place_block"' in line or '"op": "place_block"' in line:
            try:
                places.append(json.loads(line))
            except Exception:
                pass
        elif '"op":"break_block"' in line or '"op": "break_block"' in line:
            try:
                breaks.append(json.loads(line))
            except Exception:
                pass

print(f"Run folder: {p.name}")
print(f"Total break requests: {len(breaks)}")
print(f"Total place requests: {len(places)}")
for b in breaks[-10:]:
    pl = b.get("payload", {})
    print(f"  break {pl.get('pos')} tool={pl.get('tool')}")
for pl_req in places[-15:]:
    pl = pl_req.get("payload", {})
    print(f"  place {pl.get('pos')} item={pl.get('item')} state={pl.get('state')}")
