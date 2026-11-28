"""What the arena lobby's players see: the game menu, the signs and the floating board.

Pure builders over a lobby snapshot (``menu_for``, ``sign_lines``,
``display_lines``) and ``LobbyPublisher``, which sends only what changed to the
mod as ``arena_lobby_view``.
Every word here is plain English for kids on stock clients: no revision numbers, no ids,
no jargon.  The wire shapes, limits and station layout are in ``docs/LOBBY_MENU.md``.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from mclab.arena import standings

log = logging.getLogger(__name__)

MODE_LABELS = {"teams": "Team Battle", "waves": "Co-op Waves", "race": "Kill Race"}
KIT_MODE_LABELS = {
    "uniform": "Same kit for everyone",
    "per_team": "Kit per team",
    "class_picker": "Choose your own",
    "everyone_random": "Everyone random",
    "random_shared": "One random kit for all",
}
RANDOM = "random"
CURRENT = "current"  # the kit dropdown's id for a kit that is not in the library
AUTO = "auto"  # the bot form's id for "any team with room"
STANDARD_LIVES = (
    "elimination",
    "player:1",
    "player:2",
    "player:3",
    "player:5",
    "team:2",
    "team:5",
    "team:10",
)
MOBS = (
    ("zombie", "Zombie"),
    ("husk", "Husk"),
    ("skeleton", "Skeleton"),
    ("stray", "Stray"),
    ("spider", "Spider"),
    ("creeper", "Creeper"),
    ("iron_golem", "Iron Golem"),
    ("wither_skeleton", "Wither Skeleton"),
    ("blaze", "Blaze"),
    ("pillager", "Pillager"),
    ("vindicator", "Vindicator"),
    ("witch", "Witch"),
)
STATION_TEAMS = ("red", "blue", "green", "yellow")
# the contract's limits (docs/LOBBY_MENU.md)
MAX_BODY, MAX_INPUTS, MAX_BUTTONS = 12, 8, 24
MAX_TITLE, MAX_LINE, MAX_LABEL, MAX_TIP = 64, 200, 40, 200
MAX_NAME = 16
SIGN_CHARS, SIGN_LINES = 15, 4
DISPLAY_CHARS, DISPLAY_LINES = 64, 8
MAX_BOTS, MAX_PICKS = 20, 22
NOTICE_CHARS, TITLE_CHARS, SUBTITLE_CHARS = 200, 40, 80


def cut(text: Any, limit: int) -> str:
    """``text`` shortened to ``limit`` characters (no ellipsis: kids read this)."""
    return str(text)[:limit].rstrip()


# --- words ----------------------------------------------------------------------


def mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, str(mode).replace("_", " ").title())


def map_label(name: str) -> str:
    return str(name).replace("_", " ").replace("-", " ").title()


def team_label(team: str) -> str:
    return f"{str(team).replace('_', ' ').title()} Team"


def lives_id(mode: str, count: int) -> str:
    return "elimination" if mode == "elimination" else f"{mode}:{count}"


def lives_label(mode: str, count: int) -> str:
    if mode == "player":
        return f"{count + 1} lives each"
    if mode == "team":
        return f"Team pool: {count} respawn{'' if count == 1 else 's'}"
    return "1 life each"


def lives_option_label(option_id: str) -> str:
    mode, _, count = option_id.partition(":")
    return lives_label(mode, int(count) if count.isdigit() else 0)


def rules_text(snap: Mapping[str, Any]) -> str:
    """ "1 life each · no mobs": the rules that matter to the current mode."""
    parts = []
    if snap["mode"] == "teams":
        parts.append(lives_label(snap["respawn_mode"], snap["respawn_count"]))
    parts.append("mob waves" if snap["hazards_enabled"] else "no mobs")
    return " · ".join(parts)


def item_label(item: str) -> str:
    return item.rsplit(":", 1)[-1].replace("_", " ").title()


def _mob_text(group) -> str:
    return f"{group['count']} {str(group['mob']).removeprefix('minecraft:')}"


def kit_items_text(kit: Mapping[str, Any]) -> str:
    parts = []
    for entry in kit.get("items") or []:
        name = item_label(str(entry.get("item", "")))
        count = int(entry.get("count") or 1)
        parts.append(f"{name} x{count}" if count > 1 else name)
    return ", ".join(parts)


def _kit_names(kits: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    return {str(k["id"]).lower(): str(k["name"]) for k in kits}


def kit_name(kit_id: str | None, names: Mapping[str, str]) -> str:
    """A kit id as a player reads it: a name, "a random kit" or "the usual kit"."""
    key = str(kit_id or "").strip().lower()
    if key == RANDOM:
        return "a random kit"
    return names.get(key, "the standard loadout")


def seconds_text(n: int) -> str:
    return f"{n} second{'' if n == 1 else 's'}"


def countdown_seconds(snap: Mapping[str, Any]) -> int | None:
    remaining = snap.get("countdown_remaining_s")
    if snap["phase"] != "countdown" or remaining is None:
        return None
    return max(0, math.ceil(remaining))


def duration_text(seconds: Any) -> str | None:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    total = max(0, int(round(seconds)))
    minutes, rest = divmod(total, 60)
    return f"{minutes}m {rest}s" if minutes else f"{rest}s"


def result_text(snap: Mapping[str, Any]) -> str | None:
    """How the last match ended, in a few words (None before the first one)."""
    recap = snap.get("last_recap")
    if recap is None:
        return None
    winner = recap.get("winner")
    if isinstance(winner, str) and winner:
        if winner.lower() in ("draw", "tie", "none"):
            return "It was a draw"
        return f"{team_label(winner)} won"
    outcome = str(recap.get("outcome") or "").lower()
    if outcome in ("won", "win", "victory"):
        return "You beat the waves!"
    if outcome in ("lost", "lose", "failed", "defeat"):
        return "The waves won this time"
    return "Match over"


# --- the snapshot, read -----------------------------------------------------------


def _ci(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.lower() == b.lower()


def seat_of(snap: Mapping[str, Any], player: str) -> tuple[str, str | None]:
    """("team", team) for a seated player, ("spectator", None) or ("none", None)."""
    for team, info in snap["teams"].items():
        if any(_ci(h, player) for h in info["humans"]):
            return "team", team
    if any(_ci(s, player) for s in snap["spectators"]):
        return "spectator", None
    return "none", None


def _humans(snap: Mapping[str, Any]) -> list[str]:
    return [h for info in snap["teams"].values() for h in info["humans"]]


def _ready_names(snap: Mapping[str, Any]) -> set[str]:
    rev = snap["revision"]
    return {n for n, r in snap["ready_revisions"].items() if r == rev}


def _others(snap: Mapping[str, Any], player: str) -> list[str]:
    people = _humans(snap) + list(snap["spectators"])
    seen: set[str] = set()
    out = []
    for name in people:
        if not _ci(name, player) and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _map_teams(snap: Mapping[str, Any]) -> list[str]:
    info = snap["maps"].get(snap["map"]) or {}
    return list(info.get("teams") or snap["teams"])


def _team_index(snap: Mapping[str, Any], team: str) -> int:
    names = list(snap["teams"])
    return names.index(team) if team in names else 0


def team_color(snap: Mapping[str, Any], team: str) -> str:
    return standings.team_color(team, _team_index(snap, team))


def _members(info: Mapping[str, Any]) -> list[str]:
    return list(info["humans"]) + [f"{b['name']} (bot)" for b in info["bots"]]


def _status_words(bot: Mapping[str, Any]) -> str:
    if bot["status"] == "connected":
        return "Ready"
    if bot["status"] == "failed":
        return "Could not connect"
    return "Connecting"


# --- builders -------------------------------------------------------------------


def _line(
    text: str, color: str | None = None, bold: bool = False, limit: int = MAX_LINE
):
    out: dict[str, Any] = {"text": cut(text, limit)}
    if color:
        out["color"] = color
    if bold:
        out["bold"] = True
    return out


def _act(action: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"action": action}
    for key, value in extra.items():
        out[key] = cut(value, 64) if isinstance(value, str) else value
    return out


def _btn(
    label: str,
    *,
    go: str | None = None,
    action: dict | None = None,
    submit: dict | None = None,
    tip: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"label": cut(label, MAX_LABEL)}
    if tip:
        out["tooltip"] = cut(tip, MAX_TIP)
    if color:
        out["color"] = color
    if go is not None:
        out["go"] = go
    if action is not None:
        out["action"] = action
    if submit is not None:
        out["submit"] = submit
    return out


def _back(to: str = "home") -> dict[str, Any]:
    return {"label": "Back", "go": to}


def _dropdown(key, label, options, initial):
    opts = [{"id": cut(i, 48), "label": cut(lbl, 64)} for i, lbl in options]
    return {
        "key": key,
        "kind": "dropdown",
        "label": cut(label, 64),
        "options": opts[:32],
        "initial": initial,
    }


def _toggle(key, label, initial):
    return {
        "key": key,
        "kind": "toggle",
        "label": cut(label, 64),
        "initial": bool(initial),
    }


def _text_input(key, label, initial=""):
    return {
        "key": key,
        "kind": "text",
        "label": cut(label, 64),
        "initial": cut(initial, MAX_NAME),
        "max_length": MAX_NAME,
    }


def _screen(
    title: str,
    body: Sequence[dict] = (),
    *,
    inputs: Sequence[dict] = (),
    buttons: Sequence[dict] = (),
    exit: dict | None = None,
    columns: int | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"title": cut(title, MAX_TITLE) or "MCB Arena"}
    if body:
        out["body"] = [dict(x, text=cut(x["text"], MAX_LINE)) for x in body[:MAX_BODY]]
    if inputs:
        out["inputs"] = list(inputs)[:MAX_INPUTS]
    if buttons:
        out["buttons"] = list(buttons)[:MAX_BUTTONS]
    if columns:
        out["columns"] = columns
    if exit is not None:
        out["exit"] = exit
    return out


# --- screens ----------------------------------------------------------------------


def _status_lines(snap: Mapping[str, Any]) -> list[tuple[str, str]]:
    """What the game is waiting for, as (text, colour) lines for the current phase."""
    phase = snap["phase"]
    if phase == "countdown":
        n = countdown_seconds(snap) or 0
        return [(f"Starting in {seconds_text(n)}", "green")]
    if phase == "running":
        return [("A match is in progress", "gold")]
    if phase == "recap":
        return [(f"Last match: {result_text(snap) or 'Match over'}", "aqua")]
    if not _humans(snap):
        return [("Waiting for players to join", "yellow")]
    reasons = list(snap["blocking_reasons"])
    if not reasons:
        return [("Everyone is ready", "green")]
    return [(r, "yellow") for r in reasons[:2]]


def _home(snap, player, *, host: bool, kits: Mapping[str, str]) -> dict[str, Any]:
    phase = snap["phase"]
    kind, team = seat_of(snap, player)
    body = [
        _line(f"{map_label(snap['map'])} · {mode_label(snap['mode'])}", "white", True),
        _line(f"Rules: {rules_text(snap)}", "gray"),
    ]
    if kind == "team":
        text = f"You are on the {team_label(team)}"
        if phase in ("open", "countdown"):
            ready = player.lower() in _ready_names(snap)
            text += " · Ready" if ready else " · Not ready"
        body.append(_line(text, team_color(snap, team)))
    elif kind == "spectator":
        body.append(_line("You are watching", "aqua"))
    else:
        body.append(_line("You have not joined a team yet", "gray"))
    body.extend(_line(text, color) for text, color in _status_lines(snap))
    if host:
        body.append(_line("You are the host", "gold"))
    elif snap["host"]:
        body.append(_line(f"Host: {snap['host']}", "gray"))

    b: list[dict[str, Any]] = []
    join = _btn("Join a Team", go="teams", tip="Pick the team you want to fight for")
    leave = _btn("Leave Game", action=_act("leave"), color="red")
    watch = _btn("Watch", action=_act("watch"), tip="Watch without playing")
    if phase == "open":
        if kind == "team":
            if player.lower() in _ready_names(snap):
                b.append(_btn("Not Ready", action=_act("ready_toggle"), color="gold"))
            else:
                b.append(
                    _btn(
                        "I'm Ready",
                        action=_act("ready_toggle"),
                        color="green",
                        tip="The game starts when everyone is ready",
                    )
                )
            b += [
                _btn("Switch Team", go="teams"),
                _btn("Pick My Kit", go="kits"),
                watch,
                leave,
            ]
        elif kind == "spectator":
            b += [join, leave]
        else:
            b += [join, watch]
    elif phase == "countdown":
        if kind == "team":
            b += [
                _btn(
                    "Not Ready",
                    action=_act("unready"),
                    color="gold",
                    tip="Stop the countdown",
                ),
                leave,
            ]
        elif kind == "spectator":
            b.append(leave)
        else:
            b.append(watch)
    elif phase == "running":
        if kind == "spectator":
            b.append(leave)
        elif kind == "none":
            b.append(
                _btn(
                    "Watch (join next round)",
                    action=_act("watch"),
                    tip="A match is running. You will be put in the next round.",
                )
            )
    else:  # recap
        if kind == "team":
            b += [
                _btn("Play Again", action=_act("play_again"), color="green"),
                _btn("Switch Team", go="teams"),
                _btn("Pick My Kit", go="kits"),
                watch,
                leave,
            ]
        elif kind == "spectator":
            b += [join, leave]
        else:
            b.append(join)
        b.append(_btn("See Results", go="recap"))
    b.append(_btn("Game Info", go="info"))

    if host:
        if phase in ("open", "recap"):
            b += [
                _btn("Game Settings", go="settings", tip="Mode, lives, kits and more"),
                _btn("Choose Map", go="maps"),
                _btn("Bots", go="bots", tip="Add computer players"),
            ]
            if _mobs_allowed(snap):
                b.append(
                    _btn("Allied Mobs", go="mobs", tip="Mobs that fight on a team")
                )
        if phase == "countdown":
            b.append(
                _btn(
                    "Reopen Lobby",
                    action=_act("reopen"),
                    color="gold",
                    tip="Stop the countdown so settings can change",
                )
            )
        if _others(snap, player):
            b.append(_btn("Give Host", go="host", tip="Let someone else run the game"))
    return _screen("MCB Arena", body, buttons=b, columns=2)


def _teams(snap, player) -> dict[str, Any]:
    kind, mine = seat_of(snap, player)
    if snap["phase"] in ("countdown", "running"):
        return _screen(
            "Choose a Team",
            [_line("Teams are locked while a match is starting or running.", "gray")],
            exit=_back(),
        )
    buttons = []
    for team in _map_teams(snap):
        info = snap["teams"].get(team) or {"humans": [], "bots": [], "player_count": 0}
        cap = (snap["maps"].get(snap["map"]) or {}).get("per_team", 0)
        count = info.get("player_count", 0)
        label = f"{team_label(team)} ({count}/{cap})"
        members = ", ".join(_members(info)) or "Nobody yet"
        if team == mine:
            buttons.append(
                _btn(
                    f"{label} - you are here",
                    go="home",
                    color=team_color(snap, team),
                    tip=f"You are on this team: {members}",
                )
            )
        elif cap and count >= cap:
            buttons.append(
                _btn(
                    label, go="teams", color="gray", tip=f"This team is full: {members}"
                )
            )
        else:
            buttons.append(
                _btn(
                    label,
                    action=_act("join", team=team, then="home"),
                    color=team_color(snap, team),
                    tip=members,
                )
            )
    return _screen(
        "Choose a Team",
        [_line("Pick the team you want to fight for.")],
        buttons=buttons,
        exit=_back(),
        columns=1,
    )


def _kits(snap, player, kits: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    names = _kit_names(kits)
    mode = snap["kit_mode"]
    kind, _ = seat_of(snap, player)
    body: list[dict[str, Any]] = []
    buttons: list[dict[str, Any]] = []
    if mode == "class_picker":
        if kind != "team":
            body.append(_line("Join a team first to pick your kit."))
        else:
            mine = next(
                (v for k, v in snap["player_kits"].items() if _ci(k, player)), None
            )
            body.append(_line("Pick the kit you want to fight with."))
            pick = kit_name(mine, names) if mine else "none yet"
            body.append(_line(f"Your pick: {pick[:1].upper()}{pick[1:]}", "gold"))
            by_id = {str(k["id"]).lower(): k for k in kits}
            for kid in [str(x).lower() for x in snap["kit_pool"]][:MAX_PICKS]:
                kit = by_id.get(kid)
                if kit is None:
                    continue
                mark = "✔ " if mine and mine.lower() == kid else ""
                buttons.append(
                    _btn(
                        f"{mark}{kit['name']}",
                        action=_act("kit_pick", kit=kid, then="home"),
                        tip=kit_items_text(kit),
                    )
                )
            mark = "✔ " if mine and mine.lower() == RANDOM else ""
            buttons.append(
                _btn(
                    f"{mark}Random",
                    action=_act("kit_pick", kit=RANDOM, then="home"),
                    tip="Get a surprise kit when the match starts",
                )
            )
    else:
        body.append(_line(f"Kits: {KIT_MODE_LABELS.get(mode, mode)}", "gold"))
        if mode == "uniform":
            body.append(
                _line(f"Everyone gets the same kit: {kit_name(snap['kit'], names)}.")
            )
        elif mode == "per_team":
            body.append(_line("Each team gets its own kit."))
            for team in _map_teams(snap):
                if team in snap["team_kits"]:
                    chosen = kit_name(snap["team_kits"][team], names)
                    body.append(
                        _line(f"{team_label(team)}: {chosen}", team_color(snap, team))
                    )
        elif mode == "everyone_random":
            body.append(
                _line("Everybody gets their own random kit when the match starts.")
            )
        elif mode == "random_shared":
            body.append(
                _line("One random kit is picked for everyone when the match starts.")
            )
        if snap["reroll_on_respawn"]:
            body.append(_line("You get a new random kit every time you respawn."))
        body.append(_line("The host can change how kits are chosen.", "gray"))
    return _screen("Kits", body, buttons=buttons, exit=_back(), columns=2)


def _info(snap) -> dict[str, Any]:
    mp = snap["maps"].get(snap["map"]) or {}
    teams = mp.get("teams") or []
    kits_note = KIT_MODE_LABELS.get(snap["kit_mode"], snap["kit_mode"])
    body = [
        _line(
            f"Map: {map_label(snap['map'])} ({len(teams)} team"
            f"{'' if len(teams) == 1 else 's'}, up to {mp.get('per_team', 0)} each)"
        ),
        _line(f"Mode: {mode_label(snap['mode'])}"),
        _line(f"Lives: {lives_label(snap['respawn_mode'], snap['respawn_count'])}"),
        _line("Mobs: Mob waves on" if snap["hazards_enabled"] else "Mobs: No mobs"),
        _line(f"Kits: {kits_note}"),
        _line(
            "Balance: Uneven teams are allowed"
            if snap["allow_uneven"]
            else "Balance: Teams must be even"
        ),
        _line(f"Host: {snap['host'] or 'nobody yet'}", "gold"),
    ]
    for team, info in snap["teams"].items():
        members = _members(info)
        if members:
            body.append(
                _line(
                    f"{team_label(team)}: {', '.join(members)}", team_color(snap, team)
                )
            )
    if snap["spectators"]:
        body.append(_line(f"Watching: {', '.join(snap['spectators'])}", "aqua"))
    return _screen("Game Info", body, exit=_back(), columns=1)


def _player_line(entry: Any, name: str | None = None) -> str | None:
    if not isinstance(entry, Mapping):
        return None
    who = name or entry.get("name")
    if not who:
        return None
    bits = []
    kills = entry.get("player_kills", entry.get("kills"))
    if isinstance(kills, int) and not isinstance(kills, bool):
        bits.append(f"{kills} kill{'' if kills == 1 else 's'}")
    deaths = entry.get("deaths")
    if isinstance(deaths, int) and not isinstance(deaths, bool):
        bits.append(f"{deaths} death{'' if deaths == 1 else 's'}")
    return f"{who}: {', '.join(bits)}" if bits else None


def _recap(snap, player) -> dict[str, Any]:
    recap = snap.get("last_recap")
    kind, _ = seat_of(snap, player)
    if recap is None:
        body = [_line("No match has finished yet.")]
    else:
        body = [_line(result_text(snap) or "Match over", "gold", True)]
        length = duration_text(recap.get("seconds"))
        if length:
            body.append(_line(f"Length: {length}", "gray"))
        players = recap.get("players")
        rows: list[str | None] = []
        if isinstance(players, Mapping):
            rows = [_player_line(v, str(k)) for k, v in players.items()]
        elif isinstance(players, list):
            rows = [_player_line(v) for v in players]
        body.extend(_line(r) for r in [r for r in rows if r][:6])
        summary = (recap.get("kit_assignment") or {}).get("recap_summary")
        if isinstance(summary, str) and summary:
            body.append(_line(f"Kits: {summary}", "gray"))
    b = []
    if kind == "team":
        b = [
            _btn("Play Again", action=_act("play_again", then="home"), color="green"),
            _btn("Switch Team", go="teams"),
            _btn("Watch", action=_act("watch", then="home")),
        ]
    else:
        b = [_btn("Join a Team", go="teams")]
    return _screen("Match Results", body, buttons=b, exit=_back(), columns=2)


def _lives_options(snap) -> list[tuple[str, str]]:
    cur = lives_id(snap["respawn_mode"], snap["respawn_count"])
    ids = list(STANDARD_LIVES)
    if cur not in ids:
        ids.append(cur)
    return [(i, lives_option_label(i)) for i in ids]


def _settings(snap, kits: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    names = _kit_names(kits)
    modes = list(
        (snap["maps"].get(snap["map"]) or {}).get("compatible_modes")
        or snap["compatible_modes"]
    )
    if snap["mode"] not in modes:
        modes.append(snap["mode"])
    kit_options = [(str(k["id"]), str(k["name"])) for k in kits]
    kit_options.append((RANDOM, "Random kit"))
    current = str(snap["kit"]).strip().lower()
    if current == RANDOM or current in names:
        initial_kit = current
    else:
        kit_options.append((CURRENT, "Standard loadout"))
        initial_kit = CURRENT
    inputs = [
        _dropdown(
            "mode", "Game mode", [(m, mode_label(m)) for m in modes], snap["mode"]
        ),
        _dropdown(
            "lives",
            "Lives",
            _lives_options(snap),
            lives_id(snap["respawn_mode"], snap["respawn_count"]),
        ),
        _dropdown(
            "kit_mode",
            "How kits are chosen",
            list(KIT_MODE_LABELS.items()),
            snap["kit_mode"] if snap["kit_mode"] in KIT_MODE_LABELS else "uniform",
        ),
        _dropdown("kit", "Kit for everyone", kit_options, initial_kit),
        _toggle("reroll", "New random kit on each respawn", snap["reroll_on_respawn"]),
        _toggle("waves", "Mob waves", snap["hazards_enabled"]),
        _toggle("uneven", "Allow uneven teams", snap["allow_uneven"]),
    ]
    return _screen(
        "Game Settings",
        [_line("Saving changes makes everyone ready up again.", "gray")],
        inputs=inputs,
        buttons=[
            _btn(
                "Save Settings",
                submit=_act("settings", then="home"),
                color="green",
            )
        ],
        exit=_back(),
        columns=1,
    )


def _maps(snap) -> dict[str, Any]:
    buttons = []
    for name, info in snap["maps"].items():
        teams = info.get("teams") or []
        modes = ", ".join(mode_label(m) for m in info.get("compatible_modes") or [])
        tip = (
            f"{len(teams)} team{'' if len(teams) == 1 else 's'} · up to "
            f"{info.get('per_team', 0)} each · {modes}"
        )
        mark = "✔ " if name == snap["map"] else ""
        buttons.append(
            _btn(
                f"{mark}{map_label(name)}",
                action=_act("map", map=name, then="home"),
                tip=tip,
                color="gold" if mark else None,
            )
        )
    return _screen(
        "Choose Map",
        [_line("Changing the map makes everyone ready up again.", "gray")],
        buttons=buttons,
        exit=_back(),
        columns=2,
    )


def _bot_screens(snap) -> dict[str, dict[str, Any]]:
    teams = _map_teams(snap)
    bots = list(snap["bots"].values())[:MAX_BOTS]
    team_options = [(t, team_label(t)) for t in teams]
    out: dict[str, dict[str, Any]] = {}
    buttons = [_btn("Add a Bot", go="bot_add", color="green", tip="A computer player")]
    for n, bot in enumerate(bots, 1):
        sid = f"bot_{n}"
        buttons.append(
            _btn(
                f"{bot['name']} ({team_label(bot['team'])})",
                go=sid,
                color=team_color(snap, bot["team"]),
                tip=_status_words(bot),
            )
        )
        options = list(team_options)
        if bot["team"] not in teams:
            options.append((bot["team"], team_label(bot["team"])))
        body = [
            _line(f"Team: {team_label(bot['team'])}"),
            _line(
                "This bot could not connect."
                if bot["status"] == "failed"
                else f"Status: {_status_words(bot)}",
                "red" if bot["status"] == "failed" else "gray",
            ),
        ]
        if bot["status"] == "failed" and bot.get("error"):
            body.append(_line(str(bot["error"]), "gray"))
        actions = [
            _btn(
                "Rename Bot", submit=_act("bot_rename", bot=bot["bot_id"], then="bots")
            ),
            _btn(
                "Move to Team", submit=_act("bot_move", bot=bot["bot_id"], then="bots")
            ),
        ]
        if bot["status"] == "failed":
            actions.append(
                _btn("Retry", action=_act("bot_retry", bot=bot["bot_id"], then="bots"))
            )
        actions.append(
            _btn(
                "Remove Bot",
                action=_act("bot_remove", bot=bot["bot_id"], then="bots"),
                color="red",
            )
        )
        out[sid] = _screen(
            bot["name"],
            body,
            inputs=[
                _text_input("name", "Name", bot["name"]),
                _dropdown("team", "Team", options, bot["team"]),
            ],
            buttons=actions,
            exit=_back("bots"),
            columns=2,
        )
    out["bots"] = _screen(
        "Bots",
        [
            _line(
                "Click a bot to change it."
                if bots
                else "No bots yet. Bots fill empty spots on a team."
            )
        ],
        buttons=buttons,
        exit=_back(),
        columns=1,
    )
    out["bot_add"] = _screen(
        "Add a Bot",
        inputs=[
            _dropdown(
                "team",
                "Team",
                [(AUTO, "Any team with room"), *team_options],
                AUTO,
            ),
            _text_input("name", "Name (leave empty for a default)"),
        ],
        buttons=[_btn("Add Bot", submit=_act("bot_add", then="bots"), color="green")],
        exit=_back("bots"),
        columns=1,
    )
    return out


def _mobs_allowed(snap) -> bool:
    mp = snap["maps"].get(snap["map"]) or {}
    return snap["mode"] == "teams" and int(mp.get("max_mobs") or 0) > 0


def _mobs(snap) -> dict[str, Any]:
    mp = snap["maps"].get(snap["map"]) or {}
    groups = snap["mob_groups"]
    used = sum(int(g["count"]) for g in groups)
    room = int(mp.get("max_mobs") or 0) - used
    body = [_line(f"{_mob_text(g)} ({team_label(g['team'])})") for g in groups[:8]] or [
        _line("No allied mobs yet.")
    ]
    buttons = [
        _btn(
            f"Remove {_mob_text(g)} ({team_label(g['team'])})",
            action=_act("mob_remove", group_id=g["id"], then="mobs"),
            color="red",
        )
        for g in groups[:10]
    ]
    inputs: list[dict[str, Any]] = []
    if room >= 2:
        teams = _map_teams(snap)
        inputs = [
            _dropdown("team", "Team", [(t, team_label(t)) for t in teams], teams[0]),
            _dropdown("mob", "Mob", list(MOBS), MOBS[0][0]),
            {
                "key": "count",
                "kind": "slider",
                "label": "How many",
                "min": 1,
                "max": min(room, 20),
                "step": 1,
                "initial": 1,
            },
        ]
        buttons.insert(
            0, _btn("Add Mobs", submit=_act("mob_add", then="mobs"), color="green")
        )
    else:
        body.append(_line("The mob limit for this map is reached.", "gray"))
    return _screen(
        "Allied Mobs", body, inputs=inputs, buttons=buttons, exit=_back(), columns=1
    )


def _host(snap, player) -> dict[str, Any]:
    buttons = [
        _btn(name, action=_act("host", target=name, then="home"))
        for name in _others(snap, player)[:MAX_BUTTONS]
    ]
    return _screen(
        "Give Host",
        [_line("Pick who runs the game from now on.")],
        buttons=buttons,
        exit=_back(),
        columns=2,
    )


def menu_for(
    snapshot: Mapping[str, Any],
    player: str,
    *,
    kits: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The game menu for ``player``: ``{"home": "home", "screens": {...}}``.

    ``kits`` are the kit library's kits (``id``, ``name``, ``items``).
    Host-only screens exist only in the host's menu."""
    snap = snapshot
    names = _kit_names(kits)
    host = _ci(snap["host"], player)
    screens: dict[str, dict[str, Any]] = {
        "home": _home(snap, player, host=host, kits=names),
        "teams": _teams(snap, player),
        "kits": _kits(snap, player, kits),
        "info": _info(snap),
        "recap": _recap(snap, player),
    }
    if host:
        screens["settings"] = _settings(snap, kits)
        screens["maps"] = _maps(snap)
        screens.update(_bot_screens(snap))
        screens["mobs"] = _mobs(snap)
        if _others(snap, player):
            screens["host"] = _host(snap, player)
    return {"home": "home", "screens": screens}


