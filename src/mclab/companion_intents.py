"""The companion's intents: one validated contract for MCP, chat, /mcb and the API.

Every adapter turns its input into ``(intent, args, actor)`` and calls the host.
``validate`` returns normalized arguments or raises ``ValueError`` with a message a
model can act on. ``tool_schemas`` describes the same intents as tools (MCP
``inputSchema`` / OpenAI ``parameters``). Docs: COMPANION_LLM_PLAN_2026-11-23.md.
"""

import math
import re
from dataclasses import asdict, dataclass

PLAYER = re.compile(r"[A-Za-z0-9_]{1,16}")
ITEM = re.compile(r"(?:minecraft:)?([a-z0-9_]{1,64})")
# a registered arena's name (the mod's rule; mclab.arena.client.NAME)
ARENA_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
TEMPLATE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
PLACE = re.compile(r"[a-z0-9][a-z0-9 _-]{0,31}")
JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
COORD = 30_000_000
Y_MIN, Y_MAX = -64, 320
MAX_COUNT = 2304  # a full inventory of 64-stacks
# Words a player says for a group of items. "wood" is any log, whichever is closest.
GROUP_WORDS = {
    **dict.fromkeys(("wood", "log", "logs"), "#minecraft:logs"),
    **dict.fromkeys(("plank", "planks"), "#minecraft:planks"),
}
SAY_MAX = 256

SOURCES = ("mcp", "api", "chat", "command")
OPERATOR_SOURCES = frozenset({"mcp", "api"})


def _xyz(description):
    return {
        "x": {"type": "integer", "description": f"{description} X"},
        "y": {"type": "integer", "minimum": Y_MIN, "maximum": Y_MAX},
        "z": {"type": "integer", "description": f"{description} Z"},
    }


_WHEN = {
    "when": {
        "type": "string",
        "enum": ["now", "next"],
        "description": "now (default): stop the current job and the queue first. "
        "next: run after the jobs already queued.",
    }
}

