"""Combat technique: which tactic and weapon for which target (pure).

Python picks the tactic; the mod's ``fight`` op carries it out tick by tick (crit
timing, cooldowns, aim). Weapon numbers are from the 26.3 bytecode
(docs/COMBAT_MECHANICS_26.3.md); the plan is docs/COMPANION_COMBAT_PLAN_2026-11-23.md.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

M = "minecraft:"
CRIT = 1.5  # base damage multiplier of a falling, non-sprinting strike
CRIT_CYCLE = 12  # ticks from one jump to the next: crits cannot come faster
# A spear's charge (the mod's lance; its jab is the mod's poke, no longer picked) hits
# 2.0 to 4.5 from the eye for 1 + floor(relative b/s x the tier's multiplier): a
# ground sprint at a still target is 5.6 b/s. A pass is the 10-tick contact cooldown
# and the closing in again after the knockback.
CHARGE_SPEED = 5.6
CHARGE_CYCLE = 20
SNIPE_KEEP = (8.0, 15.0)  # outside a skeleton's comfort, inside its 15-block range
# An animal never fights back: shoot it from wherever, closing in past 8 blocks
# (a panicked animal zigzags, and the nearer shot lands more often).
HUNT_KEEP = (0.0, 8.0)
ARROWS = frozenset({M + "arrow", M + "spectral_arrow", M + "tipped_arrow"})
SHIELD = M + "shield"
TRIDENT = M + "trident"
MELEE_KINDS = ("sword", "axe", "trident", "spear")
SWING_KINDS = ("sword", "axe", "trident")  # swung through Player.attack: they crit
# Max health of the animals hunted for drops (LivingEntity attributes, 26.3);
# an animal not listed is taken as a pig's.
ANIMAL_HEALTH = {
    M + n: float(hp)
    for n, hp in {
        "pig": 10, "cow": 10, "mooshroom": 10, "sheep": 8, "chicken": 4,
        "rabbit": 3, "goat": 10, "cod": 3, "salmon": 3, "tropical_fish": 3,
        "pufferfish": 3, "squid": 10, "glow_squid": 10, "turtle": 30,
        "frog": 10, "armadillo": 12, "camel": 32, "sniffer": 14,
    }.items()
}  # fmt: skip
DEFAULT_ANIMAL_HEALTH = 10.0
# Max health of the mobs fought in melee (26.3 attributes; a slime or magma cube is
# taken at its biggest common size); a mob not listed is taken as a zombie's.
MOB_HEALTH = {
    M + n: float(hp)
    for n, hp in {
        "zombie": 20, "husk": 20, "drowned": 20, "zombie_villager": 20,
        "spider": 16, "cave_spider": 12, "silverfish": 8, "endermite": 8,
        "slime": 16, "magma_cube": 16, "witch": 26, "vindicator": 24,
        "hoglin": 40, "zoglin": 40, "piglin_brute": 50, "wither_skeleton": 20,
        "breeze": 30, "vex": 14, "evoker": 24, "skeleton": 20, "stray": 20,
        "bogged": 16, "pillager": 24, "enderman": 40, "creeper": 20,
        "illusioner": 32, "ravager": 100,
        "player": 20,  # a commanded attack on a player (no armour counted)
    }.items()
}  # fmt: skip
DEFAULT_MOB_HEALTH = 20.0


@dataclass(frozen=True)
class Weapon:
    item: str
    kind: str  # sword, axe, spear, trident or bow
    damage: float  # per hit, the player's base 1 included; bow: a full-draw arrow
    cooldown: float  # ticks to full strength (bow: the full draw)
    charge: float = 0.0  # a spear's KineticWeapon damage multiplier

    @property
    def crits(self) -> bool:
        return self.kind in ("sword", "axe", "trident")

    def hit(self) -> float:
        """The hit of its best technique: a crit, or a spear's charge."""
        if self.charge:
            return 1 + math.floor(CHARGE_SPEED * self.charge)
        return self.damage * (CRIT if self.crits else 1)

    @property
    def wear(self) -> int:
        """Uses a hit spends (the Weapon component: axes 2, the rest 1)."""
        return 2 if self.kind == "axe" else 1

    def cycle(self) -> float:
        """Ticks between full-strength hits (a crit waits for its jump, a charge
        for its next pass)."""
        if self.charge:
            return CHARGE_CYCLE
        return max(self.cooldown, CRIT_CYCLE if self.crits else 1)

    def dps(self) -> float:
        """Damage per tick with the best technique (crits where they apply)."""
        return self.hit() / self.cycle()

    def hits_to_kill(self, health: float) -> int:
        return max(1, math.ceil(health / self.hit() - 1e-9))


