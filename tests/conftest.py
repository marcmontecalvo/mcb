import pytest

from mclab import recorder


@pytest.fixture(autouse=True)
def isolated_arena_lock(tmp_path, monkeypatch):
    """Unit transports must never share a lock with a real running experiment."""
    monkeypatch.setattr(recorder, "ROOT", tmp_path)


@pytest.fixture(autouse=True)
def no_real_orphan_kills(monkeypatch):
    """A unit test that reaches server start or stop must never kill a real lab JVM."""
    from mclab import server

    monkeypatch.setattr(server, "orphans", lambda: [])
