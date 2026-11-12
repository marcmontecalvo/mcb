"""Records what Python was doing while it sent nothing to the body.

`touch()` is called for every request. If none arrives for `threshold` seconds, a
daemon thread records the stack of the thread that last touched (innermost frames
first) every `interval` seconds, and `python_silence_end` carries the total length
when the next request comes. While requests flow the thread only sleeps.
"""

import sys
import threading
import time
from os.path import basename

THRESHOLD_S = 0.5
FRAMES = 8


class SilenceWatchdog:
    def __init__(self, record, *, threshold=THRESHOLD_S, interval=THRESHOLD_S):
        self._record = record  # record(kind, payload)
        self._threshold, self._interval = threshold, interval
        self._lock = threading.Lock()
        self._last = time.monotonic()
        self._ident = threading.get_ident()
        self._stacks = 0  # stacks recorded in the current silence
        self._closing = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="silence-watchdog", daemon=True
        )

    def touch(self):
        now = time.monotonic()
        with self._lock:
            if not self._thread.ident and not self._closing.is_set():
                self._thread.start()  # first request: nothing runs before then
            last, stacks = self._last, self._stacks
            self._last, self._stacks = now, 0
            self._ident = threading.get_ident()
        if stacks:
            self._record(
                "python_silence_end",
                {"duration_s": round(now - last, 3), "stacks": stacks},
            )

    def close(self):
        self._closing.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self):
        wait = self._threshold
        while not self._closing.wait(wait):
            with self._lock:
                idle = time.monotonic() - self._last
                ident = self._ident
                due = idle >= self._threshold
                if due:
                    self._stacks += 1
            if due:
                frame = sys._current_frames().get(ident)
                stack = []
                while frame is not None and len(stack) < FRAMES:
                    code = frame.f_code
                    stack.append(
                        f"{basename(code.co_filename)}:{frame.f_lineno} {code.co_name}"
                    )
                    frame = frame.f_back
                self._record(
                    "python_silence", {"idle_s": round(idle, 3), "stack": stack}
                )
                wait = self._interval
            else:
                wait = self._threshold - idle
