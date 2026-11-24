"""Food and potions: what the bot carries to heal, buff and throw, and the one use
that suits this moment. Pure (tests/test_companion_consumables.py); the session
carries the choice out with the body's ``eat``, ``drink`` and ``throw_potion`` ops
(docs/BODY_ACTION_OPS.md) and asks again after every use.

The order, first that applies wins:

1. **fire**: burning without fire resistance: splash it on itself, else drink it.
2. **heal** at ``HEAL_AT`` or below. With a foe close (``CLOSE``) there is no time to
   stand and drink for 1.6 s: a healing splash on itself (instant), a golden apple,
   then a drink. With foes farther off it drinks and keeps the splash. With no foe
   around (``QUIET``) food heals: it eats while the bar is short of full and spends
   a potion only when it cannot regenerate at all.
3. **hungry**: at ``SPRINT_FOOD`` it cannot sprint: it eats once no foe is close.
4. **throw** where the splash takes foes but never the bot: it breaks 5 to 5.25
   blocks off toward them (a thrown potion reaches about 5.5), past nearer foes and
   short of farther ones, so a group from about 2 to 9 blocks off can be splashed.
   Harming on the living (healing on the undead, a splash kept back for itself),
   weakness on melee brutes, slowness on a melee pack, poison on the living.
5. **buff**: strength for a group or a brute coming, swiftness against archers,
   fire resistance against fire mobs: drunk while they are still ``DRINK_CLEAR``
   away, else splashed on itself while none is within its splash.
6. **feed**: no foe around: eat the best plain food when hungry or hurt (a golden
   apple is kept for a fight).

``fight_floor`` is the health a fight op breaks off at so the bot can heal; a hunt
with nothing to heal with fights on at its own floor.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

M = "minecraft:"
POTION = M + "potion"
SPLASH = M + "splash_potion"
LINGERING = M + "lingering_potion"
POTION_ITEMS = frozenset({POTION, SPLASH, LINGERING})
THROWN = frozenset({SPLASH, LINGERING})
GOLDEN_APPLES = frozenset({M + "golden_apple", M + "enchanted_golden_apple"})

INSTANT_HEALTH = M + "instant_health"
INSTANT_DAMAGE = M + "instant_damage"
REGENERATION = M + "regeneration"
STRENGTH = M + "strength"
SPEED = M + "speed"
FIRE_RESISTANCE = M + "fire_resistance"
WEAKNESS = M + "weakness"
SLOWNESS = M + "slowness"
POISON = M + "poison"

HEAL_AT = 10  # health (5 hearts) at which a fight breaks off to heal
CLOSE = 6.0  # a foe this near leaves no time to stand and drink or eat
DRINK_CLEAR = 7.0  # a buff is drunk only while every foe is farther than this
QUIET = 16.0  # no foe this near: out of the fight, food does the healing
BUFF_RANGE = 16.0  # foes this near are coming: drink for them
SPLASH_RADIUS = 4.0  # a splash reaches an entity within 4 blocks of where it breaks
THROW_SAFE = 5.0  # a throw breaks at least this far off: its splash misses the bot
# A thrown potion (speed 0.5, gravity 0.05, drag 0.99, thrown 20 degrees above the
# look) reaches about 5.5 blocks on level ground (ThrowLogic): a throw breaks between
# THROW_SAFE and THROW_REACH toward the foes, past the near ones, short of the far.
THROW_REACH = 5.25
CLIP = 2.5  # a foe this near in the throw's direction would catch the potion
CLIP_ANGLE = 35.0  # degrees either side of the throw that count as its direction
FRIEND_CLEAR = SPLASH_RADIUS + 0.5  # a harmful splash breaks no nearer an ally
NEED = 1.2  # a throw is worth it at this much splash, summed over the foes it takes
NEED_BRUTE = 0.5  # or this much with a brute taken at BRUTE_WEIGHT or more
BRUTE_WEIGHT = 0.5
BUFF_LEFT = 200  # ticks: a buff with less than this left is taken again
EAT_BELOW = 14  # hungry (companion_body.EAT_BELOW)
REGEN_FOOD = 18  # health regenerates at or above this food level
FULL_FOOD = 20
SPRINT_FOOD = 6  # at or below, a player cannot sprint
HEAL_RESERVE = 1  # healing splashes kept for the bot before one is thrown at undead
MARK_MIN_S = 15.0  # a thrown effect is remembered on its targets at least this long

UNDEAD = frozenset(
    M + n
    for n in (
        "zombie", "husk", "drowned", "zombie_villager", "skeleton", "stray",
        "wither_skeleton", "bogged", "phantom", "zombified_piglin", "zoglin",
        "wither", "skeleton_horse", "zombie_horse",
    )
)  # fmt: skip
ARTHROPODS = frozenset(M + n for n in ("spider", "cave_spider"))  # poison-immune
MAGIC_RESISTANT = frozenset({M + "witch"})  # harming does a witch 15%
BRUTES = frozenset(
    M + n
    for n in ("vindicator", "ravager", "evoker", "piglin_brute", "wither_skeleton")
)
MELEE = frozenset(
    M + n
    for n in (
        "zombie", "husk", "drowned", "zombie_villager", "vindicator", "ravager",
        "piglin_brute", "wither_skeleton", "zombified_piglin", "spider",
        "cave_spider", "silverfish", "endermite", "hoglin", "zoglin", "vex",
        "slime", "magma_cube", "enderman", "piglin",
    )
)  # fmt: skip
ARCHERS = frozenset(
    M + n
    for n in (
        "pillager", "skeleton", "stray", "bogged", "witch", "blaze", "ghast",
        "illusioner", "breeze",
    )
)  # fmt: skip
FIRE_MOBS = frozenset(M + n for n in ("blaze", "ghast", "magma_cube", "wither"))


@dataclass(frozen=True)
class Effect:
    id: str
    amplifier: int = 0
    duration: int = 0  # ticks; -1 never wears off

    @classmethod
    def read(cls, row) -> Effect | None:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            return None
        return cls(row["id"], int(row.get("amplifier") or 0),
                   int(row.get("duration") or 0))  # fmt: skip


@dataclass(frozen=True)
class Held:
    """A stack the bot can eat, drink or throw."""

    slot: int
    item: str
    count: int
    potion: str | None = None
    effects: tuple[Effect, ...] = ()
    nutrition: float = 0.0

    def effect(self, effect_id: str) -> Effect | None:
        return next((e for e in self.effects if e.id == effect_id), None)


def _nutrition(facts) -> float:
    """A food's nutrition from the knowledge pack's facts (or a bare number)."""
    if isinstance(facts, Mapping):
        facts = facts.get("nutrition", 0)
    return float(facts) if isinstance(facts, (int, float)) else 0.0


def held(rows, foods: Mapping[str, Mapping]) -> tuple[Held, ...]:
    """The consumables among inventory rows: potions with contents, and foods the
    knowledge pack names (``foods``: item to facts with ``nutrition``)."""
    out = []
    for row in rows or ():
        item, count = row.get("item"), int(row.get("count") or 0)
        if not isinstance(item, str) or count < 1:
            continue
        slot = int(row.get("slot", -1))
        if item in POTION_ITEMS:
            effects = tuple(
                e for e in map(Effect.read, row.get("effects") or ()) if e is not None
            )
            if effects:
                out.append(Held(slot, item, count, row.get("potion"), effects))
        elif item in foods or item in GOLDEN_APPLES:
            nutrition = _nutrition(foods.get(item, 4))
            out.append(Held(slot, item, count, nutrition=nutrition))
    return tuple(sorted(out, key=lambda h: h.slot))


@dataclass(frozen=True)
class Body:
    health: float
    position: tuple[float, float, float]
    food: int = FULL_FOOD
    saturation: float = 0.0
    absorption: float = 0.0
    fire_ticks: int = 0
    effects: Mapping[str, Effect] = field(default_factory=dict)

    @classmethod
    def from_observation(cls, body: Mapping) -> Body:
        read = (Effect.read(row) for row in body.get("effects") or ())
        effects = {e.id: e for e in read if e is not None}
        return cls(
            health=float(body.get("health", 20.0)),
            position=tuple(float(v) for v in body.get("position") or (0, 0, 0)),
            food=int(body.get("food", FULL_FOOD)),
            saturation=float(body.get("saturation") or 0.0),
            absorption=float(body.get("absorption") or 0.0),
            fire_ticks=int(body.get("fire_ticks") or 0),
            effects=effects,
        )

    def active(self, effect_id: str, at_least: int = 0) -> bool:
        """The effect runs, with more than ``at_least`` ticks left."""
        e = self.effects.get(effect_id)
        return e is not None and (e.duration < 0 or e.duration > at_least)


@dataclass(frozen=True)
class Foe:
    uuid: str
    type: str
    position: tuple[float, float, float]
    distance: float = 0.0


def ranged(foes: Iterable[Foe], here) -> tuple[Foe, ...]:
    """The foes with their distance from ``here``, nearest first."""
    at = tuple(here)
    return tuple(
        sorted(
            (Foe(f.uuid, f.type, tuple(f.position), math.dist(at, f.position))
             for f in foes),  # fmt: skip
            key=lambda f: f.distance,
        )
    )


def foes_from(rows, here, kinds: Iterable[str] | None = None) -> tuple[Foe, ...]:
    """Foes from entity rows (``uuid``, ``type``, ``position``): the body's sightings
    or an arena's wave mobs. ``kinds`` keeps only those types."""
    keep = frozenset(kinds) if kinds is not None else None
    out = []
    for row in rows or ():
        at, kind = row.get("position"), row.get("type")
        if not isinstance(kind, str) or not at or len(at) != 3:
            continue
        if keep is not None and kind not in keep:
            continue
        out.append(Foe(str(row.get("uuid")), kind, tuple(float(v) for v in at)))
    return ranged(out, here)


