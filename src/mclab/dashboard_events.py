"""Shared, bounded Server-Sent Events snapshots for the local dashboard."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

_SECRET_KEY = re.compile(
    r"(?:token(?!s)|password|secret|credential|authorization|api[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?i)(authorization|bearer|token|password|secret|api[_-]?key)"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_TEXT = re.compile(r"(?i)\bbearer\s+\S+")
_LOG = logging.getLogger(__name__)


def redact(value: Any, key: str = "") -> Any:
    """Remove credential fields and redact common credential text in logs."""
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(name): redact(child, str(name)) for name, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(child) for child in value]
    if isinstance(value, str):
        return _BEARER_TEXT.sub(
            "Bearer [REDACTED]", _SECRET_TEXT.sub(r"\1=[REDACTED]", value)
        )
    return value


class TooManyListeners(Exception):
    """Raised when the dashboard's bounded event listener pool is full."""


class Subscription:
    def __init__(self, broker: EventBroker, seen: int):
        self.broker = broker
        self.closed = False
        self.seen = seen

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.broker._unsubscribe()

    def stream(self, heartbeat: float = 15.0):
        last_heartbeat = time.monotonic()
        while not self.closed:
            frame = None
            with self.broker.condition:
                while (
                    not self.closed
                    and not self.broker.closed
                    and self.broker.sequence <= self.seen
                ):
                    remaining = heartbeat - (time.monotonic() - last_heartbeat)
                    if remaining <= 0:
                        break
                    self.broker.condition.wait(remaining)
                if self.closed or self.broker.closed:
                    return
                if self.broker.sequence > self.seen:
                    self.seen = self.broker.sequence
                    frame = self.broker.latest_frame
            if frame is not None:
                last_heartbeat = time.monotonic()
                yield frame
            else:
                last_heartbeat = time.monotonic()
                yield ": heartbeat\n\n"


class EventBroker:
    """Poll durable dashboard state once, then share each snapshot with listeners."""

    def __init__(
        self,
        dashboard,
        *,
        server_state=None,
        game_states=None,
        interval: float = 1.0,
        max_listeners: int = 8,
    ):
        self.dashboard = dashboard
        self.server_state = server_state or self._server_state
        self.game_states = game_states or self._game_states
        self.interval = interval
        self.max_listeners = min(max_listeners, 8)
        self.condition = threading.Condition()
        self.listeners = 0
        self.sequence = 0
        self.latest_frame: str | None = None
        self.latest_value: dict | None = None
        self.force_next = False
        self.closed = False
        self.thread: threading.Thread | None = None

    @staticmethod
    def _server_state() -> str:
        from mclab import server

        return server.status()["state"]

    @staticmethod
    def _game_states() -> dict[str, str]:
        from mclab import play_runs

        # the play and arena servers, cached: a host probe at most every 5 s each
        return play_runs.server_states()

    def snapshot(self) -> dict:
        return redact(
            {
                "status": {"state": self.server_state(), **self.game_states()},
                "sessions": self.dashboard.sessions(),
                "results": self.dashboard.history(),
            }
        )

    def subscribe(self) -> Subscription:
        with self.condition:
            if self.closed:
                raise RuntimeError("Dashboard event stream is closed")
            if self.listeners >= self.max_listeners:
                raise TooManyListeners("Dashboard event listener limit reached")
            was_idle = self.listeners == 0
            self.listeners += 1
            if was_idle and self.latest_value is not None:
                self.force_next = True
                seen = self.sequence
            else:
                seen = max(0, self.sequence - 1)
            if self.thread is None:
                self.thread = threading.Thread(
                    target=self._run, name="mclab-dashboard-events", daemon=True
                )
                self.thread.start()
            self.condition.notify_all()
        return Subscription(self, seen)

    def _unsubscribe(self) -> None:
        with self.condition:
            self.listeners = max(0, self.listeners - 1)
            self.condition.notify_all()

    def _publish(
        self, value: dict, event: str = "snapshot", *, force: bool = False
    ) -> None:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self.condition:
            if event == "snapshot" and value == self.latest_value and not force:
                return
            self.sequence += 1
            self.latest_value = value
            self.latest_frame = (
                f"id: {self.sequence}\nevent: {event}\ndata: {encoded}\n\n"
            )
            self.condition.notify_all()

    def _run(self) -> None:
        while True:
            with self.condition:
                while not self.closed and self.listeners == 0:
                    self.condition.wait()
                if self.closed:
                    return
            try:
                snapshot = self.snapshot()
                with self.condition:
                    force = self.force_next
                    self.force_next = False
                self._publish(snapshot, force=force)
            except Exception:
                _LOG.exception("Dashboard durable snapshot refresh failed")
                self._publish({"error": "Dashboard snapshot refresh failed"}, "error")
            with self.condition:
                if self.closed:
                    return
                self.condition.wait(self.interval)

    def close(self, timeout: float = 1.0) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()
            thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
