"""Play and arena server runs in the lab dashboard: arena matches, arena tools and
combat trials."""

import json
import sys

import pytest

from mclab import dashboard_runs as runs
from mclab import dashboard_suites as suites
from mclab import play_runs as pr
from mclab.run_config import config_schema, resolve_config
from mclab.run_launch import validate_launch


def _plan(name, teams, per_team, mobs=12):
    pads = {t: {"pads": [[i, 2, 0] for i in range(n)]} for t, n in teams.items()}
    return {"name": name, "size": [41, 15, 47],
            "capacity": {"teams": len(teams), "per_team": per_team, "mobs": mobs},
            "markers": {"teams": pads}}  # fmt: skip


@pytest.fixture
def arenas(tmp_path, monkeypatch):
    """Two arena plans and two wave presets, read the way the real ones are."""
    root = tmp_path / "arenas"
    (root / "waves").mkdir(parents=True)
    (root / "vault.json").write_text(json.dumps(_plan("vault", {"red": 8}, 4)))
    (root / "crash_site.json").write_text(
        json.dumps(_plan("crash_site", {"red": 6, "blue": 6}, 4))
    )
    (root / "waves/mixed-10.json").write_text(
        json.dumps(
            {
                "description": "One mixed wave.",
                "waves": [{"mobs": {"minecraft:zombie": 2}, "delay_ticks": 60}],
            }
        )
    )
    (root / "waves/vault-raid.json").write_text(
        json.dumps([{"mobs": {"minecraft:pillager": 3}, "delay_ticks": 100}])
    )
    monkeypatch.setattr(pr, "ARENAS", root)
    monkeypatch.setattr(pr, "WAVES", root / "waves")
    pr._plans.clear()
    return root


def arena(**run):
    return resolve_config("arena", overrides={"run": run})


def combat(**run):
    return resolve_config("combat", overrides={"run": run})


def slot(name, team=None, op=False):
    return {"name": name, "team": team, "op": op}


BOT = slot("MCBot")


# --- what the dashboard offers comes from the files -----------------------------


def test_arena_and_wave_choices_are_read_from_the_arena_folder(arenas):
    assert pr.arena_names() == ["crash_site", "vault"]
    assert pr.wave_presets() == ["mixed-10", "vault-raid"]
    (arenas / "duel.json").write_text("{}")
    (arenas / "waves/pairs.json").write_text("[]")
    assert "duel" in pr.arena_names() and "pairs" in pr.wave_presets()


def test_the_lobby_is_never_offered_as_an_arena(arenas):
    (arenas / "lobby.json").write_text("{}")
    assert "lobby" not in pr.arena_names()


def test_schema_lists_the_current_arenas_waves_games_and_actions(arenas):
    run = config_schema()["properties"]["run"]["properties"]
    assert run["arena"]["enum"] == ["crash_site", "vault"]
    assert run["waves"]["enum"] == ["mixed-10", "vault-raid"]
    assert run["arena_action"]["enum"] == list(pr.ACTIONS)
    assert run["game"]["enum"] == list(pr.GAMES)
    assert all(run["game"]["enum_help"].values())
    assert run["game"]["enum_labels"] == {
        "waves": "PvE",
        "teams": "PvP",
        "race": "Kill race",
    }
    assert run["game"]["sided"] == ["teams", "race"]
    # waves are only optional in PvP, where the dashboard offers "no mob waves"
    assert run["waves"]["optional"] == {
        "field": "game", "values": ["teams"], "label": "No mob waves",
    }  # fmt: skip
    assert run["arena"]["supported_tests"] == ["arena"]
    assert run["roster"]["supported_tests"] == ["arena"]
    assert run["weapons"]["supported_tests"] == ["combat"]
    assert {"arena", "combat"} <= set(run["count"]["supported_tests"])
    assert {"arena", "combat"} <= set(run["timeout"]["supported_tests"])
    assert run["corner"]["format"] == "position"
    assert run["spot"]["format"] == "position"
    assert run["start"]["format"] == "position"
    assert run["roster"]["format"] == "roster"
    assert run["custom_waves"]["description"]


def test_each_arena_says_its_teams_and_how_many_fit_on_a_team(arenas):
    meta = config_schema()["properties"]["run"]["properties"]["arena"]["enum_meta"]
    assert meta["crash_site"] == {"teams": ["red", "blue"], "per_team": 4, "players": 8}
    assert meta["vault"] == {"teams": ["red"], "per_team": 4, "players": 4}


def test_the_roster_knows_the_bot_and_the_teams_an_arena_can_have(arenas):
    roster = config_schema()["properties"]["run"]["properties"]["roster"]
    assert roster["bot"] == pr.DEFAULT_BOT
    assert roster["max"] == pr.MAX_PLAYERS
    assert roster["teams"] == list(pr.TEAM_NAMES)


