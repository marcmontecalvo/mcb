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
import re
import time
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
STEP_SUMS = ("kills", "hits", "crits", "shots", "damage", "jumps", "ticks")


def kit(weapon: str, *, tier: str = "iron") -> list[dict]:
    """The give entries for a weapon: one of it, and arrows for a bow."""
    if weapon not in WEAPONS:
        raise ValueError(f"weapon must be one of {', '.join(WEAPONS)}")
    if weapon == "fist":
        return []
    if weapon == "bow":
        return [{"item": M + "bow", "count": 1}, {"item": M + "arrow", "count": 64}]
    return [{"item": f"{M}{tier}_{weapon}", "count": 1}]


def _mob(mob: str) -> str:
    if not isinstance(mob, str) or not _NAME.fullmatch(mob):
        raise ValueError("mob must be an entity name such as zombie")
    return mob


def scene(
    weapon: str,
    mob: str,
    *,
    tier: str = "iron",
    distance: int = DISTANCE,
    time: int = DAY,
) -> dict:
    """The trial op's request for one weapon against one mob."""
    mob = _mob(mob)
    return {
        "surface": True,
        "clear": CLEAR,
        "clear_inventory": True,
        "heal": True,
        "time": time,
        "give": kit(weapon, tier=tier),
        # the trial op picks a cell about this far off, in sight and level with the bot
        "summon": [{"type": M + mob, "distance": distance, "helmet": mob in BURNS}],
    }


def summarize(events: list[dict]) -> dict:
    """The defence a trial saw: its actions in order and the steps' totals."""
    actions, ends, failures = [], [], []
    totals = dict.fromkeys(STEP_SUMS, 0)
    steps, health = 0, None
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
            if data.get("end") is not None:
                ends.append(data["end"])
            if data.get("ok") is False:
                failures.append(f"{data.get('action')}: {data.get('reason')}")
    return {
        "actions": actions,
        "steps": steps,
        **totals,
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


def _hunt(client, mob: str, started: float, timeout: float, clock) -> dict:
    item = M + ANIMAL_DROPS[mob]
    reply = client.call("gather", {"item": item, "count": 1}, source="api")
    if not reply.get("ok"):
        return {"end": "refused", "killed": False, "error": reply.get("error")}
    job_id = reply["result"]["job"]["id"]
    deadline = started + timeout
    job = None
    while True:
        left = deadline - clock()
        job = client.wait_job(job_id, min(JOB_WAIT, max(left, 0)))
        if job is None or job.get("state") in ("succeeded", "failed", "cancelled"):
            break
        if clock() >= deadline:
            _stop(client)
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


def _defend(client, after: int, started: float, timeout: float, clock) -> dict:
    """Follow the defence this trial set off until a kill, a death or stop, or an
    end the guard does not take up again within ``GRACE`` (a sniper's target
    walking back into sight is the same fight, not a new trial)."""
    deadline = started + timeout
    seen: list[dict] = []
    begun, ended, ended_at, killed_at = False, None, None, None
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
            **summarize(seen)}  # fmt: skip


def run_one(
    client, weapon: str, mob: str, timeout: float, clock=time.monotonic
) -> dict:
    """Wait for a quiet bot, set the scene, let the bot act, and measure it."""
    spec = scene(weapon, mob)
    settled, waited = settle(client, clock)
    after = int(client.events(0, wait=0).get("latest", 0))
    started = clock()
    reply = client.trial(spec)
    head = {"weapon": weapon, "mob": mob, "settled": settled, "settle": waited}
    if not reply.get("ok"):
        return {**head, "end": "scene_refused", "killed": False,
                "error": reply.get("error")}  # fmt: skip
    head["scene"] = reply.get("result")
    if mob in ANIMAL_DROPS:
        return {**head, **_hunt(client, mob, started, timeout, clock)}
    return {**head, **_defend(client, after, started, timeout, clock)}


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
) -> Path:
    """Every weapon against every mob, in order; the file is rewritten after each
    trial so a stopped run keeps what it measured."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}.json"
    record = {"started": stamp, "timeout": timeout, "trials": []}
    for weapon in weapons:
        for mob in mobs:
            row = run_one(client, weapon, mob, timeout, clock)
            record["trials"].append(row)
            path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            if echo is not None:
                echo(table([row], header=not record["trials"][:-1]))
    return path


COLUMNS = (
    ("weapon", 6), ("mob", 9), ("end", 15), ("actions", 22), ("kills", 5),
    ("hits", 4), ("crits", 5), ("shots", 5), ("damage", 6), ("health", 6),
    ("seconds", 7),
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
