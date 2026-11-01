"""Jev navigation contract (Phase 6; replaces bounded-local-waypoint-selector-v1).

Jev sets objectives and weights; it never picks waypoints (D6). Input is a compact
situation; output is a goal, a cost profile, an optional route-class choice and an
interrupt/resume decision. A late or invalid answer falls back to a deterministic
rule, and every commitment records which of the two produced it.
"""

from dataclasses import dataclass

from mclab.profiles import PROFILES

CONTRACT = "jev-objective-profile-v1"
DECISIONS = ("continue", "interrupt", "resume")
NIGHT = (13000, 23000)  # Game time of day when hostiles spawn on the surface.


@dataclass(frozen=True)
class Situation:
    task: str
    health: float
    food: int
    time_of_day: int
    threats: int  # visible hostiles
    blocks: int  # placeable blocks in the hotbar
    goal: tuple
    route_classes: tuple = ()  # 0 or 2..6 short summaries, e.g. "40 s, no edits"

    def payload(self):
        return {
            "contract": CONTRACT,
            "task": self.task,
            "health": self.health,
            "food": self.food,
            "night": NIGHT[0] <= self.time_of_day % 24000 < NIGHT[1],
            "threats": self.threats,
            "blocks": self.blocks,
            "goal": list(self.goal),
            "route_classes": list(self.route_classes),
        }


@dataclass(frozen=True)
class Decision:
    goal: tuple
    profile: str
    route_class: int | None = None
    decision: str = "continue"
    source: str = "fallback"  # "jev" or "fallback": who produced the commitment


def validate(raw, situation):
    """A Jev answer as a Decision, or ValueError. Never partially trusted."""
    if not isinstance(raw, dict) or raw.get("contract") != CONTRACT:
        raise ValueError("Jev answer is not this contract")
    goal = raw.get("goal")
    if (
        not isinstance(goal, list)
        or len(goal) != 3
        or any(type(v) is not int for v in goal)
    ):
        raise ValueError("Jev goal must be three integers")
    if raw.get("profile") not in PROFILES:
        raise ValueError("Jev profile is not a named profile")
    route_class = raw.get("route_class")
    if route_class is not None and (
        type(route_class) is not int
        or not 0 <= route_class < len(situation.route_classes)
    ):
        raise ValueError("Jev route class is out of range")
    decision = raw.get("decision", "continue")
    if decision not in DECISIONS:
        raise ValueError("Jev decision is not continue/interrupt/resume")
    return Decision(tuple(goal), raw["profile"], route_class, decision, "jev")


def fallback(situation):
    """Deterministic profile when Jev is late or invalid."""
    night = NIGHT[0] <= situation.time_of_day % 24000 < NIGHT[1]
    if situation.health <= 8 or situation.threats or night:
        profile = "safe"
    elif situation.blocks <= 4:
        profile = "frugal"
    else:
        profile = "race"
    return Decision(situation.goal, profile)


def choose(situation, raw=None, *, late=False):
    """The commitment for this situation: Jev's valid, timely answer or the fallback.

    Returns (decision, error): error names why Jev was not used, else None.
    """
    if late or raw is None:
        return fallback(situation), "late" if late else "no answer"
    try:
        return validate(raw, situation), None
    except ValueError as error:
        return fallback(situation), str(error)
