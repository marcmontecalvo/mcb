"""Authoritative shared arena game lobby across in-game interactions and the dashboard.

One persisted JSON snapshot (default ``runtime/instances/arena/arena-lobby.json``)
and one monotonic ``revision`` govern stock Minecraft Java Edition clients (via
lobby stations and ``/mcb lobby``) and the Lab dashboard (``/api/lobby``,
``/api/lobby/action``).

Design invariants (see ``docs/ARENA_PLAY_PLAN.md`` items 2 & 3):
- Map list, team names, compatible modes, and per-team / mob capacities come from
  the arena plans (``mclab.arena.client.load_plan`` / ``mclab.arena.designs``).
- Fresh PvP (``mode="teams"``) defaults to mob-free (``waves=[]``) and elimination
  respawns (``respawn_mode="elimination"``, ``respawn_count=0``), balanced default.
- First human becomes host; host can transfer control; host departure promotes the
  next human or cleanly resets if empty. Each human controls their own seat; only
  the host edits rules, bots, mob groups, host transfer, or reopens a countdown.
- Every material edit increments ``revision``, clears all human ``ready_revisions``, and
  records a specific reason. Spectators do not vote and joining/leaving as a spectator
  never invalidates readiness.
- Requested bots are tracked independently from connected bots (``connecting``,
  ``connected``, ``failed``); provisioning failures surface cleanly and block countdown.
- Countdown freezes all lobby mutations until launch, participant unready/leave, or host
  reopen. Late arrivals can watch and queue for the next round. Automated zero-human lab
  matches launch without blocking on human readiness.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mclab.arena import client as arena_client
from mclab.arena import designs, standings
from mclab.arena import plan as arena_plan
from mclab.companion_names import BotNames, bot_id_for

SCHEMA = 1
LOBBY_FILE = "arena-lobby.json"
DEFAULT_KIT = "sword+axe+spear+bow+trident+shield+armor+food+potions"
DEFAULT_COUNTDOWN_S = 5.0
SAVE_RETRIES = 40
MODES = ("teams", "waves", "race")
SIDED_MODES = frozenset({"teams", "race"})
WAVE_MODES = frozenset({"waves", "race"})
RESPAWN_MODES = ("elimination", "player", "team")
PLAYER_RE = re.compile(r"[A-Za-z0-9_]{1,16}")
MOB_RE = re.compile(r"[a-z0-9_.-]+:[a-z0-9_./-]+")


class LobbyError(ValueError):
    """Domain error with a structured machine code and current lobby revision."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_action",
        revision: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.revision = revision


class PermissionError_(LobbyError):
    """Raised when a non-host tries to perform a host-only lobby action."""

    def __init__(self, message: str, *, revision: int | None = None):
        super().__init__(message, code="permission_denied", revision=revision)


class StaleRevisionError(LobbyError):
    """Raised when an action or ready click references an outdated lobby revision."""

    def __init__(self, message: str, *, revision: int):
        super().__init__(message, code="stale_revision", revision=revision)


class LobbyFrozenError(LobbyError):
    """Raised when a mutation arrives while countdown is locked."""

    def __init__(self, message: str, *, revision: int | None = None):
        super().__init__(message, code="frozen", revision=revision)


@dataclass(frozen=True)
class MapInfo:
    """Capabilities derived straight from an arena plan."""

    name: str
    teams: tuple[str, ...]
    per_team: int
    max_players: int
    max_mobs: int
    compatible_modes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "teams": list(self.teams),
            "per_team": self.per_team,
            "max_players": self.max_players,
            "max_mobs": self.max_mobs,
            "compatible_modes": list(self.compatible_modes),
        }


def load_maps(
    *,
    arenas_dir: Path | None = None,
    names: tuple[str, ...] | None = None,
) -> dict[str, MapInfo]:
    """Derive available match maps, teams, modes, and capacities from plans."""
    root = arena_client.ARENAS if arenas_dir is None else arenas_dir
    ordered = list(names or designs.NAMES)
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            if path.stem != "lobby" and path.stem not in ordered:
                ordered.append(path.stem)

    out: dict[str, MapInfo] = {}
    for name in ordered:
        if name == "lobby":
            continue
        plan_dict = _read_plan_dict(name, root)
        if plan_dict is None:
            continue
        teams_map = (plan_dict.get("markers") or {}).get("teams") or {}
        teams = tuple(teams_map.keys())
        cap = plan_dict.get("capacity") or {}
        per_team = int(cap.get("per_team") or 1)
        max_mobs = int(cap.get("mobs") or 0)
        modes: list[str] = []
        if len(teams) >= 2:
            modes.append("teams")
        if max_mobs > 0:
            modes.append("waves")
            if len(teams) >= 2:
                modes.append("race")
        if not modes:
            modes.append("waves")
        out[name] = MapInfo(
            name=name,
            teams=teams,
            per_team=per_team,
            max_players=per_team * max(1, len(teams)),
            max_mobs=max_mobs,
            compatible_modes=tuple(modes),
        )
    return out


def _read_plan_dict(name: str, root: Path) -> dict[str, Any] | None:
    try:
        return arena_client.load_plan(name, root=root)
    except arena_client.ArenaError:
        builder = designs.DESIGNS.get(name)
        if builder is None:
            return None
        return arena_plan.to_dict(builder())


def _default_wave_preset() -> str | None:
    presets = arena_client.waves_presets()
    if "mixed-10" in presets:
        return "mixed-10"
    return presets[0] if presets else None


def _valid_player_name(name: Any) -> str:
    if not isinstance(name, str) or not PLAYER_RE.fullmatch(name.strip()):
        raise LobbyError(f"Invalid player name: {name!r}")
    return name.strip()


def _valid_mob_id(mob: Any) -> str:
    if not isinstance(mob, str) or not mob.strip():
        raise LobbyError("Mob type is required")
    text = mob.strip().lower()
    full = text if ":" in text else f"minecraft:{text}"
    if not MOB_RE.fullmatch(full):
        raise LobbyError(f"Invalid mob id: {mob!r}")
    if full in arena_client.FORBIDDEN_MOBS:
        raise LobbyError(f"Mob {full} is not allowed")
    return full


class InProcessFleetAdapter:
    """Adapts a live ``companion_fleet.Fleet`` for ``ArenaLobby`` bot provisioning."""

    def __init__(self, fleet: Any):
        self._fleet = fleet

    def bots(self) -> list[dict[str, Any]]:
        return list(self._fleet.bots()) if hasattr(self._fleet, "bots") else []

    def add_bot(self, bot_id: str, name: str) -> dict[str, Any]:
        try:
            bot = self._fleet.add(bot_id)
            return {"ok": True, "bot": bot}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}

    def remove_bot(self, bot_id: str) -> dict[str, Any]:
        try:
            self._fleet.remove(bot_id)
            return {"ok": True}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}

    def restart_bot(self, bot_id: str, new_name: str) -> dict[str, Any]:
        try:
            bot = self._fleet.restart(bot_id)
            return {"ok": True, "bot": bot}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}


class ApiFleetAdapter:
    """Adapts ``companion_api.ApiClient`` (loopback HTTP) for ``ArenaLobby``."""

    def __init__(self, runtime_dir: Path):
        self._runtime_dir = Path(runtime_dir)

    def _client(self):
        from mclab import companion_api

        return companion_api.ApiClient.from_runtime(
            self._runtime_dir / companion_api.RUNTIME_FILE, timeout=10.0
        )

    def bots(self) -> list[dict[str, Any]]:
        from mclab.companion_api import ApiUnavailable

        try:
            return self._client().bots()
        except ApiUnavailable:
            return []

    def add_bot(self, bot_id: str, name: str) -> dict[str, Any]:
        from mclab.companion_api import ApiUnavailable

        try:
            return self._client().add_bot(bot_id)
        except ApiUnavailable as error:
            return {"ok": False, "error": str(error)}

    def remove_bot(self, bot_id: str) -> dict[str, Any]:
        from mclab.companion_api import ApiUnavailable

        try:
            return self._client().remove_bot(bot_id)
        except ApiUnavailable as error:
            return {"ok": False, "error": str(error)}

    def restart_bot(self, bot_id: str, new_name: str) -> dict[str, Any]:
        from mclab.companion_api import ApiUnavailable

        try:
            return self._client().restart_bot(bot_id)
        except ApiUnavailable as error:
            return {"ok": False, "error": str(error)}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    for attempt in range(SAVE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == SAVE_RETRIES - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.02)


