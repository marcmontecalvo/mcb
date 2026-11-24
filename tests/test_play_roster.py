"""The arena wizard's player choices: who is online on the arena server, the saved
roster, and the match that saves its names when it starts (mclab.play_runs)."""

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from mclab import arena_roster
from mclab import dashboard as ui
from mclab import play_runs as pr
from mclab.arena import client as arena_client
from mclab.arena.client import ArenaError
from mclab.run_config import resolve_config


def arena(**run):
    return resolve_config("arena", overrides={"run": run})


def slot(name, team=None, op=False):
    return {"name": name, "team": team, "op": op}


def _plan(name, teams, per_team):
    pads = {t: {"pads": [[i, 2, 0] for i in range(n)]} for t, n in teams.items()}
    return {"name": name, "size": [41, 15, 47],
            "capacity": {"teams": len(teams), "per_team": per_team, "mobs": 12},
            "markers": {"teams": pads}}  # fmt: skip


@pytest.fixture
def arenas(tmp_path, monkeypatch):
    root = tmp_path / "arenas"
    (root / "waves").mkdir(parents=True)
    (root / "vault.json").write_text(json.dumps(_plan("vault", {"red": 8}, 4)))
    (root / "crash_site.json").write_text(
        json.dumps(_plan("crash_site", {"red": 6, "blue": 6}, 4))
    )
    (root / "waves/mixed-10.json").write_text(
        json.dumps({"waves": [{"mobs": {"minecraft:zombie": 2}, "delay_ticks": 60}]})
    )
    monkeypatch.setattr(pr, "ARENAS", root)
    monkeypatch.setattr(pr, "WAVES", root / "waves")
    pr._plans.clear()
    return root


class Online:
    """An arena client whose ``/players`` answer is fixed."""

    def __init__(self, *players, error=None):
        self.players, self.error = players, error

    def online(self):
        if self.error:
            raise self.error
        return {p["name"].lower(): p for p in self.players}


# --- who is online ----------------------------------------------------------------


def test_the_people_online_are_offered_and_the_bot_is_marked():
    client = Online(
        {"name": "MCBot", "fake": True},
        {"name": "zed", "fake": False},
        {"name": "Ann", "fake": False},
    )
    answer = pr.online_players(client=client)
    assert answer == {
        "online": [
            {"name": "Ann", "bot": False},
            {"name": "zed", "bot": False},
            {"name": "MCBot", "bot": True},
        ],
        "bot": "MCBot",
        "note": None,
    }


def test_the_bot_is_known_by_its_name_even_when_the_server_does_not_say_fake():
    answer = pr.online_players(client=Online({"name": "mcbot"}, {"name": "Ann"}))
    assert [(p["name"], p["bot"]) for p in answer["online"]] == [
        ("Ann", False),
        ("mcbot", True),
    ]


def test_another_fake_player_is_not_offered_as_a_person():
    answer = pr.online_players(
        client=Online({"name": "Carpet_1", "fake": True}, {"name": "Ann"})
    )
    assert [p["name"] for p in answer["online"]] == ["Ann", "Carpet_1"]
    assert answer["online"][1]["bot"] is True  # fake: not someone to invite


def test_names_that_cannot_join_a_match_are_left_out():
    answer = pr.online_players(
        client=Online({"name": ".BedrockAnn"}, {"name": "Ann"}, {"name": "a b"})
    )
    assert [p["name"] for p in answer["online"]] == ["Ann"]


def test_an_empty_server_is_an_empty_list_with_no_note():
    answer = pr.online_players(client=Online())
    assert answer["online"] == [] and answer["note"] is None


def test_a_stopped_arena_server_is_an_empty_list_and_a_plain_note():
    error = ArenaError("server_not_running", "no server state file")
    answer = pr.online_players(client=Online(error=error))
    assert answer["online"] == []
    assert "arena server is not running" in answer["note"].lower()
    assert answer["bot"] == "MCBot"


@pytest.mark.parametrize(
    "error",
    [
        ArenaError("unreachable", "bridge port 1: refused"),
        ArenaError("http_500", "oops"),
        OSError("boom"),
        ValueError("bad json"),
        KeyError("players"),
        TypeError("x"),
    ],
)
def test_any_other_trouble_is_a_note_not_an_error(error):
    answer = pr.online_players(client=Online(error=error))
    assert answer["online"] == []
    assert answer["note"] and "online" in answer["note"]


def test_a_client_that_cannot_be_made_is_a_note_too(monkeypatch):
    def broken(root=pr.ROOT):
        raise OSError("no config")

    monkeypatch.setattr(pr, "_arena_client", broken)
    answer = pr.online_players()
    assert answer["online"] == [] and "online" in answer["note"]


