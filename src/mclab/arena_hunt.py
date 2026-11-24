"""Arena matches, measured (scripts/arena-hunt.py): dress everyone, start the match,
send each bot that plays ``attack {arena}``, and sample the match (and every bot's
health) about once a second until the match ends.

A match is co-op waves (``waves``: everyone against the mobs), team against team
(``teams``: mob waves optional) or a kill race (``race``: teams race to kill the
waves). The humans get the bots' kit (or are healed, keeping theirs); a match
without a bot is only watched to its end. Several bots may play one match (MCBot,
MCBot2, ...): each is kitted, sent and measured on its own, never waited for as a
human, and a bot's foes are the bots and humans of the other teams.

A run's row: ``mode``, ``outcome`` (the match's won / lost / stopped, or
``timeout``, ``refused``, ``not_started``), ``reason``, ``winner`` (a team), the
tally (``players``: each player's, bots included, team, ``mob_kills``,
``player_kills``, ``deaths``, ``damage_dealt``, ``damage_taken``, ``healing``;
``team_kills``), ``waves_cleared`` of ``waves``, ``seconds``, ``min_health``, the
job's ``bot_kills`` / ``hits`` / ``damage``, the time each wave was cleared
(``wave_times``) and the ``samples``. With one bot those are its own, beside
``bot``, ``bot_team`` and ``bot_won``; with several they are the totals and
``bots`` holds each bot's row (``team``, ``bot_won``, ``min_health``,
``job_state``, ``bot_kills``, ``steps``, ``hurt_by``, ``killed_by``, ...). A series
writes ``results/companion/arena/<UTC stamp>.json`` with every run and its
``summary`` (``players``: each player's series totals and wins). Between the
matches of a series the lobby's sidebar shows the standings, and its end the
after-action table of the totals (mclab.arena.standings).
Docs: docs/ARENA_RUNTIME.md "The bot's hunt", "Several bots", "Boards and recaps".
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from mclab.arena.client import ArenaError
from mclab.arena.standings import player_totals, series_board, series_recap
from mclab.companion_names import bot_id_for_name

__all__ = [
    "ArenaError",
    "BotsUnavailable",
    "ensure_bots",
    "hunt",
    "parse_wave",
    "player_totals",
    "resolve_bots",
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


class BotsUnavailable(RuntimeError):
    """A bot the match needs is not on the server and could not be put there."""


class _Fighter:
    """One bot of a match: the shared companion api, addressed by its fleet id (None
    is the server's only or first bot, called as before bots had ids)."""

    def __init__(self, api, name: str, bot_id: str | None = None):
        self.api, self.name, self.id = api, name, bot_id

    def _route(self) -> dict:
        return {} if self.id is None else {"bot": self.id}

    def call(self, intent: str, args: dict | None = None, **kwargs) -> dict:
        return self.api.call(intent, args, **kwargs, **self._route())

    def trial(self, spec: dict) -> dict:
        return self.api.trial(spec, **self._route())

    def events(self, after: int = 0, wait: float = 0.0) -> dict:
        return self.api.events(after, wait=wait, **self._route())

    def wait_job(self, job_id: str, wait: float = 0.0) -> dict | None:
        return self.api.wait_job(job_id, wait, **self._route())


def _bots_of(bot: str | None, bots: dict[str, str | None] | None) -> dict:
    """The bots of a match, name -> fleet id: ``bots``, else the one ``bot``."""
    if bots is not None:
        return dict(bots)
    return {bot: None} if bot else {}


def resolve_bots(api, people: Iterable[str], extra: Iterable[str] = ()) -> dict:
    """Which of ``people`` are bots, name -> fleet id (in the roster's spelling): the
    ones running on the host, then ``MCBot`` / ``MCBot<N>`` (put in by
    ``ensure_bots``). ``extra`` names bots the operator says are there (a profile
    with another name); one that is not running is an error, not a human."""
    running = {r["name"].lower(): r["id"] for r in api.bots() if r.get("name")}
    wanted = {n.lower() for n in extra}
    found: dict[str, str | None] = {}
    for person in people:
        key = person.lower()
        bot_id = running.get(key) or bot_id_for_name(person)
        if bot_id is None and key in wanted:
            here = ", ".join(sorted(running.values())) or "none"
            raise BotsUnavailable(f"no bot named {person}; running: {here}")
        if bot_id is not None:
            found[person] = bot_id
    return found


def ensure_bots(api, bots: dict[str, str | None]) -> list[str]:
    """Put in every bot of the match that is not on the host yet; the ids it added."""
    have = {r["id"] for r in api.bots()}
    added = []
    for bot_id in dict.fromkeys(i for i in bots.values() if i is not None):
        if bot_id in have:
            continue
        reply = api.add_bot(bot_id)
        if not reply.get("ok"):
            raise BotsUnavailable(f"could not put in {bot_id}: {reply.get('error')}")
        added.append(bot_id)
    return added


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


def _team_of(teams: dict[str, list[str]] | None, name: str) -> str | None:
    return next(
        (t for t, names in (teams or {}).items() if any(_same(n, name) for n in names)),
        None,
    )


def _sample(fighters: list[_Fighter], arena, name: str, t: float) -> tuple[dict, dict]:
    """One look at the match and at every bot: ``health`` is the first bot's (the
    only one's), ``bot_health`` every bot's when there are several."""
    match = (arena.status(name) or {}).get("match") or {}
    seen = {f.name: _result(f.call("status", {}, source="api")) for f in fighters}
    first = next(iter(seen.values()), {})
    row = {
        "t": round(t, 1),
        "wave": match.get("wave_index"),
        "alive": len(match.get("wave_mobs") or []),
        "killed": match.get("killed"),
        "health": first.get("health"),
    }
    if len(seen) > 1:
        row["bot_health"] = {n: s.get("health") for n, s in seen.items()}
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


def _humans_playing(match: dict, humans: list[str]) -> bool:
    """Whether a running match still has a live human in it: it plays on when the
    bots are out (dead, or their jobs over), and ends only when nobody is left."""
    if match.get("state") != "running":
        return False
    return any(
        isinstance(p, dict)
        and p.get("alive")
        and any(_same(p.get("name"), h) for h in humans)
        for p in match.get("participants") or ()
    )


def _end(fighters: list[_Fighter], arena, name: str) -> None:
    """Stop every bot, then the match (each tried even when another fails)."""
    stops = [lambda f=f: f.call("stop", {}, source="api") for f in fighters]
    stops.append(lambda: arena.stop(name))
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
    bots: dict[str, str | None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sample: float = SAMPLE_S,
    kit: list[dict] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    series: tuple[int, int] | None = None,
) -> dict:
    """One match from a full-health start to its end. ``kit``: the trial op's give
    entries, handed out afresh to every bot and human (a player who died last run
    left the kit on the arena floor); None keeps what each carries. ``exit``: where
    the players still in the arena go when it ends. ``series``: (match, of), for
    the match's board and recap.

    ``bots`` (name -> fleet id; ``bot`` alone is the server's first bot) are the
    bots a roster may hold. Each one in the roster plays when ``api`` reaches it,
    on its own kit, attack job and row; the rest of the roster are the humans. A
    match with no bot playing is only watched."""
    people = roster(participants, teams)
    named = _bots_of(bot, bots)
    in_roster = [n for n in named if any(_same(p, n) for p in people)]
    spelled = {n: next(p for p in people if _same(p, n)) for n in in_roster}
    fighters = (
        [_Fighter(api, spelled[n], named[n]) for n in in_roster]
        if api is not None
        else []
    )
    humans = [p for p in people if not any(_same(p, n) for n in named)]
    many = len(fighters) > 1
    row: dict = {"mode": mode, "participants": people}
    if mode in SIDED and len(in_roster) <= 1:
        row["bot_team"] = _team_of(teams, in_roster[0]) if in_roster else None
    if kit is not None:
        row["kit"] = [g["item"] for g in kit]
    if fighters:
        spec: dict = {"heal": True, "wear": True}
        if kit is not None:
            spec.update(clear_inventory=True, give=kit)
        for f in fighters:
            f.trial(spec)
        for f in fighters:
            if kit is not None and not _holds(f, kit, clock, sleep):
                error = f"the kit never showed in {f.name}'s inventory"
                return {**row, "outcome": "not_started", "error": error}
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
    job_ids: dict[str, str] = {}
    cursors: dict[str, int] = {}
    for f in fighters:
        cursors[f.name] = f.events(0, wait=0).get("latest", 0)  # its steps come after
        reply = f.call("attack", {"arena": name}, source="api")
        if not reply.get("ok"):
            _end(fighters, arena, name)
            return {**row, "outcome": "refused", "error": reply.get("error"),
                    "refused_by": f.name}  # fmt: skip
        job_ids[f.name] = _result(reply)["job"]["id"]
    jobs: dict[str, dict | None] = {}
    over: set[str] = set()  # the bots whose job has ended
    samples, match = [], {}
    while True:
        s, match = _sample(fighters, arena, name, clock() - started)
        samples.append(s)
        if fighters:
            live = [f for f in fighters if f.name not in over]
            for turn, f in enumerate(live):  # block on the first only: one tick of wait
                job = f.wait_job(job_ids[f.name], sample if turn == 0 else 0.0)
                jobs[f.name] = job
                if job is None or job.get("state") in DONE:
                    over.add(f.name)
            if len(over) == len(fighters) and not _humans_playing(match, humans):
                break
        elif match.get("state") != "running":
            break
        if clock() - started >= timeout:
            _end(fighters, arena, name)
            match = {**match, "state": "timeout"}
            break
        if len(over) == len(fighters):  # nothing left to wait on but the match itself
            sleep(sample)
    if match.get("state") == "running":  # the jobs ended first: read the match's end
        match = (arena.status(name) or {}).get("match") or match
    if match.get("state") == "running":  # the jobs gave up on a live match: end it, or
        _end(fighters, arena, name)  # the next run finds it still going (match_running)
        match = {**match, "state": "abandoned"}
    outcome = match.get("state", "unknown")
    per = {
        f.name: _bot_row(
            f,
            jobs.get(f.name),
            cursors[f.name],
            job_ids[f.name],
            [s["bot_health"][f.name] if many else s.get("health") for s in samples],
            _team_of(teams, f.name) if mode in SIDED else None,
            match,
            outcome,
        )
        for f in fighters
    }
    results = [(jobs.get(f.name) or {}).get("result") or {} for f in fighters]
    health = [
        h
        for s in samples
        for h in (s["bot_health"].values() if many else [s.get("health")])
        if isinstance(h, (int, float))
    ]
    row.update(
        outcome=outcome,
        reason=match.get("reason"),
        waves_cleared=next(
            (r["waves_cleared"] for r in results if "waves_cleared" in r),
            match.get("wave_index"),
        ),
        waves=next((r["waves"] for r in results if "waves" in r), match.get("waves")),
        seconds=round(clock() - started, 1),
        min_health=min(health) if health else None,
        wave_times=wave_times(samples),
        **_board(match),
        samples=samples,
    )
    if many:
        row.update(_totals(per), bots=per)
    elif per:  # one bot: its row is the match's, as before bots had names
        who, only = next(iter(per.items()))
        only.pop("team", None)  # row["bot_team"] says it
        row.update(bot=who, **only)
    else:  # watched only: no job, as ever
        row.update(job_state=None, error=None)
    return row


PLAYER_STATS = ("mob_kills", "player_kills", "deaths", "damage_dealt", "damage_taken",
                "healing")  # fmt: skip


def _bot_row(f, job, cursor, job_id, healths, team, match, outcome) -> dict:
    """What one bot did in the match: its job's end and totals, its fights (every
    step, the damage it took by source, what dealt the last blow, which weapon met
    which mob type), its lowest health and, in a team match, its side and result."""
    result = (job or {}).get("result") or {}
    health = [h for h in healths if isinstance(h, (int, float))]
    out = {
        "min_health": min(health) if health else None,
        "job_state": (job or {}).get("state"),
        "error": (job or {}).get("error"),
        **_steps(f, cursor, job_id),
        **{k: result[k] for k in ("hits", "damage") if k in result},
    }
    if "kills" in result:
        out["bot_kills"] = result["kills"]
    if team is not None:
        out["team"] = team
        if outcome in ("won", "lost"):
            out["bot_won"] = match.get("winner") == team
    return out


def _totals(per: dict[str, dict]) -> dict:
    """The sides' sums over every bot's row: kills, hits, damage dealt, damage taken
    by source and the food and potions used."""
    out: dict = {}
    for key in ("bot_kills", "hits", "damage"):
        values = [b[key] for b in per.values() if key in b]
        if values:
            out[key] = round(sum(values), 2) if key == "damage" else sum(values)
    hurt: dict = {}
    uses: dict = {}
    for b in per.values():
        _add(hurt, b.get("hurt_by"))
        _merge_uses(uses, b.get("uses"))
    return {**out, "hurt_by": hurt, "uses": uses}


def _merge_uses(total: dict, more) -> None:
    for purpose, used in (more or {}).items():
        into = total.setdefault(purpose, {})
        for what, n in used.items():
            into[what] = into.get(what, 0) + n


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
    """A series: win rate, outcomes, the winning teams and the bots' wins, the won
    runs' time spread, the lowest health, the damage taken by source and what
    killed the bots, and (when bots are named) each bot's ``runs``, ``wins``,
    ``bot_kills``, ``deaths``, ``min_health`` and damage taken."""
    if not rows:
        return {"runs": 0}
    hurt: dict = {}
    uses: dict = {}
    killers: Counter = Counter()
    each: dict[str, dict] = {}
    wins = 0
    for r in rows:
        for who, b in _bots_in(r):
            _add(hurt, b.get("hurt_by"))
            _merge_uses(uses, b.get("uses"))
            if b.get("killed_by"):
                killers[b["killed_by"]] += 1
            wins += b.get("bot_won") is True
            if who is not None:
                _tally(each.setdefault(who, {}), b)
    won = [r for r in rows if r.get("outcome") == "won"]
    times = [r["seconds"] for r in won if isinstance(r.get("seconds"), (int, float))]
    health = [
        r["min_health"] for r in rows if isinstance(r.get("min_health"), (int, float))
    ]
    cleared = [
        r["waves_cleared"] for r in rows if isinstance(r.get("waves_cleared"), int)
    ]
    out = {
        "runs": len(rows),
        "won": len(won),
        "win_rate": len(won) / len(rows),
        "outcomes": dict(Counter(r.get("outcome") for r in rows)),
        "winners": dict(Counter(r["winner"] for r in rows if r.get("winner"))),
        "bot_wins": wins,
        "median_won_seconds": statistics.median(times) if times else None,
        "max_won_seconds": max(times) if times else None,
        "min_health": min(health) if health else None,
        "mean_waves_cleared": statistics.mean(cleared) if cleared else None,
        "hurt_by": dict(sorted(hurt.items(), key=lambda kv: -kv[1])),
        "killed_by": dict(killers),
        "uses": uses,
        "players": player_totals(rows),
    }
    if each:
        out["bots"] = each
    return out


def _bots_in(row: dict) -> list[tuple[str | None, dict]]:
    """The bot rows of a run: each one of ``bots``, the one flat row of a match with
    a single bot (named by ``bot``, or not by an older row), or none."""
    if row.get("bots"):
        return list(row["bots"].items())
    return [(row.get("bot"), row)]


def _tally(total: dict, b: dict) -> None:
    """Add one run of one bot to its series row."""
    for key, zero in (("runs", 0), ("wins", 0), ("bot_kills", 0), ("deaths", 0)):
        total.setdefault(key, zero)
    total.setdefault("hurt_by", {})
    total.setdefault("min_health", None)
    total["runs"] += 1
    total["wins"] += b.get("bot_won") is True
    total["bot_kills"] += b.get("bot_kills") or 0
    total["deaths"] += bool(b.get("killed_by"))
    _add(total["hurt_by"], b.get("hurt_by"))
    lows = [
        v
        for v in (total["min_health"], b.get("min_health"))
        if isinstance(v, (int, float))
    ]
    total["min_health"] = min(lows) if lows else None


def _ready(f: _Fighter, clock, sleep, wait: float = READY_WAIT_S) -> bool:
    """Whether the bot is spawned, idle and ready before ``wait`` runs out."""
    deadline = clock() + wait
    while True:
        bot = _result(f.call("status", {}, source="api"))
        if (
            bot.get("session") == "ready"
            and bot.get("body_state") == "ready"
            and not bot.get("job")
        ):
            return True
        if clock() >= deadline:
            return False
        sleep(1.0)


def _set_ops(arena, ops: dict[str, bool], bots: Iterable[str], echo) -> None:
    """Each human's operator status, as the roster asks (a bot is never one)."""
    bots = list(bots)
    for player, flag in (ops or {}).items():
        if any(_same(player, b) for b in bots):
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
    bots: dict[str, str | None] | None = None,
    echo: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    label: str | None = None,
    kit: list[dict] | None = None,
    ready_timeout: float = PLAYERS_WAIT_S,
) -> Path:
    """`repeat` matches of the same roster and waves, each written as it ends.
    ``ops``: each human's operator status, set once before the first match.
    ``bots``: name -> fleet id of the bots a roster may hold (as for ``hunt``);
    before each match every bot of the roster must be ready, and none is ever
    waited for as a human.

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
    named = _bots_of(bot, bots)
    fighters = (
        [_Fighter(api, p, named[n]) for n in named for p in people if _same(p, n)]
        if api is not None
        else []
    )
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
        "bots": {f.name: f.id for f in fighters},
        "runs": [],
        "summary": {},
    }
    _set_ops(arena, ops or {}, named, echo)
    humans = [p for p in people if not any(_same(p, n) for n in named)]
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
        unready = next((f for f in fighters if not _ready(f, clock, sleep)), None)
        if unready is not None:
            echo(f"run {i + 1}: {unready.name} is not ready; the series stops")
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
            bots=bots,
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
        _show(echo, "recap", lambda: arena.recap(name, **series_recap(record)))
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return path


def _show(echo, what: str, send: Callable[[], object]) -> None:
    """The lobby's board or chat. The mod's protocol was checked before the first
    match (``ArenaClient.require_protocol``), so a refusal here is a fault, not an
    older mod: it is said as an error, and the series goes on, since the matches
    and their record are what it is for and a missing board does not change them."""
    try:
        send()
    except ArenaError as error:
        echo(f"error: the series {what} could not be shown: {error}")
