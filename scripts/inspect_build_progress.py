import json
import pathlib
import sqlite3
from mclab import build_plan as bp

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
build_file = pathlib.Path("tests/fixtures/schematics/house-large.build.json")
build = json.loads(build_file.read_text(encoding="utf-8"))
template = bp.Template.from_build(build, (-21, 64, 58), 0, False)

# Connect to map.sqlite3
db_file = p / "map.sqlite3"
conn = sqlite3.connect(str(db_file))
cursor = conn.cursor()

def world_lookup(x, y, z):
    cursor.execute("SELECT state FROM blocks WHERE x = ? AND y = ? AND z = ?", (x, y, z))
    row = cursor.fetchone()
    return row[0] if row else None

diff = bp.diff(template, world_lookup)
prog = bp.progress(template, world_lookup)
total = sum(prog.values())
correct = prog.get("correct", 0)
frac = correct / total if total else 0.0

print(f"Build Progress: {correct}/{total} ({frac:.1%})")
print(f"Breaks needed: {len(diff.breaks)}")
print(f"Places needed: {len(diff.places)}")
print(f"Uses needed: {len(diff.uses)}")
print(f"Unknown cells: {len(diff.unknown)}")
conn.close()
