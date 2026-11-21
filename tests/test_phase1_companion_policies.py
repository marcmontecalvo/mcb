import json
from types import SimpleNamespace

from mclab import companion_body, companion_jobs
from mclab.awareness import Awareness
from mclab.gather_options import GatherOptions
from mclab.memory_policy import MemoryPolicy


class _Body:
    terrain_policy = None

    def __init__(self, inventory=None):
        self._inventory = inventory or {}
        self.precise_stance = False

    def inventory(self):
        return dict(self._inventory)

    def world(self, x, y, z):
        return None  # nothing seen: the build needs the whole template


def test_session_archive_respects_memory_mode_and_fresh_world_isolation(
    tmp_path, monkeypatch
):
    world_archive = object()
    world_calls = []
    monkeypatch.setattr(companion_body, "_level_dir", lambda: tmp_path / "level")
    monkeypatch.setattr(
        companion_body,
        "world_archive",
        lambda *args: world_calls.append(args) or world_archive,
    )
    awareness = Awareness()

    current = companion_body.session_archive(
        tmp_path / "current", awareness, "e1", memory_policy=MemoryPolicy("current")
    )
    session = companion_body.session_archive(
        tmp_path / "session", awareness, "e2", memory_policy=MemoryPolicy("session")
    )
    assert current.path == tmp_path / "current/map.sqlite3"
    assert session.path == tmp_path / "session/map.sqlite3"
    assert world_calls == []

    persistent = companion_body.session_archive(
        tmp_path / "persistent",
        awareness,
        "e3",
        memory_policy=MemoryPolicy("persistent"),
    )
    assert persistent is world_archive
    assert len(world_calls) == 1

    monkeypatch.setattr(companion_body, "_restored_world", True)
    restored = companion_body.session_archive(
        tmp_path / "restored",
        awareness,
        "e4",
        world_memory=True,
        memory_policy=MemoryPolicy("persistent"),
    )
    assert restored.path == tmp_path / "restored/map.sqlite3"
    assert len(world_calls) == 1


def test_start_session_passes_resolved_memory_policy_to_spatial_memory(
    tmp_path, monkeypatch
):
    policy = MemoryPolicy(
        "session",
        use_historical_geometry=False,
        active_map_budget_mb=16,
        persistence_queue_budget_mb=8,
    )
    observed = {}

    class Memory:
        def __init__(self, archive, session, *, policy):
            observed.update(archive=archive, session=session, policy=policy)
            self.archive = object()
            self.cells = {}
            self.policy = policy

        def close(self):
            pass

    class Body:
        def __init__(self, *args, **kwargs):
            pass

        def observe(self):
            pass

        def position(self):
            return (1, 2, 3)

    bridge = SimpleNamespace(
        episode_id="episode-1",
        http=lambda _: {
            "state": "running",
            "awareness": {"coverage": {"sample_tick": 1}},
        },
        send=lambda *args, **kwargs: "request-1",
        acknowledge=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(companion_body, "_new_identity", lambda *args: None)
    monkeypatch.setattr(companion_body, "session_archive", lambda *args: "archive")
    monkeypatch.setattr(companion_body, "SpatialMemory", Memory)
    monkeypatch.setattr(
        companion_body,
        "PlaceGraph",
        lambda archive: SimpleNamespace(
            named=lambda _: object(), add=lambda *a, **k: None
        ),
    )
    monkeypatch.setattr(companion_body, "CompanionBody", Body)
    monkeypatch.setattr(companion_body, "standable", lambda *args: True)

    _, memory = companion_body.start_session(
        bridge,
        tmp_path,
        (1, 2, 3),
        awareness=Awareness(),
        memory_policy=policy,
    )
    assert memory.policy is policy
    assert observed["policy"] is policy
    assert observed["session"] == "episode-1"


def test_build_job_propagates_gather_options_to_planning_and_execution(
    tmp_path, monkeypatch
):
    build_path = tmp_path / "build.json"
    build_path.write_text(
        json.dumps({"materials": {"minecraft:stone": 2}}), encoding="utf-8"
    )
    options = GatherOptions(
        tool_choice="best_available",
        ore_collect="nearest",
        ore_vision=False,
        vision_radius=12,
        search_radius=9,
        strip_leg=30,
        strip_turn=7,
        strip_max_legs=3,
        spare_pickaxes=1,
        carry_logs=4,
    )
    planned, executed = [], []
    monkeypatch.setattr(
        companion_jobs.bp.Template,
        "from_build",
        lambda *args: SimpleNamespace(cells=()),
    )
    monkeypatch.setattr(companion_jobs.bp, "tool_needs", lambda *args: {})
    monkeypatch.setattr(
        companion_jobs.gp,
        "plan",
        lambda *args, **kwargs: planned.append(kwargs["kit"])
        or SimpleNamespace(tasks=()),
    )
    monkeypatch.setattr(
        companion_jobs,
        "run_gather",
        lambda *args, **kwargs: executed.append(kwargs["opts"])
        or SimpleNamespace(stop="done"),
    )

    def run_build(*args, **kwargs):
        kwargs["on_need_materials"]({"minecraft:stone": 1})
        return SimpleNamespace(stop="done")

    monkeypatch.setattr(companion_jobs, "run_build", run_build)
    report = companion_jobs.build_job(
        build_path,
        (0, 0, 0),
        0,
        _Body(),
        SimpleNamespace(blocks={}),
        site_margin=None,
        gather_options=options,
    )
    assert report.stop == "built"
    assert len(planned) >= 2  # initial plan and a refill plan
    assert planned and all(value is options for value in planned)
    assert executed and all(value is options for value in executed)


def test_withdraw_and_stow_helpers_pass_gather_options(monkeypatch):
    options = GatherOptions()
    body = _Body({"minecraft:dandelion": 5})
    state = companion_jobs.GatherState()
    state.containers[(0, 0, 0)] = {"minecraft:stone": 4}
    observed = []
    monkeypatch.setattr(
        companion_jobs,
        "run_gather",
        lambda *args, **kwargs: observed.append(kwargs["opts"])
        or SimpleNamespace(stop="done"),
    )
    companion_jobs._take({"minecraft:stone": 2}, body, object(), state, options)
    assert observed == [options]
    companion_jobs._stow_junk({"minecraft:stone": 2}, body, object(), state, options)
    assert observed == [options, options]
