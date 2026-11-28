import pytest

from mclab import recorder


@pytest.fixture(autouse=True)
def isolated_arena_lock(tmp_path, monkeypatch):
    """Unit transports must never share a lock with a real running experiment."""
    monkeypatch.setattr(recorder, "ROOT", tmp_path)


@pytest.fixture(autouse=True)
def isolated_launch_records(tmp_path, monkeypatch):
    """A launch record written by a test must not land in the real results/
    (they were ~2/3 of results/launches, all recorded as failed launches)."""
    from mclab import run_launch

    monkeypatch.setattr(run_launch, "LAUNCHES", tmp_path / "results/launches")


@pytest.fixture(autouse=True)
def no_real_orphan_kills(monkeypatch):
    """A unit test that reaches server start or stop must never kill a real lab JVM."""
    from mclab import server

    monkeypatch.setattr(server, "orphans", lambda: [])


@pytest.fixture(autouse=True)
def isolated_arena_roster(tmp_path, monkeypatch):
    """A match a test starts must never write the real saved player names, nor
    read or rename the arena server's real bots."""
    from mclab import arena_roster, play_runs

    monkeypatch.setattr(
        arena_roster, "ROSTER_FILE", tmp_path / "runtime/arena-roster.json"
    )
    monkeypatch.setattr(
        play_runs, "bots_runtime", lambda root=None: tmp_path / "runtime/arena-bots"
    )


@pytest.fixture(autouse=True)
def isolated_lobby_version(tmp_path, monkeypatch):
    """Building the arena lobby in a test must never record its version in the
    arena instance's real runtime folder."""
    from mclab.arena import world

    monkeypatch.setattr(
        world, "lobby_runtime_dir", lambda: tmp_path / "runtime/arena-instance"
    )