def test_catalog_has_an_arena_row_combat_trials_and_hidden_arena_tools(arenas):
    rows = {row["id"]: row for row in pr.catalog_rows()}
    assert set(rows) == {"arena", "arena:stop", "arena:build", "arena:remove", "combat"}
    for row in rows.values():
        assert "run.test" in row["locked_fields"]
        open_fields = {f"run.{f}" for f in pr.FIELDS[row["family"]]} - set(
            row["locked_fields"]
        )
        assert set(row["basic_fields"]) <= open_fields
    match = rows["arena"]
    assert match["server"] == "arena" and match["section"] == pr.SECTION
    assert match["label"] == "Arena"
    assert match["overrides"]["run"]["arena_action"] == "match"
    assert match["overrides"]["run"]["game"] == "waves"
    assert match["overrides"]["run"]["roster"] == [BOT]
    assert match["basic_fields"][:3] == ["run.arena", "run.game", "run.roster"]
    assert "run.corner" in match["locked_fields"]
    assert (
        rows["combat"]["server"] == "play" and rows["combat"]["section"] == pr.SECTION
    )
    assert rows["combat"]["label"] == "Combat trials"
    for tool in ("arena:stop", "arena:build", "arena:remove"):
        assert rows[tool]["section"] == pr.TOOLS_SECTION
        assert rows[tool]["server"] == "arena"
    assert "run.kit" in rows["arena:build"]["locked_fields"]
    assert rows["arena:build"]["basic_fields"] == ["run.arena", "run.corner"]
    assert rows["combat"]["overrides"]["run"]["weapons"] == pr.DEFAULT_WEAPONS


def test_dashboard_catalog_shows_arena_and_trials_and_hides_the_tools(arenas):
    rows = {row["id"]: row for row in suites.test_catalog()}
    match = rows["arena"]
    assert match["family"] == "arena" and match["server"] == "arena"
    assert not match["advanced"] and not rows["combat"]["advanced"]
    assert rows["arena:build"]["advanced"] and rows["arena:stop"]["advanced"]
    validate_launch(match["default_config"])
    validate_launch(rows["combat"]["default_config"])
    assert pr.SECTION in suites.SECTIONS and pr.TOOLS_SECTION in suites.SECTIONS


def test_a_new_arena_plan_appears_in_the_catalog_without_code(arenas):
    (arenas / "duel.json").write_text(
        json.dumps(_plan("duel", {"red": 5, "blue": 5}, 2))
    )
    rows = {row["id"]: row for row in suites.test_catalog()}
    config = resolve_config(
        "arena", base=rows["arena"]["default_config"],
        overrides={"run": {"arena": "duel"}},
    )  # fmt: skip
    assert config["run"]["arena"] == "duel"


def test_suite_entry_runs_a_match_and_refuses_a_locked_field(arenas):
    jobs = suites.resolve_entries(
        [{"test_id": "arena", "overrides": {"run": {"arena": "crash_site"}}}]
    )
    assert jobs[0]["kind"] == "arena"
    assert jobs[0]["run_config"]["run"]["arena"] == "crash_site"
    with pytest.raises(ValueError, match="run.corner"):
        suites.resolve_entries(
            [{"test_id": "arena", "overrides": {"run": {"corner": [0, 64, 0]}}}]
        )


# --- settings are checked before anything runs ------------------------------------


@pytest.mark.parametrize(
    "run",
    [
        {"arena": "nowhere"},
        {"arena": "Vault"},
        {"arena": "lobby"},
        {"arena_action": "explode"},
        {"arena_action": "hunt"},
        {"game": "tag"},
        {"waves": "missing"},
        {"waves": "mixed-10", "custom_waves": "zombie*2"},
        {"custom_waves": "zombie*0"},
        {"custom_waves": "minecraft:ender_dragon"},
        {"kit": "sword+laser"},
        {"roster": [slot("not a name")]},
        {"roster": [BOT, slot("mcbot")]},  # the same player twice
        {"roster": [slot(f"P{i}") for i in range(pr.MAX_PLAYERS + 1)]},
        {"roster": [slot("Ann", "purple")]},
        {"corner": [0, 64]},
        {"corner": [0, 64, 40_000_000]},
        {"weapons": "sword"},
    ],
)
def test_bad_arena_settings_are_refused(arenas, run):
    with pytest.raises(ValueError):
        arena(**run)


@pytest.mark.parametrize(
    "run",
    [
        {"weapons": "sword,laser"},
        {"weapons": ""},
        {"mobs": "zombie*9"},
        {"mobs": "pig+zombie"},
        {"spot": [1, 2]},
        {"arena": "vault"},
    ],
)
def test_bad_combat_settings_are_refused(arenas, run):
    with pytest.raises(ValueError):
        combat(**run)


