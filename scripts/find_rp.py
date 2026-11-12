import json
import pathlib

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
events_file = p / "events.jsonl"
print(f"Inspecting run: {p.name}")

# Sample nav_ticks every 1000 lines
nav_ticks = []
with open(events_file, encoding="utf-8", errors="replace") as f:
    for line in f:
        if '"type":"navigation_tick"' in line:
            try:
                data = json.loads(line)
                pl = data.get("payload", {})
                nav_ticks.append(pl)
            except Exception:
                pass

print(f"Total nav_ticks: {len(nav_ticks)}")
step = max(1, len(nav_ticks) // 20)
for nt in nav_ticks[::step]:
    result = nt.get("result")
    if isinstance(result, list):
        result = result[:2]
    print(
        f"tick={nt.get('tick')} mode={nt.get('mode')} "
        f"result={result} "
        f"pos={nt.get('search_start')}"
    )