def test_without_a_client_the_arena_servers_own_bridge_is_asked(tmp_path, monkeypatch):
    instance = tmp_path / "runtime/instances/arena"
    instance.mkdir(parents=True)
    (instance / "supervisor.json").write_text(json.dumps({"token": "secret"}))
    config = tmp_path / "config/instances"
    config.mkdir(parents=True)
    (config / "arena.json").write_text(json.dumps({"bridge_port": 24444}))
    asked = []

    class Transport:
        def __init__(self, port, token, timeout=5.0):
            self.port, self.token, self.timeout = port, token, timeout

        def get(self, path):
            asked.append((self.port, self.token, path, self.timeout))
            return {"players": [{"name": "Ann", "fake": False}]}

    monkeypatch.setattr(arena_client, "HttpTransport", Transport)
    answer = pr.online_players(root=tmp_path)
    assert [p["name"] for p in answer["online"]] == ["Ann"]
    port, token, path, timeout = asked[0]
    assert (port, token, path) == (24444, "secret", "/players")
    assert timeout <= 3  # a dead server must not hang the wizard
    assert all("policy" not in a[2] for a in asked)


def test_with_the_arena_server_down_the_real_client_path_gives_a_note(tmp_path):
    config = tmp_path / "config/instances"
    config.mkdir(parents=True)
    (config / "arena.json").write_text(json.dumps({"bridge_port": 24444}))
    answer = pr.online_players(root=tmp_path)  # no supervisor.json: not running
    assert answer["online"] == []
    assert "not running" in answer["note"]


def test_with_no_arena_config_the_real_client_path_gives_a_note(tmp_path):
    answer = pr.online_players(root=tmp_path)
    assert answer["online"] == [] and answer["note"]


# --- the saved roster, as the dashboard serves it -----------------------------------


def test_the_saved_roster_starts_empty():
    assert pr.roster_state() == {"players": [], "last_match": None, "bot": "MCBot"}


def test_saving_a_player_by_hand_answers_with_the_whole_roster():
    state = pr.save_player({"name": "Ann", "label": "Dad", "pinned": True})
    assert state["players"][0]["name"] == "Ann"
    assert state["players"][0]["label"] == "Dad" and state["players"][0]["pinned"]
    assert pr.roster_state() == state
    state = pr.save_player({"name": "ann", "pinned": False})
    assert [p["name"] for p in state["players"]] == ["Ann"]
    assert state["players"][0]["label"] == "Dad"


def test_forgetting_a_player_answers_with_the_whole_roster():
    pr.save_player({"name": "Ann"})
    pr.save_player({"name": "Bo"})
    state = pr.forget_player({"name": "ANN"})
    assert [p["name"] for p in state["players"]] == ["Bo"]
    assert pr.forget_player({"name": "nobody"}) == state  # nothing to forget


@pytest.mark.parametrize(
    "request_",
    [{}, {"name": ""}, {"name": "a b"}, {"name": 5}, {"name": "Ann", "pinned": 1}],
)
def test_a_bad_save_is_refused(request_):
    with pytest.raises(ValueError):
        pr.save_player(request_)
    assert pr.roster_state()["players"] == []


def test_a_bad_forget_is_refused():
    with pytest.raises(ValueError):
        pr.forget_player({"name": "a b"})
    with pytest.raises(ValueError):
        pr.forget_player({})


def test_the_bot_is_not_a_name_to_save():
    with pytest.raises(ValueError, match="bot"):
        pr.save_player({"name": "mcbot"})
    assert pr.roster_state()["players"] == []


def test_only_known_fields_are_read_from_a_save_request():
    pr.save_player({"name": "Ann", "ip": "10.0.0.2", "uuid": "abc", "uses": 99})
    (entry,) = pr.roster_state()["players"]
    assert set(entry) == {"name", "label", "pinned", "uses", "last_used"}
    assert entry["uses"] == 0


# --- a started match saves its names -------------------------------------------


def test_a_match_saves_who_played_and_the_setup_when_it_starts(arenas, tmp_path):
    seen = {}

    class Child:
        def wait(self, timeout=None):
            # by the time the match runs, the names are already saved
            seen["roster"] = pr.roster_state()
            return 0

    config = arena(
        arena_action="match",
        arena="crash_site",
        game="teams",
        waves="mixed-10",
        roster=[
            slot("MCBot", "red"),
            slot("Dad", "red", op=True),
            slot("Son", "blue"),
        ],
    )
    pr.execute(config, spawn=lambda argv, env: Child(), results=tmp_path / "results")
    state = seen["roster"]
    assert sorted(p["name"] for p in state["players"]) == ["Dad", "Son"]
    last = state["last_match"]
    assert (last["arena"], last["game"], last["waves"]) == (
        "crash_site",
        "teams",
        "mixed-10",
    )
    assert last["roster"] == [
        slot("MCBot", "red"),
        slot("Dad", "red", op=True),
        slot("Son", "blue"),
    ]


def test_a_match_with_no_roster_saves_the_bot_alone_as_the_last_setup(arenas, tmp_path):
    config = arena(arena_action="match", arena="vault", waves="mixed-10")
    pr.execute(
        config,
        spawn=lambda argv, env: type("C", (), {"wait": lambda self, timeout=None: 0})(),
        results=tmp_path / "results",
    )
    state = pr.roster_state()
    assert state["players"] == []
    assert state["last_match"]["roster"] == [slot("MCBot")]


