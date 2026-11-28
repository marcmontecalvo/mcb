"""The companion host: one long-lived process per play instance that owns one bot.

``Host`` implements ``companion_protocol.Host``; MCP, the control API, the talker and
``/mcb`` are thin adapters over ``call``. Docs: COMPANION_LLM_PLAN_2026-11-23.md.

Threads
- The **worker** (``companion-worker``) owns the session: the bridge, the body, world
  memory and places. It runs one job at a time and, between jobs, polls the body
  about every ``idle_s`` (heartbeat: the body releases itself after 2 s of silence).
- Callers (API, talker, ingress) only touch host state under ``_cond`` and the
  session's thread-safe ``say``. Place reads/writes are posted to a mailbox the
  worker serves between idle ticks and inside every bridge poll of a running job.
- Cancel: ``stop`` / ``when="now"`` marks the running job. The bridge runs
  ``_on_poll`` first in every ``state()`` poll, which raises ``JobCancelled`` once;
  the worker then halts the body (``cancel_route`` + ``release``) and goes idle.
  The session itself is never torn down by a stop.
"""

from __future__ import annotations

import copy
import logging
import math
import os
import queue
import re
import secrets
import signal
import threading
import time
from collections import deque
from datetime import UTC, datetime

from mclab import companion_intents
from mclab.companion_intents import (
    INTENTS,
    JOB_INTENTS,
    UNTRUSTED_INTENTS,
    Actor,
    authorized,
    validate,
)
from mclab.companion_protocol import EVENT_TYPES, TERMINAL_STATES, HostError
from mclab.companion_session import Defend, JobCancelled, JobFailed
from mclab.companion_status import STALL_SECONDS
from mclab.progress_watch import BodyDied

log = logging.getLogger(__name__)

IDLE_S = 0.25
EVENT_LIMIT = 1000
JOB_LIMIT = 500  # finished jobs kept for job_status / idempotency keys
MAILBOX_TIMEOUT_S = 5.0
MAX_WAIT_S = 60.0
PROGRESS_EVENT_S = 1.0  # job_progress events at most this often (a stage change always)
STOP_WAIT_S = 2.0  # stop waits this long for the running job to unwind
RETRY_S = (1.0, 30.0)  # session (re)start backoff, doubling
QUIET_STAGES = {"waiting", "lost sight"}  # standing still is the job (follow)
DEFENSE_REST_S = 10.0  # after a stop ends a fight the guard rests this long
BOT_NAME = re.compile(r"[A-Za-z0-9_]{3,16}")
DEFAULT_BOT = {"name": "MCBot"}


def _new_id() -> str:
    return "j-" + secrets.token_hex(4)