def test_other_families_cannot_use_play_fields(arenas):
    with pytest.raises(ValueError, match="not supported"):
        resolve_config("gather", overrides={"run": {"arena": "vault"}})


def _teams(**sizes):
    return [
        slot(f"{team[0].upper()}{i}", team)
        for team, n in sizes.items()
        for i in range(n)
    ]


@pytest.mark.parametrize(
    "run, message",
    [
        ({"arena": "vault"}, "action"),
        ({"arena_action": "match"}, "arena"),
        ({"arena_action": "match", "arena": "vault"}, "waves"),
        (
            {
                "arena_action": "match",
                "arena": "vault",
                "game": "race",
                "roster": _teams(red=1, blue=1),
            },
            "waves",
        ),  # fmt: skip
        (
            {
                "arena_action": "match",
                "arena": "vault",
                "waves": "mixed-10",
                "roster": [],
            },
            "player",
        ),  # fmt: skip
        (
            {
                "arena_action": "match",
                "arena": "crash_site",
                "game": "teams",
                "roster": _teams(red=2),
            },
            "two teams",
        ),  # fmt: skip
        (
            {
                "arena_action": "match",
                "arena": "crash_site",
                "game": "teams",
                "roster": [*_teams(red=1, blue=1), slot("Cy")],
            },
            "team",
        ),  # fmt: skip
        (
            {
                "arena_action": "match",
                "arena": "crash_site",
                "game": "teams",
                "roster": _teams(red=1, green=1),
            },
            "green",
        ),  # fmt: skip
        (
            {
                "arena_action": "match",
                "arena": "crash_site",
                "game": "teams",
                "roster": _teams(red=5, blue=1),
            },
            "4",
        ),  # fmt: skip
        (
            {
                "arena_action": "match",
                "arena": "vault",
                "waves": "mixed-10",
                "roster": [slot(f"P{i}") for i in range(5)],
            },
            "4",
        ),  # fmt: skip
        ({"arena_action": "build", "arena": "vault"}, "corner"),
    ],
)
def test_incomplete_arena_runs_are_refused_at_launch(arenas, run, message):
    with pytest.raises(ValueError, match=message):
        validate_launch(arena(**run))


def test_incomplete_combat_run_is_refused_at_launch(arenas):
    with pytest.raises(ValueError, match="weapons"):
        validate_launch(combat(mobs="zombie"))


def test_complete_runs_pass(arenas):
    validate_launch(arena(arena_action="match", arena="vault", custom_waves="zombie*2"))
    validate_launch(
        arena(arena_action="match", arena="crash_site", game="teams",
              roster=[slot("MCBot", "red"), slot("Ann", "blue", op=True)])
    )  # fmt: skip
    validate_launch(
        arena(arena_action="match", arena="crash_site", game="race", waves="mixed-10",
              roster=_teams(red=4, blue=4))
    )  # fmt: skip
    validate_launch(arena(arena_action="build", arena="vault", corner=[1, 70, -3]))
    validate_launch(arena(arena_action="stop", arena="vault"))
    validate_launch(
        combat(weapons="sword,spear+sword", mobs="zombie,zombie*2+skeleton")
    )


# --- the commands the worker runs -----------------------------------------------


def test_a_waves_match_command_matches_the_script(arenas, tmp_path):
    run = arena(
        arena_action="match",
        arena="vault",
        custom_waves="pillager*2, pillager+vindicator",
        roster=[BOT, slot("Alex", op=True)],
        kit="keep",
        count=3,
        timeout=300,
    )["run"]
    argv = pr.command(run, tmp_path / "out")
    assert argv[0].endswith("arena-hunt.py")
    assert argv[1:] == [
        "--arena", "vault", "--mode", "waves", "--world",
        "--wave", "pillager*2", "--wave", "pillager+vindicator",
        "--players", "MCBot", "Alex", "--op", "Alex", "--kit", "keep",
        "--repeat", "3", "--timeout", "300", "--out", str(tmp_path / "out"),
    ]  # fmt: skip
    preset = arena(arena_action="match", arena="vault", waves="mixed-10")["run"]
    argv = pr.command(preset, tmp_path)
    assert argv[argv.index("--waves") + 1] == "mixed-10"
    assert "--timeout" not in argv and "--op" not in argv


def test_a_team_match_command_names_each_team(arenas, tmp_path):
    run = arena(
        arena_action="match", arena="crash_site", game="teams",
        roster=[slot("MCBot", "red"), slot("Ann", "blue", op=True), slot("Bo", "red")],
    )["run"]  # fmt: skip
    argv = pr.command(run, tmp_path)
    assert argv[argv.index("--mode") + 1] == "teams"
    teams = [argv[i + 1] for i, a in enumerate(argv) if a == "--team"]
    assert teams == ["red=MCBot,Bo", "blue=Ann"]
    assert "--players" not in argv and "--waves" not in argv
    assert argv[argv.index("--op") + 1] == "Ann"


