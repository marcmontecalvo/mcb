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

    def start(self, name, mode, *, waves=None, participants=None, teams=None, exit=None):  # noqa: A002
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
        self.held = {g["item"]: g["count"] for g in spec.get("give", ())}
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
                    "inventory": dict(getattr(self, "held", {})),
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
    assert row["bot_kills"] == 4 and row["hits"] == 9
    assert row["job_state"] == "succeeded"
    assert [w["wave"] for w in row["wave_times"]] == [1, 2]
    assert row["samples"] and {"t", "wave", "alive", "killed", "health"} <= set(
        row["samples"][0]
    )
    assert arena.stopped == []  # the match ended by itself


def test_a_job_that_fails_mid_match_stops_the_match_for_the_next_run():
    # Vault raid 20261124T043026Z: the job failed in wave 3, the match ran on, and
    # the next run could not start (match_running).
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock, end=None)
    api.job = {"id": "j1", "state": "failed", "error": "I may not attack vault"}
    row = ah.hunt(api, arena, "vault", [], participants=["MCBot"], timeout=60,
                  clock=clock)  # fmt: skip
    assert arena.stopped == ["vault"]
    assert row["outcome"] == "abandoned" and row["error"] == "I may not attack vault"


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


def test_a_hunt_counts_the_food_and_potions_used_by_purpose():
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock)
    finish(api, arena)
    real = api.call
    heal = {"action": "throw", "purpose": "heal", "potion": "minecraft:strong_healing",
            "ok": True}  # fmt: skip

    def call(intent, args=None, key=None, source="mcp"):
        out = real(intent, args, key, source)
        if intent == "attack":
            api.log += [
                ("job_step", {"id": "j1", "step": heal}),
                ("job_step", {"id": "j1", "step": {**heal, "ok": False}}),
                ("job_step", {"id": "j1", "step": {
                    "action": "eat", "purpose": "heal",
                    "item": "minecraft:golden_apple", "ok": True}}),
            ]  # fmt: skip
        return out

    api.call = call
    row = ah.hunt(
        api, arena, "vault", [], participants=["MCBot"], timeout=60, clock=clock
    )
    assert row["uses"] == {
        "heal": {"minecraft:strong_healing": 1, "minecraft:golden_apple": 1}
    }  # a refused use is not counted


def test_summary_sums_damage_taken_and_uses_across_runs():
    rows = [
        {"outcome": "won", "hurt_by": {"a": 2.0}, "uses": {"heal": {"p": 1}}},
        {"outcome": "lost", "hurt_by": {"a": 1.0, "b": 3.0}, "killed_by": "b",
         "uses": {"heal": {"p": 2}, "harm": {"q": 1}}},
    ]  # fmt: skip
    s = ah.summary(rows)
    assert s["hurt_by"] == {"a": 3.0, "b": 3.0} and s["killed_by"] == {"b": 1}
    assert s["uses"] == {"heal": {"p": 3}, "harm": {"q": 1}}


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


def test_the_match_waits_until_the_bot_holds_the_kit_it_was_handed():
    """Rung 2 run 3 fought bare-handed: the first look after the trial op still saw
    the empty inventory of the death before. The match starts once the status shows
    the kit."""
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock)
    finish(api, arena)
    sword = "minecraft:iron_sword"
    real, looks = api.call, []

    def call(intent, args=None, key=None, source="mcp"):
        out = real(intent, args, key, source)
        if intent == "status":
            looks.append(1)
            out["result"]["inventory"] = {sword: 1} if len(looks) >= 3 else {}
        return out

    api.call = call
    slept = []
    row = ah.hunt(api, arena, "vault", [], participants=["MCBot"], timeout=60,
                  clock=clock, kit=[{"item": sword, "count": 1}],
                  sleep=lambda s: slept.append(s))  # fmt: skip
    assert row["outcome"] == "won" and len(slept) == 2
    assert arena.started  # only after the third look held the sword


