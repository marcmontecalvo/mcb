"""The combat trial runner (scripts/combat-trial.py) against a fake control API."""

import pytest

from mclab import combat_trial as ct

M = "minecraft:"


def test_kits_hold_one_weapon_and_a_bow_gets_arrows():
    assert ct.kit("sword") == [{"item": M + "iron_sword", "count": 1}]
    assert ct.kit("axe", tier="diamond") == [{"item": M + "diamond_axe", "count": 1}]
    assert ct.kit("spear") == [{"item": M + "iron_spear", "count": 1}]
    assert ct.kit("bow") == [
        {"item": M + "bow", "count": 1},
        {"item": M + "arrow", "count": 64},
    ]
    assert ct.kit("fist") == []
    with pytest.raises(ValueError, match="weapon"):
        ct.kit("club")


def test_scene_clears_heals_arms_and_summons_at_a_distance():
    spec = ct.scene("sword", "zombie", distance=9, time=6000)

    assert spec == {
        "surface": True,  # out of a hole or a cave: trials are fought in the open
        "clear": ct.CLEAR,
        "clear_inventory": True,
        "heal": True,
        "time": 6000,
        "give": [{"item": M + "iron_sword", "count": 1}],
        # anywhere about 9 blocks off, in sight, on ground level with the bot
        "summon": [{"type": M + "zombie", "distance": 9, "helmet": True}],
    }


def test_only_undead_that_burn_get_a_helmet():
    assert ct.scene("axe", "skeleton")["summon"][0]["helmet"] is True
    assert ct.scene("axe", "creeper")["summon"][0]["helmet"] is False
    assert ct.scene("axe", "pig")["summon"][0]["helmet"] is False


def test_unknown_mob_names_are_refused():
    with pytest.raises(ValueError, match="mob"):
        ct.scene("sword", "Zombie!")


def _event(seq, type, **data):
    return {"seq": seq, "at": 0, "type": type, "data": data}


def test_summary_adds_the_steps_and_keeps_the_order_of_actions():
    events = [
        _event(1, "defense", action="fight", reason="fighting the zombie"),
        _event(2, "defense_step", action="fight", ok=True, kills=0, hits=2, crits=1,
               ticks=40, health=18.0, damage=12.5, flees=1, closest=3.4),
        _event(3, "chat_out", text="hi"),
        _event(4, "defense_step", action="fight", ok=True, kills=1, hits=1, crits=1,
               ticks=25, health=17.0, end="clear", damage=7.5, flees=1, closest=4.1),
    ]  # fmt: skip

    s = ct.summarize(events)

    assert s == {
        "actions": ["fight"],
        "steps": 2,
        "kills": 1,
        "hits": 3,
        "crits": 2,
        "shots": 0,
        "damage": 20.0,
        "jumps": 0,
        "ticks": 65,
        "flees": 2,
        "closest": 3.4,  # the nearest any step let its target come
        "health": 17.0,
        "ends": ["clear"],
        "failures": [],
    }


def test_summary_records_failed_steps_and_escape_jumps():
    events = [
        _event(1, "defense", action="escape", reason="running"),
        _event(2, "defense_step", action="escape", ok=False, reason="cornered",
               jumps=3, ticks=30),
        _event(3, "defense", action="walk_away", reason="walking"),
        _event(4, "defense_step", action="walk_away"),
    ]  # fmt: skip

    s = ct.summarize(events)

    assert s["actions"] == ["escape", "walk_away"]
    assert s["failures"] == ["escape: cornered"]
    assert (s["jumps"], s["ticks"], s["health"]) == (3, 30, None)
    assert s["closest"] is None  # no fight step reported one


def _status(job=None, queue=(), active=None, resting=0.0, position=None):
    return {"job": job, "queue": list(queue), "position": position,
            "defense": {"active": active, "resting": resting}}  # fmt: skip


