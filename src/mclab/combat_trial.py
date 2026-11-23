"""Combat trials: one weapon against one mob on the play server, measured.

The operator's scene op (control API ``POST /trial``, never an MCP tool) clears the
area and the inventory, heals, sets the time, gives the kit and summons the mob
``distance`` blocks away. Then the bot acts on its own:

- a hostile mob: the guard defends; the trial ends at the first ``defense_step``
  with a kill, at ``defense_end`` (the mob fled, the bot escaped, died or was
  stopped) or at the timeout;
- an animal: it is hunted the way a player asks for meat (``gather`` its raw
  drop), which runs the animal rules (biggest single hit, crits, no sweep).

``scripts/combat-trial.py`` runs the weapon x mob matrix and writes
``results/companion/trials/<UTC stamp>.json``.
Docs: COMPANION_COMBAT_PLAN_2026-11-23.md section 5.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

M = "minecraft:"
CLEAR = 24  # blocks around the bot cleared of mobs, drops and earlier trial mobs
DISTANCE = 8  # the mob is summoned about this far from the bot, in its sight
DAY = 6000  # noon: no natural spawns joining in
POLL = 5.0  # seconds per events long-poll
JOB_WAIT = 60.0  # the control API caps a job long-poll at this
SETTLE = 30.0  # seconds for a busy bot to go quiet before a trial (twice, with a stop)
GRACE = 3.0  # after a defence ends, this long for the guard to take it up again
FINAL = ("died", "stopped")  # defence ends nothing takes up again
# Every trial is fought on one arena: a bot further than ARENA_SLACK blocks across
# from it walks back first (one that drifted into a trench or onto ice between
# trials measured the terrain, not the weapon), for at most ARENA_WAIT seconds.
ARENA_SLACK = 4.0
ARENA_WAIT = 120.0
DONE = ("succeeded", "failed", "cancelled")
WEAPONS = ("sword", "axe", "spear", "bow", "fist")
# Undead that burn by day get a helmet so a day trial is a fight, not a sunburn.
BURNS = frozenset(
    "zombie skeleton stray bogged zombie_villager drowned phantom".split()
)
# An animal and the raw drop a player would ask for: the hunt runs through gather.
ANIMAL_DROPS = {
    "pig": "porkchop",
    "cow": "beef",
    "sheep": "mutton",
    "chicken": "chicken",
    "rabbit": "rabbit",
}
_NAME = re.compile(r"[a-z][a-z_]{0,31}")
STEP_SUMS = ("kills", "hits", "crits", "shots", "damage", "jumps", "ticks", "flees")


def kit(weapon: str, *, tier: str = "iron") -> list[dict]:
    """The give entries for a weapon: one of it, and arrows for a bow."""
    if weapon not in WEAPONS:
        raise ValueError(f"weapon must be one of {', '.join(WEAPONS)}")
    if weapon == "fist":
        return []
    if weapon == "bow":
        return [{"item": M + "bow", "count": 1}, {"item": M + "arrow", "count": 64}]
    return [{"item": f"{M}{tier}_{weapon}", "count": 1}]


MAX_GROUP = 8  # mobs of one kind in a group cell


def _mob(mob: str) -> str:
    if not isinstance(mob, str) or not _NAME.fullmatch(mob):
        raise ValueError("mob must be an entity name such as zombie")
    return mob


def group(cell: str) -> list[str]:
    """The mobs of a matrix cell: ``zombie``, ``zombie*3``, ``zombie*2+skeleton``."""
    out = []
    for part in cell.split("+"):
        name, star, count = part.partition("*")
        count = count if star else "1"
        n = int(count) if re.fullmatch(r"\d", count) else 0
        if not 1 <= n <= MAX_GROUP:
            raise ValueError(f"a group is mob or mob*N (N 1..{MAX_GROUP}): {cell!r}")
        out += [_mob(name)] * n
    return out


def scene(
    weapon: str,
    mob: str,
    *,
    tier: str = "iron",
    distance: int = DISTANCE,
    time: int = DAY,
) -> dict:
    """The trial op's request for one weapon against one mob or a group of them
    (``group``); each further mob of a group stands a block further off, so they
    come at the bot in a file rather than stacked in one cell."""
    mobs = group(mob)
    if len(mobs) > 1 and any(m in ANIMAL_DROPS for m in mobs):
        raise ValueError(f"an animal is hunted alone: {mob!r}")
    return {
        "surface": True,
        "clear": CLEAR,
        "clear_inventory": True,
        "heal": True,
        "time": time,
        "give": kit(weapon, tier=tier),
        # the trial op picks a cell about this far off, in sight and level with the bot
        "summon": [
            {"type": M + m, "distance": distance + i, "helmet": m in BURNS}
            for i, m in enumerate(mobs)
        ],
    }


def summarize(events: list[dict]) -> dict:
    """The defence a trial saw: its actions in order and the steps' totals."""
    actions, ends, failures = [], [], []
    totals = dict.fromkeys(STEP_SUMS, 0)
    steps, health, closest = 0, None, None
    for event in events:
        data = event.get("data") or {}
        if event.get("type") == "defense":
            actions.append(data.get("action"))
        elif event.get("type") == "defense_step":
            steps += 1
            for key in STEP_SUMS:
                value = data.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[key] += value
            if data.get("health") is not None:
                health = data["health"]
            near = data.get("closest")  # the nearest a fight let its target come
            if isinstance(near, (int, float)) and not isinstance(near, bool):
                closest = near if closest is None else min(closest, near)
            if data.get("end") is not None:
                ends.append(data["end"])
            if data.get("ok") is False:
                failures.append(f"{data.get('action')}: {data.get('reason')}")
    return {
        "actions": actions,
        "steps": steps,
        **totals,
        "closest": closest,
        "health": health,
        "ends": ends,
        "failures": failures,
    }


