import json
import pathlib

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
events_file = p / "events.jsonl"
reqs = []
with open(events_file, encoding="utf-8", errors="replace") as f:
    for line in f:
        if "planner_request" in line:
            r = json.loads(line)
            pl = r.get("payload", {})
            reqs.append(pl)

print(f"Total planner requests: {len(reqs)}")
for r in reqs[-10:]:
    print(r)