class Host:
    def __init__(
        self,
        session_factory,
        *,
        bot,
        clock=time.time,
        idle_s: float = IDLE_S,
        event_limit: int = EVENT_LIMIT,
        job_limit: int = JOB_LIMIT,
        mailbox_timeout: float = MAILBOX_TIMEOUT_S,
        retry_s: tuple[float, float] = RETRY_S,
    ):
        self._factory = session_factory
        self.bot = dict(bot) if isinstance(bot, dict) else {"name": str(bot)}
        self.clock = clock
        self.idle_s, self.job_limit = idle_s, job_limit
        self.mailbox_timeout, self.retry_s = mailbox_timeout, retry_s
        self._cond = threading.Condition(threading.RLock())
        self._jobs: dict[str, dict] = {}
        self._keys: dict[str, str] = {}
        self._queue: deque[str] = deque()
        self._current: str | None = None  # the job whose bridge polls may be cancelled
        self._active: str | None = None  # the job shown as running (until finished)
        self._cancel_id: str | None = None
        self._cancel_raised = False
        self._events: deque[dict] = deque(maxlen=event_limit)
        self._seq = 0
        self._progress_at: dict[str, tuple[float, float]] = {}  # id -> (update, event)
        self._snapshot: dict = {}
        self._snap_at = -math.inf
        self._moved_at = clock()
        self._session_state, self._session_detail = "starting", None
        self._last_failure: str | None = None
        self._mailbox: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._in_hook = False
        # the guard: Defend may be raised only inside a job's or the idle poll's own
        # bridge polls (never in a halt or cleanup), and not while defending
        self._guarded = False
        self._defending: str | None = None  # the reason, while a defence runs
        self._defense_cancel = False  # a stop with no job ends the defence
        self._rest_until = -math.inf  # monotonic: the guard rests after a stop
        self._session = None
        self._thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._work, name="companion-worker", daemon=True
        )
        self._thread.start()

    def wait_ready(self, timeout: float) -> bool:
        """True once a session is up (tests and main use it; jobs simply queue)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                if self._session_state == "ready":
                    return True
            time.sleep(0.005)
        return False

    def shutdown(self, timeout: float = 30.0) -> None:
        """Cancel the job and the queue, stop the worker; it halts the body and
        despawns it (a logout that saves) before closing memory and the bridge."""
        self._stopping.set()
        with self._cond:
            self._cancel_all_locked("the host is stopping")
            self._cond.notify_all()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    # --- the protocol --------------------------------------------------------------
    def call(self, intent, args, actor, *, key=None) -> dict:
        try:
            if not isinstance(actor, Actor):
                raise ValueError("actor must be an Actor")
            if intent not in INTENTS:
                validate(intent, args)  # raises the readable "Unknown intent"
            if intent not in UNTRUSTED_INTENTS and not authorized(actor):
                who = actor.name or actor.source
                return {
                    "ok": False,
                    "error": f"Not allowed: {who} may not use {intent}; "
                    "ask the bot's owner",
                }
            clean = validate(intent, args)
            if intent == "attack" and "player" in clean["target"]:
                self._check_player_target(clean["target"]["player"], actor)
            if key is not None and (
                not isinstance(key, str) or not 1 <= len(key) <= 128
            ):
                raise ValueError("key must be a string of 1 to 128 characters")
            return {"ok": True, "result": self._dispatch(intent, clean, actor, key)}
        except (ValueError, LookupError, HostError) as error:
            return {"ok": False, "error": str(error).strip("'\"")}
        except Exception as error:  # noqa: BLE001 - never raise for a bad request
            log.exception("call %s failed", intent)
            return {"ok": False, "error": f"Internal error: {error}"}

    def _check_player_target(self, name: str, actor: Actor) -> None:
        """Hitting a player is stricter than commanding the bot: the bot is never its
        own target, and only the operator or someone the config names may order it.
        The owner may name themselves: the target is whoever is named."""
        if str(self.bot.get("name") or "").lower() == name.lower():
            raise ValueError("The bot cannot attack itself")
        if not companion_intents.may_attack_players(actor):
            who = actor.name or actor.source
            raise HostError(
                f"Not allowed: {who} may not attack a player; only the bot's owner "
                "or an allowlisted player may"
            )

    def knows(self, job_id: str) -> bool:
        """Whether this host has (or had) the job: a fleet routes by it."""
        with self._cond:
            return job_id in self._jobs

    def wait_job(self, job_id: str, timeout: float) -> dict | None:
        with self._cond:
            if job_id not in self._jobs:
                return None
            self._cond.wait_for(
                lambda: job_id not in self._jobs
                or self._jobs[job_id]["state"] in TERMINAL_STATES,
                timeout=max(0.0, float(timeout)),
            )
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job is not None else None

    def events(self, after: int = 0, wait: float = 0.0) -> dict:
        wait = max(0.0, min(float(wait), MAX_WAIT_S))
        with self._cond:
            if wait > 0:
                self._cond.wait_for(
                    lambda: self._seq > after or self._stopping.is_set(), timeout=wait
                )
            return {
                "latest": self._seq,
                "events": [copy.deepcopy(e) for e in self._events if e["seq"] > after],
            }

    def trial(self, spec) -> dict:
        """Set the scene for an operator's combat trial (control API ``/trial`` only:
        no intent, so no MCP tool and no chat command can reach it). Shaped as call."""
        if not isinstance(spec, dict):
            return {"ok": False, "error": "a trial is a JSON object"}
        try:
            return {"ok": True, "result": self._on_worker(lambda s: s.trial(spec))}
        except (ValueError, HostError) as error:
            return {"ok": False, "error": str(error)}

    def note(self, type: str, data: dict) -> None:
        if type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type {type!r}")
        self._emit(type, dict(data))

    def status(self) -> dict:
        with self._cond:
            job = self._jobs.get(self._active) if self._active else None
            value = {
                "bot": self.bot.get("name"),
                "bot_id": self.bot.get("id"),
                "session": self._session_state,
                "session_detail": self._session_detail,
                **copy.deepcopy(self._snapshot),
                "job": copy.deepcopy(job),
                "queue": [self._brief(self._jobs[i]) for i in self._queue],
                "last_failure": self._last_failure,
                "defense": {
                    "active": self._defending,
                    # seconds the guard still rests after a stop ended a fight
                    "resting": round(max(0.0, self._rest_until - time.monotonic()), 1),
                },
            }
            value["explanation"] = self._explain(value, job)
        return value

    # --- requests ------------------------------------------------------------------
    def _dispatch(self, intent, args, actor, key):
        if intent == "status":
            return self.status()
        if intent == "say":
            return self._say(args["text"], args["to"])
        if intent == "list_places":
            return {"places": self._on_worker(lambda s: s.list_places())}
        if intent == "remember_place":
            return {
                "place": self._on_worker(
                    lambda s: s.remember_place(args["name"], args["pos"])
                )
            }
        if intent == "job_status":
            with self._cond:
                job = self._jobs.get(args["job_id"])
                if job is None:
                    raise HostError(f"No job {args['job_id']}")
                return {"job": copy.deepcopy(job)}
        if intent == "stop":
            return self._stop()
        if intent == "defense":
            return {"defense": self._on_worker(lambda s: s.defense_settings(args))}
        if intent in JOB_INTENTS:
            return {"job": self._submit(intent, args, actor, key)}
        raise HostError(
            f"{intent} is not served"
        )  # pragma: no cover - INTENTS cover all

    def _say(self, text, to):
        session = self._session
        if session is None:
            raise HostError("The bot is not in the world right now")
        session.say(text, to)
        self._emit("chat_out", {"text": text, "to": to})
        return {"said": text, "to": to}

    def _submit(self, intent, args, actor, key) -> dict:
        with self._cond:
            if key is not None and key in self._keys:
                return copy.deepcopy(self._jobs[self._keys[key]])
            job_id = _new_id()
            while job_id in self._jobs:
                job_id = _new_id()
            if args.get("when", "now") == "now":
                self._cancel_all_locked(f"replaced by {job_id}")
            job = {
                "id": job_id,
                "intent": intent,
                "args": args,
                "actor": actor.to_dict(),
                "key": key,
                "state": "queued",
                "created_at": self.clock(),
                "started_at": None,
                "finished_at": None,
                "progress": {
                    "stage": None,
                    "done": None,
                    "total": None,
                    "detail": None,
                },
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            self._queue.append(job_id)
            if key is not None:
                self._keys[key] = job_id
            self._emit_locked("job_queued", copy.deepcopy(job))
            self._prune_locked()
        self._wake.set()
        return copy.deepcopy(job)

    def _stop(self) -> dict:
        with self._cond:
            running, cancelled = self._cancel_all_locked("stopped")
            if self._defending is not None:
                # "stop" in a fight means stand still: the guard rests a while.
                # A stop of a job alone leaves the guard awake.
                self._rest_until = time.monotonic() + DEFENSE_REST_S
                if running is None:
                    self._defense_cancel = True
        self._wake.set()
        if running is not None:
            self.wait_job(running, STOP_WAIT_S)
        return {"stopped": running, "cancelled": cancelled}

    def _cancel_all_locked(self, reason: str):
        cancelled = []
        while self._queue:
            job_id = self._queue.popleft()
            self._finish_locked(self._jobs[job_id], "cancelled", error=reason)
            cancelled.append(job_id)
        running = self._current
        if running is not None and self._cancel_id != running:
            self._cancel_id, self._cancel_raised = running, False
        return running, cancelled

    def _on_worker(self, fn):
        """Run ``fn(session)`` on the worker thread (SQLite thread affinity)."""
        if threading.current_thread() is self._thread:
            return fn(self._require_session())
        box = {"done": threading.Event()}
        self._mailbox.put((fn, box))
        self._wake.set()
        if not box["done"].wait(self.mailbox_timeout):
            box["abandoned"] = True
            raise HostError("The bot is busy; try again in a moment")
        if "error" in box:
            raise box["error"]
        return box["value"]

    def _require_session(self):
        if self._session is None:
            raise HostError("The bot is not in the world right now")
        return self._session

    def _service_mailbox(self) -> None:
        while True:
            try:
                fn, box = self._mailbox.get_nowait()
            except queue.Empty:
                return
            if box.get("abandoned"):
                continue
            try:
                box["value"] = fn(self._require_session())
            except Exception as error:  # noqa: BLE001 - handed to the caller
                box["error"] = error
            finally:
                box["done"].set()

    # --- events and job records -----------------------------------------------------
    def _emit(self, kind: str, data: dict) -> None:
        with self._cond:
            self._emit_locked(kind, data)

    def _emit_locked(self, kind: str, data: dict) -> None:
        self._seq += 1
        self._events.append(
            {"seq": self._seq, "at": self.clock(), "type": kind, "data": data}
        )
        self._cond.notify_all()

    def _finish_locked(self, job, state, *, result=None, error=None) -> None:
        job.update(state=state, finished_at=self.clock(), result=result, error=error)
        if state == "failed":
            self._last_failure = f"{job['intent']}: {error}"
        if self._active == job["id"]:
            self._active = None
        self._progress_at.pop(job["id"], None)
        self._emit_locked("job_finished", copy.deepcopy(job))

    def _prune_locked(self) -> None:
        excess = len(self._jobs) - self.job_limit
        for job_id in [
            i for i, j in self._jobs.items() if j["state"] in TERMINAL_STATES
        ][: max(0, excess)]:
            key = self._jobs.pop(job_id)["key"]
            if key is not None and self._keys.get(key) == job_id:
                del self._keys[key]

    def _progress(
        self, job_id, stage=None, done=None, total=None, detail=None, step=None
    ):
        """A running job's progress (throttled job_progress events), or with ``step``
        one body step's outcome (a job_step event each, never throttled)."""
        now = self.clock()
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job["state"] != "running":
                return
            if step is not None:
                self._emit_locked(
                    "job_step", {"id": job_id, "intent": job["intent"], "step": step}
                )
                if (stage, done, total, detail) == (None, None, None, None):
                    return
            progress = job["progress"]
            new_stage = stage is not None and stage != progress["stage"]
            for name, value in (
                ("stage", stage),
                ("done", done),
                ("total", total),
                ("detail", detail),
            ):
                if value is not None:
                    progress[name] = value
            _, emitted = self._progress_at.get(job_id, (now, -math.inf))
            announce = new_stage or now - emitted >= PROGRESS_EVENT_S
            self._progress_at[job_id] = (now, now if announce else emitted)
            if announce:
                self._emit_locked(
                    "job_progress",
                    {"id": job_id, "intent": job["intent"], "progress": dict(progress)},
                )
            record = copy.deepcopy(job) if announce else None
        if record is not None:
            self._record_progress(record)

    def _record_progress(self, job) -> None:
        session = self._session
        if session is None:
            return
        try:
            session.record_progress(job)
        except Exception as error:  # noqa: BLE001 - progress.json is a convenience
            log.warning("progress.json not written: %s", error)

    @staticmethod
    def _brief(job) -> dict:
        return {
            "id": job["id"],
            "intent": job["intent"],
            "args": copy.deepcopy(job["args"]),
        }

    def _explain(self, value, job) -> str:
        state = value["session"]
        if state == "starting":
            return "Starting up: spawning the bot."
        if state == "died":
            return "I died; coming back."
        if state == "lost":
            return f"Lost the body ({value['session_detail']}); reconnecting."
        if state == "stopped":
            return "Not running."
        if self._defending is not None:
            paused = f" ({job['intent']} paused)" if job is not None else ""
            return f"Defending: {self._defending}{paused}."
        failure = (
            f" Last failure: {value['last_failure']}." if value["last_failure"] else ""
        )
        if job is None:
            where = value.get("position")
            text = "Idle" + (
                f" at {' '.join(str(math.floor(v)) for v in where)}" if where else ""
            )
            queued = f"; {len(value['queue'])} queued" if value["queue"] else ""
            return f"{text}{queued}.{failure}"
        progress = job["progress"]
        stage = progress["stage"] or "starting"
        counts = ""
        if progress["total"]:
            counts = f" {progress['done'] or 0:g}/{progress['total']:g}"
        updated = self._progress_at.get(job["id"], (job["started_at"] or 0, 0))[0]
        age = self.clock() - max(updated, self._moved_at)
        if age > STALL_SECONDS and stage not in QUIET_STAGES:
            return (
                f"Stalled on {job['intent']} ({stage}{counts}), no progress for "
                f"{age:.0f}s;{failure or ' no failure reported.'}"
            )
        detail = f" ({progress['detail']})" if progress["detail"] else ""
        return f"Running {job['intent']}: {stage}{counts}{detail}."

    def _publish(self, snapshot: dict) -> None:
        with self._cond:
            if snapshot.get("position") != self._snapshot.get("position"):
                self._moved_at = self.clock()
            self._snapshot = snapshot
            self._snap_at = time.monotonic()

    def _session_event(self, state: str, detail: str | None = None) -> None:
        with self._cond:
            self._session_state, self._session_detail = state, detail
            self._emit_locked("session", {"state": state, "detail": detail})

    # --- the worker -------------------------------------------------------------------
    def _on_poll(self) -> None:
        """Runs first in every bridge poll, on the worker thread."""
        if self._in_hook:
            return
        self._in_hook = True
        try:
            with self._cond:
                cancel = (
                    self._cancel_id is not None
                    and self._cancel_id == self._current
                    and not self._cancel_raised
                )
                if cancel:
                    self._cancel_raised = True  # cleanup polls after it must work
                end_defense = self._defense_cancel and self._defending is not None
                if end_defense:
                    self._defense_cancel = False
            if cancel:
                raise JobCancelled(self._cancel_id)
            if end_defense:
                raise JobCancelled("defense")
            self._service_mailbox()
            session = self._session
            if session is not None and self._guarded:
                self._guard(session)
            if session is not None and time.monotonic() - self._snap_at >= self.idle_s:
                self._publish(session.snapshot())
        finally:
            self._in_hook = False

    def _guard(self, session) -> None:
        """Raise ``Defend`` when a threat needs the bot (inside a guarded poll)."""
        if self._defending is not None or time.monotonic() < self._rest_until:
            return
        action = session.threat()
        if action.kind != "none":
            self._guarded = False  # the job's cleanup polls must not raise it again
            raise Defend(action)

    def _guarded_call(self, fn, *args):
        self._guarded = True
        try:
            return fn(*args)
        finally:
            self._guarded = False

    def _defend(self, session, action, job_id: str | None = None) -> None:
        """Carry out ``action`` and whatever the guard decides next, until nothing
        threatens. A stop (JobCancelled) and a death (BodyDied) end it early and
        reach the caller."""
        try:
            session.halt()  # the job's walk or dig stops first
        except Exception as error:  # noqa: BLE001 - the defence goes on
            log.warning("halt before defending failed: %s", error)
        last = None
        steps, end = 0, "error"
        try:
            while action.kind != "none":
                with self._cond:
                    self._defending = action.reason
                    if action.kind != last:
                        self._emit_locked(
                            "defense",
                            {"action": action.kind, "reason": action.reason,
                             "job": job_id},
                        )  # fmt: skip
                if action.kind != last:
                    last = action.kind
                    self._announce(action.reason)
                    if job_id is not None:
                        self._progress(job_id, stage="defending", detail=action.reason)
                outcome = session.defend(action)
                steps += 1
                self._emit("defense_step", {**(outcome or {"action": action.kind}),
                                            "job": job_id})  # fmt: skip
                action = session.threat()
            end = "clear"
        except JobCancelled:
            end = "stopped"
            raise
        except BodyDied:
            end = "died"
            raise
        finally:
            with self._cond:
                self._emit_locked(
                    "defense_end", {"end": end, "steps": steps, "job": job_id}
                )
                self._defending = None
                self._defense_cancel = False

    def _announce(self, reason: str) -> None:
        try:
            self._say(reason[:1].upper() + reason[1:] + ".", None)
        except Exception as error:  # noqa: BLE001 - chat is not essential
            log.warning("could not say: %s", error)

    def _work(self) -> None:
        retry = self.retry_s[0]
        while not self._stopping.is_set():
            try:
                if self._session is None:
                    if self._open_session():
                        retry = self.retry_s[0]
                    else:
                        self._pause(retry)
                        retry = min(retry * 2, self.retry_s[1])
                    continue
                job_id = self._next_job()
                if job_id is not None:
                    self._execute(job_id)
                    continue
                self._idle_tick()
                self._pause(self.idle_s)
            except JobCancelled:
                continue  # a stop that landed between jobs
            except Exception:  # noqa: BLE001 - the worker never dies
                log.exception("companion worker")
                self._pause(self.idle_s)
        self._close_session(despawn=True)

    def _pause(self, seconds: float) -> None:
        self._service_mailbox()
        if self._wake.wait(seconds):
            self._wake.clear()
        self._service_mailbox()

    def _open_session(self) -> bool:
        self._session_event("starting")
        session = None
        try:
            session = self._factory()
            session.poll_hook = self._on_poll
            session.start()
        except Exception as error:  # noqa: BLE001 - retried with backoff
            log.exception("companion session did not start")
            self._session_event("lost", f"could not start: {error}")
            if session is not None:
                self._close(session, despawn=False)
            return False
        self._session = session
        self._session_event("ready")
        self._publish(session.snapshot())
        return True

    def _close_session(self, *, despawn: bool) -> None:
        session, self._session = self._session, None
        if session is not None:
            if despawn:
                self._halt(session)
            self._close(session, despawn=despawn)
        with self._cond:
            self._session_state, self._session_detail = "stopped", None
            self._cond.notify_all()

    @staticmethod
    def _close(session, *, despawn: bool) -> None:
        try:
            session.close(despawn=despawn)
        except Exception as error:  # noqa: BLE001 - nothing more to do
            log.warning("session close failed: %s", error)

    def _next_job(self) -> str | None:
        if self._stopping.is_set():
            return None
        with self._cond:
            while self._queue:
                job_id = self._queue.popleft()
                job = self._jobs[job_id]
                if job["state"] != "queued":
                    continue
                job.update(state="running", started_at=self.clock())
                self._current = self._active = job_id
                self._cancel_id, self._cancel_raised = None, False
                self._progress_at[job_id] = (self.clock(), -math.inf)
                self._emit_locked("job_started", copy.deepcopy(job))
                return job_id
        return None

    def _execute(self, job_id: str) -> None:
        session = self._session
        with self._cond:
            job = self._jobs[job_id]
            intent, args = job["intent"], copy.deepcopy(job["args"])
            actor = Actor.from_dict(job["actor"])
        self._record_progress(copy.deepcopy(job))
        state, result, error, health = "succeeded", None, None, "ok"
        session.current_job = job_id  # a re-run after a defence keeps the job's stats
        try:
            while True:
                try:
                    result = self._guarded_call(
                        session.run,
                        intent,
                        args,
                        actor,
                        lambda **kw: self._progress(job_id, **kw),
                    )
                    break
                except Defend as threat:
                    # then the job runs again: jobs re-plan from the world as it is
                    self._defend(session, threat.action, job_id)
        except JobCancelled:
            state, error = "cancelled", "stopped"
        except BodyDied:
            state, error, health = "failed", "died", "dead"
        except JobFailed as failure:
            state, error = "failed", str(failure)
        except Exception as failure:  # noqa: BLE001 - recorded on the job
            log.exception("job %s (%s) failed", job_id, intent)
            health = self._health(session)
            state = "failed"
            error = (
                "died" if health == "dead" else f"{type(failure).__name__}: {failure}"
            )
        with self._cond:
            if job["state"] == "cancelled" or self._cancel_id == job_id:
                if state == "failed" and health == "ok":
                    state, error = "cancelled", "stopped"
            self._current = None
            self._cancel_id, self._cancel_raised = None, False
        if state != "succeeded" and health == "ok":
            self._halt(session)
        with self._cond:
            self._finish_locked(job, state, result=result, error=error)
        self._record_progress(copy.deepcopy(job))
        if health == "dead":
            self._recover(session, error)
        elif health == "lost":
            self._lose(session, error)

    def _halt(self, session) -> None:
        try:
            session.halt()
        except (Exception, JobCancelled) as error:  # noqa: BLE001 - best effort
            log.warning("halt failed: %s", error)

    @staticmethod
    def _health(session) -> str:
        try:
            return session.health()
        except Exception:  # noqa: BLE001 - unanswerable: lost
            return "lost"

    def _idle_tick(self) -> None:
        session = self._session
        try:
            try:
                self._publish(self._guarded_call(session.idle))
            except Defend as threat:
                self._defend(session, threat.action)
        except JobCancelled:
            return  # a stop ended the defence
        except BodyDied as error:
            self._recover(session, str(error))
        except Exception as error:  # noqa: BLE001 - classified below
            health = self._health(session)
            if health == "dead":
                self._recover(session, str(error))
            elif health == "lost":
                self._lose(session, str(error))
            else:
                log.warning("idle poll failed (session healthy): %s", error)

    def _recover(self, session, detail) -> None:
        """Death: say where the things lie, spawn again, tell the players."""
        self._session_event("died", detail)
        try:
            info = session.respawn()
        except Exception as error:  # noqa: BLE001 - a new session then
            log.exception("respawn failed")
            self._lose(session, f"respawn failed: {error}")
            return
        self._session_event("ready", "respawned")
        self._publish(session.snapshot())
        death = info.get("death")
        where = f" at {' '.join(str(v) for v in death)}" if death else ""
        text = f"I died{where}; my things are there (place 'death'). I'm back."
        try:
            self._say(text, None)
        except Exception as error:  # noqa: BLE001 - chat is not essential
            log.warning("could not say: %s", error)

    def _lose(self, session, detail) -> None:
        self._session_event("lost", str(detail))
        if self._session is session:
            self._session = None
        self._close(session, despawn=False)