def _stop(client) -> None:
    """A trial past its time leaves nothing running into the next."""
    try:
        client.call("stop", {}, source="api")
    except Exception:  # noqa: BLE001 - the next scene clears what is left anyway
        pass


def _wait(client, job_id: str, deadline: float, clock):
    """The job once it is done, None when it vanished, or "timeout" (stopped)."""
    while True:
        left = deadline - clock()
        job = client.wait_job(job_id, min(JOB_WAIT, max(left, 0)))
        if job is None or job.get("state") in DONE:
            return job
        if clock() >= deadline:
            _stop(client)
            return "timeout"


def _position(client):
    reply = client.call("status", {}, source="api")
    state = reply.get("result") if reply.get("ok") else None
    where = state.get("position") if isinstance(state, dict) else None
    return where if isinstance(where, (list, tuple)) and len(where) == 3 else None


def parse_arena(text: str) -> tuple[int, int, int]:
    """``X,Y,Z`` (whole blocks) as the operator writes it on the command line."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3 or not all(re.fullmatch(r"-?\d+", p) for p in parts):
        raise ValueError(f"arena must be X,Y,Z in whole blocks, got {text!r}")
    return tuple(int(p) for p in parts)


def locate(client) -> tuple[int, int, int] | None:
    """The block the bot stands in (the default arena), or None when unknown."""
    where = _position(client)
    return None if where is None else tuple(math.floor(v) for v in where)


def home(client, arena, clock=time.monotonic) -> dict:
    """Walk a bot that drifted off the arena back to it (``goto``). Returns how far
    across it was and the end: "there", the job's state, "timeout", "refused",
    "lost_job" or "unknown" (no position)."""
    where = _position(client)
    if where is None:
        return {"from": None, "end": "unknown"}
    away = math.hypot(where[0] - (arena[0] + 0.5), where[2] - (arena[2] + 0.5))
    if away <= ARENA_SLACK:
        return {"from": away, "end": "there"}
    reply = client.call("goto", dict(zip("xyz", arena, strict=True)), source="api")
    if not reply.get("ok"):
        return {"from": away, "end": "refused", "error": reply.get("error")}
    job = _wait(client, reply["result"]["job"]["id"], clock() + ARENA_WAIT, clock)
    if job == "timeout":
        return {"from": away, "end": "timeout"}
    return {"from": away, "end": "lost_job" if job is None else job["state"]}


def _hunt(client, mob: str, started: float, timeout: float, clock) -> dict:
    item = M + ANIMAL_DROPS[mob]
    reply = client.call("gather", {"item": item, "count": 1}, source="api")
    if not reply.get("ok"):
        return {"end": "refused", "killed": False, "error": reply.get("error")}
    job = _wait(client, reply["result"]["job"]["id"], started + timeout, clock)
    if job == "timeout":
        return {"end": "timeout", "killed": False, "seconds": clock() - started}
    if job is None:
        return {"end": "lost_job", "killed": False}
    begun, finished = job.get("started_at"), job.get("finished_at")
    seconds = (
        finished - begun
        if isinstance(begun, (int, float)) and isinstance(finished, (int, float))
        else clock() - started
    )
    return {
        "end": job["state"],
        "killed": job["state"] == "succeeded",
        "seconds": seconds,
        "job": {k: job.get(k) for k in ("state", "result", "error")},
    }


def _quiet(state: dict) -> tuple[bool, float]:
    """Whether the bot is idle with its guard awake, and how long a rest has left."""
    defense = state.get("defense") or {}
    resting = defense.get("resting") or 0.0
    busy = bool(state.get("job") or state.get("queue") or defense.get("active"))
    return not busy and resting <= 0, resting


def settle(client, clock=time.monotonic) -> tuple[bool, float]:
    """Wait for an idle bot whose guard is awake, so a trial never inherits the
    last one's job, fight or rest. A bot still busy after ``SETTLE`` seconds is
    stopped once and given ``SETTLE`` more. Returns (settled, seconds)."""
    start = clock()
    deadline, stopped = start + SETTLE, False
    after = int(client.events(0, wait=0).get("latest", 0))
    while True:
        reply = client.call("status", {}, source="api")
        state = reply.get("result") if reply.get("ok") else None
        quiet, resting = _quiet(state) if isinstance(state, dict) else (False, 0.0)
        now = clock()
        if quiet:
            return True, now - start
        if now >= deadline:
            if stopped:
                return False, now - start
            _stop(client)
            deadline, stopped = now + SETTLE, True
            continue
        wait = min(POLL, deadline - now)
        if resting > 0 and state.get("job") is None and not state.get("queue"):
            wait = min(wait, resting)
        # a job or a fight ending is an event: the long-poll wakes for it
        after = int(client.events(after, wait=wait).get("latest", after))


def _defend(
    client, after: int, started: float, timeout: float, clock, need: int = 1
) -> dict:
    """Follow the defence this trial set off until ``need`` kills (every mob of
    the scene), a death or stop, or an end the guard does not take up again within
    ``GRACE`` (a sniper's target walking back into sight is the same fight, not a
    new trial). ``seconds`` is the time to the last kill, ``first_kill`` to the
    first."""
    deadline = started + timeout
    seen: list[dict] = []
    begun, ended, ended_at, killed_at = False, None, None, None
    kills, first = 0, None
    while killed_at is None and ended not in FINAL:
        now = clock()
        limit = deadline if ended is None else min(deadline, ended_at + GRACE)
        if now >= limit:
            break
        batch = client.events(after, wait=min(POLL, limit - now))
        for event in batch.get("events", ()):
            after = max(after, event.get("seq", after))
            kind, data = event.get("type"), event.get("data") or {}
            if kind == "defense":
                begun, ended = True, None
            elif not begun:
                continue  # the tail of a defence from before this trial
            seen.append(event)
            if kind == "defense_step" and (data.get("kills") or 0) > 0:
                kills += data["kills"]
                first = clock() if first is None else first
                if kills >= need:
                    killed_at = clock()
                    break
            if kind == "defense_end":
                ended, ended_at = data.get("end"), clock()
                if ended in FINAL:
                    break
    if killed_at is not None:
        end, at = "killed", killed_at
    elif ended is not None:
        end, at = f"defense_{ended}", ended_at
    else:
        end, at = "timeout", clock()
        _stop(client)
    return {"end": end, "killed": end == "killed", "seconds": at - started,
            "first_kill": None if first is None else first - started,
            **summarize(seen)}  # fmt: skip


def run_one(
    client, weapon: str, mob: str, timeout: float, clock=time.monotonic, arena=None
) -> dict:
    """Wait for a quiet bot, bring it back to the arena (when one is given), set
    the scene, let the bot act, and measure it."""
    spec = scene(weapon, mob)
    settled, waited = settle(client, clock)
    back = None
    if arena is not None:
        back = home(client, arena, clock)
        if back["end"] != "there" and back["from"] is not None:
            settled, more = settle(client, clock)  # the walk may have woken the guard
            waited += more
    after = int(client.events(0, wait=0).get("latest", 0))
    started = clock()
    reply = client.trial(spec)
    head = {"weapon": weapon, "mob": mob, "settled": settled, "settle": waited}
    if back is not None:
        head["arena"] = back
    if not reply.get("ok"):
        return {**head, "end": "scene_refused", "killed": False,
                "error": reply.get("error")}  # fmt: skip
    head["scene"] = reply.get("result")
    if mob in ANIMAL_DROPS:
        return {**head, **_hunt(client, mob, started, timeout, clock)}
    need = len(group(mob))
    return {**head, **_defend(client, after, started, timeout, clock, need)}


def run_matrix(
    client,
    weapons,
    mobs,
    *,
    out_dir: Path | str,
    timeout: float,
    clock=time.monotonic,
    run_one=run_one,
    echo=None,
    arena=None,
    repeat: int = 1,
    baseline: dict | None = None,
) -> Path:
    """Every weapon against every mob (or group), ``repeat`` times round the whole
    matrix (so drift over the run spreads over every cell), each on ``arena`` when
    given. The file is rewritten after each trial, with the per-cell ``summary``
    and the ``regressions`` against a ``baseline`` summary, so a stopped run keeps
    what it measured."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}.json"
    record = {"started": stamp, "timeout": timeout, "repeat": repeat,
              "arena": list(arena) if arena is not None else None,
              "summary": {}, "regressions": [], "trials": []}  # fmt: skip
    for n in range(1, repeat + 1):
        for weapon in weapons:
            for mob in mobs:
                row = run_one(client, weapon, mob, timeout, clock, arena=arena)
                record["trials"].append({**row, "repeat": n})
                record["summary"] = summary(record["trials"])
                if baseline is not None:
                    record["regressions"] = compare(record["summary"], baseline)
                path.write_text(json.dumps(record, indent=2), encoding="utf-8")
                if echo is not None:
                    echo(table([row], header=len(record["trials"]) == 1))
    return path


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _spread(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    p90 = ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]
    return {"median": statistics.median(ordered), "mean": statistics.fmean(ordered),
            "min": ordered[0], "max": ordered[-1], "p90": p90}  # fmt: skip