def test_combat_command_matches_the_script(arenas, tmp_path):
    run = combat(weapons="sword,bow", mobs="zombie", count=2, spot=[-281, 77, -37])
    argv = pr.command(run["run"], tmp_path)
    assert argv[0].endswith("combat-trial.py")
    assert argv[1:] == [
        "--weapons", "sword,bow", "--mobs", "zombie", "--repeat", "2",
        "--out", str(tmp_path), "--arena=-281,77,-37",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "run, expected",
    [
        (
            {"arena_action": "build", "corner": [-440, 77, -60]},
            ["build", "vault", "--at", "-440", "77", "-60", "--wait"],
        ),
        ({"arena_action": "stop"}, ["stop", "vault"]),
        ({"arena_action": "remove"}, ["remove", "vault"]),
        ({"arena_action": "remove", "restore": True}, ["remove", "vault", "--restore"]),
    ],
)
def test_arena_tool_arguments(arenas, run, expected):
    assert pr.arena_args(arena(arena="vault", **run)["run"]) == expected


# --- results land where the dashboard history reads them -------------------------


def hunt_record():
    return {
        "arena": "vault",
        "runs": [
            {"outcome": "won", "seconds": 55.1, "waves_cleared": 2, "waves": 2,
             "min_health": 11.0},
            {"outcome": "lost", "seconds": 40.0, "waves_cleared": 1, "waves": 2,
             "min_health": 0.0, "killed_by": "minecraft:vindicator"},
        ],
        "summary": {"runs": 2, "won": 1, "win_rate": 0.5},
    }  # fmt: skip


def test_a_match_summary_is_a_history_row(arenas):
    run = arena(arena_action="match", arena="vault", waves="mixed-10")["run"]
    summary = pr.summarize(run, hunt_record(), run_id="R1")
    assert summary["run_id"] == "R1" and summary["suite"] == "arena"
    assert summary["status"] == "failed"
    assert summary["course_id"] == "vault · PvE · mixed-10"
    assert [e["status"] for e in summary["episodes"]] == ["success", "lost"]
    assert summary["episodes"][0]["ticks"] == round(55.1 * 20)
    assert summary["series"]["win_rate"] == 0.5
    row = runs.result_row(pr.ROOT / "results/arena/R1/summary.json", summary)
    assert row["statuses"] == {"success": 1, "lost": 1}
    assert row["median_ticks"] == round(55.1 * 20)


def test_a_team_match_summary_names_the_winners_and_the_kills(arenas):
    run = arena(arena_action="match", arena="crash_site", game="teams",
                roster=_teams(red=1, blue=1))["run"]  # fmt: skip
    record = {"runs": [
        {"outcome": "won", "winner": "red", "bot_won": True, "seconds": 30.0,
         "players": {"MCBot": {"team": "red", "mob_kills": 0, "player_kills": 1,
                               "deaths": 0, "damage_dealt": 24.5,
                               "damage_taken": 6.0, "healing": 8.0}},
         "team_kills": {"red": 0, "blue": 0}},
        {"outcome": "lost", "reason": "draw", "seconds": 600.0},
    ], "summary": {"winners": {"red": 1}}}  # fmt: skip
    summary = pr.summarize(run, record, run_id="R2")
    assert summary["course_id"] == "crash_site · PvP"
    first, second = summary["episodes"]
    assert first["status"] == "success" and first["winner"] == "red"
    assert first["bot_won"] is True
    assert first["players"]["MCBot"]["player_kills"] == 1
    assert first["players"]["MCBot"]["damage_dealt"] == 24.5
    assert second["status"] == "lost" and second["reason"] == "draw"


def test_combat_summary_is_a_history_row(arenas):
    run = combat(weapons="sword", mobs="zombie")["run"]
    record = {
        "trials": [
            {"weapon": "sword", "mob": "zombie", "killed": True, "seconds": 4.0},
            {"weapon": "sword", "mob": "zombie", "killed": False, "end": "timeout"},
        ],
        "summary": {"sword/zombie": {"kill_rate": 0.5}},
        "regressions": [],
    }
    summary = pr.summarize(run, record, run_id="T1")
    assert summary["suite"] == "combat" and summary["status"] == "failed"
    assert summary["course_id"] == "sword vs zombie"
    assert [e["status"] for e in summary["episodes"]] == ["success", "timeout"]
    assert summary["cells"] == record["summary"]
    won = pr.summarize(run, {"trials": record["trials"][:1]}, run_id="T2")
    assert won["status"] == "passed"


