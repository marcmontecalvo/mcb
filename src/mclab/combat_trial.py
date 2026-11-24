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
# A trial's mobs left alive (an escape, a timeout, a death) are cleared at once and
# again before a walk back: live, a skeleton a spear escaped from shot the bot dead
# on its way to the next scene. The body clears trial mobs anywhere loaded.
CLEANUP = 48
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
# The parts a kit is made of, in the dashboard's order: part -> (label, group, what
# it is). A new part is one entry here and its give entries in ``kit``; the dashboard
# offers it at once. (A mace waits on the bot fighting with one: CombatLogic.kind.)
KIT_GROUPS = ("Weapons", "Protection", "Supplies")
KIT_PARTS = {
    "sword": ("Sword", "Weapons", "Quick hits; a jump makes a critical hit."),
    "axe": ("Axe", "Weapons", "Slow, heavy hits."),
    "spear": ("Spear", "Weapons", "Long reach, with a charge attack."),
    "bow": ("Bow and 64 arrows", "Weapons", "Shoots from range."),
    "trident": ("Trident", "Weapons", "Thrown or held; Loyalty III brings it back."),
    "shield": ("Shield", "Protection", "Blocks hits and arrows."),
    "armor": ("Armor", "Protection", "Helmet, chestplate, leggings and boots."),
    "food": ("Food", "Supplies", "16 cooked beef and 2 golden apples."),
    "potions": (
        "Potions",
        "Supplies",
        "Drinks healing, strength, swiftness, regeneration and fire resistance; "
        "splashes healing on itself; throws harming, weakness, slowness and poison.",
    ),
}  # fmt: skip
WEAPONS = (*KIT_PARTS, "fist")
ARMOR = ("helmet", "chestplate", "leggings", "boots")
# Materials, weakest first: the ones the bot's weapon table knows
# (companion_combat.WEAPONS) and the armor sets the game has.
WEAPON_TIERS = ("wooden", "stone", "copper", "golden", "iron", "diamond", "netherite")
ARMOR_TIERS = ("leather", "chainmail", "copper", "golden", "iron", "diamond",
               "netherite")  # fmt: skip
DEFAULT_TIER = "iron"
TIERED = ("sword", "axe", "spear")  # the parts made of the weapon material
FOOD = ((M + "cooked_beef", 16), (M + "golden_apple", 2))
# (item, potion, how many): what companion_consumables drinks, splashes on itself
# and throws. A potion never stacks, so each is a give entry of its own.
POTIONS = (
    ("potion", "strong_healing", 2),
    ("potion", "strength", 1),
    ("potion", "swiftness", 1),
    ("potion", "regeneration", 1),
    ("potion", "fire_resistance", 1),
    ("splash_potion", "strong_healing", 3),
    ("splash_potion", "strong_harming", 2),
    ("splash_potion", "weakness", 1),
    ("splash_potion", "slowness", 1),
    ("splash_potion", "poison", 1),
)
LOYALTY = 3  # a thrown trident flies back: Loyalty III, the quickest return
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
# The mobs the dashboard offers, by group (any entity name is accepted): the hostile
# ones a fight or an arena wave is made of, and the animals hunted for food.
ANIMALS = "Animals (hunted for food)"
MOBS = {
    **dict.fromkeys(
        ("zombie", "husk", "drowned", "skeleton", "stray", "bogged", "spider",
         "cave_spider", "creeper", "witch", "pillager", "vindicator"),
        "Hostile",
    ),
    **dict.fromkeys(ANIMAL_DROPS, ANIMALS),
}  # fmt: skip
_NAME = re.compile(r"[a-z][a-z_]{0,31}")
STEP_SUMS = ("kills", "hits", "crits", "shots", "damage", "jumps", "ticks", "flees",
             "raises", "block_ticks")  # fmt: skip
CHARGE_LOG = 8  # a fight's charge hits kept (the mod reports its first eight)


ALL = "all"  # every weapon the bot chooses between (a raid trial): sword+axe+spear+bow
ALL_PARTS = ("sword", "axe", "spear", "bow")