def _mean(rows: list[dict], key: str) -> float | None:
    values = [r[key] for r in rows if _number(r.get(key))]
    return statistics.fmean(values) if values else None


def summary(trials: list[dict]) -> dict:
    """Per ``weapon/mob`` cell: trials, kills and kill rate; the spread of the
    time to kill (kills only) and of the health left; mean hits, crits, shots and
    flees; the crit rate (crits over hits); and how the trials ended and failed."""
    cells: dict[str, list[dict]] = {}
    for row in trials:
        cells.setdefault(f"{row.get('weapon')}/{row.get('mob')}", []).append(row)
    out = {}
    for cell, rows in cells.items():
        killed = [r for r in rows if r.get("killed")]
        hits = sum(r["hits"] for r in rows if _number(r.get("hits")))
        crits = sum(r["crits"] for r in rows if _number(r.get("crits")))
        failures = Counter(f for r in rows for f in r.get("failures") or ())
        out[cell] = {
            "n": len(rows),
            "killed": len(killed),
            "kill_rate": len(killed) / len(rows),
            "seconds": _spread(
                [r["seconds"] for r in killed if _number(r.get("seconds"))]
            ),
            "health": _spread([r["health"] for r in rows if _number(r.get("health"))]),
            **{
                key: _mean(rows, key)
                for key in ("hits", "crits", "shots", "flees", "damage")
            },
            "crit_rate": crits / hits if hits else None,
            "ends": dict(Counter(str(r.get("end")) for r in rows)),
            "failures": dict(failures),
        }
    return out


