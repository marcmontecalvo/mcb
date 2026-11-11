import pytest
from test_spatial_memory import observation

from mclab import companion_body, nbt
from mclab.awareness import Awareness
from mclab.spatial_memory import SpatialMemory, world_archive


def level(tmp_path, seed, name="world"):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    settings = {"seed": (nbt.LONG, seed)}
    data = {"WorldGenSettings": (nbt.COMPOUND, settings)}
    (folder / "level.dat").write_bytes(nbt.dumps({"Data": (nbt.COMPOUND, data)}))
    return folder


def at(*position, tick):
    obs = observation(position, tick=tick)
    obs["body"] = {"position": [position[0] + 0.5, position[1] + 1, position[2] + 0.5]}
    return obs


def test_world_archive_is_keyed_by_level_seed_and_dimension(tmp_path):
    store = tmp_path / "memory"
    first = world_archive(level(tmp_path, 7), Awareness(), store)
    again = world_archive(level(tmp_path, 7), Awareness(), store)
    assert again.path == first.path and again.identity == first.identity
    assert store in first.path.parents
    reseeded = world_archive(level(tmp_path, 8), Awareness(), store)
    assert reseeded.identity.key != first.identity.key
    nether = world_archive(
        level(tmp_path, 7), Awareness(), store, dimension="minecraft:the_nether"
    )
    assert nether.identity.key != first.identity.key


def test_remembered_sections_load_on_demand_and_keep_their_source(tmp_path):
    world, store = level(tmp_path, 7), tmp_path / "memory"
    old = SpatialMemory(world_archive(world, Awareness(), store), "session-1")
    old.ingest(at(0, 0, 0, tick=5))
    old.close()
    archive = world_archive(world, Awareness(), store)
    # A 50,000-block world must not load every stored key into RAM at start.
    archive.section_keys = lambda: pytest.fail("loaded every section key")
    memory = SpatialMemory(archive, "session-2")
    try:
        memory.ingest(at(20, 0, 0, tick=1))
        memory.recall_surroundings()
        remembered = memory.cells.record((0, 0, 0))
        assert remembered["observation_session"] == "session-1"
        assert remembered["observed_tick"] == 5 and remembered["observed_at_ns"] > 0
        assert (0, 0, 0) not in memory.fresh(1)  # never fresh motor evidence
    finally:
        memory.close()


def test_a_gap_in_an_earlier_session_does_not_block_later_recall(tmp_path):
    world, store = level(tmp_path, 7), tmp_path / "memory"
    world_archive(world, Awareness(), store).mark_gap()
    assert (
        world_archive(world, Awareness(), store).recall((0, 0, 0), 4, 4, limit=1) == []
    )


def test_only_sessions_in_a_kept_world_use_world_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(companion_body, "_level_dir", lambda: level(tmp_path, 7))
    monkeypatch.setattr(companion_body, "WORLD_MEMORY", tmp_path / "memory")
    run = tmp_path / "run"

    def archive(world_memory=None):
        return companion_body.session_archive(run, Awareness(), "ep", world_memory)

    assert archive().path.parent == tmp_path / "memory"
    assert archive(False).path == run / "map.sqlite3"
    monkeypatch.setattr(companion_body, "_restored_world", True)
    assert archive().path == run / "map.sqlite3"  # a restored snapshot is reset


def test_an_unreadable_seed_keeps_the_session_on_a_per_run_archive(
    tmp_path, monkeypatch, caplog
):
    broken = tmp_path / "world"
    broken.mkdir()
    (broken / "level.dat").write_bytes(b"not nbt")
    assert world_archive(broken, Awareness(), tmp_path / "memory") is None
    monkeypatch.setattr(companion_body, "_level_dir", lambda: broken)
    monkeypatch.setattr(companion_body, "WORLD_MEMORY", tmp_path / "memory")
    run = tmp_path / "run"
    with caplog.at_level("WARNING"):
        archive = companion_body.session_archive(run, Awareness(), "ep")
    assert archive.path == run / "map.sqlite3"
    assert "world memory off" in caplog.text
    assert not (tmp_path / "memory").exists()


def test_a_gap_left_by_an_earlier_session_drops_its_terrain_but_keeps_places(
    tmp_path,
):
    from mclab.place_graph import PlaceGraph

    world, store = level(tmp_path, 7), tmp_path / "memory"
    old = SpatialMemory(world_archive(world, Awareness(), store), "session-1")
    old.ingest(at(0, 0, 0, tick=5))
    old.close()
    archive = world_archive(world, Awareness(), store)
    PlaceGraph(archive).add("home", (0, 64, 0))
    PlaceGraph(archive).add("camp", (90, 64, 0))
    archive.mark_gap()  # writes were lost: what is stored may be stale
    reopened = world_archive(world, Awareness(), store)
    assert reopened.recall((0, 0, 0), 4, 4, limit=10) == []  # not stale terrain
    graph = PlaceGraph(reopened)
    assert graph.named("home").position == (0, 64, 0)
    assert graph.route("home", "camp", verified_after_ns=0) == ()  # no raise
    # without a gap, opening keeps what was stored
    SpatialMemory(reopened, "session-2").close()
    kept = SpatialMemory(world_archive(world, Awareness(), store), "session-3")
    kept.ingest(at(0, 0, 0, tick=1))
    kept.close()
    assert world_archive(world, Awareness(), store).recall((0, 0, 0), 4, 4, limit=1)


def test_recall_filters_wanted_sections_before_the_section_budget(tmp_path):
    archive = world_archive(level(tmp_path, 7), Awareness(), tmp_path / "memory")
    archive.write(
        observation(*[(x * 16, 0, z * 16) for x in range(-3, 4) for z in range(-3, 4)])[
            "visible_blocks"
        ]
    )
    far = (3, 0, 3)  # among the farthest of 49 stored sections
    cells = archive.recall((0, 0, 0), 64, 16, limit=4096, section_limit=32, only={far})
    assert [c["position"] for c in cells] == [[48, 0, 48]]