QUIET = _status()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeClient:
    """Plays back a scripted event feed; each events() call is one wait."""

    def __init__(self, clock, feed=(), job=None, trial_ok=True, statuses=()):
        self.clock = clock
        self.feed = list(feed)  # [(at_time, event)]
        self.job = job
        self.trial_ok = trial_ok
        self.statuses = list(statuses)  # each status call takes the next; then quiet
        self.trials, self.calls, self.waits = [], [], []
        self.status_at, self.trial_at = [], None

    def trial(self, spec):
        self.trial_at = self.clock.now
        self.trials.append(spec)
        if not self.trial_ok:
            return {"ok": False, "error": "trial failed: unknown_entity"}
        return {"ok": True, "result": {"summoned": [{"type": "x", "uuid": "u1"}]}}

    def events(self, after=0, wait=0.0):
        ready = [e for at, e in self.feed if at <= self.clock.now + wait]
        out = [e for e in ready if e["seq"] > after]
        if not out:
            self.clock.now += wait
        else:
            self.clock.now = max(self.clock.now, max(
                at for at, e in self.feed if e in out))  # fmt: skip
        latest = max([after] + [e["seq"] for e in ready])
        return {"latest": latest, "events": out}

    def call(self, intent, args=None, key=None, source="mcp"):
        if intent == "status":
            self.status_at.append(self.clock.now)
            state = self.statuses.pop(0) if self.statuses else QUIET
            return {"ok": True, "result": state}
        self.calls.append((intent, args, source))
        return {"ok": True, "result": {"job": {"id": "j1", "state": "queued"}}}

    def wait_job(self, job_id, wait=0.0):
        self.waits.append((job_id, wait))
        self.clock.now += 3.0
        return self.job


def test_a_hostile_trial_ends_at_the_kill():
    clock = FakeClock()
    client = FakeClient(clock, feed=[
        (0.5, _event(1, "defense", action="fight", reason="fighting the zombie")),
        (2.0, _event(2, "defense_step", action="fight", ok=True, kills=1, hits=3,
                     crits=2, ticks=50, health=19.0, end="clear")),
        (9.0, _event(3, "defense_step", action="fight", ok=True, kills=0)),
    ])  # fmt: skip

    out = ct.run_one(client, "sword", "zombie", timeout=60, clock=clock)

    assert client.trials == [ct.scene("sword", "zombie")]
    assert out["weapon"] == "sword" and out["mob"] == "zombie"
    assert out["killed"] is True and out["end"] == "killed"
    assert (out["kills"], out["crits"], out["steps"]) == (1, 2, 1)
    assert out["seconds"] == pytest.approx(2.0)


def test_a_hostile_trial_ends_with_the_defence_when_nothing_is_killed():
    clock = FakeClock()
    client = FakeClient(clock, feed=[
        (1.0, _event(1, "defense", action="escape", reason="running")),
        (3.0, _event(2, "defense_step", action="escape", ok=True, end="clear",
                     jumps=6)),
        (3.1, _event(3, "defense_end", end="clear", steps=1, job=None)),
    ])  # fmt: skip

    out = ct.run_one(client, "bow", "zombie", timeout=60, clock=clock)

    assert out["killed"] is False and out["end"] == "defense_clear"
    assert out["actions"] == ["escape"] and out["jumps"] == 6
    assert out["seconds"] == pytest.approx(3.1)


def test_a_kill_reported_in_the_last_step_wins_over_the_defence_end():
    clock = FakeClock()
    client = FakeClient(clock, feed=[
        (0.5, _event(1, "defense", action="fight")),
        (1.0, _event(2, "defense_step", action="fight", ok=True, kills=1)),
        (1.0, _event(3, "defense_end", end="clear", steps=1, job=None)),
    ])  # fmt: skip

    out = ct.run_one(client, "sword", "zombie", timeout=60, clock=clock)

    assert out["killed"] is True and out["end"] == "killed"