@dataclass(frozen=True)
class Use:
    """One use: ``op`` eat, drink or throw; ``at`` (throw) "self" or a mob's uuid."""

    op: str
    item: str
    reason: str  # fire, heal, hungry, harm, weaken, slow, poison, buff, feed
    potion: str | None = None
    at: str | None = None
    effect: str | None = None
    targets: tuple[str, ...] = ()  # the foes a throw should reach
    duration: int = 0  # ticks the effect lasts (a throw's mark)
    point: tuple[float, float, float] | None = None  # a throw: where it breaks

    def fields(self) -> dict:
        """The body op's request fields."""
        if self.op == "eat":
            return {"item": self.item}
        if self.op == "drink":
            return {"potion": self.potion}
        if self.point is not None:
            at = {"position": [round(v, 2) for v in self.point]}
        else:
            at = "self" if self.at == "self" else {"entity": self.at}
        return {"potion": self.potion, "item": self.item, "at": at}

    def summary(self) -> dict:
        """How a job step reports it (``purpose``: the step's ``reason`` is the op's
        refusal)."""
        out = {"action": self.op, "purpose": self.reason, "item": self.item}
        if self.potion:
            out["potion"] = self.potion
        if self.op == "throw":
            out["at"] = self.at or "point"
            out["targets"] = len(self.targets)
        return out


