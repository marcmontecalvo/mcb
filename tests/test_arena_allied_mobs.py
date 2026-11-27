"""Allied mob groups in arena teams mode: schema parity, validation, targeting, and runs."""

from __future__ import annotations

import importlib.util
import json
import re

import pytest

from mclab import arena_hunt as ah
from mclab import companion_attack as attack
from mclab.arena import client as arena
from mclab.paths import ROOT


def _java_supported_types() -> set[str]:
    java = (
        ROOT
        / "mcb-body/src/main/java/com/vulpine/mcb/arena/ArenaAlliedMobs.java"
    ).read_text("utf-8")
    block = re.search(
        r"SUPPORTED_TYPES\s*=\s*Set\.of\((.*?)\);", java, flags=re.DOTALL
    )
    assert block, "SUPPORTED_TYPES not found in ArenaAlliedMobs.java"
    return set(re.findall(r'"(minecraft:[a-z0-9_]+)"', block.group(1)))


def test_supported_allied_mobs_match_java_curated_allowlist():
    expected = {
        "minecraft:zombie",
        "minecraft:husk",
        "minecraft:drowned",
        "minecraft:skeleton",
        "minecraft:stray",
        "minecraft:wither_skeleton",
        "minecraft:spider",
        "minecraft:cave_spider",
        "minecraft:pillager",
        "minecraft:vindicator",
        "minecraft:witch",
        "minecraft:iron_golem",
        "minecraft:wolf",
        "minecraft:piglin_brute",
        "minecraft:blaze",
        "minecraft:breeze",
    }
    assert arena.SUPPORTED_ALLIED_MOBS == expected
    assert _java_supported_types() == expected


def test_protocol_is_bumped_to_6_for_allied_mobs():
    assert arena.PROTOCOL == 6


def test_check_team_mobs_accepts_groups_kits_and_overrides():
    team_mobs = {
        "red": [
            {
                "type": "minecraft:skeleton",
                "count": 2,
                "name": "Red Archer",
                "kit": {
                    "give": [
                        {
                            "item": "minecraft:bow",
                            "count": 1,
                            "enchantments": {"minecraft:power": 2},
                        }
                    ],
                    "wear": True,
                },
                "overrides": [
                    {
                        "index": 0,
                        "name": "Captain",
                        "kit": {
                            "give": [
                                {"item": "minecraft:iron_helmet", "count": 1},
                                {"item": "minecraft:iron_sword", "count": 1},
                            ],
                            "clear_inventory": True,
                            "heal": True,
                            "wear": True,
                        },
                    }
                ],
            }
        ],
        "blue": [
            {"type": "minecraft:zombie", "count": 3},
            {"type": "minecraft:iron_golem", "count": 1, "name": "Sentinel"},
        ],
    }
    team_mob_kits = {
        "blue": {
            "give": [
                {"item": "minecraft:iron_chestplate", "count": 1},
                {
                    "item": "minecraft:splash_potion",
                    "count": 1,
                    "potion": "minecraft:strong_healing",
                },
            ],
            "wear": True,
        }
    }
    arena.check_team_mobs(team_mobs, team_mob_kits)


@pytest.mark.parametrize(
    "bad_mob",
    ["minecraft:ender_dragon", "minecraft:villager", "minecraft:sheep", "minecraft:creeper"],
)
def test_check_team_mobs_rejects_unsupported_mob_types(bad_mob):
    with pytest.raises(arena.ArenaError) as err:
        arena.check_team_mobs({"red": [{"type": bad_mob, "count": 1}]})
    assert err.value.reason == "unsupported_mob"