# A cell is worse than its baseline past these: a kill rate this much lower, a
# median time to kill this much longer (by both the share and the seconds), or
# the lowest health left this much lower.
WORSE_KILL_RATE = 0.2
WORSE_SECONDS = (0.25, 1.0)
WORSE_HEALTH = 4.0


def compare(now: dict, base: dict) -> list[dict]:
    """The cells of ``now`` worse than in ``base`` (both ``summary`` results)."""
    out = []

    def flag(cell, metric, was, is_):
        out.append({"cell": cell, "metric": metric, "was": was, "now": is_})

    for cell, c in now.items():
        b = base.get(cell)
        if b is None:
            continue
        if b["kill_rate"] - c["kill_rate"] >= WORSE_KILL_RATE:
            flag(cell, "kill_rate", b["kill_rate"], c["kill_rate"])
        if c["seconds"] and b["seconds"]:
            was, is_ = b["seconds"]["median"], c["seconds"]["median"]
            share, seconds = WORSE_SECONDS
            if is_ > was * (1 + share) and is_ - was > seconds:
                flag(cell, "seconds.median", was, is_)
        if c["health"] and b["health"]:
            was, is_ = b["health"]["min"], c["health"]["min"]
            if was - is_ >= WORSE_HEALTH:
                flag(cell, "health.min", was, is_)
    return out


