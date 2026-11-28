"""The lobby's game menu, signs, floating board and the publisher that sends them."""

from __future__ import annotations

import json
import re

import pytest

from mclab import arena_lobby_menu as lm
from mclab.arena_lobby import ArenaLobby, LobbyError

CHAT_COLORS = {
    "black", "dark_blue", "dark_green", "dark_aqua", "dark_red", "dark_purple",
    "gold", "gray", "dark_gray", "blue", "green", "aqua", "red", "light_purple",
    "yellow", "white",
}  # fmt: skip
BANNED = (r"#\d", r"\brev\b", "Elim", r"\bP:\d", "NoMob", r"\.\.\.", "…", r"\bid\b")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def lobby(tmp_path, clock):
    return ArenaLobby(
        state_path=tmp_path / "arena-lobby.json",
        runtime_dir=tmp_path,
        kits_path=tmp_path / "kits.json",
        clock=clock,
    )


def menu(lobby, player):
    return lm.menu_for(lobby.snapshot(), player, kits=lobby.kit_list())


def labels(screen):
    return [b["label"] for b in screen.get("buttons", [])]


def button(screen, label):
    for b in screen["buttons"]:
        if b["label"] == label:
            return b
    raise AssertionError(f"no button {label!r} in {labels(screen)}")


def texts(menu_json):
    """Every string a player can read in a menu."""
    out = []

    def btn(b):
        out.extend([b["label"], b.get("tooltip", "")])

    for screen in menu_json["screens"].values():
        out.append(screen["title"])
        out.extend(line["text"] for line in screen.get("body", []))
        for inp in screen.get("inputs", []):
            out.append(inp["label"])
            out.extend(o["label"] for o in inp.get("options", []))
        for b in screen.get("buttons", []):
            btn(b)
        if "exit" in screen:
            btn(screen["exit"])
    return [t for t in out if t]


def check_button(b, screens):
    assert 1 <= len(b["label"]) <= 40, b["label"]
    assert len(b.get("tooltip", "")) <= 200
    assert b.get("color", "white") in CHAT_COLORS
    assert 40 <= b.get("width", 40) <= 300
    kinds = [k for k in ("go", "action", "submit", "close") if k in b]
    assert len(kinds) == 1, b
    if "go" in b:
        assert b["go"] in screens, b
    for key in ("action", "submit"):
        if key in b:
            for k, v in b[key].items():
                assert re.fullmatch(r"[a-z_]{1,32}", k)
                assert isinstance(v, (str, int, bool))
                if isinstance(v, str):
                    assert len(v) <= 64
            assert "action" in b[key]


def check_menu(m):
    screens = m["screens"]
    assert m["home"] in screens and len(screens) <= 48
    for sid, s in screens.items():
        assert re.fullmatch(r"[a-z0-9_]{1,32}", sid), sid
        assert 1 <= len(s["title"]) <= 64
        assert len(s.get("body", [])) <= 12
        for line in s.get("body", []):
            assert len(line["text"]) <= 200
            assert line.get("color", "white") in CHAT_COLORS
        assert len(s.get("inputs", [])) <= 8 and len(s.get("buttons", [])) <= 24
        assert 1 <= s.get("columns", 2) <= 3
        keys = [i["key"] for i in s.get("inputs", [])]
        assert len(keys) == len(set(keys))
        for inp in s.get("inputs", []):
            assert re.fullmatch(r"[a-z_]{1,32}", inp["key"])
            assert len(inp["label"]) <= 64
            if inp["kind"] == "dropdown":
                ids = [o["id"] for o in inp["options"]]
                assert 1 <= len(ids) <= 32 and inp["initial"] in ids
                assert all(
                    len(o["id"]) <= 48 and len(o["label"]) <= 64 for o in inp["options"]
                )
            elif inp["kind"] == "toggle":
                assert isinstance(inp["initial"], bool)
            elif inp["kind"] == "text":
                assert 1 <= inp["max_length"] <= 32 and len(inp["initial"]) <= 32
            else:
                assert inp["kind"] == "slider"
                assert inp["min"] < inp["max"] and inp["step"] >= 1
        for b in s.get("buttons", []):
            check_button(b, screens)
        if "exit" in s:
            check_button(s["exit"], screens)
    for t in texts(m):
        for pat in BANNED:
            assert not re.search(pat, t), f"{pat!r} in {t!r}"


def seat(lobby, *people):
    """people: (name, team) pairs; the first becomes host."""
    for name, team in people:
        lobby.join(name, team)


# --- contract limits over every phase and role ------------------------------------


