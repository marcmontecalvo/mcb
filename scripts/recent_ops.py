import json
import pathlib

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
events_file = p / "events.jsonl"
ops = []
with open(events_file, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        if '"type":"request"' in line or '"type": "request"' in line:
            try:
                ops.append(json.loads(line))
            except Exception:
                pass

print(f"Total requests: {len(ops)}")
for op in ops[-20:]:
    payload = op.get("payload", {})
    op_name = payload.get("op")
    arg = payload.get("pos") or payload.get("goal") or payload.get("target") or payload.get("item") or ""
    print(f"  {op_name:18} {arg}")
