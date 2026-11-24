"""Arena matches, measured (scripts/arena-hunt.py): dress everyone, start the match,
send the bot ``attack {arena}`` when it plays, and sample the match (and the bot's
health) about once a second until the match ends.

A match is co-op waves (``waves``: everyone against the mobs), team against team
(``teams``: mob waves optional) or a kill race (``race``: teams race to kill the
waves). The humans get the bot's kit (or are healed, keeping theirs); a match
without the bot is only watched to its end.

A run's row: ``mode``, ``outcome`` (the match's won / lost / stopped, or
``timeout``, ``refused``, ``not_started``), ``reason``, ``winner`` (a team; with
``bot_team`` and ``bot_won``), the tally (``players``: each player's team,
``mob_kills``, ``player_kills``, ``deaths``, ``damage_dealt``, ``damage_taken``,
``healing``; ``team_kills``), ``waves_cleared`` of ``waves``, ``seconds``,
``min_health``, the job's ``bot_kills`` / ``hits`` / ``damage``, the time each
wave was cleared (``wave_times``) and the ``samples``. A series writes
``results/companion/arena/<UTC stamp>.json`` with every run and its ``summary``
(``players``: each player's series totals and wins). Between the matches of a
series the lobby's sidebar shows the standings, and its end a chat recap
(mclab.arena.standings).
Docs: docs/ARENA_RUNTIME.md "The bot's hunt", "Boards and recaps".
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mclab.arena.client import ArenaError
from mclab.arena.standings import player_totals, series_board, series_recap

__all__ = [
    "ArenaError",
    "hunt",
    "parse_wave",
    "player_totals",
    "roster",
    "run_series",
    "series_board",
    "series_recap",
    "summary",
    "wave_times",
]

SAMPLE_S = 1.0  # one look at the match and the bot per second
READY_WAIT_S = 90.0  # a bot that died last run respawns before the next
KIT_WAIT_S = 10.0  # the kit handed out shows in the bot's status by then
PLAYERS_WAIT_S = 900.0  # humans respawn and type /mcb ready before each match
DONE = frozenset({"succeeded", "failed", "cancelled"})
JOB_FIELDS = ("kills", "hits", "damage", "waves_cleared", "waves")
USES = frozenset({"eat", "drink", "throw"})  # food and potion steps (consumables)
BOT = "MCBot"
SIDED = frozenset({"teams", "race"})


def parse_wave(text: str, delay: int) -> dict:
    """``pillager*2+vindicator`` as one wave: mobs by full id, after `delay` ticks."""
    mobs: dict[str, int] = {}
    for part in (p.strip() for p in text.split("+")):
        kind, _, count = part.partition("*")
        if not kind or (count and not count.isdigit()) or count == "0":
            raise ValueError(f"a wave is type*count+type, got {text!r}")
        full = kind if ":" in kind else f"minecraft:{kind}"
        mobs[full] = mobs.get(full, 0) + (int(count) if count else 1)
    return {"mobs": mobs, "delay_ticks": delay}


def _holds(api, kit: list[dict], clock, sleep, wait: float = KIT_WAIT_S) -> bool:
    """Whether the bot's status shows every kit item before ``wait`` runs out: the
    look right after the trial op can still be the empty inventory of a death."""
    deadline = clock() + wait
    while True:
        held = _result(api.call("status", {}, source="api")).get("inventory") or {}
        if all(held.get(g["item"], 0) >= 1 for g in kit):
            return True
        if clock() >= deadline:
            return False
        sleep(0.5)


def _result(reply: dict) -> dict:
    out = reply.get("result") if reply.get("ok") else None
    return out if isinstance(out, dict) else {}


def roster(participants, teams) -> list[str]:
    """Everyone in the match: the teams' members in order, else the participants."""
    if teams:
        return [n for names in teams.values() for n in names]
    return list(participants or ())