class Marks:
    """Effects thrown on foes, remembered until they wear off (the body cannot see a
    mob's effects): the same foe is not weakened twice."""

    def __init__(self) -> None:
        self._until: dict[str, dict[str, float]] = {}

    def record(self, use: Use, now: float) -> None:
        if use.op != "throw" or use.effect is None:
            return
        until = now + max(MARK_MIN_S, use.duration / 20.0 * 0.5)
        for uuid in use.targets:
            self._until.setdefault(uuid, {})[use.effect] = until

    def active(self, now: float) -> dict[str, set[str]]:
        out = {}
        for uuid, effects in list(self._until.items()):
            live = {e: t for e, t in effects.items() if t > now}
            if live:
                self._until[uuid] = live
                out[uuid] = set(live)
            else:
                del self._until[uuid]
        return out


# --- choosing --------------------------------------------------------------------


def _with(items: Iterable[Held], effect_id: str, kinds: frozenset[str]) -> list[Held]:
    """Stacks of ``kinds`` carrying the effect, strongest first."""
    found = [h for h in items if h.item in kinds and h.effect(effect_id) is not None]
    return sorted(found, key=lambda h: (-h.effect(effect_id).amplifier, h.slot))


def _drink(items, effect_id: str, reason: str) -> Use | None:
    found = _with(items, effect_id, frozenset({POTION}))
    if not found:
        return None
    h = found[0]
    return Use("drink", h.item, reason, potion=h.potion, effect=effect_id,
               duration=h.effect(effect_id).duration)  # fmt: skip


def _splash_self(items, effect_id: str, reason: str) -> Use | None:
    found = _with(items, effect_id, frozenset({SPLASH}))
    if not found:
        return None
    h = found[0]
    return Use("throw", h.item, reason, potion=h.potion, at="self", effect=effect_id,
               duration=h.effect(effect_id).duration)  # fmt: skip


def _golden_apple(items, body: Body) -> Use | None:
    if body.active(REGENERATION) or body.absorption > 0:
        return None  # one is already working
    apples = [h for h in items if h.item in GOLDEN_APPLES]
    if not apples:
        return None
    best = max(apples, key=lambda h: (h.item == M + "enchanted_golden_apple", -h.slot))
    return Use("eat", best.item, "heal")


def _food(items) -> Held | None:
    plain = [h for h in items if h.potion is None and h.item not in GOLDEN_APPLES
             and h.item not in POTION_ITEMS]  # fmt: skip
    return max(plain, key=lambda h: (h.nutrition, -h.slot)) if plain else None


