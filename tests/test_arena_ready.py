"""A match waits for its players: each named one online, each human one ready.

Humans join after the dashboard starts a match (the server only just came up), so
the start holds until they are in and have typed ``/mcb ready`` (or ``ready`` in
chat). See docs/ARENA_RUNTIME.md "Waiting for players".
"""

import pytest

from mclab import arena_cli
from mclab.arena import client as arena

BOT = {"name": "MCBot", "fake": True}
ANN = {"name": "Ann", "fake": False}
BO = {"name": "Bo", "fake": False}


def ready_command(name):
    return ("player_command", {"player_name": name, "command": "ready"})


def chat(name, text):
    return ("player_chat", {"player_name": name, "text": text})


class Lobby:
    """The bridge as the wait sees it. Each ``/players`` read is one round of the
    script: who is online then, and the events that arrived since the last round."""

    def __init__(self, rounds, *, latest=10, old=()):
        self.rounds, self.round, self.latest = list(rounds), 0, latest
        self.events = [
            {"sequence": i + 1, "type": kind, "data": data}
            for i, (kind, data) in enumerate(old)
        ]
        self.says = []

    def get(self, path):
        if path == "/players":
            roster, events = self.rounds[min(self.round, len(self.rounds) - 1)]
            if self.round < len(self.rounds):
                for kind, data in events:
                    self.latest += 1
                    self.events.append(
                        {"sequence": self.latest, "type": kind, "data": data}
                    )
            self.round += 1
            return {"players": roster}
        after = int(path.split("after=")[1])
        return {
            "latest_sequence": self.latest,
            "events": [e for e in self.events if e["sequence"] > after],
        }

    def post(self, path, payload):
        assert path == "/say"
        self.says.append(payload)
        return {"status": "queued"}


def wait(lobby, names, *, timeout=100, arena_name="vault"):
    clock = iter(range(10_000))
    lines = []
    admin = arena.ArenaClient(lobby, clock=lambda: next(clock), sleep=lambda _s: None)
    admin.wait_ready(names, timeout=timeout, arena=arena_name, echo=lines.append)
    return lines


def told(lobby, name):
    return [s["text"] for s in lobby.says if s.get("to") == name]


def test_a_human_is_waited_for_greeted_then_waited_for_again_until_ready():
    lobby = Lobby([
        ([BOT], []),
        ([BOT], []),
        ([BOT, ANN], []),
        ([BOT, ANN], []),
        ([BOT, ANN], [ready_command("Ann")]),
    ])  # fmt: skip

    lines = wait(lobby, ["MCBot", "Ann"])

    assert lines[0] == "Waiting for Ann to join the server."
    assert "Waiting for Ann to type /mcb ready." in lines
    assert len(lines) == len(set(lines))  # a line only when the wait changes
    greeting, *rest = told(lobby, "Ann")
    assert "vault" in greeting and "/mcb ready" in greeting
    assert rest == ["Everyone is ready: the match starts."]
    assert lobby.round == 5  # returned on the round Ann was ready


def test_the_bot_and_other_fake_players_need_only_be_online():
    lobby = Lobby([([], []), ([BOT], [])])

    wait(lobby, ["MCBot"])

    assert lobby.round == 2 and lobby.says == []


def test_ready_in_chat_counts_and_other_lines_and_other_players_do_not():
    lobby = Lobby([
        ([ANN, BO], [chat("Ann", "almost"), ready_command("Carl"), chat("Bo", "ready?")]),
        ([ANN, BO], [chat("Ann", "  Ready ")]),
        ([ANN, BO], [ready_command("Bo")]),
    ])  # fmt: skip

    wait(lobby, ["Ann", "Bo"])

    assert lobby.round == 3


def test_names_match_whatever_their_case():
    lobby = Lobby([([ANN], [ready_command("ann")])])

    wait(lobby, ["ANN"])

    assert lobby.round == 1


def test_a_ready_from_before_the_wait_does_not_count():
    lobby = Lobby(
        [([ANN], []), ([ANN], [ready_command("Ann")])], old=[ready_command("Ann")]
    )

    wait(lobby, ["Ann"])

    assert lobby.round == 2


