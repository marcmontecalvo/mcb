"""Arena runtime, Python side: plans, wave presets and a standalone bridge client.

The mod (``com.vulpine.mcb.arena``) owns the rules; this module only reads the plan and
wave files under ``arenas/``, checks waves with the same limits the mod enforces, and
talks to the body bridge's ``/admin`` endpoint without a body or a run (the arena ops
need neither). See docs/ARENA_RUNTIME.md.
"""

import itertools
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from mclab.paths import INSTANCE, ROOT, config, read_json

ARENAS = ROOT / "arenas"
WAVES = ARENAS / "waves"
OPS = (
    "arena_build",
    "arena_remove",
    "arena_list",
    "arena_status",
    "arena_start",
    "arena_stop",
    "arena_spawn",
    "arena_op",
    "arena_kit",
    "arena_board",
    "arena_recap",
)
# The arena protocol this lab speaks (ArenaLogic.PROTOCOL, reported by arena_list).
# Raise both together whenever an op is added or a request gains or changes a field:
# the mod rejects a field it does not know, so a lab and a mod of different protocols
# would fail a match only once it starts. 1: every mod from before the handshake.
# 2: arena_board, arena_say and arena_start's series. 3: arena_recap (the series
# table, drawn by the mod like a match's) in place of arena_say. 4: arena_start's
# keep_inventory (arena deaths never drop; keep_inventory keeps what they carry).
# 5: arena_start's respawn_mode, respawn_count and respawn_delay_ticks (PvP respawns).
# 6: arena_start's team_mobs and team_mob_kits (counted allied mob groups in teams mode).
PROTOCOL = 6
RESPAWN_MODES = ("elimination", "player", "team")
MAX_RESPAWNS = 99
MAX_RESPAWN_DELAY_TICKS = 1200
SUPPORTED_ALLIED_MOBS = frozenset(
    {
        "minecraft:zombie",
        "minecraft:husk",
        "minecraft:drowned",
        "minecraft:skeleton",
        "minecraft:stray",
        "minecraft:wither_skeleton",
        "minecraft:spider",
        "minecraft:cave_spider",
        "minecraft:pillager",
        "minecraft:vindicator",
        "minecraft:witch",
        "minecraft:iron_golem",
        "minecraft:wolf",
        "minecraft:piglin_brute",
        "minecraft:blaze",
        "minecraft:breeze",
    }
)
MAX_ALLIED_GROUP_COUNT = 32
MAX_ALLIED_GROUPS_PER_TEAM = 32
MAX_ALLIED_NAME_LEN = 32
MAX_KIT_GIVE = 36
MAX_KIT_COUNT = 256
MAX_KIT_ENCHANTMENTS = 4
MAX_KIT_ENCHANTMENT_LEVEL = 10
KIT_FIELDS = frozenset({"give", "clear_inventory", "heal", "wear"})
GIVE_FIELDS = frozenset({"item", "count", "enchantments", "potion"})
ALLIED_GROUP_FIELDS = frozenset({"type", "count", "name", "kit", "overrides"})
ALLIED_OVERRIDE_FIELDS = frozenset({"index", "name", "kit"})
UPDATE_MOD = "stop the arena server, run `mcarena mod-install`, then start it again"
ENVELOPE = frozenset(
    {
        "schema_version",
        "run_id",
        "episode_id",
        "body_id",
        "sequence",
        "op",
        "request_id",
    }
)
# The mod's limits (ArenaRequests.java / ArenaPlan.java): kept in step by hand.
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
MOB_ID = re.compile(r"[a-z0-9_.-]+:[a-z0-9_./-]+")
PLAYER = re.compile(r"[A-Za-z0-9_]{1,16}")
MAX_WAVES, MAX_PER_TYPE, MAX_PER_WAVE, MAX_DELAY = 50, 64, 256, 72_000
FORBIDDEN_MOBS = frozenset({"minecraft:ender_dragon"})


