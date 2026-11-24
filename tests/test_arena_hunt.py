import json

import pytest

from mclab import arena_hunt as ah


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeArena:
    """A waves match that clears one wave per `per_wave` status reads, or never."""

    def __init__(self, clock, waves=2, per_wave=3, end="won", fail_start=None):
        self.clock, self.waves, self.per_wave, self.end = clock, waves, per_wave, end
        self.reads, self.started, self.stopped = 0, [], []
        self.fail_start = fail_start

    def start(self, name, mode, *, waves=None, participants=None, teams=None):
        if self.fail_start:
            raise ah.ArenaError(self.fail_start, "no")
        self.started.append((name, mode, waves, participants))
        self.reads = 0
        return {"name": name, "mode": mode, "participants": participants}

    def status(self, name):
        self.reads += 1
        wave = min(self.reads // self.per_wave, self.waves)
        over = self.end is not None and wave >= self.waves
        mobs = (
            [] if over else [{"uuid": "m", "type": "minecraft:pillager", "health": 20}]
        )
        match = {
            "mode": "waves",
            "state": self.end if over else "running",
            "wave_index": wave,
            "waves": self.waves,
            "killed": wave * 2,
            "spawned": wave * 2 + len(mobs),
            "wave_mobs": mobs,
        }
        if over:
            match["reason"] = "cleared"
        return {"name": name, "match": match, "match_state": match["state"]}

    def stop(self, name):
        self.stopped.append(name)
        return {"name": name}


class FakeApi:
    def __init__(self, clock, health=(20.0, 14.0, 9.5, 18.0), refuse=None):
        self.clock, self.health, self.refuse = clock, list(health), refuse
        self.calls, self.trials, self.reads = [], [], 0
        self.job = {"id": "j1", "state": "running"}
        self.log = []  # host events: (type, data), seq is the index + 1

    def trial(self, spec):
        self.trials.append(spec)
        return {"ok": True, "result": {}}

    def call(self, intent, args=None, key=None, source="mcp"):
        self.calls.append((intent, args))
        if intent == "attack":
            if self.refuse:
                return {"ok": False, "error": self.refuse}
            return {"ok": True, "result": {"job": {"id": "j1"}}}
        if intent == "status":
            h = self.health[min(self.reads, len(self.health) - 1)]
            self.reads += 1
            return {
                "ok": True,
                "result": {
                    "session": "ready",
                    "body_state": "ready",
                    "health": h,
                    "position": [0, 0, 0],
                },
            }
        return {"ok": True, "result": {}}

    def wait_job(self, job_id, wait=0.0):
        self.clock.now += max(wait, 0.5)
        return dict(self.job)

    def events(self, after=0, wait=0.0):
        rows = [
            {"seq": i + 1, "type": t, "data": d} for i, (t, d) in enumerate(self.log)
        ]
        return {"latest": len(rows), "events": [r for r in rows if r["seq"] > after]}


def finish(api, arena):
    """The job ends with the match: done once the arena reports it over."""
    original = arena.status

    def status(name):
        out = original(name)
        if out["match"]["state"] != "running":
            api.job = {
                "id": "j1",
                "state": "succeeded",
                "result": {"kills": 4, "hits": 9, "waves_cleared": 2, "waves": 2},
            }
        return out

    arena.status = status


def test_a_hunt_heals_starts_the_match_sends_the_bot_and_samples_it():
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock)
    finish(api, arena)
    waves = [{"mobs": {"minecraft:pillager": 1}, "delay_ticks": 40}]
    row = ah.hunt(
        api, arena, "vault", waves, participants=["MCBot"], timeout=60, clock=clock
    )
    assert api.trials == [{"heal": True, "wear": True}]  # no kit: keeps what it has
    assert arena.started == [("vault", "waves", waves, ["MCBot"])]
    assert ("attack", {"arena": "vault"}) in api.calls
    assert row["outcome"] == "won" and row["reason"] == "cleared"
    assert row["waves_cleared"] == 2 and row["waves"] == 2
    assert row["min_health"] == 9.5
    assert row["kills"] == 4 and row["hits"] == 9
    assert row["job_state"] == "succeeded"
    assert [w["wave"] for w in row["wave_times"]] == [1, 2]
    assert row["samples"] and {"t", "wave", "alive", "killed", "health"} <= set(
        row["samples"][0]
    )
    assert arena.stopped == []  # the match ended by itself