def test_a_death_in_the_trial_is_its_end_at_once():
    clock = FakeClock()
    client = FakeClient(clock, feed=[
        (0.5, _event(1, "defense", action="fight")),
        (2.0, _event(2, "defense_end", end="died", steps=0, job=None)),
        (8.0, _event(3, "defense", action="fight")),  # never waited for
    ])  # fmt: skip

    out = ct.run_one(client, "sword", "creeper", timeout=60, clock=clock)

    assert out["end"] == "defense_died" and out["killed"] is False
    assert out["seconds"] == pytest.approx(2.0) and clock.now == pytest.approx(2.0)


def test_the_end_of_a_defence_begun_before_the_trial_is_not_its_end():
    clock = FakeClock()
    client = FakeClient(clock, feed=[
        (0.2, _event(1, "defense_end", end="clear", steps=3, job=None)),
        (6.0, _event(2, "defense", action="fight")),  # a later poll than the end
        (7.0, _event(3, "defense_step", action="fight", ok=True, kills=1)),
    ])  # fmt: skip

    out = ct.run_one(client, "sword", "skeleton", timeout=60, clock=clock)

    assert out["end"] == "killed" and out["actions"] == ["fight"]


def test_a_defence_taken_up_again_after_its_end_is_followed_to_the_kill():
    clock = FakeClock()
    client = FakeClient(clock, feed=[
        (0.5, _event(1, "defense", action="fight")),
        (2.0, _event(2, "defense_step", action="fight", ok=True, kills=0, shots=4)),
        (2.1, _event(3, "defense_end", end="clear", steps=1, job=None)),
        (3.0, _event(4, "defense", action="fight")),  # the mob came back in sight
        (6.0, _event(5, "defense_step", action="fight", ok=True, kills=1, shots=3)),
    ])  # fmt: skip

    out = ct.run_one(client, "bow", "zombie", timeout=60, clock=clock)

    assert out["end"] == "killed" and out["actions"] == ["fight", "fight"]
    assert out["shots"] == 7 and out["seconds"] == pytest.approx(6.0)


def test_the_trial_waits_for_a_quiet_bot_before_setting_the_scene():
    clock = FakeClock()
    client = FakeClient(clock, statuses=[
        _status(job={"id": "j0", "state": "running"}),
        _status(active="fighting the zombie"),
        _status(resting=4.0),
    ])  # fmt: skip

    out = ct.run_one(client, "sword", "zombie", timeout=5, clock=clock)

    assert len(client.status_at) == 4  # three busy, then quiet
    assert client.trial_at == client.status_at[-1] > client.status_at[2] + 3.9
    assert out["settled"] is True and out["settle"] == pytest.approx(client.trial_at)


def test_a_bot_that_will_not_settle_is_stopped_once_then_the_trial_goes_on():
    clock = FakeClock()
    busy = _status(job={"id": "j0", "state": "running"})
    client = FakeClient(clock, statuses=[busy] * 1000)

    out = ct.run_one(client, "sword", "zombie", timeout=5, clock=clock)

    assert client.calls[0] == ("stop", {}, "api")
    assert out["settled"] is False and client.trials  # measured, flagged


def test_a_hostile_trial_with_no_reaction_times_out():
    clock = FakeClock()
    client = FakeClient(clock)

    out = ct.run_one(client, "sword", "zombie", timeout=20, clock=clock)

    assert out["end"] == "timeout" and out["steps"] == 0
    assert clock.now >= 20


def test_events_before_the_trial_are_not_counted():
    clock = FakeClock()
    old = _event(7, "defense_step", action="fight", ok=True, kills=5)
    client = FakeClient(clock, feed=[
        (-1.0, old),
        (0.5, _event(8, "defense", action="fight")),
        (1.0, _event(9, "defense_step", action="fight", ok=True, kills=1)),
    ])  # fmt: skip

    out = ct.run_one(client, "axe", "skeleton", timeout=30, clock=clock)

    assert out["kills"] == 1