def test_an_arena_tool_saves_nothing(arenas, tmp_path):
    config = arena(arena_action="stop", arena="vault")
    pr.execute(config, run_cli=lambda argv, env: (0, '{"ok": true}'))
    assert pr.roster_state() == {"players": [], "last_match": None, "bot": "MCBot"}


def test_a_combat_trial_saves_nothing(arenas, tmp_path):
    config = resolve_config(
        "combat", overrides={"run": {"weapons": "sword", "mobs": "zombie"}}
    )
    pr.execute(
        config,
        spawn=lambda argv, env: type("C", (), {"wait": lambda self, timeout=None: 0})(),
        results=tmp_path,
    )
    assert pr.roster_state()["players"] == []


def test_a_roster_that_cannot_be_saved_never_stops_the_match(
    arenas, tmp_path, monkeypatch, capsys
):
    def broken(self, *args, **kwargs):
        raise OSError("read-only disk")

    monkeypatch.setattr(arena_roster.RosterStore, "record_match", broken)
    ran = []

    class Child:
        def wait(self, timeout=None):
            ran.append(True)
            return 0

    config = arena(
        arena_action="match", arena="vault", waves="mixed-10", roster=[slot("Ann")]
    )
    result = pr.execute(
        config, spawn=lambda argv, env: Child(), results=tmp_path / "results"
    )
    assert ran and result["status"] == "completed_arena"
    assert "roster" in capsys.readouterr().out.lower()


def test_the_match_command_still_names_the_same_players(arenas, tmp_path):
    """Saving names changes nothing about the arguments the match runs with."""
    config = arena(
        arena_action="match",
        arena="crash_site",
        game="teams",
        waves="mixed-10",
        roster=[slot("Dad", "red"), slot("Son", "blue")],
    )
    argv = pr.command(config["run"], tmp_path)
    teams = [argv[i + 1] for i, a in enumerate(argv) if a == "--team"]
    assert teams == ["red=Dad", "blue=Son"]


# --- the dashboard routes ------------------------------------------------------


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "ROOT", tmp_path)
    monkeypatch.setattr(ui, "SESSIONS", tmp_path / "results/sessions")
    handler = ui.handler(ui.Dashboard())
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as http:
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        yield http, handler.token
        http.shutdown()
        thread.join(timeout=3)


def call(http, token, method, path, body=None):
    connection = HTTPConnection("127.0.0.1", http.server_port)
    headers = {}
    payload = None
    if method == "POST":
        payload = json.dumps(body or {})
        headers = {
            "Origin": f"http://127.0.0.1:{http.server_port}",
            "X-UI-Token": token,
            "Content-Type": "application/json",
        }
    try:
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_get_roster_serves_the_saved_names_and_the_last_match(dashboard):
    http, token = dashboard
    pr.save_player({"name": "Ann", "label": "Dad"})
    status, body = call(http, token, "GET", "/api/roster")
    assert status == 200
    assert body["players"][0]["name"] == "Ann" and body["bot"] == "MCBot"
    assert body["last_match"] is None


def test_get_roster_online_serves_who_is_on_the_arena_server(dashboard, monkeypatch):
    http, token = dashboard
    monkeypatch.setattr(
        pr,
        "_arena_client",
        lambda root=pr.ROOT: Online({"name": "Ann"}, {"name": "MCBot", "fake": True}),
    )
    status, body = call(http, token, "GET", "/api/roster/online")
    assert status == 200
    assert [(p["name"], p["bot"]) for p in body["online"]] == [
        ("Ann", False),
        ("MCBot", True),
    ]
    assert body["note"] is None


def test_get_roster_online_with_the_server_down_is_a_200_with_a_note(
    dashboard, monkeypatch
):
    http, token = dashboard

    def down(root=pr.ROOT):
        raise ArenaError("server_not_running", "no server state file")

    monkeypatch.setattr(pr, "_arena_client", down)
    status, body = call(http, token, "GET", "/api/roster/online")
    assert status == 200 and body["online"] == [] and "not running" in body["note"]


def test_post_roster_saves_a_name_and_post_remove_forgets_it(dashboard):
    http, token = dashboard
    status, body = call(
        http,
        token,
        "POST",
        "/api/roster",
        {"name": "Son", "label": "Son", "pinned": True},
    )
    assert status == 200 and body["players"][0]["pinned"] is True
    status, body = call(http, token, "POST", "/api/roster/remove", {"name": "son"})
    assert status == 200 and body["players"] == []


def test_post_roster_refuses_a_bad_name_with_a_400_and_a_reason(dashboard):
    http, token = dashboard
    status, body = call(http, token, "POST", "/api/roster", {"name": "bad name"})
    assert status == 400 and "Minecraft name" in body["error"]


def test_post_roster_needs_the_dashboards_token(dashboard):
    http, _ = dashboard
    status, _body = call(http, "wrong", "POST", "/api/roster", {"name": "Ann"})
    assert status == 403
    assert pr.roster_state()["players"] == []