def test_a_kit_that_never_shows_is_not_started():
    clock = Clock()
    api, arena = FakeApi(clock), FakeArena(clock)

    def sleep(s):
        clock.now += s

    api.trial = lambda spec: {"ok": True, "result": {}}  # given, never seen held
    row = ah.hunt(api, arena, "vault", [], participants=["MCBot"], timeout=60,
                  clock=clock, kit=[{"item": "minecraft:bow", "count": 1}],
                  sleep=sleep)  # fmt: skip
    assert row["outcome"] == "not_started" and "kit" in row["error"]
    assert not arena.started


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


# ---- team matches, humans and the arena server ------------------------------


class FakeContest:
    """A teams (or race) match that ends after ``reads`` status reads with ``winner``."""

    def __init__(self, clock, teams, reads=3, winner="red", mode="teams"):
        self.clock, self.teams, self.left, self.winner = clock, teams, reads, winner
        self.mode = mode
        self.started, self.stopped, self.kits, self.ops = [], [], [], []
        self.refuse_op = set()

    def start(self, name, mode, *, waves=None, participants=None, teams=None, exit=None):  # noqa: A002
        self.started.append({"name": name, "mode": mode, "waves": waves,
                             "teams": teams, "exit": exit})  # fmt: skip
        return {"name": name, "mode": mode, "teams": teams}

    def status(self, name):
        self.left -= 1
        over = self.left <= 0
        people = [{"name": n, "team": t, "alive": True,
                   "mob_kills": 2 if t == "red" else 1,
                   "player_kills": 1 if n == "MCBot" else 0}
                  for t, names in self.teams.items() for n in names]  # fmt: skip
        match = {"mode": self.mode, "state": "won" if over else "running",
                 "participants": people,
                 "team_kills": {t: 3 if t == "red" else 1 for t in self.teams}}  # fmt: skip
        if over:
            match.update(winner=self.winner, reason="team_standing")
        return {"name": name, "match": match, "match_state": match["state"]}

    def stop(self, name):
        self.stopped.append(name)
        return {"name": name}

    def kit(self, players, give):
        self.kits.append((list(players), give))
        return {"players": list(players)}

    def op(self, player, *, op=True):
        if player in self.refuse_op:
            raise ah.ArenaError("player_offline", player)
        self.ops.append((player, op))
        return {"player": player, "op": op, "changed": True}


def ended(api, contest):
    """The bot's job ends with the match."""
    original = contest.status

    def status(name):
        out = original(name)
        if out["match"]["state"] != "running":
            api.job = {"id": "j1", "state": "succeeded", "result": {"kills": 1}}
        return out

    contest.status = status


def test_a_team_match_starts_its_teams_dresses_everyone_and_reports_the_winner():
    clock = Clock()
    teams = {"red": ["MCBot", "Ann"], "blue": ["Bob"]}
    api, contest = FakeApi(clock), FakeContest(clock, teams)
    ended(api, contest)
    give = [{"item": "minecraft:iron_sword", "count": 1}]
    row = ah.hunt(api, contest, "duel", [], mode="teams", teams=teams,
                  exit=(0, 102, 0), timeout=60, clock=clock, kit=give)  # fmt: skip
    assert contest.started == [{"name": "duel", "mode": "teams", "waves": None,
                                "teams": teams, "exit": (0, 102, 0)}]  # fmt: skip
    assert contest.kits == [(["Ann", "Bob"], give)]  # the humans get the bot's kit
    assert api.trials[0]["give"] == give
    assert ("attack", {"arena": "duel"}) in api.calls
    assert row["mode"] == "teams" and row["outcome"] == "won"
    assert row["winner"] == "red" and row["bot_team"] == "red" and row["bot_won"]
    assert row["kills"]["MCBot"] == {"team": "red", "mob_kills": 2, "player_kills": 1}
    assert row["team_kills"] == {"red": 3, "blue": 1}
    assert row["participants"] == ["MCBot", "Ann", "Bob"]


