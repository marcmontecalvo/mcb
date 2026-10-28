"""Explicit development-only Python scheduling experiments."""

import math
import sys
from contextlib import contextmanager


@contextmanager
def thread_switch_interval(milliseconds=None):
    """Temporarily change process-wide GIL scheduling, preserving caller settings.

    This is not a control deadline or a guarantee of thread preemption: native
    operations can hold the GIL longer. Call only under exclusive arena ownership.
    None leaves the interpreter's current setting untouched.
    """
    if milliseconds is None:
        yield
        return
    if (
        type(milliseconds) not in (int, float)
        or not math.isfinite(milliseconds)
        or not 0.1 <= milliseconds <= 5
    ):
        raise ValueError("Thread switch interval must be 0.1..5 ms")
    previous = sys.getswitchinterval()
    sys.setswitchinterval(milliseconds / 1000)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)
