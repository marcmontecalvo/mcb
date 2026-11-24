"""Auto-defence: what the companion does about hostile mobs (pure decisions).

Fight when the held weapons suit the target (``companion_combat`` picks the tactic;
beside an ally when one is near), fetch a weapon from the base, stay with an ally, go
home, burrow into a sealed hole, or get away: a calm ``walk_away`` from a lone mob
that is not pressing, a sprinting ``escape`` otherwise. At or below the health floor
never fight. Wardens and bosses in view always mean escape. The host's guard calls
``decide`` with the latest observation and carries the action out.
Docs: COMPANION_DEFENSE_PLAN_2026-11-23.md, COMPANION_COMBAT_PLAN_2026-11-23.md.
"""

import math
from collections.abc import Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields, replace

from mclab import companion_combat as combat
from mclab.profiles import HOSTILE

M = "minecraft:"
PLAYER = M + "player"
THREAT_DY = 6.0  # hostiles further above or below are in another cave or tunnel
ALLY_LEASH = 6.0  # a fight beside an ally stays this close to them
ESCAPE_NEAR = 6.0  # a mob closer than this is pressing: escape, don't stroll
CREEPER = M + "creeper"
CREEPER_SWELL = 7.0  # a started swell runs down only past 7 blocks
WEAPONS = combat.MELEE_RANKED  # best first, by damage per second with crits
RANGED_STYLES = frozenset({"snipe"})  # tactics that reach a mob no walk gets to
# A bow-wielding mob shoots from 15 blocks: it is a threat that far, whatever the
# threat radius (live, a third skeleton shot from past 12 after the guard called a
# group fight clear).
ARCHERS = frozenset(M + n for n in ("skeleton", "stray", "bogged"))
ARCHER_RANGE = 16.0
FIGHT_RADIUS_MAX = 32  # the fight op's radius bound

# field: (low, high) inclusive
_BOUNDS = {
    "floor_health": (1, 19),
    "threat_radius": (2, 32),
    "ally_radius": (0, 64),
    "base_radius": (0, 256),
}


@dataclass(frozen=True)
class DefensePolicy:
    """The adjustable settings (``bot.json`` ``defense``; the ``defense`` intent)."""

    floor_health: int = 6  # three hearts: at or below, escape; never fight
    threat_radius: float = 12
    ally_radius: float = 32
    base_radius: float = 64

    def __post_init__(self):
        for name, (low, high) in _BOUNDS.items():
            value = getattr(self, name)
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(
                    f"defense {name} must be a number from {low} to {high}"
                )

    @classmethod
    def from_config(cls, raw: Mapping | None) -> "DefensePolicy":
        raw = dict(raw or {})
        unknown = raw.keys() - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown defense settings: {', '.join(sorted(unknown))}")
        return cls(**raw)

    def changed(self, **changes) -> "DefensePolicy":
        return replace(self, **changes)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Threat:
    uuid: str
    type: str
    position: tuple[float, float, float]
    distance: float


@dataclass(frozen=True)
class Ally:
    name: str
    position: tuple[float, float, float]
    distance: float


@dataclass(frozen=True)
class Situation:
    health: float
    position: tuple[float, float, float]
    entities: Iterable[Mapping]  # the observation's visible_entities
    inventory: Mapping[str, int]
    name: str  # the bot's own player name (not an ally)
    base: tuple | None = None  # the base place
    base_weapons: tuple = ()  # ((container pos, weapon item), ...) at the base
    can_burrow: bool = True  # the ground here can be dug and sealed
    # A hole is worth it only while waiting gets something: day (mobs burn, no new
    # ones) or health back. By day a healthy bot would leave at once and dig in again.
    night: bool = False
    can_heal: bool = True  # fed enough to regenerate, or food held
    # uuids a hand-to-hand fight found no way to (a ledge): fought only from range
    out_of_reach: Collection[str] = frozenset()


@dataclass(frozen=True)
class Action:
    """``kind``: none, fight, arm, regroup, shelter, burrow, walk_away or escape."""

    kind: str
    reason: str = ""
    target: str | None = None  # fight: the entity uuid first engaged
    item: str | None = None  # fight: the weapon; arm: the weapon to fetch
    where: tuple | None = None  # arm: the container; regroup: the ally; shelter: base
    anchor: tuple | None = None  # fight: stay within `leash` of this (an ally)
    leash: float | None = None
    threats: tuple[Threat, ...] = field(default=())
    tactic: combat.Tactic | None = None  # fight: how (companion_combat)


def _dist(a, b) -> float:
    return math.dist(tuple(a), tuple(b))


def threats(entities, position, policy: DefensePolicy) -> list[Threat]:
    """Hostiles within the threat radius (an archer: within its bow's range) and
    roughly level with us, nearest first."""
    out = []
    for e in entities:
        at = e.get("position") or ()
        if e.get("type") not in HOSTILE or len(at) != 3:
            continue
        if abs(at[1] - position[1]) > THREAT_DY:
            continue
        d = _dist(at, position)
        limit = policy.threat_radius
        if e["type"] in ARCHERS:
            limit = max(limit, ARCHER_RANGE)
        if d <= limit:
            out.append(Threat(str(e.get("uuid")), e["type"], tuple(at), d))
    return sorted(out, key=lambda t: t.distance)