def _table() -> dict[str, Weapon]:
    out = {}
    swords = {"wooden": 4, "golden": 4, "stone": 5, "copper": 5, "iron": 6}
    swords |= {"diamond": 7, "netherite": 8}
    for tier, damage in swords.items():
        out[f"{tier}_sword"] = ("sword", damage, 12.5)
    axes = {
        "wooden": (7, 0.8), "golden": (7, 1.0), "stone": (9, 0.8), "copper": (9, 0.8),
        "iron": (9, 0.9), "diamond": (9, 1.0), "netherite": (10, 1.0),
    }  # fmt: skip
    for tier, (damage, speed) in axes.items():
        out[f"{tier}_axe"] = ("axe", damage, 20 / speed)
    # jab damage, swing seconds, charge damage multiplier (COMBAT_MECHANICS section 4)
    spears = {
        "wooden": (1, 0.65, 0.7), "golden": (1, 0.95, 0.7), "stone": (2, 0.75, 0.82),
        "copper": (2, 0.85, 0.82), "iron": (3, 0.95, 0.95),
        "diamond": (4, 1.05, 1.075), "netherite": (5, 1.15, 1.2),
    }  # fmt: skip
    for tier, (damage, swing, charge) in spears.items():
        out[f"{tier}_spear"] = ("spear", damage, swing * 20, charge)
    out["trident"] = ("trident", 9, 20 / 1.1)
    out["bow"] = ("bow", 6, 20.0)
    return {M + k: Weapon(M + k, *v) for k, v in out.items()}


WEAPONS = _table()
MELEE_RANKED = tuple(
    w.item
    for w in sorted(WEAPONS.values(), key=lambda w: -w.dps())
    if w.kind in MELEE_KINDS
)


def weapon(item: str) -> Weapon | None:
    return WEAPONS.get(item)


@dataclass(frozen=True)
class Tactic:
    """What the mod's fight op does; ``wire()`` is its ``tactic`` field.

    style: melee, lance (a spear's charge), poke (its jab), hit_and_back, snipe,
    hunt or avoid (never engaged).
    """

    style: str
    weapon: str | None = None
    crit: bool = False  # time strikes on the way down from a jump
    sweep: bool = True  # a grounded sword hit may sweep what stands near the target
    keep: tuple[float, float] | None = None  # stand-off distance (min, max)
    melee: str | None = None  # snipe, lance: the weapon for a target that closes in
    no_gaze: bool = False  # never look at its eyes (enderman)

    def wire(self) -> dict:
        out = {"style": self.style, "crit": self.crit, "sweep": self.sweep}
        if self.keep is not None:
            out["keep"] = list(self.keep)
        if self.melee is not None:
            out["melee"] = self.melee
        if self.no_gaze:
            out["no_gaze"] = True
        return out


AVOID = Tactic("avoid")


@dataclass(frozen=True)
class Profile:
    styles: tuple[str, ...]  # preference order; empty: never engaged
    no_gaze: bool = False
    first: bool = False  # fought before nearer mobs (the evoker, before its vexes)


def _profiles() -> dict[str, Profile]:
    # A bow alone beats running from a mob best met in melee: shoot it as it comes.
    melee = Profile(("melee", "snipe"))
    out = {}
    groups = {
        # "player" is a commanded attack's target (the owner's order): a 20-health
        # melee target, never an animal to hunt. The auto-defence never meets it:
        # players are no threat.
        melee: "zombie husk drowned zombie_villager spider cave_spider silverfish "
        "endermite slime magma_cube witch vindicator hoglin zoglin "
        "piglin_brute wither_skeleton player",
        # the breeze deflects arrows; a vex is too small and quick to shoot
        Profile(("melee",)): "breeze vex",
        # it flees a player inside 8 blocks: melee landed 0-1 hits in 10 trials
        # while its vexes wore the bot down (20261124T014222Z), so the bow first
        # (a Loyalty trident's throw stands in for the bow wherever there is none)
        Profile(("snipe", "throw", "melee"), first=True): "evoker",
        Profile(("snipe", "throw", "melee")): (
            "skeleton stray bogged pillager illusioner"
        ),
        # the owner's order: the bow, the spear's charge past its fuse or the
        # trident's throw, then close in
        Profile(("snipe", "charge", "throw", "hit_and_back")): "creeper",
        # a ravager (100 health, a 12-point bite) is met only behind a shield (a
        # blocked bite can stun it); else it is shot as it comes, never met
        Profile(("shielded", "snipe", "throw")): "ravager",
        Profile(("snipe", "throw")): "phantom blaze ghast",
        Profile(("melee",), no_gaze=True): "enderman",
        Profile(()): "warden guardian elder_guardian ender_dragon wither",
    }
    for profile, names in groups.items():
        for name in names.split():
            out[M + name] = profile
    return out