def test_a_hunt_keeps_its_job_steps_and_sums_what_hurt_the_bot():
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock)
    api.log = [("job_step", {"id": "old", "step": {"hurt_by": {"x": 99.0}}})]  # before
    finish(api, arena)
    real = api.call

    def call(intent, args=None, key=None, source="mcp"):
        out = real(intent, args, key, source)
        if intent == "attack":  # the job's fights land after it is sent
            api.log += [
                ("job_step", {"id": "j1", "step": {
                    "weapon": "minecraft:bow", "target_type": "minecraft:pillager",
                    "hurt_by": {"minecraft:arrow": 4.0}}}),
                ("job_progress", {"id": "j1"}),
                ("job_step", {"id": "other", "step": {"hurt_by": {"y": 1.0}}}),
                ("job_step", {"id": "j1", "step": {
                    "weapon": "minecraft:trident", "target_type": "minecraft:vindicator",
                    "hurt_by": {"minecraft:arrow": 1.5, "minecraft:vindicator": 7.0},
                    "killed_by": "minecraft:vindicator"}}),
            ]  # fmt: skip
        return out

    api.call = call
    row = ah.hunt(
        api, arena, "vault", [], participants=["MCBot"], timeout=60, clock=clock
    )
    assert [s["weapon"] for s in row["steps"]] == ["minecraft:bow", "minecraft:trident"]
    assert row["hurt_by"] == {"minecraft:arrow": 5.5, "minecraft:vindicator": 7.0}
    assert row["killed_by"] == "minecraft:vindicator"
    assert row["picks"] == {
        "minecraft:pillager": {"minecraft:bow": 1},
        "minecraft:vindicator": {"minecraft:trident": 1},
    }


def test_summary_sums_damage_taken_across_runs():
    rows = [
        {"outcome": "won", "hurt_by": {"a": 2.0}},
        {"outcome": "lost", "hurt_by": {"a": 1.0, "b": 3.0}, "killed_by": "b"},
    ]
    s = ah.summary(rows)
    assert s["hurt_by"] == {"a": 3.0, "b": 3.0} and s["killed_by"] == {"b": 1}


def test_a_kit_is_handed_out_afresh_each_run():
    """A bot that died last run dropped its kit in the arena: each run starts from the
    same kit, worn, or the next run measures fists."""
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock)
    finish(api, arena)
    give = [{"item": "minecraft:iron_sword", "count": 1}]
    row = ah.hunt(
        api,
        arena,
        "vault",
        [],
        participants=["MCBot"],
        timeout=60,
        clock=clock,
        kit=give,
    )
    assert api.trials == [
        {"heal": True, "wear": True, "clear_inventory": True, "give": give}
    ]
    assert row["kit"] == ["minecraft:iron_sword"]


def test_a_hunt_past_its_time_stops_the_bot_and_the_match():
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock, end=None)
    row = ah.hunt(
        api, arena, "vault", [], participants=["MCBot"], timeout=10, clock=clock
    )
    assert row["outcome"] == "timeout"
    assert ("stop", {}) in api.calls and arena.stopped == ["vault"]


def test_a_refused_attack_stops_the_match_it_started():
    clock = Clock()
    api, arena = FakeApi(clock, refuse="I am not in an arena"), FakeArena(clock)
    row = ah.hunt(
        api, arena, "vault", [], participants=["MCBot"], timeout=10, clock=clock
    )
    assert row["outcome"] == "refused" and row["error"] == "I am not in an arena"
    assert arena.stopped == ["vault"]