def test_an_animal_trial_hunts_through_gather():
    clock = FakeClock()
    job = {"id": "j1", "state": "succeeded", "result": {"gathered": 1},
           "error": None, "started_at": 10.0, "finished_at": 14.5}  # fmt: skip
    client = FakeClient(clock, job=job)

    out = ct.run_one(client, "axe", "pig", timeout=60, clock=clock)

    assert client.calls == [("gather", {"item": M + "porkchop", "count": 1}, "api")]
    assert client.waits == [("j1", 60)]
    assert out["killed"] is True and out["end"] == "succeeded"
    assert out["seconds"] == pytest.approx(4.5)
    assert out["job"] == {"state": "succeeded", "result": {"gathered": 1},
                          "error": None}  # fmt: skip


def test_an_animal_trial_that_fails_reports_the_error():
    clock = FakeClock()
    job = {
        "id": "j1",
        "state": "failed",
        "result": None,
        "error": "I can't find a pig",
        "started_at": 1.0,
        "finished_at": 2.0,
    }
    client = FakeClient(clock, job=job)

    out = ct.run_one(client, "sword", "cow", timeout=60, clock=clock)

    assert client.calls[0][1] == {"item": M + "beef", "count": 1}
    assert out["killed"] is False and out["end"] == "failed"
    assert out["job"]["error"] == "I can't find a pig"


def test_a_hunt_past_its_time_is_stopped():
    clock = FakeClock()
    client = FakeClient(clock, job={"id": "j1", "state": "running"})

    out = ct.run_one(client, "sword", "pig", timeout=10, clock=clock)

    assert out["end"] == "timeout" and out["killed"] is False
    assert [w for _, w in client.waits] == [10, 7, 4, 1]
    assert client.calls[-1] == ("stop", {}, "api")


def test_a_hostile_trial_past_its_time_is_stopped():
    clock = FakeClock()
    client = FakeClient(clock)

    ct.run_one(client, "sword", "zombie", timeout=5, clock=clock)

    assert client.calls == [("stop", {}, "api")]


def test_a_refused_hunt_reports_why():
    clock = FakeClock()
    client = FakeClient(clock)
    client.call = lambda *a, **k: {"ok": False, "error": "busy"}

    out = ct.run_one(client, "axe", "cow", timeout=10, clock=clock)

    assert (out["end"], out["error"]) == ("refused", "busy")


def test_a_hunt_whose_job_vanished_says_so():
    clock = FakeClock()
    client = FakeClient(clock, job=None)

    assert ct.run_one(client, "axe", "sheep", timeout=10, clock=clock)["end"] == (
        "lost_job"
    )


def test_a_bot_away_from_the_arena_walks_back_before_the_scene():
    clock = FakeClock()
    far = _status(position=[110.4, 64.0, -20.7])
    client = FakeClient(clock, job={"state": "succeeded"}, statuses=[far, far])

    out = ct.run_one(client, "sword", "zombie", timeout=5, clock=clock,
                     arena=(100, 64, -20))  # fmt: skip

    gotos = [c for c in client.calls if c[0] == "goto"]
    assert gotos == [("goto", {"x": 100, "y": 64, "z": -20}, "api")]
    # measured across, from the arena block's centre
    assert out["arena"] == {"from": pytest.approx(9.97, abs=0.01),
                            "end": "succeeded"}  # fmt: skip
    assert client.trial_at is not None and client.trial_at >= 3.0  # after the walk


def test_a_bot_within_the_arena_slack_stays_where_it_is():
    clock = FakeClock()
    near = _status(position=[101.5, 64.0, -18.5])
    client = FakeClient(clock, statuses=[near, near])

    out = ct.run_one(client, "sword", "zombie", timeout=5, clock=clock,
                     arena=(100, 64, -20))  # fmt: skip

    assert [c for c in client.calls if c[0] == "goto"] == []
    assert out["arena"] == {"from": pytest.approx(1.41, abs=0.01), "end": "there"}