def _same(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.lower() == b.lower()


def _sample(api, arena, name: str, t: float) -> tuple[dict, dict]:
    match = (arena.status(name) or {}).get("match") or {}
    bot = _result(api.call("status", {}, source="api")) if api is not None else {}
    row = {
        "t": round(t, 1),
        "wave": match.get("wave_index"),
        "alive": len(match.get("wave_mobs") or []),
        "killed": match.get("killed"),
        "health": bot.get("health"),
    }
    return row, match


def wave_times(samples: list[dict]) -> list[dict]:
    """When the match moved past each wave (the first sample at a higher index)."""
    out, last = [], None
    for s in samples:
        wave = s.get("wave")
        if wave is None:
            continue
        if last is not None and wave > last:
            out.append({"wave": wave, "t": s["t"]})
        last = wave
    return out


def _end(api, arena, name: str) -> None:
    stops = [lambda: arena.stop(name)]
    if api is not None:
        stops.insert(0, lambda: api.call("stop", {}, source="api"))
    for stop in stops:
        try:
            stop()
        except Exception:  # noqa: BLE001 - nothing left running is the aim either way
            pass


def hunt(
    api,
    arena,
    name: str,
    waves: list[dict],
    *,
    participants: list[str] | None = None,
    timeout: float,
    mode: str = "waves",
    teams: dict[str, list[str]] | None = None,
    exit=None,  # noqa: A002 - the start op's field
    bot: str | None = BOT,
    clock: Callable[[], float] = time.monotonic,
    sample: float = SAMPLE_S,
    kit: list[dict] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    series: tuple[int, int] | None = None,
) -> dict:
    """One match from a full-health start to its end. ``kit``: the trial op's give
    entries, handed out afresh to the bot and every human (a player who died last
    run left the kit on the arena floor); None keeps what each carries. ``exit``:
    where the players still in the arena go when it ends. ``bot`` plays only when
    it is in the roster and ``api`` reaches it; else the match is only watched.
    ``series``: (match, of), for the match's board and recap."""
    people = roster(participants, teams)
    plays = api is not None and any(_same(p, bot) for p in people)
    humans = [p for p in people if not _same(p, bot)]
    row: dict = {"mode": mode, "participants": people}
    bot_team = None
    if mode in SIDED:
        bot_team = next(
            (
                t
                for t, names in (teams or {}).items()
                if any(_same(n, bot) for n in names)
            ),
            None,
        )
        row["bot_team"] = bot_team
    if kit is not None:
        row["kit"] = [g["item"] for g in kit]
    if plays:
        spec: dict = {"heal": True, "wear": True}
        if kit is not None:
            spec.update(clear_inventory=True, give=kit)
        api.trial(spec)
        if kit is not None and not _holds(api, kit, clock, sleep):
            return {**row, "outcome": "not_started",
                    "error": "the kit never showed in the bot's inventory"}  # fmt: skip
    try:
        if humans:
            arena.kit(humans, kit)
        arena.start(
            name,
            mode,
            waves=waves or None,
            participants=None if teams else (people or None),
            teams=teams,
            exit=exit,
            series=series,
        )
    except ArenaError as error:
        return {**row, "outcome": "not_started", "error": str(error)}
    started = clock()
    job_id, job, cursor = None, None, 0
    if plays:
        cursor = api.events(0, wait=0).get("latest", 0)  # this job's steps come after
        reply = api.call("attack", {"arena": name}, source="api")
        if not reply.get("ok"):
            _end(api, arena, name)
            return {**row, "outcome": "refused", "error": reply.get("error")}
        job_id = _result(reply)["job"]["id"]
    watcher = api if plays else None
    samples, match = [], {}
    while True:
        s, match = _sample(watcher, arena, name, clock() - started)
        samples.append(s)
        if plays:
            job = api.wait_job(job_id, sample)
            if job is None or job.get("state") in DONE:
                break
        elif match.get("state") != "running":
            break
        if clock() - started >= timeout:
            _end(watcher, arena, name)
            match = {**match, "state": "timeout"}
            break
        if not plays:
            sleep(sample)
    if match.get("state") == "running":  # the job ended first: read the match's end
        match = (arena.status(name) or {}).get("match") or match
    if match.get("state") == "running":  # the job gave up on a live match: end it, or
        _end(watcher, arena, name)  # the next run finds it still going (match_running)
        match = {**match, "state": "abandoned"}
    result = (job or {}).get("result") or {}
    health = [s["health"] for s in samples if isinstance(s.get("health"), (int, float))]
    row.update(
        outcome=match.get("state", "unknown"),
        reason=match.get("reason"),
        waves_cleared=result.get("waves_cleared", match.get("wave_index")),
        waves=result.get("waves", match.get("waves")),
        seconds=round(clock() - started, 1),
        min_health=min(health) if health else None,
        job_state=(job or {}).get("state"),
        error=(job or {}).get("error"),
        wave_times=wave_times(samples),
        **_board(match),
        **(_steps(api, cursor, job_id) if plays else {}),
        samples=samples,
        **{k: result[k] for k in ("hits", "damage") if k in result},
    )
    if "kills" in result:
        row["bot_kills"] = result["kills"]
    if bot_team is not None and row["outcome"] in ("won", "lost"):
        row["bot_won"] = match.get("winner") == bot_team
    return row


PLAYER_STATS = ("mob_kills", "player_kills", "deaths", "damage_dealt", "damage_taken",
                "healing")  # fmt: skip


def _board(match: dict) -> dict:
    """The match's tally: the winning team, each player's kills, downs and health
    dealt, taken and healed, each team's kills."""
    out: dict = {}
    if match.get("winner"):
        out["winner"] = match["winner"]
    players = {}
    for p in match.get("participants") or ():
        if not p.get("name"):
            continue
        entry = {k: p.get(k, 0) for k in PLAYER_STATS}
        if p.get("team"):
            entry = {"team": p["team"], **entry}
        players[p["name"]] = entry
    if players:
        out["players"] = players
    if match.get("team_kills"):
        out["team_kills"] = match["team_kills"]
    return out


def _add(total: dict, more) -> None:
    for k, v in (more or {}).items():
        if isinstance(v, (int, float)):
            total[k] = round(total.get(k, 0.0) + v, 2)


def _steps(api, cursor: int, job_id: str) -> dict:
    """The job's fights (host job_step events): every step, the damage the bot took
    by source, what landed the last blow, and which weapon met which mob type."""
    steps = [
        e["data"].get("step") or {}
        for e in api.events(cursor, wait=0).get("events") or ()
        if e.get("type") == "job_step" and (e.get("data") or {}).get("id") == job_id
    ]
    hurt: dict = {}
    picks: dict = {}
    uses: dict = {}
    killed_by = None
    for s in steps:
        _add(hurt, s.get("hurt_by"))
        killed_by = s.get("killed_by") or killed_by
        if s.get("target_type") and s.get("weapon"):
            kind = picks.setdefault(s["target_type"], {})
            kind[s["weapon"]] = kind.get(s["weapon"], 0) + 1
        if s.get("action") in USES and s.get("ok") and s.get("purpose"):
            what = s.get("potion") or s.get("item")
            used = uses.setdefault(s["purpose"], {})
            used[what] = used.get(what, 0) + 1
    return {"steps": steps, "hurt_by": hurt, "killed_by": killed_by, "picks": picks,
            "uses": uses}  # fmt: skip


def summary(rows: list[dict]) -> dict:
    """A series: win rate, outcomes, the winning teams and the bot's wins, the won
    runs' time spread, the lowest health, the damage taken by source and what
    killed the bot."""
    if not rows:
        return {"runs": 0}
    hurt: dict = {}
    uses: dict = {}
    for r in rows:
        _add(hurt, r.get("hurt_by"))
        for purpose, used in (r.get("uses") or {}).items():
            total = uses.setdefault(purpose, {})
            for what, n in used.items():
                total[what] = total.get(what, 0) + n
    killers = Counter(r["killed_by"] for r in rows if r.get("killed_by"))
    won = [r for r in rows if r.get("outcome") == "won"]
    times = [r["seconds"] for r in won if isinstance(r.get("seconds"), (int, float))]
    health = [
        r["min_health"] for r in rows if isinstance(r.get("min_health"), (int, float))
    ]
    cleared = [
        r["waves_cleared"] for r in rows if isinstance(r.get("waves_cleared"), int)
    ]
    return {
        "runs": len(rows),
        "won": len(won),
        "win_rate": len(won) / len(rows),
        "outcomes": dict(Counter(r.get("outcome") for r in rows)),
        "winners": dict(Counter(r["winner"] for r in rows if r.get("winner"))),
        "bot_wins": sum(1 for r in rows if r.get("bot_won") is True),
        "median_won_seconds": statistics.median(times) if times else None,
        "max_won_seconds": max(times) if times else None,
        "min_health": min(health) if health else None,
        "mean_waves_cleared": statistics.mean(cleared) if cleared else None,
        "hurt_by": dict(sorted(hurt.items(), key=lambda kv: -kv[1])),
        "killed_by": dict(killers),
        "uses": uses,
        "players": player_totals(rows),
    }


def _ready(api, clock, sleep, wait: float = READY_WAIT_S) -> bool:
    deadline = clock() + wait
    while True:
        bot = _result(api.call("status", {}, source="api"))
        if (
            bot.get("session") == "ready"
            and bot.get("body_state") == "ready"
            and not bot.get("job")
        ):
            return True
        if clock() >= deadline:
            return False
        sleep(1.0)


def _set_ops(arena, ops: dict[str, bool], bot: str | None, echo) -> None:
    """Each human's operator status, as the roster asks (the bot is never one)."""
    for player, flag in (ops or {}).items():
        if _same(player, bot):
            continue
        try:
            arena.op(player, op=flag)
            echo(f"{player}: operator {'on' if flag else 'off'}")
        except ArenaError as error:
            echo(f"{player}: operator status not set ({error})")


def _line(i: int, repeat: int, row: dict) -> str:
    head = f"run {i + 1}/{repeat}: "
    if row.get("winner"):
        head += f"{row['winner']} won"
    elif row.get("mode") in SIDED and row.get("outcome") == "lost":
        head += f"no winner ({row.get('reason')})"
    else:
        head += f"{row['outcome']}"
    if row.get("waves"):
        head += f" waves {row.get('waves_cleared')}/{row.get('waves')}"
    head += f" in {row.get('seconds')}s"
    if row.get("min_health") is not None:
        head += f", min health {row['min_health']}"
    return head + (f" ({row['error']})" if row.get("error") else "")


def run_series(
    api,
    arena,
    name: str,
    waves: list[dict],
    *,
    repeat: int,
    participants: list[str] | None = None,
    timeout: float,
    out_dir: Path | str,
    mode: str = "waves",
    teams: dict[str, list[str]] | None = None,
    exit=None,  # noqa: A002 - the start op's field
    ops: dict[str, bool] | None = None,
    bot: str | None = BOT,
    echo: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    label: str | None = None,
    kit: list[dict] | None = None,
    ready_timeout: float = PLAYERS_WAIT_S,
) -> Path:
    """`repeat` matches of the same roster and waves, each written as it ends.
    ``ops``: each human's operator status, set once before the first match.

    Before each match after the first, every human is waited for again (up to
    ``ready_timeout`` seconds): back on their feet and ``/mcb ready``. A player
    who died is on the death screen when a match ends; starting the next one then
    counted their team out at once and its kit's heal left them unable to respawn.
    The first match's wait is the caller's (the script stops before anything
    starts when nobody comes).

    Each match knows which of the series it is (its board and chat recap say so).
    With more than one match, the lobby's sidebar shows the standings after each
    (team wins, series kills) and the series' end sends a chat recap of everyone's
    totals; a single match keeps its own final board and recap."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = Path(out_dir) / f"{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    people = roster(participants, teams)
    plays = api is not None and any(_same(p, bot) for p in people)
    record = {
        "arena": name,
        "label": label,
        "mode": mode,
        "waves": waves,
        "participants": people,
        "teams": teams,
        "exit": None if exit is None else list(exit),
        "ops": ops or {},
        "kit": None if kit is None else [g["item"] for g in kit],
        "repeat": repeat,
        "runs": [],
        "summary": {},
    }
    _set_ops(arena, ops or {}, bot, echo)
    humans = [p for p in people if not _same(p, bot)]
    for i in range(repeat):
        if i and humans:
            try:
                arena.wait_ready(
                    people,
                    timeout=ready_timeout,
                    arena=name,
                    echo=echo,
                    match=(i + 1, repeat),
                )
            except ArenaError as error:
                echo(f"run {i + 1}: {error.detail or error}; the series stops")
                break
        if plays and not _ready(api, clock, sleep):
            echo(f"run {i + 1}: the bot is not ready; the series stops")
            break
        row = hunt(
            api,
            arena,
            name,
            waves,
            participants=participants,
            timeout=timeout,
            mode=mode,
            teams=teams,
            exit=exit,
            bot=bot,
            clock=clock,
            kit=kit,
            sleep=sleep,
            series=(i + 1, repeat),
        )
        record["runs"].append(row)
        record["summary"] = summary(record["runs"])
        path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        echo(_line(i, repeat, row))
        if repeat > 1:
            _show(echo, "standings", lambda: arena.board(name, *series_board(record)))
    if repeat > 1 and record["runs"]:
        _show(echo, "recap", lambda: arena.announce(name, series_recap(record)))
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return path


def _show(echo, what: str, send: Callable[[], object]) -> None:
    """The lobby's board or chat: a server that refuses it (an older mod) is
    said, and the series goes on."""
    try:
        send()
    except ArenaError as error:
        echo(f"the series {what} were not shown ({error})")
