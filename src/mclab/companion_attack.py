"""An owner-commanded attack: which entity is the target, and what ends the job.

Pure rules over the observation's ``visible_entities`` and the body's ``/players``
roster (every online player: name, position, dimension, game mode, invulnerable).
The session's ``attack`` job loops: find the target, close in, fight with the
tactic ``companion_combat`` picks, find it again.
Docs: COMPANION_COMBAT_PLAN_2026-11-23.md ("Commanded attack").
"""

import math
from dataclasses import dataclass, replace

from mclab import companion_combat as combat
from mclab.companion_defense import PLAYER
from mclab.profiles import HOSTILE

LOST = "lost"  # logged out, in another dimension, or nothing of that type in sight
INVULNERABLE = "invulnerable"  # creative, spectator or invulnerable: it cannot be hurt
KILLED = "killed"
LOW_HEALTH = "low_health"  # the health floor: the fight ends, the guard takes over

UNHURTABLE_MODES = frozenset({"creative", "spectator"})


@dataclass(frozen=True)
class Victim:
    uuid: str | None  # None: a player out of sight, known only from the roster
    type: str
    position: tuple[float, float, float]
    name: str | None = None
    in_sight: bool = True  # False: an arena's wave mob the body does not see

    @property
    def seen(self) -> bool:
        return self.uuid is not None and self.in_sight

    @property
    def label(self) -> str:
        return self.name or self.type.removeprefix("minecraft:")


def hurtable(entry: dict) -> bool:
    return entry.get("mode") not in UNHURTABLE_MODES and not entry.get("invulnerable")


def _entry(roster, name: str) -> dict | None:
    wanted = name.lower()
    return next(
        (e for e in roster or () if str(e.get("name", "")).lower() == wanted), None
    )


def _position(row) -> tuple | None:
    at = row.get("position") if isinstance(row, dict) else None
    if isinstance(at, (list, tuple)) and len(at) == 3:
        return tuple(float(v) for v in at)
    return None


def _nearest(entities, here, wanted) -> Victim | None:
    best, best_distance = None, math.inf
    for row in entities or ():
        at = _position(row)
        if at is None or not wanted(row):
            continue
        distance = math.dist(at, here) if here is not None else 0.0
        if distance < best_distance:
            best = Victim(str(row.get("uuid")), row["type"], at, row.get("name"))
            best_distance = distance
    return best


def _player(name: str, state: dict, roster, bot: str):
    if name.lower() == bot.lower():
        return LOST  # the bot is never its own target
    me = _entry(roster, bot)
    entry = _entry(roster, name)
    if roster is not None and entry is None:
        return LOST  # logged out
    if entry is not None:
        if me is not None and me.get("dimension") != entry.get("dimension"):
            return LOST
        if not hurtable(entry):
            return INVULNERABLE
    wanted = name.lower()
    for row in state.get("visible_entities") or ():
        at = _position(row)
        if (
            row.get("type") == PLAYER
            and str(row.get("name", "")).lower() == wanted
            and at is not None
        ):
            return Victim(str(row.get("uuid")), PLAYER, at, row.get("name"))
    at = _position(entry)
    if at is None:
        return LOST
    return Victim(None, PLAYER, at, entry.get("name"))


def find(target: dict, state: dict, roster, bot: str, chosen: str | None = None):
    """The ``Victim`` to attack now, or LOST / INVULNERABLE. ``target`` is the
    validated one of ``{"player": name}``, ``{"mob": type}``, ``{"nearest_hostile":
    True}``. ``roster`` is the ``/players`` list, None when it could not be read.
    ``chosen``: the uuid picked for a mob or hostile target on an earlier look (the
    same one is followed, not whichever is nearest now)."""
    if "player" in target:
        return _player(target["player"], state, roster, bot)
    here = _position({"position": (state.get("body") or {}).get("position")})
    entities = state.get("visible_entities") or ()
    if chosen is not None:
        found = _nearest(entities, here, lambda row: str(row.get("uuid")) == chosen)
    elif "mob" in target:
        found = _nearest(entities, here, lambda row: row.get("type") == target["mob"])
    else:
        found = _nearest(
            entities,
            here,
            lambda row: row.get("type") in HOSTILE
            and not combat.never_engaged(row["type"]),
        )
    return found if found is not None else LOST


# --- an arena hunt (docs/ARENA_RUNTIME.md: arena_status) --------------------------

WAITING = "waiting"  # no foe is alive yet (between waves)
NO_MATCH = "no_match"  # the arena runs no match
MATCH_OVER = frozenset({"won", "lost", "stopped"})
MODES = frozenset({"waves", "teams", "race"})  # co-op, team against team, kill race