def test_missing_record_is_a_failed_summary(arenas):
    run = combat(weapons="sword", mobs="zombie")["run"]
    summary = pr.summarize(run, None, run_id="T3")
    assert summary["status"] == "failed" and summary["episodes"] == []


class FakeChild:
    def __init__(self, argv, code=0, write=None):
        self.argv, self.code, self.write = argv, code, write

    def wait(self, timeout=None):
        if self.write:
            self.write(self.argv)
        return self.code


def _listing(protocol=None):
    """An ``mcarena arena list`` answer from a mod speaking ``protocol`` (None: the
    lab's own; 0: a mod from before the handshake)."""
    from mclab.arena import client as arena_client

    answer = {"ok": True, "arenas": []}
    if protocol != 0:
        answer["protocol"] = arena_client.PROTOCOL if protocol is None else protocol

    def run_cli(argv, env):
        assert env["MCLAB_INSTANCE"] == "arena" and argv[-1] == "list"
        return 0, json.dumps(answer) + "\n"

    return run_cli


def test_a_match_on_an_older_mod_fails_at_start_with_the_fix(arenas, tmp_path):
    spawned = []
    config = arena(arena_action="match", arena="vault", waves="mixed-10")

    result = pr.execute(
        config,
        spawn=lambda argv, env: spawned.append(argv),
        run_cli=_listing(protocol=0),
        results=tmp_path / "results",
    )

    assert spawned == [] and result["status"] == "failed"
    assert result["error"].startswith("mod_outdated")
    assert "mcarena mod-install" in result["error"]
    assert not (tmp_path / "results").exists()  # no run, so no history row
    # ...but the setup is kept, so "Same as last time" brings it back after the fix
    assert pr.roster_state()["last_match"]["arena"] == "vault"


def test_a_match_fails_at_start_when_the_arena_server_does_not_answer(arenas, tmp_path):
    config = arena(arena_action="match", arena="vault", waves="mixed-10")

    result = pr.execute(
        config,
        spawn=lambda argv, env: pytest.fail("spawned"),
        run_cli=lambda argv, env: (1, "Traceback ..."),
        results=tmp_path,
    )

    assert result["status"] == "failed" and "arena list" in result["error"]


def test_execute_runs_a_match_on_the_arena_instance(arenas, tmp_path):
    seen = {}

    def write(argv):
        from pathlib import Path

        out = Path(argv[argv.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "20261124T000000Z.json").write_text(json.dumps(hunt_record()))

    def spawn(argv, env):
        seen.update(argv=argv, env=env)
        return FakeChild(argv[argv.index("-c") + 2 :], write=write)

    config = arena(arena_action="match", arena="vault", waves="mixed-10")
    result = pr.execute(
        config, spawn=spawn, run_cli=_listing(), results=tmp_path / "results"
    )
    assert seen["env"]["MCLAB_INSTANCE"] == "arena"
    assert seen["argv"][0] == sys.executable
    assert result["status"] == "completed_arena"
    folder = tmp_path / "results/arena" / result["run_id"]
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["run_id"] == result["run_id"]
    assert summary["source"].endswith("20261124T000000Z.json")


def test_combat_trials_run_on_the_play_instance(arenas, tmp_path):
    seen = {}

    def spawn(argv, env):
        seen.update(env=env)
        return FakeChild(argv, code=2)

    result = pr.execute(
        combat(weapons="sword", mobs="zombie"), spawn=spawn, results=tmp_path
    )
    assert seen["env"]["MCLAB_INSTANCE"] == "play"
    assert result["status"] == "failed" and result["returncode"] == 2
    assert "exit code 2" in result["error"]
    summary = tmp_path / "combat" / result["run_id"] / "summary.json"
    assert json.loads(summary.read_text())["status"] == "failed"


def test_an_interrupted_match_is_stopped_and_cleaned_up(arenas, tmp_path, monkeypatch):
    calls = []

    class Interrupted(FakeChild):
        def wait(self, timeout=None):
            raise KeyboardInterrupt

    monkeypatch.setattr(pr, "_stop_child", lambda child: calls.append("stopped"))
    monkeypatch.setattr(
        pr, "arena_action", lambda run, **k: calls.append(pr.arena_args(run))
    )
    config = arena(arena_action="match", arena="vault", waves="mixed-10")
    with pytest.raises(KeyboardInterrupt):
        pr.execute(
            config,
            spawn=lambda argv, env: Interrupted(argv),
            run_cli=_listing(),
            results=tmp_path,
        )
    assert calls == ["stopped", ["stop", "vault"]]


def test_execute_arena_tool_reads_the_cli_answer_from_the_arena_server(
    arenas, tmp_path
):
    def run_cli(argv, env):
        assert env["MCLAB_INSTANCE"] == "arena"
        assert argv[-2:] == ["stop", "vault"]
        return 0, '{"ok": true, "name": "vault"}\n'

    result = pr.execute(
        arena(arena_action="stop", arena="vault"), run_cli=run_cli, results=tmp_path
    )
    answer = {"ok": True, "name": "vault"}
    assert result == {"status": "completed_arena", "details": answer}

    def refused(argv, env):
        return 1, '{"ok": false, "reason": "no_arena", "detail": "vault"}'

    result = pr.execute(
        arena(arena_action="stop", arena="vault"), run_cli=refused, results=tmp_path
    )
    assert result["status"] == "failed" and result["error"] == "no_arena: vault"


# --- the play and arena servers ----------------------------------------------------


class Api:
    def __init__(self, replies):
        self.replies = list(replies)

    def call(self, intent, args, source="api"):
        assert intent == "status"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"ok": True, "result": reply}


