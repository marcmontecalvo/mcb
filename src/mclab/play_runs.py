"""Play-server runs in the lab dashboard: arena actions, arena hunts and combat trials.

The dashboard's lab runs drive the lab server; these drive the PLAY server, through
the same scripts and CLI an operator uses (``scripts/arena-hunt.py``,
``scripts/combat-trial.py``, ``mcplay arena``), each in a child process bound to the
play instance. What the dashboard offers is read from the sources, not listed here:
the arenas are the plan files in ``arenas/``, the wave presets ``arenas/waves/``, the
weapons and mobs whatever ``combat_trial`` accepts. A new arena, wave preset or
weapon shows up without a change here; a new kind of play run is one ``PlayRun``
in ``RUNS`` (and its command). Docs: docs/LAB_UI.md "Arenas and combat".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from mclab.paths import ROOT

ARENAS = ROOT / "arenas"
WAVES = ARENAS / "waves"
SCRIPTS = ROOT / "scripts"
RESULTS = ROOT / "results"
INSTANCE = "play"
SECTION = "Arenas and combat"
COORDINATE_LIMIT = 30_000_000
WAVE_DELAY = 60  # ticks before each custom wave (arena-hunt.py --delay)
PLAY_WAIT_S = 300.0  # a cold play server and its bot are ready within this
DEFAULT_KIT = "all+armor+shield+trident+food+potions"
DEFAULT_WEAPONS = "sword,axe,spear,bow"
DEFAULT_MOBS = "zombie,skeleton,creeper,pig"
DEFAULT_PLAYERS = "MCBot"
_PLAYER = re.compile(r"[A-Za-z0-9_]{1,16}")
MAX_PLAYERS = 16

# run.<field> -> family; every one is None/False by default (run_config.RunSettings)
FIELDS = {
    "arena": (
        "arena", "arena_action", "waves", "custom_waves", "kit", "players",
        "corner", "restore", "count", "timeout",
    ),
    "combat": ("weapons", "mobs", "spot", "count", "timeout"),
}  # fmt: skip
FAMILIES = tuple(FIELDS)
POSITIONS = ("corner", "spot")
# label, help: shown by the dashboard next to the field
HELP = {
    "arena": ("Arena", "An arena plan in arenas/ (build it first)."),
    "arena_action": ("Arena action", "hunt, start, stop, build or remove."),
    "waves": ("Waves", "A wave preset in arenas/waves/."),
    "custom_waves": (
        "Custom waves",
        "Instead of a preset: one wave per comma, e.g. pillager*2, "
        "pillager+vindicator (each after 3 seconds).",
    ),
    "kit": (
        "Kit",
        "What the bot is handed each match: weapons and extras joined by + "
        f"(default {DEFAULT_KIT}), or keep for what it carries.",
    ),
    "players": ("Players", "Names, comma separated."),
    "corner": ("Corner", "The arena's minimum corner X Y Z."),
    "restore": ("Restore", "Clear the arena's blocks to air when removing it."),
    "weapons": (
        "Weapons",
        "Comma separated: sword, axe, spear, bow, trident, shield, armor, fist, "
        "food, potions or all; join with + for one kit (spear+sword).",
    ),
    "mobs": (
        "Mobs",
        "Comma separated: a mob, or a group like zombie*2+skeleton (an animal alone).",
    ),
    "spot": (
        "Fight spot",
        "X Y Z every trial is fought at (blank: where the bot stands).",
    ),
}


@dataclass(frozen=True)
class PlayRun:
    """One dashboard row: the fields it uses, the ones shown up front, defaults."""

    id: str
    family: str
    label: str
    description: str
    uses: tuple[str, ...]
    basic: tuple[str, ...]
    defaults: dict = field(default_factory=dict)


ACTIONS = {
    "hunt": "The bot clears a waves match on its own, measured.",
    "start": "Start a waves match for you (and anyone named) to play.",
    "stop": "End the arena's match and discard its mobs.",
    "build": "Build the arena at a corner on the play server.",
    "remove": "Unregister the arena (Restore clears it to air).",
}
RUNS = (
    PlayRun(
        "arena:hunt", "arena", "Bot clears an arena",
        "The bot fights waves of mobs in a built arena with a full kit, food and "
        "potions; wins, time and damage taken are recorded.",
        ("arena", "waves", "custom_waves", "kit", "players", "count", "timeout"),
        ("arena", "waves", "count"),
        {"players": DEFAULT_PLAYERS, "kit": DEFAULT_KIT},
    ),
    PlayRun(
        "arena:start", "arena", "Start an arena match", ACTIONS["start"],
        ("arena", "waves", "custom_waves", "players"),
        ("arena", "waves", "players"),
    ),
    PlayRun(
        "arena:stop", "arena", "Stop an arena match", ACTIONS["stop"],
        ("arena",), ("arena",),
    ),
    PlayRun(
        "arena:build", "arena", "Build an arena", ACTIONS["build"],
        ("arena", "corner"), ("arena", "corner"),
    ),
    PlayRun(
        "arena:remove", "arena", "Remove an arena", ACTIONS["remove"],
        ("arena", "restore"), ("arena", "restore"),
    ),
    PlayRun(
        "combat", "combat", "Combat trials",
        "Each weapon against each mob, one fight at a time, measured (kill rate, "
        "time to kill, health left).",
        ("weapons", "mobs", "spot", "count", "timeout"),
        ("weapons", "mobs", "count"),
        {"weapons": DEFAULT_WEAPONS, "mobs": DEFAULT_MOBS},
    ),
)  # fmt: skip


# --- choices, read from the files ---------------------------------------------------


def _stems(directory: Path) -> list[str]:
    return (
        sorted(p.stem for p in directory.glob("*.json")) if directory.is_dir() else []
    )


def arena_names() -> list[str]:
    """Every arena plan (``arenas/<name>.json``)."""
    return _stems(ARENAS)


def wave_presets() -> list[str]:
    """Every wave preset (``arenas/waves/<name>.json``)."""
    return _stems(WAVES)


def _first(names: list[str], preferred: str) -> str | None:
    return preferred if preferred in names else (names[0] if names else None)


def catalog_rows() -> list[dict]:
    """The dashboard's rows: overrides on the family defaults, the fields shown up
    front, and every family field the row does not use locked (hidden, and refused
    if changed)."""
    rows = []
    for spec in RUNS:
        run = {**spec.defaults}
        if spec.family == "arena":
            run["arena_action"] = spec.id.partition(":")[2]
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
                "section": SECTION,
                "server": "play",
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


def check(run: dict) -> None:
    """Refuse bad play-run settings (shape and choices); completeness is
    ``require``'s, at launch."""
    from mclab import combat_trial as ct

    family = run["test"]
    if family == "arena":
        if run["arena_action"] not in (None, *ACTIONS):
            raise ValueError(f"arena action must be one of {', '.join(ACTIONS)}")
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
            ct.kit(run["kit"])
        players = _names(run["players"])
        if len(players) > MAX_PLAYERS or not all(map(_PLAYER.fullmatch, players)):
            raise ValueError("players are Minecraft names, comma separated")
        _position(run["corner"], "corner")
    elif family == "combat":
        for name, one in (("weapons", ct.kit), ("mobs", lambda m: ct.scene("fist", m))):
            if run[name] is None:
                continue
            if not _names(run[name]):
                raise ValueError(f"run.{name} is empty")
            for part in _names(run[name]):
                one(part)
        _position(run["spot"], "spot")