class ArenaError(Exception):
    """A failure with the mod's own reason code (``arena_not_ready``, ``overlap``)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def check_protocol(listing: dict) -> None:
    """Refuse an arena server whose mod speaks another protocol than this lab
    (``listing``: its ``arena_list`` answer): ``mod_outdated`` when the mod is older,
    with the steps that update it; ``lab_outdated`` when it is newer."""
    got = listing.get("protocol")
    got = got if isinstance(got, int) and not isinstance(got, bool) else 1
    if got < PROTOCOL:
        raise ArenaError(
            "mod_outdated",
            f"the arena server runs an older mcb-body mod (arena protocol {got}, "
            f"this lab needs {PROTOCOL}): {UPDATE_MOD}",
        )
    if got > PROTOCOL:
        raise ArenaError(
            "lab_outdated",
            f"the arena server runs a newer mcb-body mod (arena protocol {got}, this "
            f"lab speaks {PROTOCOL}): update this checkout, or {UPDATE_MOD} from it",
        )


# ---- files -----------------------------------------------------------------


def _json_file(path: Path, kind: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ArenaError(f"no_{kind}", f"cannot read {path}: {error}") from error
    except ValueError as error:
        raise ArenaError(f"bad_{kind}", f"{path} is not JSON: {error}") from error


def _names(directory: Path) -> list[str]:
    return (
        sorted(p.stem for p in directory.glob("*.json")) if directory.is_dir() else []
    )


def _resolve(ref: str, directory: Path, kind: str) -> Path:
    path = directory / f"{ref}.json" if NAME.fullmatch(ref) else Path(ref)
    if not path.is_file():
        have = ", ".join(_names(directory)) or "none"
        raise ArenaError(f"no_{kind}", f"{ref}: not found (in {directory}: {have})")
    return path


def load_plan(ref: str, root: Path = ARENAS) -> dict:
    """``arenas/<name>.json`` for a name, else the file at a path."""
    plan = _json_file(_resolve(ref, root, "plan"), "plan")
    if not isinstance(plan, dict):
        raise ArenaError("bad_plan", f"{ref}: a plan is a JSON object")
    return plan


def waves_presets(root: Path = WAVES) -> list[str]:
    return _names(root)


def load_waves(ref: str, root: Path = WAVES) -> list[dict]:
    """``arenas/waves/<name>.json`` for a name, else the file at a path.

    The file holds a list of waves, or ``{"waves": [...]}``.
    """
    data = _json_file(_resolve(ref, root, "waves"), "waves")
    waves = data.get("waves") if isinstance(data, dict) else data
    check_waves(waves)
    return waves


def check_waves(waves) -> None:
    """The mod's wave rules, so a bad file fails here without a round trip."""

    def bad(detail: str):
        return ArenaError("bad_waves", detail)

    if not isinstance(waves, list) or not waves:
        raise bad("a match needs at least one wave")
    if len(waves) > MAX_WAVES:
        raise bad(f"at most {MAX_WAVES} waves")
    for i, wave in enumerate(waves):
        where = f"waves[{i}]"
        if not isinstance(wave, dict):
            raise bad(f"{where} must be an object")
        extra = set(wave) - {"mobs", "delay_ticks"}
        if extra:
            raise bad(f"{where} has unknown field {sorted(extra)[0]}")
        mobs = wave.get("mobs")
        if not isinstance(mobs, dict) or not mobs:
            raise bad(f"{where}.mobs must name mob types and counts")
        total = 0
        for mob, count in mobs.items():
            if not MOB_ID.fullmatch(mob):
                raise bad(f"{where}.mobs {mob} is not a namespaced id")
            if mob in FORBIDDEN_MOBS:
                raise bad(f"{where}.mobs {mob} is not allowed")
            if not _whole(count) or not 1 <= count <= MAX_PER_TYPE:
                raise bad(f"{where}.mobs.{mob} must be 1..{MAX_PER_TYPE}")
            total += count
        if total > MAX_PER_WAVE:
            raise bad(f"{where} holds more than {MAX_PER_WAVE} mobs")
        delay = wave.get("delay_ticks", 0)
        if not _whole(delay) or not 0 <= delay <= MAX_DELAY:
            raise bad(f"{where}.delay_ticks must be 0..{MAX_DELAY}")


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_mob_name(value, where: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_ALLIED_NAME_LEN
        or not value.strip()
    ):
        raise ArenaError(
            "bad_request",
            f"{where} must be 1..{MAX_ALLIED_NAME_LEN} non-blank characters",
        )
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ArenaError("bad_request", f"{where} holds a control character")


