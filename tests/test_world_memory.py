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


def level_26(tmp_path, seed, name="world"):
    """Minecraft 26.3 layout: the seed lives in data/minecraft/world_gen_settings.dat
    and level.dat has none (play world seen Nov 23, 2026)."""
    folder = tmp_path / name
    (folder / "data/minecraft").mkdir(parents=True, exist_ok=True)
    (folder / "level.dat").write_bytes(
        nbt.dumps({"Data": (nbt.COMPOUND, {"LevelName": (nbt.STRING, name)})})
    )
    settings = {"seed": (nbt.LONG, seed)}
    (folder / "data/minecraft/world_gen_settings.dat").write_bytes(
        nbt.dumps({"data": (nbt.COMPOUND, settings), "DataVersion": (nbt.INT, 1)})
    )
    return folder


def test_world_archive_reads_the_seed_where_26_3_keeps_it(tmp_path):
    store = tmp_path / "memory"
    archive = world_archive(level_26(tmp_path, 20261123), Awareness(), store)
    assert archive is not None
    assert archive.identity.generation == "seed:20261123"
    # the same world in the old layout keeps the same identity
    (tmp_path / "old").mkdir()
    old = world_archive(level(tmp_path / "old", 20261123), Awareness(), store)
    assert old.identity.generation == archive.identity.generation


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


def test_check_scripts_never_use_world_memory():
    """Checks run in the arena or a restored copy; their results must not depend
    on earlier runs, so outside companion_world they turn world memory off."""
    from mclab.paths import ROOT

    for script in sorted((ROOT / "scripts").glob("check-*.py")):
        text = script.read_text(encoding="utf-8")
        if "start_session(" in text:
            assert "companion_world(" in text or "world_memory=False" in text, (
                script.name
            )


def test_remembered_cells_are_never_fresh_motor_evidence_even_at_a_reused_tick(
    tmp_path,
):
    # Item 6: memory.cells (with recalled history) feeds stance choice and planning
    # only; motor decisions read memory.fresh, which must exclude other sessions
    # even when the new session's tick counter reaches the old observed_tick.
    world, store = level(tmp_path, 7), tmp_path / "memory"
    old = SpatialMemory(world_archive(world, Awareness(), store), "session-1")
    old.ingest(at(0, 0, 0, tick=5))
    old.close()
    memory = SpatialMemory(world_archive(world, Awareness(), store), "session-2")
    try:
        memory.ingest(at(20, 0, 0, tick=5))
        memory.recall_surroundings()
        assert (0, 0, 0) in memory.cells
        assert (0, 0, 0) not in memory.fresh(5)
        assert (20, 0, 0) in memory.fresh(5)
    finally:
        memory.close()


def test_start_session_writes_home_before_the_first_terrain_write(
    tmp_path, monkeypatch
):
    """The first observe queues a large terrain write that holds the db lock for
    seconds; home written after it kept the bot still at spawn for 4.5 s
    (run 20261112T033833606944Z)."""
    from mclab.place_graph import PlaceGraph

    class Bridge:
        episode_id = "ep"

        def send(self, *args, **kwargs):
            # Creating the db costs about a second of disk syncs; done after the
            # spawn the bot stood through it (run 20261112T043759535110Z, 1.75 s).
            spawned.append((tmp_path / "map.sqlite3").exists())
            return "req"

        def acknowledge(self, *args, **kwargs):
            pass

        def http(self, path):
            return {"state": "running", "awareness": {"coverage": {"sample_tick": 0}}}

    seen, spawned = [], []

    class Body:
        def __init__(self, bridge, memory, **kwargs):
            self.memory = memory

        def observe(self):
            seen.append(PlaceGraph(self.memory.archive).named("home"))

        def position(self):
            return (9, 9, 9)

    monkeypatch.setattr(companion_body, "_level_dir", lambda: tmp_path / "world")
    monkeypatch.setattr(companion_body, "_new_identity", lambda bridge, kind: None)
    monkeypatch.setattr(companion_body, "CompanionBody", Body)
    monkeypatch.setattr(companion_body, "standable", lambda cells, pos: True)
    body, memory = companion_body.start_session(
        Bridge(), tmp_path, (1, 64, 2), world_memory=False
    )
    memory.close()
    assert spawned == [True]
    assert seen[0].position == (1, 64, 2)
    assert body.places.named("home").position == (1, 64, 2)