@pytest.mark.parametrize(
    "team_mobs, team_mob_kits",
    [
        ("not-a-dict", None),
        ({"Red!": [{"type": "minecraft:zombie", "count": 1}]}, None),
        ({"red": "not-a-list"}, None),
        ({"red": ["not-an-object"]}, None),
        ({"red": [{"type": "zombie", "count": 1}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 0}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 33}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": True}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "extra": 1}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "name": ""}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "name": "   "}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "name": "x" * 33}]}, None),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 2,
                        "overrides": [{"index": 2, "name": "OOB"}],
                    }
                ]
            },
            None,
        ),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 2,
                        "overrides": [
                            {"index": 0, "name": "A"},
                            {"index": 0, "name": "B"},
                        ],
                    }
                ]
            },
            None,
        ),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 1,
                        "overrides": [{"index": 0, "bogus": 1}],
                    }
                ]
            },
            None,
        ),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 1,
                        "kit": {"summon": []},
                    }
                ]
            },
            None,
        ),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 1,
                        "kit": {"give": [{"item": "sword", "count": 1}]},
                    }
                ]
            },
            None,
        ),
        (None, {"red": {"give": [{"item": "minecraft:iron_sword", "count": 0}]}}),
        (None, {"Red!": {"give": []}}),
        (None, "not-a-dict"),
    ],
)
def test_check_team_mobs_rejects_malformed_inputs(team_mobs, team_mob_kits):
    with pytest.raises(arena.ArenaError) as err:
        arena.check_team_mobs(team_mobs, team_mob_kits)
    assert err.value.reason == "bad_request"


def test_parse_team_mobs_cli_helper():
    parsed = arena.parse_team_mobs(
        ["red=skeleton*2+wolf@Fang", "blue=minecraft:zombie*3", "red=witch"]
    )
    assert parsed == {
        "red": [
            {"type": "minecraft:skeleton", "count": 2},
            {"type": "minecraft:wolf", "count": 1, "name": "Fang"},
            {"type": "minecraft:witch", "count": 1},
        ],
        "blue": [{"type": "minecraft:zombie", "count": 3}],
    }
    assert arena.parse_team_mobs(None) is None
    for bad in ("red", "red=", "=zombie", "Red!=zombie", "red=zombie*0", "red=sheep*2"):
        with pytest.raises(arena.ArenaError):
            arena.parse_team_mobs([bad])


def test_foes_includes_enemy_allied_mobs_and_excludes_friendly_allied_mobs():
    match = {
        "mode": "teams",
        "state": "running",
        "wave_mobs": [
            {
                "uuid": "w-1",
                "type": "minecraft:pillager",
                "position": [20.5, 64.0, 0.5],
                "health": 24.0,
            }
        ],
        "participants": [
            {
                "uuid": "u-bot",
                "name": "MCBot",
                "team": "red",
                "alive": True,
                "position": [0.5, 64.0, 0.5],
            },
            {
                "uuid": "u-enemy",
                "name": "Bob",
                "team": "blue",
                "alive": True,
                "position": [15.5, 64.0, 0.5],
            },
        ],
        "team_mobs": [
            {
                "uuid": "m-ally",
                "team": "red",
                "type": "minecraft:skeleton",
                "name": "Red Archer",
                "position": [1.5, 64.0, 0.5],
                "health": 20.0,
            },
            {
                "uuid": "m-enemy-z",
                "team": "blue",
                "type": "minecraft:zombie",
                "position": [8.5, 64.0, 0.5],
                "health": 20.0,
            },
            {
                "uuid": "m-enemy-w",
                "team": "blue",
                "type": "minecraft:witch",
                "name": "Blue Hex",
                "position": [12.5, 64.0, 0.5],
                "health": 26.0,
            },
        ],
    }
    rows = attack.foes(match, "MCBot")
    uuids = [r["uuid"] for r in rows]
    assert "m-ally" not in uuids
    assert uuids == ["w-1", "u-enemy", "m-enemy-z", "m-enemy-w"]
    # Witch threat weighting applies to enemy allied witches too (12 / 2.5 = 4.8 < 8)
    found = attack.hunt(
        {"name": "crossroads", "match": match},
        {"body": {"position": [0.5, 64.0, 0.5]}, "visible_entities": []},
        None,
        "MCBot",
    )
    assert found.uuid == "m-enemy-w" and found.name == "Blue Hex"


class _MobOnlyArena:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.index = 0
        self.started = []
        self.stopped = []
        self.boards = []
        self.recaps = []

    def start(self, name, mode, **kwargs):
        self.started.append({"name": name, "mode": mode, **kwargs})
        self.index = 0
        return {"name": name, "mode": mode}

    def status(self, name):
        snap = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return {"name": name, "match": snap, "match_state": snap["state"]}

    def stop(self, name):
        self.stopped.append(name)

    def board(self, name, title, rows):
        self.boards.append((name, title, rows))

    def recap(self, name, title, players, **kwargs):
        self.recaps.append((name, title, players, kwargs))