def test_bot_ready_needs_a_ready_session_and_body(arenas):
    from mclab.companion_api import ApiUnavailable

    ready = {"session": "ready", "body_state": "ready"}
    assert pr.bot_ready(lambda: Api([ready]))
    assert not pr.bot_ready(lambda: Api([{**ready, "body_state": "spawning"}]))

    def down():
        raise ApiUnavailable("down")

    assert not pr.bot_ready(down)


def test_each_server_reads_its_own_instance(arenas, monkeypatch):
    seen = []
    ready = {"session": "ready", "body_state": "ready"}

    def client(instance, **kwargs):
        seen.append(instance)
        return Api([ready])

    monkeypatch.setattr(pr, "_client", client)
    assert pr.bot_ready(instance="arena")
    assert seen == ["arena"]
    assert pr.server_for("arena") == "arena" and pr.server_for("combat") == "play"
    assert pr.server_for("gather") is None
    assert pr._env("arena")["MCLAB_INSTANCE"] == "arena"


def test_ensure_server_starts_a_stopped_server_and_waits(arenas):
    states = iter([False, False, False, True])
    started = []
    clock = iter(range(0, 1000, 5))
    result = pr.ensure_server(
        "arena",
        ready=lambda: next(states),
        state=lambda: "stopped",
        start=lambda: started.append(1),
        clock=lambda: next(clock),
        sleep=lambda s: None,
    )
    assert started == [1] and result == {"state": "ready", "started": True}
    assert pr.ensure_server(
        "play", ready=lambda: True, state=lambda: "x", start=None, clock=None,
        sleep=None,
    ) == {"state": "ready", "started": False}  # fmt: skip


def test_ensure_server_gives_up_after_the_wait(arenas):
    clock = iter(range(0, 10_000, 100))
    with pytest.raises(RuntimeError, match="arena server's bot is not ready"):
        pr.ensure_server(
            "arena",
            ready=lambda: False,
            state=lambda: "running",
            start=lambda: pytest.fail("a running server is not started again"),
            clock=lambda: next(clock),
            sleep=lambda s: None,
            wait=300,
        )


def test_server_state_reports_each_host(arenas, monkeypatch):
    from mclab.companion_api import ApiUnavailable

    monkeypatch.setattr(pr, "_cached", {})

    class Healthy:
        def health(self):
            return True

    def down():
        raise ApiUnavailable("down")

    assert pr.server_state("play", client=lambda: Healthy(), ttl=0) == "running"
    assert pr.server_state("arena", client=down, ttl=0) == "stopped"
    states = pr.server_states(client=lambda instance: Healthy, ttl=0)
    assert set(states) == set(pr.SERVERS)


def test_the_dashboard_starts_the_server_each_run_needs(arenas, monkeypatch):
    from mclab import dashboard

    match = runs.plan(
        {
            "kind": "arena",
            "run_config": arena(arena_action="match", arena="vault", waves="mixed-10"),
        }
    )[0]
    trial = runs.plan(
        {"kind": "combat", "run_config": combat(weapons="sword", mobs="zombie")}
    )[0]
    assert not dashboard.needs_lab_server(match)
    assert dashboard.play_servers([match, trial, match]) == ["arena", "play"]
    assert runs.folder(match) == "arena"
    [setup] = runs.plan({"kind": "setup", "operation": "arena-start"})
    assert setup["operation"] == "arena-start"
    called = []
    monkeypatch.setattr(
        pr, "ensure_server", lambda name: called.append(name) or {"state": "ready"}
    )
    assert runs.setup("arena-start") == {
        "status": "completed_setup",
        "details": {"state": "ready"},
    }
    runs.setup("play-start")
    assert called == ["arena", "play"]


# --- the dashboard's friendlier fields: checkboxes, materials, help, where to watch ---


def _run_schema():
    return config_schema()["properties"]["run"]["properties"]


def _values(field):
    return [o["value"] for o in field["choices"]["options"]]