def kit(
    weapon: str, *, tier: str = DEFAULT_TIER, armor: str | None = None
) -> list[dict]:
    """The give entries for a kit: a weapon, or several parts joined by ``+``
    (``spear+sword``): one of each, arrows for a bow, Loyalty on a trident, a
    shield, ``armor`` (a full set), ``food`` (FOOD) and ``potions`` (POTIONS, one
    entry each). The fist is an empty kit of its own; ``all`` is every weapon at
    once (``all+armor+shield``). Swords, axes and spears are made of ``tier``
    (WEAPON_TIERS), the armor of ``armor`` (ARMOR_TIERS; default: ``tier``)."""
    parts = []
    for part in weapon.split("+"):
        parts += ALL_PARTS if part == ALL else [part]
    known = len(parts) == len(set(parts)) and all(p in WEAPONS for p in parts)
    if not known or (len(parts) > 1 and "fist" in parts):
        raise ValueError(
            f"weapon must be one of {', '.join(WEAPONS)} or {ALL}, or several "
            "joined by +"
        )
    armor = armor or tier
    if any(p in TIERED for p in parts) and tier not in WEAPON_TIERS:
        raise ValueError(f"weapon material must be one of {', '.join(WEAPON_TIERS)}")
    if "armor" in parts and armor not in ARMOR_TIERS:
        raise ValueError(f"armor material must be one of {', '.join(ARMOR_TIERS)}")
    out = []
    for part in parts:
        if part == "bow":
            out += [{"item": M + "bow", "count": 1}, {"item": M + "arrow", "count": 64}]
        elif part == "trident":
            out.append({"item": M + "trident", "count": 1,
                        "enchantments": {M + "loyalty": LOYALTY}})  # fmt: skip
        elif part == "shield":
            out.append({"item": M + "shield", "count": 1})
        elif part == "armor":
            out += [{"item": f"{M}{armor}_{piece}", "count": 1} for piece in ARMOR]
        elif part == "food":
            out += [{"item": item, "count": n} for item, n in FOOD]
        elif part == "potions":
            out += [
                {"item": M + item, "count": 1, "potion": M + potion}
                for item, potion, n in POTIONS
                for _ in range(n)
            ]
        elif part != "fist":
            out.append({"item": f"{M}{tier}_{part}", "count": 1})
    return out


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
    tier: str = DEFAULT_TIER,
    armor: str | None = None,
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
        "give": kit(weapon, tier=tier, armor=armor),
        # the kit's armor and shield go on before the summon: the session dresses
        # only an idle bot, and the summon sets the guard off at once
        "wear": True,
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
    lance: Counter = Counter()  # a spear charge's ticks per move, over its fights
    charge_log: list = []  # its first hits, fight by fight
    picks: Counter = Counter()  # fight steps per "target weapon style"
    kill_types: Counter = Counter()  # kills per mob type (an evoker's vexes apart)
    hurt: Counter = Counter()  # damage taken per source type, over the steps
    killed_by = None
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
            if isinstance(data.get("lance"), dict):
                lance.update({k: v for k, v in data["lance"].items() if _number(v)})
            if isinstance(data.get("charge_log"), list):
                charge_log += data["charge_log"][:CHARGE_LOG]
            if data.get("action") == "fight" and data.get("weapon") is not None:
                pick = (data.get("target_type"), data["weapon"], data.get("style"))
                picks[" ".join(_bare(p) for p in pick)] += 1
            kill_types.update(_typed_kills(data))
            if isinstance(data.get("hurt_by"), dict):
                hurt.update(
                    {_bare(k): v for k, v in data["hurt_by"].items() if _number(v)}
                )
            if data.get("killed_by") is not None:
                killed_by = _bare(data["killed_by"])
    out = {
        "actions": actions,
        "steps": steps,
        **totals,
        "closest": closest,
        "health": health,
        "ends": ends,
        "failures": failures,
    }
    if lance:
        out["lance"] = dict(sorted(lance.items()))
        out["charge_log"] = charge_log[: 2 * CHARGE_LOG]
    if picks:
        out["picks"] = dict(sorted(picks.items()))
    if kill_types:
        out["kill_types"] = dict(sorted(kill_types.items()))
    if hurt:
        out["hurt_by"] = {k: round(v, 1) for k, v in sorted(hurt.items())}
    if killed_by is not None:
        out["killed_by"] = killed_by
    return out


def _bare(name) -> str:
    """A type or item id without its namespace (``?`` when unknown)."""
    return "?" if name is None else str(name).removeprefix(M)