def test_a_walk_back_that_fails_is_recorded_and_the_trial_goes_on():
    clock = FakeClock()
    far = _status(position=[130.0, 64.0, -20.0])
    client = FakeClient(clock, job={"state": "failed", "error": "no_path"},
                        statuses=[far, far])  # fmt: skip

    out = ct.run_one(client, "sword", "zombie", timeout=5, clock=clock,
                     arena=(100, 64, -20))  # fmt: skip

    assert out["arena"]["end"] == "failed"
    assert client.trials == [ct.scene("sword", "zombie")]


def test_a_walk_back_past_its_time_is_stopped():
    clock = FakeClock()
    far = _status(position=[130.0, 64.0, -20.0])
    client = FakeClient(clock, job={"state": "running"}, statuses=[far, far])

    out = ct.run_one(client, "sword", "zombie", timeout=5, clock=clock,
                     arena=(100, 64, -20))  # fmt: skip

    assert out["arena"]["end"] == "timeout"
    assert ("stop", {}, "api") in client.calls
    assert clock.now >= ct.ARENA_WAIT


def test_a_bot_with_no_known_position_is_not_walked():
    clock = FakeClock()
    client = FakeClient(clock)

    out = ct.run_one(client, "sword", "zombie", timeout=5, clock=clock,
                     arena=(100, 64, -20))  # fmt: skip

    assert [c for c in client.calls if c[0] == "goto"] == []
    assert out["arena"] == {"from": None, "end": "unknown"}


def test_without_an_arena_the_trial_never_walks():
    clock = FakeClock()
    client = FakeClient(clock, statuses=[_status(position=[500.0, 64.0, 0.0])])

    out = ct.run_one(client, "sword", "zombie", timeout=5, clock=clock)

    assert [c for c in client.calls if c[0] == "goto"] == []
    assert "arena" not in out


def test_the_arena_is_the_bots_position_when_none_is_named():
    clock = FakeClock()
    client = FakeClient(clock, statuses=[_status(position=[12.7, 70.0, -3.2])])

    assert ct.locate(client) == (12, 70, -4)
    assert ct.locate(FakeClient(clock)) is None


def test_an_operator_names_the_arena_as_three_whole_numbers():
    assert ct.parse_arena("12,64,-3") == (12, 64, -3)
    assert ct.parse_arena(" 12, 64 ,-3 ") == (12, 64, -3)
    for bad in ("12,64", "a,b,c", "1.5,64,2", "1,2,3,4", ""):
        with pytest.raises(ValueError):
            ct.parse_arena(bad)


def test_the_matrix_passes_its_arena_and_records_it(tmp_path):
    seen = []

    def one(client, weapon, mob, timeout, clock, arena=None):
        seen.append(arena)
        return {"weapon": weapon, "mob": mob, "end": "killed"}

    path = ct.run_matrix(object(), ["sword"], ["zombie"], out_dir=tmp_path,
                         timeout=5, run_one=one, arena=(1, 2, 3))  # fmt: skip

    import json

    assert seen == [(1, 2, 3)]
    assert json.loads(path.read_text(encoding="utf-8"))["arena"] == [1, 2, 3]


def test_the_matrix_echoes_a_header_once_then_rows(tmp_path):
    printed = []

    def one(client, weapon, mob, timeout, clock, arena=None):
        return {"weapon": weapon, "mob": mob, "end": "killed"}

    ct.run_matrix(object(), ["sword"], ["zombie", "skeleton"], out_dir=tmp_path,
                  timeout=5, run_one=one, echo=printed.append)  # fmt: skip

    assert [len(p.splitlines()) for p in printed] == [3, 1]