def _eat(items, reason: str) -> Use | None:
    h = _food(items)
    return Use("eat", h.item, reason) if h is not None else None


def _first(*options) -> Use | None:
    for option in options:
        use = option()
        if use is not None:
            return use
    return None


def _fire(body: Body, items) -> Use | None:
    if body.fire_ticks <= 0 or body.active(FIRE_RESISTANCE):
        return None
    return _first(
        lambda: _splash_self(items, FIRE_RESISTANCE, "fire"),
        lambda: _drink(items, FIRE_RESISTANCE, "fire"),
    )


def _regen_drink(items, body: Body) -> Use | None:
    return None if body.active(REGENERATION) else _drink(items, REGENERATION, "heal")


def _heal(body: Body, items, near: float) -> Use | None:
    if body.health > HEAL_AT:
        return None
    if near > QUIET:  # out of the fight: food heals, a potion only when it cannot
        if body.food < FULL_FOOD:
            fed = _eat(items, "feed")
            if fed is not None:
                return fed
        if body.food >= REGEN_FOOD:
            return None
        return _first(
            lambda: _drink(items, INSTANT_HEALTH, "heal"),
            lambda: _regen_drink(items, body),
            lambda: _golden_apple(items, body),
            lambda: _splash_self(items, INSTANT_HEALTH, "heal"),
        )
    if near < CLOSE:  # no time to stand and drink
        return _first(
            lambda: _splash_self(items, INSTANT_HEALTH, "heal"),
            lambda: _golden_apple(items, body),
            lambda: _drink(items, INSTANT_HEALTH, "heal"),
            lambda: _regen_drink(items, body),
            lambda: None if body.active(REGENERATION)
            else _splash_self(items, REGENERATION, "heal"),
        )  # fmt: skip
    return _first(
        lambda: _drink(items, INSTANT_HEALTH, "heal"),
        lambda: _golden_apple(items, body),
        lambda: _regen_drink(items, body),
        lambda: _splash_self(items, INSTANT_HEALTH, "heal"),
    )


def _living(f: Foe) -> bool:
    return f.type not in UNDEAD


# How each thrown effect scores the foes its splash reaches, the reason it is
# thrown for, and the score a throw needs (a brute at the centre needs only 1).
def _harm_score(f: Foe, marked: set[str]) -> int:
    if f.type in UNDEAD:
        return -2  # harming heals the undead
    return 0 if f.type in MAGIC_RESISTANT else 1


def _undead_score(f: Foe, marked: set[str]) -> int:
    return 1 if f.type in UNDEAD else -2  # healing harms the undead, heals the rest


def _weak_score(f: Foe, marked: set[str]) -> int:
    return 1 if f.type in MELEE and WEAKNESS not in marked else 0


def _slow_score(f: Foe, marked: set[str]) -> int:
    return 1 if f.type in MELEE and SLOWNESS not in marked else 0


def _poison_score(f: Foe, marked: set[str]) -> int:
    if f.type in UNDEAD or f.type in ARTHROPODS or POISON in marked:
        return 0
    return 1


THROWS = (  # effect, reason, score, alone: one brute well inside is worth it
    (INSTANT_DAMAGE, "harm", _harm_score, True),
    (INSTANT_HEALTH, "harm", _undead_score, True),
    (WEAKNESS, "weaken", _weak_score, True),
    (SLOWNESS, "slow", _slow_score, False),
    (POISON, "poison", _poison_score, False),
)


def _flat(a, b) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _points(here, foes: tuple[Foe, ...], friends) -> list[tuple[float, ...]]:
    """Where a throw may break: toward each foe, as near it as the arc reaches
    (``THROW_SAFE`` .. ``THROW_REACH`` from the bot, past a foe that is nearer),
    at its height. Never over a foe within ``CLIP`` in that direction (it would
    break on it, beside the bot), nor within ``FRIEND_CLEAR`` of a friend."""
    out = []
    for c in foes:
        flat = _flat(c.position, here)
        if flat < CLIP:
            continue
        ux, uz = (c.position[0] - here[0]) / flat, (c.position[2] - here[2]) / flat
        if any(_in_line(f, here, ux, uz) for f in foes):
            continue
        r = min(max(flat, THROW_SAFE), THROW_REACH)
        point = (here[0] + ux * r, c.position[1], here[2] + uz * r)
        if all(math.dist(point, p) > FRIEND_CLEAR for p in friends):
            out.append(point)
    return out