def test_materials_are_offered_for_matches_and_trials_from_the_game_lists(arenas):
    from mclab import combat_trial as ct

    run = _run_schema()
    assert run["weapon_material"]["enum"] == list(ct.WEAPON_TIERS)
    assert run["armor_material"]["enum"] == list(ct.ARMOR_TIERS)
    for name in ("weapon_material", "armor_material"):
        assert sorted(run[name]["supported_tests"]) == ["arena", "combat"]
        assert run[name]["placeholder"] == ct.DEFAULT_TIER
    rows = {row["id"]: row for row in pr.catalog_rows()}
    assert "run.weapon_material" not in rows["arena"]["locked_fields"]
    assert "run.weapon_material" not in rows["combat"]["locked_fields"]
    assert "run.weapon_material" in rows["arena:stop"]["locked_fields"]


def test_the_kit_is_checkboxes_from_the_kit_catalog(arenas):
    from mclab import combat_trial as ct

    kit = _run_schema()["kit"]
    assert kit["format"] == "choices"
    assert _values(kit) == [*ct.KIT_PARTS, "keep"]
    for option in kit["choices"]["options"][:-1]:
        label, group, text = ct.KIT_PARTS[option["value"]]
        assert (option["label"], option["group"], option["help"]) == (
            label,
            group,
            text,
        )
    assert kit["choices"]["separator"] == "+"
    assert kit["choices"]["empty"] == "fist"
    assert kit["choices"]["exclusive"] == ["keep"]


def test_weapons_and_mobs_are_checkboxes_with_room_for_more(arenas):
    from mclab import combat_trial as ct

    run = _run_schema()
    weapons = [p for p, (_, group, _) in ct.KIT_PARTS.items() if group == "Weapons"]
    assert _values(run["weapons"]) == [*weapons, "fist"]
    assert _values(run["mobs"]) == list(ct.MOBS)
    for name in ("weapons", "mobs"):
        assert run[name]["format"] == "choices"
        assert run[name]["choices"]["separator"] == ","
        assert run[name]["choices"]["other"]  # a typed extra: spear+sword, zombie*3


def test_waves_offer_the_presets_with_their_text_or_a_wave_builder(arenas):
    from mclab import combat_trial as ct

    run = _run_schema()
    assert run["waves"]["custom"]["field"] == "custom_waves"
    assert run["waves"]["enum_help"]["mixed-10"] == "One mixed wave."
    assert "vault-raid" not in run["waves"]["enum_help"]  # a bare list: no text
    assert run["custom_waves"]["format"] == "waves"
    assert run["custom_waves"]["mobs"] == [
        m for m, group in ct.MOBS.items() if group != ct.ANIMALS
    ]


def test_each_arena_is_described_from_its_design_and_plan(arenas):
    from mclab.arena import designs

    help_ = _run_schema()["arena"]["enum_help"]
    first = designs.DESIGNS["vault"].__doc__.strip().splitlines()[0]
    assert help_["vault"].startswith(first)
    assert "41 × 47" in help_["vault"] and "12 mobs" in help_["vault"]
    (arenas / "bare.json").write_text("{}")
    assert _run_schema()["arena"]["enum_help"]["bare"]  # still has its text


def test_count_and_timeout_say_what_they_mean_for_each_kind_of_run(arenas):
    run = _run_schema()
    assert run["count"]["by_test"]["arena"]["label"] == "Matches"
    assert run["count"]["by_test"]["combat"]["label"] == "Rounds"
    assert run["timeout"]["by_test"]["arena"]["placeholder"] == "600"
    assert run["timeout"]["by_test"]["combat"]["placeholder"] == "90"
    for by in (run["count"]["by_test"], run["timeout"]["by_test"]):
        assert all(text["help"] for text in by.values())


@pytest.mark.parametrize(
    "family, run",
    [
        ("arena", {"weapon_material": "leather"}),
        ("arena", {"armor_material": "stone"}),
        ("combat", {"weapon_material": "obsidian"}),
        ("combat", {"armor_material": "wooden", "weapons": "armor"}),
    ],
)
def test_a_material_the_game_does_not_have_is_refused(arenas, family, run):
    with pytest.raises(ValueError, match="material"):
        resolve_config(family, overrides={"run": run})


def test_commands_carry_the_materials_only_when_chosen(arenas, tmp_path):
    match = arena(
        arena_action="match",
        arena="vault",
        waves="mixed-10",
        weapon_material="diamond",
        armor_material="netherite",
    )
    argv = pr.command(match["run"], tmp_path)
    assert argv[argv.index("--tier") + 1] == "diamond"
    assert argv[argv.index("--armor") + 1] == "netherite"
    trial = combat(weapons="sword", mobs="zombie", weapon_material="wooden")
    argv = pr.command(trial["run"], tmp_path)
    assert argv[argv.index("--tier") + 1] == "wooden" and "--armor" not in argv
    plain = pr.command(
        arena(arena_action="match", arena="vault", waves="mixed-10")["run"], tmp_path
    )
    assert "--tier" not in plain and "--armor" not in plain