class ArenaLobby:
    """Authoritative game lobby state machine with monotonic revision and JSON save."""

    def __init__(
        self,
        *,
        state_path: Path,
        runtime_dir: Path | None = None,
        fleet: Any = None,
        clock: Callable[[], float] = time.time,
        countdown_seconds: float = DEFAULT_COUNTDOWN_S,
        on_launch: Callable[[dict[str, Any]], None] | None = None,
        arenas_dir: Path | None = None,
        recover: bool = False,
    ):
        self.state_path = Path(state_path)
        self.runtime_dir = Path(runtime_dir) if runtime_dir else self.state_path.parent
        self._fleet = fleet
        self._clock = clock
        self.countdown_seconds = float(countdown_seconds)
        self._on_launch = on_launch
        self._maps = load_maps(arenas_dir=arenas_dir)
        if not self._maps:
            raise LobbyError("No arena maps are available")
        self._lock = threading.RLock()
        self._last_mtime_ns: int = -1
        self._state: dict[str, Any] = self._blank_state()
        if self.state_path.is_file():
            self._load_from_disk(recover=recover)
        else:
            self._save_to_disk()

    @classmethod
    def load(
        cls,
        *,
        state_path: Path,
        runtime_dir: Path | None = None,
        fleet: Any = None,
        clock: Callable[[], float] = time.time,
        countdown_seconds: float = DEFAULT_COUNTDOWN_S,
        on_launch: Callable[[dict[str, Any]], None] | None = None,
        arenas_dir: Path | None = None,
    ) -> ArenaLobby:
        """Load persisted lobby state with safe restart recovery."""
        return cls(
            state_path=state_path,
            runtime_dir=runtime_dir,
            fleet=fleet,
            clock=clock,
            countdown_seconds=countdown_seconds,
            on_launch=on_launch,
            arenas_dir=arenas_dir,
            recover=True,
        )

    def _blank_state(self) -> dict[str, Any]:
        default_map = (
            "crossroads" if "crossroads" in self._maps else next(iter(self._maps))
        )
        info = self._maps[default_map]
        mode = "teams" if "teams" in info.compatible_modes else info.compatible_modes[0]
        return {
            "schema": SCHEMA,
            "revision": 1,
            "phase": "open",  # open | countdown | running | recap
            "host": None,
            "map": default_map,
            "mode": mode,
            "waves": [],
            "waves_preset": None if mode == "teams" else _default_wave_preset(),
            "kit": DEFAULT_KIT,
            "weapon_material": "iron",
            "armor_material": "iron",
            "kit_overrides": {},
            "respawn_mode": "elimination",
            "respawn_count": 0,
            "allow_uneven": False,
            # participant seats: ordered list of {"name": str, "team": str}
            "humans": [],
            # requested bots: bot_id -> {"bot_id", "name", "team", "status", "error"}
            "bots": {},
            # Item 4 extensibility: [{"id", "team", "mob", "count", "kit"?}]
            "mob_groups": [],
            "next_mob_group_id": 1,
            # spectators: ordered list of player names
            "spectators": [],
            # queued for next round: [{"name": str, "preferred_team": str | None}]
            "queued_next": [],
            # human lowercased name -> revision they approved
            "ready_revisions": {},
            "last_change_reason": "Lobby created.",
            "countdown_ends_at": None,
            "countdown_revision": None,
            "active_match": None,
            "last_recap": None,
        }

    # --- persistence & cross-process sync -------------------------------------

    def _sync_from_disk(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            mtime_ns = self.state_path.stat().st_mtime_ns
        except OSError:
            return
        if mtime_ns != self._last_mtime_ns:
            self._load_from_disk(recover=False)

    def _load_from_disk(self, *, recover: bool) -> None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._state = self._blank_state()
            self._save_to_disk()
            return
        if not isinstance(raw, dict):
            self._state = self._blank_state()
            self._save_to_disk()
            return
        merged = self._blank_state()
        for key in merged:
            if key in raw:
                merged[key] = raw[key]
        if merged["map"] not in self._maps:
            merged["map"] = (
                "crossroads" if "crossroads" in self._maps else next(iter(self._maps))
            )
        self._state = merged
        try:
            self._last_mtime_ns = self.state_path.stat().st_mtime_ns
        except OSError:
            self._last_mtime_ns = -1

        if recover:
            changed = False
            if self._state["phase"] in ("countdown", "running"):
                self._state["phase"] = "open"
                self._state["countdown_ends_at"] = None
                self._state["countdown_revision"] = None
                self._state["active_match"] = None
                self._state["ready_revisions"] = {}
                self._state["last_change_reason"] = (
                    "Lobby recovered after restart. Ready up again."
                )
                changed = True
            # Reconcile host against known humans/spectators
            all_people = [h["name"] for h in self._state["humans"]] + list(
                self._state["spectators"]
            )
            if self._state["host"] is not None and not any(
                p.lower() == self._state["host"].lower() for p in all_people
            ):
                self._state["host"] = all_people[0] if all_people else None
                changed = True
            # Reconcile bots against live fleet if present
            if self._state["bots"] and self._fleet is not None:
                self._reconcile_bots_locked()
                changed = True
            if changed:
                self._save_to_disk()

    def _save_to_disk(self) -> None:
        _atomic_write_json(self.state_path, self._state)
        try:
            self._last_mtime_ns = self.state_path.stat().st_mtime_ns
        except OSError:
            pass

    # --- helpers --------------------------------------------------------------

    def _map_info(self, map_name: str | None = None) -> MapInfo:
        name = map_name or self._state["map"]
        info = self._maps.get(name)
        if info is None:
            known = ", ".join(sorted(self._maps))
            raise LobbyError(f"Unknown map {name!r} (available: {known})")
        return info

    def _bot_names(self) -> BotNames:
        try:
            return BotNames.load(self.runtime_dir)
        except ValueError:
            return BotNames()

    def _require_not_frozen(self) -> None:
        if self._state["phase"] == "countdown":
            raise LobbyFrozenError(
                "Lobby is frozen during countdown. "
                "Host can reopen or a player can unready.",
                revision=self._state["revision"],
            )
        if self._state["phase"] == "running":
            raise LobbyError(
                "A match is running right now. "
                "Use watch to spectate and queue for the next round.",
                code="match_running",
                revision=self._state["revision"],
            )

    def _require_expected_revision(self, expected_revision: int | None) -> None:
        if expected_revision is None:
            return
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision != self._state["revision"]
        ):
            raise StaleRevisionError(
                f"Lobby changed to revision {self._state['revision']}: "
                f"{self._state['last_change_reason']}",
                revision=self._state["revision"],
            )

    def _require_host(self, actor: str, *, operator: bool = False) -> None:
        if operator and self._state["host"] is None:
            return
        host = self._state["host"]
        if host is None:
            if operator:
                return
            raise PermissionError_(
                "Only the lobby host can do that; join the lobby first.",
                revision=self._state["revision"],
            )
        if actor.lower() != host.lower():
            raise PermissionError_(
                f"Only host {host} can change lobby settings or manage bots.",
                revision=self._state["revision"],
            )

    def _ensure_host_for_human(self, player: str) -> None:
        if self._state["host"] is None:
            self._state["host"] = player

    def _find_human(self, name: str) -> dict[str, str] | None:
        key = name.lower()
        return next(
            (h for h in self._state["humans"] if h["name"].lower() == key), None
        )

    def _is_spectator(self, name: str) -> bool:
        key = name.lower()
        return any(s.lower() == key for s in self._state["spectators"])

    def _all_humans_in_lobby(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for name in [h["name"] for h in self._state["humans"]] + list(
            self._state["spectators"]
        ):
            if name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
        return out

    def _bump_revision(self, reason: str) -> None:
        self._state["revision"] += 1
        self._state["ready_revisions"] = {}
        if not reason.endswith("Ready up again.") and "ready" not in reason.lower():
            reason = f"{reason.rstrip('.')} Ready up again."
        self._state["last_change_reason"] = reason

    def _cancel_countdown_locked(self, reason: str) -> None:
        if self._state["phase"] == "countdown":
            self._state["phase"] = "open"
            self._state["countdown_ends_at"] = None
            self._state["countdown_revision"] = None
            self._state["last_change_reason"] = reason

    def _team_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for h in self._state["humans"]:
            counts[h["team"]] = counts.get(h["team"], 0) + 1
        for b in self._state["bots"].values():
            counts[b["team"]] = counts.get(b["team"], 0) + 1
        return counts

    def _team_member_counts_including_mobs(self) -> dict[str, int]:
        counts = self._team_counts()
        for g in self._state["mob_groups"]:
            counts[g["team"]] = counts.get(g["team"], 0) + int(g["count"])
        return counts

    def _check_seat_capacity(
        self,
        team: str,
        *,
        moving_human: str | None = None,
        moving_bot: str | None = None,
    ) -> None:
        info = self._map_info()
        if team not in info.teams:
            avail = ", ".join(info.teams)
            raise LobbyError(
                f"Team {team} is not available on {info.name} (available: {avail}).",
                code="invalid_team",
                revision=self._state["revision"],
            )
        team_players = sum(
            1
            for h in self._state["humans"]
            if h["team"] == team
            and (moving_human is None or h["name"].lower() != moving_human.lower())
        ) + sum(
            1
            for bid, b in self._state["bots"].items()
            if b["team"] == team and bid != moving_bot
        )
        if team_players >= info.per_team:
            raise LobbyError(
                f"Team {team} is full on {info.name} "
                f"({team_players}/{info.per_team} per team).",
                code="over_capacity",
                revision=self._state["revision"],
            )
        total_players = sum(
            1
            for h in self._state["humans"]
            if moving_human is None or h["name"].lower() != moving_human.lower()
        ) + sum(1 for bid in self._state["bots"] if bid != moving_bot)
        if total_players >= info.max_players:
            raise LobbyError(
                f"{info.name} holds at most {info.max_players} total players "
                f"({total_players}/{info.max_players}).",
                code="over_capacity",
                revision=self._state["revision"],
            )

    def _check_mob_capacity(self, team: str, add_count: int) -> None:
        info = self._map_info()
        if team not in info.teams:
            avail = ", ".join(info.teams)
            raise LobbyError(
                f"Team {team} is not available on {info.name} (available: {avail}).",
                code="invalid_team",
                revision=self._state["revision"],
            )
        current = sum(int(g["count"]) for g in self._state["mob_groups"])
        if current + add_count > info.max_mobs:
            raise LobbyError(
                f"{info.name} holds at most {info.max_mobs} allied mobs "
                f"(requested {current + add_count}).",
                code="over_capacity",
                revision=self._state["revision"],
            )

    def _reconcile_bots_locked(self) -> None:
        if self._fleet is None:
            return
        try:
            running = {r["id"]: r for r in self._fleet.bots() if r.get("id")}
        except Exception:  # noqa: BLE001
            return
        names = self._bot_names()
        for bot_id, slot in self._state["bots"].items():
            slot["name"] = names.name_for(bot_id)
            row = running.get(bot_id)
            if row is not None:
                if row.get("session") == "ready":
                    slot["status"] = "connected"
                    slot["error"] = None
                elif slot["status"] != "failed":
                    slot["status"] = "connecting"
            elif slot["status"] == "connected":
                slot["status"] = "connecting"

    def _validation_errors_locked(self) -> list[str]:
        info = self._map_info()
        mode = self._state["mode"]
        errors: list[str] = []
        if mode not in info.compatible_modes:
            avail = ", ".join(info.compatible_modes)
            errors.append(
                f"Mode {mode} is not compatible with {info.name} (compatible: {avail})."
            )
        if (
            mode in WAVE_MODES
            and not self._state["waves"]
            and not self._state["waves_preset"]
        ):
            errors.append(f"Mode {mode} requires mob waves.")

        player_counts = self._team_counts()
        member_counts = self._team_member_counts_including_mobs()
        total_players = sum(player_counts.values())
        total_mobs = sum(int(g["count"]) for g in self._state["mob_groups"])

        if total_mobs > info.max_mobs:
            errors.append(
                f"Allied mobs ({total_mobs}) exceed {info.name} "
                f"limit ({info.max_mobs})."
            )

        if mode == "waves":
            if total_players + total_mobs < 1:
                errors.append("Need at least 1 participant to start.")
            if total_players > info.max_players:
                errors.append(
                    f"Players ({total_players}) exceed {info.name} "
                    f"total capacity ({info.max_players})."
                )
            for team, count in player_counts.items():
                if team not in info.teams:
                    avail = ", ".join(info.teams)
                    errors.append(
                        f"Team {team} is not available on {info.name} "
                        f"(available: {avail})."
                    )
                elif count > info.per_team:
                    errors.append(
                        f"Team {team} has {count} players; {info.name} holds "
                        f"at most {info.per_team} per team."
                    )
        else:
            active_teams: dict[str, int] = {}
            for team, count in member_counts.items():
                if count <= 0:
                    continue
                if team not in info.teams:
                    avail = ", ".join(info.teams)
                    errors.append(
                        f"Team {team} is not available on {info.name} "
                        f"(available: {avail})."
                    )
                    continue
                p_count = player_counts.get(team, 0)
                if p_count > info.per_team:
                    errors.append(
                        f"Team {team} has {p_count} players; {info.name} holds "
                        f"at most {info.per_team} per team."
                    )
                active_teams[team] = count
            if len(active_teams) < 2:
                errors.append(f"Mode {mode} needs participants on at least 2 teams.")
            elif not self._state["allow_uneven"]:
                sizes = set(active_teams.values())
                if len(sizes) > 1:
                    detail = ", ".join(f"{t}: {c}" for t, c in active_teams.items())
                    errors.append(
                        f"Teams are uneven ({detail}). "
                        "Balance teams or host can allow uneven teams."
                    )
        return errors

    def _bot_blocking_reasons_locked(self) -> list[str]:
        reasons: list[str] = []
        for slot in self._state["bots"].values():
            if slot["status"] == "failed":
                err = slot.get("error") or "provisioning failed"
                reasons.append(f"Bot {slot['name']} failed to connect: {err}")
            elif slot["status"] != "connected":
                reasons.append(f"Waiting for bot {slot['name']} to connect")
        return reasons

    def _human_blocking_reasons_locked(self) -> list[str]:
        rev = self._state["revision"]
        ready = self._state["ready_revisions"]
        waiting = [
            h["name"]
            for h in self._state["humans"]
            if ready.get(h["name"].lower()) != rev
        ]
        if waiting:
            return [f"Waiting for ready: {', '.join(waiting)}"]
        return []

    def _all_humans_ready_locked(self) -> bool:
        humans = self._state["humans"]
        if not humans:
            return False
        rev = self._state["revision"]
        ready = self._state["ready_revisions"]
        return all(ready.get(h["name"].lower()) == rev for h in humans)

    def _maybe_start_countdown_locked(self) -> bool:
        if self._state["phase"] != "open":
            return False
        if not self._all_humans_ready_locked():
            return False
        if self._validation_errors_locked():
            return False
        if self._bot_blocking_reasons_locked():
            return False
        self._state["phase"] = "countdown"
        self._state["countdown_revision"] = self._state["revision"]
        self._state["countdown_ends_at"] = self._clock() + self.countdown_seconds
        self._state["last_change_reason"] = (
            f"All players ready for rev #{self._state['revision']}. Starting countdown!"
        )
        return True

    def _build_match_config_locked(self) -> dict[str, Any]:
        teams: dict[str, list[str]] = {}
        for h in self._state["humans"]:
            teams.setdefault(h["team"], []).append(h["name"])
        for slot in self._state["bots"].values():
            teams.setdefault(slot["team"], []).append(slot["name"])
        return {
            "map": self._state["map"],
            "mode": self._state["mode"],
            "teams": teams,
            "participants": [n for names in teams.values() for n in names],
            "humans": [h["name"] for h in self._state["humans"]],
            "bots": [copy.deepcopy(b) for b in self._state["bots"].values()],
            "mob_groups": copy.deepcopy(self._state["mob_groups"]),
            "spectators": list(self._state["spectators"]),
            "waves": copy.deepcopy(self._state["waves"]),
            "waves_preset": self._state["waves_preset"],
            "kit": self._state["kit"],
            "weapon_material": self._state["weapon_material"],
            "armor_material": self._state["armor_material"],
            "kit_overrides": copy.deepcopy(self._state["kit_overrides"]),
            "respawn_mode": self._state["respawn_mode"],
            "respawn_count": self._state["respawn_count"],
            "allow_uneven": self._state["allow_uneven"],
            "revision": self._state["revision"],
        }

    def _pick_open_team_locked(self, preferred: str | None = None) -> str | None:
        info = self._map_info()
        counts = self._team_counts()
        if preferred in info.teams and counts.get(preferred, 0) < info.per_team:
            return preferred
        if self._state["mode"] == "waves":
            first = info.teams[0]
            return first if counts.get(first, 0) < info.per_team else None
        candidates = [t for t in info.teams if counts.get(t, 0) < info.per_team]
        if not candidates:
            return None
        # Prefer balancing existing active teams first, then open empty teams
        active = [t for t in candidates if counts.get(t, 0) > 0]
        if len(active) >= 2:
            return min(active, key=lambda t: (counts.get(t, 0), info.teams.index(t)))
        return min(candidates, key=lambda t: (counts.get(t, 0), info.teams.index(t)))

    # --- public read API ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            info = self._map_info()
            teams_out: dict[str, dict[str, Any]] = {}
            all_team_names = list(info.teams)
            for h in self._state["humans"]:
                if h["team"] not in all_team_names:
                    all_team_names.append(h["team"])
            for b in self._state["bots"].values():
                if b["team"] not in all_team_names:
                    all_team_names.append(b["team"])
            for g in self._state["mob_groups"]:
                if g["team"] not in all_team_names:
                    all_team_names.append(g["team"])

            for t in all_team_names:
                t_humans = [h["name"] for h in self._state["humans"] if h["team"] == t]
                t_bots = [
                    copy.deepcopy(b)
                    for b in self._state["bots"].values()
                    if b["team"] == t
                ]
                t_mobs = [
                    copy.deepcopy(g)
                    for g in self._state["mob_groups"]
                    if g["team"] == t
                ]
                teams_out[t] = {
                    "humans": t_humans,
                    "bots": t_bots,
                    "mob_groups": t_mobs,
                    "active": bool(t_humans or t_bots or t_mobs),
                    "player_count": len(t_humans) + len(t_bots),
                    "capacity": info.per_team if t in info.teams else 0,
                }

            val_errors = self._validation_errors_locked()
            bot_blocks = self._bot_blocking_reasons_locked()
            human_blocks = self._human_blocking_reasons_locked()
            hazards_enabled = bool(self._state["waves"] or self._state["waves_preset"])
            if hazards_enabled:
                hazards_summary = (
                    f"Mob waves: {self._state['waves_preset']}"
                    if self._state["waves_preset"]
                    else f"Custom mob waves ({len(self._state['waves'])})"
                )
            else:
                hazards_summary = "No mob waves (mob-free PvP)"

            countdown_remaining = None
            if (
                self._state["phase"] == "countdown"
                and self._state["countdown_ends_at"] is not None
            ):
                countdown_remaining = round(
                    max(0.0, self._state["countdown_ends_at"] - self._clock()), 2
                )

            return {
                "schema": self._state["schema"],
                "revision": self._state["revision"],
                "phase": self._state["phase"],
                "host": self._state["host"],
                "map": self._state["map"],
                "mode": self._state["mode"],
                "compatible_modes": list(info.compatible_modes),
                "maps": {k: v.to_dict() for k, v in self._maps.items()},
                "waves": copy.deepcopy(self._state["waves"]),
                "waves_preset": self._state["waves_preset"],
                "hazards_enabled": hazards_enabled,
                "hazards_summary": hazards_summary,
                "kit": self._state["kit"],
                "weapon_material": self._state["weapon_material"],
                "armor_material": self._state["armor_material"],
                "kit_overrides": copy.deepcopy(self._state["kit_overrides"]),
                "respawn_mode": self._state["respawn_mode"],
                "respawn_count": self._state["respawn_count"],
                "allow_uneven": self._state["allow_uneven"],
                "teams": teams_out,
                "bots": copy.deepcopy(self._state["bots"]),
                "mob_groups": copy.deepcopy(self._state["mob_groups"]),
                "spectators": list(self._state["spectators"]),
                "queued_next": [q["name"] for q in self._state["queued_next"]],
                "ready_revisions": dict(self._state["ready_revisions"]),
                "bots_ready": not bot_blocks,
                "validation_errors": val_errors,
                "blocking_reasons": val_errors + bot_blocks + human_blocks,
                "can_start": not val_errors
                and not bot_blocks
                and self._all_humans_ready_locked(),
                "last_change_reason": self._state["last_change_reason"],
                "countdown_revision": self._state["countdown_revision"],
                "countdown_remaining_s": countdown_remaining,
                "last_recap": copy.deepcopy(self._state["last_recap"]),
            }

    # --- seats & self-control -------------------------------------------------

    def play(
        self, actor: str, *, expected_revision: int | None = None
    ) -> dict[str, Any]:
        """Open/join the lobby, or ready for a rematch if in recap."""
        player = _valid_player_name(actor)
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            if self._state["phase"] == "recap":
                return self.play_again(player)
            if self._state["phase"] == "running":
                return self.watch(player, queue_next=True)
            self._require_not_frozen()
            existing = self._find_human(player)
            if existing is not None:
                self._ensure_host_for_human(player)
                self._save_to_disk()
                return self.snapshot()
            team = self._pick_open_team_locked()
            if team is None:
                info = self._map_info()
                raise LobbyError(
                    f"All teams are full on {info.name} "
                    f"({info.max_players}/{info.max_players}).",
                    code="over_capacity",
                    revision=self._state["revision"],
                )
            return self.join(player, team)

    def join(
        self,
        actor: str,
        team: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Create/activate or join ``team`` (moves player from another team/watch)."""
        player = _valid_player_name(actor)
        team_name = str(team).strip().lower()
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            if self._state["phase"] == "recap":
                self._state["phase"] = "open"
            existing = self._find_human(player)
            self._check_seat_capacity(
                team_name, moving_human=existing["name"] if existing else None
            )
            self._ensure_host_for_human(player)
            # Remove from spectators / queued_next if present
            self._state["spectators"] = [
                s for s in self._state["spectators"] if s.lower() != player.lower()
            ]
            self._state["queued_next"] = [
                q
                for q in self._state["queued_next"]
                if q["name"].lower() != player.lower()
            ]
            if existing is not None:
                if existing["team"] == team_name:
                    self._save_to_disk()
                    return self.snapshot()
                old_team = existing["team"]
                existing["team"] = team_name
                self._bump_revision(
                    f"{player} switched from {old_team} to {team_name}."
                )
            else:
                was_empty = not any(
                    h["team"] == team_name for h in self._state["humans"]
                ) and not any(
                    b["team"] == team_name for b in self._state["bots"].values()
                )
                self._state["humans"].append({"name": player, "team": team_name})
                verb = "created and joined" if was_empty else "joined"
                self._bump_revision(f"{player} {verb} {team_name}.")
            self._save_to_disk()
            return self.snapshot()

    def watch(
        self,
        actor: str,
        *,
        queue_next: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Switch to spectator (or join spectator gallery as a late arrival)."""
        player = _valid_player_name(actor)
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            existing = self._find_human(player)
            if existing is not None:
                if self._state["phase"] == "running":
                    raise LobbyError(
                        "You are in the running match.",
                        code="match_running",
                        revision=self._state["revision"],
                    )
                if self._state["phase"] == "countdown":
                    self._cancel_countdown_locked(
                        f"Countdown cancelled: {player} switched to spectator."
                    )
                self._state["humans"] = [
                    h
                    for h in self._state["humans"]
                    if h["name"].lower() != player.lower()
                ]
                if not self._is_spectator(player):
                    self._state["spectators"].append(player)
                self._bump_revision(f"{player} moved to spectator.")
            else:
                # Fresh spectator / late arrival: never bumps revision or readiness
                if not self._is_spectator(player):
                    self._state["spectators"].append(player)
                if self._state["phase"] == "open":
                    self._ensure_host_for_human(player)
                if queue_next and not any(
                    q["name"].lower() == player.lower()
                    for q in self._state["queued_next"]
                ):
                    self._state["queued_next"].append(
                        {"name": player, "preferred_team": None}
                    )
            self._save_to_disk()
            return self.snapshot()

    def leave(
        self, actor: str, *, expected_revision: int | None = None
    ) -> dict[str, Any]:
        """Leave the lobby; promotes next human host if host leaves."""
        player = _valid_player_name(actor)
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            existing = self._find_human(player)
            was_spectator = self._is_spectator(player)
            was_host = (
                self._state["host"] is not None
                and self._state["host"].lower() == player.lower()
            )
            if existing is not None:
                if self._state["phase"] == "countdown":
                    self._cancel_countdown_locked(
                        f"Countdown cancelled: {player} left the lobby."
                    )
                self._state["humans"] = [
                    h
                    for h in self._state["humans"]
                    if h["name"].lower() != player.lower()
                ]
                self._state["ready_revisions"].pop(player.lower(), None)
                self._bump_revision(f"{player} left the lobby.")
            elif was_spectator:
                # Spectator departure does NOT bump revision or clear readiness
                self._state["spectators"] = [
                    s for s in self._state["spectators"] if s.lower() != player.lower()
                ]
                self._state["queued_next"] = [
                    q
                    for q in self._state["queued_next"]
                    if q["name"].lower() != player.lower()
                ]
            if was_host:
                remaining = self._all_humans_in_lobby()
                if remaining:
                    self._state["host"] = remaining[0]
                    note = f"Host {player} left; {remaining[0]} is now host."
                    if existing is not None:
                        self._state["last_change_reason"] = f"{note} Ready up again."
                    else:
                        self._state["last_change_reason"] = note
                else:
                    self._state["host"] = None
                    if self._state["phase"] == "countdown":
                        self._state["phase"] = "open"
                        self._state["countdown_ends_at"] = None
                        self._state["countdown_revision"] = None
                    self._state["last_change_reason"] = (
                        f"Host {player} left; lobby is open for a new host."
                    )
            self._save_to_disk()
            return self.snapshot()

    def handle_disconnect(self, player: str) -> dict[str, Any]:
        """Handle a human disconnecting (host recovery + countdown cancel)."""
        return self.leave(player)

    # --- host controls: rules -------------------------------------------------

    def transfer_host(
        self,
        actor: str,
        target: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        caller = _valid_player_name(actor)
        new_host_in = _valid_player_name(target)
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_host(caller)
            people = self._all_humans_in_lobby()
            resolved = next(
                (p for p in people if p.lower() == new_host_in.lower()), None
            )
            if resolved is None:
                raise LobbyError(
                    f"{new_host_in} is not in the lobby.",
                    code="unknown_player",
                    revision=self._state["revision"],
                )
            self._state["host"] = resolved
            self._state["last_change_reason"] = (
                f"{caller} transferred host to {resolved}."
            )
            self._save_to_disk()
            return self.snapshot()

    def set_map(
        self,
        actor: str,
        map_name: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            info = self._map_info(str(map_name).strip().lower())
            if self._state["phase"] == "recap":
                self._state["phase"] = "open"
            self._state["map"] = info.name
            extra = ""
            if self._state["mode"] not in info.compatible_modes:
                self._state["mode"] = info.compatible_modes[0]
                if (
                    self._state["mode"] in WAVE_MODES
                    and not self._state["waves"]
                    and not self._state["waves_preset"]
                ):
                    self._state["waves_preset"] = _default_wave_preset()
                extra = f" (mode set to {self._state['mode']})"
            self._bump_revision(f"{actor} selected map {info.name}{extra}.")
            self._save_to_disk()
            return self.snapshot()

    def cycle_map(
        self,
        actor: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            names = list(self._maps)
            idx = (names.index(self._state["map"]) + 1) % len(names)
            return self.set_map(
                actor,
                names[idx],
                operator=operator,
                expected_revision=expected_revision,
            )

    def set_mode(
        self,
        actor: str,
        mode: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        mode_name = str(mode).strip().lower()
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            if mode_name not in MODES:
                raise LobbyError(
                    f"Unknown mode {mode_name!r} (valid: {', '.join(MODES)}).",
                    revision=self._state["revision"],
                )
            info = self._map_info()
            if mode_name not in info.compatible_modes:
                avail = ", ".join(info.compatible_modes)
                raise LobbyError(
                    f"Map {info.name} only supports: {avail} (not {mode_name}).",
                    code="incompatible_mode",
                    revision=self._state["revision"],
                )
            if self._state["phase"] == "recap":
                self._state["phase"] = "open"
            self._state["mode"] = mode_name
            if mode_name == "teams":
                # Fresh PvP defaults mob-free unless host adds waves explicitly
                self._state["waves"] = []
                self._state["waves_preset"] = None
            elif not self._state["waves"] and not self._state["waves_preset"]:
                self._state["waves_preset"] = _default_wave_preset()
            self._bump_revision(f"{actor} set mode to {mode_name}.")
            self._save_to_disk()
            return self.snapshot()

    def cycle_mode(
        self,
        actor: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            compat = list(self._map_info().compatible_modes)
            cur = self._state["mode"]
            nxt = (
                compat[(compat.index(cur) + 1) % len(compat)]
                if cur in compat
                else compat[0]
            )
            return self.set_mode(
                actor, nxt, operator=operator, expected_revision=expected_revision
            )

    def set_waves(
        self,
        actor: str,
        *,
        waves: list[dict[str, Any]] | None = None,
        preset: str | None = None,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            if waves is not None and preset is not None:
                raise LobbyError("Choose custom waves or a wave preset, not both.")
            if preset is not None:
                presets = arena_client.waves_presets()
                if preset not in presets:
                    avail = ", ".join(presets)
                    raise LobbyError(
                        f"Unknown wave preset {preset!r} (available: {avail})."
                    )
                self._state["waves_preset"] = preset
                self._state["waves"] = []
                reason = f"{actor} set waves to {preset}."
            elif waves is not None:
                if waves:
                    try:
                        arena_client.check_waves(waves)
                    except arena_client.ArenaError as error:
                        raise LobbyError(str(error)) from error
                elif self._state["mode"] in WAVE_MODES:
                    raise LobbyError(
                        f"Mode {self._state['mode']} requires at least one wave."
                    )
                self._state["waves"] = copy.deepcopy(waves)
                self._state["waves_preset"] = None
                reason = (
                    f"{actor} set {len(waves)} custom wave(s)."
                    if waves
                    else f"{actor} cleared mob waves (mob-free PvP)."
                )
            else:
                if self._state["mode"] in WAVE_MODES:
                    raise LobbyError(f"Mode {self._state['mode']} requires mob waves.")
                self._state["waves"] = []
                self._state["waves_preset"] = None
                reason = f"{actor} cleared mob waves (mob-free PvP)."
            self._bump_revision(reason)
            self._save_to_disk()
            return self.snapshot()

    def set_kit(
        self,
        actor: str,
        kit: str,
        *,
        weapon_material: str | None = None,
        armor_material: str | None = None,
        team: str | None = None,
        slot: str | None = None,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            if not isinstance(kit, str) or not kit.strip():
                raise LobbyError("Kit must be a non-empty string.")
            clean_kit = kit.strip()
            if team or slot:
                key = f"slot:{slot}" if slot else f"team:{team}"
                self._state["kit_overrides"][key] = clean_kit
                reason = f"{actor} set kit override for {key} to {clean_kit}."
            else:
                self._state["kit"] = clean_kit
                if weapon_material is not None:
                    self._state["weapon_material"] = (
                        str(weapon_material).strip().lower()
                    )
                if armor_material is not None:
                    self._state["armor_material"] = str(armor_material).strip().lower()
                reason = f"{actor} changed kit to {clean_kit}."
            self._bump_revision(reason)
            self._save_to_disk()
            return self.snapshot()

    def set_respawns(
        self,
        actor: str,
        mode: str,
        count: int = 0,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        rmode = str(mode).strip().lower()
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            if rmode not in RESPAWN_MODES:
                raise LobbyError(
                    f"Respawn mode must be one of {', '.join(RESPAWN_MODES)}."
                )
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or count > 99
            ):
                raise LobbyError("Respawn count must be an integer from 0 to 99.")
            if rmode == "elimination" and count != 0:
                raise LobbyError(
                    "Elimination mode cannot have extra respawns (use count 0)."
                )
            if rmode in ("player", "team") and count == 0:
                raise LobbyError(f"Respawn mode {rmode} needs count >= 1.")
            self._state["respawn_mode"] = rmode
            self._state["respawn_count"] = count
            desc = "elimination" if rmode == "elimination" else f"{rmode} ({count})"
            self._bump_revision(f"{actor} set respawns to {desc}.")
            self._save_to_disk()
            return self.snapshot()

    def cycle_respawns(
        self,
        actor: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        presets = (
            ("elimination", 0),
            ("player", 1),
            ("player", 2),
            ("team", 2),
            ("team", 4),
        )
        with self._lock:
            self._sync_from_disk()
            cur = (self._state["respawn_mode"], self._state["respawn_count"])
            nxt = (
                presets[(presets.index(cur) + 1) % len(presets)]
                if cur in presets
                else presets[0]
            )
            return self.set_respawns(
                actor,
                nxt[0],
                nxt[1],
                operator=operator,
                expected_revision=expected_revision,
            )

    def set_allow_uneven(
        self,
        actor: str,
        allow: bool,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            flag = bool(allow)
            self._state["allow_uneven"] = flag
            label = "enabled" if flag else "disabled"
            self._bump_revision(f"{actor} {label} uneven teams.")
            self._save_to_disk()
            return self.snapshot()

    # --- host controls: bots & mob groups -------------------------------------

    def _next_bot_id_locked(self) -> str:
        used = set(self._state["bots"])
        n = 1
        while bot_id_for(n) in used:
            n += 1
        return bot_id_for(n)

    def _resolve_bot_id_locked(self, ref: str) -> str:
        key = str(ref).strip().lower()
        for bid, slot in self._state["bots"].items():
            if bid.lower() == key or slot["name"].lower() == key:
                return bid
        names = self._bot_names()
        mapped = names.id_for(ref)
        if mapped and mapped in self._state["bots"]:
            return mapped
        raise LobbyError(
            f"No bot {ref!r} in the lobby.",
            code="unknown_bot",
            revision=self._state["revision"],
        )

    def _provision_bot_locked(self, bot_id: str, name: str) -> tuple[str, str | None]:
        if self._fleet is None:
            return "connected", None
        try:
            running = {r["id"]: r for r in self._fleet.bots() if r.get("id")}
        except Exception as error:  # noqa: BLE001
            return "failed", str(error)
        row = running.get(bot_id)
        if row is not None:
            return (
                "connected" if row.get("session") == "ready" else "connecting"
            ), None
        reply = self._fleet.add_bot(bot_id, name)
        if not reply.get("ok"):
            return "failed", str(reply.get("error") or "bot provisioning failed")
        # Check if already ready immediately
        try:
            after = {r["id"]: r for r in self._fleet.bots() if r.get("id")}
        except Exception:  # noqa: BLE001
            after = {}
        if after.get(bot_id, {}).get("session") == "ready":
            return "connected", None
        return "connecting", None

    def add_bot(
        self,
        actor: str,
        team: str | None = None,
        *,
        name: str | None = None,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            if self._state["phase"] == "recap":
                self._state["phase"] = "open"
            team_name = (
                str(team).strip().lower()
                if team is not None
                else self._pick_open_team_locked()
            )
            if team_name is None:
                info = self._map_info()
                raise LobbyError(
                    f"All teams are full on {info.name} "
                    f"({info.max_players}/{info.max_players}).",
                    code="over_capacity",
                    revision=self._state["revision"],
                )
            self._check_seat_capacity(team_name)
            bot_id = self._next_bot_id_locked()
            names = self._bot_names()
            if name is not None:
                clean_name = _valid_player_name(name)
                if any(
                    h["name"].lower() == clean_name.lower()
                    for h in self._state["humans"]
                ):
                    raise LobbyError(f"{clean_name} is already a human player's name.")
                try:
                    names = names.rename(bot_id, clean_name)
                    names.save(self.runtime_dir)
                except ValueError as error:
                    raise LobbyError(str(error)) from error
            display_name = names.name_for(bot_id)
            status, err = self._provision_bot_locked(bot_id, display_name)
            slot = {
                "bot_id": bot_id,
                "name": display_name,
                "team": team_name,
                "status": status,
                "error": err,
            }
            self._state["bots"][bot_id] = slot
            self._bump_revision(f"{team_name.capitalize()} added {display_name}.")
            self._save_to_disk()
            return copy.deepcopy(slot)

    def retry_bot(
        self,
        actor: str,
        bot_ref: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            bot_id = self._resolve_bot_id_locked(bot_ref)
            slot = self._state["bots"][bot_id]
            status, err = self._provision_bot_locked(bot_id, slot["name"])
            slot["status"] = status
            slot["error"] = err
            self._save_to_disk()
            return copy.deepcopy(slot)

    def sync_bots(self) -> dict[str, Any]:
        """Reconcile requested bot slots with live companion fleet status."""
        with self._lock:
            self._sync_from_disk()
            self._reconcile_bots_locked()
            self._maybe_start_countdown_locked()
            self._save_to_disk()
            return self.snapshot()

    def rename_bot(
        self,
        actor: str,
        bot_ref: str,
        new_name: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        clean_new = _valid_player_name(new_name)
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            bot_id = self._resolve_bot_id_locked(bot_ref)
            if any(
                h["name"].lower() == clean_new.lower() for h in self._state["humans"]
            ):
                raise LobbyError(f"{clean_new} is already a human player's name.")
            names = self._bot_names()
            old_name = self._state["bots"][bot_id]["name"]
            try:
                updated = names.rename(bot_id, clean_new)
                updated.save(self.runtime_dir)
            except ValueError as error:
                raise LobbyError(str(error)) from error
            slot = self._state["bots"][bot_id]
            slot["name"] = updated.name_for(bot_id)
            if self._fleet is not None:
                reply = self._fleet.restart_bot(bot_id, slot["name"])
                if not reply.get("ok"):
                    slot["status"] = "failed"
                    slot["error"] = str(reply.get("error") or "restart failed")
                else:
                    self._reconcile_bots_locked()
            self._bump_revision(f"{actor} renamed {old_name} to {slot['name']}.")
            self._save_to_disk()
            return self.snapshot()

    def move_bot(
        self,
        actor: str,
        bot_ref: str,
        team: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        team_name = str(team).strip().lower()
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            bot_id = self._resolve_bot_id_locked(bot_ref)
            slot = self._state["bots"][bot_id]
            if slot["team"] == team_name:
                return self.snapshot()
            self._check_seat_capacity(team_name, moving_bot=bot_id)
            old_team = slot["team"]
            slot["team"] = team_name
            self._bump_revision(
                f"{actor} moved {slot['name']} from {old_team} to {team_name}."
            )
            self._save_to_disk()
            return self.snapshot()

    def remove_bot(
        self,
        actor: str,
        bot_ref: str | None = None,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            if not self._state["bots"]:
                raise LobbyError("There are no bots in the lobby.")
            bot_id = (
                self._resolve_bot_id_locked(bot_ref)
                if bot_ref is not None
                else list(self._state["bots"])[-1]
            )
            slot = self._state["bots"].pop(bot_id)
            if self._fleet is not None:
                try:
                    self._fleet.remove_bot(bot_id)
                except Exception:  # noqa: BLE001
                    pass
            self._bump_revision(f"{actor} removed bot {slot['name']}.")
            self._save_to_disk()
            return self.snapshot()

    def add_mob_group(
        self,
        actor: str,
        team: str,
        mob: str,
        count: int,
        *,
        kit: str | None = None,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        team_name = str(team).strip().lower()
        mob_id = _valid_mob_id(mob)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise LobbyError("Mob group count must be a positive integer.")
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            self._check_mob_capacity(team_name, count)
            gid = f"m-{self._state['next_mob_group_id']}"
            self._state["next_mob_group_id"] += 1
            group = {
                "id": gid,
                "team": team_name,
                "mob": mob_id,
                "count": count,
                **({"kit": kit} if kit else {}),
            }
            self._state["mob_groups"].append(group)
            short_mob = mob_id.removeprefix("minecraft:")
            self._bump_revision(
                f"{team_name.capitalize()} added {count}x {short_mob}."
            )
            self._save_to_disk()
            return copy.deepcopy(group)

    def move_mob_group(
        self,
        actor: str,
        group_id: str,
        team: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        team_name = str(team).strip().lower()
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            info = self._map_info()
            if team_name not in info.teams:
                avail = ", ".join(info.teams)
                raise LobbyError(
                    f"Team {team_name} is not available on {info.name} "
                    f"(available: {avail})."
                )
            group = next(
                (g for g in self._state["mob_groups"] if g["id"] == group_id), None
            )
            if group is None:
                raise LobbyError(f"Unknown mob group {group_id!r}.")
            group["team"] = team_name
            self._bump_revision(f"{actor} moved mob group {group_id} to {team_name}.")
            self._save_to_disk()
            return self.snapshot()

    def remove_mob_group(
        self,
        actor: str,
        group_id: str,
        *,
        operator: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            self._require_not_frozen()
            self._require_host(actor, operator=operator)
            before = len(self._state["mob_groups"])
            self._state["mob_groups"] = [
                g for g in self._state["mob_groups"] if g["id"] != group_id
            ]
            if len(self._state["mob_groups"]) == before:
                raise LobbyError(f"Unknown mob group {group_id!r}.")
            self._bump_revision(f"{actor} removed mob group {group_id}.")
            self._save_to_disk()
            return self.snapshot()

    # --- readiness, countdown, launch & rematch -------------------------------

    def ready(self, actor: str, *, seen_revision: int | None = None) -> dict[str, Any]:
        player = _valid_player_name(actor)
        with self._lock:
            self._sync_from_disk()
            if self._state["phase"] == "running":
                raise LobbyError(
                    "A match is already running.",
                    code="match_running",
                    revision=self._state["revision"],
                )
            if self._is_spectator(player) and self._find_human(player) is None:
                raise LobbyError(
                    "Spectators do not vote to start. Join a team first.",
                    code="spectator_cannot_ready",
                    revision=self._state["revision"],
                )
            if self._find_human(player) is None:
                raise LobbyError(
                    "Join a team before readying up.",
                    code="not_participant",
                    revision=self._state["revision"],
                )
            self._require_expected_revision(seen_revision)
            if self._state["phase"] == "recap":
                self._state["phase"] = "open"
            self._state["ready_revisions"][player.lower()] = self._state["revision"]
            self._reconcile_bots_locked()
            self._maybe_start_countdown_locked()
            self._save_to_disk()
            return self.snapshot()

    def unready(self, actor: str) -> dict[str, Any]:
        player = _valid_player_name(actor)
        with self._lock:
            self._sync_from_disk()
            if self._find_human(player) is None:
                raise LobbyError("You are not a seated participant.")
            self._state["ready_revisions"].pop(player.lower(), None)
            if self._state["phase"] == "countdown":
                self._cancel_countdown_locked(
                    f"Countdown cancelled: {player} unreadied."
                )
            self._save_to_disk()
            return self.snapshot()

    def reopen(self, actor: str, *, operator: bool = False) -> dict[str, Any]:
        with self._lock:
            self._sync_from_disk()
            self._require_host(actor, operator=operator)
            if self._state["phase"] != "countdown":
                raise LobbyError("Lobby is not in countdown.")
            self._state["phase"] = "open"
            self._state["countdown_ends_at"] = None
            self._state["countdown_revision"] = None
            self._bump_revision(f"{actor} reopened the lobby.")
            self._save_to_disk()
            return self.snapshot()

    def tick(self, now: float | None = None) -> dict[str, Any] | None:
        """Advance countdown timer; atomically launches match when complete."""
        launch_cfg: dict[str, Any] | None = None
        with self._lock:
            self._sync_from_disk()
            if self._state["phase"] != "countdown":
                return None
            current_time = self._clock() if now is None else float(now)
            ends_at = self._state["countdown_ends_at"]
            if ends_at is None or current_time < ends_at:
                return None
            self._reconcile_bots_locked()
            if (
                self._state["countdown_revision"] != self._state["revision"]
                or not self._all_humans_ready_locked()
                or self._validation_errors_locked()
                or self._bot_blocking_reasons_locked()
            ):
                self._cancel_countdown_locked(
                    "Countdown cancelled: lobby state changed before launch."
                )
                self._save_to_disk()
                return None
            launch_cfg = self._build_match_config_locked()
            self._state["phase"] = "running"
            self._state["countdown_ends_at"] = None
            self._state["active_match"] = launch_cfg
            self._state["last_change_reason"] = (
                f"Match started on {launch_cfg['map']} ({launch_cfg['mode']})."
            )
            self._save_to_disk()
        if launch_cfg is not None and self._on_launch is not None:
            self._on_launch(copy.deepcopy(launch_cfg))
        return launch_cfg

    def launch_automated(self, *, actor: str = "lab") -> dict[str, Any]:
        """Start an automated zero-human lab match without human readiness."""
        launch_cfg: dict[str, Any]
        with self._lock:
            self._sync_from_disk()
            self._require_not_frozen()
            if self._state["humans"]:
                raise LobbyError(
                    "Automated launch is only for zero-human matches; "
                    "use ready check with humans."
                )
            self._reconcile_bots_locked()
            errors = (
                self._validation_errors_locked() + self._bot_blocking_reasons_locked()
            )
            if errors:
                raise LobbyError("; ".join(errors), revision=self._state["revision"])
            launch_cfg = self._build_match_config_locked()
            self._state["phase"] = "running"
            self._state["active_match"] = launch_cfg
            self._state["last_change_reason"] = (
                f"Automated match started by {actor} on {launch_cfg['map']}."
            )
            self._save_to_disk()
        if self._on_launch is not None:
            self._on_launch(copy.deepcopy(launch_cfg))
        return launch_cfg

    def complete_match(self, recap: dict[str, Any]) -> dict[str, Any]:
        """Return to lobby on match end, show recap, and fold queued arrivals."""
        with self._lock:
            self._sync_from_disk()
            self._state["phase"] = "recap"
            self._state["active_match"] = None
            self._state["countdown_ends_at"] = None
            self._state["countdown_revision"] = None
            self._state["ready_revisions"] = {}
            self._state["last_recap"] = copy.deepcopy(recap)

            folded: list[str] = []
            still_spectating: list[str] = []
            for item in self._state["queued_next"]:
                name = item["name"]
                if self._find_human(name) is not None:
                    continue
                team = self._pick_open_team_locked(item.get("preferred_team"))
                if team is not None:
                    self._state["humans"].append({"name": name, "team": team})
                    self._state["spectators"] = [
                        s
                        for s in self._state["spectators"]
                        if s.lower() != name.lower()
                    ]
                    folded.append(f"{name}->{team}")
                else:
                    still_spectating.append(name)
            self._state["queued_next"] = []
            if folded:
                self._state["revision"] += 1
                note = f"Match complete. Queued players joined: {', '.join(folded)}."
            else:
                note = "Match complete. Click Play Again or Change Teams."
            if still_spectating:
                note += f" (Full; still spectating: {', '.join(still_spectating)}.)"
            self._state["last_change_reason"] = note
            self._save_to_disk()
            return self.snapshot()

    def play_again(
        self, actor: str, *, expected_revision: int | None = None
    ) -> dict[str, Any]:
        """Keep lineup and start a fresh ready check (marks caller ready)."""
        player = _valid_player_name(actor)
        with self._lock:
            self._sync_from_disk()
            self._require_expected_revision(expected_revision)
            if self._state["phase"] == "running":
                raise LobbyError("Match is still running.")
            if self._state["phase"] == "recap":
                self._state["phase"] = "open"
                self._state["ready_revisions"] = {}
                self._state["last_change_reason"] = (
                    f"{player} started Play Again. Ready up!"
                )
            if self._find_human(player) is not None:
                self._state["ready_revisions"][player.lower()] = self._state["revision"]
                self._reconcile_bots_locked()
                self._maybe_start_countdown_locked()
            self._save_to_disk()
            return self.snapshot()

    def change_teams(self, actor: str) -> dict[str, Any]:
        """Move from recap back to open lobby for team/rule adjustments."""
        player = _valid_player_name(actor)
        with self._lock:
            self._sync_from_disk()
            if self._state["phase"] == "running":
                raise LobbyError("Match is still running.")
            if self._state["phase"] == "countdown":
                raise LobbyFrozenError(
                    "Lobby is frozen during countdown.",
                    revision=self._state["revision"],
                )
            self._state["phase"] = "open"
            self._state["ready_revisions"] = {}
            self._state["last_change_reason"] = (
                f"{player} opened Change Teams. Adjust sides and ready up."
            )
            self._save_to_disk()
            return self.snapshot()

    def fail_match(self, error: str) -> dict[str, Any]:
        """Recover cleanly back to open if starting or running a match fails."""
        with self._lock:
            self._sync_from_disk()
            self._state["phase"] = "open"
            self._state["active_match"] = None
            self._state["countdown_ends_at"] = None
            self._state["countdown_revision"] = None
            self._state["ready_revisions"] = {}
            detail = str(error or "unknown error").strip()
            self._state["last_change_reason"] = (
                f"Match failed to start: {detail}. Ready up again."
            )
            self._save_to_disk()
            return self.snapshot()

    # --- unified action dispatcher (in-game stations, /mcb lobby, dashboard) ---

    def handle_action(
        self,
        actor: str,
        payload: dict[str, Any],
        *,
        operator: bool = False,
    ) -> dict[str, Any]:
        """Dispatch a structured lobby command from game or dashboard."""
        action = str(payload.get("action") or "status").strip().lower()
        exp = payload.get("expected_revision")
        seen = payload.get("seen_revision", exp)
        if action == "status":
            snap = self.snapshot()
            return {"ok": True, "message": self.status_text(snap), "lobby": snap}
        if action == "play":
            snap = self.play(actor, expected_revision=exp)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action in ("watch", "spectate"):
            q = payload.get("queue_next")
            if q is None:
                q = self.snapshot()["phase"] in ("countdown", "running")
            snap = self.watch(actor, queue_next=bool(q), expected_revision=exp)
            msg = (
                f"{actor} is watching (queued for next round)."
                if q
                else f"{actor} is watching."
            )
            return {"ok": True, "message": msg, "lobby": snap}
        if action in ("rematch", "play_again"):
            snap = self.play_again(actor, expected_revision=exp)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action in ("change_teams", "teams"):
            snap = self.change_teams(actor)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "join":
            team = payload.get("team")
            if not team:
                raise LobbyError(
                    "Specify a team to join (e.g. red, blue, green, yellow)."
                )
            snap = self.join(actor, str(team), expected_revision=exp)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "leave":
            snap = self.leave(actor, expected_revision=exp)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "ready":
            snap = self.ready(actor, seen_revision=seen)
            msg = (
                snap["last_change_reason"]
                if snap["phase"] == "countdown"
                else f"{actor} is ready (rev #{snap['revision']})."
            )
            return {"ok": True, "message": msg, "lobby": snap}
        if action == "ready_toggle":
            cur = self.snapshot()
            if cur["ready_revisions"].get(actor.lower()) == cur["revision"]:
                snap = self.unready(actor)
                return {"ok": True, "message": f"{actor} unreadied.", "lobby": snap}
            snap = self.ready(actor, seen_revision=seen)
            msg = (
                snap["last_change_reason"]
                if snap["phase"] == "countdown"
                else f"{actor} is ready (rev #{snap['revision']})."
            )
            return {"ok": True, "message": msg, "lobby": snap}
        if action == "unready":
            snap = self.unready(actor)
            return {"ok": True, "message": f"{actor} unreadied.", "lobby": snap}
        if action == "reopen":
            snap = self.reopen(actor, operator=operator)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "map":
            snap = self.set_map(
                actor,
                str(payload.get("map") or ""),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "cycle_map":
            snap = self.cycle_map(actor, operator=operator, expected_revision=exp)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "mode":
            snap = self.set_mode(
                actor,
                str(payload.get("mode") or ""),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "cycle_mode":
            snap = self.cycle_mode(actor, operator=operator, expected_revision=exp)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action in ("waves", "hazards"):
            snap = self.set_waves(
                actor,
                waves=payload.get("waves"),
                preset=payload.get("preset"),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "kit":
            snap = self.set_kit(
                actor,
                str(payload.get("kit") or ""),
                weapon_material=payload.get("weapon_material"),
                armor_material=payload.get("armor_material"),
                team=payload.get("team"),
                slot=payload.get("slot"),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "respawns":
            rmode = payload.get("respawn_mode", payload.get("mode", "elimination"))
            rcount = payload.get("respawn_count", payload.get("count", 0))
            snap = self.set_respawns(
                actor,
                str(rmode),
                int(rcount),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "cycle_respawns":
            snap = self.cycle_respawns(actor, operator=operator, expected_revision=exp)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "uneven":
            raw = payload.get("allow", payload.get("value", payload.get("enabled")))
            if isinstance(raw, str):
                allow = raw.strip().lower() in ("on", "true", "yes", "1")
            else:
                allow = bool(raw)
            snap = self.set_allow_uneven(
                actor, allow, operator=operator, expected_revision=exp
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "toggle_uneven":
            cur = self.snapshot()["allow_uneven"]
            snap = self.set_allow_uneven(
                actor, not cur, operator=operator, expected_revision=exp
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action in ("host", "transfer_host"):
            target = payload.get("target") or payload.get("player") or ""
            snap = self.transfer_host(actor, str(target), expected_revision=exp)
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "bot_add":
            slot = self.add_bot(
                actor,
                payload.get("team"),
                name=payload.get("name"),
                operator=operator,
                expected_revision=exp,
            )
            snap = self.snapshot()
            msg = snap["last_change_reason"]
            if slot["status"] == "failed":
                msg += f" (Warning: {slot['name']} failed: {slot['error']})"
            return {"ok": True, "message": msg, "bot": slot, "lobby": snap}
        if action == "bot_rename":
            snap = self.rename_bot(
                actor,
                str(payload.get("bot") or ""),
                str(payload.get("name") or payload.get("to") or ""),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "bot_move":
            snap = self.move_bot(
                actor,
                str(payload.get("bot") or ""),
                str(payload.get("team") or ""),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "bot_remove":
            snap = self.remove_bot(
                actor,
                payload.get("bot"),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "bot_retry":
            slot = self.retry_bot(
                actor,
                str(payload.get("bot") or ""),
                operator=operator,
                expected_revision=exp,
            )
            return {
                "ok": True,
                "message": f"Retried {slot['name']}: {slot['status']}.",
                "lobby": self.snapshot(),
            }
        if action == "mob_add":
            group = self.add_mob_group(
                actor,
                str(payload.get("team") or ""),
                str(payload.get("mob") or ""),
                int(payload.get("count", 1)),
                kit=payload.get("kit"),
                operator=operator,
                expected_revision=exp,
            )
            snap = self.snapshot()
            return {
                "ok": True,
                "message": snap["last_change_reason"],
                "group": group,
                "lobby": snap,
            }
        if action == "mob_move":
            snap = self.move_mob_group(
                actor,
                str(payload.get("group_id") or ""),
                str(payload.get("team") or ""),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "mob_remove":
            snap = self.remove_mob_group(
                actor,
                str(payload.get("group_id") or ""),
                operator=operator,
                expected_revision=exp,
            )
            return {"ok": True, "message": snap["last_change_reason"], "lobby": snap}
        if action == "launch_automated":
            cfg = self.launch_automated(actor=actor)
            return {
                "ok": True,
                "message": "Automated match started.",
                "match": cfg,
                "lobby": self.snapshot(),
            }
        raise LobbyError(f"Unknown lobby action: {action!r}")

    # --- sidebar board & chat formatting --------------------------------------

    def board_rows(
        self, snap: dict[str, Any] | None = None
    ) -> tuple[str, list[dict[str, Any]]]:
        """Format lobby state for ``ArenaClient.board`` (<= 15 rows, <= 40 chars)."""
        s = snap if snap is not None else self.snapshot()
        phase = s["phase"]
        if phase == "countdown" and s["countdown_remaining_s"] is not None:
            title = (
                f"Starting in {int(s['countdown_remaining_s'] + 0.99)}s · {s['map']}"
            )
        elif phase == "recap":
            title = f"Recap · {s['map']}"
        elif phase == "running":
            title = f"Live · {s['map']} ({s['mode']})"
        else:
            title = f"Lobby · {s['map']} · {s['mode']}"
        title = title[:64]

        sections: list[tuple[dict[str, Any] | None, list[dict[str, Any]]]] = []
        respawn_lbl = (
            "Elim"
            if s["respawn_mode"] == "elimination"
            else f"{s['respawn_mode'][0].upper()}:{s['respawn_count']}"
        )
        rules_val = f"{'Mobs' if s['hazards_enabled'] else 'NoMob'} {respawn_lbl}"
        sections.append(
            (
                {
                    "text": f"Host: {s['host'] or 'open'}"[:40],
                    "value": f"#{s['revision']}"[:16],
                    "color": "gold",
                },
                [
                    {
                        "text": ("Uneven OK" if s["allow_uneven"] else "Balanced")[:40],
                        "value": rules_val[:16],
                        "color": "gray",
                    }
                ],
            )
        )
        rev = s["revision"]
        ready = s["ready_revisions"]
        for i, (team, info) in enumerate(s["teams"].items()):
            color = standings.team_color(team, i)
            head = {
                "text": team.capitalize()[:40],
                "value": f"{info['player_count']}/{info['capacity']}"[:16],
                "color": color,
                "bold": True,
            }
            members: list[dict[str, Any]] = []
            for name in info["humans"]:
                tag = "ok" if ready.get(name.lower()) == rev else "..."
                members.append({"text": f" {name}"[:40], "value": tag, "color": color})
            for bot in info["bots"]:
                st = (
                    "bot"
                    if bot["status"] == "connected"
                    else ("ERR" if bot["status"] == "failed" else "...")
                )
                members.append(
                    {
                        "text": f" {bot['name']}"[:40],
                        "value": st,
                        "color": "red" if bot["status"] == "failed" else color,
                    }
                )
            for grp in info["mob_groups"]:
                short = grp["mob"].removeprefix("minecraft:")
                members.append(
                    {
                        "text": f" {short}"[:40],
                        "value": f"x{grp['count']}"[:16],
                        "color": color,
                    }
                )
            if info["active"] or len(s["teams"]) <= 2:
                sections.append((head, members))
        if s["spectators"]:
            val = (
                f"{len(s['spectators'])} ({len(s['queued_next'])} next)"
                if s["queued_next"]
                else str(len(s["spectators"]))
            )
            sections.append(
                (
                    {"text": "Spectators", "value": val[:16], "color": "aqua"},
                    [],
                )
            )
        return title, standings.fit(sections)

    def status_text(self, snap: dict[str, Any] | None = None) -> str:
        """One concise chat summary for in-game ``/mcb lobby status``."""
        s = snap if snap is not None else self.snapshot()
        parts = [
            f"Lobby #{s['revision']} ({s['phase']}): {s['map']} {s['mode']}",
            s["hazards_summary"],
            f"host={s['host'] or 'open'}",
        ]
        team_bits = []
        for team, info in s["teams"].items():
            members = info["humans"] + [b["name"] for b in info["bots"]]
            if members or info["mob_groups"]:
                m_str = ", ".join(members)
                if info["mob_groups"]:
                    mobs = ", ".join(
                        f"{g['count']}x {g['mob'].removeprefix('minecraft:')}"
                        for g in info["mob_groups"]
                    )
                    m_str = f"{m_str}, {mobs}" if m_str else mobs
                team_bits.append(
                    f"{team}({info['player_count']}/{info['capacity']}): {m_str}"
                )
        if team_bits:
            parts.append(" | ".join(team_bits))
        if s["spectators"]:
            parts.append(f"watching: {', '.join(s['spectators'])}")
        if s["blocking_reasons"]:
            parts.append(s["blocking_reasons"][0])
        return " · ".join(parts)


_DEFAULT_LOCK = threading.Lock()
_DEFAULT_LOBBIES: dict[Path, ArenaLobby] = {}


def make_arena_launcher(
    *,
    runtime_dir: Path,
    lobby_getter: Callable[[], ArenaLobby],
    board: Callable[[str, str, list[dict[str, Any]]], Any] | None = None,
    prepare_fn: Callable[..., dict[str, Any]] | None = None,
    run_series_fn: Callable[..., Any] | None = None,
    admin_factory: Callable[[], Any] | None = None,
    api_factory: Callable[[], Any] | None = None,
    out_dir: Path | None = None,
    match_timeout_s: float = 600.0,
    async_launch: bool = True,
) -> Callable[[dict[str, Any]], None]:
    """Build an ``on_launch`` hook running headless lobby matches via ``arena_hunt``."""
    rt_dir = Path(runtime_dir)

    def _update_board(snap: dict[str, Any] | None = None) -> None:
        if board is None:
            return
        try:
            lobby = lobby_getter()
            title, rows = lobby.board_rows(snap)
            board("lobby", title, rows)
        except Exception:  # noqa: BLE001
            pass

    def _run(cfg: dict[str, Any]) -> None:
        try:
            from mclab import arena_hunt, combat_trial, companion_api
            from mclab.arena import world

            admin = (
                admin_factory()
                if admin_factory is not None
                else arena_client.ArenaClient.from_state()
            )
            admin.require_protocol()
            prep = prepare_fn or world.prepare
            ready = prep(admin, cfg["map"], echo=lambda _line: None)
            if cfg.get("waves_preset"):
                waves = arena_client.load_waves(cfg["waves_preset"])
            elif cfg.get("waves"):
                waves = list(cfg["waves"])
            else:
                waves = []
            kit_spec = cfg.get("kit")
            if not kit_spec or kit_spec == "keep":
                kit = None
            else:
                kit = combat_trial.kit(
                    kit_spec,
                    tier=cfg.get("weapon_material") or combat_trial.DEFAULT_TIER,
                    armor=cfg.get("armor_material") or combat_trial.DEFAULT_TIER,
                )
            bots_map = {
                b["name"]: b["bot_id"]
                for b in cfg.get("bots") or ()
                if isinstance(b, dict) and b.get("name")
            }
            api = (
                (
                    api_factory()
                    if api_factory is not None
                    else companion_api.ApiClient.from_runtime(
                        rt_dir / companion_api.RUNTIME_FILE
                    )
                )
                if bots_map
                else None
            )
            mode = cfg["mode"]
            teams = cfg["teams"] if mode in SIDED_MODES else None
            participants = cfg["participants"] if mode not in SIDED_MODES else None
            _update_board()
            runner = run_series_fn or arena_hunt.run_series
            record_path = runner(
                api,
                admin,
                cfg["map"],
                waves,
                repeat=1,
                participants=participants,
                timeout=match_timeout_s,
                out_dir=Path(out_dir) if out_dir is not None else rt_dir / "matches",
                mode=mode,
                teams=teams,
                exit=ready["spawn"],
                bots=bots_map,
                echo=lambda _line: None,
                label=f"lobby-rev-{cfg['revision']}",
                kit=kit,
                respawn_mode=cfg["respawn_mode"],
                respawn_count=cfg["respawn_count"],
            )
            recap: dict[str, Any] = {}
            if isinstance(record_path, (str, Path)) and Path(record_path).is_file():
                data = json.loads(Path(record_path).read_text(encoding="utf-8"))
                runs = data.get("runs") or []
                last_run = runs[-1] if runs else {}
                recap = {
                    key: last_run.get(key)
                    for key in (
                        "winner",
                        "outcome",
                        "reason",
                        "seconds",
                        "players",
                        "team_kills",
                    )
                    if last_run.get(key) is not None
                }
                recap["record_path"] = str(record_path)
            snap = lobby_getter().complete_match(recap)
            _update_board(snap)
        except Exception as error:  # noqa: BLE001
            snap = lobby_getter().fail_match(str(error))
            _update_board(snap)

    def launch(cfg: dict[str, Any]) -> None:
        payload = copy.deepcopy(cfg)
        if async_launch:
            threading.Thread(
                target=_run,
                args=(payload,),
                name="arena-lobby-match",
                daemon=True,
            ).start()
        else:
            _run(payload)

    return launch


def default_lobby(
    *,
    fleet: Any = None,
    runtime_dir: Path | None = None,
    on_launch: Callable[[dict[str, Any]], None] | None = None,
) -> ArenaLobby:
    """Authoritative shared lobby for the arena server instance."""
    from mclab import paths

    rt = (
        Path(runtime_dir)
        if runtime_dir is not None
        else paths.instance_paths("arena").runtime
    )
    state_path = rt / LOBBY_FILE
    with _DEFAULT_LOCK:
        existing = _DEFAULT_LOBBIES.get(state_path)
        if existing is not None:
            if fleet is not None and existing._fleet is None:
                existing._fleet = (
                    InProcessFleetAdapter(fleet) if hasattr(fleet, "add") else fleet
                )
            if on_launch is not None and existing._on_launch is None:
                existing._on_launch = on_launch
            return existing
        adapter = (
            InProcessFleetAdapter(fleet)
            if fleet is not None and hasattr(fleet, "add")
            else (fleet if fleet is not None else ApiFleetAdapter(rt))
        )
        lobby = ArenaLobby.load(
            state_path=state_path,
            runtime_dir=rt,
            fleet=adapter,
            on_launch=on_launch,
        )
        _DEFAULT_LOBBIES[state_path] = lobby
        return lobby