def test_mob_only_series_runs_without_players_and_records_allied_mob_artifacts(tmp_path):
    now = [0.0]

    def clock():
        return now[0]

    def sleep(s):
        now[0] += s

    team_mobs = {
        "red": [{"type": "minecraft:skeleton", "count": 2}],
        "blue": [{"type": "minecraft:zombie", "count": 3}],
    }
    team_mob_kits = {
        "red": {"give": [{"item": "minecraft:bow", "count": 1}]}
    }
    snapshots = [
        {
            "mode": "teams",
            "state": "running",
            "participants": [],
            "team_mobs": [
                {"uuid": "r1", "team": "red", "type": "minecraft:skeleton", "position": [1, 64, 1], "health": 20.0},
                {"uuid": "b1", "team": "blue", "type": "minecraft:zombie", "position": [9, 64, 9], "health": 20.0},
            ],
            "teams_alive": {"red": 0, "blue": 0},
            "teams_allied_mobs_alive": {"red": 2, "blue": 3},
            "teams_total_alive": {"red": 2, "blue": 3},
            "allied_team_kills": {"red": 0, "blue": 0},
        },
        {
            "mode": "teams",
            "state": "won",
            "winner": "red",
            "participants": [],
            "team_mobs": [
                {"uuid": "r1", "team": "red", "type": "minecraft:skeleton", "position": [5, 64, 5], "health": 14.0},
            ],
            "teams_alive": {"red": 0, "blue": 0},
            "teams_allied_mobs_alive": {"red": 1, "blue": 0},
            "teams_total_alive": {"red": 1, "blue": 0},
            "allied_team_kills": {"red": 3, "blue": 1},
        },
    ]
    fake_arena = _MobOnlyArena(snapshots)
    path = ah.run_series(
        None,
        fake_arena,
        "crossroads",
        [],
        repeat=2,
        timeout=60,
        out_dir=tmp_path,
        mode="teams",
        team_mobs=team_mobs,
        team_mob_kits=team_mob_kits,
        echo=lambda _line: None,
        clock=clock,
        sleep=sleep,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["team_mobs"] == team_mobs
    assert record["team_mob_kits"] == team_mob_kits
    assert record["participants"] == []
    assert len(record["runs"]) == 2
    assert fake_arena.started[0]["team_mobs"] == team_mobs
    assert fake_arena.started[0]["team_mob_kits"] == team_mob_kits
    assert fake_arena.stopped == []
    # Mob-only series must not send an invalid empty-players arena_recap
    assert fake_arena.recaps == []
    run0 = record["runs"][0]
    assert run0["outcome"] == "won" and run0["winner"] == "red"
    assert run0["teams_allied_mobs_alive"] == {"red": 1, "blue": 0}
    assert run0["teams_total_alive"] == {"red": 1, "blue": 0}
    assert run0["allied_team_kills"] == {"red": 3, "blue": 1}
    assert record["summary"]["winners"] == {"red": 2}
    assert record["summary"]["allied_team_kills"] == {"red": 6, "blue": 2}


def test_arena_hunt_script_cli_accepts_team_mob_and_team_mobs_json(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "arena_hunt_script_allied", ROOT / "scripts/arena-hunt.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    parser = mod._parser()
    args = parser.parse_args(
        [
            "--arena",
            "crossroads",
            "--mode",
            "teams",
            "--team-mob",
            "red=skeleton*2",
            "--team-mob",
            "blue=zombie*3",
        ]
    )
    assert args.team_mob == ["red=skeleton*2", "blue=zombie*3"]

    cfg_path = tmp_path / "mobs.json"
    cfg_path.write_text(
        json.dumps(
            {
                "team_mobs": {
                    "red": [{"type": "minecraft:skeleton", "count": 2}],
                    "blue": [{"type": "minecraft:zombie", "count": 3}],
                },
                "team_mob_kits": {
                    "red": {"give": [{"item": "minecraft:bow", "count": 1}]}
                },
            }
        ),
        encoding="utf-8",
    )
    args_json = parser.parse_args(
        [
            "--arena",
            "crossroads",
            "--mode",
            "teams",
            "--team-mobs-json",
            str(cfg_path),
        ]
    )
    assert args_json.team_mobs_json == str(cfg_path)

    # Rejects --team-mob in non-teams mode
    with pytest.raises(SystemExit) as bad_mode:
        mod.main(
            [
                "--arena",
                "vault",
                "--mode",
                "waves",
                "--wave",
                "zombie*1",
                "--team-mob",
                "red=skeleton*2",
            ]
        )
    assert bad_mode.value.code == 2

    # Rejects combining --team-mob and --team-mobs-json
    with pytest.raises(SystemExit) as both_flags:
        mod.main(
            [
                "--arena",
                "crossroads",
                "--mode",
                "teams",
                "--team-mob",
                "red=skeleton*2",
                "--team-mobs-json",
                str(cfg_path),
            ]
        )
    assert both_flags.value.code == 2


@pytest.mark.parametrize(
    "team_mobs, team_mob_kits",
    [
        ({"red": [{"type": "minecraft:zombie", "count": 1, "name": "Bad\nName"}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "overrides": "nope"}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "overrides": ["nope"]}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "kit": "nope"}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "kit": {"wear": "yes"}}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "kit": {"give": "nope"}}]}, None),
        ({"red": [{"type": "minecraft:zombie", "count": 1, "kit": {"give": ["nope"]}}]}, None),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 1,
                        "kit": {"give": [{"item": "minecraft:bow", "extra": 1}]},
                    }
                ]
            },
            None,
        ),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 1,
                        "kit": {
                            "give": [
                                {
                                    "item": "minecraft:bow",
                                    "enchantments": {"power": 1},
                                }
                            ]
                        },
                    }
                ]
            },
            None,
        ),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 1,
                        "kit": {
                            "give": [
                                {
                                    "item": "minecraft:bow",
                                    "enchantments": {"minecraft:power": 99},
                                }
                            ]
                        },
                    }
                ]
            },
            None,
        ),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 1,
                        "kit": {
                            "give": [
                                {
                                    "item": "minecraft:potion",
                                    "potion": "healing",
                                }
                            ]
                        },
                    }
                ]
            },
            None,
        ),
        (
            {
                "red": [
                    {
                        "type": "minecraft:zombie",
                        "count": 1,
                        "kit": {
                            "give": [
                                {
                                    "item": "minecraft:bow",
                                    "enchantments": "nope",
                                }
                            ]
                        },
                    }
                ]
            },
            None,
        ),
    ],
)
def test_check_team_mobs_rejects_nested_kit_and_override_errors(team_mobs, team_mob_kits):
    with pytest.raises(arena.ArenaError) as err:
        arena.check_team_mobs(team_mobs, team_mob_kits)
    assert err.value.reason == "bad_request"