def _in_line(f: Foe, here, ux: float, uz: float) -> bool:
    flat = _flat(f.position, here)
    if flat >= CLIP:
        return False
    if flat < 1e-6:
        return True
    cos = ((f.position[0] - here[0]) * ux + (f.position[2] - here[2]) * uz) / flat
    return cos >= math.cos(math.radians(CLIP_ANGLE))


def _intensity(f: Foe, point) -> float:
    """How strongly a splash breaking at ``point`` takes the foe (1 - d/4)."""
    return max(0.0, 1.0 - math.dist(f.position, point) / SPLASH_RADIUS)


def _throw(items, foes: tuple[Foe, ...], marks, friends, here) -> Use | None:
    points = _points(here, foes, friends)
    if not points:
        return None
    for effect_id, reason, score, alone in THROWS:
        stacks = _with(items, effect_id, THROWN)
        if effect_id == INSTANT_HEALTH:  # keep a healing splash for the bot itself
            spare = sum(h.count for h in stacks if h.item == SPLASH) - HEAL_RESERVE
            stacks = [h for h in stacks if h.item != SPLASH or spare > 0]
        if not stacks:
            continue
        best = None
        for point in points:
            total, hit, brute = 0.0, [], False
            for f in foes:
                weight = _intensity(f, point)
                if weight <= 0:
                    continue
                value = score(f, set(marks.get(f.uuid, ())))
                total += value * weight
                if value > 0:
                    hit.append(f.uuid)
                    brute = brute or (f.type in BRUTES and weight >= BRUTE_WEIGHT)
            need = NEED_BRUTE if alone and brute else NEED
            if total >= need and (best is None or total > best[0]):
                best = (total, point, hit)
        if best is not None:
            h = stacks[0]
            return Use("throw", h.item, reason, potion=h.potion, point=best[1],
                       effect=effect_id, targets=tuple(best[2]),
                       duration=h.effect(effect_id).duration)  # fmt: skip
    return None


def _buff(body: Body, items, foes: tuple[Foe, ...]) -> Use | None:
    coming = [f for f in foes if f.distance <= BUFF_RANGE]
    if not coming:
        return None
    group = len(coming) >= 2
    wants = []
    if group or any(f.type in BRUTES for f in coming):
        wants.append(STRENGTH)
    if group and any(f.type in ARCHERS for f in coming):
        wants.append(SPEED)
    if any(f.type in FIRE_MOBS for f in coming):
        wants.append(FIRE_RESISTANCE)
    near = coming[0].distance
    for effect_id in wants:
        if body.active(effect_id, BUFF_LEFT):
            continue
        if near >= DRINK_CLEAR:
            use = _drink(items, effect_id, "buff")
            if use is not None:
                return use
        if near > SPLASH_RADIUS + 1:  # instant, and no foe shares the splash
            use = _splash_self(items, effect_id, "buff")
            if use is not None:
                return use
    return None


def decide(
    body: Body,
    items: tuple[Held, ...],
    foes: tuple[Foe, ...],
    marks: Mapping[str, Iterable[str]] | None = None,
    friends: Iterable = (),
) -> Use | None:
    """The use that suits this moment, or None. ``foes``: with their distances
    (``ranged``), nearest first; ``marks``: effects already thrown on foes by uuid;
    ``friends``: positions a harmful splash must not reach (players, allies)."""
    marks = marks or {}
    friends = [tuple(p) for p in friends]
    near = foes[0].distance if foes else math.inf
    return _first(
        lambda: _fire(body, items),
        lambda: _heal(body, items, near),
        lambda: _eat(items, "hungry")
        if body.food <= SPRINT_FOOD and near >= CLOSE
        else None,
        lambda: _throw(items, foes, marks, friends, body.position),
        lambda: _buff(body, items, foes),
        lambda: _eat(items, "feed")
        if near > QUIET
        and body.food < FULL_FOOD
        and (body.health < 20 or body.food < EAT_BELOW)
        else None,
    )


def can_heal(items: Iterable[Held]) -> bool:
    """Something held heals: a healing potion of any kind, regeneration, or a golden
    apple."""
    return any(
        h.item in GOLDEN_APPLES
        or (h.item in (POTION, SPLASH)
            and (h.effect(INSTANT_HEALTH) or h.effect(REGENERATION)))
        for h in items
    )  # fmt: skip


def fight_floor(body: Body, items: Iterable[Held], floor: int) -> int:
    """The health a fight op breaks off at: ``HEAL_AT`` while something held heals
    (kept under the health left, so the fight still runs), else ``floor``."""
    if not can_heal(items):
        return floor
    return max(floor, min(HEAL_AT, math.ceil(body.health) - 2), 0)
