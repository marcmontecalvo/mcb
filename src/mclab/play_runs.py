"""Play and arena server runs in the lab dashboard: arena matches, arena tools and
combat trials.

The dashboard's lab runs drive the lab server; these drive the two game servers
people play on, through the same scripts and CLI an operator uses
(``scripts/arena-hunt.py``, ``scripts/combat-trial.py``, ``mcarena arena``), each
in a child process bound to its instance:

* the ARENA server (``config/instances/arena.json``): an empty void world. A match
  builds the lobby and its arena there when they are missing, makes the lobby
  everyone's spawn and sends the players back to it when the match ends, so the
  people who join need no operator rights.
* the PLAY server: the survival world. Combat trials fight there, next to the bot.

What the dashboard offers is read from the sources, not listed here: the arenas are
the plan files in ``arenas/`` (their teams and team sizes too), the wave presets
``arenas/waves/``, the weapons and mobs whatever ``combat_trial`` accepts. A new
arena, wave preset or weapon shows up without a change here; a new kind of run is
one ``PlayRun`` in ``RUNS`` (and its command). Docs: docs/LAB_UI.md "Arenas and
combat", docs/ARENA_RUNTIME.md "The arena server".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from mclab import arena_roster
from mclab.arena.plan import TEAM_NAMES
from mclab.paths import ROOT

ARENAS = ROOT / "arenas"
WAVES = ARENAS / "waves"
SCRIPTS = ROOT / "scripts"
RESULTS = ROOT / "results"
SECTION = "Arenas and combat"
TOOLS_SECTION = "Arena tools"  # advanced: build, stop and remove by hand
LOBBY = "lobby"  # the arena server's spawn (mclab.arena.world): never a match arena
COORDINATE_LIMIT = 30_000_000
WAVE_DELAY = 60  # ticks before each custom wave (arena-hunt.py --delay)
SERVER_WAIT_S = 300.0  # a cold server and its bot are ready within this
DEFAULT_KIT = "sword+axe+spear+bow+trident+shield+armor+food+potions"
DEFAULT_WEAPONS = "sword,axe,spear,bow"
DEFAULT_MOBS = "zombie,skeleton,creeper,pig"
DEFAULT_BOT = "MCBot"
_PLAYER = re.compile(r"[A-Za-z0-9_]{1,16}")
MAX_PLAYERS = 16  # 8v8
WHERE_TTL_S = 10.0  # the arena list is asked of the arena server at most this often
ONLINE_TIMEOUT_S = 2.0  # a stopped arena server must not hang the wizard

# The game servers, and which one each family of run drives.
SERVERS = ("play", "arena")
FAMILY_SERVER = {"arena": "arena", "combat": "play"}

# run.<field> -> family; every one is None/False by default (run_config.RunSettings)
MATERIALS = ("weapon_material", "armor_material")
FIELDS = {
    "arena": (
        "arena", "arena_action", "game", "waves", "custom_waves", "roster", "kit",
        *MATERIALS, "corner", "restore", "count", "timeout",
    ),
    "combat": ("weapons", "mobs", "spot", *MATERIALS, "count", "timeout"),
}  # fmt: skip
FAMILIES = tuple(FIELDS)
POSITIONS = ("corner", "spot")
SHARED = ("count", "timeout")  # run fields other families use too
# game -> (label, help): arena-hunt.py --mode
GAMES = {
    "waves": (
        "PvE",
        "Everyone on one side against the mob waves; the match is won when the "
        "last wave is cleared.",
    ),
    "teams": (
        "PvP",
        "Team against team, bot and people alike; the last team standing wins. "
        "Mob waves are optional and fight everyone.",
    ),
    "race": (
        "Kill race",
        "Teams race to kill the most wave mobs; the scoreboard counts each "
        "team's kills.",
    ),
}
SIDED = ("teams", "race")  # games played on teams
WAVE_GAMES = ("waves", "race")  # games that need mob waves
ACTIONS = {
    "match": "Play a match: the arena server builds the arena when it is missing.",
    "stop": "End the arena's match and discard its mobs.",
    "build": "Build the arena at a corner on the arena server.",
    "remove": "Unregister the arena (Restore clears it to air).",
}
# label, help: shown by the dashboard next to the field
HELP = {
    "arena": (
        "Arena",
        "Which arena. The arena server builds it the first time it is played.",
    ),
    "arena_action": ("Arena action", "match, stop, build or remove."),
    "game": ("Game", "How the match is won."),
    "waves": ("Waves", "Which mobs come, and how many waves of them."),
    "custom_waves": (
        "Your waves",
        "Each wave is the mobs that come at once; the next wave comes 3 seconds "
        "after the last one is cleared.",
    ),
    "roster": (
        "Players",
        "The bot, and everyone else in the match: add your own Minecraft name to "
        "join in. Each person joins the arena server and types /mcb ready; the "
        "match waits for them. Op makes a person an operator on the arena server.",
    ),
    "kit": (
        "Kit",
        "What every player is handed at the start of each match.",
    ),
    "weapon_material": (
        "Weapon material",
        "What the sword, axe and spear are made of.",
    ),
    "armor_material": ("Armor material", "What the armor is made of."),
    "corner": (
        "Corner",
        "Where the arena's lowest north-west corner goes (X Y Z on the arena server).",
    ),
    "restore": ("Clear the blocks", "Also turn the arena's blocks back into air."),
    "weapons": (
        "Weapons",
        "Each weapon ticked is fought with on its own, against each mob.",
    ),
    "mobs": (
        "Mobs",
        "Each mob ticked is fought on its own, with each weapon.",
    ),
    "spot": (
        "Fight spot",
        "X Y Z on the play server where every fight happens. Blank: where the bot "
        "stands now.",
    ),
}
# Per kind of run: run.count and run.timeout mean something different to each.
BY_TEST = {
    "count": {
        "arena": {
            "label": "Matches",
            "help": "Matches in a row, each from full health.",
        },
        "combat": {
            "label": "Rounds",
            "help": "Times round every weapon against every mob.",
        },
    },
    "timeout": {
        "arena": {
            "label": "Time limit per match (seconds)",
            "help": "A match not won by then counts as lost.",
            "placeholder": "600",
        },
        "combat": {
            "label": "Time limit per fight (seconds)",
            "help": "A fight not over by then counts as a timeout.",
            "placeholder": "90",
        },
    },
}  # fmt: skip


@dataclass(frozen=True)
class PlayRun:
    """One dashboard row: the fields it uses, the ones shown up front, defaults,
    the server it drives, the catalog section it sits in and, for an arena row,
    its arena action."""

    id: str
    family: str
    label: str
    description: str
    uses: tuple[str, ...]
    basic: tuple[str, ...]
    defaults: dict = field(default_factory=dict)
    section: str = SECTION
    action: str | None = None


def _slot(name: str, team: str | None = None, op: bool = False) -> dict:
    """A roster entry (run_config.RosterEntry)."""
    return {"name": name, "team": team, "op": op}


RUNS = (
    PlayRun(
        "arena", "arena", "Arena",
        "A match on the arena server: PvE against mob waves, PvP team against team, "
        "or a kill race. The bot and people play together; wins, kills, time and "
        "damage taken are recorded.",
        ("arena", "game", "roster", "waves", "custom_waves", "kit", *MATERIALS,
         "count", "timeout"),
        ("arena", "game", "roster", "waves", "count"),
        {"game": "waves", "roster": [_slot(DEFAULT_BOT)], "kit": DEFAULT_KIT},
        action="match",
    ),
    PlayRun(
        "arena:stop", "arena", "Stop an arena's match", ACTIONS["stop"],
        ("arena",), ("arena",), section=TOOLS_SECTION, action="stop",
    ),
    PlayRun(
        "arena:build", "arena", "Build an arena", ACTIONS["build"],
        ("arena", "corner"), ("arena", "corner"), section=TOOLS_SECTION,
        action="build",
    ),
    PlayRun(
        "arena:remove", "arena", "Remove an arena", ACTIONS["remove"],
        ("arena", "restore"), ("arena", "restore"), section=TOOLS_SECTION,
        action="remove",
    ),
    PlayRun(
        "combat", "combat", "Combat trials",
        "Each weapon against each mob, one fight at a time on the play server, "
        "measured (kill rate, time to kill, health left).",
        ("weapons", "mobs", "spot", *MATERIALS, "count", "timeout"),
        ("weapons", "mobs", "count"),
        {"weapons": DEFAULT_WEAPONS, "mobs": DEFAULT_MOBS},
    ),
)  # fmt: skip


def server_for(family: str) -> str | None:
    """The game server a family of run drives (None: the lab's own runs)."""
    return FAMILY_SERVER.get(family)


# --- choices, read from the files ---------------------------------------------------


def _stems(directory: Path) -> list[str]:
    return (
        sorted(p.stem for p in directory.glob("*.json")) if directory.is_dir() else []
    )


def arena_names() -> list[str]:
    """Every arena plan (``arenas/<name>.json``) but the arena server's lobby."""
    return [name for name in _stems(ARENAS) if name != LOBBY]


def wave_presets() -> list[str]:
    """Every wave preset (``arenas/waves/<name>.json``)."""
    return _stems(WAVES)


def _first(names: list[str], preferred: str) -> str | None:
    return preferred if preferred in names else (names[0] if names else None)


_plans: dict = {}  # path -> (mtime, plan head): plan files run to 150 KB


def _plan_head(path: Path) -> dict:
    """What the dashboard needs of a plan: its size, capacity, description and
    team names (in the plan's order)."""
    mtime = path.stat().st_mtime
    cached = _plans.get(path)
    if cached is None or cached[0] != mtime:
        try:
            data = json.loads(path.read_text("utf-8"))
        except ValueError:
            data = {}
        head = {k: data.get(k) for k in ("size", "capacity", "description")}
        teams = ((data.get("markers") or {}).get("teams")) or {}
        head["teams"] = list(teams) if isinstance(teams, dict) else []
        cached = _plans[path] = (mtime, head)
    return cached[1]


def arena_meta(name: str) -> dict:
    """An arena's teams, how many fit on a team, and how many players in all."""
    head = _plan_head(ARENAS / f"{name}.json")
    per_team = (head.get("capacity") or {}).get("per_team")
    teams = head["teams"]
    return {
        "teams": teams,
        "per_team": per_team,
        "players": per_team * len(teams) if isinstance(per_team, int) else None,
    }


def arena_help() -> dict[str, str]:
    """A line per arena: its design's description, size and mob limit."""
    from mclab.arena import designs

    out = {}
    for name in arena_names():
        head = _plan_head(ARENAS / f"{name}.json")
        design = designs.DESIGNS.get(name)
        doc = (design.__doc__ or "") if design else ""
        text = head.get("description") or " ".join(doc.strip().split("\n\n")[0].split())
        size, mobs = head.get("size"), (head.get("capacity") or {}).get("mobs")
        if isinstance(size, list) and len(size) == 3:
            text += f" {size[0]} × {size[2]} blocks"
            text += f", up to {mobs} mobs at once." if mobs else "."
        out[name] = text.strip() or name
    return out


def wave_help() -> dict[str, str]:
    """The description of each wave preset that has one."""
    out = {}
    for name in wave_presets():
        try:
            data = json.loads((WAVES / f"{name}.json").read_text("utf-8"))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("description"):
            out[name] = data["description"]
    return out


def _words(name: str) -> str:
    return name.replace("_", " ").capitalize()


def kit_choices() -> dict:
    """The kit as checkboxes: ``combat_trial.KIT_PARTS`` in its groups, or keep
    what each player carries instead."""
    from mclab import combat_trial as ct

    options = [
        {"value": part, "label": label, "group": group, "help": text}
        for part, (label, group, text) in ct.KIT_PARTS.items()
    ]
    options.append(
        {
            "value": "keep",
            "label": "Keep what they carry",
            "group": "Instead",
            "help": "Hand out nothing: everyone fights with whatever they have.",
        }
    )
    return _choices(options, "+", empty="fist", exclusive=["keep"])


def _choices(
    options: list[dict],
    separator: str,
    *,
    empty: str | None = None,
    exclusive: list[str] | None = None,
    other: str | None = None,
) -> dict:
    """A checkbox field (``format: "choices"``): the options, how ticked ones are
    joined, the value when none is ticked, options that stand alone, and the
    prompt of a box for names off the list (None: no box)."""
    return {
        "options": options,
        "separator": separator,
        "empty": empty,
        "exclusive": exclusive or [],
        "other": other,
    }


def weapon_choices() -> dict:
    """Combat-trial weapons: each ticked one is a trial of its own."""
    from mclab import combat_trial as ct

    options = [
        {"value": part, "label": label, "group": None, "help": text}
        for part, (label, group, text) in ct.KIT_PARTS.items()
        if group == "Weapons"
    ]
    fist = {"value": "fist", "label": "Bare hands", "group": None, "help": None}
    options.append({**fist, "help": "No weapon at all."})
    return _choices(
        options,
        ",",
        other="Or type kits of their own, joined by +: spear+sword, sword+shield+armor",
    )


def mob_choices() -> dict:
    from mclab import combat_trial as ct

    options = [
        {"value": mob, "label": _words(mob), "group": group, "help": None}
        for mob, group in ct.MOBS.items()
    ]
    return _choices(
        options,
        ",",
        other="Or type a group fought at once: zombie*3, zombie*2+skeleton, "
        "or any other mob's name",
    )


def field_families() -> dict[str, list[str]]:
    """run.<field> -> the play families that use it (count and timeout are shared
    with other families, so run_config lists those)."""
    out: dict[str, list[str]] = {}
    for family, names in FIELDS.items():
        for name in names:
            if name not in SHARED:
                out.setdefault(name, []).append(family)
    return out


def decorate_schema(run: dict) -> None:
    """Fill in the play fields of the run schema (``config_schema``'s
    ``properties.run.properties``): choices read from the files and the catalogs,
    each arena's teams and team size, labels and help, and per-family text for the
    shared fields. Built each time the dashboard asks, so a new arena, preset or
    kit part shows up at once."""
    from mclab import combat_trial as ct

    names = arena_names()
    run["arena"]["enum"] = names
    run["arena"]["enum_help"] = arena_help()
    run["arena"]["enum_meta"] = {name: arena_meta(name) for name in names}
    run["waves"]["enum"] = wave_presets()
    run["waves"]["enum_help"] = wave_help()
    run["waves"]["custom"] = {"field": "custom_waves", "label": "Make my own waves"}
    run["waves"]["optional"] = {
        "field": "game",
        "values": [g for g in GAMES if g not in WAVE_GAMES],
        "label": "No mob waves",
    }
    run["arena_action"]["enum"] = list(ACTIONS)
    run["game"]["enum"] = list(GAMES)
    run["game"]["enum_labels"] = {name: label for name, (label, _) in GAMES.items()}
    run["game"]["enum_help"] = {name: text for name, (_, text) in GAMES.items()}
    run["game"]["sided"] = list(SIDED)
    run["roster"].update(
        format="roster", bot=DEFAULT_BOT, max=MAX_PLAYERS, teams=list(TEAM_NAMES)
    )
    run["custom_waves"]["format"] = "waves"
    run["custom_waves"]["mobs"] = [
        m for m, group in ct.MOBS.items() if group != ct.ANIMALS
    ]
    run["weapon_material"]["enum"] = list(ct.WEAPON_TIERS)
    run["armor_material"]["enum"] = list(ct.ARMOR_TIERS)
    for name in MATERIALS:
        run[name]["placeholder"] = ct.DEFAULT_TIER
    for name, choices in (
        ("kit", kit_choices()),
        ("weapons", weapon_choices()),
        ("mobs", mob_choices()),
    ):
        run[name].update(format="choices", choices=choices)
    for name in POSITIONS:
        run[name]["format"] = "position"
    for name, (label, text) in HELP.items():
        run[name].update(label=label, help=text, description=text)
    for name, by_test in BY_TEST.items():
        run[name].setdefault("by_test", {}).update(by_test)


def catalog_rows() -> list[dict]:
    """The dashboard's rows: overrides on the family defaults, the fields shown up
    front, and every family field the row does not use locked (hidden, and refused
    if changed)."""
    rows = []
    for spec in RUNS:
        run = {**spec.defaults}
        if spec.family == "arena":
            run["arena_action"] = spec.action
            run["arena"] = _first(arena_names(), "vault")
            if "waves" in spec.uses:
                run["waves"] = _first(wave_presets(), "mixed-10")
        unused = [f for f in FIELDS[spec.family] if f not in spec.uses]
        rows.append(
            {
                "id": spec.id,
                "label": spec.label,
                "family": spec.family,
                "description": spec.description,
                "section": spec.section,
                "server": server_for(spec.family),
                "overrides": {"run": run},
                "locked_fields": ["run.test", *(f"run.{f}" for f in unused)],
                "basic_fields": [f"run.{f}" for f in spec.basic],
            }
        )
    return rows


# --- settings -----------------------------------------------------------------------


def _names(text: str | None) -> list[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def custom_waves(text: str | None) -> list[dict]:
    """``pillager*2, pillager+vindicator``: one wave per comma (arena-hunt --wave)."""
    from mclab import arena_hunt
    from mclab.arena import client

    waves = [arena_hunt.parse_wave(part, WAVE_DELAY) for part in _names(text)]
    try:
        client.check_waves(waves)
    except client.ArenaError as error:
        raise ValueError(f"custom waves: {error.detail}") from error
    return waves


def _position(value, name: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(v) is not int or abs(v) > COORDINATE_LIMIT for v in value)
    ):
        raise ValueError(f"run.{name} must be three whole coordinates")


def _check_roster(roster: list[dict] | None) -> None:
    roster = roster or []
    if len(roster) > MAX_PLAYERS:
        raise ValueError(f"a match holds at most {MAX_PLAYERS} players")
    seen = set()
    for entry in roster:
        name = entry["name"]
        if not _PLAYER.fullmatch(name):
            raise ValueError(f"{name!r} is not a Minecraft name")
        if name.lower() in seen:
            raise ValueError(f"{name} is in the match twice")
        seen.add(name.lower())
        if entry["team"] not in (None, *TEAM_NAMES):
            raise ValueError(
                f"{name}'s team {entry['team']!r} is not one of {', '.join(TEAM_NAMES)}"
            )


def check(run: dict) -> None:
    """Refuse bad play-run settings (shape and choices); completeness and what the
    chosen arena can hold are ``require``'s, at launch."""
    from mclab import combat_trial as ct

    family = run["test"]
    gear = _gear(run)
    if run["weapon_material"] not in (None, *ct.WEAPON_TIERS):
        raise ValueError(f"weapon material must be one of {', '.join(ct.WEAPON_TIERS)}")
    if run["armor_material"] not in (None, *ct.ARMOR_TIERS):
        raise ValueError(f"armor material must be one of {', '.join(ct.ARMOR_TIERS)}")
    if family == "arena":
        if run["arena_action"] not in (None, *ACTIONS):
            raise ValueError(f"arena action must be one of {', '.join(ACTIONS)}")
        if run["game"] not in (None, *GAMES):
            raise ValueError(f"game must be one of {', '.join(GAMES)}")
        if run["arena"] is not None and run["arena"] not in arena_names():
            raise ValueError(
                f"unknown arena {run['arena']!r}; plans: {', '.join(arena_names())}"
            )
        if run["waves"] is not None and run["waves"] not in wave_presets():
            raise ValueError(
                f"unknown wave preset {run['waves']!r}; presets: "
                + ", ".join(wave_presets())
            )
        if run["waves"] is not None and run["custom_waves"]:
            raise ValueError("choose a wave preset or custom waves, not both")
        if run["custom_waves"] is not None:
            custom_waves(run["custom_waves"])
        if run["kit"] not in (None, "keep"):
            ct.kit(run["kit"], **gear)
        _check_roster(run["roster"])
        _position(run["corner"], "corner")
    elif family == "combat":
        for name, one in (
            ("weapons", lambda w: ct.kit(w, **gear)),
            ("mobs", lambda m: ct.scene("fist", m)),
        ):
            if run[name] is None:
                continue
            if not _names(run[name]):
                raise ValueError(f"run.{name} is empty")
            for part in _names(run[name]):
                one(part)
        _position(run["spot"], "spot")


def _gear(run: dict) -> dict:
    """The kit's materials as ``combat_trial.kit`` takes them (iron unless chosen;
    the scripts' defaults)."""
    from mclab import combat_trial as ct

    return {
        "tier": run["weapon_material"] or ct.DEFAULT_TIER,
        "armor": run["armor_material"] or ct.DEFAULT_TIER,
    }


def _gear_args(run: dict) -> list[str]:
    argv = []
    if run["weapon_material"] is not None:
        argv += ["--tier", run["weapon_material"]]
    if run["armor_material"] is not None:
        argv += ["--armor", run["armor_material"]]
    return argv


def _game(run: dict) -> str:
    return run["game"] or "waves"


def _roster(run: dict) -> list[dict]:
    """The match's players; none given is the bot alone (arena-hunt's default)."""
    return [_slot(DEFAULT_BOT)] if run["roster"] is None else run["roster"]


def teams_of(roster: list[dict]) -> dict[str, list[str]]:
    """team -> its players, teams in the order they first appear."""
    teams: dict[str, list[str]] = {}
    for entry in roster:
        teams.setdefault(entry["team"], []).append(entry["name"])
    return teams


def _require_match(run: dict) -> None:
    game = _game(run)
    label = GAMES[game][0]
    if game in WAVE_GAMES and run["waves"] is None and not run["custom_waves"]:
        raise ValueError(f"a {label} match needs a wave preset or custom waves")
    roster = _roster(run)
    if not roster:
        raise ValueError("a match needs at least one player")
    meta = arena_meta(run["arena"])
    per_team, arena = meta["per_team"], run["arena"]
    if game not in SIDED:
        if meta["players"] is not None and len(roster) > meta["players"]:
            raise ValueError(
                f"{arena} holds {meta['players']} players; the match has {len(roster)}"
            )
        return
    missing = [e["name"] for e in roster if e["team"] is None]
    if missing:
        raise ValueError(f"every player in a {label} match needs a team: {missing[0]}")
    teams = teams_of(roster)
    if len(teams) < 2:
        raise ValueError(f"a {label} match needs players on at least two teams")
    for team, names in teams.items():
        if team not in meta["teams"]:
            raise ValueError(
                f"{arena} has no {team} team; its teams: {', '.join(meta['teams'])}"
            )
        if per_team is not None and len(names) > per_team:
            raise ValueError(
                f"team {team} has {len(names)} players; {arena} holds {per_team} a team"
            )


def require(run: dict) -> None:
    """What a play run needs before it may start, the roster against the arena."""
    if run["test"] == "arena":
        if run["arena_action"] is None:
            raise ValueError("choose an arena action")
        if run["arena"] is None:
            raise ValueError("choose an arena")
        if run["arena_action"] == "match":
            _require_match(run)
        if run["arena_action"] == "build" and run["corner"] is None:
            raise ValueError("a build needs the arena's corner X Y Z")
    elif not _names(run["weapons"]) or not _names(run["mobs"]):
        raise ValueError("a combat trial needs weapons and mobs")


# --- commands -----------------------------------------------------------------------


def command(run: dict, out: Path) -> list[str]:
    """The script and its arguments for a match series or a combat trial series."""
    if run["test"] == "combat":
        argv = [str(SCRIPTS / "combat-trial.py"), "--weapons", run["weapons"]]
        argv += ["--mobs", run["mobs"], "--repeat", str(run["count"])]
        if run["timeout"] is not None:
            argv += ["--timeout", str(run["timeout"])]
        argv += _gear_args(run) + ["--out", str(out)]
        if run["spot"] is not None:
            # "=": argparse would read a negative coordinate as a flag
            argv.append("--arena=" + ",".join(map(str, run["spot"])))
        return argv
    game = _game(run)
    argv = [str(SCRIPTS / "arena-hunt.py"), "--arena", run["arena"]]
    argv += ["--mode", game, "--world"]
    if run["waves"] is not None:
        argv += ["--waves", run["waves"]]
    for wave in _names(run["custom_waves"]):
        argv += ["--wave", wave]
    roster = _roster(run)
    if game in SIDED:
        for team, names in teams_of(roster).items():
            argv += ["--team", f"{team}={','.join(names)}"]
    elif roster:
        argv += ["--players", *(e["name"] for e in roster)]
    for entry in roster:
        if entry["op"]:
            argv += ["--op", entry["name"]]
    if run["kit"] is not None:
        argv += ["--kit", run["kit"]]
    argv += _gear_args(run) + ["--repeat", str(run["count"])]
    if run["timeout"] is not None:
        argv += ["--timeout", str(run["timeout"])]
    return argv + ["--out", str(out)]


def arena_args(run: dict) -> list[str]:
    """``mcarena arena`` arguments for an arena tool (stop, build, remove)."""
    action, name = run["arena_action"], run["arena"]
    if action == "build":
        return ["build", name, "--at", *map(str, run["corner"]), "--wait"]
    if action == "remove":
        return ["remove", name, *(["--restore"] if run["restore"] else [])]
    if action == "stop":
        return ["stop", name]
    raise ValueError(f"arena action {action!r} is not an arena tool")


def _env(instance: str) -> dict:
    return {
        **os.environ,
        "MCLAB_INSTANCE": instance,  # never the lab
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }


_ARENA_CLI = (
    "import sys\n"
    "from mclab import arena_cli\n"
    "raise SystemExit(arena_cli.main(sys.argv[1:]))\n"
)


def _run_cli(argv: list[str], env: dict) -> tuple[int, str]:
    done = subprocess.run(
        argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )  # fmt: skip
    return done.returncode, done.stdout


def arena_action(run: dict, *, run_cli=_run_cli) -> dict:
    """One ``mcarena arena`` command on the arena server; its JSON answer."""
    argv = [sys.executable, "-u", "-c", _ARENA_CLI, *arena_args(run)]
    code, out = run_cli(argv, _env(server_for("arena")))
    print(out.strip(), flush=True)
    try:
        answer = json.loads(out.strip().splitlines()[-1])
    except (IndexError, ValueError):
        answer = {"ok": False, "reason": "no_answer", "detail": f"exit code {code}"}
    if code == 0 and answer.get("ok"):
        return {"status": "completed_arena", "details": answer}
    return {
        "status": "failed",
        "error": f"{answer.get('reason', 'failed')}: {answer.get('detail', '')}",
    }


def arena_listing(*, run_cli=_run_cli) -> dict:
    """``mcarena arena list``: every arena registered on the arena server."""
    argv = [sys.executable, "-u", "-c", _ARENA_CLI, "list"]
    code, out = run_cli(argv, _env(server_for("arena")))
    if code != 0:
        raise RuntimeError(f"arena list: exit code {code}")
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (IndexError, ValueError) as error:
        raise RuntimeError(f"arena list: no answer ({error})") from error


SERVER_LABELS = {
    "lab": "Lab server: the flat test world the lab missions run in",
    "play": "Play server: the survival world the bot lives in",
    "arena": "Arena server: the lobby and the arenas, floating in an empty world",
}


def servers(root: Path = ROOT, *, lan_ip=None) -> dict:
    """The address to join each server at from this PC, from its instance config,
    and from another PC on the network when the config opens it to the LAN."""
    from mclab import paths

    lan_ip = lan_ip or paths.lan_ip
    out = {}
    for name, label in SERVER_LABELS.items():
        address = lan_address = None
        try:
            config = json.loads(
                paths.instance_paths(name, root=root).config_file.read_text("utf-8")
            )
            host = config.get("server_host") or "127.0.0.1"
            address = f"{host}:{config['game_port']}"
            ip = lan_ip() if config.get("lan") is True else None
            lan_address = f"{ip}:{config['game_port']}" if ip else None
        except (OSError, ValueError, KeyError):
            pass
        out[name] = {"address": address, "lan_address": lan_address, "label": label}
    return out


_where_cache: dict = {}


def where(
    *,
    listing=arena_listing,
    root: Path = ROOT,
    clock=time.monotonic,
    ttl: float = WHERE_TTL_S,
) -> dict:
    """Where to watch: each server's address, whether the arena server's lobby is
    built, and each arena on it (built or not, its corner, its spectator box and
    the command to stand in it). The arena list is asked at most every ``ttl``
    seconds."""
    now = clock()
    cached = _where_cache.get(root)
    if cached is not None and cached[0] > now:
        return cached[1]
    out: dict = {
        "servers": servers(root),
        "arena_server": server_for("arena"),
        "bot": DEFAULT_BOT,
        "lobby": False,
        "arenas": {},
    }
    try:
        answer = listing()
        if not answer.get("ok"):
            raise RuntimeError(f"{answer.get('reason')}: {answer.get('detail', '')}")
        for item in answer.get("arenas") or []:
            built = item.get("state") == "built"
            if item["name"] == LOBBY:
                out["lobby"] = built
                continue
            spot = item.get("spectator")
            out["arenas"][item["name"]] = {
                "built": built,
                "corner": item.get("origin"),
                "spectator": spot,
                "match": item.get("match_state"),
                "teleport": f"/tp @s {spot[0]} {spot[1]} {spot[2]}"
                if built and spot
                else None,
            }
    except (RuntimeError, OSError, KeyError, TypeError) as error:
        out["arenas_error"] = str(error)
    _where_cache[root] = (now + ttl, out)
    return out


# --- who can play: the people online and the saved roster ----------------------------


def _arena_client(root: Path = ROOT):
    """A client for the arena server's body bridge (its own instance, not the
    lab's), with a short timeout."""
    from mclab import paths
    from mclab.arena.client import ArenaClient

    instance = paths.instance_paths(server_for("arena"), root=root)
    settings = json.loads(instance.config_file.read_text("utf-8"))
    client = ArenaClient.from_state(state_file=instance.state, settings=settings)
    client.transport.timeout = ONLINE_TIMEOUT_S
    return client


def online_players(*, client=None, root: Path = ROOT) -> dict:
    """Who is online on the arena server now, for the wizard to offer: ``online``
    (people first, by name; the bot and any other fake player marked ``bot``), the
    ``bot``'s name, and a ``note`` saying why the list is empty when the server
    cannot be asked. Reads the bridge's ``/players`` roster; never an error."""
    from mclab.arena.client import ArenaError

    out: dict = {"online": [], "bot": DEFAULT_BOT, "note": None}
    try:
        players = (client or _arena_client(root)).online().values()
        out["online"] = sorted(
            (
                {
                    "name": p["name"],
                    "bot": p.get("fake") is True
                    or p["name"].lower() == DEFAULT_BOT.lower(),
                }
                for p in players
                if _PLAYER.fullmatch(p["name"])
            ),
            key=lambda p: (p["bot"], p["name"].lower()),
        )
    except ArenaError as error:
        out["note"] = (
            "The arena server is not running. Start it to see who is online."
            if error.reason == "server_not_running"
            else f"Couldn't ask the arena server who is online: {error}"
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        out["note"] = f"Couldn't ask the arena server who is online: {error!r}"
    return out


def roster_state(store=None) -> dict:
    """The saved roster for the wizard: the names (pinned first, then the most
    recently used), the last match's setup, and the bot's name."""
    store = store or arena_roster.RosterStore()
    return {
        "players": store.players(),
        "last_match": store.last_match(),
        "bot": DEFAULT_BOT,
    }


def save_player(request: dict, store=None) -> dict:
    """Save or change a name by hand (``name`` and optionally ``label`` and
    ``pinned``); the roster after. A bad name, label or pin is a ValueError."""
    store = store or arena_roster.RosterStore()
    name = arena_roster.valid_name(request.get("name"))
    if name.lower() == DEFAULT_BOT.lower():
        raise ValueError(f"{DEFAULT_BOT} is the bot, not a player to save")
    store.upsert(name, label=request.get("label"), pinned=request.get("pinned"))
    return roster_state(store)


def forget_player(request: dict, store=None) -> dict:
    """Remove a name from the saved roster; the roster after."""
    store = store or arena_roster.RosterStore()
    store.remove(request.get("name"))
    return roster_state(store)


def remember_match(run: dict, *, store=None) -> None:
    """A match is starting: save its players and its setup as the last match. A
    roster that cannot be saved never stops the match."""
    try:
        (store or arena_roster.RosterStore()).record_match(
            _roster(run),
            arena=run["arena"],
            game=_game(run),
            waves=run["waves"],
            custom_waves=run["custom_waves"],
            bot=DEFAULT_BOT,
        )
    except (OSError, ValueError) as error:
        print(f"could not save the arena roster: {error}", flush=True)


def _spawn(argv: list[str], env: dict):
    flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        if os.name == "nt"
        else 0
    )
    # inherits the worker's stdout (the session's console.log); -u keeps it live
    return subprocess.Popen(
        argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, creationflags=flags
    )


def _stop_child(child) -> None:
    from mclab.dashboard_runs import stop_child

    stop_child(child)


def execute(config: dict, *, spawn=_spawn, run_cli=_run_cli, results=None) -> dict:
    """Run one play run for the dashboard worker on its server. Matches and trials
    write their record and a ``summary.json`` (a history row) to
    ``results/<family>/<stamp>/``; an arena tool answers at once."""
    from mclab.dashboard_runs import _SHIM

    run = config["run"]
    family = run["test"]
    if family == "arena" and run["arena_action"] != "match":
        return arena_action(run, run_cli=run_cli)
    if family == "arena":
        remember_match(run)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder = Path(results or RESULTS) / family / stamp
    folder.mkdir(parents=True)
    argv = command(run, folder)
    # The shim turns the dashboard's Ctrl+Break into KeyboardInterrupt in the script.
    child = spawn([sys.executable, "-u", "-c", _SHIM, *argv], _env(server_for(family)))
    try:
        code = child.wait()
    except KeyboardInterrupt:
        _stop_child(child)
        if family == "arena":  # a match left running: end it, or it plays on
            arena_action({**run, "arena_action": "stop"})
        raise
    records = sorted(p for p in folder.glob("*.json") if p.name != "summary.json")
    record = json.loads(records[-1].read_text("utf-8")) if records else None
    summary = summarize(run, record, run_id=stamp)
    if records:
        summary["source"] = records[-1].relative_to(folder.parent.parent).as_posix()
    (folder / "summary.json").write_text(json.dumps(summary, indent=1), "utf-8")
    result = {
        "status": f"completed_{family}" if code == 0 else "failed",
        "returncode": code,
        "run_id": stamp,
    }
    if code != 0:
        result["error"] = f"{Path(argv[0]).name} exit code {code}"
    return result


def _episode(status: str, seconds) -> dict:
    row = {"status": status, "seconds": seconds}
    if status == "success" and isinstance(seconds, (int, float)):
        row["ticks"] = round(seconds * 20)
    return row


# what a match row says beyond its outcome (arena_hunt.hunt), kept when present
_MATCH_KEYS = ("reason", "winner", "bot_won", "bot_kills", "kills", "team_kills")


def summarize(run: dict, record: dict | None, *, run_id: str) -> dict:
    """A match or trial record as a dashboard history row
    (results/*/*/summary.json)."""
    family = run["test"]
    if family == "arena":
        rows = (record or {}).get("runs") or []
        episodes = [
            {
                **_episode(
                    "success" if r.get("outcome") == "won" else str(r.get("outcome")),
                    r.get("seconds"),
                ),
                **{k: r.get(k) for k in ("waves_cleared", "waves", "min_health")},
                "killed_by": r.get("killed_by"),
                **{k: r[k] for k in _MATCH_KEYS if k in r},
            }
            for r in rows
        ]
        parts = [run["arena"], GAMES[_game(run)][0]]
        if run["waves"] or run["custom_waves"]:
            parts.append(run["waves"] or run["custom_waves"])
        stage = " · ".join(parts)
        extra = {"series": (record or {}).get("summary") or {}}
    else:
        rows = (record or {}).get("trials") or []
        episodes = [
            {
                **_episode(
                    "success" if r.get("killed") else str(r.get("end")),
                    r.get("seconds"),
                ),
                "weapon": r.get("weapon"),
                "mob": r.get("mob"),
            }
            for r in rows
        ]
        stage = f"{run['weapons']} vs {run['mobs']}"
        extra = {
            "cells": (record or {}).get("summary") or {},
            "regressions": (record or {}).get("regressions") or [],
        }
    passed = bool(episodes) and all(e["status"] == "success" for e in episodes)
    return {
        "suite": family,
        "run_id": run_id,
        "controller": "mcb",
        "course_id": stage,
        "status": "passed" if passed else "failed",
        "episodes": episodes,
        **extra,
    }


# --- the play and arena servers -----------------------------------------------------


def _client(instance: str, **kwargs):
    from mclab import companion_api, paths

    runtime = paths.instance_paths(instance).runtime / companion_api.RUNTIME_FILE
    return companion_api.ApiClient.from_runtime(runtime, **kwargs)


def bot_ready(client=None, *, instance: str = "play") -> bool:
    """The server's bot is spawned and idle-ready (as arena-hunt waits for)."""
    from mclab.companion_api import ApiUnavailable

    make = client or (lambda: _client(instance))
    try:
        reply = make().call("status", {}, source="api")
    except ApiUnavailable:
        return False
    bot = reply.get("result") or {}
    return bot.get("session") == "ready" and bot.get("body_state") == "ready"


_cached: dict = {}  # instance -> (until, state)


def server_state(
    instance: str, *, client=None, ttl: float = 5.0, clock=time.monotonic
) -> str:
    """``running`` while the server's companion host answers, else ``stopped``.
    Cached for ``ttl`` seconds: the dashboard asks every second."""
    from mclab.companion_api import ApiUnavailable

    now = clock()
    cached = _cached.get(instance)
    if ttl and cached is not None and cached[0] > now:
        return cached[1]
    make = client or (lambda: _client(instance, timeout=1.0))
    try:
        state = "running" if make().health() else "stopped"
    except ApiUnavailable:
        state = "stopped"
    _cached[instance] = (now + ttl, state)
    return state


def server_states(*, client=None, ttl: float = 5.0) -> dict[str, str]:
    """Each game server's state; ``client(instance)`` makes a host client."""
    return {
        name: server_state(name, client=client and client(name), ttl=ttl)
        for name in SERVERS
    }


def _server_state(instance: str) -> str:
    code, out = _run_cli(
        [sys.executable, "-c",
         "import json\nfrom mclab import server\nprint(json.dumps(server.status()))"],
        _env(instance),
    )  # fmt: skip
    try:
        return json.loads(out.strip().splitlines()[-1])["state"]
    except (IndexError, ValueError, KeyError, TypeError):
        return "unknown"


def _start_server(instance: str) -> None:
    code, out = _run_cli(
        [sys.executable, "-c",
         "import sys\nfrom mclab import play_cli\n"
         f"raise SystemExit(play_cli.run({instance!r}))",
         "server", "start"],
        _env(instance),
    )  # fmt: skip
    print(out.strip(), flush=True)
    if code != 0:
        raise RuntimeError(f"the {instance} server did not start (exit code {code})")


def ensure_server(
    instance: str,
    *,
    ready=None,
    state=None,
    start=None,
    clock=None,
    sleep=None,
    wait: float = SERVER_WAIT_S,
) -> dict:
    """Have a game server up with its bot ready: start it if it is stopped, then
    wait for the bot."""
    ready = ready or (lambda: bot_ready(instance=instance))
    if ready():
        return {"state": "ready", "started": False}
    state = state or (lambda: _server_state(instance))
    start = start or (lambda: _start_server(instance))
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    started = False
    if state() in ("stopped", "exited"):
        start()
        started = True
    deadline = clock() + wait
    while not ready():
        if clock() >= deadline:
            raise RuntimeError(
                f"the {instance} server's bot is not ready after {wait:.0f}s; see "
                f"runtime/instances/{instance}/companion-host.log"
            )
        sleep(2.0)
    return {"state": "ready", "started": started}