def _check_mob_kit(kit, where: str) -> None:
    if not isinstance(kit, dict):
        raise ArenaError("bad_request", f"{where} must be an object")
    extra = set(kit) - KIT_FIELDS
    if extra:
        raise ArenaError("bad_request", f"{where} has unknown field {sorted(extra)[0]}")
    for flag in ("clear_inventory", "heal", "wear"):
        if flag in kit and not isinstance(kit[flag], bool):
            raise ArenaError("bad_request", f"{where}.{flag} must be true or false")
    if "give" in kit:
        give = kit["give"]
        if not isinstance(give, list) or len(give) > MAX_KIT_GIVE:
            raise ArenaError(
                "bad_request", f"{where}.give must be a list of at most {MAX_KIT_GIVE} items"
            )
        for i, entry in enumerate(give):
            g_where = f"{where}.give[{i}]"
            if not isinstance(entry, dict):
                raise ArenaError("bad_request", f"{g_where} must be an object")
            g_extra = set(entry) - GIVE_FIELDS
            if g_extra:
                raise ArenaError(
                    "bad_request", f"{g_where} has unknown field {sorted(g_extra)[0]}"
                )
            item = entry.get("item")
            if not isinstance(item, str) or not MOB_ID.fullmatch(item):
                raise ArenaError("bad_request", f"{g_where}.item must be a namespaced id")
            if "count" in entry:
                cnt = entry["count"]
                if not _whole(cnt) or not 1 <= cnt <= MAX_KIT_COUNT:
                    raise ArenaError(
                        "bad_request", f"{g_where}.count must be 1..{MAX_KIT_COUNT}"
                    )
            if "enchantments" in entry:
                ench = entry["enchantments"]
                if not isinstance(ench, dict) or len(ench) > MAX_KIT_ENCHANTMENTS:
                    raise ArenaError(
                        "bad_request",
                        f"{g_where}.enchantments must map at most {MAX_KIT_ENCHANTMENTS} ids",
                    )
                for eid, lvl in ench.items():
                    if not isinstance(eid, str) or not MOB_ID.fullmatch(eid):
                        raise ArenaError(
                            "bad_request",
                            f"{g_where}.enchantments {eid} is not a namespaced id",
                        )
                    if not _whole(lvl) or not 1 <= lvl <= MAX_KIT_ENCHANTMENT_LEVEL:
                        raise ArenaError(
                            "bad_request",
                            f"{g_where}.enchantments.{eid} must be 1..{MAX_KIT_ENCHANTMENT_LEVEL}",
                        )
            if "potion" in entry:
                potion = entry["potion"]
                if not isinstance(potion, str) or not MOB_ID.fullmatch(potion):
                    raise ArenaError(
                        "bad_request", f"{g_where}.potion must be a namespaced id"
                    )


