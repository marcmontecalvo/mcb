import pytest

from mclab.progress_watch import ProgressWatch, StallError, WatchedBody


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Walker:
    """goto_stance fails after walking ``dx`` blocks and taking ``took`` seconds."""

    def __init__(self, clock, dx=0.0, took=0.0, ok=False):
        self.clock, self.dx, self.took, self.ok = clock, dx, took, ok
        self.pos = (0.0, 64.0, 0.0)

    def position(self):
        return self.pos

    def goto_stance(self, target, reach):
        self.clock.t += self.took
        self.pos = (self.pos[0] + self.dx, self.pos[1], self.pos[2])
        return self.ok


def test_a_long_failed_walk_is_not_progress_just_because_the_feet_moved():
    clock = Clock()
    body = Walker(clock, dx=19.0, took=31.7)
    watch = ProgressWatch(clock=clock, log=lambda m: None)
    wb = WatchedBody(body, watch)
    wb.goto_stance((40, 64, 0), 4.5)
    assert [e["kind"] for e in watch.long_stops] == ["stall"]
    assert watch.long_stops[0]["op"] == "goto_stance"


def test_failed_long_walks_in_a_row_stop_the_run_instead_of_hiding_as_slow_ops():
    clock = Clock()
    body = Walker(clock, dx=3.0, took=30.0)
    watch = ProgressWatch(clock=clock, log=lambda m: None)
    wb = WatchedBody(body, watch)
    with pytest.raises(StallError):
        for _ in range(10):
            wb.goto_stance((90, 64, 0), 4.5)


def test_a_quick_failed_walk_that_got_nearer_is_progress():
    clock = Clock()
    body = Walker(clock, dx=3.0, took=2.0)
    watch = ProgressWatch(clock=clock, log=lambda m: None)
    wb = WatchedBody(body, watch)
    for _ in range(5):
        wb.goto_stance((90, 64, 0), 4.5)
    assert watch.events == []


def test_a_successful_slow_walk_is_still_a_recovered_slow_op():
    clock = Clock()
    body = Walker(clock, dx=19.0, took=31.7, ok=True)
    watch = ProgressWatch(clock=clock, log=lambda m: None)
    WatchedBody(body, watch).goto_stance((40, 64, 0), 4.5)
    assert [e["kind"] for e in watch.long_stops] == ["slow_op"]
    assert watch.events == []


def test_a_failed_walk_that_moved_away_is_not_progress():
    clock = Clock()
    body = Walker(clock, dx=-3.0, took=1.0)
    watch = ProgressWatch(clock=clock, log=lambda m: None)
    wb = WatchedBody(body, watch)
    wb.goto_stance((40, 64, 0), 4.5)
    clock.t += 6
    wb.goto_stance((40, 64, 0), 4.5)
    assert len(watch.events) == 1