def test_a_refused_scene_is_reported_and_nothing_is_watched():
    clock = FakeClock()
    client = FakeClient(clock, trial_ok=False)

    out = ct.run_one(client, "sword", "zombie", timeout=60, clock=clock)

    assert out["end"] == "scene_refused"
    assert out["error"] == "trial failed: unknown_entity"
    assert client.calls == [] and clock.now == 0


def test_the_matrix_runs_every_pair_and_writes_one_file(tmp_path):
    clock = FakeClock()
    seen = []

    def one(client, weapon, mob, timeout, clock, arena=None):
        seen.append((weapon, mob))
        return {"weapon": weapon, "mob": mob, "end": "killed", "killed": True}

    path = ct.run_matrix(
        object(),
        ["sword", "bow"],
        ["zombie", "pig"],
        out_dir=tmp_path,
        timeout=5,
        clock=clock,
        run_one=one,
    )

    assert seen == [("sword", "zombie"), ("sword", "pig"), ("bow", "zombie"),
                    ("bow", "pig")]  # fmt: skip
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert [r["weapon"] + "/" + r["mob"] for r in data["trials"]] == [
        "sword/zombie", "sword/pig", "bow/zombie", "bow/pig"
    ]  # fmt: skip
    assert path.parent == tmp_path


def test_the_table_has_a_row_per_trial():
    rows = [
        {"weapon": "sword", "mob": "zombie", "end": "killed", "killed": True,
         "actions": ["fight"], "kills": 1, "hits": 3, "crits": 2, "shots": 0,
         "health": 18.0, "seconds": 4.2},
        {"weapon": "bow", "mob": "pig", "end": "failed", "killed": False,
         "seconds": 1.0},
    ]  # fmt: skip

    text = ct.table(rows)

    lines = text.splitlines()
    assert len(lines) == 4  # header, rule, two rows
    assert "sword" in lines[2] and "fight" in lines[2] and "4.2" in lines[2]
    assert "failed" in lines[3]


# --- groups of mobs, repeats, the per-cell summary and the baseline compare


def test_a_group_names_each_mob_and_counts():
    assert ct.group("zombie") == ["zombie"]
    assert ct.group("zombie*3") == ["zombie"] * 3
    assert ct.group("zombie*2+skeleton") == ["zombie", "zombie", "skeleton"]
    for bad in ("zombie*0", "zombie*9", "+zombie", "zombie**2", "Zombie"):
        with pytest.raises(ValueError):
            ct.group(bad)


def test_a_group_scene_summons_each_a_block_further_off():
    spec = ct.scene("sword", "zombie*2+creeper")
    assert spec["summon"] == [
        {"type": M + "zombie", "distance": ct.DISTANCE, "helmet": True},
        {"type": M + "zombie", "distance": ct.DISTANCE + 1, "helmet": True},
        {"type": M + "creeper", "distance": ct.DISTANCE + 2, "helmet": False},
    ]


def test_an_animal_is_hunted_alone():
    with pytest.raises(ValueError, match="alone"):
        ct.scene("sword", "pig+zombie")


def test_a_group_trial_ends_when_every_mob_is_killed():
    clock = FakeClock()
    client = FakeClient(clock, feed=[
        (0.5, _event(1, "defense", action="fight")),
        (2.0, _event(2, "defense_step", action="fight", ok=True, kills=1)),
        (2.1, _event(3, "defense_end", end="clear", steps=1, job=None)),
        (2.5, _event(4, "defense", action="fight")),
        (6.0, _event(5, "defense_step", action="fight", ok=True, kills=1)),
    ])  # fmt: skip

    out = ct.run_one(client, "sword", "zombie*2", timeout=60, clock=clock)

    assert out["killed"] is True and out["kills"] == 2
    assert out["seconds"] == pytest.approx(6.0)
    # timed when its batch of events arrives, as the last kill is
    assert 2.0 <= out["first_kill"] < out["seconds"]