def test_foes_ignores_team_mobs_outside_teams_mode_or_without_position_or_dead():
    match = {
        "mode": "race",
        "state": "running",
        "participants": [
            {"uuid": "u-1", "name": "MCBot", "team": "red", "alive": True, "position": [0, 64, 0]},
        ],
        "wave_mobs": [
            {"uuid": "w-1", "type": "minecraft:zombie", "position": [5, 64, 0], "health": 20.0},
        ],
        "team_mobs": [
            {"uuid": "m-1", "team": "blue", "type": "minecraft:skeleton", "position": [2, 64, 0]},
        ],
    }
    assert [r["uuid"] for r in attack.foes(match, "MCBot")] == ["w-1"]
    teams_match = {
        **match,
        "mode": "teams",
        "team_mobs": [
            "not-a-dict",
            {"uuid": "m-no-team", "type": "minecraft:zombie", "position": [2, 64, 0]},
            {"uuid": "m-dead", "team": "blue", "type": "minecraft:zombie", "alive": False, "position": [2, 64, 0]},
            {"uuid": "m-no-pos", "team": "blue", "type": "minecraft:zombie"},
            {"uuid": "m-ok", "team": "blue", "type": "minecraft:skeleton", "position": [3, 64, 0]},
        ],
    }
    assert [r["uuid"] for r in attack.foes(teams_match, "MCBot")] == ["w-1", "m-ok"]