def test_a_player_who_leaves_is_waited_for_again_and_greeted_on_return():
    lobby = Lobby([
        ([ANN, BO], [ready_command("Ann")]),
        ([BO], []),
        ([ANN, BO], [ready_command("Bo")]),
        ([ANN, BO], [ready_command("Ann")]),
    ])  # fmt: skip

    lines = wait(lobby, ["Ann", "Bo"])

    assert lobby.round == 4
    assert "Waiting for Ann to join the server." in lines
    greetings = [t for t in told(lobby, "Ann") if "/mcb ready" in t]
    assert len(greetings) == 2


def test_a_ready_player_hears_who_is_still_awaited():
    lobby = Lobby([
        ([ANN], [ready_command("Ann")]),
        ([ANN, BO], [ready_command("Bo")]),
    ])  # fmt: skip

    wait(lobby, ["Ann", "Bo"])

    assert "Ready. Waiting for Bo." in told(lobby, "Ann")


def test_the_wait_gives_up_naming_who_did_not_come():
    lobby = Lobby([([ANN, BOT], [])])

    with pytest.raises(arena.ArenaError) as error:
        wait(lobby, ["MCBot", "Ann", "Bo"], timeout=5)

    assert error.value.reason == "players_not_ready"
    assert "Bo has not joined" in error.value.detail
    assert "Ann has not typed /mcb ready" in error.value.detail


def test_no_names_means_no_wait():
    lobby = Lobby([])

    assert wait(lobby, []) == []
    assert lobby.round == 0


# ---- mcplay arena start ------------------------------------------------------


class Admin:
    def __init__(self):
        self.calls = []

    def wait_ready(self, names, **kwargs):
        self.calls.append(("wait_ready", names, kwargs))

    def start(self, name, mode, **kwargs):
        self.calls.append(("start", name, kwargs["participants"]))
        return {"name": name}


def test_start_waits_for_the_players_first_when_asked(tmp_path, capsys):
    (tmp_path / "waves").mkdir()
    (tmp_path / "waves/one.json").write_text('[{"mobs": {"minecraft:zombie": 1}}]')
    admin = Admin()

    code = arena_cli.main(
        ["start", "vault", "--waves", "one", "--players", "MCBot", "Ann",
         "--ready-timeout", "60"],
        client=admin, arenas=tmp_path,
    )  # fmt: skip

    assert code == 0
    (kind, names, kwargs), start = admin.calls
    assert (kind, names) == ("wait_ready", ["MCBot", "Ann"])
    assert kwargs["timeout"] == 60 and kwargs["arena"] == "vault"
    assert start == ("start", "vault", ["MCBot", "Ann"])


def test_a_teams_match_waits_for_every_team_member(tmp_path):
    admin = Admin()

    code = arena_cli.main(
        ["start", "vault", "--mode", "teams", "--team", "red=Ann,MCBot",
         "--team", "blue=Bo", "--ready-timeout", "30"],
        client=admin, arenas=tmp_path,
    )  # fmt: skip

    assert code == 0
    assert admin.calls[0][:2] == ("wait_ready", ["Ann", "MCBot", "Bo"])
    assert admin.calls[1][0] == "start"


def test_a_wait_that_gives_up_is_the_commands_answer(tmp_path, capsys):
    (tmp_path / "waves").mkdir()
    (tmp_path / "waves/one.json").write_text('[{"mobs": {"minecraft:zombie": 1}}]')
    lobby = Lobby([([], [])])
    clock = iter(range(10_000))
    admin = arena.ArenaClient(lobby, clock=lambda: next(clock), sleep=lambda _s: None)

    code = arena_cli.main(
        ["start", "vault", "--waves", "one", "--players", "Ann",
         "--ready-timeout", "3"],
        client=admin, arenas=tmp_path,
    )  # fmt: skip

    out, err = capsys.readouterr()
    assert code == 1
    assert '"players_not_ready"' in out.splitlines()[-1]
    assert "Waiting for Ann to join the server." in err


def test_start_does_not_wait_by_default_or_without_named_players(tmp_path):
    (tmp_path / "waves").mkdir()
    (tmp_path / "waves/one.json").write_text('[{"mobs": {"minecraft:zombie": 1}}]')
    for argv in (
        ["start", "vault", "--waves", "one", "--players", "Ann"],
        ["start", "vault", "--waves", "one", "--ready-timeout", "60"],
    ):
        admin = Admin()
        assert arena_cli.main(argv, client=admin, arenas=tmp_path) == 0
        assert [c[0] for c in admin.calls] == ["start"]