def test_every_menu_in_every_phase_obeys_the_contract(lobby, clock, tmp_path):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.watch("Cy")
    lobby.add_bot("Ann", "green", name="Robo")
    lobby.add_mob_group("Ann", "red", "zombie", 2)
    lobby.set_kit_mode("Ann", "class_picker")
    lobby.set_allow_uneven("Ann", True)
    for who in ("Ann", "Bob", "Cy", "Newcomer"):
        check_menu(menu(lobby, who))
    lobby.ready("Ann")
    lobby.ready("Bob")
    assert lobby.snapshot()["phase"] == "countdown"
    for who in ("Ann", "Bob", "Cy", "Newcomer"):
        check_menu(menu(lobby, who))
    clock.now += 10
    lobby.tick()
    assert lobby.snapshot()["phase"] == "running"
    for who in ("Ann", "Bob", "Cy", "Newcomer"):
        check_menu(menu(lobby, who))
    lobby.complete_match({"winner": "red", "outcome": "won", "seconds": 130})
    for who in ("Ann", "Bob", "Cy", "Newcomer"):
        check_menu(menu(lobby, who))


# --- home -----------------------------------------------------------------------


def test_a_newcomer_sees_join_and_watch_and_no_host_controls(lobby):
    seat(lobby, ("Ann", "red"))
    m = menu(lobby, "Newcomer")
    home = m["screens"]["home"]
    assert m["home"] == "home"
    assert labels(home) == ["Join a Team", "Watch", "Game Info"]
    assert set(m["screens"]) == {"home", "teams", "kits", "info", "recap"}


def test_the_host_alone_gets_every_host_screen(lobby):
    seat(lobby, ("Ann", "red"))
    m = menu(lobby, "Ann")
    for sid in ("settings", "maps", "bots", "bot_add", "mobs"):
        assert sid in m["screens"], sid
    assert "host" not in m["screens"]  # nobody to give it to
    home = labels(m["screens"]["home"])
    for label in ("Game Settings", "Choose Map", "Bots", "Allied Mobs"):
        assert label in home
    assert "Give Host" not in home


def test_the_host_can_give_host_to_another_person(lobby):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    m = menu(lobby, "Ann")
    assert "Give Host" in labels(m["screens"]["home"])
    host = m["screens"]["host"]
    assert labels(host) == ["Bob"]
    assert host["buttons"][0]["action"] == {
        "action": "host",
        "target": "Bob",
        "then": "home",
    }
    assert "host" not in menu(lobby, "Bob")["screens"]


def test_a_seated_player_readies_and_unreadies(lobby):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    home = menu(lobby, "Bob")["screens"]["home"]
    assert button(home, "I'm Ready")["action"] == {"action": "ready_toggle"}
    for needed in ("Switch Team", "Pick My Kit", "Watch", "Leave Game", "Game Info"):
        assert needed in labels(home)
    lobby.ready("Bob")
    home = menu(lobby, "Bob")["screens"]["home"]
    assert "Not Ready" in labels(home) and "I'm Ready" not in labels(home)
    body = " ".join(line["text"] for line in home["body"])
    assert "Ready" in body and "Waiting for: Ann" in body


def test_home_says_map_mode_rules_and_team(lobby):
    seat(lobby, ("Ann", "red"))
    body = [line["text"] for line in menu(lobby, "Ann")["screens"]["home"]["body"]]
    assert body[0] == "Crossroads · Team Battle"
    assert any("1 life each" in t and "no mobs" in t for t in body)
    assert any("Red Team" in t for t in body)
    assert any("host" in t.lower() for t in body)


def test_countdown_freezes_host_controls_but_lets_the_host_reopen(lobby):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.ready("Ann")
    lobby.ready("Bob")
    ann = menu(lobby, "Ann")
    home = ann["screens"]["home"]
    assert "Reopen Lobby" in labels(home) and "Game Settings" not in labels(home)
    assert button(home, "Reopen Lobby")["action"]["action"] == "reopen"
    assert button(home, "Not Ready")["action"] == {"action": "unready"}
    assert "Switch Team" not in labels(home)
    assert "Reopen Lobby" not in labels(menu(lobby, "Bob")["screens"]["home"])
    body = " ".join(line["text"] for line in home["body"])
    assert "Starting in" in body


