"""Transactional compressed spatial sections, separate from the motor loop."""

import heapq
import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from mclab.section_codec import decode_section, encode_section, section_key


class MapArchive:
    """Sparse 16-cube sections; explicit air replaces solids, absent cells stay unknown.

    Schema-one indexed cells migrate transactionally, including all namespaces and
    coverage gaps. A failed migration leaves the original archive usable.
    """

    def __init__(self, path, identity):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.identity = identity
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError("Unsupported spatial archive schema")
            db.execute("""
                CREATE TABLE IF NOT EXISTS sections (
                    namespace TEXT NOT NULL, x INTEGER NOT NULL,
                    y INTEGER NOT NULL, z INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY(namespace,x,z,y))
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS coverage (
                    namespace TEXT PRIMARY KEY, trusted INTEGER NOT NULL)
            """)
            if version == 1:
                rows = db.execute("SELECT namespace,payload FROM cells")
                while batch := rows.fetchmany(4096):
                    namespaces = defaultdict(list)
                    for namespace, payload in batch:
                        namespaces[namespace].append(json.loads(payload))
                    for namespace, cells in namespaces.items():
                        self._write_cells(db, namespace, cells)
                db.execute("DROP TABLE cells")
            db.execute("PRAGMA user_version=2")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _write_cells(db, namespace, cells):
        sections = defaultdict(dict)
        for cell in cells:
            p = tuple(cell["position"])
            sections[section_key(p)][p] = cell
        for key, changes in sections.items():
            row = db.execute(
                "SELECT payload FROM sections "
                "WHERE namespace=? AND x=? AND y=? AND z=?",
                (namespace, *key),
            ).fetchone()
            previous = (
                {tuple(c["position"]): c for c in decode_section(key, row[0])}
                if row
                else {}
            )
            previous.update(changes)
            db.execute(
                "INSERT INTO sections VALUES(?,?,?,?,?) "
                "ON CONFLICT(namespace,x,z,y) DO UPDATE SET payload=excluded.payload",
                (namespace, *key, encode_section(previous.values())),
            )

    def write(self, cells):
        with self.connect() as db:
            # Serialize read/merge/write across independent writer connections.
            db.execute("BEGIN IMMEDIATE")
            self._write_cells(db, self.identity.key, cells)

    def mark_gap(self):
        with self.connect() as db:
            db.execute(
                "INSERT INTO coverage VALUES(?,0) "
                "ON CONFLICT(namespace) DO UPDATE SET trusted=0",
                (self.identity.key,),
            )

    def recall(self, center, radius, height, *, limit):
        """Bound returned cells and decoded working set; disk work is explicit."""
        if min(radius, height) < 0 or limit < 1:
            raise ValueError("Invalid map recall bounds")
        x, y, z = center
        lower = (x - radius, y - height, z - radius)
        upper = (x + radius, y + height, z + radius)
        with self.connect() as db:
            gap = db.execute(
                "SELECT trusted FROM coverage WHERE namespace=?", (self.identity.key,)
            ).fetchone()
            if gap is not None and not gap[0]:
                raise RuntimeError("Archive has an observation gap; rescan required")
            lo, hi = section_key(lower), section_key(upper)
            rows = db.execute(
                "SELECT x,y,z,payload FROM sections WHERE namespace=? "
                "AND x BETWEEN ? AND ? AND y BETWEEN ? AND ? AND z BETWEEN ? AND ?",
                (self.identity.key, lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]),
            )

            def admitted():
                for sx, sy, sz, payload in rows:
                    for cell in decode_section((sx, sy, sz), payload):
                        if all(
                            a <= p <= b
                            for p, a, b in zip(
                                cell["position"], lower, upper, strict=True
                            )
                        ):
                            yield cell

            return heapq.nsmallest(
                limit,
                admitted(),
                key=lambda c: (
                    sum(
                        (a - b) ** 2 for a, b in zip(c["position"], center, strict=True)
                    ),
                    c["position"],
                ),
            )