LISTING = {
    "ok": True,
    "arenas": [
        {"name": "vault", "state": "built", "origin": [-345, 77, -60],
         "spectator": [-325, 82, -56], "match_state": "lost"},
        {"name": "duel", "state": "planned", "origin": None, "spectator": None},
        {"name": "lobby", "state": "built", "origin": [-12, 100, -12],
         "spectator": [0, 105, -9]},
    ],
}  # fmt: skip


def _configs(tmp_path):
    (tmp_path / "config/instances").mkdir(parents=True)
    (tmp_path / "config/lab.json").write_text(json.dumps({"game_port": 25575}))
    (tmp_path / "config/instances/play.json").write_text(
        json.dumps({"game_port": 25565, "server_host": "127.0.0.1"})
    )
    (tmp_path / "config/instances/arena.json").write_text(
        json.dumps({"game_port": 25566, "server_host": "127.0.0.1"})
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_where():
    pr._where_cache.clear()
    yield
    pr._where_cache.clear()


def test_where_names_each_server_and_each_arenas_watch_spot(tmp_path):
    out = pr.where(listing=lambda: LISTING, root=_configs(tmp_path))
    assert out["servers"]["lab"]["address"] == "127.0.0.1:25575"
    assert out["servers"]["play"]["address"] == "127.0.0.1:25565"
    assert out["servers"]["arena"]["address"] == "127.0.0.1:25566"
    assert all(s["label"] for s in out["servers"].values())
    assert out["arena_server"] == "arena"
    assert out["arenas"]["vault"] == {
        "built": True, "corner": [-345, 77, -60], "spectator": [-325, 82, -56],
        "match": "lost", "teleport": "/tp @s -325 82 -56",
    }  # fmt: skip
    assert out["arenas"]["duel"]["built"] is False
    assert out["arenas"]["duel"]["teleport"] is None
    assert "lobby" not in out["arenas"] and out["lobby"] is True
    assert "arenas_error" not in out


def test_a_lan_server_also_gives_the_address_other_pcs_join_at(tmp_path):
    root = _configs(tmp_path)
    play = root / "config/instances/play.json"
    play.write_text(json.dumps({**json.loads(play.read_text()), "lan": True}))

    out = pr.servers(root, lan_ip=lambda: "192.168.1.20")

    assert out["play"]["address"] == "127.0.0.1:25565"  # this PC
    assert out["play"]["lan_address"] == "192.168.1.20:25565"
    assert out["lab"]["lan_address"] is None  # still loopback only
    assert pr.servers(root, lan_ip=lambda: None)["play"]["lan_address"] is None


def test_where_names_the_bot_so_the_page_can_tell_who_must_say_ready(tmp_path):
    out = pr.where(listing=lambda: LISTING, root=_configs(tmp_path))
    assert out["bot"] == pr.DEFAULT_BOT


def test_where_still_names_the_servers_when_the_arena_server_is_down(tmp_path):
    def down():
        raise RuntimeError("bridge port 8785: refused")

    out = pr.where(listing=down, root=_configs(tmp_path))
    assert out["servers"]["arena"]["address"] == "127.0.0.1:25566"
    assert out["arenas"] == {} and out["lobby"] is False
    assert "refused" in out["arenas_error"]
    pr._where_cache.clear()
    refused = pr.where(
        listing=lambda: {"ok": False, "reason": "unreachable", "detail": "x"},
        root=_configs(tmp_path / "b"),
    )
    assert refused["arenas"] == {} and "unreachable" in refused["arenas_error"]


def test_where_asks_the_arena_server_at_most_every_few_seconds(tmp_path):
    calls = []
    now = [0.0]

    def listing():
        calls.append(now[0])
        return LISTING

    root = _configs(tmp_path)
    for at in (0.0, 5.0, 11.0):
        now[0] = at
        pr.where(listing=listing, root=root, clock=lambda: now[0], ttl=10)
    assert calls == [0.0, 11.0]


def test_arena_listing_runs_the_arena_cli_on_the_arena_server():
    seen = {}

    def cli(argv, env):
        seen.update(argv=argv, env=env)
        return 0, 'log line\n{"ok": true, "arenas": []}\n'

    assert pr.arena_listing(run_cli=cli) == {"ok": True, "arenas": []}
    assert seen["argv"][-1] == "list" and seen["env"]["MCLAB_INSTANCE"] == "arena"
    with pytest.raises(RuntimeError, match="exit code 2"):
        pr.arena_listing(run_cli=lambda argv, env: (2, "Traceback ..."))
