"""The companion host's one body in a kept world (the play server, no mcb-lab).

A ``Session`` spawns the bot by its stable name straight through mcb-body (no
``natural_start`` episode: no frozen clock, no kit, no cleared mobs), keeps world
memory and places persistent, and runs the job intents on ``CompanionBody``. Every
method except ``say`` and ``snapshot`` runs on the host's worker thread (SQLite and
the bridge have thread affinity). Docs: COMPANION_LLM_PLAN_2026-11-23.md, section 2.

- Spawn position: the last good position saved in ``companion-state.json`` (instance
  runtime), else the world spawn from the server's ``level.dat``. Never a guessed
  column: one over the ocean drowned the bot, and a body in water never settles.
- Cancel: the host sets ``poll_hook``; the bridge runs it first in every ``state()``
  poll, and the host raises ``JobCancelled`` from it.
- Death (Carpet drops the items and disconnects the body): ``respawn`` remembers the
  place ``death`` and spawns the same name again with ``keep_state`` at the world
  spawn (vanilla), not beside what killed it.
"""

from __future__ import annotations

import functools
import http.client
import json
import logging
import math
import os
import time
from dataclasses import replace
from pathlib import Path

from mclab import companion_burrow as burrow
from mclab import companion_defense as defense
from mclab import paths
from mclab.build_runner import Result
from mclab.companion_body import (
    DIG_TERRAIN,
    NATURAL_MOVEMENT,
    REGEN_FOOD,
    CompanionBody,
    await_sampled_stream,
    open_bridge,
    session_archive,
)
from mclab.companion_defense import DefensePolicy
from mclab.follow import PLAYER, FollowTarget, feet_cell, find_player
from mclab.gather_runner import STATION_REACH, flush_containers
from mclab.progress_watch import BodyDied

log = logging.getLogger(__name__)

AWARENESS = paths.ROOT / "config/stepc/awareness-sections-64.json"
OVERWORLD = "minecraft:overworld"
DIED_STATES = frozenset({"dead", "disconnected_or_dead"})
# a body that is no longer in the world: spawn it again
GONE_STATES = DIED_STATES | {"idle", "retired", "spawn_timeout"}
SPAWN_TIMEOUT_S = 60.0
IDLE_OBSERVE_S = 5.0  # idle: feed the world memory this often (never a discarded read)
SAVE_EVERY_S = 10.0  # last good position written this often while it changes
SAFE_HEALTH = 10.0  # a position is "good" only while the body is this healthy
KEEP_EVENTS = 2000  # bridge.trim while idle
BUILD_OFFSET = (3, 0, 2)  # default build origin from the feet (dashboard dx/dz)
FOLLOW_SLACK = 1.0  # follow: walk again once this much beyond the distance
FOLLOW_WAIT_TICKS = 5  # follow: look again this often while close
FOLLOW_RETRY_TICKS = 20  # follow: a walk that failed waits this long before the next
HALT_TIMEOUT_S = 5.0
# defence (COMPANION_DEFENSE_PLAN_2026-11-23.md): one ``defend`` call is one step,
# and the host's guard decides again after each
FIGHT_TICKS = 200  # one fight step: the guard decides again at least this often
# decide again at once
FIGHT_ENDS = frozenset({"low_health", "avoid", "timeout", "unreachable"})
OUT_OF_REACH_S = 30.0  # a mob no walk reached is fought only from range this long
# A defence step's outcome (host event defense_step): the fight and escape ops' scalars.
OUTCOME_FIELDS = (
    "kills", "hits", "crits", "shots", "damage", "style", "ticks", "health", "end",
    "jumps", "flees", "closest",
    "distance", "nearest",
    "lance", "charge_log",  # a spear charge's ticks per move and first hits (small)
)  # fmt: skip
DEFEND_WAIT_TICKS = 10  # a step that could do nothing waits this long
FLEE_DISTANCE = 12  # blocks: one flee step
FLEE_RADIUS = 3.0
FLEE_TURNS = (0, 45, -45, 90, -90)  # degrees off straight away, tried in order
ESCAPE_SCAN = 24  # the escape steers around every hostile this close, new ones too
ESCAPE_CLEAR = 16  # and ends once none is this close
ESCAPE_TICKS = 200  # one step; the guard decides again after it
REGROUP_NEAR = 3.0  # beside an ally
BASE_NEAR = 2.0  # at the base place
BASE_EXTENT = 16.0  # known containers this close to the base place are the base's
BASE_PLACES = ("base", "home")  # a home recorded at the first spawn is no base
HEAL_MARGIN = 4  # leave a burrow at the floor plus this much health
BURROW_WAIT_TICKS = 20
DUSK = 12000  # time_of_day from here to the end of the day: night, mobs about
NO_THREAT = defense.Action("none", "no threat near")


class JobCancelled(BaseException):
    """Raised from ``Bridge.state()`` when the running job is stopped. A
    BaseException: runner code that catches ``Exception`` must not swallow a stop."""


class JobFailed(Exception):
    """A job ended without reaching its goal; the message is for a player or model."""