# --- the process ---------------------------------------------------------------------


def load_bot(
    settings: dict, root=None, bot_id: str | None = None, *, names=None
) -> dict:
    """A bot's profile.

    Without ``bot_id`` (or for ``default``): ``bots/<config companion.bot>/bot.json``
    (default name MCBot). Another id is its own ``bots/<id>/bot.json`` if there is
    one, else, for ``mcbot<N>``, a copy of the first bot's profile named MCBot<N>.
    ``names`` (a ``BotNames``) is the operator's renames: they win over the profile."""
    profile = _profile(settings, root, bot_id)
    renamed = (names.renamed if names is not None else {}).get(bot_id or "default")
    if renamed:
        profile["name"] = renamed
    return profile


def _profile(settings: dict, root, bot_id: str | None) -> dict:
    from mclab.paths import ROOT, read_json

    root = root or ROOT
    base_id = (settings.get("companion") or {}).get("bot", "default")

    def profile_at(identity):
        path = root / "bots" / identity / "bot.json"
        profile = dict(read_json(path)) if path.is_file() else None
        return path, profile

    def checked(path, profile):
        profile.setdefault("name", DEFAULT_BOT["name"])
        if not BOT_NAME.fullmatch(str(profile["name"])):
            raise ValueError(f"{path}: name must be 3-16 letters, digits or _")
        return profile

    base_path, base = profile_at(base_id)
    if bot_id in (None, "default"):
        return checked(base_path, base if base is not None else {})
    path, own = profile_at(bot_id)
    if own is not None:
        own.setdefault("id", bot_id)
        return checked(path, own)
    from mclab.companion_names import bot_name_for

    try:
        name = bot_name_for(bot_id)
    except ValueError:
        raise ValueError(f"No bot profile bots/{bot_id}/bot.json") from None
    derived = copy.deepcopy(checked(base_path, base if base is not None else {}))
    derived.update({"id": bot_id, "name": name})
    return derived