PROFILES = _profiles()
HUNT = Profile(("hunt",))  # anything without a profile: an animal
# Hurt from range: walking calmly away from them only gets the bot shot in the back.
RANGED = frozenset(
    M + n
    for n in (
        "skeleton stray bogged pillager illusioner witch blaze ghast breeze evoker"
    ).split()
)


def never_engaged(target: str) -> bool:
    return not PROFILES.get(target, HUNT).styles


def first(target: str) -> bool:
    return PROFILES.get(target, HUNT).first


def _held(inventory: Mapping[str, int], kinds) -> list[Weapon]:
    return [
        w
        for item, n in inventory.items()
        if n > 0 and (w := WEAPONS.get(item)) is not None and w.kind in kinds
    ]


def _best(inventory, kinds) -> Weapon | None:
    held = _held(inventory, kinds)
    return max(held, key=lambda w: (w.dps(), w.item)) if held else None


Wear = Mapping[str, list[int]]  # remaining uses of each held piece, by item


def mob_health(target: str) -> float:
    return MOB_HEALTH.get(target, DEFAULT_MOB_HEALTH)


def kill_ticks(w: Weapon, health: float) -> float:
    """Ticks from its first full-strength hit to the kill: the first lands on
    arrival, then each further hit waits a cycle."""
    return (w.hits_to_kill(health) - 1) * w.cycle()


def _quickest(inventory, kinds, health: float) -> Weapon | None:
    """The held weapon that kills ``health`` soonest; a tie goes to the higher
    damage per second (the next mob comes sooner)."""
    held = _held(inventory, kinds)
    if not held:
        return None
    return min(held, key=lambda w: (kill_ticks(w, health), -w.dps(), w.item))


def _fewest_hits(inventory, kinds, health: float) -> Weapon | None:
    """The held swing weapon that kills ``health`` in the fewest plain (uncritted)
    hits, for a fight where each hit is its own round trip; a tie goes to the
    shorter cooldown."""
    held = _held(inventory, kinds)
    if not held:
        return None
    return min(held, key=lambda w: (math.ceil(health / w.damage - 1e-9), w.cooldown,
                                    w.item))  # fmt: skip


def charge_first(spear: Weapon, swing: Weapon, health: float) -> bool:
    """Whether a spear's charge before the swing weapon kills sooner than the swing
    weapon alone. Alone, its first hit lands on arrival: (n - 1) cycles. After the
    charge the swap resets its cooldown, so each of the m swings left waits a full
    cycle: m cycles. Measured: an iron spear before an iron sword took a zombie
    3.5 s against 2.7 (10x matrix 20261123T235754Z)."""
    alone = swing.hits_to_kill(health) - 1
    rest = health - spear.hit()
    after = 0 if rest <= 0 else swing.hits_to_kill(rest)
    return after < alone


def _arrows(inventory: Mapping[str, int]) -> bool:
    return any(inventory.get(a, 0) > 0 for a in ARROWS)


def hunt_weapon(
    target: str | None, inventory: Mapping[str, int], wear: Wear | None = None
) -> Weapon | None:
    """What an animal is hunted with (None: the fist). The owner's rule: the bow
    while there are arrows (shot, never swung); else the held weapon that kills it
    in the fewest ticks, then with the least wear, among those the kill does not
    break. A piece's wear is known per piece (``wear``); without it, any survives."""
    if _arrows(inventory) and (bow := _best(inventory, ("bow",))) is not None:
        return bow
    health = ANIMAL_HEALTH.get(target or "", DEFAULT_ANIMAL_HEALTH)
    best, key = None, None
    for w in _held(inventory, MELEE_KINDS):
        hits = w.hits_to_kill(health)
        spend = hits * w.wear
        if wear is not None and w.item in wear and max(wear[w.item]) < spend:
            continue  # this kill would break every piece of it
        k = (-(hits - 1) * w.cycle(), -spend, w.hit(), w.item)
        if key is None or k > key:
            best, key = w, k
    return best


def tactic(
    target: str,
    inventory: Mapping[str, int],
    wear: Wear | None = None,
    loyal: bool = False,
) -> Tactic:
    """The first style in the target's profile that the held weapons allow.
    ``loyal``: a carried trident has Loyalty (only that one is thrown)."""
    profile = PROFILES.get(target, HUNT)
    for style in profile.styles:
        if style == "throw":
            chosen = _throw(inventory, target) if loyal else None
        else:
            chosen = _style(style, inventory, target, wear)
        if chosen is not None:
            return (
                Tactic(**{**chosen.__dict__, "no_gaze": True})
                if profile.no_gaze
                else chosen
            )
    return AVOID


LOYALTY = M + "loyalty"


def loyal(stacks) -> bool:
    """Whether a carried trident has Loyalty (the body's inventory rows carry each
    stack's enchantments): only such a one is thrown, since it comes back."""
    return any(
        s.get("item") == TRIDENT and (s.get("enchantments") or {}).get(LOYALTY, 0) > 0
        for s in stacks or ()
    )


