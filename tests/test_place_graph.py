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