def fight_radius(seen: Iterable[Threat], policy: DefensePolicy) -> int:
    """The radius a fight looks in: the threat radius, widened to the farthest
    threat seen, so a fight never ends "done" with an archer still shooting."""
    far = max((t.distance for t in seen), default=0.0)
    return min(FIGHT_RADIUS_MAX, math.ceil(max(policy.threat_radius, far)))


def allies(entities, name, position, policy: DefensePolicy) -> list[Ally]:
    """Other players (people and other bots) within the ally radius, nearest first."""
    out = []
    for e in entities:
        at = e.get("position") or ()
        if e.get("type") != PLAYER or e.get("name") == name or len(at) != 3:
            continue
        d = _dist(at, position)
        if d <= policy.ally_radius:
            out.append(Ally(str(e.get("name")), tuple(at), d))
    return sorted(out, key=lambda a: a.distance)


def best_weapon(inventory: Mapping[str, int]) -> str | None:
    return next((w for w in WEAPONS if inventory.get(w, 0) > 0), None)


def _pressed(s: Situation, seen: tuple[Threat, ...]) -> bool:
    """True when a calm walk would not do: escape at a sprint instead."""
    nearest = seen[0]
    return (
        s.night
        or len(seen) > 1
        or nearest.distance < ESCAPE_NEAR
        or nearest.type in combat.RANGED
        or (nearest.type == CREEPER and nearest.distance < CREEPER_SWELL)
    )


def decide(s: Situation, policy: DefensePolicy) -> Action:
    seen = tuple(threats(s.entities, s.position, policy))
    if not seen:
        return Action("none", "no threat near")
    friends = allies(s.entities, s.name, s.position, policy)
    ally = friends[0] if friends else None
    base = (
        s.base
        if s.base is not None and _dist(s.base, s.position) <= policy.base_radius
        else None
    )
    low = s.health <= policy.floor_health
    near = f"{_a(seen[0])} near"  # every reason says what the bot reacts to

    if low:
        hurt = f"hurt, {near}"
        if ally is not None:  # an ally can fight for us: stay with them
            return Action(
                "regroup", f"{hurt}: staying with {ally.name}", where=ally.position,
                threats=seen,
            )  # fmt: skip
        if base is not None:
            return Action("shelter", f"{hurt}: going home", where=base, threats=seen)
        if s.can_burrow and (s.night or s.can_heal):
            return Action("burrow", f"{hurt}: hiding in a hole", threats=seen)
        return Action("escape", f"{hurt} and nowhere to hide: running", threats=seen)

    boss = next((t for t in seen if combat.never_engaged(t.type)), None)
    if boss is not None:
        return Action("escape", f"getting away from {_a(boss)}", threats=seen)

    able = [
        (t, plan)
        for t in seen
        if (plan := combat.tactic(t.type, s.inventory)).style != "avoid"
        and (t.uuid not in s.out_of_reach or plan.style in RANGED_STYLES)
    ]
    if able:
        target, plan = min(able, key=lambda tp: (not combat.first(tp[0].type),))
        return Action(
            "fight",
            f"fighting the {_name(target)}"
            + (f" beside {ally.name}" if ally is not None else ""),
            target=target.uuid,
            item=plan.weapon,
            anchor=ally.position if ally is not None else None,
            leash=ALLY_LEASH if ally is not None else None,
            threats=seen,
            tactic=plan,
        )
    if base is not None:
        stocked = [
            (pos, item)
            for pos, item in s.base_weapons
            if _dist(pos, s.position) <= policy.base_radius
        ]
        if stocked:
            pos, item = min(
                stocked, key=lambda c: (WEAPONS.index(c[1]), _dist(c[0], s.position))
            )
            return Action(
                "arm", f"{near}: fetching the {_short(item)} from the base",
                item=item, where=tuple(pos), threats=seen,
            )  # fmt: skip
    if seen[0].uuid in s.out_of_reach:
        unarmed = f"{near} but out of reach"
    else:
        unarmed = f"{near} and no weapon" + (
            " for it" if best_weapon(s.inventory) else ""
        )
    if ally is not None:
        return Action(
            "regroup", f"{unarmed}: staying with {ally.name}", where=ally.position,
            threats=seen,
        )  # fmt: skip
    if base is not None:
        return Action("shelter", f"{unarmed}: going home", where=base, threats=seen)
    pressed = _pressed(s, seen)
    if not s.night:
        if pressed:
            return Action("escape", f"{unarmed}: running", threats=seen)
        return Action("walk_away", f"{unarmed}: walking away", threats=seen)
    if s.can_burrow and not (seen[0].type == CREEPER and pressed):
        return Action("burrow", f"{unarmed}: hiding in a hole", threats=seen)
    return Action("escape", f"{unarmed} and nowhere to hide: running", threats=seen)


def _short(item: str) -> str:
    return item.removeprefix(M).replace("_", " ")


def _name(threat: Threat) -> str:
    return _short(threat.type)


def _a(threat: Threat) -> str:
    name = _name(threat)
    return ("an " if name[:1] in "aeiou" else "a ") + name
