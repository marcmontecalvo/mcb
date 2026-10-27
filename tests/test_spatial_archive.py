import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from test_spatial_memory import identity, observation

from mclab.spatial_archive import MapArchive


def legacy_archive(path, *, corrupt=False):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE cells(namespace TEXT,x INTEGER,y INTEGER,z INTEGER,
                payload TEXT, PRIMARY KEY(namespace,x,z,y));
            CREATE TABLE coverage(namespace TEXT PRIMARY KEY,trusted INTEGER);
            PRAGMA user_version=1;
        """)
        for namespace in (identity(), replace(identity(), generation="other")):
            for x in range(-16, 17):
                cell = observation((x, -2, 17))["visible_blocks"][0]
                db.execute(
                    "INSERT INTO cells VALUES(?,?,?,?,?)",
                    (
                        namespace.key,
                        *cell["position"],
                        "broken" if corrupt else json.dumps(cell),
                    ),
                )
        db.execute("INSERT INTO coverage VALUES(?,0)", (identity().key,))


def test_migration_preserves_other_namespaces_air_updates_and_gaps(tmp_path):
    path = tmp_path / "map.db"
    legacy_archive(path)
    archive = MapArchive(path, identity())
    with pytest.raises(RuntimeError, match="gap"):
        archive.recall((0, -2, 17), 30, 0, limit=100)
    other = MapArchive(path, replace(identity(), generation="other"))
    assert len(other.recall((0, -2, 17), 30, 0, limit=100)) == 33
    other.write(observation((0, -2, 17), block="minecraft:air")["visible_blocks"])
    assert other.recall((0, -2, 17), 0, 0, limit=1)[0]["block"] == "minecraft:air"
    assert len(other.recall((0, -2, 17), 30, 0, limit=100)) == 33
    with other.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='cells'"
        ).fetchall()
        assert (
            db.execute("SELECT typeof(payload) FROM sections").fetchone()[0] == "blob"
        )


def test_failed_migration_rolls_back_without_removing_original_cells(tmp_path):
    path = tmp_path / "map.db"
    legacy_archive(path, corrupt=True)
    with pytest.raises(json.JSONDecodeError):
        MapArchive(path, identity())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM cells").fetchone()[0] == 66


def test_concurrent_section_updates_do_not_lose_neighbour_cells(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda x: archive.write(observation((x, 0, 0))["visible_blocks"]),
                range(16),
            )
        )
    assert len(archive.recall((0, 0, 0), 20, 0, limit=100)) == 16
    assert archive.recall((0, 1, 0), 20, 0, limit=100) == []