def world_spawn_for(settings: dict):
    """Where a bot without a saved position comes in: the level's spawn, or on the
    arena server (``companion.spawn: "lobby"``) the lobby, built first."""
    from mclab.companion_session import level_spawn

    spawn = (settings.get("companion") or {}).get("spawn", "level")
    if spawn == "level":
        return level_spawn
    if spawn == "lobby":
        from mclab.arena import world

        return world.lobby_world_spawn
    raise ValueError(f"companion.spawn must be 'level' or 'lobby', not {spawn!r}")


def session_factory(
    bot: dict, *, results=None, runtime=None, world_spawn=None, slot=None
):
    """New Session per (re)connect: its own results folder; one GatherState for the
    host's life (stations and containers it learns).

    ``slot`` is the bot's place in a fleet. The first one (``default``, or none) keeps
    the files a single bot always had; every other bot gets its own results folder,
    saved state, defence settings and world memory, so two bots never share a file."""
    from mclab.companion_defense import DefensePolicy
    from mclab.companion_session import Session, instance_runtime, level_spawn
    from mclab.gather_runner import GatherState
    from mclab.paths import ROOT

    results = results or ROOT / "results/companion-host"
    runtime = runtime or instance_runtime()
    world_spawn = world_spawn or level_spawn
    gather_state = GatherState()
    defense = DefensePolicy.from_config(bot.get("defense"))  # a typo fails here
    if slot in (None, "default"):
        files, memory_root = runtime, None
    else:
        files = runtime / "bots" / slot
        results, memory_root = results / slot, files / "world-memory"

    def make():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return Session(
            bot["name"],
            folder=results / stamp,
            state_file=files / "companion-state.json",
            gather_state=gather_state,
            defense=defense,
            defense_file=files / "companion-defense.json",
            world_spawn=world_spawn,
            memory_root=memory_root,
        )

    return make