def _typed_kills(data: dict) -> Counter:
    """A step's kills by bare mob type (the body's ``kill_types``)."""
    types = data.get("kill_types")
    if not isinstance(types, dict):
        return Counter()
    return Counter({_bare(k): int(v) for k, v in types.items() if _number(v)})


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
    head = {"from": away, **_cleanup(client)}
    reply = client.call("goto", dict(zip("xyz", arena, strict=True)), source="api")
    if not reply.get("ok"):
        return {**head, "end": "refused", "error": reply.get("error")}
    job = _wait(client, reply["result"]["job"]["id"], clock() + ARENA_WAIT, clock)
    if job == "timeout":
        return {**head, "end": "timeout"}
    return {**head, "end": "lost_job" if job is None else job["state"]}


def _cleanup(client) -> dict:
    """Clear every trial mob still about: {"cleared": n}, or the refusal."""
    reply = client.trial({"clear": CLEANUP})
    if not reply.get("ok"):
        return {"error": reply.get("error")}
    return {"cleared": (reply.get("result") or {}).get("cleared", 0)}


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
    """Whether the bot is idle with its guard awake and its body in the world (not
    respawning after a lost fight), and how long a rest has left. The body's state
    is its last published snapshot, still "ready" while it lies dead: the session's
    state turns at the death."""
    defense = state.get("defense") or {}
    resting = defense.get("resting") or 0.0
    away = (
        state.get("body_state", "ready") != "ready"
        or state.get("session", "ready") != "ready"
    )
    busy = bool(state.get("job") or state.get("queue") or defense.get("active") or away)
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


def _all_down(need: Counter, got: Counter, untyped: int) -> bool:
    """Whether every mob of the scene is killed: each type's count, kills of other
    types (an evoker's vexes, a stray) aside; kills a body reported without their
    types stand in for any kind still short."""
    short = sum(max(0, n - got[kind]) for kind, n in need.items())
    return untyped >= short


def _defend(
    client, after: int, started: float, timeout: float, clock, need: Counter
) -> dict:
    """Follow the defence this trial set off until every mob of the scene is
    killed (``need``: count by bare type), a death or stop, or an end the guard
    does not take up again within ``GRACE`` (a sniper's target walking back into
    sight is the same fight, not a new trial). ``seconds`` is the time to the last
    kill, ``first_kill`` to the first."""
    deadline = started + timeout
    seen: list[dict] = []
    begun, ended, ended_at, killed_at = False, None, None, None
    got, untyped, first = Counter(), 0, None
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
                typed = _typed_kills(data)
                got.update(typed)
                untyped += max(0, int(data["kills"]) - sum(typed.values()))
                first = clock() if first is None else first
                if _all_down(need, got, untyped):
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
    client,
    weapon: str,
    mob: str,
    timeout: float,
    clock=time.monotonic,
    arena=None,
    gear: dict | None = None,
) -> dict:
    """Wait for a quiet bot, bring it back to the arena (when one is given), set
    the scene, let the bot act, and measure it. ``gear``: the kit's materials
    (``{"tier": ..., "armor": ...}``, as ``scene`` takes them)."""
    spec = scene(weapon, mob, **(gear or {}))
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
    head["armor"] = ((head["scene"] or {}).get("wear") or {}).get("armor")
    if mob in ANIMAL_DROPS:
        return {**head, **_hunt(client, mob, started, timeout, clock)}
    need = Counter(group(mob))
    row = {**head, **_defend(client, after, started, timeout, clock, need)}
    if not row["killed"]:
        row["cleanup"] = _cleanup(client)
    return row


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
    gear: dict | None = None,
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
              "gear": gear, "summary": {}, "regressions": [],
              "trials": []}  # fmt: skip
    for n in range(1, repeat + 1):
        for weapon in weapons:
            for mob in mobs:
                row = run_one(
                    client, weapon, mob, timeout, clock, arena=arena, gear=gear
                )
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
                for key in (
                    "hits", "crits", "shots", "flees", "damage", "raises", "armor"
                )  # fmt: skip
            },
            "crit_rate": crits / hits if hits else None,
            "ends": dict(Counter(str(r.get("end")) for r in rows)),
            "failures": dict(failures),
        }
        hurt: Counter = Counter()
        for r in rows:
            if isinstance(r.get("hurt_by"), dict):
                hurt.update({k: v for k, v in r["hurt_by"].items() if _number(v)})
        if hurt:  # per trial: the cell's sources of damage, side by side
            out[cell]["hurt_by"] = {k: v / len(rows) for k, v in sorted(hurt.items())}
        killers = Counter(r["killed_by"] for r in rows if r.get("killed_by"))
        if killers:
            out[cell]["killed_by"] = dict(killers)
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