def foes(match: dict, bot: str | None) -> list[dict]:
    """A match's foes, as status rows: every placed wave mob, then, in the teams
    mode (the only one where players hurt players), each live participant of
    another team than the bot's. A bot on no team has no enemies among players."""
    rows = [m for m in match.get("wave_mobs") or () if _position(m) is not None]
    if match.get("mode") != "teams" or not bot:
        return rows
    people = match.get("participants") or ()
    me = next(
        (p for p in people if str(p.get("name", "")).lower() == bot.lower()), None
    )
    side = (me or {}).get("team")
    if not side:
        return rows
    for p in people:
        if p.get("alive") and p.get("team") and p["team"] != side:
            if _position(p) is not None:
                rows.append(
                    {
                        "uuid": p.get("uuid"),
                        "type": PLAYER,
                        "position": p["position"],
                        "name": p.get("name"),
                    }
                )
    return rows


def hunt(status: dict, state: dict, chosen: str | None = None, bot: str | None = None):
    """The foe to fight now (a ``Victim``), WAITING, NO_MATCH, or how the match ended
    (won, lost, stopped). Every live wave mob counts, wherever it is in the arena
    (a waves match is won only when all are dead), and in the teams mode every
    live enemy of ``bot``'s team. The foe already chosen is kept while it lives;
    else the nearest. Where the body sees it, its own sighting is used (fresher
    than the status); ``seen`` is then only how the job reports it, since the
    fight op tracks a foe in reach by uuid either way."""
    match = status.get("match")
    if not isinstance(match, dict) or match.get("mode") not in MODES:
        return NO_MATCH
    if match.get("state") in MATCH_OVER:
        return match["state"]
    if match.get("state") != "running":
        return NO_MATCH
    mobs = foes(match, bot)
    if not mobs:
        return WAITING
    here = _position({"position": (state.get("body") or {}).get("position")})
    pick = _threat(mobs, here, chosen)
    sighted = _nearest(
        state.get("visible_entities") or (),
        here,
        lambda row: str(row.get("uuid")) == pick.uuid,
    )
    return sighted or replace(pick, in_sight=False)


# Casters first: what they do goes on all the while they live (an evoker's vexes and
# fangs, a witch's harming and poison and the heals she throws her side). A hunt
# weighs a mob's distance by this, so a witch at 12 comes before a pillager at 8 and a
# vindicator already at 2 before an evoker at 15. Vault raid 20261124T044539Z: three
# deaths in wave 4 sniping the nearest pillager, the witches' splashes and poison 100
# of the damage taken.
THREAT = {"minecraft:evoker": 3.0, "minecraft:witch": 2.5}


def _threat(mobs, here, chosen: str | None) -> Victim:
    """The wave mob to fight: the least distance / THREAT weight. The one already
    chosen is kept while it lives, unless a mob of greater weight comes out ahead."""

    def cost(row):
        far = math.dist(_position(row), here) if here is not None else 0.0
        return far / THREAT.get(row.get("type"), 1.0)

    best = min(mobs, key=cost)
    kept = next((m for m in mobs if str(m.get("uuid")) == chosen), None)
    if kept is not None and THREAT.get(best.get("type"), 1.0) <= THREAT.get(
        kept.get("type"), 1.0
    ):
        best = kept
    uuid = str(best.get("uuid"))
    return Victim(uuid, best["type"], _position(best), best.get("name"))


def arena_here(listing: dict, position) -> str | None:
    """The name of the arena whose bounds hold ``position`` (``arena_list``)."""
    cell = [math.floor(v) for v in position]
    for arena in listing.get("arenas") or ():
        box = arena.get("bounds") or {}
        lo, hi = box.get("min"), box.get("max")
        if lo and hi and all(a <= c <= b for a, c, b in zip(lo, cell, hi, strict=True)):
            return arena.get("name")
    return None


@dataclass
class Tally:
    """What an attack job did, across the fights (and defences) it was split into."""

    job: str | None = None
    kills: int = 0
    hits: int = 0
    crits: int = 0
    damage: float = 0.0
    fights: int = 0

    def add(self, data: dict) -> None:
        self.fights += 1
        self.kills += int(data.get("kills") or 0)
        self.hits += int(data.get("hits") or 0)
        self.crits += int(data.get("crits") or 0)
        self.damage = round(self.damage + float(data.get("damage") or 0.0), 1)

    @staticmethod
    def for_job(previous, job: str | None) -> "Tally":
        """The tally of ``job``: the one already running, or a fresh one."""
        if previous is not None and previous.job == job:
            return previous
        return Tally(job=job)

    def result(self, outcome: str, target: str) -> dict:
        return {
            "outcome": outcome,
            "target": target,
            "kills": self.kills,
            "hits": self.hits,
            "damage": self.damage,
            "crits": self.crits,
            "fights": self.fights,
        }