# name -> (description, properties, required). Order is the tool order.
INTENTS = {
    "status": (
        "What the bot is doing now: current job and queue, position, health, food, "
        "inventory, players in sight and the last failure. Cheap; call it first.",
        {},
        [],
    ),
    "say": (
        "Say something in game chat as the bot, to everyone or to one player.",
        {
            "text": {"type": "string", "minLength": 1, "maxLength": SAY_MAX},
            "to": {"type": "string", "description": "player name; omit for everyone"},
        },
        ["text"],
    ),
    "list_places": (
        "Named places the bot remembers in this world (home, chests, ...).",
        {},
        [],
    ),
    "remember_place": (
        "Name a place so goto can use it later. Without x/y/z: where the bot stands.",
        {
            "name": {"type": "string", "description": "lower case, up to 32 chars"},
            **_xyz("place"),
        },
        ["name"],
    ),
    "job_status": (
        "State, progress and result of one job by its id.",
        {"job_id": {"type": "string"}},
        ["job_id"],
    ),
    "stop": (
        "Stop the running job and drop the queue. Always safe; the bot stands still.",
        {},
        [],
    ),
    "defense": (
        "Read or change how the bot defends itself from hostile mobs (it always "
        "does: fights when armed, else hides or goes home). floor_hearts: at or "
        "below this health it stops fighting and escapes (default 3). Radii in "
        "blocks: threats, allies to stay with, a base to run to. No arguments: read.",
        {
            "floor_hearts": {"type": "number", "minimum": 0.5, "maximum": 9.5},
            "threat_radius": {"type": "integer", "minimum": 2, "maximum": 32},
            "ally_radius": {"type": "integer", "minimum": 0, "maximum": 64},
            "base_radius": {"type": "integer", "minimum": 0, "maximum": 256},
        },
        [],
    ),
    "goto": (
        "Walk to a block position, a named place, or a player (come here). Give "
        "exactly one of: x/y/z, place, player.",
        {
            **_xyz("goal"),
            "place": {"type": "string", "description": "a name from list_places"},
            "player": {"type": "string", "description": "walk to this player"},
            "radius": {"type": "integer", "minimum": 1, "maximum": 8},
            **_WHEN,
        },
        [],
    ),
    "follow": (
        "Keep following a player until stopped or given another job.",
        {
            "player": {"type": "string"},
            "distance": {"type": "integer", "minimum": 2, "maximum": 8},
            **_WHEN,
        },
        ["player"],
    ),
    "gather": (
        "Collect an item: mine, chop, craft and smelt as needed until the bot holds "
        "`count` of it. Item is a Minecraft id such as oak_log or iron_ingot, or a "
        "group tag when any kind will do: #minecraft:logs for wood (any log, the "
        "nearest), #minecraft:planks, #minecraft:wool. This is also how to hunt: "
        "gather a drop an animal gives (porkchop, beef, mutton, chicken, leather, "
        "feather) and the bot finds and kills the animals for it, shooting with a "
        "bow while it has arrows.",
        {
            "item": {"type": "string"},
            "count": {"type": "integer", "minimum": 1, "maximum": MAX_COUNT},
            **_WHEN,
        },
        ["item", "count"],
    ),
    "build": (
        "Build a saved template (a name from builds/). Origin x/y/z is the corner "
        "block; omit it to build beside the bot. Materials missing are gathered.",
        {
            "template": {"type": "string"},
            **_xyz("origin"),
            "rotation": {"type": "integer", "enum": [0, 90, 180, 270]},
            **_WHEN,
        },
        ["template"],
    ),
    "attack": (
        "Fight one target on the owner's order, until it dies, you stop it or it can "
        "no longer be fought. Give exactly one of: `player` (an online player's "
        "name), `mob` (a kind such as zombie or cow: the nearest one seen) or "
        "`nearest_hostile` (true: the nearest hostile mob) or `arena` (an arena's "
        "name, or true for the one the bot stands in: hunt every wave mob of its "
        "match, wherever it is, fighting on to the end). The bot walks or chases "
        "into range, picks the tactic and weapon itself (`weapon`: an item id it "
        "holds, to use that instead), chases again when the target runs off, and "
        "outside an arena still stops at its health floor (see defense). The job's "
        "result says how it ended: killed; lost (the target logged out, went to "
        "another dimension or cannot be found); invulnerable (a player in creative "
        "or spectator mode); for an arena won, lost or stopped; with kills, hits, "
        "crits, shots and damage. Only the owner or allowlist may use it; a player "
        "may be named only if the owners or allowlist name them.",
        {
            "player": {"type": "string", "description": "an online player's name"},
            "mob": {
                "type": "string",
                "description": "an entity type such as zombie, skeleton or cow",
            },
            "nearest_hostile": {
                "type": "boolean",
                "description": "true: the nearest hostile mob in sight",
            },
            "arena": {
                "type": ["string", "boolean"],
                "description": "an arena's name, or true for the arena the bot "
                "stands in: every wave mob of its match",
            },
            "weapon": {
                "type": "string",
                "description": "an item id the bot holds, such as iron_sword or bow",
            },
            **_WHEN,
        },
        [],
    ),
}
JOB_INTENTS = frozenset({"goto", "follow", "gather", "build", "attack"})
INSTANT_INTENTS = frozenset(INTENTS) - JOB_INTENTS
# What an unauthorized player's chat may make the bot do: look, never act.
UNTRUSTED_INTENTS = frozenset({"status", "list_places"})


def tool_schemas(*, trusted: bool = True) -> list[dict]:
    tools = []
    for name, (description, properties, required) in INTENTS.items():
        if not trusted and name not in UNTRUSTED_INTENTS:
            continue
        schema = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = list(required)
        tools.append({"name": name, "description": description, "input_schema": schema})
    return tools


# --- validation ----------------------------------------------------------------


def _int(args, key, low, high, default=None):
    value = args.get(key, default)
    if value is None:
        return None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)  # a model's 64.0
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{key} must be an integer from {low} to {high}")
    return value


def _text(args, key, pattern, what):
    value = args.get(key)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{key} must be {what}")
    return value


def _pos(args, *, required=False):
    present = [k for k in "xyz" if args.get(k) is not None]
    if not present and not required:
        return None
    if len(present) != 3:
        raise ValueError("give all of x, y and z")
    return (
        _int(args, "x", -COORD, COORD),
        _int(args, "y", Y_MIN, Y_MAX),
        _int(args, "z", -COORD, COORD),
    )


