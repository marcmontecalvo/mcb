import pathlib
import sqlite3

p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
conn = sqlite3.connect(p / "map.sqlite3")
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cur.fetchall()]
print(f"Run: {p.name}")
print("Tables:", tables)

for t in tables:
    cur.execute(f"SELECT count(*) FROM {t}")
    print(f"  {t}: {cur.fetchone()[0]} rows")
