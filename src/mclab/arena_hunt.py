"""The bot's arena hunt, measured (scripts/arena-hunt.py): heal and dress it, start a
waves match with it as a participant, send ``attack {arena}``, and sample the match
and the bot's health about once a second until the job ends with the match.

A run's row: ``outcome`` (the match's won / lost / stopped, or ``timeout``,
``refused``, ``not_started``), ``reason``, ``waves_cleared`` of ``waves``,
``seconds``, ``min_health``, the job's ``kills`` / ``hits`` / ``damage``, the time
each wave was cleared (``wave_times``) and the ``samples``. A series writes
``results/companion/arena/<UTC stamp>.json`` with every run and its ``summary``.
Docs: docs/ARENA_RUNTIME.md "The bot's hunt".
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

__all__ = ["ArenaError", "hunt", "parse_wave", "run_series", "summary", "wave_times"]

SAMPLE_S = 1.0  # one look at the match and the bot per second
READY_WAIT_S = 90.0  # a bot that died last run respawns before the next
KIT_WAIT_S = 10.0  # the kit handed out shows in the bot's status by then
DONE = frozenset({"succeeded", "failed", "cancelled"})
JOB_FIELDS = ("kills", "hits", "damage", "waves_cleared", "waves")
USES = frozenset({"eat", "drink", "throw"})  # food and potion steps (consumables)


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


def _sample(api, arena, name: str, t: float) -> tuple[dict, dict]:
    match = (arena.status(name) or {}).get("match") or {}
    bot = _result(api.call("status", {}, source="api"))
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
    for stop in (lambda: api.call("stop", {}, source="api"), lambda: arena.stop(name)):
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
    participants: list[str],
    timeout: float,
    clock: Callable[[], float] = time.monotonic,
    sample: float = SAMPLE_S,
    kit: list[dict] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """One match: the bot hunts it from a full-health start to its end. ``kit``:
    the trial op's give entries, handed out afresh (a bot that died last run left
    its kit on the arena floor); None keeps what it carries."""
    spec: dict = {"heal": True, "wear": True}
    if kit is not None:
        spec.update(clear_inventory=True, give=kit)
    api.trial(spec)
    row: dict = {"participants": participants}
    if kit is not None:
        row["kit"] = [g["item"] for g in kit]
        if not _holds(api, kit, clock, sleep):
            return {**row, "outcome": "not_started",
                    "error": "the kit never showed in the bot's inventory"}  # fmt: skip
    try:
        arena.start(name, "waves", waves=waves, participants=participants)
    except ArenaError as error:
        return {**row, "outcome": "not_started", "error": str(error)}
    started = clock()
    cursor = api.events(0, wait=0).get("latest", 0)  # this job's steps come after
    reply = api.call("attack", {"arena": name}, source="api")
    if not reply.get("ok"):
        _end(api, arena, name)
        return {**row, "outcome": "refused", "error": reply.get("error")}
    job_id = _result(reply)["job"]["id"]
    samples, match, job = [], {}, None
    while True:
        s, match = _sample(api, arena, name, clock() - started)
        samples.append(s)
        job = api.wait_job(job_id, sample)
        if job is None or job.get("state") in DONE:
            break
        if clock() - started >= timeout:
            _end(api, arena, name)
            match = {**match, "state": "timeout"}
            break
    if match.get("state") == "running":  # the job ended first: read the match's end
        match = (arena.status(name) or {}).get("match") or match
    if match.get("state") == "running":  # the job gave up on a live match: end it, or
        _end(api, arena, name)  # the next run finds it still going (match_running)
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
        **_steps(api, cursor, job_id),
        samples=samples,
        **{
            k: result[k]
            for k in JOB_FIELDS
            if k in result and k not in ("waves_cleared", "waves")
        },
    )
    return row


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
    """A series: win rate, outcomes, the won runs' time spread, the lowest health,
    the damage taken by source and what killed the bot."""
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
        "median_won_seconds": statistics.median(times) if times else None,
        "max_won_seconds": max(times) if times else None,
        "min_health": min(health) if health else None,
        "mean_waves_cleared": statistics.mean(cleared) if cleared else None,
        "hurt_by": dict(sorted(hurt.items(), key=lambda kv: -kv[1])),
        "killed_by": dict(killers),
        "uses": uses,
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


def run_series(
    api,
    arena,
    name: str,
    waves: list[dict],
    *,
    repeat: int,
    participants: list[str],
    timeout: float,
    out_dir: Path | str,
    echo: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    label: str | None = None,
    kit: list[dict] | None = None,
) -> Path:
    """`repeat` hunts of the same waves, each written as it ends."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = Path(out_dir) / f"{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "arena": name,
        "label": label,
        "waves": waves,
        "participants": participants,
        "kit": None if kit is None else [g["item"] for g in kit],
        "runs": [],
        "summary": {},
    }
    for i in range(repeat):
        if not _ready(api, clock, sleep):
            echo(f"run {i + 1}: the bot is not ready; the series stops")
            break
        row = hunt(
            api,
            arena,
            name,
            waves,
            participants=participants,
            timeout=timeout,
            clock=clock,
            kit=kit,
            sleep=sleep,
        )
        record["runs"].append(row)
        record["summary"] = summary(record["runs"])
        path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        cleared = f"{row.get('waves_cleared')}/{row.get('waves')}"
        echo(
            f"run {i + 1}/{repeat}: {row['outcome']} waves {cleared} in "
            f"{row.get('seconds')}s, min health {row.get('min_health')}"
            + (f" ({row['error']})" if row.get("error") else "")
        )
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return path