# --- signs and the floating board -----------------------------------------------


def _sign(*lines: tuple[str, str | None, bool]) -> dict[str, Any]:
    out = []
    for text, color, bold in lines[:SIGN_LINES]:
        out.append(_line(text, color, bold, SIGN_CHARS))
    return {"lines": out}


def _join_sign(snap, team: str) -> dict[str, Any]:
    title = (f"{team.upper()} TEAM", team, True)
    mp = snap["maps"].get(snap["map"]) or {}
    if team not in (mp.get("teams") or []):
        return _sign(title, ("Not on", "gray", False), ("this map", "gray", False))
    info = snap["teams"].get(team) or {"player_count": 0}
    cap = mp.get("per_team", 0)
    count = info.get("player_count", 0)
    counts = (f"{count}/{cap} players", "white", False)
    if snap["phase"] in ("countdown", "running"):
        return _sign(title, counts, ("Locked", "gray", False))
    if cap and count >= cap:
        return _sign(title, ("FULL", "red", True), counts)
    return _sign(title, counts, ("Click to join", "green", False))


def _ready_sign(snap) -> dict[str, Any]:
    phase = snap["phase"]
    if phase == "countdown":
        n = countdown_seconds(snap) or 0
        return _sign(
            ("STARTING", "green", True),
            (f"in {seconds_text(n)}", "white", False),
            ("Click to cancel", "gray", False),
        )
    if phase == "running":
        return _sign(("MATCH", "gold", True), ("in progress", "white", False))
    if phase == "recap":
        return _sign(
            ("READY", "green", True),
            ("Match over", "white", False),
            ("Play again?", "gray", False),
        )
    humans = _humans(snap)
    if not humans:
        return _sign(
            ("READY", "green", True),
            ("Nobody has", "white", False),
            ("joined yet", "white", False),
        )
    ready = len(_ready_names(snap) & {h.lower() for h in humans})
    return _sign(
        ("READY", "green", True),
        (f"{ready}/{len(humans)} ready", "white", False),
        ("Click to toggle", "gray", False),
    )