def test_hunt_and_run_series_reject_team_mobs_outside_teams_mode(tmp_path):
    mobs = {"red": [{"type": "minecraft:zombie", "count": 1}]}
    with pytest.raises(arena.ArenaError) as e1:
        ah.hunt(None, _MobOnlyArena([]), "vault", [], mode="waves", team_mobs=mobs, timeout=10)
    assert e1.value.reason == "bad_request"
    with pytest.raises(arena.ArenaError) as e2:
        ah.run_series(
            None, _MobOnlyArena([]), "vault", [], repeat=1, mode="race", team_mobs=mobs,
            timeout=10, out_dir=tmp_path,
        )
    assert e2.value.reason == "bad_request"


def test_arena_hunt_script_forwards_team_mobs_json_and_team_mob_to_run_series(
    monkeypatch, tmp_path
):
    spec = importlib.util.spec_from_file_location(
        "arena_hunt_script_forward", ROOT / "scripts/arena-hunt.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured = []

    class DummyClient:
        def require_protocol(self):
            return {"protocol": arena.PROTOCOL}

        def wait_ready(self, *args, **kwargs):
            pass

    def fake_run_series(*args, **kwargs):
        captured.append(kwargs)
        out = tmp_path / "run.json"
        out.write_text(json.dumps({"summary": {"runs": 1}}), encoding="utf-8")
        return out

    monkeypatch.setattr(arena.ArenaClient, "from_state", classmethod(lambda cls: DummyClient()))
    monkeypatch.setattr(ah, "run_series", fake_run_series)

    # 1) --team-mob (mob-only, no --team required!)
    rc1 = mod.main(
        [
            "--arena",
            "crossroads",
            "--mode",
            "teams",
            "--team-mob",
            "red=skeleton*2",
            "--team-mob",
            "blue=zombie*3",
            "--out",
            str(tmp_path),
        ]
    )
    assert rc1 == 0
    assert captured[-1]["team_mobs"] == {
        "red": [{"type": "minecraft:skeleton", "count": 2}],
        "blue": [{"type": "minecraft:zombie", "count": 3}],
    }
    assert captured[-1]["team_mob_kits"] is None
    assert captured[-1]["teams"] is None

    # 2) --team-mobs-json inline bare dict + wrapped object with kits
    inline = json.dumps(
        {
            "red": [{"type": "minecraft:wolf", "count": 2}],
            "blue": [{"type": "minecraft:blaze", "count": 1}],
        }
    )
    rc2 = mod.main(
        [
            "--arena",
            "crossroads",
            "--mode",
            "teams",
            "--team-mobs-json",
            inline,
            "--out",
            str(tmp_path),
        ]
    )
    assert rc2 == 0
    assert captured[-1]["team_mobs"]["red"][0]["type"] == "minecraft:wolf"

    wrapped_file = tmp_path / "wrapped.json"
    wrapped_file.write_text(
        json.dumps(
            {
                "team_mobs": {
                    "red": [{"type": "minecraft:skeleton", "count": 2}],
                    "blue": [{"type": "minecraft:zombie", "count": 2}],
                },
                "team_mob_kits": {
                    "red": {"give": [{"item": "minecraft:bow", "count": 1}]}
                },
            }
        ),
        encoding="utf-8",
    )
    rc3 = mod.main(
        [
            "--arena",
            "crossroads",
            "--mode",
            "teams",
            "--team-mobs-json",
            str(wrapped_file),
            "--out",
            str(tmp_path),
        ]
    )
    assert rc3 == 0
    assert captured[-1]["team_mob_kits"] == {
        "red": {"give": [{"item": "minecraft:bow", "count": 1}]}
    }

    # Bad JSON shapes rejected with exit code 2
    for bad_json in ("[]", '{"team_mobs": {}, "extra": 1}'):
        with pytest.raises(SystemExit) as bad_err:
            mod.main(
                [
                    "--arena",
                    "crossroads",
                    "--mode",
                    "teams",
                    "--team-mobs-json",
                    bad_json,
                ]
            )
        assert bad_err.value.code == 2