def test_a_group_trial_with_one_left_alive_is_not_a_kill():
    clock = FakeClock()
    client = FakeClient(clock, feed=[
        (0.5, _event(1, "defense", action="fight")),
        (2.0, _event(2, "defense_step", action="fight", ok=True, kills=1)),
        (2.1, _event(3, "defense_end", end="died", steps=1, job=None)),
    ])  # fmt: skip

    out = ct.run_one(client, "sword", "zombie+skeleton", timeout=60, clock=clock)

    assert out["killed"] is False and out["end"] == "defense_died"
    assert out["kills"] == 1


def test_repeats_go_round_the_whole_matrix_each_time(tmp_path):
    seen = []

    def one(client, weapon, mob, timeout, clock, arena=None):
        seen.append((weapon, mob))
        return {"weapon": weapon, "mob": mob, "end": "killed", "killed": True,
                "seconds": 3.0}  # fmt: skip

    path = ct.run_matrix(object(), ["sword", "bow"], ["zombie"], out_dir=tmp_path,
                         timeout=5, run_one=one, repeat=2)  # fmt: skip

    import json

    assert seen == [("sword", "zombie"), ("bow", "zombie")] * 2
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [r["repeat"] for r in data["trials"]] == [1, 1, 2, 2]
    assert data["repeat"] == 2
    assert data["summary"]["sword/zombie"]["n"] == 2


def _row(weapon, mob, killed=True, seconds=3.0, health=20.0, **more):
    end = "killed" if killed else "timeout"
    return {"weapon": weapon, "mob": mob, "killed": killed, "seconds": seconds,
            "health": health, "end": end, **more}  # fmt: skip


def test_a_cell_summary_has_the_rates_and_spreads():
    rows = [
        _row("sword", "zombie", seconds=s, health=h, hits=4, crits=c, flees=0)
        for s, h, c in ((2.0, 20, 4), (3.0, 18, 2), (4.0, 16, 3), (10.0, 12, 0))
    ] + [_row("sword", "zombie", killed=False, seconds=90.0, health=4.0,
              failures=["fight: unreachable"])]  # fmt: skip

    cell = ct.summary(rows)["sword/zombie"]

    assert (cell["n"], cell["killed"]) == (5, 4)
    assert cell["kill_rate"] == pytest.approx(0.8)
    # time to kill, over the kills only
    assert cell["seconds"]["median"] == pytest.approx(3.5)
    assert cell["seconds"]["min"] == 2.0 and cell["seconds"]["max"] == 10.0
    assert cell["seconds"]["p90"] == pytest.approx(10.0)
    assert cell["health"]["min"] == 4.0
    assert cell["crit_rate"] == pytest.approx(9 / 16)
    assert cell["ends"] == {"killed": 4, "timeout": 1}
    assert cell["failures"] == {"fight: unreachable": 1}


def test_a_regression_is_flagged_against_the_baseline():
    base = ct.summary([_row("sword", "zombie", seconds=s) for s in (2.0, 2.0, 2.0)]
                      + [_row("bow", "pig", seconds=5.0)] * 3)  # fmt: skip
    now = ct.summary(
        [_row("sword", "zombie", seconds=s) for s in (4.0, 4.0, 4.0)]
        + [_row("bow", "pig", killed=False, seconds=90.0)] * 3
    )

    flagged = {(r["cell"], r["metric"]) for r in ct.compare(now, base)}

    assert ("sword/zombie", "seconds.median") in flagged
    assert ("bow/pig", "kill_rate") in flagged
    # within the noise: nothing
    same = ct.summary([_row("sword", "zombie", seconds=s) for s in (2.0, 2.2, 2.1)])
    assert ct.compare(same, base) == []


def test_the_summary_table_has_a_row_per_cell():
    text = ct.summary_table(ct.summary([_row("sword", "zombie"), _row("bow", "pig")]))

    assert len(text.splitlines()) == 4 and "sword" in text and "pig" in text