class Defend(BaseException):
    """Raised from ``Bridge.state()`` when a threat needs the bot now: the job unwinds
    (like a stop), the host defends, then runs the job again. ``action`` is the
    ``companion_defense.Action`` that was decided."""

    def __init__(self, action):
        super().__init__(action.reason)
        self.action = action


# --- files ----------------------------------------------------------------------------


def instance_runtime() -> Path:
    """This instance's runtime folder (``runtime/instances/<name>``; the lab's is
    ``runtime``). The only place that knows where paths keeps it."""
    return Path(getattr(paths, "INSTANCE_RUNTIME", paths.RUNTIME))


def write_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _cell(value) -> tuple[int, int, int] | None:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in value)
    ):
        return tuple(int(math.floor(v)) for v in value)
    return None


def world_spawn(level_dir) -> tuple[int, int, int] | None:
    """The overworld spawn point saved in ``level.dat``: ``Data.spawn {pos,
    dimension}`` (Minecraft 26.x) or ``Data.SpawnX/Y/Z`` (older). None when the file
    is missing or unreadable, or the spawn is in another dimension."""
    from mclab import nbt

    try:
        data = nbt.load(Path(level_dir) / "level.dat").get("Data", {})
    except (OSError, ValueError, KeyError, IndexError, EOFError, AttributeError):
        return None
    spawn = data.get("spawn")
    if isinstance(spawn, dict):
        if spawn.get("dimension", OVERWORLD) != OVERWORLD:
            return None
        return _cell(spawn.get("pos"))
    return _cell([data.get(k) for k in ("SpawnX", "SpawnY", "SpawnZ")])


def level_spawn() -> tuple[int, int, int] | None:
    """The play server's world spawn (its level folder from server.properties)."""
    from mclab.companion_body import _level_dir

    return world_spawn(_level_dir())


# --- chat out ---------------------------------------------------------------------