def require(run: dict) -> None:
    """What a play run needs before it may start."""
    if run["test"] == "arena":
        if run["arena_action"] is None:
            raise ValueError("choose an arena action")
        if run["arena"] is None:
            raise ValueError("choose an arena")
        if (
            run["arena_action"] in ("hunt", "start")
            and run["waves"] is None
            and not run["custom_waves"]
        ):
            raise ValueError("choose a wave preset or custom waves")
        if run["arena_action"] == "build" and run["corner"] is None:
            raise ValueError("a build needs the arena's corner X Y Z")
    elif not _names(run["weapons"]) or not _names(run["mobs"]):
        raise ValueError("a combat trial needs weapons and mobs")


# --- commands -----------------------------------------------------------------------


def command(run: dict, out: Path) -> list[str]:
    """The script and its arguments for a hunt or a combat trial series."""
    if run["test"] == "combat":
        argv = [str(SCRIPTS / "combat-trial.py"), "--weapons", run["weapons"]]
        argv += ["--mobs", run["mobs"], "--repeat", str(run["count"])]
        if run["timeout"] is not None:
            argv += ["--timeout", str(run["timeout"])]
        argv += ["--out", str(out)]
        if run["spot"] is not None:
            # "=": argparse would read a negative coordinate as a flag
            argv.append("--arena=" + ",".join(map(str, run["spot"])))
        return argv
    argv = [str(SCRIPTS / "arena-hunt.py"), "--arena", run["arena"]]
    if run["waves"] is not None:
        argv += ["--waves", run["waves"]]
    for wave in _names(run["custom_waves"]):
        argv += ["--wave", wave]
    players = _names(run["players"])
    if players:
        argv += ["--players", *players]
    if run["kit"] is not None and run["kit"] != DEFAULT_KIT:
        argv += ["--kit", run["kit"]]
    argv += ["--repeat", str(run["count"])]
    if run["timeout"] is not None:
        argv += ["--timeout", str(run["timeout"])]
    return argv + ["--out", str(out)]


def arena_args(run: dict, *, scratch: Path | None = None) -> list[str]:
    """``mcplay arena`` arguments for an arena action other than a hunt; custom
    waves for a match go through a waves file in ``scratch``."""
    action, name = run["arena_action"], run["arena"]
    if action == "build":
        return ["build", name, "--at", *map(str, run["corner"]), "--wait"]
    if action == "remove":
        return ["remove", name, *(["--restore"] if run["restore"] else [])]
    if action == "stop":
        return ["stop", name]
    args = ["start", name, "--waves"]
    if run["custom_waves"]:
        path = Path(scratch or tempfile.mkdtemp(prefix="mcb-waves-")) / "waves.json"
        path.write_text(json.dumps(custom_waves(run["custom_waves"])), "utf-8")
        args.append(str(path))
    else:
        args.append(run["waves"])
    players = _names(run["players"])
    return args + (["--players", *players] if players else [])