def member_factory(settings, runtime, world_spawn, *, root=None):
    """The fleet's factory: ``make(fleet_id)`` loads the bot's profile, starts its
    host (worker, session) and its talker. A talker that will not start is logged:
    the bot still plays, it just cannot chat. The name book (``bot-names.json`` in
    ``runtime``) is read at every build, so a restarted bot comes back renamed."""
    from mclab.companion_fleet import Member
    from mclab.companion_names import BotNames

    def make(fleet_id):
        bot = load_bot(settings, root, fleet_id, names=BotNames.load(runtime))
        bot.setdefault("id", fleet_id)
        host = Host(
            session_factory(
                bot, runtime=runtime, world_spawn=world_spawn, slot=fleet_id
            ),
            bot=bot,
        )
        member = Member(fleet_id, bot, host)
        host.start()
        try:
            from mclab import companion_talker, paths

            talker = companion_talker.start(
                host, bot=bot, instance=paths.INSTANCE.name, **_talker_kwargs(fleet_id)
            )
            member.talker = talker
            member.stop.append(talker.stop)
        except Exception:  # noqa: BLE001
            log.exception("talker for %s not started", fleet_id)
        return member

    return make


def _talker_kwargs(fleet_id) -> dict:
    """Every bot but the first logs its chats under its own results folder."""
    if fleet_id in (None, "default"):
        return {}
    from mclab.paths import ROOT

    return {"results_dir": ROOT / "results" / "companion-host" / fleet_id}


