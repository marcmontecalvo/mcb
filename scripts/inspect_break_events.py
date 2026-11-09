import json
import pathlib

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
events_file = p / "events.jsonl"

with open(events_file, "r", encoding="utf-8", errors="replace") as f:
    for idx, line in enumerate(f):
        if 20950 <= idx <= 21120:
            ev = json.loads(line)
            typ = ev.get("type")
            pl = ev.get("payload", {})
            event_name = ev.get("event")
            op = pl.get("op") or event_name
            if op == "heartbeat" or event_name == "tick":
                continue
            print(f"[{idx}] typ={typ} op={op} -> {json.dumps(pl)[:120]}")
