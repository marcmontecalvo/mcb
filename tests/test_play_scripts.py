"""The arena-hunt and combat-trial scripts: kit materials reach the run."""

import json
import runpy
from pathlib import Path

import pytest

from mclab import arena_hunt as ah
from mclab import combat_trial as ct
from mclab.arena import client as arena

ROOT = Path(__file__).resolve().parents[1]


class FakeApi:
    @classmethod
    def from_runtime(cls, *args, **kwargs):
        return cls()

    def events(self, *args, **kwargs):
        return {"latest": 0}


def _script(name: str) -> dict:
    namespace = runpy.run_path(str(ROOT / "scripts" / name))
    namespace["main"].__globals__["ApiClient"] = FakeApi
    return namespace


class FakeAdmin:
    def __init__(self, refuse=None):
        self.waited, self.refuse = [], refuse

    def wait_ready(self, names, **kwargs):
        self.waited.append((names, kwargs))
        if self.refuse:
            raise arena.ArenaError("players_not_ready", self.refuse)


def _hunt(tmp_path, monkeypatch, admin=None):
    seen = {}

    def series(api, admin, name, waves, **kwargs):
        seen.update(kwargs, api=api, waves=waves)
        path = tmp_path / "hunt.json"
        path.write_text(json.dumps({"summary": {}}), "utf-8")
        return path

    admin = admin or FakeAdmin()
    monkeypatch.setattr(ah, "run_series", series)
    monkeypatch.setattr(arena.ArenaClient, "from_state", classmethod(lambda c: admin))
    return _script("arena-hunt.py")["main"], seen, admin


def test_a_hunt_hands_out_its_kit_in_the_materials_asked(tmp_path, monkeypatch):
    main, seen, _ = _hunt(tmp_path, monkeypatch)

    code = main(["--arena", "vault", "--waves", "mixed-10", "--kit", "sword+armor",
                 "--tier", "diamond", "--armor", "netherite"])  # fmt: skip

    assert code == 0
    assert seen["kit"] == ct.kit("sword+armor", tier="diamond", armor="netherite")


def test_a_hunt_waits_for_its_players_before_the_first_match(tmp_path, monkeypatch):
    main, seen, admin = _hunt(tmp_path, monkeypatch)

    code = main(["--arena", "vault", "--waves", "mixed-10",
                 "--players", "MCBot", "Ann", "--ready-timeout", "120"])  # fmt: skip

    assert code == 0
    ((names, kwargs),) = admin.waited
    assert names == ["MCBot", "Ann"]
    assert kwargs["timeout"] == 120 and kwargs["arena"] == "vault"
    assert seen["participants"] == ["MCBot", "Ann"]
    assert seen["ready_timeout"] == 120  # and again before every later match


def test_a_hunt_whose_players_never_come_stops_before_it_starts(
    tmp_path, monkeypatch, capsys
):
    main, seen, _ = _hunt(tmp_path, monkeypatch, FakeAdmin(refuse="Ann has not joined"))

    code = main(["--arena", "vault", "--waves", "mixed-10", "--players", "Ann"])

    assert code == 2 and seen == {}
    assert "Ann has not joined" in capsys.readouterr().err


def test_a_team_match_on_the_arena_server_readies_its_world_and_roster(
    tmp_path, monkeypatch
):
    from mclab.arena import world

    main, seen, admin = _hunt(tmp_path, monkeypatch)
    prepared = []

    def prepare(client, name, *, echo, **kwargs):
        prepared.append((client, name))
        return {"arena": name, "origin": (1, 2, 3), "spawn": (0, 102, 0), "built": []}

    monkeypatch.setattr(world, "prepare", prepare)

    code = main(["--arena", "duel", "--mode", "teams", "--team", "red=MCBot,Ann",
                 "--team", "blue=Bob", "--op", "Ann", "--deop", "Bob",
                 "--world"])  # fmt: skip

    assert code == 0 and prepared == [(admin, "duel")]
    assert seen["mode"] == "teams"
    assert seen["teams"] == {"red": ["MCBot", "Ann"], "blue": ["Bob"]}
    assert seen["ops"] == {"Ann": True, "Bob": False}
    assert seen["exit"] == (0, 102, 0)
    ((names, _),) = admin.waited
    assert names == ["MCBot", "Ann", "Bob"]


def test_a_match_without_the_bot_needs_no_companion(tmp_path, monkeypatch):
    main, seen, _ = _hunt(tmp_path, monkeypatch)
    apis = []
    main.__globals__["ApiClient"] = type(
        "Counted", (FakeApi,), {"from_runtime": classmethod(lambda c: apis.append(1))}
    )

    code = main(["--arena", "duel", "--mode", "teams", "--team", "red=Ann",
                 "--team", "blue=Bob"])  # fmt: skip

    assert code == 0 and apis == [] and seen["api"] is None and seen["waves"] == []


def test_a_match_refuses_a_roster_its_mode_cannot_hold(capsys):
    main = _script("arena-hunt.py")["main"]
    for argv, says in [
        (["--mode", "race", "--team", "red=Ann", "--team", "blue=Bob"], "--waves"),
        (["--waves", "mixed-10", "--team", "red=Ann"], "--team"),
        (["--mode", "teams", "--players", "Ann"], "--team"),
    ]:
        with pytest.raises(SystemExit):
            main(["--arena", "duel", *argv])
        assert says in capsys.readouterr().err


def test_a_hunt_refuses_a_material_before_it_starts(capsys):
    main = _script("arena-hunt.py")["main"]

    with pytest.raises(SystemExit):
        main(["--arena", "vault", "--waves", "mixed-10", "--tier", "leather"])
    assert "weapon material" in capsys.readouterr().err


def test_combat_trials_fight_in_the_materials_asked(tmp_path, monkeypatch):
    seen = {}

    def matrix(client, weapons, mobs, **kwargs):
        seen.update(kwargs, weapons=weapons, mobs=mobs)
        path = tmp_path / "trials.json"
        path.write_text(json.dumps({"summary": {}, "regressions": []}), "utf-8")
        return path

    monkeypatch.setattr(ct, "run_matrix", matrix)
    main = _script("combat-trial.py")["main"]

    code = main(["--weapons", "sword+armor", "--mobs", "zombie", "--arena=1,2,3",
                 "--tier", "golden", "--armor", "chainmail"])  # fmt: skip

    assert code == 0
    assert seen["gear"] == {"tier": "golden", "armor": "chainmail"}
    assert seen["weapons"] == ["sword+armor"]


def test_combat_trials_refuse_a_material_before_they_start(capsys):
    main = _script("combat-trial.py")["main"]

    with pytest.raises(SystemExit):
        main(["--weapons", "armor", "--mobs", "zombie", "--armor", "stone"])
    assert "armor material" in capsys.readouterr().err
