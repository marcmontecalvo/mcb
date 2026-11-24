"""Pursuit: can the bot close on its target (pure rules over the gap's history)."""

import pytest

from mclab import companion_pursuit as pursuit

LOOK = 60  # ticks between two looks of the attack job (one fight step of a player)


def effect(name, amplifier=0):
    return {"id": f"minecraft:{name}", "amplifier": amplifier, "duration": 600}


def chase(gaps, *, step=LOOK, uuid="u-foe", foe_speed=0.0, start=0):
    """A pursuit that has looked at each gap in turn (blocks), ``step`` ticks apart,
    the foe on the x axis ahead of a bot at the origin; the foe's own course is
    ``foe_speed`` blocks a second further along the axis (a kiter), else it stays
    where the gap puts it."""
    run = pursuit.Pursuit()
    for i, gap in enumerate(gaps):
        tick = start + i * step
        run.observe(uuid, tick, (0.0, 64.0, 0.0), (gap, 64.0, 0.0))
    return run


# --- own speed ----------------------------------------------------------------------


def test_the_bots_own_speed_is_a_sprint_scaled_by_its_speed_and_slowness_effects():
    assert pursuit.own_speed([]) == pytest.approx(pursuit.SPRINT_SPEED)
    assert pursuit.own_speed(None) == pytest.approx(pursuit.SPRINT_SPEED)
    slowed = pursuit.own_speed([effect("slowness")])
    assert slowed == pytest.approx(pursuit.SPRINT_SPEED * 0.85)
    assert pursuit.own_speed([effect("slowness", 1)]) == pytest.approx(
        pursuit.SPRINT_SPEED * 0.70
    )
    assert pursuit.own_speed([effect("speed")]) == pytest.approx(
        pursuit.SPRINT_SPEED * 1.2
    )
    both = pursuit.own_speed([effect("speed"), effect("slowness")])
    assert both == pytest.approx(pursuit.SPRINT_SPEED * 1.2 * 0.85)  # they multiply


def test_a_deep_slowness_stops_the_bot_and_a_garbled_row_is_ignored():
    assert pursuit.own_speed([effect("slowness", 7)]) == 0.0  # never negative
    rows = [effect("strength"), {"id": 5}, "junk", {"amplifier": 2}]
    assert pursuit.own_speed(rows) == pytest.approx(pursuit.SPRINT_SPEED)


# --- the gap's history ---------------------------------------------------------------


def test_a_target_the_bot_is_gaining_on_can_be_closed_on():
    run = chase([20.0, 14.0, 8.0])
    assert run.closing_rate() == pytest.approx(6.0 / (LOOK / 20))  # 6 blocks a second
    assert not run.cannot_close(pursuit.SPRINT_SPEED)


def test_a_kiter_holding_the_gap_cannot_be_closed_on():
    # live series 20261124T164259Z: a player ran from the bot at its own pace and the
    # gap stayed 5 to 6 blocks for a whole 200-tick fight, no hit landing
    run = chase([6.0, 6.0])
    assert run.closing_rate() == pytest.approx(0.0)
    assert run.cannot_close(pursuit.SPRINT_SPEED)


def test_a_target_in_reach_is_never_a_kiter_however_the_gap_moves():
    run = chase([pursuit.REACH_BAND - 0.5, pursuit.REACH_BAND - 0.5])
    assert not run.cannot_close(pursuit.SPRINT_SPEED)


def test_one_look_or_a_short_span_proves_nothing():
    assert not chase([12.0]).cannot_close(pursuit.SPRINT_SPEED)
    assert chase([12.0]).closing_rate() is None
    quick = chase([12.0, 12.0], step=pursuit.MIN_SPAN_TICKS - 1)
    assert quick.closing_rate() is None
    assert not quick.cannot_close(pursuit.SPRINT_SPEED)
    assert chase([12.0, 12.0], step=pursuit.MIN_SPAN_TICKS).cannot_close(
        pursuit.SPRINT_SPEED
    )