def _throw(inventory, target: str) -> Tactic | None:
    """A Loyalty trident thrown from the bow's band (the mod draws it 10 ticks);
    while it is out, the quickest other swing weapon meets one that closes in."""
    if inventory.get(TRIDENT, 0) <= 0:
        return None
    others = {k: v for k, v in inventory.items() if k != TRIDENT}
    backup = _quickest(others, SWING_KINDS, mob_health(target))
    melee = backup.item if backup else None
    return Tactic("throw", TRIDENT, keep=SNIPE_KEEP, melee=melee)


def fists() -> Tactic:
    """Bare hands: what a commanded attack falls back to with nothing to wield."""
    return Tactic("melee", None, crit=False, sweep=False)


def ordered_tactic(
    target: str,
    inventory: Mapping[str, int],
    wear: Wear | None = None,
    loyal: bool = False,
) -> Tactic:
    """The tactic for a fight the owner ordered (an attack, an arena hunt): the
    guard's ``tactic``, except that a mob the guard would avoid is fought in melee
    with the best weapon held, and with bare hands only when nothing is held."""
    plan = tactic(target, inventory, wear, loyal=loyal)
    if plan.style != "avoid":
        return plan
    return _style("melee", inventory, target, wear) or fists()


def forced_tactic(
    weapon: str, target: str, inventory: Mapping[str, int], loyal: bool = False
) -> Tactic | None:
    """The tactic for a named weapon (the owner's order), or None when it is not held
    (a bow also needs arrows). The weapon decides the style: a bow snipes (a held
    swing weapon backs it up close in), a spear charges, a Loyalty trident is thrown
    (``loyal``), a sword, axe or other trident swings for crits, and any other item
    is a plain hit."""
    if inventory.get(weapon, 0) <= 0:
        return None
    if weapon == TRIDENT and loyal:
        return _throw(inventory, target)
    w = WEAPONS.get(weapon)
    if w is None:
        return Tactic("melee", weapon, crit=False, sweep=False)
    if w.kind == "bow":
        if not _arrows(inventory):
            return None
        backup = _quickest(inventory, SWING_KINDS, mob_health(target)) or _best(
            inventory, ("spear",)
        )
        return Tactic(
            "snipe", w.item, keep=SNIPE_KEEP, melee=backup.item if backup else None
        )
    if w.kind == "spear":
        return Tactic("lance", w.item)
    return Tactic("melee", w.item, crit=True)


def _style(style: str, inventory, target: str = "", wear=None) -> Tactic | None:
    if style == "snipe":
        bow = _best(inventory, ("bow",))
        if bow is None or not _arrows(inventory):
            return None
        health = mob_health(target)
        backup = _quickest(inventory, SWING_KINDS, health) or _best(
            inventory, ("spear",)
        )
        return Tactic(
            "snipe", bow.item, keep=SNIPE_KEEP, melee=backup.item if backup else None
        )
    if style == "melee":
        # The owner's rule: the spear's charge opens from range and the best swing
        # weapon takes over up close (the mod hands back once it is far out), when
        # that kills sooner than the swing weapon alone (``charge_first``).
        spear = _best(inventory, ("spear",))
        swing = _quickest(inventory, SWING_KINDS, mob_health(target))
        if swing is None:
            return Tactic("lance", spear.item) if spear else None
        if spear is not None and charge_first(spear, swing, mob_health(target)):
            return Tactic("lance", spear.item, crit=True, melee=swing.item)
        return Tactic("melee", swing.item, crit=True)
    if style == "shielded":
        # melee behind the off-hand shield (the mod raises it): each strike drops
        # it, so the swing weapon with the fewest hits to the kill
        if inventory.get(SHIELD, 0) <= 0:
            return None
        best = _fewest_hits(inventory, SWING_KINDS, mob_health(target))
        return Tactic("melee", best.item, crit=True) if best else None
    if style == "charge":
        # a creeper: never handed to a swing weapon, outrun after each hit
        spear = _best(inventory, ("spear",))
        return Tactic("lance", spear.item) if spear else None
    if style == "hit_and_back":
        best = _fewest_hits(inventory, SWING_KINDS, mob_health(target))
        return Tactic("hit_and_back", best.item) if best else None
    if style == "hunt":
        best = hunt_weapon(target, inventory, wear)
        if best is not None and best.kind == "bow":
            return Tactic("snipe", best.item, keep=HUNT_KEEP)
        if best is not None and best.kind == "spear":
            return Tactic("lance", best.item)
        return Tactic(
            "hunt",
            best.item if best else None,
            crit=best is None or best.crits,
            sweep=False,
        )
    raise ValueError(f"unknown combat style: {style}")
