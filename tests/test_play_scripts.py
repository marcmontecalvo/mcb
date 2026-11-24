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


def test_a_hunt_hands_out_its_kit_in_the_materials_asked(tmp_path, monkeypatch):
    seen = {}

    def series(api, admin, name, waves, **kwargs):
        seen.update(kwargs)
        path = tmp_path / "hunt.json"
        path.write_text(json.dumps({"summary": {}}), "utf-8")
        return path

    monkeypatch.setattr(ah, "run_series", series)
    monkeypatch.setattr(arena.ArenaClient, "from_state", classmethod(lambda c: None))
    main = _script("arena-hunt.py")["main"]

    code = main(["--arena", "vault", "--waves", "mixed-10", "--kit", "sword+armor",
                 "--tier", "diamond", "--armor", "netherite"])  # fmt: skip

    assert code == 0
    assert seen["kit"] == ct.kit("sword+armor", tier="diamond", armor="netherite")


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