def _start_adapters(fleet, settings, token, runtime, *, on_shutdown=None):
    """API and chat ingress over the whole fleet, each optional: a missing or failing
    one is logged. ``on_shutdown`` is the API's ``POST /shutdown`` hook (the
    supervisor's stop)."""
    from mclab.companion_session import write_atomic

    stops = []
    try:
        from mclab import companion_api

        server = companion_api.serve(
            fleet,
            port=settings["companion_port"],
            token=token,
            on_shutdown=on_shutdown,
        )
        stops.append(server.shutdown)
        write_atomic(
            runtime / "companion-host.json",
            {"port": server.port, "token": token, "pid": os.getpid()},
        )
    except Exception:  # noqa: BLE001 - the host still plays without it
        log.exception("control API not started")
    try:
        from mclab import companion_ingress, paths

        if paths.INSTANCE.name == "arena":
            from mclab import arena_lobby
            from mclab.arena import client as arena_client

            def _lobby_board(arena_name: str, title: str, rows: list[dict]) -> None:
                try:
                    arena_client.ArenaClient.from_state().board(arena_name, title, rows)
                except Exception:  # noqa: BLE001
                    log.debug("lobby board update failed", exc_info=True)

            launcher = arena_lobby.make_arena_launcher(
                runtime_dir=runtime,
                lobby_getter=lambda: arena_lobby.default_lobby(
                    fleet=fleet, runtime_dir=runtime
                ),
                board=None,  # the publisher owns the floating board
            )
            lobby = arena_lobby.default_lobby(
                fleet=fleet, runtime_dir=runtime, on_launch=launcher
            )
            ingress = companion_ingress.Ingress(
                fleet,
                fleet,
                companion_ingress.bridge_events_source(),
                players=companion_ingress.bridge_players_source(),
                poll_s=0.25,
                stop_all=True,
                lobby=lobby,
                publisher=_lobby_publisher(lobby, fleet, _lobby_board),
            )
        else:
            ingress = companion_ingress.Ingress(
                fleet,
                fleet,
                companion_ingress.bridge_events_source(),
                players=companion_ingress.bridge_players_source(),
                stop_all=True,
            )
        ingress.start()
        stops.append(ingress.stop)
    except Exception:  # noqa: BLE001
        log.exception("chat ingress not started")
    return stops