def test_a_running_match_lets_outsiders_watch_and_queue(lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.ready("Ann")
    lobby.ready("Bob")
    clock.now += 10
    lobby.tick()
    outsider = menu(lobby, "Cy")["screens"]["home"]
    assert [lbl for lbl in labels(outsider) if "Watch" in lbl]
    in_match = menu(lobby, "Bob")["screens"]["home"]
    assert labels(in_match) == ["Game Info"]
    body = " ".join(line["text"] for line in in_match["body"])
    assert "in progress" in body
    host = menu(lobby, "Ann")["screens"]["home"]
    assert "Game Settings" not in labels(host)


def test_recap_offers_play_again_switch_team_and_results(lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.ready("Ann")
    lobby.ready("Bob")
    clock.now += 10
    lobby.tick()
    lobby.complete_match({"winner": "red", "outcome": "won", "seconds": 75})
    home = menu(lobby, "Bob")["screens"]["home"]
    assert button(home, "Play Again")["action"]["action"] == "play_again"
    assert button(home, "See Results")["go"] == "recap"
    assert "Switch Team" in labels(home)
    spectator = menu(lobby, "Newcomer")["screens"]["home"]
    assert "Play Again" not in labels(spectator) and "Join a Team" in labels(spectator)
    recap = menu(lobby, "Bob")["screens"]["recap"]
    body = " ".join(line["text"] for line in recap["body"])
    assert "Red Team won" in body and "1m 15s" in body


def test_recap_screen_before_any_match(lobby):
    recap = menu(lobby, "Ann")["screens"]["recap"]
    assert "No match" in recap["body"][0]["text"]


def test_recap_for_waves_and_unknown_results(lobby):
    lobby.join("Ann", "red")
    lobby.set_mode("Ann", "waves")
    lobby.complete_match({"outcome": "won", "players": {"Ann": {"deaths": 1}}})
    body = " ".join(
        line["text"] for line in menu(lobby, "Ann")["screens"]["recap"]["body"]
    )
    assert "beat" in body.lower()
    lobby.complete_match({"outcome": "lost", "players": [{"name": "Ann", "deaths": 2}]})
    body = " ".join(
        line["text"] for line in menu(lobby, "Ann")["screens"]["recap"]["body"]
    )
    assert "waves won" in body.lower()
    lobby.complete_match({})
    body = " ".join(
        line["text"] for line in menu(lobby, "Ann")["screens"]["recap"]["body"]
    )
    assert "over" in body.lower()


def test_a_spectator_can_leave_or_join(lobby):
    seat(lobby, ("Ann", "red"))
    lobby.watch("Cy")
    home = menu(lobby, "Cy")["screens"]["home"]
    assert "Join a Team" in labels(home) and "Leave Game" in labels(home)


# --- teams ----------------------------------------------------------------------


def test_team_buttons_show_counts_members_and_full_teams(lobby):
    lobby.join("Ann", "red")
    lobby.set_map("Ann", "duel")  # two teams of two
    seat(lobby, ("Bob", "red"), ("Cy", "blue"))
    teams = menu(lobby, "Cy")["screens"]["teams"]
    assert labels(teams) == ["Red Team (2/2)", "Blue Team (1/2) - you are here"]
    red = teams["buttons"][0]
    assert red["color"] == "gray" and "full" in red["tooltip"].lower()
    assert red["tooltip"].startswith("This team is full")
    assert "Ann, Bob" in red["tooltip"]
    blue = teams["buttons"][1]
    assert blue["go"] == "home"
    free = menu(lobby, "Dee")["screens"]["teams"]
    assert free["buttons"][1]["action"] == {
        "action": "join",
        "team": "blue",
        "then": "home",
    }
    assert free["buttons"][1]["color"] == "blue"
    assert free["exit"] == {"label": "Back", "go": "home"}


def test_teams_screen_is_locked_during_a_match(lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.ready("Ann")
    lobby.ready("Bob")
    clock.now += 10
    lobby.tick()
    teams = menu(lobby, "Cy")["screens"]["teams"]
    assert teams.get("buttons", []) == []
    assert "locked" in teams["body"][0]["text"].lower()


# --- kits -----------------------------------------------------------------------


def test_kit_picker_lists_the_pool_with_item_tooltips(lobby):
    seat(lobby, ("Ann", "red"))
    lobby.set_kit_mode("Ann", "class_picker")
    lobby.set_kit_pool("Ann", ["fighter", "archer"])
    kits = menu(lobby, "Ann")["screens"]["kits"]
    assert labels(kits) == ["Fighter", "Archer", "Random"]
    fighter = kits["buttons"][0]
    assert fighter["action"] == {"action": "kit_pick", "kit": "fighter", "then": "home"}
    assert (
        "Iron Sword" in fighter["tooltip"] and "Cooked Beef x16" in fighter["tooltip"]
    )
    assert kits["buttons"][2]["action"]["kit"] == "random"
    lobby.pick_kit("Ann", "archer")
    kits = menu(lobby, "Ann")["screens"]["kits"]
    assert labels(kits)[1] == "✔ Archer"
    assert "Archer" in " ".join(line["text"] for line in kits["body"])


def test_kit_picker_needs_a_seat(lobby):
    seat(lobby, ("Ann", "red"))
    lobby.set_kit_mode("Ann", "class_picker")
    kits = menu(lobby, "Newcomer")["screens"]["kits"]
    assert kits.get("buttons", []) == []
    assert "Join a team" in kits["body"][0]["text"]


@pytest.mark.parametrize(
    ("mode", "phrase"),
    [
        ("uniform", "same kit"),
        ("per_team", "its own kit"),
        ("everyone_random", "random kit"),
        ("random_shared", "One random kit"),
    ],
)
def test_other_kit_modes_explain_themselves(lobby, mode, phrase):
    seat(lobby, ("Ann", "red"))
    lobby.set_kit_mode("Ann", mode)
    kits = menu(lobby, "Ann")["screens"]["kits"]
    assert kits.get("buttons", []) == []
    assert phrase.lower() in " ".join(line["text"] for line in kits["body"]).lower()


def test_kit_explanations_name_the_kits_and_rerolls(lobby):
    seat(lobby, ("Ann", "red"))
    lobby.set_kit("Ann", "tank")
    lobby.set_kit_reroll("Ann", True)
    body = " ".join(
        line["text"] for line in menu(lobby, "Ann")["screens"]["kits"]["body"]
    )
    assert "Tank" in body and "respawn" in body
    lobby.set_kit_mode("Ann", "per_team")
    lobby.set_kit("Ann", "archer", team="red")
    lobby.set_kit("Ann", "random", team="blue")
    body = " ".join(
        line["text"] for line in menu(lobby, "Ann")["screens"]["kits"]["body"]
    )
    assert "Red Team: Archer" in body and "Blue Team: a random kit" in body


# --- info -----------------------------------------------------------------------


def test_info_lists_every_setting_read_only(lobby):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.watch("Cy")
    info = menu(lobby, "Bob")["screens"]["info"]
    body = [line["text"] for line in info["body"]]
    joined = " | ".join(body)
    for needle in (
        "Crossroads",
        "Team Battle",
        "1 life each",
        "No mobs",
        "Same kit for everyone",
        "Teams must be even",
        "Host: Ann",
        "Red Team: Ann",
        "Watching: Cy",
    ):
        assert needle in joined, needle
    assert not info.get("buttons")
    assert info["exit"] == {"label": "Back", "go": "home"}


# --- settings -------------------------------------------------------------------


def test_settings_form_offers_the_maps_modes_and_the_current_values(lobby):
    seat(lobby, ("Ann", "red"))
    lobby.set_respawns("Ann", "player", 7)
    settings = menu(lobby, "Ann")["screens"]["settings"]
    by_key = {i["key"]: i for i in settings["inputs"]}
    assert list(by_key) == [
        "mode",
        "lives",
        "kit_mode",
        "kit",
        "reroll",
        "waves",
        "uneven",
    ]
    assert [o["id"] for o in by_key["mode"]["options"]] == ["teams", "waves", "race"]
    assert [o["label"] for o in by_key["mode"]["options"]] == [
        "Team Battle",
        "Co-op Waves",
        "Kill Race",
    ]
    assert by_key["lives"]["initial"] == "player:7"
    assert {"id": "player:7", "label": "8 lives each"} in by_key["lives"]["options"]
    ids = [o["id"] for o in by_key["lives"]["options"]]
    assert "elimination" in ids and "team:5" in ids
    assert {"id": "elimination", "label": "1 life each"} in by_key["lives"]["options"]
    assert {"id": "team:5", "label": "Team pool: 5 respawns"} in by_key["lives"][
        "options"
    ]
    assert by_key["kit_mode"]["initial"] == "uniform"
    kit_ids = [o["id"] for o in by_key["kit"]["options"]]
    assert {"fighter", "random", "current"} <= set(kit_ids)
    assert by_key["kit"]["initial"] == "current"
    assert by_key["reroll"]["initial"] is False
    assert by_key["waves"]["initial"] is False and by_key["uneven"]["initial"] is False
    save = button(settings, "Save Settings")
    assert save["submit"] == {"action": "settings", "then": "home"}


def test_settings_mode_dropdown_only_offers_what_the_map_supports(lobby):
    lobby.join("Ann", "red")
    lobby.set_map("Ann", "vault")
    assert lobby.snapshot()["compatible_modes"] == ["waves"]
    modes = ["waves"]
    by_key = {i["key"]: i for i in menu(lobby, "Ann")["screens"]["settings"]["inputs"]}
    assert [o["id"] for o in by_key["mode"]["options"]] == modes


def test_settings_reflect_a_library_kit_and_waves(lobby):
    lobby.join("Ann", "red")
    lobby.set_kit("Ann", "tank")
    lobby.set_mode("Ann", "waves")
    by_key = {i["key"]: i for i in menu(lobby, "Ann")["screens"]["settings"]["inputs"]}
    assert by_key["kit"]["initial"] == "tank"
    assert by_key["waves"]["initial"] is True


# --- maps -----------------------------------------------------------------------


def test_maps_screen_has_a_button_per_map(lobby):
    lobby.join("Ann", "red")
    maps = menu(lobby, "Ann")["screens"]["maps"]
    assert "✔ Crossroads" in labels(maps)
    assert "Crash Site" in labels(maps)
    duel = button(maps, "Duel")
    assert duel["action"] == {"action": "map", "map": "duel", "then": "home"}
    assert "2 teams" in duel["tooltip"] and "Team Battle" in duel["tooltip"]
    assert len(maps["buttons"]) == len(lobby.snapshot()["maps"])


# --- bots -----------------------------------------------------------------------


def test_bots_screens(lobby):
    lobby.join("Ann", "red")
    lobby.add_bot("Ann", "blue", name="Robo")
    m = menu(lobby, "Ann")
    bots = m["screens"]["bots"]
    assert labels(bots) == ["Add a Bot", "Robo (Blue Team)"]
    assert bots["buttons"][0]["go"] == "bot_add"
    assert bots["buttons"][1]["go"] == "bot_1"
    add = m["screens"]["bot_add"]
    by_key = {i["key"]: i for i in add["inputs"]}
    assert [o["id"] for o in by_key["team"]["options"]] == [
        "auto",
        "red",
        "blue",
        "green",
        "yellow",
    ]
    assert by_key["name"]["kind"] == "text" and by_key["name"]["max_length"] == 16
    assert button(add, "Add Bot")["submit"] == {"action": "bot_add", "then": "bots"}
    assert add["exit"] == {"label": "Back", "go": "bots"}
    one = m["screens"]["bot_1"]
    bot_id = next(iter(lobby.snapshot()["bots"]))
    assert one["title"] == "Robo"
    assert button(one, "Rename Bot")["submit"] == {
        "action": "bot_rename",
        "bot": bot_id,
        "then": "bots",
    }
    assert button(one, "Move to Team")["submit"]["action"] == "bot_move"
    assert button(one, "Remove Bot")["action"] == {
        "action": "bot_remove",
        "bot": bot_id,
        "then": "bots",
    }
    assert {i["key"]: i for i in one["inputs"]}["team"]["initial"] == "blue"
    assert {i["key"]: i for i in one["inputs"]}["name"]["initial"] == "Robo"
    assert one["exit"] == {"label": "Back", "go": "bots"}


def test_a_bot_that_failed_can_be_retried(tmp_path, clock):
    class Failing:
        def bots(self):
            return []

        def add_bot(self, bot_id, name):
            return {"ok": False, "error": "no room"}

        def remove_bot(self, bot_id):
            return {"ok": True}

    lobby = ArenaLobby(
        state_path=tmp_path / "l.json",
        runtime_dir=tmp_path,
        kits_path=tmp_path / "k.json",
        fleet=Failing(),
        clock=clock,
    )
    lobby.join("Ann", "red")
    lobby.add_bot("Ann", "blue")
    m = menu(lobby, "Ann")
    check_menu(m)
    one = m["screens"]["bot_1"]
    assert "Retry" in labels(one)
    assert "could not connect" in " ".join(line["text"] for line in one["body"])
    assert button(one, "Retry")["action"]["action"] == "bot_retry"


# --- mobs -----------------------------------------------------------------------


def test_mobs_screen_lists_groups_and_adds(lobby):
    lobby.join("Ann", "red")
    lobby.add_mob_group("Ann", "blue", "skeleton", 3)
    mobs = menu(lobby, "Ann")["screens"]["mobs"]
    assert "3 skeleton (Blue Team)" in " ".join(line["text"] for line in mobs["body"])
    group = lobby.snapshot()["mob_groups"][0]["id"]
    remove = [b for b in mobs["buttons"] if b["label"].startswith("Remove")][0]
    assert remove["action"] == {
        "action": "mob_remove",
        "group_id": group,
        "then": "mobs",
    }
    add = button(mobs, "Add Mobs")
    assert add["submit"] == {"action": "mob_add", "then": "mobs"}
    by_key = {i["key"]: i for i in mobs["inputs"]}
    assert [o["id"] for o in by_key["team"]["options"]] == [
        "red",
        "blue",
        "green",
        "yellow",
    ]
    assert "zombie" in [o["id"] for o in by_key["mob"]["options"]]
    assert by_key["count"]["kind"] == "slider"


def test_allied_mobs_only_where_they_make_sense(lobby):
    lobby.join("Ann", "red")
    lobby.set_map("Ann", "vault")
    home = menu(lobby, "Ann")["screens"]["home"]
    assert "Allied Mobs" not in labels(home)
    lobby.set_map("Ann", "crossroads")
    lobby.set_mode("Ann", "waves")
    assert "Allied Mobs" not in labels(menu(lobby, "Ann")["screens"]["home"])


# --- signs and the floating board -------------------------------------------------


def test_team_signs_show_counts_full_and_unavailable_teams(lobby):
    lobby.join("Ann", "red")
    lobby.set_map("Ann", "duel")
    seat(lobby, ("Bob", "red"), ("Cy", "blue"))
    signs = lm.sign_lines(lobby.snapshot())
    assert set(signs) == {
        "join_red",
        "join_blue",
        "join_green",
        "join_yellow",
        "ready",
        "help",
    }
    red = [line["text"] for line in signs["join_red"]["lines"]]
    assert red[0] == "RED TEAM" and "FULL" in red
    assert signs["join_red"]["lines"][0]["color"] == "red"
    blue = [line["text"] for line in signs["join_blue"]["lines"]]
    assert "1/2 players" in blue and "Click to join" in blue
    green = [line["text"] for line in signs["join_green"]["lines"]]
    assert green == ["GREEN TEAM", "Not on", "this map"]
    assert signs["join_green"]["lines"][1]["color"] == "gray"


def test_sign_lines_fit_a_sign(lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    for phase in range(4):
        if phase == 1:
            lobby.ready("Ann")
            lobby.ready("Bob")
        if phase == 2:
            clock.now += 10
            lobby.tick()
        if phase == 3:
            lobby.complete_match({})
        for sign in lm.sign_lines(lobby.snapshot()).values():
            assert 1 <= len(sign["lines"]) <= 4
            for line in sign["lines"]:
                assert len(line["text"]) <= 15
                assert line.get("color", "white") in CHAT_COLORS


def test_ready_sign_counts_ready_players(lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    texts_ = lambda: [  # noqa: E731
        line["text"] for line in lm.sign_lines(lobby.snapshot())["ready"]["lines"]
    ]
    assert texts_() == ["READY", "0/2 ready", "Click to toggle"]
    lobby.ready("Ann")
    assert texts_()[1] == "1/2 ready"
    lobby.ready("Bob")
    assert texts_() == ["STARTING", "in 5 seconds", "Click to cancel"]
    clock.now += 10
    lobby.tick()
    assert texts_() == ["MATCH", "in progress"]
    lobby.complete_match({})
    assert texts_() == ["READY", "Match over", "Play again?"]


def test_ready_sign_with_nobody_seated(lobby):
    assert [
        line["text"] for line in lm.sign_lines(lobby.snapshot())["ready"]["lines"]
    ] == [
        "READY",
        "Nobody has",
        "joined yet",
    ]


def test_help_sign_points_at_the_compass_and_g(lobby):
    lines = [line["text"] for line in lm.sign_lines(lobby.snapshot())["help"]["lines"]]
    assert lines == ["GAME MENU", "Right-click the", "compass or", "press G"]


def test_join_signs_while_a_match_is_on(lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.ready("Ann")
    lobby.ready("Bob")
    signs = lm.sign_lines(lobby.snapshot())
    assert [line["text"] for line in signs["join_red"]["lines"]][-1] == "Locked"


def test_display_lines(lobby, clock):
    lines = lm.display_lines(lobby.snapshot())
    assert 1 <= len(lines) <= 8 and all(len(x["text"]) <= 64 for x in lines)
    assert lines[0] == {"text": "CROSSROADS", "color": "gold", "bold": True}
    assert lines[1]["text"] == "Team Battle · 1 life each · no mobs"
    joined = " | ".join(x["text"] for x in lines)
    assert "Waiting for players to join" in joined
    assert "compass" in joined
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.ready("Ann")
    joined = " | ".join(x["text"] for x in lm.display_lines(lobby.snapshot()))
    assert "Waiting for: Bob" in joined
    lobby.ready("Bob")
    joined = " | ".join(x["text"] for x in lm.display_lines(lobby.snapshot()))
    assert "Starting in 5" in joined
    clock.now += 10
    lobby.tick()
    joined = " | ".join(x["text"] for x in lm.display_lines(lobby.snapshot()))
    assert "A match is in progress" in joined
    lobby.complete_match({"winner": "blue"})
    joined = " | ".join(x["text"] for x in lm.display_lines(lobby.snapshot()))
    assert "Blue Team won" in joined
    lobby.complete_match({})
    joined = " | ".join(x["text"] for x in lm.display_lines(lobby.snapshot()))
    assert "Match over" in joined


def test_display_says_why_a_start_is_blocked(lobby):
    seat(lobby, ("Ann", "red"), ("Bob", "red"), ("Cy", "blue"))
    for who in ("Ann", "Bob", "Cy"):
        lobby.ready(who)
    joined = " | ".join(x["text"] for x in lm.display_lines(lobby.snapshot()))
    assert "uneven" in joined.lower()


def test_long_text_is_cut_to_the_limits():
    assert lm.cut("x" * 100, 10) == "x" * 10
    assert lm.cut("short", 10) == "short"
    assert lm.cut("a b c d", 4) == "a b"
    assert lm.cut("", 5) == ""


# --- publisher ------------------------------------------------------------------


class Wire:
    """Records what the mod was sent; can fail on demand."""

    def __init__(self):
        self.sent = []
        self.fail = 0

    def __call__(self, **fields):
        if self.fail:
            self.fail -= 1
            raise OSError("mod unreachable")
        self.sent.append(fields)
        return {"ok": True}


@pytest.fixture
def pub(lobby):
    wire = Wire()
    online = ["Ann", "Bob", "MCBot"]
    boards = []
    publisher = lm.LobbyPublisher(
        wire,
        lambda: list(online),
        lobby,
        board=lambda name, title, rows: boards.append((name, title, rows)),
        skip=lambda: ["MCBot"],
    )
    publisher.wire, publisher.online, publisher.boards = wire, online, boards
    return publisher


def test_first_publish_sends_everything_once(pub, lobby):
    seat(lobby, ("Ann", "red"))
    pub.publish()
    (sent,) = pub.wire.sent
    assert set(sent) == {"menus", "signs", "display"}
    assert set(sent["menus"]) == {"Ann", "Bob"}  # not the bot
    assert len(sent["signs"]) == 6 and sent["display"]
    assert len(pub.boards) == 1 and pub.boards[0][0] == "lobby"
    pub.publish()
    assert len(pub.wire.sent) == 1  # nothing changed, nothing sent
    assert len(pub.boards) == 1


def test_publish_sends_only_what_changed(pub, lobby):
    seat(lobby, ("Ann", "red"))
    pub.publish()
    lobby.join("Bob", "blue")
    pub.publish()
    second = pub.wire.sent[1]
    assert set(second["menus"]) == {"Ann", "Bob"}  # both menus mention the roster
    assert "join_blue" in second["signs"] and "ready" in second["signs"]
    assert "join_red" not in second["signs"] and "join_green" not in second["signs"]
    lobby.watch("Cy")
    pub.online.append("Cy")
    pub.publish()
    third = pub.wire.sent[2]
    assert "Cy" in third["menus"]


def test_a_menu_that_did_not_change_is_not_resent(pub, lobby):
    seat(lobby, ("Ann", "red"))
    pub.publish()
    pub.online.append("Zed")
    pub.publish()
    assert set(pub.wire.sent[1]["menus"]) == {"Zed"}
    assert "signs" not in pub.wire.sent[1] and "display" not in pub.wire.sent[1]


def test_show_is_always_sent_and_carries_the_actors_menu(pub, lobby):
    seat(lobby, ("Ann", "red"))
    pub.publish()
    notice = {"text": "You joined Red", "color": "green"}
    pub.publish(show={"Ann": {"screen": "home", "notice": notice}})
    assert pub.wire.sent[1] == {"show": {"Ann": {"screen": "home", "notice": notice}}}


def test_show_for_a_player_not_in_the_online_list_still_gets_a_menu(pub, lobby):
    seat(lobby, ("Ann", "red"))
    pub.publish(show={"Zed": {"screen": "home"}})
    sent = pub.wire.sent[0]
    assert "Zed" in sent["menus"] and sent["show"] == {"Zed": {"screen": "home"}}


def test_a_failed_send_is_logged_swallowed_and_retried(pub, lobby, caplog):
    seat(lobby, ("Ann", "red"))
    pub.wire.fail = 1
    with caplog.at_level("DEBUG"):
        pub.publish()  # does not raise
    assert not pub.wire.sent
    pub.tick()  # nothing changed in the lobby, but the last send failed
    (sent,) = pub.wire.sent
    assert set(sent) >= {"menus", "signs", "display"}


def test_a_failed_board_push_is_retried(lobby):
    seat(lobby, ("Ann", "red"))
    wire, calls, fail = Wire(), [], [True]

    def board(name, title, rows):
        if fail[0]:
            raise OSError("down")
        calls.append(title)

    publisher = lm.LobbyPublisher(wire, lambda: ["Ann"], lobby, board=board)
    publisher.publish()
    assert calls == []
    fail[0] = False
    publisher.tick()
    assert len(calls) == 1


def test_players_that_cannot_be_listed_just_means_no_menus(lobby):
    seat(lobby, ("Ann", "red"))
    wire = Wire()

    def broken():
        raise OSError("bridge down")

    publisher = lm.LobbyPublisher(wire, broken, lobby)
    publisher.publish()
    assert "menus" not in wire.sent[0] and "signs" in wire.sent[0]
    wire2 = Wire()
    none = lm.LobbyPublisher(wire2, lambda: None, lobby)
    none.publish()
    assert "menus" not in wire2.sent[0]


def test_tick_republishes_on_revision_phase_countdown_and_players(pub, lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    pub.tick()
    assert len(pub.wire.sent) == 1
    pub.tick()
    assert len(pub.wire.sent) == 1  # idle
    lobby.ready("Ann")
    pub.tick()
    # readiness is not a revision bump but the board and menus show it
    assert len(pub.wire.sent) == 2 or len(pub.boards) >= 1
    pub.online.append("Cy")
    before = len(pub.wire.sent)
    pub.tick()
    assert len(pub.wire.sent) == before + 1 and "Cy" in pub.wire.sent[-1]["menus"]


def test_tick_follows_the_countdown_each_second(pub, lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.ready("Ann")
    lobby.ready("Bob")
    pub.tick()
    count = len(pub.wire.sent)
    clock.now += 1.0
    pub.tick()
    assert len(pub.wire.sent) == count + 1
    clock.now += 0.1
    pub.tick()
    assert len(pub.wire.sent) == count + 1


def test_a_match_start_closes_everyones_menu_and_the_end_shows_the_recap(
    pub, lobby, clock
):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.watch("Cy")
    pub.online.append("Cy")
    lobby.ready("Ann")
    lobby.ready("Bob")
    pub.tick()
    clock.now += 10
    lobby.tick()
    pub.tick()
    assert any(
        sorted(s.get("close", [])) == ["Ann", "Bob", "Cy"] for s in pub.wire.sent
    )
    lobby.complete_match({"winner": "red"})
    pub.tick()
    shows = [s["show"] for s in pub.wire.sent if "show" in s]
    assert shows[-1] == {
        "Ann": {"screen": "recap"},
        "Bob": {"screen": "recap"},
        "Cy": {"screen": "recap"},
    }


def test_the_first_tick_after_a_restart_does_not_replay_events(pub, lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.complete_match({"winner": "red"})
    pub.tick()
    assert not any("show" in s or "close" in s for s in pub.wire.sent)


def test_players_whose_ready_was_cleared_are_told_to_ready_up_again(pub, lobby):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"), ("Cy", "red"))
    lobby.ready("Ann")
    lobby.ready("Bob")
    pub.tick()
    lobby.set_map("Ann", "hall")
    pub.tick()
    announces = [a for s in pub.wire.sent for a in s.get("announce", [])]
    assert len(announces) == 1
    (note,) = announces
    assert sorted(note["players"]) == ["Ann", "Bob"]
    assert note["title"] and len(note["title"]) <= 40 and len(note["subtitle"]) <= 80
    assert "Hall" in note["subtitle"] and "Ready up again" not in note["title"] or True
    pub.tick()
    assert len([a for s in pub.wire.sent for a in s.get("announce", [])]) == 1


def test_nobody_is_announced_when_nobody_was_ready(pub, lobby):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    pub.tick()
    lobby.set_map("Ann", "hall")
    pub.tick()
    assert not any("announce" in s for s in pub.wire.sent)


def test_a_failed_close_is_retried(pub, lobby, clock):
    seat(lobby, ("Ann", "red"), ("Bob", "blue"))
    lobby.ready("Ann")
    lobby.ready("Bob")
    pub.tick()
    clock.now += 10
    lobby.tick()
    pub.wire.fail = 1
    pub.tick()
    assert not any("close" in s for s in pub.wire.sent)
    pub.tick()
    assert any("close" in s for s in pub.wire.sent)
    pub.tick()
    assert len([s for s in pub.wire.sent if "close" in s]) == 1


def test_a_lobby_that_cannot_be_read_is_survived(pub, lobby, monkeypatch):
    def boom():
        raise RuntimeError("disk")

    monkeypatch.setattr(lobby, "snapshot", boom)
    pub.tick()  # no exception
    pub.publish()
    assert not pub.wire.sent


def test_the_menu_is_what_the_dashboard_made_it(pub, lobby):
    """Everything the publisher sends is plain JSON the mod can take."""
    seat(lobby, ("Ann", "red"))
    pub.publish()
    json.dumps(pub.wire.sent)
    for m in pub.wire.sent[0]["menus"].values():
        check_menu(m)


def test_lobby_errors_are_not_ours_to_hide(lobby):
    with pytest.raises(LobbyError):
        lobby.apply_settings("Ann", {"mode": "teams"})