class SayClient:
    """``POST /say {text, to?}`` on the body bridge. Its own connection per call, so
    any thread may use it (the Bridge object belongs to the worker thread)."""

    def __init__(self, port: int, token: str, timeout: float = 4.0):
        self.port, self._token, self.timeout = port, token, timeout

    def say(self, text: str, to: str | None = None) -> None:
        payload = {"text": text, **({"to": to} if to else {})}
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=self.timeout
        )
        try:
            connection.request(
                "POST",
                "/say",
                body=json.dumps(payload).encode(),
                headers={
                    "Authorization": "Bearer " + self._token,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            data = response.read()
        finally:
            connection.close()
        if not 200 <= response.status < 300:
            raise RuntimeError(f"say failed: HTTP {response.status} {data[:200]!r}")


def default_say_client() -> SayClient:
    from mclab.server import STATE

    return SayClient(paths.config()["bridge_port"], paths.read_json(STATE)["token"])


# --- defaults for the live session --------------------------------------------------


def open_host_bridge(folder: Path):
    """A recorded bridge with no request budget and a fresh companion identity."""
    from mclab.navigation_runner import _new_identity

    bridge = open_bridge(folder, budget=None)
    _new_identity(bridge, "companion")
    bridge.policy_memory = None
    return bridge


def load_awareness():
    from mclab.awareness import Awareness

    return Awareness.from_payload(paths.read_json(AWARENESS))


def open_world_memory(bridge, folder: Path, awareness):
    """Persistent world memory (the real level's archive; per-run if it has no seed)."""
    from mclab.memory_policy import MemoryPolicy
    from mclab.spatial_memory import SpatialMemory

    policy = MemoryPolicy()
    archive = session_archive(folder, awareness, bridge.episode_id, True, policy)
    return SpatialMemory(archive, bridge.episode_id, policy=policy)


def open_places(memory):
    from mclab.place_graph import PlaceGraph

    return PlaceGraph(memory.archive)


@functools.cache
def vanilla_knowledge():
    from mclab.companion_jobs import load_vanilla_pack

    return load_vanilla_pack()


def template_path(name: str) -> Path:
    """``builds/<name>.build.json`` (or a test schematic), resolved as the dashboard
    resolves its build list."""
    from mclab.dashboard_runs import SCHEMATIC_DIRS, companion_file, companion_files

    stem = name.removesuffix(".build.json")
    for directory in SCHEMATIC_DIRS:
        relative = f"{directory}/{stem}.build.json"
        if (paths.ROOT / relative).is_file():
            return companion_file(relative, ".build.json", SCHEMATIC_DIRS, "template")
    known = [
        Path(p).name.removesuffix(".build.json")
        for p in companion_files(".build.json", SCHEMATIC_DIRS)
    ]
    raise JobFailed(
        f"No build template called {stem!r}; known: {', '.join(known) or 'none'}"
    )


class HostBody(CompanionBody):
    """CompanionBody whose walks carry the follow target (native_driver updates the
    goal from every observation while ``follow`` is set)."""

    follow: FollowTarget | None = None

    def _goto_controller(self, *args, **kwargs):
        controller = super()._goto_controller(*args, **kwargs)
        controller.follow = self.follow
        return controller


def _short(item: str) -> str:
    """An id as words: ``minecraft:oak_log`` -> oak log, ``#minecraft:logs`` -> logs."""
    return item.removeprefix("#").removeprefix("minecraft:").replace("_", " ")


def _where(pos) -> str:
    return " ".join(str(int(math.floor(v))) for v in pos)


def _night(body: dict) -> bool:
    """Mobs about: from dusk to the end of the day. A body without a clock (an older
    mcb-body) counts as day."""
    time_of_day = body.get("time_of_day")
    return time_of_day is not None and int(time_of_day) >= DUSK


def _counts(stacks) -> dict[str, int]:
    """Item counts from a list of ``{item, count}`` stacks."""
    counts: dict[str, int] = {}
    for stack in stacks or ():
        counts[stack["item"]] = counts.get(stack["item"], 0) + int(stack["count"])
    return counts


# --- the session -------------------------------------------------------------------


class Session:
    def __init__(
        self,
        bot_name: str,
        *,
        folder: Path,
        state_file: Path,
        gather_state=None,
        gather_options=None,
        open_bridge=open_host_bridge,
        open_memory=open_world_memory,
        make_places=open_places,
        make_body=HostBody,
        awareness=None,
        say_client=None,
        knowledge=vanilla_knowledge,
        clock=time.monotonic,
        world_spawn=level_spawn,
        defense: DefensePolicy | None = None,
        defense_file: Path | None = None,
    ):
        from mclab.gather_options import GatherOptions
        from mclab.gather_runner import GatherState

        self.bot_name = bot_name
        self.folder = Path(folder)
        self.state_file = Path(state_file)
        self.gather_state = gather_state if gather_state is not None else GatherState()
        self.gather_options = gather_options or GatherOptions()
        self._open_bridge, self._open_memory = open_bridge, open_memory
        self._make_places, self._make_body = make_places, make_body
        self._awareness, self._say_client = awareness, say_client
        self._knowledge = knowledge
        self._world_spawn = world_spawn
        self.clock = clock
        self.poll_hook = None  # set by the host: runs first in every bridge poll
        self.bridge = self.memory = self.places = self.body = None
        self.last_good: tuple[int, int, int] | None = None
        self._died_event = self._handled_death = None  # respawn: body_died seen
        self._saved_at = -math.inf
        self._saved: tuple[int, int, int] | None = None
        self._observed_at = -math.inf
        # defence: bot.json settings, then the ones changed in play (saved apart:
        # _save rewrites the state file whole)
        self._defense_base = defense or DefensePolicy()
        self.defense_file = (
            Path(defense_file)
            if defense_file is not None
            else self.state_file.with_name("companion-defense.json")
        )
        self.defense_policy = self._load_defense()
        self._threat: defense.Action | None = None
        self._threat_tick = None
        self._spent_chests: set = set()  # this defence: a chest that failed
        self._burrow_failed = None  # this defence: feet where a burrow failed
        self._out_of_reach: dict[str, float] = {}  # uuid -> clock() until

    # --- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        position = None
        try:
            if self._awareness is None:
                self._awareness = load_awareness()
            if self._say_client is None:
                self._say_client = default_say_client()
            self.bridge = self._open_bridge(self.folder)
            self.bridge.cancel_check = self._on_poll
            self.memory = self._open_memory(self.bridge, self.folder, self._awareness)
            self.places = self._make_places(self.memory)
            position = self.spawn_position()
            self._spawn(position)
        except BaseException:
            if position is not None and position == self._saved_position():
                # the host retries a failed start: not at the same spot (it drowned
                # the bot at every retry), at the world spawn
                self._forget_saved()
            self.close(despawn=False)
            raise

    def _on_poll(self) -> None:
        if self.poll_hook is not None:
            self.poll_hook()

    def _saved_position(self) -> tuple[int, int, int] | None:
        saved = read_state(self.state_file)
        return (
            _cell(saved.get("position")) if saved.get("bot") == self.bot_name else None
        )

    def _forget_saved(self) -> None:
        saved = read_state(self.state_file)
        saved.pop("position", None)
        try:
            write_atomic(self.state_file, saved)
        except OSError as error:
            log.warning("could not forget the saved position: %s", error)

    def spawn_position(self) -> tuple[int, int, int]:
        if (cell := self._saved_position()) is not None:
            return cell
        spawn = self._world_spawn()
        if spawn is None:
            raise JobFailed(
                "No saved position and no world spawn in level.dat; "
                "is the play server's world created?"
            )
        return spawn

    def _spawn(self, position) -> None:
        x, y, z = position
        request = self.bridge.send(
            "spawn",
            name=self.bot_name,
            position=[x + 0.5, y, z + 0.5],
            yaw=0.0,
            replace=True,
            stream=True,
            extended_observation=True,
            keep_state=True,  # the saved inventory, health and food stay
            awareness=self._awareness.payload(),
            terrain_encoding="sections-v1",
        )
        self.bridge.acknowledge(request, "spawn_requested", timeout=10)
        await_sampled_stream(self.bridge, timeout=SPAWN_TIMEOUT_S)
        body = self._make_body(
            self.bridge,
            self.memory,
            movement_policy=NATURAL_MOVEMENT,
            terrain_policy=DIG_TERRAIN,
        )
        body.places = self.places
        self.body = body
        body.observe()
        self._observed_at = self.clock()
        feet = tuple(body.position())
        if self.places.named("home") is None:
            self.places.add("home", feet, kind="home", provenance="spawn")
        from mclab.gather_runner import hydrate_containers

        hydrate_containers(body, self.gather_state)
        self.last_good = feet
        self._save(feet, force=True)

    def respawn(self) -> dict:
        """Bring the same name back. One that died (its items lie where it died; the
        spot becomes the place ``death``) comes back at the world spawn like a vanilla
        player, not beside what killed it; one that left the world without dying
        comes back where it was. The one place to change how a companion returns."""
        death = self._new_death()
        if death is not None:
            self.places.remove("death")
            self.places.add("death", death, kind="death", provenance="died")
        if self.body is not None:
            self.body.follow = None
            self.body._drop_ahead()
        self.body = None
        if self._died_event is not None:
            self._spawn(self._world_spawn() or self.last_good or self.spawn_position())
        else:
            self._spawn(self.last_good or self.spawn_position())
        return {
            "death": list(death) if death is not None else None,
            "position": list(self.body.position()),
        }

    def _new_death(self):
        """Where the body died, from the newest ``body_died`` not yet handled (else
        the last sample); sets ``_died_event``. None when it did not die."""
        self._died_event = None
        for event in reversed(getattr(self.bridge, "events", None) or ()):
            if event is self._handled_death:
                break
            if event.get("type") == "body_died":
                self._died_event = self._handled_death = event
                break
        if self._died_event is None:
            return None
        cell = _cell((self._died_event.get("data") or {}).get("position"))
        if cell is not None:
            return cell
        for sample in reversed(getattr(self.bridge, "samples", None) or ()):
            cell = _cell((sample.get("body") or {}).get("position"))
            if cell is not None:
                return cell
        return None

    def halt(self) -> None:
        """Stand still: cancel the route and release every held control."""
        if self.body is not None:
            self.body.follow = None
            self.body._drop_ahead()
        for op, kind in (("cancel_route", "route_cancelled"), ("release", "released")):
            request = self.bridge.send(op)
            self.bridge.acknowledge(request, kind, timeout=HALT_TIMEOUT_S)

    def close(self, despawn: bool = False) -> None:
        """Despawn (a logout that saves the body, not a death) when asked, then close
        the world memory and the bridge. Every step runs even if one fails."""
        steps = []
        if despawn and self.bridge is not None and self.body is not None:
            steps.append(self._despawn)
        if self.memory is not None:
            steps.append(self.memory.close)
        if self.bridge is not None:
            steps.append(self.bridge.close)
        for step in steps:
            try:
                step()
            except Exception as error:  # noqa: BLE001 - closing goes on
                log.warning("session close: %s failed: %s", step.__name__, error)
        self.body = None

    def _despawn(self) -> None:
        self.bridge.cancel_check = None  # never cancelled half way out
        try:
            if self.last_good is not None:
                self._save(self.last_good, force=True)
            request = self.bridge.send("despawn")
            self.bridge.acknowledge(request, "despawned", timeout=HALT_TIMEOUT_S)
        finally:
            self.bridge.cancel_check = self._on_poll

    # --- observation ---------------------------------------------------------------
    def idle(self) -> dict:
        """One heartbeat poll (about every 250 ms from the host); feeds the world
        memory every IDLE_OBSERVE_S. Raises BodyDied when the body is gone."""
        state = self.bridge.state()
        body_state = state.get("body_state", state.get("state"))
        if body_state in GONE_STATES:
            raise BodyDied(f"body state {body_state}")
        now = self.clock()
        if body_state == "ready" and now - self._observed_at >= IDLE_OBSERVE_S:
            self.body.observe()
            self._observed_at = now
        self._keep_position(state)
        self.bridge.trim(KEEP_EVENTS)
        return self.snapshot(state)

    def health(self) -> str:
        """ok | dead (the body is gone) | lost (no bridge, or another server launch)."""
        try:
            state = self.bridge.http("/state")
        except Exception:  # noqa: BLE001 - any failure is a lost bridge
            return "lost"
        return (
            "dead"
            if state.get("body_state", state.get("state")) in GONE_STATES
            else "ok"
        )

    def _keep_position(self, state: dict) -> None:
        """Only a healthy, dry stance on the ground is a place to come back to: a
        shallow-water spot saved as good drowned the bot at every later start."""
        body = state.get("body") or {}
        cell = _cell(body.get("position"))
        if (
            cell is None
            or state.get("body_state", state.get("state")) != "ready"
            or float(body.get("health", 20.0)) < SAFE_HEALTH
            or body.get("on_ground") is False
            or (body.get("survival") or {}).get("in_water") is True
        ):
            return
        self.last_good = cell
        self._save(cell)

    def _save(self, cell, *, force: bool = False) -> None:
        now = self.clock()
        if not force and (cell == self._saved or now - self._saved_at < SAVE_EVERY_S):
            return
        write_atomic(
            self.state_file,
            {"bot": self.bot_name, "position": list(cell), "saved_at": time.time()},
        )
        self._saved, self._saved_at = tuple(cell), now

    def snapshot(self, state: dict | None = None) -> dict:
        """Plain data for ``status``: position, health, food, held items, players in
        sight. Without ``state``: the bridge's latest observation."""
        if state is None:
            samples = getattr(self.bridge, "samples", None) or ()
            state = samples[-1] if samples else {}
        body = state.get("body") or {}
        position = body.get("position")
        inventory = _counts(body.get("inventory"))
        players = [
            {
                "name": e.get("name"),
                "position": [round(v, 1) for v in e["position"]],
                "distance": round(math.dist(position, e["position"]), 1)
                if position
                else None,
            }
            for e in state.get("visible_entities") or ()
            if e.get("type") == PLAYER and e.get("position")
        ]
        return {
            "tick": state.get("tick"),
            "body_state": state.get("body_state", state.get("state")),
            "position": [round(v, 2) for v in position] if position else None,
            "health": body.get("health"),
            "food": body.get("food"),
            "inventory": inventory,
            "players": players,
        }

    def record_progress(self, job: dict) -> None:
        write_atomic(
            self.folder / "progress.json",
            {
                "job": job["intent"],
                "job_id": job["id"],
                "state": job["state"],
                **job["progress"],
                "updated_at": time.time(),
            },
        )

    def say(self, text: str, to: str | None = None) -> None:
        if self._say_client is None:
            raise RuntimeError("The bot is not in the world right now")
        self._say_client.say(text, to)

    # --- places (instant intents, on the worker thread) --------------------------
    def list_places(self) -> list[dict]:
        return [
            {"name": p.name, "kind": p.kind, "position": list(p.position)}
            for p in self.places.listed()
        ]

    def remember_place(self, name: str, pos=None) -> dict:
        where = tuple(pos) if pos is not None else tuple(self.body.position())
        self.places.remove(name)
        place = self.places.add(name, where, kind="place", provenance="named")
        return {
            "name": place.name,
            "kind": place.kind,
            "position": list(place.position),
        }

    # --- defence (the host's guard; COMPANION_DEFENSE_PLAN_2026-11-23.md) ----------
    def _load_defense(self) -> DefensePolicy:
        saved = read_state(self.defense_file)
        try:
            return self._defense_base.changed(**saved) if saved else self._defense_base
        except (TypeError, ValueError) as error:
            log.warning(
                "ignoring saved defense settings %s: %s", self.defense_file, error
            )
            return self._defense_base

    def defense_settings(self, changes: dict) -> dict:
        """The defence settings, after ``changes`` (validated ``defense`` intent
        args) are applied and saved. Empty ``changes``: just the current ones."""
        if changes:
            policy = self.defense_policy.changed(**changes)
            saved = read_state(self.defense_file)
            saved.update(changes)
            write_atomic(self.defense_file, saved)
            self.defense_policy = policy
            self._threat = None  # decide again with the new settings
        policy = self.defense_policy
        return {**policy.as_dict(), "floor_hearts": policy.floor_health / 2}

    def threat(self) -> defense.Action:
        """What to do about hostiles now, from the newest sample. Runs inside every
        guarded ``bridge.state()`` poll, so it never polls itself; decided once per
        server tick."""
        samples = getattr(self.bridge, "samples", None) or ()
        state = samples[-1] if samples else {}
        tick = state.get("tick")
        if self._threat is not None and tick is not None and tick == self._threat_tick:
            return self._threat
        action = self._decide(state)
        self._threat, self._threat_tick = action, tick
        if action.kind == "none":  # this defence is over: what failed may work next
            self._spent_chests.clear()
            self._burrow_failed = None
        return action

    def _decide(self, state: dict) -> defense.Action:
        body = state.get("body") or {}
        position = body.get("position")
        if self.body is None or position is None:
            return NO_THREAT
        inventory = _counts(body.get("inventory"))
        base = self._base()
        situation = defense.Situation(
            health=float(body.get("health", 20.0)),
            position=tuple(position),
            entities=tuple(state.get("visible_entities") or ()),
            inventory=inventory,
            name=self.bot_name,
            base=base,
            base_weapons=self._base_weapons(base),
            night=_night(body),
            can_heal=self._can_heal(body, inventory),
            out_of_reach=self._still_out_of_reach(),
        )
        action = defense.decide(situation, self.defense_policy)
        if action.kind == "burrow":
            plan = self._burrow_plan(_cell(position), inventory)
            if not isinstance(plan, burrow.Burrow):
                situation = replace(situation, can_burrow=False)
                action = defense.decide(situation, self.defense_policy)
        return action

    def _still_out_of_reach(self) -> frozenset[str]:
        now = self.clock()
        for uuid in [u for u, until in self._out_of_reach.items() if until <= now]:
            del self._out_of_reach[uuid]
        return frozenset(self._out_of_reach)

    def _base(self) -> tuple | None:
        """The place named base, else home unless it is only where the bot first
        spawned (an open field is no shelter)."""
        for name in BASE_PLACES:
            place = self.places.named(name) if self.places is not None else None
            if place is not None and place.provenance != "spawn":
                return tuple(place.position)
        return None

    def _base_weapons(self, base) -> tuple:
        """(container, weapon) for every weapon known to be in a chest at the base."""
        if base is None:
            return ()
        found = []
        for pos, contents in self.gather_state.containers.items():
            if pos in self._spent_chests or math.dist(pos, base) > BASE_EXTENT:
                continue
            found.extend(
                (tuple(pos), item)
                for item in defense.WEAPONS
                if contents.get(item, 0) > 0
            )
        return tuple(found)

    def _burrow_plan(self, feet, inventory):
        if feet == self._burrow_failed:
            return "a burrow here already failed"
        return burrow.plan_burrow(self.memory.cells, feet, inventory, self.knowledge())

    def _feet(self) -> tuple[int, int, int]:
        """The feet cell from the newest sample (the body's own snapshot can be
        seconds old when a job is cut short)."""
        samples = getattr(self.bridge, "samples", None) or ()
        if samples:
            cell = _cell((samples[-1].get("body") or {}).get("position"))
            if cell is not None:
                return cell
        return tuple(self.body.position())

    def defend(self, action: defense.Action) -> dict:
        """One step of ``action``; the host's guard decides again after it. Returns
        the step's outcome: the action, and for a body op (fight, escape) its ok,
        reason and scalar result fields (OUTCOME_FIELDS; never the inventory)."""
        self.knowledge()  # body.foods: eat_when_needed knows what is food
        step = {
            "fight": self._fight,
            "walk_away": self._walk_away,
            "escape": self._escape,
            "regroup": self._regroup,
            "shelter": self._shelter,
            "arm": self._arm,
            "burrow": self._burrow,
        }.get(action.kind)
        result = step(action) if step is not None else None
        outcome = {"action": action.kind}
        if isinstance(result, Result):
            data = result.data or {}
            outcome.update(ok=result.ok, reason=result.reason)
            outcome.update({k: data[k] for k in OUTCOME_FIELDS if k in data})
        return outcome

    def trial(self, spec: dict) -> dict:
        """Set the scene for an operator's combat trial (the body's trial op). The
        body checks every field; a refusal raises ValueError with its reason."""
        if self.body is None:  # respawning after a lost fight
            raise ValueError("trial failed: body_not_ready")
        result = self.body.trial(**spec)
        if not result.ok:
            raise ValueError(f"trial failed: {result.reason}")
        return {k: v for k, v in (result.data or {}).items() if k != "inventory"}

    def _rest(self) -> None:
        """Nothing to move for: eat if hungry or hurt, and let a few ticks pass."""
        self.body.eat_when_needed()
        self.body.wait_ticks(DEFEND_WAIT_TICKS)

    def _fight(self, action: defense.Action) -> Result:
        policy = self.defense_policy

        def once(entity):
            return self.body.fight(
                entity=entity,
                weapon=action.item,
                radius=int(policy.threat_radius),
                floor=int(policy.floor_health),
                max_ticks=FIGHT_TICKS,
                anchor=action.anchor,
                leash=int(action.leash) if action.leash is not None else None,
                tactic=action.tactic.wire() if action.tactic is not None else None,
            )

        result = once(action.target)
        if not result.ok and action.target is not None:
            if result.reason in ("not_allowed", "not_found"):
                result = once(None)  # the nearest the body will fight instead
        if not result.ok and result.reason in FIGHT_ENDS:
            if result.reason == "unreachable" and action.target is not None:
                # the body found no walk to it (a ledge): from range only, a while
                self._out_of_reach[action.target] = self.clock() + OUT_OF_REACH_S
                self._threat = None  # decide again with it out of reach
            return result
        spent = int((result.data or {}).get("ticks", 0)) if result.ok else 0
        if spent < DEFEND_WAIT_TICKS:  # nothing in reach yet: let the mob come
            self.body.wait_ticks(DEFEND_WAIT_TICKS - spent)
        return result

    def _escape(self, action: defense.Action) -> Result:
        """Sprint-jump away from every hostile around (the body steers each tick)."""
        result = self.body.escape(
            radius=ESCAPE_SCAN, clear=ESCAPE_CLEAR, max_ticks=ESCAPE_TICKS
        )
        if not result.ok and result.reason == "cornered":
            self._walk_away(action)  # the route planner sees past the next cell
        return result

    def _walk_away(self, action: defense.Action) -> None:
        """Walk FLEE_DISTANCE straight away from the threats, else off to a side."""
        x, y, z = self._feet()
        dx, dz = 1.0, 0.0
        if action.threats:
            n = len(action.threats)
            cx = sum(t.position[0] for t in action.threats) / n
            cz = sum(t.position[2] for t in action.threats) / n
            if math.hypot(x + 0.5 - cx, z + 0.5 - cz) > 1e-6:
                dx, dz = x + 0.5 - cx, z + 0.5 - cz
        heading = math.atan2(dz, dx)
        for turn in FLEE_TURNS:
            angle = heading + math.radians(turn)
            goal = (
                x + round(FLEE_DISTANCE * math.cos(angle)),
                y,
                z + round(FLEE_DISTANCE * math.sin(angle)),
            )
            if self.body.walk_to(goal, FLEE_RADIUS):
                return
        self.body.wait_ticks(DEFEND_WAIT_TICKS)

    def _regroup(self, action: defense.Action) -> None:
        x, y, z = self._feet()
        if math.dist((x + 0.5, y, z + 0.5), action.where) > REGROUP_NEAR:
            if self.body.walk_to(_cell(action.where), REGROUP_NEAR):
                return
        self._rest()

    def _shelter(self, action: defense.Action) -> None:
        base = _cell(action.where)
        if math.dist(self._feet(), base) > BASE_NEAR:
            if self.body.walk_far(base, BASE_NEAR):
                return
        self._rest()

    def _arm(self, action: defense.Action) -> None:
        """Take the weapon from the base chest; what the chest holds is learned from
        the result, so a stale record is not fetched from twice."""
        chest, item = tuple(action.where), action.item
        if not self.body.goto_stance(chest, STATION_REACH):
            self._spent_chests.add(chest)
            return
        result = self.body.container(chest, "withdraw", [{"item": item, "count": 1}])
        if result.ok:
            self.gather_state.containers[chest] = _counts(result.data.get("contents"))
        elif result.reason == "not_a_container":
            self.gather_state.containers.pop(chest, None)
        else:
            self._spent_chests.add(chest)
        self._flush(flush_containers)

    def _burrow(self, action: defense.Action) -> None:
        """Dig in and seal, wait until healed (and armed or day), dig out. A stop or
        a death still ends it: every wait is a bridge poll."""
        feet = self._feet()
        plan = self._burrow_plan(feet, self.body.inventory())
        if not isinstance(plan, burrow.Burrow):
            self._burrow_failed = feet
            return
        result = burrow.dig_in(self.body, plan)
        if not result.ok:
            log.info("burrow at %s failed: %s", feet, result.reason)
            self._burrow_failed = feet
            return
        while True:
            state = self.bridge.state()
            if state.get("body_state", state.get("state")) in GONE_STATES:
                raise BodyDied("died in the burrow")
            if self._may_leave(state.get("body") or {}):
                break
            self.body.eat_when_needed()
            self.body.wait_ticks(BURROW_WAIT_TICKS)
        burrow.dig_out(self.body, plan)

    def _may_leave(self, body: dict) -> bool:
        """Healed (or unable to heal: hungry with no food), and armed or day."""
        inventory = _counts(body.get("inventory"))
        floor = self.defense_policy.floor_health
        healed = float(body.get("health", 20.0)) >= min(20, floor + HEAL_MARGIN)
        armed = defense.best_weapon(inventory) is not None
        return (healed or not self._can_heal(body, inventory)) and (
            armed or not _night(body)
        )

    def _can_heal(self, body: dict, inventory: dict) -> bool:
        """Health regenerates: the food bar is high enough, or food is held."""
        foods = getattr(self.knowledge(), "foods", None) or {}
        return int(body.get("food", 20)) >= REGEN_FOOD or any(
            inventory.get(item, 0) > 0 for item in foods
        )

    # --- jobs ------------------------------------------------------------------------
    def run(self, intent: str, args: dict, actor, progress) -> dict:
        handler = {
            "goto": self.goto,
            "follow": self.follow,
            "gather": self.gather,
            "build": self.build,
        }.get(intent)
        if handler is None:
            raise JobFailed(f"{intent} is not a job")
        return handler(args, actor, progress)

    def knowledge(self):
        value = self._knowledge()
        self.body.foods = dict(getattr(value, "foods", None) or {})  # eat_when_needed
        return value

    def _seen(self, name: str, actor):
        """Where ``name`` stands: in sight, else the asking player's own position."""
        state = self.bridge.state()
        seen = find_player(state.get("visible_entities"), name)
        if seen is None and actor is not None and actor.position is not None:
            if (actor.name or "").lower() == name.lower():
                seen = tuple(actor.position)
        return seen, state

    def goto(self, args: dict, actor, progress) -> dict:
        target, radius = args["target"], args["radius"]
        if "place" in target:
            name = target["place"]
            if self.places.named(name) is None:
                known = ", ".join(p.name for p in self.places.listed()) or "none"
                raise JobFailed(f"I don't know a place called {name!r}; known: {known}")
            progress(stage="walking", detail=f"to {name}")
            if not self.body.go_to(name):
                raise self._stopped_short(name)
            return {"arrived": name, "position": list(self.body.position())}
        if "player" in target:
            name = target["player"]
            seen, _ = self._seen(name, actor)
            if seen is None:
                raise JobFailed(f"I can't see {name}")
            goal, label = feet_cell(seen), name
        else:
            goal = tuple(target["pos"])
            label = _where(goal)
        progress(stage="walking", detail=f"to {label}")
        if not self.body.walk_far(goal, radius):
            raise self._stopped_short(label)
        return {"arrived": label, "position": list(self.body.position())}

    def _stopped_short(self, label: str) -> JobFailed:
        here = _where(self.body.position())
        return JobFailed(f"I could not get to {label}; I stopped at {here}")

    def follow(self, args: dict, actor, progress) -> dict:
        """Keep within ``distance`` of the player until stopped (never returns by
        itself). Out of sight, the bot goes where they were last seen and waits."""
        name, distance = args["player"], args["distance"]
        target = FollowTarget(name)
        seen, state = self._seen(name, actor)
        if seen is None:
            raise JobFailed(f"I can't see {name}")
        target.last_seen = (seen, state.get("tick"))
        self.body.follow = target
        try:
            while True:
                state = self.bridge.state()
                seen = target.locate(state)
                if seen is not None:
                    target.last_seen = (seen, state.get("tick"))
                where = target.last_seen[0]
                here = (state.get("body") or {}).get("position")
                if here is None:
                    raise BodyDied("observation has no position: the body is dead")
                gap = math.dist(here, where)
                sight = "" if seen is not None else " (out of sight)"
                if gap > distance + FOLLOW_SLACK:
                    progress(
                        stage="following", detail=f"{gap:.0f} blocks from {name}{sight}"
                    )
                    if not self.body.walk_to(feet_cell(where), radius=distance):
                        self.body.wait_ticks(FOLLOW_RETRY_TICKS)
                else:
                    progress(
                        stage="waiting" if seen is not None else "lost sight",
                        detail=f"near {name}"
                        if seen is not None
                        else f"where {name} was",
                    )
                    self.body.wait_ticks(FOLLOW_WAIT_TICKS)
        finally:
            self.body.follow = None

    def gather(self, args: dict, actor, progress) -> dict:
        from mclab.gather_plan import group_members, plan
        from mclab.gather_runner import flush_containers, run_gather

        item, count = args["item"], args["count"]
        knowledge = self.knowledge()
        members = group_members(knowledge, item)
        if not members:
            raise JobFailed(f"I know no group of items called {_short(item)}")

        def holding() -> int:
            inventory = self.body.inventory()
            return sum(inventory.get(i, 0) for i in members)

        held = holding()
        if held >= count:
            return {"item": item, "held": held, "gathered": 0}
        progress(stage="planning", done=held, total=count)
        wanted = {item: count}
        first = plan(
            wanted,
            self.body.inventory(),
            knowledge,
            self.gather_state.stations,
            starter_tools=True,
            kit=self.gather_options,
        )
        if not first.tasks and first.missing:
            lacking = ", ".join(sorted({_short(m.item) for m in first.missing}))
            raise JobFailed(
                f"I don't know how to get {_short(item)}: no way to {lacking}"
            )
        progress(stage="gathering", detail=f"{len(first.tasks)} tasks")
        try:
            report = run_gather(
                first,
                self.body,
                knowledge,
                wanted,
                state=self.gather_state,
                opts=self.gather_options,
            )
        finally:
            self._flush(flush_containers)
        if report.stop == "died":
            raise BodyDied("died while gathering")
        now = holding()
        if now >= count:
            return {
                "item": item,
                "held": now,
                "gathered": now - held,
                "tasks": len(report.done),
            }
        why = report.failures[-1][1] if report.failures else "no failure reported"
        raise JobFailed(
            f"I have {now}/{count} {_short(item)}; "
            f"gathering stopped ({report.stop}: {why})"
        )

    def build(self, args: dict, actor, progress) -> dict:
        from mclab.companion_jobs import build_job
        from mclab.gather_runner import flush_containers

        name = args["template"]
        path = template_path(name)
        origin = args["origin"]
        if origin is None:
            origin = tuple(
                a + b for a, b in zip(self.body.position(), BUILD_OFFSET, strict=True)
            )

        def on_stage(stage, done=None, total=None, detail=None):
            progress(stage=stage, done=done, total=total, detail=detail)

        def on_progress(report, final):
            progress(
                stage="done" if final else None,
                done=len(report.placed),
                detail=f"stop={report.stop or 'building'} steps={report.steps}",
            )

        try:
            job = build_job(
                path,
                tuple(origin),
                args["rotation"],
                self.body,
                self.knowledge(),
                state=self.gather_state,
                gather_options=self.gather_options,
                on_progress=on_progress,
                on_stage=on_stage,
            )
        finally:
            self._flush(flush_containers)
        phases = [job.starter, job.gather, *job.refills, job.build]
        if any(p is not None and p.stop == "died" for p in phases):
            raise BodyDied("died while building")
        if job.stop == "built":
            return {
                "template": name,
                "origin": list(origin),
                "placed": len(job.build.placed) if job.build is not None else 0,
            }
        short = ", ".join(f"{n} {_short(i)}" for i, n in sorted(job.shortfall.items()))
        raise JobFailed(
            f"The {name} build stopped: {job.stop}"
            + (f" (short of {short})" if short else "")
        )

    def _flush(self, flush) -> None:
        try:
            flush(self.body, self.gather_state)
        except Exception as error:  # noqa: BLE001 - the job's own outcome wins
            log.warning("could not save known containers: %s", error)
