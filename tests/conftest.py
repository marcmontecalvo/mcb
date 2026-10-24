import pytest

from mclab import recorder


@pytest.fixture(autouse=True)
def isolated_arena_lock(tmp_path, monkeypatch):
    """Unit transports must never share a lock with a real running experiment."""
    monkeypatch.setattr(recorder, "ROOT", tmp_path)