def _lobby_publisher(lobby, fleet, board, *, client_factory=None, players=None):
    """The lobby's game menu, signs and floating board, sent to the arena server.

    Companion bots are not players: they never get a menu."""
    from mclab import arena_lobby_menu, companion_ingress
    from mclab.arena import client as arena_client

    factory = client_factory or arena_client.ArenaClient.from_state

    def send(**fields):
        return factory().lobby_view(**fields)

    def bot_names():
        try:
            return [row["name"] for row in fleet.bots() if row.get("name")]
        except Exception:  # noqa: BLE001
            return []

    return arena_lobby_menu.LobbyPublisher(
        send,
        players or companion_ingress.bridge_players_source(),
        lobby,
        board=board,
        skip=bot_names,
    )


def main(argv=None) -> int:
    """``python -m mclab.companion_host``: run the play instance's companions until
    the control API's ``POST /shutdown`` (the supervisor) or SIGTERM / Ctrl+Break /
    Ctrl+C. One bot (MCBot) starts; the API adds and removes the others on demand
    (``POST /bots``). On the way out: adapters stop, the jobs are cancelled, the
    bodies halt and despawn (a logout that saves), memory and bridges close,
    ``companion-host.json`` is removed."""
    from mclab import paths
    from mclab.companion_fleet import Fleet
    from mclab.companion_session import instance_runtime

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = paths.config()
    runtime = instance_runtime()
    fleet = Fleet(member_factory(settings, runtime, world_spawn_for(settings)))
    stopping = threading.Event()

    def stop(*_):
        stopping.set()

    for name in ("SIGTERM", "SIGBREAK", "SIGINT"):
        if (number := getattr(signal, name, None)) is not None:
            signal.signal(number, stop)
    fleet.add()  # the first bot; a bad profile stops the host here, at start
    stops = _start_adapters(
        fleet, settings, secrets.token_urlsafe(32), runtime, on_shutdown=stop
    )
    try:
        while not stopping.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        for halt in reversed(stops):
            try:
                halt()
            except Exception:  # noqa: BLE001 - stop the rest anyway
                log.exception("adapter stop failed")
        fleet.shutdown()
        (runtime / "companion-host.json").unlink(missing_ok=True)
    return 0


__all__ = [
    "Host",
    "HostError",
    "JobCancelled",
    "JobFailed",
    "companion_intents",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