def test_a_slowed_bot_behind_a_runner_is_known_to_fall_short_at_once():
    # slowness I: 4.8 blocks a second against a sprinting player's 5.6. Two looks a
    # second apart (the foe 5.6 blocks further on, the bot gaining nothing) say so
    # without waiting out the usual span.
    def looked():
        run = pursuit.Pursuit()
        run.observe("u", 0, (0.0, 64.0, 0.0), (10.0, 64.0, 0.0))
        run.observe("u", 20, (4.0, 64.0, 0.0), (15.6, 64.0, 0.0))  # gap 10 to 11.6
        return run

    run = looked()
    assert run.closing_rate() is None  # too short to measure on its own
    assert run.foe_speed() == pytest.approx(5.6)
    assert run.cannot_close(pursuit.own_speed([effect("slowness")]))
    # the same two looks with a bot quick enough to be gaining are no verdict
    assert not looked().cannot_close(pursuit.SPRINT_SPEED + 1.0)


def test_a_foe_running_toward_the_bot_is_closed_on_whatever_the_speeds():
    run = pursuit.Pursuit()
    run.observe("u", 0, (0.0, 64.0, 0.0), (20.0, 64.0, 0.0))
    run.observe("u", 20, (0.0, 64.0, 0.0), (14.4, 64.0, 0.0))  # it runs at the bot
    assert not run.cannot_close(pursuit.own_speed([effect("slowness", 2)]))


def test_the_verdict_has_a_band_so_it_does_not_flip_each_look():
    run = pursuit.Pursuit()
    for i, gap in enumerate([20.0, 20.0, 20.0]):  # a kiter: ranged
        run.observe("u", i * LOOK, (0.0, 64.0, 0.0), (gap, 64.0, 0.0))
    assert run.cannot_close(pursuit.SPRINT_SPEED)
    # a little ground gained (above the kiter line, below the resume line): same
    run.observe("u", 3 * LOOK, (0.0, 64.0, 0.0), (20.0 - 3.0, 64.0, 0.0))
    gained = run.closing_rate()
    assert pursuit.MIN_CLOSING < gained < pursuit.RESUME_CLOSING
    assert run.cannot_close(pursuit.SPRINT_SPEED)
    # a clear gain: the foe is coming to melee, so melee is back
    run.observe("u", 4 * LOOK, (0.0, 64.0, 0.0), (6.0, 64.0, 0.0))
    assert run.closing_rate() >= pursuit.RESUME_CLOSING
    assert not run.cannot_close(pursuit.SPRINT_SPEED)


def test_another_target_starts_a_fresh_history():
    run = chase([6.0, 6.0], uuid="a")
    assert run.cannot_close(pursuit.SPRINT_SPEED)
    run.observe("b", 2 * LOOK, (0.0, 64.0, 0.0), (6.0, 64.0, 0.0))
    assert run.uuid == "b" and run.closing_rate() is None
    assert not run.cannot_close(pursuit.SPRINT_SPEED)


def test_a_look_that_is_no_later_than_the_last_is_ignored():
    run = chase([20.0, 14.0])
    run.observe("u-foe", LOOK, (0.0, 64.0, 0.0), (30.0, 64.0, 0.0))  # same tick
    run.observe("u-foe", 0, (0.0, 64.0, 0.0), (30.0, 64.0, 0.0))  # earlier tick
    assert run.closing_rate() == pytest.approx(6.0 / (LOOK / 20))


def test_old_looks_age_out_of_the_window():
    run = chase([30.0, 30.0, 30.0, 12.0, 2.0], step=LOOK)  # a kiter that then turns
    assert run.span() <= pursuit.WINDOW_TICKS
    assert run.closing_rate() > pursuit.RESUME_CLOSING  # the old flat looks are gone


# --- targets the bot cannot get at ---------------------------------------------------


def test_a_blocked_target_is_remembered_for_a_while_then_given_another_try():
    run = pursuit.Pursuit()
    assert run.blocked(0) == frozenset()
    run.block("u-ann", 100)
    assert run.blocked(100) == {"u-ann"}
    assert run.blocked(100 + pursuit.BLOCK_TICKS - 1) == {"u-ann"}
    assert run.blocked(100 + pursuit.BLOCK_TICKS) == frozenset()  # tried again
    run.block("u-ann", 100)
    run.block("u-bob", 150)
    assert run.blocked(150) == {"u-ann", "u-bob"}
    run.clear_block("u-ann")
    assert run.blocked(150) == {"u-bob"}