def _env() -> dict:
    return {
        **os.environ,
        "MCLAB_INSTANCE": INSTANCE,  # never the lab
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
    """One ``mcplay arena`` command on the play server; its JSON answer."""
    with tempfile.TemporaryDirectory(prefix="mcb-arena-") as scratch:
        argv = [sys.executable, "-u", "-c", _ARENA_CLI]
        argv += arena_args(run, scratch=Path(scratch))
        code, out = run_cli(argv, _env())
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
    """Run one play run for the dashboard worker. Hunts and trials write their
    record and a ``summary.json`` (a history row) to ``results/<family>/<stamp>/``."""
    from mclab.dashboard_runs import _SHIM

    run = config["run"]
    family = run["test"]
    if family == "arena" and run["arena_action"] != "hunt":
        return arena_action(run, run_cli=run_cli)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder = Path(results or RESULTS) / family / stamp
    folder.mkdir(parents=True)
    # The shim turns the dashboard's Ctrl+Break into KeyboardInterrupt in the script.
    child = spawn([sys.executable, "-u", "-c", _SHIM, *command(run, folder)], _env())
    try:
        code = child.wait()
    except KeyboardInterrupt:
        _stop_child(child)
        if family == "arena":  # a hunt left mid-match: end it, or it runs on
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
        result["error"] = f"{Path(command(run, folder)[0]).name} exit code {code}"
    return result


def _episode(status: str, seconds) -> dict:
    row = {"status": status, "seconds": seconds}
    if status == "success" and isinstance(seconds, (int, float)):
        row["ticks"] = round(seconds * 20)
    return row


def summarize(run: dict, record: dict | None, *, run_id: str) -> dict:
    """A hunt or trial record as a dashboard history row (results/*/*/summary.json)."""
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
            }
            for r in rows
        ]
        stage = f"{run['arena']} · {run['waves'] or run['custom_waves']}"
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


# --- the play server ----------------------------------------------------------------


def _client(**kwargs):
    from mclab import companion_api, paths

    runtime = paths.instance_paths(INSTANCE).runtime / companion_api.RUNTIME_FILE
    return companion_api.ApiClient.from_runtime(runtime, **kwargs)


def bot_ready(client=_client) -> bool:
    """The play bot is spawned and idle-ready (as arena-hunt waits for)."""
    from mclab.companion_api import ApiUnavailable

    try:
        reply = client().call("status", {}, source="api")
    except ApiUnavailable:
        return False
    bot = reply.get("result") or {}
    return bot.get("session") == "ready" and bot.get("body_state") == "ready"


_cached: dict = {}


def play_state(*, client=None, ttl: float = 5.0, clock=time.monotonic) -> str:
    """``running`` while the play companion host answers, else ``stopped``. Cached
    for ``ttl`` seconds: the dashboard asks every second."""
    from mclab.companion_api import ApiUnavailable

    now = clock()
    if ttl and _cached.get("until", -1.0) > now:
        return _cached["state"]
    make = client or (lambda: _client(timeout=1.0))
    try:
        state = "running" if make().health() else "stopped"
    except ApiUnavailable:
        state = "stopped"
    _cached.update(state=state, until=now + ttl)
    return state


def _server_state() -> str:
    code, out = _run_cli(
        [sys.executable, "-c",
         "import json\nfrom mclab import server\nprint(json.dumps(server.status()))"],
        _env(),
    )  # fmt: skip
    try:
        return json.loads(out.strip().splitlines()[-1])["state"]
    except (IndexError, ValueError, KeyError, TypeError):
        return "unknown"


def _start_server() -> None:
    code, out = _run_cli(
        [sys.executable, "-m", "mclab.play_cli", "server", "start"], _env()
    )
    print(out.strip(), flush=True)
    if code != 0:
        raise RuntimeError(f"mcplay server start failed (exit code {code})")


def ensure_play(
    *,
    ready=bot_ready,
    state=_server_state,
    start=_start_server,
    clock=time.monotonic,
    sleep=time.sleep,
    wait: float = PLAY_WAIT_S,
) -> dict:
    """Have the play server up with its bot ready: start it if it is stopped, then
    wait for the bot."""
    if ready():
        return {"state": "ready", "started": False}
    started = False
    if state() in ("stopped", "exited"):
        start()
        started = True
    deadline = clock() + wait
    while not ready():
        if clock() >= deadline:
            raise RuntimeError(
                f"the play server's bot is not ready after {wait:.0f}s; see "
                "runtime/instances/play/companion-host.log"
            )
        sleep(2.0)
    return {"state": "ready", "started": started}