def test_a_match_that_will_not_start_is_reported_not_raised():
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock, fail_start="not_enough_pads")
    row = ah.hunt(
        api, arena, "vault", [], participants=["MCBot"], timeout=10, clock=clock
    )
    assert row["outcome"] == "not_started" and row["error"] == "not_enough_pads: no"
    assert not any(intent == "attack" for intent, _ in api.calls)


def test_a_lost_match_counts_as_a_death():
    clock = Clock()
    api, arena = FakeApi(clock, health=(20.0, 4.0, 0.0)), FakeArena(clock, end="lost")
    finish(api, arena)
    row = ah.hunt(
        api, arena, "vault", [], participants=["MCBot"], timeout=60, clock=clock
    )
    assert row["outcome"] == "lost" and row["min_health"] == 0.0


def test_summary_counts_wins_and_spreads_the_times():
    rows = [
        {
            "outcome": "won",
            "seconds": 30.0,
            "min_health": 8.0,
            "waves_cleared": 2,
            "waves": 2,
        },
        {
            "outcome": "won",
            "seconds": 50.0,
            "min_health": 12.0,
            "waves_cleared": 2,
            "waves": 2,
        },
        {
            "outcome": "lost",
            "seconds": 20.0,
            "min_health": 0.0,
            "waves_cleared": 1,
            "waves": 2,
        },
    ]
    s = ah.summary(rows)
    assert s["runs"] == 3 and s["won"] == 2 and s["win_rate"] == pytest.approx(2 / 3)
    assert s["outcomes"] == {"won": 2, "lost": 1}
    assert s["median_won_seconds"] == 40.0 and s["min_health"] == 0.0
    assert s["mean_waves_cleared"] == pytest.approx(5 / 3)
    assert ah.summary([])["runs"] == 0


def test_a_series_waits_for_the_bot_and_writes_every_run(tmp_path):
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock)
    finish(api, arena)
    lines = []
    path = ah.run_series(
        api,
        arena,
        "vault",
        [{"mobs": {"minecraft:pillager": 1}, "delay_ticks": 1}],
        repeat=2,
        participants=["MCBot"],
        timeout=60,
        out_dir=tmp_path,
        echo=lines.append,
        clock=clock,
        label="pairs",
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["arena"] == "vault" and record["label"] == "pairs"
    assert len(record["runs"]) == 2 and record["summary"]["runs"] == 2
    assert all("samples" in r for r in record["runs"])
    assert any("won" in line for line in lines)


def test_a_series_stops_when_the_bot_never_comes_back_ready(tmp_path):
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock)
    real = api.call

    def call(intent, args=None, key=None, source="mcp"):
        out = real(intent, args, key, source)
        if intent == "status":
            out["result"]["body_state"] = "dead"
        return out

    api.call = call
    slept, lines = [], []

    def sleep(s):
        slept.append(s)
        clock.now += s

    path = ah.run_series(
        api,
        arena,
        "vault",
        [],
        repeat=3,
        participants=["MCBot"],
        timeout=60,
        out_dir=tmp_path,
        echo=lines.append,
        clock=clock,
        sleep=sleep,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["runs"] == [] and arena.started == []
    assert slept and sum(slept) >= ah.READY_WAIT_S
    assert "not ready" in lines[0]


def test_a_wave_is_written_as_type_count_plus_type():
    assert ah.parse_wave("pillager*2+vindicator", 40) == {
        "mobs": {"minecraft:pillager": 2, "minecraft:vindicator": 1},
        "delay_ticks": 40,
    }
    assert ah.parse_wave("minecraft:witch+witch", 5)["mobs"] == {"minecraft:witch": 2}
    for bad in ("", "pillager*x", "*2", "zombie*0"):
        with pytest.raises(ValueError):
            ah.parse_wave(bad, 1)


def test_wave_times_mark_when_each_wave_is_cleared():
    samples = [
        {"t": 1.0, "wave": 0},
        {"t": 2.0, "wave": 0},
        {"t": 3.5, "wave": 1},
        {"t": 9.0, "wave": 3},
    ]
    assert ah.wave_times(samples) == [{"wave": 1, "t": 3.5}, {"wave": 3, "t": 9.0}]
