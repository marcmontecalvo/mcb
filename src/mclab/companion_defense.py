"""Auto-defence: what the companion does about hostile mobs (pure decisions).

Fight when armed and healthy (beside an ally when one is near), fetch a weapon from
the base, stay with an ally, go home, or burrow into a sealed hole; at or below the
health floor never fight. Creepers and wardens are avoided, never fought. The host's
guard calls ``decide`` with the latest observation and carries the action out.
Docs: COMPANION_DEFENSE_PLAN_2026-11-23.md.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields, replace

from mclab.profiles import HOSTILE

M = "minecraft:"
AVOID = frozenset({M + "creeper", M + "warden"})  # never in melee
PLAYER = M + "player"
THREAT_DY = 6.0  # hostiles further above or below are in another cave or tunnel
ALLY_LEASH = 6.0  # a fight beside an ally stays this close to them
TIERS = ("wooden", "golden", "stone", "copper", "iron", "diamond", "netherite")
# Best first: any sword beats any axe (axes swing slowly), then by tier.
WEAPONS = tuple(
    M + f"{tier}_{kind}" for kind in ("sword", "axe") for tier in reversed(TIERS)
)

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


@dataclass(frozen=True)
class Action:
    """``kind``: none, fight, avoid, arm, regroup, shelter, burrow or flee."""

    kind: str
    reason: str = ""
    target: str | None = None  # fight: the entity uuid first engaged
    item: str | None = None  # fight: the weapon; arm: the weapon to fetch
    where: tuple | None = None  # arm: the container; regroup: the ally; shelter: base
    anchor: tuple | None = None  # fight: stay within `leash` of this (an ally)
    leash: float | None = None
    threats: tuple[Threat, ...] = field(default=())


def _dist(a, b) -> float:
    return math.dist(tuple(a), tuple(b))


def threats(entities, position, policy: DefensePolicy) -> list[Threat]:
    """Hostiles within the threat radius and roughly level with us, nearest first."""
    out = []
    for e in entities:
        at = e.get("position") or ()
        if e.get("type") not in HOSTILE or len(at) != 3:
            continue
        if abs(at[1] - position[1]) > THREAT_DY:
            continue
        d = _dist(at, position)
        if d <= policy.threat_radius:
            out.append(Threat(str(e.get("uuid")), e["type"], tuple(at), d))
    return sorted(out, key=lambda t: t.distance)


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


def decide(s: Situation, policy: DefensePolicy) -> Action:
    seen = tuple(threats(s.entities, s.position, policy))
    if not seen:
        return Action("none", "no threat near")
    fightable = [t for t in seen if t.type not in AVOID]
    friends = allies(s.entities, s.name, s.position, policy)
    ally = friends[0] if friends else None
    base = (
        s.base
        if s.base is not None and _dist(s.base, s.position) <= policy.base_radius
        else None
    )
    low = s.health <= policy.floor_health

    if low:
        if ally is not None:  # an ally can fight for us: stay with them
            return Action(
                "regroup", f"hurt: staying with {ally.name}", where=ally.position,
                threats=seen,
            )  # fmt: skip
        if base is not None:
            return Action("shelter", "hurt: going home", where=base, threats=seen)
        if s.can_burrow:
            return Action("burrow", "hurt: hiding in a hole", threats=seen)
        return Action("flee", "hurt and nowhere to hide: running", threats=seen)

    if not fightable:
        return Action("avoid", f"keeping away from the {_name(seen[0])}", threats=seen)

    weapon = best_weapon(s.inventory)
    if weapon is not None:
        target = fightable[0]
        return Action(
            "fight",
            f"fighting the {_name(target)}"
            + (f" beside {ally.name}" if ally is not None else ""),
            target=target.uuid,
            item=weapon,
            anchor=ally.position if ally is not None else None,
            leash=ALLY_LEASH if ally is not None else None,
            threats=seen,
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
                "arm", f"fetching the {_short(item)} from the base", item=item,
                where=tuple(pos), threats=seen,
            )  # fmt: skip
    if ally is not None:
        return Action(
            "regroup", f"no weapon: staying with {ally.name}", where=ally.position,
            threats=seen,
        )  # fmt: skip
    if base is not None:
        return Action("shelter", "no weapon: going home", where=base, threats=seen)
    if s.can_burrow:
        return Action("burrow", "no weapon: hiding in a hole", threats=seen)
    return Action("flee", "no weapon and nowhere to hide: running", threats=seen)


def _short(item: str) -> str:
    return item.removeprefix(M).replace("_", " ")


def _name(threat: Threat) -> str:
    return _short(threat.type)
