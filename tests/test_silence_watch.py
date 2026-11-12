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


def test_a_full_collection_is_paid_once_and_a_long_pause_is_recorded():
    import gc

    from mclab import silence_watch

    events = []
    dog = make(events, threshold=30)
    heap = [{"position": [i, i, i]} for i in range(300_000)]  # a small map
    silence_watch.GC_PAUSE_S, kept = 0.0, silence_watch.GC_PAUSE_S
    try:
        dog.touch()  # the first request installs the collector hook
        gc.collect()
        frozen = gc.get_freeze_count()
        assert frozen >= 600_000  # what survived is never walked again
        started = time.perf_counter()
        gc.collect()
        second = time.perf_counter() - started
        dog.touch()  # the next request writes the pauses out
    finally:
        silence_watch.GC_PAUSE_S = kept
        dog.close()
    pauses = [p["pause_s"] for k, p in events if k == "python_gc"]
    assert len(pauses) >= 2 and second < max(pauses[0], 0.005)
    assert gc.get_freeze_count() == 0  # close hands everything back
    gc.collect()
    assert len(heap) == 300_000 and len(pauses) == len(
        [k for k, _ in events if k == "python_gc"]
    )  # no hook left behind
