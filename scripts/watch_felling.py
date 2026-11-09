import json
import pathlib
import sys

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
events_file = p / "events.jsonl"

breaks = []
with open(events_file, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        if '"op":"break_block"' in line or '"op": "break_block"' in line:
            obj = json.loads(line)
            breaks.append(obj)

print(f"Total break requests: {len(breaks)}")
for b in breaks[-10:]:
    p = b.get("payload", {})
    print(" ", p.get("pos"), p.get("tool"))
