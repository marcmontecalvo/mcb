import json
import pathlib
import sqlite3
import zlib

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
events_file = p / "events.jsonl"
print(f"Inspecting run: {p.name}")


with open(events_file, 'r', encoding='utf-8', errors='replace') as f:
    for line in f:
        if '"type":"request"' in line and '"goal"' in line:
            r = json.loads(line)
            pl = r.get('payload', {})
            print(f"Goal request: op={pl.get('op')} goal={pl.get('goal')} reach={pl.get('interact_reach') or pl.get('reach')}")
        elif '"type":"observation"' in line and '"body"' in line:
            r = json.loads(line)
            pl = r.get('payload', {})
            b = pl.get('body', {})
            pos = b.get('position')

cur.execute("SELECT x, y, z, payload FROM sections WHERE x = -1 AND z = 4")
rows = cur.fetchall()
print(f"Found {len(rows)} sections for chunk (-1, 4)")
for x, y, z, payload in rows:
    decomp = zlib.decompress(payload)
    palette, rws = json.loads(decomp)
    for off, pal_idx, _ in rws:
        bx = off // 256
        by = (off % 256) // 16
        bz = off % 16
        world_x = x * 16 + bx
        world_y = y * 16 + by
        world_z = z * 16 + bz
        if world_x == -8 and world_z == 71:
            facts = palette[pal_idx]
            print(f"({world_x}, {world_y}, {world_z}) -> {facts.get('block')} {facts.get('properties')}")
conn.close()