def check_team_mobs(team_mobs, team_mob_kits=None) -> None:
    """Validate ``team_mobs`` and optional ``team_mob_kits`` with the mod's rules."""
    if team_mobs is not None:
        if not isinstance(team_mobs, dict):
            raise ArenaError(
                "bad_request", "team_mobs must map a team to a list of mob groups"
            )
        for team, groups in team_mobs.items():
            if not isinstance(team, str) or not NAME.fullmatch(team):
                raise ArenaError("bad_request", f"team name {team!r}")
            t_where = f"team_mobs.{team}"
            if not isinstance(groups, list) or len(groups) > MAX_ALLIED_GROUPS_PER_TEAM:
                raise ArenaError(
                    "bad_request",
                    f"{t_where} must be a list of at most {MAX_ALLIED_GROUPS_PER_TEAM} groups",
                )
            for i, group in enumerate(groups):
                g_where = f"{t_where}[{i}]"
                if not isinstance(group, dict):
                    raise ArenaError("bad_request", f"{g_where} must be an object")
                extra = set(group) - ALLIED_GROUP_FIELDS
                if extra:
                    raise ArenaError(
                        "bad_request", f"{g_where} has unknown field {sorted(extra)[0]}"
                    )
                mob_type = group.get("type")
                if not isinstance(mob_type, str) or not MOB_ID.fullmatch(mob_type):
                    raise ArenaError(
                        "bad_request", f"{g_where}.type must be a namespaced id"
                    )
                if mob_type not in SUPPORTED_ALLIED_MOBS:
                    raise ArenaError(
                        "unsupported_mob",
                        f"{mob_type} is not a supported allied mob",
                    )
                count = group.get("count")
                if not _whole(count) or not 1 <= count <= MAX_ALLIED_GROUP_COUNT:
                    raise ArenaError(
                        "bad_request",
                        f"{g_where}.count must be 1..{MAX_ALLIED_GROUP_COUNT}",
                    )
                if "name" in group and group["name"] is not None:
                    _check_mob_name(group["name"], f"{g_where}.name")
                if "kit" in group and group["kit"] is not None:
                    _check_mob_kit(group["kit"], f"{g_where}.kit")
                if "overrides" in group and group["overrides"] is not None:
                    overrides = group["overrides"]
                    if not isinstance(overrides, list):
                        raise ArenaError(
                            "bad_request", f"{g_where}.overrides must be a list"
                        )
                    seen_indices: set[int] = set()
                    for j, ov in enumerate(overrides):
                        ov_where = f"{g_where}.overrides[{j}]"
                        if not isinstance(ov, dict):
                            raise ArenaError(
                                "bad_request", f"{ov_where} must be an object"
                            )
                        ov_extra = set(ov) - ALLIED_OVERRIDE_FIELDS
                        if ov_extra:
                            raise ArenaError(
                                "bad_request",
                                f"{ov_where} has unknown field {sorted(ov_extra)[0]}",
                            )
                        idx = ov.get("index")
                        if not _whole(idx) or not 0 <= idx < count:
                            raise ArenaError(
                                "bad_request",
                                f"{ov_where}.index must be 0..{count - 1}",
                            )
                        if idx in seen_indices:
                            raise ArenaError(
                                "bad_request", f"{ov_where}.index {idx} is duplicated"
                            )
                        seen_indices.add(idx)
                        if "name" in ov and ov["name"] is not None:
                            _check_mob_name(ov["name"], f"{ov_where}.name")
                        if "kit" in ov and ov["kit"] is not None:
                            _check_mob_kit(ov["kit"], f"{ov_where}.kit")
    if team_mob_kits is not None:
        if not isinstance(team_mob_kits, dict):
            raise ArenaError(
                "bad_request", "team_mob_kits must map a team to a kit object"
            )
        for team, kit in team_mob_kits.items():
            if not isinstance(team, str) or not NAME.fullmatch(team):
                raise ArenaError("bad_request", f"team name {team!r}")
            _check_mob_kit(kit, f"team_mob_kits.{team}")


def parse_team_mobs(items: list[str] | None) -> dict[str, list[dict]] | None:
    """``["red=skeleton*2+wolf@Fang", "blue=zombie*3"]`` to ``team_mobs`` dict."""
    if not items:
        return None
    out: dict[str, list[dict]] = {}
    for item in items:
        team, sep, spec = item.partition("=")
        if not sep or not NAME.fullmatch(team) or not spec.strip():
            raise ArenaError(
                "bad_request",
                f"--team-mob wants TEAM=type*count+type (got {item!r})",
            )
        groups = out.setdefault(team, [])
        for raw_part in spec.split("+"):
            part = raw_part.strip()
            if not part:
                raise ArenaError(
                    "bad_request",
                    f"--team-mob wants TEAM=type*count+type (got {item!r})",
                )
            head, has_at, label = part.partition("@")
            kind, has_star, count_str = head.partition("*")
            kind = kind.strip()
            if not kind or (has_star and (not count_str.isdigit() or count_str == "0")):
                raise ArenaError(
                    "bad_request",
                    f"--team-mob wants TEAM=type*count+type (got {item!r})",
                )
            full_type = kind if ":" in kind else f"minecraft:{kind}"
            group: dict = {
                "type": full_type,
                "count": int(count_str) if has_star else 1,
            }
            if has_at:
                group["name"] = label.strip()
            groups.append(group)
    check_team_mobs(out)
    return out