SUMMARY_COLUMNS = (
    ("cell", 22), ("n", 3), ("kill%", 5), ("med_s", 6), ("p90_s", 6),
    ("min_hp", 6), ("hits", 5), ("crit%", 5), ("shots", 5), ("flees", 5),
)  # fmt: skip


def summary_table(cells: dict) -> str:
    """A plain-text table, one row per cell of a ``summary``."""

    def pct(v):
        return "-" if v is None else f"{100 * v:.0f}"

    def num(v):
        return "-" if v is None else f"{v:.1f}"

    lines = [" ".join(n.ljust(w) for n, w in SUMMARY_COLUMNS),
             " ".join("-" * w for _, w in SUMMARY_COLUMNS)]  # fmt: skip
    for cell, c in cells.items():
        s, h = c["seconds"] or {}, c["health"] or {}
        values = (cell, str(c["n"]), pct(c["kill_rate"]), num(s.get("median")),
                  num(s.get("p90")), num(h.get("min")), num(c["hits"]),
                  pct(c["crit_rate"]), num(c["shots"]), num(c["flees"]))  # fmt: skip
        cols = zip(values, SUMMARY_COLUMNS, strict=True)
        lines.append(" ".join(v.ljust(w) for v, (_, w) in cols))
    return "\n".join(lines)


COLUMNS = (
    ("weapon", 6), ("mob", 9), ("end", 15), ("actions", 22), ("kills", 5),
    ("hits", 4), ("crits", 5), ("shots", 5), ("damage", 6), ("health", 6),
    ("flees", 5), ("closest", 7), ("seconds", 7),
)  # fmt: skip


def _cell(row: dict, key: str) -> str:
    value = row.get(key)
    if value is None:
        return "-"
    if isinstance(value, list):
        return ",".join(str(v) for v in value) or "-"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def table(rows: list[dict], *, header: bool = True) -> str:
    """A plain-text table, one row per trial."""
    lines = []
    if header:
        lines.append(" ".join(name.ljust(width) for name, width in COLUMNS))
        lines.append(" ".join("-" * width for _, width in COLUMNS))
    for row in rows:
        lines.append(" ".join(_cell(row, name).ljust(width) for name, width in COLUMNS))
    return "\n".join(lines)
