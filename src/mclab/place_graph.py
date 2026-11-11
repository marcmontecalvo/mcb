"""Namespaced coarse places and verified connections; no teleport execution.

An edge is a planning hint, never fresh motor evidence. Teleport edges are stored
for future integration and excluded from routing unless explicitly permitted.
"""

import heapq
import json
import math
import time
from dataclasses import asdict, dataclass, replace

PLACE_LIMIT = 4096


def _text(value):
    return type(value) is str and 0 < len(value.strip()) <= 256


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    position: tuple
    provenance: str
    verified_at_ns: int
    kind: str = "place"  # e.g. home, build_site, furnace; payloads before it: place

    def __post_init__(self):
        if (
            not all(_text(v) for v in (self.id, self.name, self.provenance, self.kind))
            or not isinstance(self.position, (tuple, list))
            or len(self.position) != 3
            or any(type(v) is not int or abs(v) > 30_000_000 for v in self.position)
            or type(self.verified_at_ns) is not int
            or self.verified_at_ns <= 0
        ):
            raise ValueError("Invalid place")
        object.__setattr__(self, "position", tuple(self.position))


@dataclass(frozen=True)
class Connection:
    source: str
    target: str
    kind: str
    cost: float
    requirements: tuple
    provenance: str
    verified_at_ns: int

    def __post_init__(self):
        if (
            not all(_text(v) for v in (self.source, self.target, self.provenance))
            or self.source == self.target
            or self.kind not in {"walk", "terrain", "teleport"}
            or type(self.cost) not in (int, float)
            or not math.isfinite(self.cost)
            or self.cost <= 0
            or not isinstance(self.requirements, (list, tuple))
            or len(self.requirements) > 32
            or any(not _text(v) for v in self.requirements)
            or type(self.verified_at_ns) is not int
            or self.verified_at_ns <= 0
        ):
            raise ValueError("Invalid connection")
        if self.kind == "teleport" and not self.requirements:
            raise ValueError(
                "Teleport requires explicit activation/access/cost requirements"
            )
        object.__setattr__(self, "requirements", tuple(self.requirements))