def normalize_respawns(
    respawn_mode: str | None = None,
    respawn_count: int | None = None,
    respawn_delay_ticks: int | None = None,
) -> tuple[str, int, int | None]:
    """Validate and normalize PvP respawn settings.

    ``elimination`` allows 0 extra respawns; ``player`` and ``team`` require an
    integer ``respawn_count`` in ``0..MAX_RESPAWNS``, with ``0`` normalized
    consistently to ``("elimination", 0, delay)``.
    """
    mode = "elimination" if respawn_mode is None else respawn_mode
    if not isinstance(mode, str) or mode not in RESPAWN_MODES:
        raise ArenaError(
            "bad_request",
            f"respawn_mode must be one of {', '.join(RESPAWN_MODES)}",
        )
    if respawn_count is None:
        if mode != "elimination":
            raise ArenaError(
                "bad_request", f"respawn_count is required for {mode} respawns"
            )
        count = 0
    else:
        if not _whole(respawn_count) or not 0 <= respawn_count <= MAX_RESPAWNS:
            raise ArenaError(
                "bad_request", f"respawn_count must be 0..{MAX_RESPAWNS}"
            )
        count = respawn_count
    if mode == "elimination" and count > 0:
        raise ArenaError("bad_request", "elimination allows 0 respawns")
    if mode != "elimination" and count == 0:
        mode = "elimination"
    if respawn_delay_ticks is not None:
        if (
            not _whole(respawn_delay_ticks)
            or not 0 <= respawn_delay_ticks <= MAX_RESPAWN_DELAY_TICKS
        ):
            raise ArenaError(
                "bad_request",
                f"respawn_delay_ticks must be 0..{MAX_RESPAWN_DELAY_TICKS}",
            )
    return mode, count, respawn_delay_ticks


def parse_teams(items: list[str] | None) -> dict[str, list[str]] | None:
    """``["red=Ann,Bo", "blue=Cy"]`` to ``{"red": ["Ann", "Bo"], "blue": ["Cy"]}``."""
    if not items:
        return None
    teams: dict[str, list[str]] = {}
    for item in items:
        team, _, members = item.partition("=")
        players = [p for p in members.split(",") if p]
        if (
            not NAME.fullmatch(team)
            or not players
            or not all(PLAYER.fullmatch(p) for p in players)
        ):
            raise ArenaError(
                "bad_request", f"--team wants name=Player,Player (got {item!r})"
            )
        teams[team] = players
    return teams


# ---- the client --------------------------------------------------------------


class HttpTransport:
    """GET and POST against the body bridge, with its bearer token."""

    def __init__(self, port: int, token: str, timeout: float = 5.0):
        self.port, self.token, self.timeout = port, token, timeout

    def _send(self, request: urllib.request.Request) -> dict:
        request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise ArenaError(f"http_{error.code}", error.reason) from error
        except (urllib.error.URLError, OSError) as error:
            raise ArenaError(
                "unreachable", f"bridge port {self.port}: {error}"
            ) from error

    def get(self, path: str) -> dict:
        return self._send(urllib.request.Request(f"http://127.0.0.1:{self.port}{path}"))

    def post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(request)


