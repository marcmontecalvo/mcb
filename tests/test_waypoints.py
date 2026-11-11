import json

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
