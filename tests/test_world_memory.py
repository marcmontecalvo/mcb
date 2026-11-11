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
