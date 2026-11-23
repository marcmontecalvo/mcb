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
  place ``death`` and spawns the same name again with ``keep_state``.
"""

from __future__ import annotations

import functools
import http.client
import json
import logging
import math
import os
import time
from pathlib import Path

from mclab import paths
from mclab.companion_body import (
    DIG_TERRAIN,
    NATURAL_MOVEMENT,
    CompanionBody,
    await_sampled_stream,
    open_bridge,
    session_archive,
)
from mclab.follow import PLAYER, FollowTarget, feet_cell, find_player
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


class JobCancelled(BaseException):
    """Raised from ``Bridge.state()`` when the running job is stopped. A
    BaseException: runner code that catches ``Exception`` must not swallow a stop."""


class JobFailed(Exception):
    """A job ended without reaching its goal; the message is for a player or model."""


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
    return item.removeprefix("minecraft:").replace("_", " ")


def _where(pos) -> str:
    return " ".join(str(int(math.floor(v))) for v in pos)


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
        self._saved_at = -math.inf
        self._saved: tuple[int, int, int] | None = None
        self._observed_at = -math.inf

    # --- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            if self._awareness is None:
                self._awareness = load_awareness()
            if self._say_client is None:
                self._say_client = default_say_client()
            self.bridge = self._open_bridge(self.folder)
            self.bridge.cancel_check = self._on_poll
            self.memory = self._open_memory(self.bridge, self.folder, self._awareness)
            self.places = self._make_places(self.memory)
            self._spawn(self.spawn_position())
        except BaseException:
            self.close(despawn=False)
            raise

    def _on_poll(self) -> None:
        if self.poll_hook is not None:
            self.poll_hook()

    def spawn_position(self) -> tuple[int, int, int]:
        saved = read_state(self.state_file)
        if saved.get("bot") == self.bot_name and (cell := _cell(saved.get("position"))):
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
        """The body died (its items lie where it died) or left the world: remember
        that spot as the place ``death`` and spawn the same name again at the last
        good position. The one place to change how a companion comes back."""
        death = self._death_cell()
        if death is not None:
            self.places.remove("death")
            self.places.add("death", death, kind="death", provenance="died")
        if self.body is not None:
            self.body.follow = None
            self.body._drop_ahead()
        self.body = None
        self._spawn(self.last_good or self.spawn_position())
        return {
            "death": list(death) if death is not None else None,
            "position": list(self.body.position()),
        }

    def _death_cell(self):
        for event in reversed(getattr(self.bridge, "events", None) or ()):
            if event.get("type") == "body_died":
                cell = _cell((event.get("data") or {}).get("position"))
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
        body = state.get("body") or {}
        cell = _cell(body.get("position"))
        if (
            cell is None
            or state.get("body_state", state.get("state")) != "ready"
            or float(body.get("health", 20.0)) < SAFE_HEALTH
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
        inventory: dict[str, int] = {}
        for stack in body.get("inventory") or ():
            inventory[stack["item"]] = inventory.get(stack["item"], 0) + stack["count"]
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
        from mclab.gather_plan import plan
        from mclab.gather_runner import flush_containers, run_gather

        item, count = args["item"], args["count"]
        knowledge = self.knowledge()
        held = self.body.inventory().get(item, 0)
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
        now = self.body.inventory().get(item, 0)
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
