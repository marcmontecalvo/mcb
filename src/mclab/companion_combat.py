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
JAB_REACH = 4.5  # a spear jab reaches 2.0 to 4.5 from the eye to the target's box
POKE_KEEP = (2.5, 4.0)
# A creeper lights with the body's feet inside 3 blocks of its own; its box edge is a
# half-width (0.3) nearer. Jabbing from 3.6 to 4.4 (eye to box) leaves almost a block
# for the body to drift on while it turns round as the creeper walks in.
CREEPER_FUSE = 3.0
FUSE_KEEP = (3.6, 4.4)
SNIPE_KEEP = (8.0, 15.0)  # outside a skeleton's comfort, inside its 15-block range
# An animal never fights back: shoot it from wherever, closing in past 8 blocks
# (a panicked animal zigzags, and the nearer shot lands more often).
HUNT_KEEP = (0.0, 8.0)
ARROWS = frozenset({M + "arrow", M + "spectral_arrow", M + "tipped_arrow"})
MELEE_KINDS = ("sword", "axe", "trident", "spear")
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


@dataclass(frozen=True)
class Weapon:
    item: str
    kind: str  # sword, axe, spear, trident or bow
    damage: float  # per hit, the player's base 1 included; bow: a full-draw arrow
    cooldown: float  # ticks to full strength (bow: the full draw)

    @property
    def crits(self) -> bool:
        return self.kind in ("sword", "axe", "trident")

    def hit(self) -> float:
        return self.damage * (CRIT if self.crits else 1)

    @property
    def wear(self) -> int:
        """Uses a hit spends (the Weapon component: axes 2, the rest 1)."""
        return 2 if self.kind == "axe" else 1

    def cycle(self) -> float:
        """Ticks between full-strength hits (a crit waits for its jump)."""
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
    spears = {
        "wooden": (1, 0.65), "golden": (1, 0.95), "stone": (2, 0.75),
        "copper": (2, 0.85), "iron": (3, 0.95), "diamond": (4, 1.05),
        "netherite": (5, 1.15),
    }  # fmt: skip
    for tier, (damage, swing) in spears.items():
        out[f"{tier}_spear"] = ("spear", damage, swing * 20)
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

    style: melee, poke, hit_and_back, snipe, hunt or avoid (never engaged). A
    profile's ``outreach`` is a poke from past a creeper's fuse (``FUSE_KEEP``).
    """

    style: str
    weapon: str | None = None
    crit: bool = False  # time strikes on the way down from a jump
    sweep: bool = True  # a grounded sword hit may sweep what stands near the target
    keep: tuple[float, float] | None = None  # stand-off distance (min, max)
    melee: str | None = None  # snipe: the weapon for a target that closes in
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
        melee: "zombie husk drowned zombie_villager spider cave_spider silverfish "
        "endermite slime magma_cube witch vindicator hoglin zoglin "
        "piglin_brute wither_skeleton",
        # the breeze deflects arrows; a vex is too small and quick to shoot
        Profile(("melee",)): "breeze vex",
        Profile(("melee", "snipe"), first=True): "evoker",
        Profile(("snipe", "melee")): "skeleton stray bogged pillager",
        Profile(("snipe", "hit_and_back", "outreach")): "creeper",
        Profile(("snipe",)): "phantom blaze ghast",
        Profile(("melee",), no_gaze=True): "enderman",
        Profile(()): "warden ravager guardian elder_guardian ender_dragon wither",
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
    for n in "skeleton stray bogged pillager witch blaze ghast breeze evoker".split()
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
    target: str, inventory: Mapping[str, int], wear: Wear | None = None
) -> Tactic:
    """The first style in the target's profile that the held weapons allow."""
    profile = PROFILES.get(target, HUNT)
    for style in profile.styles:
        chosen = _style(style, inventory, target, wear)
        if chosen is not None:
            return (
                Tactic(**{**chosen.__dict__, "no_gaze": True})
                if profile.no_gaze
                else chosen
            )
    return AVOID


def _style(style: str, inventory, target: str = "", wear=None) -> Tactic | None:
    if style == "snipe":
        bow = _best(inventory, ("bow",))
        if bow is None or not _arrows(inventory):
            return None
        backup = _best(inventory, MELEE_KINDS)
        return Tactic(
            "snipe", bow.item, keep=SNIPE_KEEP, melee=backup.item if backup else None
        )
    if style == "melee":
        best = _best(inventory, MELEE_KINDS)
        if best is None:
            return None
        if best.kind == "spear":
            return Tactic("poke", best.item, keep=POKE_KEEP)
        return Tactic("melee", best.item, crit=True)
    if style == "hit_and_back":
        best = _best(inventory, ("sword", "axe", "trident"))
        return Tactic("hit_and_back", best.item) if best else None
    if style == "outreach":  # a spear's reach beyond a creeper's fuse
        best = _best(inventory, ("spear",))
        return Tactic("poke", best.item, keep=FUSE_KEEP) if best else None
    if style == "hunt":
        best = hunt_weapon(target, inventory, wear)
        if best is not None and best.kind == "bow":
            return Tactic("snipe", best.item, keep=HUNT_KEEP)
        if best is not None and best.kind == "spear":
            return Tactic("poke", best.item, keep=POKE_KEEP)
        return Tactic(
            "hunt",
            best.item if best else None,
            crit=best is None or best.crits,
            sweep=False,
        )
    raise ValueError(f"unknown combat style: {style}")