def sign_lines(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The text of every lobby sign, by station id."""
    signs = {f"join_{team}": _join_sign(snapshot, team) for team in STATION_TEAMS}
    signs["ready"] = _ready_sign(snapshot)
    signs["help"] = _sign(
        ("GAME MENU", "gold", True),
        ("Right-click the", "white", False),
        ("compass or", "white", False),
        ("press G", "white", False),
    )
    return signs


def display_lines(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The floating text above the spawn: map, rules, who it waits for, countdown."""
    snap = snapshot
    lines = [
        _line(map_label(snap["map"]).upper(), "gold", True, DISPLAY_CHARS),
        _line(
            f"{mode_label(snap['mode'])} · {rules_text(snap)}",
            "white",
            False,
            DISPLAY_CHARS,
        ),
    ]
    if snap["host"]:
        lines.append(_line(f"Host: {snap['host']}", "gray", False, DISPLAY_CHARS))
    phase = snap["phase"]
    if phase == "recap":
        lines.append(
            _line(result_text(snap) or "Match over", "aqua", True, DISPLAY_CHARS)
        )
    else:
        for text, color in _status_lines(snap):
            lines.append(_line(text, color, phase == "countdown", DISPLAY_CHARS))
    lines.append(
        _line(
            "Right-click the compass or press G for the game menu",
            "gray",
            False,
            DISPLAY_CHARS,
        )
    )
    return lines[:DISPLAY_LINES]


# --- the publisher --------------------------------------------------------------


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class LobbyPublisher:
    """Sends what the lobby's players see to the mod, only what changed.

    ``send(**fields)`` is ``ArenaClient.lobby_view``; ``players()`` names who is online
    (None when that is unknown); ``board(name, title, rows)`` is the sidebar's
    ``arena_board``; ``skip()`` names more bots to leave out (the lobby's own bots are
    known from its snapshot).  A failed send is logged and swallowed, and what it
    carried is sent again on the next tick: nothing is marked as sent until it was."""

    def __init__(
        self,
        send: Callable[..., Any],
        players: Callable[[], Sequence[str] | None],
        lobby: Any,
        *,
        board: Callable[[str, str, list[dict]], Any] | None = None,
        kits: Callable[[], Sequence[Mapping[str, Any]]] | None = None,
        skip: Callable[[], Iterable[str]] | None = None,
        name: str = "lobby",
    ):
        self._send, self._players, self.lobby = send, players, lobby
        self._board, self._skip, self._name = board, skip, name
        self._kits = (
            kits if kits is not None else getattr(lobby, "kit_list", lambda: ())
        )
        self._lock = threading.RLock()
        self._menus: dict[str, str] = {}  # lower-cased name -> menu as last sent
        self._signs: dict[str, str] = {}
        self._display: str | None = None
        self._board_sent: str | None = None
        self._pending: dict[str, Any] = {"close": [], "announce": [], "show": {}}
        self._dirty = False
        self._last_key: Any = None
        self._prev_phase: str | None = None
        self._prev_revision: int | None = None
        self._prev_ready: set[str] = set()

    # -- who is here ------------------------------------------------------------

    def _online(self, snap: Mapping[str, Any]) -> list[str] | None:
        try:
            names = self._players()
        except Exception:  # noqa: BLE001 - the bridge may be down
            log.debug("lobby publisher: could not list players", exc_info=True)
            return None
        if names is None:
            return None
        bots = {b["name"].lower() for b in snap["bots"].values()}
        try:
            bots |= {str(n).lower() for n in (self._skip() if self._skip else ())}
        except Exception:  # noqa: BLE001
            log.debug("lobby publisher: could not list bots", exc_info=True)
        seen: set[str] = set()
        out = []
        for n in names:
            if isinstance(n, str) and n and n.lower() not in bots | seen:
                seen.add(n.lower())
                out.append(n)
        return out

    # -- publishing -------------------------------------------------------------

    def publish(
        self,
        snap: Mapping[str, Any] | None = None,
        *,
        show: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> bool:
        """Send what changed (always with ``show``). False when a send failed."""
        with self._lock:
            return self._publish(snap, show)

    def _publish(self, snap, show) -> bool:
        try:
            snap = snap if snap is not None else self.lobby.snapshot()
            return self._push(snap, show)
        except Exception:  # noqa: BLE001 - the lobby must never be blocked by its screens
            log.warning("lobby publisher failed", exc_info=True)
            self._dirty = True
            return False

    def _push(self, snap, show) -> bool:
        names = self._online(snap)
        kits = list(self._kits())
        wanted = list(names or [])
        merged_show = {**self._pending["show"], **(show or {})}
        have = {n.lower() for n in wanted}
        wanted += [n for n in merged_show if n.lower() not in have]
        menus: dict[str, Any] = {}
        menu_keys: dict[str, str] = {}
        for who in wanted:
            menu = menu_for(snap, who, kits=kits)
            key = _dump(menu)
            if self._menus.get(who.lower()) != key:
                menus[who] = menu
                menu_keys[who.lower()] = key
        signs, sign_keys = {}, {}
        for sid, sign in sign_lines(snap).items():
            key = _dump(sign)
            if self._signs.get(sid) != key:
                signs[sid], sign_keys[sid] = sign, key
        display = display_lines(snap)
        display_key = _dump(display)
        fields: dict[str, Any] = {}
        if menus:
            fields["menus"] = menus
        if signs:
            fields["signs"] = signs
        if display_key != self._display:
            fields["display"] = display
        if merged_show:
            fields["show"] = dict(merged_show)
        if self._pending["close"]:
            fields["close"] = list(self._pending["close"])
        if self._pending["announce"]:
            fields["announce"] = list(self._pending["announce"])
        ok = True
        if fields:
            try:
                self._send(**fields)
            except Exception:  # noqa: BLE001
                log.warning("lobby view not sent; will retry", exc_info=True)
                ok = False
            else:
                self._menus.update(menu_keys)
                self._signs.update(sign_keys)
                if "display" in fields:
                    self._display = display_key
                self._pending = {"close": [], "announce": [], "show": {}}
        if names is not None:
            for lower in [k for k in self._menus if k not in have]:
                if lower not in {n.lower() for n in merged_show}:
                    del self._menus[lower]
        ok = self._push_board(snap) and ok
        self._dirty = not ok
        return ok

    def _push_board(self, snap) -> bool:
        if self._board is None:
            return True
        try:
            title, rows = self.lobby.board_rows(snap)
            key = _dump([title, rows])
            if key != self._board_sent:
                self._board(self._name, title, rows)
                self._board_sent = key
        except Exception:  # noqa: BLE001
            log.warning("lobby board not sent; will retry", exc_info=True)
            return False
        return True

    # -- the poll loop's hook ---------------------------------------------------

    def tick(self) -> None:
        """Republish when the lobby or who is online changed; open the recap when a
        match ends, close the menus when one starts, and tell players who must ready
        up again."""
        with self._lock:
            try:
                snap = self.lobby.snapshot()
            except Exception:  # noqa: BLE001
                log.warning("lobby publisher could not read the lobby", exc_info=True)
                return
            names = self._online(snap)
            phase, revision = snap["phase"], snap["revision"]
            ready_now = _ready_names(snap)
            self._collect_events(snap, names, phase, revision, ready_now)
            key = (
                revision,
                phase,
                countdown_seconds(snap),
                None if names is None else tuple(sorted(n.lower() for n in names)),
                tuple(sorted(ready_now)),
                tuple(
                    sorted((b["bot_id"], b["status"]) for b in snap["bots"].values())
                ),
                snap["host"],
                tuple(snap["spectators"]),
            )
            self._prev_phase, self._prev_revision = phase, revision
            self._prev_ready = ready_now
            pending = any(self._pending.values())
            if key != self._last_key or pending or self._dirty:
                self._last_key = key
                self._publish(snap, None)

    def _collect_events(self, snap, names, phase, revision, ready_now) -> None:
        prev = self._prev_phase
        if prev is None:  # the first look is not an event
            return
        online = None if names is None else {n.lower() for n in names}
        if phase == "recap" and prev != "recap":
            who = _humans(snap) + list(snap["spectators"])
            self._pending["show"].update(
                {
                    n: {"screen": "recap"}
                    for n in who
                    if online is None or n.lower() in online
                }
            )
        if phase == "running" and prev != "running":
            for n in names or []:
                if n not in self._pending["close"]:
                    self._pending["close"].append(n)
        if (
            revision != self._prev_revision
            and phase == "open"
            and prev != "running"
            and self._prev_ready
        ):
            lost = [
                h
                for h in _humans(snap)
                if h.lower() in self._prev_ready and h.lower() not in ready_now
            ]
            if lost:
                reason = str(snap["last_change_reason"]).strip()
                reason = (
                    reason.removesuffix("Ready up again.").strip()
                    or "The lobby changed."
                )
                self._pending["announce"].append(
                    {
                        "players": lost,
                        "title": cut("Ready up again", TITLE_CHARS),
                        "subtitle": cut(reason, SUBTITLE_CHARS),
                    }
                )
