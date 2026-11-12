import threading
import time

from mclab.silence_watch import SilenceWatchdog


def make(events, threshold=0.05):
    return SilenceWatchdog(
        lambda kind, payload: events.append((kind, payload)),
        threshold=threshold,
        interval=threshold,
    )


def test_records_stack_during_silence_then_duration(tmp_path):
    events = []
    dog = make(events)
    dog.touch()
    time.sleep(0.3)  # this frame is the silent thread's stack
    dog.touch()
    dog.close()
    stacks = [p for k, p in events if k == "python_silence"]
    assert 2 <= len(stacks) <= 6
    assert any("test_records_stack_during_silence" in f for f in stacks[0]["stack"])
    assert len(stacks[0]["stack"]) <= 16
    ends = [p for k, p in events if k == "python_silence_end"]
    assert len(ends) == 1 and ends[0]["duration_s"] >= 0.25
    assert ends[0]["stacks"] == len(stacks)


def test_quiet_when_requests_keep_flowing():
    events = []
    dog = make(events)
    dog.touch()
    for _ in range(15):
        time.sleep(0.01)
        dog.touch()
    dog.close()
    assert events == []


def test_close_stops_thread_even_mid_silence():
    before = threading.active_count()
    events = []
    dog = make(events, threshold=30)
    dog.touch()
    dog.touch()  # starts the thread
    started = time.monotonic()
    dog.close()
    assert time.monotonic() - started < 1
    assert threading.active_count() == before
    dog.close()  # idempotent