def _player(args, key="player"):
    return _text(args, key, PLAYER, "a player name (letters, digits, _; 1-16)")


def _when(args):
    value = args.get("when", "now")
    if value not in ("now", "next"):
        raise ValueError('when must be "now" or "next"')
    return value


def _goto(args):
    forms = [
        form
        for form, present in (
            ("pos", any(args.get(k) is not None for k in "xyz")),
            ("place", args.get("place") is not None),
            ("player", args.get("player") is not None),
        )
        if present
    ]
    if len(forms) != 1:
        raise ValueError("goto needs exactly one target: x/y/z, place or player")
    if forms[0] == "pos":
        target = {"pos": _pos(args, required=True)}
    elif forms[0] == "place":
        target = {"place": _place_name(args)}
    else:
        target = {"player": _player(args)}
    radius = _int(args, "radius", 1, 8, 2)
    return {"target": target, "radius": radius, "when": _when(args)}


def _place_name(args, key="place"):
    value = args.get(key)
    if isinstance(value, str):
        value = value.strip().lower()
    return _text({key: value}, key, PLACE, "a place name (a-z, digits, space, _ -)")


def _item(args):
    """An item id, or a group of items as a tag (``#minecraft:logs``: any wood).
    Plain words for the common groups are taken too ("wood", "planks")."""
    value = args.get("item")
    said = value.strip().lower() if isinstance(value, str) else ""
    said = GROUP_WORDS.get(said, said)
    tag = said.startswith("#")
    found = ITEM.fullmatch(said.removeprefix("#"))
    if not found:
        raise ValueError(
            "item must be a Minecraft item id such as oak_log, or a group such as "
            "#minecraft:logs"
        )
    return ("#" if tag else "") + "minecraft:" + found.group(1)


def _say(args):
    text = args.get("text")
    if isinstance(text, str):
        text = " ".join(text.split())  # one line: no newlines or control spacing
    if not isinstance(text, str) or not 1 <= len(text) <= SAY_MAX:
        raise ValueError(f"text must be 1 to {SAY_MAX} characters")
    to = args.get("to")
    return {"text": text, "to": None if to is None else _player(args, "to")}


def _defense(args):
    """Changes to the defence settings; hearts become health points."""
    out = {}
    hearts = args.get("floor_hearts")
    if hearts is not None:
        if (
            type(hearts) not in (int, float)
            or not 0.5 <= hearts <= 9.5
            or (hearts * 2) % 1
        ):
            raise ValueError("floor_hearts must be 0.5 to 9.5 in half hearts")
        out["floor_health"] = int(hearts * 2)
    for key, high in (("threat_radius", 32), ("ally_radius", 64), ("base_radius", 256)):
        value = _int(args, key, 2 if key == "threat_radius" else 0, high)
        if value is not None:
            out[key] = value
    return out


def _entity_type(args, key="mob"):
    """An entity type id: ``zombie`` and ``Zombie`` are both ``minecraft:zombie``."""
    value = args.get(key)
    said = value.strip().lower() if isinstance(value, str) else ""
    found = ITEM.fullmatch(said)
    if not found:
        raise ValueError(f"{key} must be an entity type such as zombie or cow")
    if found.group(1) == "player":
        raise ValueError("to attack a player use player (their name), not mob")
    return "minecraft:" + found.group(1)


def _weapon(args):
    value = args.get("weapon")
    if value is None:
        return None
    found = ITEM.fullmatch(value.strip().lower()) if isinstance(value, str) else None
    if not found:
        raise ValueError("weapon must be an item id such as iron_sword or bow")
    return "minecraft:" + found.group(1)


def _arena(value):
    """An arena's registered name, or true: the arena the bot stands in."""
    if value is True or (isinstance(value, str) and ARENA_NAME.fullmatch(value)):
        return value
    raise ValueError(
        "arena must be an arena's name (lowercase letters, digits, - and _, at most "
        "32) or true for the arena the bot stands in"
    )