def test_a_match_of_humans_only_is_watched_to_its_end_without_the_bot():
    clock = Clock()
    teams = {"red": ["Ann"], "blue": ["Bob"]}
    api, contest = FakeApi(clock), FakeContest(clock, teams, reads=4, winner="blue")
    slept = []

    def sleep(s):
        slept.append(s)
        clock.now += s

    row = ah.hunt(api, contest, "duel", [], mode="teams", teams=teams, timeout=60,
                  clock=clock, sleep=sleep)  # fmt: skip
    assert api.trials == [] and not [c for c in api.calls if c[0] == "attack"]
    assert contest.kits == [(["Ann", "Bob"], None)]  # healed, keeping their gear
    assert row["outcome"] == "won" and row["winner"] == "blue"
    assert row["bot_team"] is None and "bot_won" not in row
    assert len(row["samples"]) == 4 and slept
    assert row["samples"][0]["health"] is None


def test_a_match_without_the_bot_needs_no_companion_api():
    clock = Clock()
    teams = {"red": ["Ann"], "blue": ["Bob"]}
    contest = FakeContest(clock, teams, reads=2)

    def sleep(s):
        clock.now += s

    row = ah.hunt(None, contest, "duel", [], mode="teams", teams=teams, timeout=60,
                  clock=clock, sleep=sleep)  # fmt: skip
    assert row["outcome"] == "won"


def test_a_match_of_humans_only_past_its_time_is_stopped():
    clock = Clock()
    teams = {"red": ["Ann"], "blue": ["Bob"]}
    api, contest = FakeApi(clock), FakeContest(clock, teams, reads=10_000)

    def sleep(s):
        clock.now += s

    row = ah.hunt(api, contest, "duel", [], mode="teams", teams=teams, timeout=5,
                  clock=clock, sleep=sleep)  # fmt: skip
    assert row["outcome"] == "timeout" and contest.stopped == ["duel"]


def test_a_race_takes_its_waves_and_counts_the_bot_on_the_losing_side():
    clock = Clock()
    teams = {"red": ["Ann"], "blue": ["MCBot"]}
    api, contest = FakeApi(clock), FakeContest(clock, teams, mode="race")
    ended(api, contest)
    waves = [{"mobs": {"minecraft:zombie": 4}, "delay_ticks": 0}]
    row = ah.hunt(api, contest, "hall", waves, mode="race", teams=teams, timeout=60,
                  clock=clock)  # fmt: skip
    assert contest.started[0]["waves"] == waves and contest.started[0]["mode"] == "race"
    assert row["bot_team"] == "blue" and row["bot_won"] is False


def test_a_series_sets_each_players_operator_status_once(tmp_path):
    clock = Clock()
    teams = {"red": ["MCBot", "Ann"], "blue": ["Bob", "Cat"]}
    api, contest = FakeApi(clock), FakeContest(clock, teams)
    contest.refuse_op = {"Cat"}
    ended(api, contest)
    lines = []
    path = ah.run_series(api, contest, "duel", [], mode="teams", teams=teams,
                         ops={"Ann": True, "Bob": False, "Cat": True, "MCBot": True},
                         repeat=1, timeout=60, out_dir=tmp_path, echo=lines.append,
                         clock=clock)  # fmt: skip
    assert contest.ops == [("Ann", True), ("Bob", False)]  # never the bot
    assert any("Cat" in line and "player_offline" in line for line in lines)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["mode"] == "teams" and record["teams"] == teams
    assert record["summary"]["winners"] == {"red": 1}
    assert any("red won" in line for line in lines)


def test_summary_counts_the_winning_teams_and_the_bots_wins():
    rows = [
        {"outcome": "won", "winner": "red", "bot_won": True},
        {"outcome": "won", "winner": "blue", "bot_won": False},
        {"outcome": "lost", "reason": "draw"},
    ]
    s = ah.summary(rows)
    assert s["winners"] == {"red": 1, "blue": 1}
    assert s["bot_wins"] == 1 and s["won"] == 2
