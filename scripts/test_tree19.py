import json
import pathlib

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
events_file = p / "events.jsonl"

last_obs = None
with open(events_file, encoding="utf-8", errors="replace") as f:
    for line in f:
        if '"type":"observation"' in line or '"type": "observation"' in line:
            last_obs = line

if last_obs:
    obs = json.loads(last_obs)
    print("Obs tick:", obs.get("payload", {}).get("tick"))
    # Check what awareness sections or memory has
    # Let's inspect map.sqlite3
    import sqlite3

    conn = sqlite3.connect(p / "map.sqlite3")
    cur = conn.cursor()
    # Let's check sections
    print("map.sqlite3 opened")