def _attack(args):
    """One target: a player by name, the nearest mob of a type, the nearest
    hostile, or every wave mob of an arena's match."""
    hostile = args.get("nearest_hostile")
    if hostile is not None and type(hostile) is not bool:
        raise ValueError("nearest_hostile must be true or false")
    forms = [
        form
        for form, present in (
            ("player", args.get("player") is not None),
            ("mob", args.get("mob") is not None),
            ("nearest_hostile", hostile is True),
            ("arena", args.get("arena") is not None),
        )
        if present
    ]
    if len(forms) != 1:
        raise ValueError(
            "attack needs exactly one target: player, mob, nearest_hostile or arena"
        )
    if forms[0] == "player":
        target = {"player": _player(args)}
    elif forms[0] == "mob":
        target = {"mob": _entity_type(args)}
    elif forms[0] == "arena":
        target = {"arena": _arena(args["arena"])}
    else:
        target = {"nearest_hostile": True}
    return {"target": target, "weapon": _weapon(args), "when": _when(args)}


_VALIDATORS = {
    "attack": _attack,
    "status": lambda a: {},
    "list_places": lambda a: {},
    "stop": lambda a: {},
    "defense": _defense,
    "say": _say,
    "remember_place": lambda a: {"name": _place_name(a, "name"), "pos": _pos(a)},
    "job_status": lambda a: {"job_id": _text(a, "job_id", JOB_ID, "a job id")},
    "goto": _goto,
    "follow": lambda a: {
        "player": _player(a),
        "distance": _int(a, "distance", 2, 8, 3),
        "when": _when(a),
    },
    "gather": lambda a: {
        "item": _item(a),
        "count": _int(a, "count", 1, MAX_COUNT, 0),
        "when": _when(a),
    },
    "build": lambda a: {
        "template": _text(a, "template", TEMPLATE, "a template name from builds/"),
        "origin": _pos(a),
        "rotation": _rotation(a),
        "when": _when(a),
    },
}


def _rotation(args):
    value = _int(args, "rotation", 0, 270, 0)
    if value not in (0, 90, 180, 270):
        raise ValueError("rotation must be 0, 90, 180 or 270")
    return value


def validate(intent: str, args: dict | None) -> dict:
    """Normalized arguments for ``intent``; ValueError names what is wrong."""
    if intent not in INTENTS:
        raise ValueError(f"Unknown intent {intent!r}; use one of {', '.join(INTENTS)}")
    args = dict(args or {})
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    allowed = set(INTENTS[intent][1])
    extra = sorted(set(args) - allowed)
    if extra:
        raise ValueError(f"Unknown argument {extra[0]!r} for {intent}")
    return _VALIDATORS[intent](args)


# --- who is asking -----------------------------------------------------------------


@dataclass(frozen=True)
class Actor:
    """Who asked: ``mcp``/``api`` are the local operator; ``chat``/``command`` are
    players in the game and go through the owner/allowlist."""

    source: str
    name: str | None = None
    uuid: str | None = None
    position: tuple[float, float, float] | None = None

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError(f"Unknown actor source {self.source!r}")
        if self.position is not None:
            object.__setattr__(self, "position", tuple(float(v) for v in self.position))

    def to_dict(self) -> dict:
        value = asdict(self)
        if self.position is not None:
            value["position"] = list(self.position)
        return value

    @classmethod
    def from_dict(cls, value: dict) -> "Actor":
        position = value.get("position")
        return cls(
            value["source"],
            name=value.get("name"),
            uuid=value.get("uuid"),
            position=tuple(position) if position is not None else None,
        )


def _allowlisted(uuid, name=""):
    from mclab.companion_service import allowed

    return allowed(uuid, name)


def authorized(actor: Actor) -> bool:
    """May this actor make the bot act (jobs, stop, say)?"""
    if actor.source in OPERATOR_SOURCES:
        return True
    return bool(_allowlisted(actor.uuid or "", actor.name or ""))


def _listed(uuid, name=""):
    from mclab.companion_service import allowed

    return allowed(uuid, name, listed=True)


def may_attack_players(actor: Actor) -> bool:
    """May this actor order an attack on a player? Stricter than ``authorized``: an
    empty owners/allowlist lets every player command the bot, but not hit a player;
    only the local operator or someone the config names does."""
    if actor.source in OPERATOR_SOURCES:
        return True
    return bool(_listed(actor.uuid or "", actor.name or ""))
