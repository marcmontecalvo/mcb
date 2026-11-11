import json
import math

from test_spatial_memory import identity

from mclab.build_runner import Result
from mclab.companion_body import CompanionBody
from mclab.place_graph import Connection, Place, PlaceGraph
from mclab.spatial_memory import MapArchive


def graph(tmp_path):
    return PlaceGraph(MapArchive(tmp_path / "map.db", identity()))


def test_named_places_add_rename_remove_and_look_up(tmp_path):
    g = graph(tmp_path)
    g.add("home", (0, 64, 0), kind="home")
    g.add("mine", (100, 40, 0))
    g.put_connection(Connection("home", "mine", "walk", 100, (), "walked", 1))
    g.rename("mine", "deep mine")
    assert g.named("mine") is None
    assert g.named("deep mine").position == (100, 40, 0)
    # a rename is not a move: the walked connection survives
    assert g.route("home", "mine", verified_after_ns=0) == ("home", "mine")
    assert [p.name for p in g.listed()] == ["home", "deep mine"]
    g.remove("deep mine")
    assert g.named("deep mine") is None
    assert g.route("home", "mine", verified_after_ns=0) == ()
    reopened = PlaceGraph(MapArchive(g.archive.path, identity()))
    assert reopened.named("home").kind == "home"
    assert reopened.dimension == "minecraft:overworld"


def test_nearest_place_of_a_kind_and_remember_is_idempotent(tmp_path):
    g = graph(tmp_path)
    near = g.remember("furnace", (5, 64, 5))
    far = g.remember("furnace", (500, 64, 5))
    g.remember("chest", (1, 64, 1))
    g.put_connection(Connection(near.id, far.id, "walk", 1, (), "walked", 1))
    assert g.remember("furnace", (5, 64, 5)) == near  # no move: edges stay
    assert g.nearest((0, 64, 0), "furnace") == near
    assert g.nearest((400, 64, 0), "furnace") == far
    assert g.nearest((0, 64, 0), "home") is None
    assert len(g.listed("furnace")) == 2
    assert g.route(near.id, far.id, verified_after_ns=0) == (near.id, far.id)


def test_places_written_before_kinds_existed_still_load(tmp_path):
    g = graph(tmp_path)
    old = {
        "id": "old",
        "name": "Old",
        "position": [1, 2, 3],
        "provenance": "x",
        "verified_at_ns": 5,
    }
    with g.archive.connect() as db:
        db.execute(
            "INSERT INTO places VALUES(?,?,?)",
            (g.archive.identity.key, "old", json.dumps(old)),
        )
    assert g.named("Old") == Place("old", "Old", (1, 2, 3), "x", 5)


def test_body_records_useful_blocks_and_walks_to_a_named_place(tmp_path):
    body = CompanionBody(None, None)
    body.places = graph(tmp_path)
    body._obs = {"body": {"position": [0, 64, 0]}}
    body.op = lambda name, **f: Result(True, None, {"placed": f.get("state")})
    body.place_block((1, 64, 1), "minecraft:crafting_table", "minecraft:crafting_table")
    body.place_block((2, 64, 1), "minecraft:dirt", "minecraft:dirt")
    body.furnace((3, 64, 1), "status")
    body.container((4, 64, 1), "open")
    kinds = sorted(p.kind for p in body.places.listed())
    assert kinds == ["container", "crafting_table", "furnace"]
    walked = []
    body.walk_to = lambda pos, radius=1.5: walked.append(pos) or True
    body.places.add("camp", (9, 70, 9))
    assert body.go_to("camp") is True and walked == [(9, 70, 9)]
    assert body.go_to("nowhere") is False


def walker(tmp_path, *, step=48.0, stuck_after=None):
    """A body whose walk_to moves at most ``step`` blocks toward the goal."""
    body = CompanionBody(None, None)
    body.places = graph(tmp_path)
    body._obs = {"body": {"position": [0, 64, 0]}}
    legs = []

    def walk_to(pos, radius=1.5):
        here = body._obs["body"]["position"]
        legs.append((tuple(here), tuple(pos)))
        if stuck_after is not None and len(legs) > stuck_after:
            return False
        gap = math.dist(here, pos)
        t = 1.0 if gap <= step else step / gap
        body._obs["body"]["position"] = [
            a + (b - a) * t for a, b in zip(here, pos, strict=True)
        ]
        return t == 1.0

    body.walk_to = walk_to
    return body, legs


def test_a_far_walk_is_split_into_legs_inside_the_planner_horizon(tmp_path):
    body, legs = walker(tmp_path)
    assert body.walk_far((2000, 64, 0)) is True
    assert body.position() == (2000, 64, 0)
    assert len(legs) > 2000 / 64
    assert all(math.dist(a, b) <= 48.5 for a, b in legs)


def test_a_far_walk_gives_up_when_legs_stop_making_progress(tmp_path):
    body, legs = walker(tmp_path, stuck_after=3)
    assert body.walk_far((2000, 64, 0)) is False
    assert len(legs) < 10


def test_going_to_a_named_place_follows_and_records_walked_connections(tmp_path):
    body, legs = walker(tmp_path)
    g = body.places
    home = g.add("home", (0, 64, 0), kind="home")
    g.add("ford", (0, 64, 300))
    g.add("camp", (300, 64, 300))
    g.put_connection(Connection("home", "ford", "walk", 300, (), "walked", 1))
    g.put_connection(Connection("ford", "camp", "walk", 300, (), "walked", 1))
    assert body.go_to("camp") is True
    # the walk went through the ford rather than the straight diagonal
    assert any(b == (0, 64, 300) for _, b in legs)
    # and the legs actually walked are now verified connections
    assert g.route("home", "camp", verified_after_ns=2) == ("home", "ford", "camp")
    # a direct trip records its own connection from the nearest start place
    body._obs["body"]["position"] = list(home.position)
    g.add("tower", (0, 64, -500))
    assert body.go_to("tower") is True
    assert g.route("home", "tower", verified_after_ns=2) == ("home", "tower")
