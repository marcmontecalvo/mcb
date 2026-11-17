from pathlib import Path

import pytest

from mclab import dashboard


def test_save_retries_a_replace_blocked_by_a_reader(tmp_path, monkeypatch):
    # Windows: a reader with the file open makes the replace fail (WinError 5)
    real, calls = Path.replace, []

    def flaky(self, target):
        calls.append(target)
        if len(calls) < 3:
            raise PermissionError(5, "Access is denied")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(dashboard.time, "sleep", lambda s: None)
    dashboard.save(tmp_path / "session.json", {"id": "x"})
    assert len(calls) == 3
    assert (tmp_path / "session.json").read_text().strip().startswith("{")


def test_save_gives_up_when_the_file_stays_locked(tmp_path, monkeypatch):
    def locked(self, target):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(dashboard.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        dashboard.save(tmp_path / "session.json", {"id": "x"})