_sequence = itertools.count(time.time_ns() // 1000)


class ArenaClient:
    """Sends arena ops to ``/admin`` and reads their answers from ``/events``."""

    def __init__(
        self,
        transport,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.transport, self._clock, self._sleep = transport, clock, sleep
        self.cursor: int | None = None

    @classmethod
    def from_state(
        cls, *, state_file: Path | None = None, settings: dict | None = None
    ) -> "ArenaClient":
        state_file = INSTANCE.state if state_file is None else state_file
        if not state_file.is_file():
            raise ArenaError(
                "server_not_running", "no server state file: start the server first"
            )
        settings = config() if settings is None else settings
        return cls(
            HttpTransport(settings["bridge_port"], read_json(state_file)["token"])
        )

    # -- transport
    def _events(self, after: int) -> dict:
        return self.transport.get(f"/events?after={after}")

    def _cursor(self) -> int:
        if self.cursor is None:
            self.cursor = self._events(2**62)["latest_sequence"]
        return self.cursor

    def call(self, op: str, *, timeout: float = 15.0, **fields) -> dict:
        """One op: its ``<op>_done`` data, or an ArenaError with the mod's reason."""
        self._cursor()
        sequence = next(_sequence)
        self.transport.post(
            "/admin",
            {
                "schema_version": 1,
                "run_id": "arena-cli",
                "episode_id": "arena-cli",
                "body_id": "arena-cli",
                "sequence": sequence,
                "op": op,
                "request_id": uuid.uuid4().hex,
                **fields,
            },
        )
        deadline = self._clock() + timeout
        while True:
            for event in self._poll():
                data = event["data"]
                if data.get("request_sequence") != sequence:
                    continue
                if event["type"] == f"{op}_done":
                    return {
                        k: v
                        for k, v in data.items()
                        if k not in ("request_sequence", "request_id")
                    }
                if event["type"] == f"{op}_failed":
                    raise ArenaError(
                        str(data.get("reason", "failed")), str(data.get("detail", ""))
                    )
            if self._clock() >= deadline:
                raise ArenaError(
                    "timeout",
                    f"{op} was not answered in {timeout:g}s (is the server running?)",
                )
            self._sleep(0.1)

    def _poll(self) -> list[dict]:
        batch = self._events(self._cursor())
        events = sorted(batch["events"], key=lambda e: e["sequence"])
        if events:
            self.cursor = events[-1]["sequence"]
        return events

    def wait_for(
        self,
        name: str,
        kinds: set[str],
        *,
        timeout: float,
        on_event: Callable[[dict], None] | None = None,
    ) -> dict:
        """The next event of arena ``name`` of a type in ``kinds``.

        The arena's other events go to ``on_event``.
        """
        deadline = self._clock() + timeout
        while True:
            for event in self._poll():
                if event["data"].get("name") != name:
                    continue
                if event["type"] in kinds:
                    return event
                if on_event is not None:
                    on_event(event)
            if self._clock() >= deadline:
                raise ArenaError(
                    "timeout", f"no {sorted(kinds)} for {name} in {timeout:g}s"
                )
            self._sleep(0.5)

    # -- players
    def say(self, text: str, to: str | None = None) -> None:
        """A line in game chat (from the bot, or MCB with no bot), to one player."""
        self.transport.post("/say", {"text": text, **({"to": to} if to else {})})

    def online(self) -> dict[str, dict]:
        """Every player online by lowercased name: ``{"name", "fake", ...}``."""
        return {p["name"].lower(): p for p in self.transport.get("/players")["players"]}

    def wait_ready(
        self,
        names: list[str],
        *,
        timeout: float,
        arena: str,
        echo: Callable[[str], None] = print,
        match: tuple[int, int] | None = None,
    ) -> None:
        """Hold until every named player is online and every human among them is
        alive and has typed ``/mcb ready`` (or ``ready`` in chat) since the wait
        began. A player who leaves must be ready again; one on the death screen is
        waited for until they respawn. Fake players (the bot) need only be online.

        Each human is told how to start when they join; ``match`` (number, of) names
        a later match of a series. ``echo`` gets a line each time the wait changes.
        Raises ``players_not_ready`` after ``timeout``."""
        if not names:
            return
        wanted = {n.lower(): n for n in names}
        self._cursor()  # a ready from before the wait does not count
        deadline = self._clock() + timeout
        greeted: set[str] = set()
        ready: set[str] = set()
        last = None
        which = f"Arena {arena}" + (
            f", match {match[0]} of {match[1]}," if match is not None else ""
        )
        while True:
            online = self.online()
            humans = {k for k in wanted if k in online and not online[k].get("fake")}
            for gone in (greeted | ready) - humans:
                greeted.discard(gone)
                ready.discard(gone)
            heard = self._ready_lines(set(wanted) & humans)
            for key in sorted(humans - greeted):
                greeted.add(key)
                self.say(
                    f"{which} is waiting for you: type /mcb ready "
                    "(or ready in chat) when you are set.",
                    to=online[key]["name"],
                )
            absent = [wanted[k] for k in wanted if k not in online]
            dead = [
                wanted[k] for k in wanted
                if k in humans and online[k].get("alive") is False
            ]  # fmt: skip
            new = heard - ready
            ready |= heard
            unready = [wanted[k] for k in wanted if k in humans and k not in ready]
            if not absent and not unready and not dead:
                for key in sorted(humans):
                    self.say(
                        "Everyone is ready: the match starts.", to=online[key]["name"]
                    )
                return
            awaited = ", ".join(absent + dead + [n for n in unready if n not in dead])
            for key in sorted(new):
                self.say(f"Ready. Waiting for {awaited}.", to=online[key]["name"])
            if absent:
                line = f"Waiting for {', '.join(absent)} to join the server."
            elif dead:
                line = f"Waiting for {', '.join(dead)} to respawn."
            else:
                line = f"Waiting for {', '.join(unready)} to type /mcb ready."
            if line != last:
                echo(line)
                last = line
            if self._clock() >= deadline:
                missing = [f"{n} has not joined" for n in absent]
                missing += [f"{n} has not respawned" for n in dead]
                missing += [
                    f"{n} has not typed /mcb ready" for n in unready if n not in dead
                ]
                raise ArenaError(
                    "players_not_ready", f"{'; '.join(missing)} after {timeout:g}s"
                )
            self._sleep(1.0)

    def _ready_lines(self, among: set[str]) -> set[str]:
        """The lowercased names in ``among`` who said ready in the new events."""
        out = set()
        for event in self._poll():
            data = event["data"]
            said = (
                event["type"] == "player_command" and data.get("command") == "ready"
            ) or (
                event["type"] == "player_chat"
                and str(data.get("text", "")).strip().lower() == "ready"
            )
            name = str(data.get("player_name", "")).lower()
            if said and name in among:
                out.add(name)
        return out

    # -- the ops
    def build(
        self,
        plan: dict,
        origin,
        *,
        name: str | None = None,
        dimension: str | None = None,
    ) -> dict:
        extra = {
            k: v for k, v in (("name", name), ("dimension", dimension)) if v is not None
        }
        return self.call("arena_build", plan=plan, origin=list(origin), **extra)

    def remove(self, name: str, *, restore: bool = False) -> dict:
        return self.call("arena_remove", name=name, restore=restore)

    def list(self) -> dict:
        return self.call("arena_list")

    def require_protocol(self) -> dict:
        """The arena list, once the server's mod is known to speak this lab's
        protocol (``check_protocol``). Asked before anything starts: a mismatch is
        then said at once, not as a match that fails after everyone is ready."""
        listing = self.list()
        check_protocol(listing)
        return listing

    def status(self, name: str) -> dict:
        return self.call("arena_status", name=name)

    def start(
        self,
        name: str,
        mode: str,
        *,
        waves=None,
        participants=None,
        teams=None,
        exit=None,  # noqa: A002 - the mod's field name
        series: tuple[int, int] | None = None,
        keep_inventory: bool = False,
        respawn_mode: str = "elimination",
        respawn_count: int | None = None,
        respawn_delay_ticks: int | None = None,
        team_mobs: dict[str, Sequence[dict]] | None = None,
        team_mob_kits: dict[str, dict] | None = None,
    ) -> dict:
        """A match: ``waves`` (co-op), ``teams`` (team against team; waves
        optional, empty for none) or ``race`` (teams race to kill the waves).
        ``exit``: the cell participants still in the arena go to at the end;
        ``series``: (match, of) when it is one of a series (its board and recap
        say which); ``keep_inventory``: keep what participants carry across
        death instead of clearing their inventory (nothing is dropped either way);
        ``respawn_mode`` / ``respawn_count`` / ``respawn_delay_ticks``: PvP
        respawn rules (``teams`` mode only); ``team_mobs`` / ``team_mob_kits``:
        counted allied mob groups and default team mob kits (``teams`` mode only)."""
        if waves:
            check_waves(waves)
        norm_mode, norm_count, norm_delay = normalize_respawns(
            respawn_mode, respawn_count, respawn_delay_ticks
        )
        if mode != "teams" and (norm_mode != "elimination" or norm_delay is not None):
            raise ArenaError("bad_request", "respawns belong to the teams mode")
        if team_mobs is not None or team_mob_kits is not None:
            if mode != "teams":
                raise ArenaError(
                    "bad_request", "team_mobs and team_mob_kits belong to the teams mode"
                )
            check_team_mobs(team_mobs, team_mob_kits)
        fields = {
            "waves": waves or None,
            "participants": participants,
            "teams": teams,
            "exit": None if exit is None else list(exit),
            "series": None if series is None else {"match": series[0], "of": series[1]},
            "keep_inventory": True if keep_inventory else None,
            "respawn_mode": norm_mode if norm_mode != "elimination" else None,
            "respawn_count": norm_count if norm_mode != "elimination" else None,
            "respawn_delay_ticks": norm_delay,
            "team_mobs": team_mobs,
            "team_mob_kits": team_mob_kits,
        }
        return self.call(
            "arena_start",
            name=name,
            mode=mode,
            **{k: v for k, v in fields.items() if v is not None},
        )

    def stop(self, name: str) -> dict:
        return self.call("arena_stop", name=name)

    # -- what the players at an arena see between matches
    def board(self, name: str, title: str, rows: Sequence[dict]) -> dict:
        """The arena's series board on the sidebar: ``rows`` of ``text`` and
        optional ``value``, ``color``, ``bold``, ``struck`` (at most 15)."""
        return self.call("arena_board", name=name, title=title, rows=list(rows))

    def clear_board(self, name: str) -> dict:
        return self.call("arena_board", name=name, clear=True)

    def recap(
        self,
        name: str,
        title: str,
        players: Sequence[dict],
        *,
        teams: Sequence[str] = (),
        lines: Sequence[Sequence[dict]] = (),
    ) -> dict:
        """An after-action table in chat for everyone in the arena's world, drawn
        by the mod like a match's own (``ArenaRecap``): ``title``, the ``lines``
        under it (parts of ``text``, optional ``color`` and ``bold``), then
        ``players`` (``name``, ``team`` when there are ``teams``, and any of
        ``mob_kills``, ``player_kills``, ``deaths``, ``dealt``, ``taken``,
        ``healing``) grouped by ``teams`` in order."""
        extra: dict = {}
        if teams:
            extra["teams"] = list(teams)
        if lines:
            extra["lines"] = [list(line) for line in lines]
        return self.call(
            "arena_recap", name=name, title=title, players=list(players), **extra
        )

    # -- the world and its players
    def spawn(self, position) -> dict:
        """The world spawn, exactly (no spawn radius): the arena server's lobby."""
        return self.call("arena_spawn", position=list(position))

    def op(self, player: str, *, op: bool = True) -> dict:
        """Give (or take away) an online player's operator status."""
        return self.call("arena_op", player=player, operator=op)

    def kit(self, players: Sequence[str], give: Sequence[dict] | None) -> dict:
        """Each player cleared, given ``give``, dressed and healed; ``give`` None
        keeps what they carry and only heals."""
        kit: dict = {"heal": True}
        if give is not None:
            kit = {"give": give, "clear_inventory": True, "heal": True, "wear": True}
        return self.call("arena_kit", players=list(players), kit=kit)
