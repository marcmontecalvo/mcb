from dataclasses import replace

import pytest
from test_spatial_memory import identity

from mclab.place_graph import Connection, Place, PlaceGraph
from mclab.spatial_memory import MapArchive


def graph(tmp_path):
    archive = MapArchive(tmp_path / "map.db", identity())
    result = PlaceGraph(archive)
    for i, name in enumerate(("home", "pass", "hill")):
        result.put_place(
            Place(name, name.title(), (i * 16, 80, 0), "observed-route", 100)
        )
    return result


def edge(source, target, kind="walk", cost=1, requirements=()):
    return Connection(
        source, target, kind, cost, requirements, "live-verification", 100
    )


def test_persistent_coarse_detour_is_directed_and_invalidates_on_change(tmp_path):
    g = graph(tmp_path)
    g.put_connection(edge("home", "pass"))
    g.put_connection(edge("pass", "hill"))
    reopened = PlaceGraph(MapArchive(g.archive.path, identity()))
    assert reopened.route("home", "hill", verified_after_ns=90) == (
        "home",
        "pass",
        "hill",
    )
    assert reopened.route("hill", "home", verified_after_ns=90) == ()
    assert reopened.route("home", "hill", verified_after_ns=101) == ()
    reopened.put_place(Place("pass", "Changed", (16, 81, 0), "changed-geometry", 101))
    assert reopened.route("home", "hill", verified_after_ns=90) == ()


def test_teleport_is_schema_only_and_requires_all_preconditions(tmp_path):
    g = graph(tmp_path)
    g.put_connection(edge("home", "pass"))
    g.put_connection(edge("pass", "hill"))
    g.put_connection(
        edge("home", "hill", "teleport", 0.1, ("activated", "access", "cost-paid"))
    )
    assert g.route("home", "hill", verified_after_ns=1) == ("home", "pass", "hill")
    assert g.route(
        "home",
        "hill",
        verified_after_ns=1,
        allow_teleport=True,
        capabilities=("activated",),
    ) == ("home", "pass", "hill")
    assert g.route(
        "home",
        "hill",
        verified_after_ns=1,
        allow_teleport=True,
        capabilities=("activated", "access", "cost-paid"),
    ) == ("home", "hill")
    with pytest.raises(ValueError, match="requirements"):
        edge("home", "hill", "teleport")


@pytest.mark.parametrize(
    "field,value",
    [("world", "other"), ("generation", "reset-2"), ("dimension", "nether")],
)
def test_places_and_connections_are_partitioned(tmp_path, field, value):
    g = graph(tmp_path)
    g.put_connection(edge("home", "hill"))
    other = PlaceGraph(
        MapArchive(g.archive.path, replace(identity(), **{field: value}))
    )
    assert other.route("home", "hill", verified_after_ns=1) == ()
    with pytest.raises(ValueError, match="namespace"):
        other.put_connection(edge("home", "hill"))


def test_coarse_graph_budgets_and_gaps_fail_closed(tmp_path):
    g = graph(tmp_path)
    with pytest.raises(RuntimeError, match="budget"):
        g.route("home", "hill", verified_after_ns=1, node_limit=2)
    g.archive.mark_gap()
    with pytest.raises(RuntimeError, match="gap"):
        g.route("home", "hill", verified_after_ns=1)


class _NoStorage:
    """An archive whose storage hangs: any connect is a failure of the test."""

    def __init__(self, archive):
        self.identity, self.path = archive.identity, archive.path

    def connect(self, writing=False):
        raise AssertionError("a place lookup touched storage")


def test_place_lookups_never_touch_storage(tmp_path):
    # The guard looks the base up on every poll; one connect there froze the body
    # 8.9 s while sqlite waited (combat matrix 20261123T211141Z, sword pig).
    g = graph(tmp_path)
    real, g.archive = g.archive, _NoStorage(g.archive)
    assert [p.name for p in g.listed()] == ["Home", "Pass", "Hill"]
    assert g.named("Pass").position == (16, 80, 0)
    assert g.named("nowhere") is None
    assert g.nearest((30, 80, 0)).name == "Hill"
    g.archive = real


def test_place_writes_are_seen_at_once_and_survive_reopen(tmp_path):
    g = graph(tmp_path)
    g.add("base", (5, 70, 5), kind="home")
    g.rename("Pass", "Gap")
    g.remove("Hill")
    g.put_place(Place("home", "Home", (1, 81, 1), "observed-route", 200))
    expected = [("Home", (1, 81, 1)), ("Gap", (16, 80, 0)), ("base", (5, 70, 5))]
    assert [(p.name, p.position) for p in g.listed()] == expected
    assert [p.name for p in g.listed("home")] == ["base"]
    assert g.remember("chest", (2, 64, 2)) == g.remember("chest", (2, 64, 2))
    reopened = PlaceGraph(MapArchive(g.archive.path, identity()))
    assert reopened.listed() == g.listed()


def test_a_failed_place_write_changes_nothing(tmp_path):
    g = graph(tmp_path)
    real, g.archive = g.archive, _NoStorage(g.archive)
    for write in (
        lambda: g.add("base", (5, 70, 5)),
        lambda: g.rename("Pass", "Gap"),
        lambda: g.remove("Hill"),
    ):
        with pytest.raises(AssertionError):
            write()
    g.archive = real
    assert [p.name for p in g.listed()] == ["Home", "Pass", "Hill"]


def test_containers_round_trip_across_reopen(tmp_path):
    g = graph(tmp_path)
    g.save_containers({(1, 64, 2): {"minecraft:dirt": 5}, (3, 64, 2): {}})
    reopened = PlaceGraph(MapArchive(g.archive.path, identity()))
    assert reopened.load_containers() == {
        (1, 64, 2): {"minecraft:dirt": 5},
        (3, 64, 2): {},
    }
    reopened.save_containers({(3, 64, 2): {"minecraft:stone": 1}})
    assert g.load_containers() == {(3, 64, 2): {"minecraft:stone": 1}}
