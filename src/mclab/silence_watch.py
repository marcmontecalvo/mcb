"""Records what Python was doing while it sent nothing to the body.

`touch()` is called for every request. If none arrives for `threshold` seconds, a
daemon thread records the stack of the thread that last touched (innermost frames
first) every `interval` seconds, and `python_silence_end` carries the total length
when the next request comes. While requests flow the thread only sleeps.

It also tames the cyclic collector. A full collection walks every tracked object and
the map is millions of them, so the whole process stopped for 0.1 s early in a run
and 2.5 s after ten minutes, 38 times (iron run 20261112T184523101728Z). After each
full collection the survivors are frozen, so the next one walks only what was made
since. A collection that still pauses `GC_PAUSE_S` is recorded as `python_gc`.
"""

import gc
import sys
import threading
import time
from os.path import basename

THRESHOLD_S = 0.5
FRAMES = 16
GC_PAUSE_S = 0.1


class SilenceWatchdog:
    def __init__(self, record, *, threshold=THRESHOLD_S, interval=THRESHOLD_S):
        self._record = record  # record(kind, payload)
        self._threshold, self._interval = threshold, interval
        self._lock = threading.Lock()
        self._last = time.monotonic()
        self._ident = threading.get_ident()
        self._stacks = 0  # stacks recorded in the current silence
        self._closing = threading.Event()
        self._gc_started = 0.0
        self._pauses: list[float] = []  # the hook only appends: it may hold no lock
        self._thread = threading.Thread(
            target=self._run, name="silence-watchdog", daemon=True
        )

    def touch(self):
        now = time.monotonic()
        with self._lock:
            if not self._thread.ident and not self._closing.is_set():
                self._thread.start()  # first request: nothing runs before then
                gc.callbacks.append(self._collected)
            last, stacks = self._last, self._stacks
            self._last, self._stacks = now, 0
            self._ident = threading.get_ident()
        if stacks:
            self._record(
                "python_silence_end",
                {"duration_s": round(now - last, 3), "stacks": stacks},
            )
        pauses, self._pauses = self._pauses, []
        for pause in pauses:
            self._record("python_gc", {"pause_s": pause})

    def _collected(self, phase, info):
        if phase == "start":
            self._gc_started = time.monotonic()
            return
        if info["generation"] == 2:
            # ponytail: a frozen object that later dies in a reference cycle is
            # kept until close(); the map is acyclic, so that is little.
            gc.freeze()
        pause = time.monotonic() - self._gc_started
        if pause >= GC_PAUSE_S:
            self._pauses.append(round(pause, 3))

    def close(self):
        if self._collected in gc.callbacks:
            gc.callbacks.remove(self._collected)
            gc.unfreeze()
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