class PlaceGraph:
    def __init__(self, archive):
        self.archive = archive
        with archive.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS places (
                    namespace TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(namespace,id))
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS connections (
                    namespace TEXT NOT NULL, source TEXT NOT NULL,
                    target TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(namespace,source,target,kind))
            """)

    def put_place(self, place):
        if not isinstance(place, Place):
            raise ValueError("Expected Place")
        with self.archive.connect() as db:
            db.execute(
                "INSERT INTO places VALUES(?,?,?) ON CONFLICT(namespace,id) "
                "DO UPDATE SET payload=excluded.payload",
                (self.archive.identity.key, place.id, json.dumps(asdict(place))),
            )
            # Moving/re-verifying a place invalidates all old incident connections.
            db.execute(
                "DELETE FROM connections WHERE namespace=? AND (source=? OR target=?)",
                (self.archive.identity.key, place.id, place.id),
            )

    # --- named waypoints ---------------------------------------------------
    # ponytail: lookups read the namespace's places (capped at PLACE_LIMIT) and
    # filter in Python; they run when a job picks a goal, never in the motor loop.
    # A name/kind index is the upgrade if a world collects thousands of places.
    @property
    def dimension(self):
        return self.archive.identity.dimension

    def listed(self, kind=None):
        with self.archive.connect() as db:
            rows = db.execute(
                "SELECT payload FROM places WHERE namespace=? ORDER BY rowid LIMIT ?",
                (self.archive.identity.key, PLACE_LIMIT),
            ).fetchall()
        places = [Place(**json.loads(row[0])) for row in rows]
        return [p for p in places if kind is None or p.kind == kind]

    def named(self, name):
        return next((p for p in self.listed() if p.name == name), None)

    def nearest(self, position, kind=None):
        return min(
            self.listed(kind),
            key=lambda p: math.dist(p.position, position),
            default=None,
        )

    def add(self, name, position, *, kind="place", provenance="named"):
        """Name -> position in this world and dimension; the id is the first name."""
        place = Place(
            name, name, tuple(position), provenance, time.time_ns(), kind=kind
        )
        self.put_place(place)
        return place

    def remember(self, kind, position):
        """Record a useful spot once; seeing it again is not a move."""
        position = tuple(position)
        same = next((p for p in self.listed(kind) if p.position == position), None)
        if same is not None:
            return same
        x, y, z = position
        return self.add(f"{kind}@{x},{y},{z}", position, kind=kind, provenance="seen")

    def rename(self, name, new_name):
        place = self.named(name)
        if place is None:
            raise KeyError(name)
        renamed = replace(place, name=new_name)
        # payload only: put_place would drop the walked connections
        with self.archive.connect() as db:
            db.execute(
                "UPDATE places SET payload=? WHERE namespace=? AND id=?",
                (json.dumps(asdict(renamed)), self.archive.identity.key, place.id),
            )
        return renamed

    def remove(self, name):
        place = self.named(name)
        if place is None:
            return
        key = self.archive.identity.key
        with self.archive.connect() as db:
            db.execute("DELETE FROM places WHERE namespace=? AND id=?", (key, place.id))
            db.execute(
                "DELETE FROM connections WHERE namespace=? AND (source=? OR target=?)",
                (key, place.id, place.id),
            )

    def put_connection(self, connection):
        if not isinstance(connection, Connection):
            raise ValueError("Expected Connection")
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            endpoints = db.execute(
                "SELECT id FROM places WHERE namespace=? AND id IN (?,?)",
                (self.archive.identity.key, connection.source, connection.target),
            ).fetchall()
            if len(endpoints) != 2:
                raise ValueError("Connection endpoints must exist in this namespace")
            db.execute(
                "INSERT INTO connections VALUES(?,?,?,?,?) "
                "ON CONFLICT(namespace,source,target,kind) "
                "DO UPDATE SET payload=excluded.payload",
                (
                    self.archive.identity.key,
                    connection.source,
                    connection.target,
                    connection.kind,
                    json.dumps(asdict(connection)),
                ),
            )

    def route(
        self,
        source,
        target,
        *,
        verified_after_ns,
        capabilities=(),
        allow_teleport=False,
        node_limit=1024,
    ):
        """Return a bounded coarse path or empty; directed edges require verification.

        Callers supply a wall-clock freshness cutoff and satisfied preconditions.
        This graph cannot authorize terrain edits, teleports or motor commands.
        """
        if (
            type(node_limit) is not int
            or not 1 <= node_limit <= 10000
            or type(verified_after_ns) is not int
            or verified_after_ns < 0
            or type(allow_teleport) is not bool
        ):
            raise ValueError("Invalid coarse routing bounds")
        with self.archive.connect() as db:
            db.execute("BEGIN")
            gap = db.execute(
                "SELECT trusted FROM coverage WHERE namespace=?",
                (self.archive.identity.key,),
            ).fetchone()
            if gap is not None and not gap[0]:
                raise RuntimeError("Archive has an observation gap; rescan required")
            rows = db.execute(
                "SELECT payload FROM places WHERE namespace=? LIMIT ?",
                (self.archive.identity.key, node_limit + 1),
            ).fetchall()
            if len(rows) > node_limit:
                raise RuntimeError("Coarse graph node budget exceeded")
            places = {
                p.id: p
                for row in rows
                if (p := Place(**json.loads(row[0]))).verified_at_ns
                >= verified_after_ns
            }
            if source not in places or target not in places:
                return ()
            frontier, costs, previous = [(0, source)], {source: 0}, {}
            while frontier:
                cost, node = heapq.heappop(frontier)
                if cost != costs[node]:
                    continue
                if node == target:
                    path = [node]
                    while node in previous:
                        node = previous[node]
                        path.append(node)
                    return tuple(reversed(path))
                edges = db.execute(
                    "SELECT payload FROM connections "
                    "WHERE namespace=? AND source=? LIMIT ?",
                    (self.archive.identity.key, node, node_limit + 1),
                ).fetchall()
                if len(edges) > node_limit:
                    raise RuntimeError("Coarse graph edge budget exceeded")
                for row in edges:
                    edge = Connection(**json.loads(row[0]))
                    if (
                        edge.target not in places
                        or edge.verified_at_ns < verified_after_ns
                        or edge.kind == "teleport"
                        and not allow_teleport
                        or not set(edge.requirements) <= set(capabilities)
                    ):
                        continue
                    total = cost + edge.cost
                    if total < costs.get(edge.target, math.inf):
                        costs[edge.target], previous[edge.target] = total, node
                        heapq.heappush(frontier, (total, edge.target))
            return ()
