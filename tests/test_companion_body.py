import math
from dataclasses import replace

import pytest

from mclab import companion_body as cb
from mclab.companion_body import CompanionBody

FULL = [0, 0, 0, 1, 1, 1]


class FakeMemory:
    def __init__(self):
        self.cells = {}
        for x in range(-6, 7):
            for z in range(-6, 7):
                self.cells[(x, -1, z)] = {
                    "block": "minecraft:stone",
                    "collision_boxes": [FULL],
                }
                for y in range(0, 4):
                    self.cells[(x, y, z)] = {
                        "block": "minecraft:air",
                        "collision_boxes": [],
                    }
        self.cells[(2, 0, 0)] = {
            "block": "minecraft:oak_stairs",
            "properties": {"facing": "north", "half": "bottom"},
            "collision_boxes": [FULL],
        }
        self.ingested = []
        self.recent_ticks = {}

    def ingest(self, obs):
        self.ingested.append(obs)

    def live_observed_tick(self, pos):
        return self.recent_ticks.get(pos)


class FakeBridge:
    """Answers every op with the event kind set in ``reply`` (default: done)."""

    def __init__(self):
        self.events, self.sent, self.reply = [], [], {}
        self.sequence = 0
        self.tick = None  # server tick stamped on the reply event

    def send(self, op, **fields):
        self.sequence += 1
        self.sent.append((op, fields))
        kind, data = self.reply.get(op, ("done", {}))
        if kind != "silent":
            suffix = {"done": f"{op}_done", "failed": f"{op}_failed"}.get(kind, kind)
            self.events.append(
                {"type": suffix, "data": {"request_sequence": self.sequence, **data}}
                | ({} if self.tick is None else {"tick": self.tick})
            )
        return {"sequence": self.sequence}

    def wait(self, predicate, timeout=10, **_):
        if not predicate(None):
            raise TimeoutError("fake wait")


def body(monkeypatch=None):
    bridge, memory = FakeBridge(), FakeMemory()
    b = CompanionBody(bridge, memory)
    b._obs = {
        "tick": 100,
        "body": {"position": [0.5, 0.0, 0.5], "food": 17, "inventory": []},
    }
    return b, bridge, memory


def test_op_maps_done_failed_rejected_and_timeout():
    b, bridge, _ = body()
    inv = [{"slot": 0, "item": "minecraft:dirt", "count": 3}]
    bridge.reply["place_block"] = (
        "done",
        {"placed": "minecraft:dirt", "inventory": inv},
    )
    assert b.place_block((1, 0, 1), "minecraft:dirt", "minecraft:dirt").ok
    assert b._inv == {"minecraft:dirt": 3}  # op result (inventory() re-observes)

    bridge.reply["use_block"] = ("failed", {"reason": "no_effect"})
    res = b.use_block((1, 0, 1), "minecraft:wooden_hoe")
    assert (res.ok, res.reason) == (False, "no_effect")

    bridge.reply["break_block"] = ("rejected", {"reason": "bad_pos"})
    assert b.break_block((1, 0, 1)).reason == "rejected:bad_pos"

    bridge.reply["craft"] = ("silent", {})
    assert b.craft("minecraft:stick", 1).reason == "timeout"
    assert bridge.sent[0] == (
        "place_block",
        {"pos": [1, 0, 1], "state": "minecraft:dirt", "item": "minecraft:dirt"},
    )


def test_shared_body_walk_permissions_keep_doors_and_climbing_without_edits():
    b, _, _ = body()
    assert b.movement_policy == cb.NATURAL_MOVEMENT
    assert b.terrain_policy.use_doors
    assert b.movement_policy.allow_climb
    assert not b.terrain_policy.allow_break and not b.terrain_policy.allow_place
    assert b.dig_to((0, -1, 0)) is False
    b.terrain_policy = cb.DIG_TERRAIN
    assert b.policy_snapshot()["terrain"]["allow_break"]
    assert b.terrain_policy.use_doors


def test_navigation_policies_are_deduplicated_when_permissions_change():
    b, _, _ = body()
    assert b.policy_id() == b.policy_id() == 0
    b.terrain_policy = cb.DIG_TERRAIN
    assert b.policy_id() == 1
    b.terrain_policy = cb.WALK_TERRAIN
    assert b.policy_id() == 0
    assert len(b.policies) == 2


def test_world_is_delivered_cells_only_with_a_short_overlay():
    b, bridge, _ = body()
    assert b.world(2, 0, 0) == "minecraft:oak_stairs[facing=north,half=bottom]"
    assert b.world(50, 0, 50) is None  # never delivered: unknown, not air
    bridge.reply["break_block"] = ("done", {})
    assert b.break_block((2, 0, 0)).ok
    assert b.world(2, 0, 0) == "minecraft:air"
    b._obs["tick"] += cb.OVERLAY_TICKS + 1
    assert b.world(2, 0, 0).startswith("minecraft:oak_stairs")  # the map wins later


def test_a_newer_sensor_sample_replaces_the_overlay(monkeypatch):
    # run 20261112T032720895516Z: a door placed closed, opened by the motor,
    # still read closed from the overlay, so the door repair did nothing
    b, bridge, memory = body()
    bridge.reply["break_block"] = ("done", {})
    assert b.break_block((2, 0, 0)).ok
    assert b.world(2, 0, 0) == "minecraft:air"
    memory.recent_ticks[(2, 0, 0)] = 105  # sampled after the edit at tick 100
    monkeypatch.setattr(cb, "policy_state", lambda _: {"tick": 105, "body": {}})
    b.observe()
    assert b.world(2, 0, 0).startswith("minecraft:oak_stairs")


def test_a_sample_from_before_the_edit_keeps_the_overlay(monkeypatch):
    # run 20261112T134722151875Z: a tulip broken and dirt placed 2 ticks later, both
    # after the last observation (tick 100). The sensor's sample of the break (air)
    # was newer than that observation, dropped the dirt overlay, and site prep
    # ended "unfinished site edit" on a filled cell.
    b, bridge, memory = body()
    bridge.tick = 104  # the server applied the edit at tick 104
    bridge.reply["place_block"] = ("done", {"placed": "minecraft:dirt"})
    assert b.place_block((2, 0, 0), "minecraft:dirt", "minecraft:dirt").ok
    memory.recent_ticks[(2, 0, 0)] = 102  # sampled before the edit
    monkeypatch.setattr(cb, "policy_state", lambda _: {"tick": 103, "body": {}})
    b.observe()
    assert b.world(2, 0, 0) == "minecraft:dirt"


@pytest.mark.parametrize("operation", ["place_block", "break_block", "use_block"])
def test_edit_event_tick_controls_overlay_and_expiry(monkeypatch, operation):
    b, bridge, memory = body()
    bridge.tick = 104
    bridge.reply[operation] = (
        "done", {"placed": "minecraft:dirt", "after": "minecraft:dirt"}
    )
    if operation == "place_block":
        b.place_block((2, 0, 0), "minecraft:dirt", "minecraft:dirt")
    elif operation == "break_block":
        b.break_block((2, 0, 0))
    else:
        b.use_block((2, 0, 0))
    expected = "minecraft:air" if operation == "break_block" else "minecraft:dirt"
    memory.recent_ticks[(2, 0, 0)] = 104
    monkeypatch.setattr(cb, "policy_state", lambda _: {"tick": 104, "body": {}})
    b.observe()
    assert b.world(2, 0, 0) == expected  # same tick is not known to be later
    b._obs["tick"] = 104 + cb.OVERLAY_TICKS
    assert b.world(2, 0, 0) == expected
    b._obs["tick"] += 1
    assert b.world(2, 0, 0).startswith("minecraft:oak_stairs")


def test_successive_edits_keep_latest_until_newer_sample(monkeypatch):
    b, bridge, memory = body()
    bridge.tick = 102
    assert b.break_block((2, 0, 0)).ok
    bridge.tick = 104
    bridge.reply["place_block"] = ("done", {"placed": "minecraft:dirt"})
    assert b.place_block((2, 0, 0), "minecraft:dirt", "minecraft:dirt").ok
    memory.recent_ticks[(2, 0, 0)] = 103
    monkeypatch.setattr(cb, "policy_state", lambda _: {"tick": 105, "body": {}})
    b.observe()
    assert b.world(2, 0, 0) == "minecraft:dirt"
    memory.recent_ticks[(2, 0, 0)] = 105
    b.observe()
    assert b.world(2, 0, 0).startswith("minecraft:oak_stairs")


@pytest.mark.parametrize("encoding", ["sparse", "uniform", "dense"])
def test_overlay_uses_actual_compact_sample_tick(tmp_path, monkeypatch, encoding):
    from mclab.awareness import Awareness
    from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory
    from mclab.terrain_transport import expand_terrain

    awareness = Awareness.resolve("sections")
    identity = MapIdentity("w", "g", "minecraft:overworld", awareness)
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity), "session")
    b, bridge, _ = body()
    b.memory = memory
    bridge.tick = 104
    bridge.reply["place_block"] = ("done", {"placed": "minecraft:dirt"})
    assert b.place_block((2, 0, 0), "minecraft:dirt", "minecraft:dirt").ok
    state = {"block": "minecraft:air", "fluid": "minecraft:empty",
             "replaceable": True, "properties": {}, "collision_boxes": []}

    def observation(captured, sample):
        terrain = {
            "schema_version": 1, "encoding": "sparse_section_palette",
            "unknown_is_air": False, "sample_tick": sample, "palette": [state],
            "sections": [], "known_cells": 0, "bulk_cells": 0,
            "dense_sections": [], "uniform_sections": [], "encoding_ms": 0.1,
        }
        if encoding == "sparse":
            terrain["sections"] = [
                {"section": [0, 0, 0], "cells": [[512, 0, captured]]}
            ]
            terrain["known_cells"] = 1
        else:
            entry = {"section": [0, 0, 0], "tick": captured}
            entry.update(
                {"state": 0} if encoding == "uniform" else {"states": [0] * 4096}
            )
            terrain[f"{encoding}_sections"] = [entry]
            terrain["bulk_cells"] = 4096
        return expand_terrain({
            "tick": sample + 3, "sensor_profile": "awareness-v1", "body": {},
            "awareness": {"config": awareness.payload(), "revision": "r",
                          "coverage": {"sample_tick": sample}},
            "terrain_sections": terrain,
        })

    try:
        monkeypatch.setattr(cb, "policy_state", lambda _: observation(102, 105))
        b.observe()
        assert b.world(2, 0, 0) == "minecraft:dirt"
        monkeypatch.setattr(cb, "policy_state", lambda _: observation(106, 106))
        b.observe()
        assert b.world(2, 0, 0) == "minecraft:air"
    finally:
        memory.close()


def test_stances_exclude_avoided_cells_and_are_reached_by_interacts():
    b, _, _ = body()
    target = (3, 0, 3)
    found = b.stances(target, 4.5)
    assert found and (0, 0, 0) in found
    again = b.stances(target, 4.5, avoid={found[0]})
    assert found[0] not in again


def test_goto_stance_skips_navigation_when_already_in_reach(monkeypatch):
    b, _, _ = body()
    monkeypatch.setattr(
        b, "_navigate", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    assert b.goto_stance((2, 0, 2), 4.5) is True


def test_goto_stance_with_avoid_walks_to_another_known_stance(monkeypatch):
    b, _, _ = body()
    goals = []
    monkeypatch.setattr(b, "_navigate", lambda goal, **k: goals.append(goal) or True)
    assert b.goto_stance((2, 0, 2), 4.5, avoid={(0, 0, 0)})
    assert goals and goals[0] != (0, 0, 0)


def test_goto_stance_replans_when_the_planned_stance_is_avoided(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]  # far: not in reach
    calls = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        calls.append((goal, reach))
        b._obs["body"]["position"] = [0.5, 0.0, 0.5]  # lands on a stance we avoid
        return True

    monkeypatch.setattr(b, "_navigate", navigate)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [])
    assert b.goto_stance((200, 0, 200), 4.5, avoid=set()) is True
    assert calls[0] == ((200, 0, 200), 4.5)


def test_goto_controller_stops_on_arrival_deadline_and_unreachable():
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.unreachable_since, ctl.mode = 10**12, None, "following"
    assert ctl.stop_reason({"tick": 1}) is None
    ctl.mode = "arrived"
    assert ctl.stop_reason({"tick": 2}) == "arrived"
    ctl.mode = "unreachable"
    assert ctl.stop_reason({"tick": 3}) is None
    assert ctl.stop_reason({"tick": 3 + cb.UNREACHABLE_TICKS}) == "unreachable"
    ctl.deadline = 0
    assert ctl.stop_reason({"tick": 5}) == "deadline"


def _idle_ctl(monkeypatch, now, mode="following"):
    monkeypatch.setattr(cb.time, "monotonic", lambda: now[0])
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.mode, ctl.last_result = 10**9, mode, None
    ctl.unreachable_since = ctl.searching_since = None
    ctl.guard = cb.StuckGuard()
    ctl.idle_state = {"anchor": None, "since": 0.0}
    return ctl


def test_wall_clock_idle_only_reports_and_never_ends_the_goto(monkeypatch, capsys):
    now = [0.0]
    ctl = _idle_ctl(monkeypatch, now)
    obs = {"tick": 1, "body": {"position": [0.5, 62, 0.5]}}
    assert ctl.stop_reason(obs) is None
    now[0] = 6.0
    assert ctl.stop_reason(obs) is None  # was "stuck" at 5 s of wall time
    assert "STALL 6.0s navigation idle" in capsys.readouterr().out


def test_tick_guard_still_ends_a_followed_route_that_never_moves(monkeypatch):
    ctl = _idle_ctl(monkeypatch, [0.0])
    obs = {"tick": 1, "body": {"position": [0.5, 62, 0.5]}}
    assert ctl.stop_reason(obs) is None
    obs["tick"] = 1 + cb.STUCK_TICKS
    assert ctl.stop_reason(obs) == "stuck"


def test_ticks_during_a_plan_request_or_an_edit_step_are_not_idle(monkeypatch):
    ctl = _idle_ctl(monkeypatch, [0.0], mode="terrain_planning")
    obs = {"tick": 1, "body": {"position": [0.5, 62, 0.5]}}
    for tick in (1, 1 + cb.STUCK_TICKS, 1 + 3 * cb.STUCK_TICKS):
        obs["tick"] = tick
        assert ctl.stop_reason(obs) is None
    ctl.mode = "following"
    obs["step_follower"] = {"state": "following", "issued": True}
    for tick in (1000, 1000 + 3 * cb.STUCK_TICKS):
        obs["tick"] = tick
        assert ctl.stop_reason(obs) is None


def test_flip_flop_guard_is_gone():
    assert not hasattr(cb, "FLIP_S") and not hasattr(cb, "FLIP_GAIN")


def test_probe_follows_an_incomplete_plan_whose_first_step_is_a_dig():
    from mclab.route_types import RouteResult

    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.probe, ctl.no_drive, ctl.walk_partial = True, True, True
    ctl.deadline, ctl.walk_wall = 10**12, 0.0
    ctl.mode = "terrain_editing"
    ctl.last_result = RouteResult(
        "incomplete", ((0, 0, 0), (1, 0, 0), (2, 0, 0)), 1, 4000, "budget"
    )
    assert ctl._probe_verdict() is None and ctl.probe is False  # walked on
    ctl.probe, ctl.mode = True, "terrain_incomplete"
    ctl.last_result = RouteResult("unreachable", (), 1, 4000, "done")
    assert ctl._probe_verdict() == "unreachable"


def test_successful_block_edit_resets_shared_navigation_idle_clock(monkeypatch):
    monkeypatch.setattr(cb.time, "monotonic", lambda: 12.0)
    b, bridge, _ = body()
    b._navigation_idle = {"anchor": (0.5, 0, 0.5), "since": 0.0}
    bridge.reply["break_block"] = ("done", {})
    assert b.break_block((1, 0, 0)).ok
    assert b._navigation_idle["since"] == 12.0


def test_drop_pickup_does_not_snap_a_nearby_item_onto_a_tree_canopy(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [64.7, 72.0, -67.5]
    monkeypatch.setattr(b, "_stand_y", lambda *args: 76)
    monkeypatch.setattr(
        b, "_navigate", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    assert not b.walk_to_drop((63, 73, -66), 0.8)


def test_drop_pickup_still_walks_to_a_nearby_ground_stance(monkeypatch):
    b, _, _ = body()
    monkeypatch.setattr(b, "_stand_y", lambda *args: 1)
    goals = []
    monkeypatch.setattr(b, "_navigate", lambda goal, **k: goals.append(goal) or True)
    assert b.walk_to_drop((2, 1, 0), 0.8)
    assert goals == [(2, 1, 0)]


def test_exhausted_plan_without_a_move_tries_another_goal_instead_of_replanning():
    from mclab.route_types import RouteResult

    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.mode = 10**12, "incomplete"
    ctl.searching_since, ctl.unreachable_since = None, None
    ctl.last_result = RouteResult("incomplete", ((60, 71, -73),), 1, 4000, "budget")
    assert ctl.stop_reason({"tick": 1}) == "incomplete"
    ctl.last_result = RouteResult(
        "incomplete", ((60, 71, -73), (60, 71, -74)), 1, 4000, "budget"
    )
    assert ctl.stop_reason({"tick": 2}) is None


def test_a_goal_beyond_the_sensed_terrain_is_reached_through_hops(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [0.5, 1.0, 0.5]
    far = (101, 1, 0)
    monkeypatch.setattr(b, "_cells_now", lambda: {})
    monkeypatch.setattr(cb, "standable", lambda cells, node: True)
    original, goals = cb.CompanionBody._navigate, []

    def navigate(goal, **kw):
        goals.append(goal)
        here = b.position()
        if math.hypot(goal[0] - here[0], goal[2] - here[2]) > cb.SENSED_RADIUS:
            return original(b, goal, **kw)
        b._obs["body"]["position"] = [goal[0] + 0.5, 1.0, goal[2] + 0.5]
        return True

    monkeypatch.setattr(b, "_navigate", navigate)
    assert navigate(far, reach=None, radius=1.0)
    assert goals[-1] == far and len(goals) >= 4  # far, hop, far, hop, ..., far
    assert all(0 < g[0] <= cb.SENSED_RADIUS * 3 for g in goals[1:-1])
    assert b._hop_toward((120, 1, 0)) is None  # inside the sensed radius


def test_goto_stance_walks_known_stances_before_any_interaction_search(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]  # far: not in reach
    calls = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        calls.append((goal, reach))
        return False  # the walk fails

    monkeypatch.setattr(b, "_navigate", navigate)
    assert b.goto_stance((2, 0, 2), 4.5) is False
    assert 0 < len(calls) <= cb.MAX_CANDIDATES
    assert all(reach is None for _, reach in calls)  # body planner, stand-on goals


def test_goto_stance_skips_candidates_the_planner_cannot_route(monkeypatch):
    # roadmap item 1 (Nov 6): a stance across known water is unroutable, but testing
    # that by walking it burned the StuckGuard's 10 s - 12 of them ended the job at
    # 0 tasks (20261106T170919632775Z). The probe asks the planner first, parked.
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]  # far: not in reach
    seen = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        seen.append((goal, probe))
        return goal != (2, 0, 0)  # no route to that first stance

    monkeypatch.setattr(b, "_navigate", navigate)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(2, 0, 0), (3, 0, 1)])
    assert b.goto_stance((2, 0, 2), 4.5) is True
    # Every walk starts as a parked probe; a found route is walked by that same
    # controller, never planned a second time (run 20261111T135214868983Z).
    assert seen == [((2, 0, 0), True), ((3, 0, 1), True)]


def test_roof_support_top_is_not_visible_from_recorded_floor_height():
    b, _, memory = body()
    b._obs["body"]["position"] = [-19.5, 65.0, 59.31]
    target, support = (-18, 67, 58), (-18, 66, 58)
    memory.cells[support] = {"block": "minecraft:oak_planks", "collision_boxes": [FULL]}
    assert not b.can_place_here(target, 4.5)


def test_walk_only_reuses_failed_stance_probe_until_geometry_changes(monkeypatch):
    b, _, _ = body()
    b.walk_only = True
    b._obs["body"]["position"] = [20.5, 0, 20.5]
    goals = [(2, 0, 0), (3, 0, 1)]
    monkeypatch.setattr(b, "stances", lambda *a, **k: goals)
    monkeypatch.setattr(b, "_walkable", lambda *a: set(goals))
    seen = []
    monkeypatch.setattr(b, "_navigate", lambda goal, **kw: seen.append(goal) or False)
    assert not b.goto_stance((2, 0, 2), 4.5)
    assert not b.goto_stance((3, 0, 2), 4.5)
    assert seen == goals
    assert not b.goto_stance((3, 0, 2), 4.5)
    assert seen == goals
    b._remember((0, 0, 0), "minecraft:stone", 100)
    assert not b.goto_stance((3, 0, 2), 4.5)
    assert seen == [*goals, *goals]


def test_goto_stance_far_target_uses_the_edit_search_after_probes_park(monkeypatch):
    # Iron run 20261112T233253015689Z: deep in its own dug cave, every known stance
    # for the crafting table probed "unreachable" (walk-only partial routes); the
    # edit-allowing interaction search was gated on an empty stance list, never ran,
    # and three furnace crafts died on no_stance. One parked probe set now falls
    # through to a real (editing) route to the interaction goal.
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]
    calls = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        calls.append((goal, probe))
        return False

    monkeypatch.setattr(b, "_navigate", navigate)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(200, 0, 196), (201, 0, 196)])
    assert b.goto_stance((200, 0, 200), 4.5) is False
    assert calls[-1] == ((200, 0, 200), False)  # the fallback walk, edits allowed


def test_goto_stance_keeps_probing_near_targets_without_the_edit_search(monkeypatch):
    # _worth_searching: inside the sensed map the interaction search only floods
    # the known graph (ravine run 20261101T203526520132Z) - a failed probe set near
    # home must NOT trigger it.
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]
    calls = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        calls.append((goal, probe))
        return False

    monkeypatch.setattr(b, "_navigate", navigate)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(22, 0, 22)])
    assert b.goto_stance((23, 0, 23), 4.5) is False
    assert all(goal != (23, 0, 23) for goal, _ in calls)  # no fallback walk


def test_build_runner_tries_second_walk_only_stance_when_first_fails(monkeypatch):
    from test_build_plan import BLOCKS

    from mclab import build_plan as bp
    from mclab import build_runner as br

    b, _, _ = body()
    b.walk_only = True
    b._obs["body"]["position"] = [20.5, 0, 20.5]
    goals = [(2, 0, 0), (3, 0, 1)]
    monkeypatch.setattr(b, "stances", lambda *a, **k: goals)
    monkeypatch.setattr(b, "_walkable", lambda *a: set(goals))
    seen = []
    monkeypatch.setattr(
        b, "_navigate", lambda goal, **kw: seen.append(goal) or goal == goals[1]
    )
    report = br.BuildReport()
    runner = br._Runner(b, report, 100, BLOCKS)
    monkeypatch.setattr(br, "_do", lambda body, act: br.Result(True))
    runner.attempt(bp.Place((2, 0, 2), "minecraft:oak_planks", "minecraft:oak_planks"))
    assert seen == goals
    assert not report.failures


@pytest.mark.parametrize("walk_only", [False, True])
def test_walk_to_plans_once_and_probes_only_when_walk_only(monkeypatch, walk_only):
    b, _, _ = body()
    b.walk_only = walk_only
    seen = []
    monkeypatch.setattr(b, "_stand_y", lambda x, y, z: y)
    monkeypatch.setattr(
        b, "_navigate", lambda goal, **kw: seen.append(kw["probe"]) or True
    )
    assert b.walk_to((3, 0, 3)) is True
    assert seen == [walk_only]


def test_probe_with_a_route_becomes_the_walk_instead_of_ending(monkeypatch):
    from mclab.route_types import RouteResult

    monkeypatch.setattr(cb.time, "monotonic", lambda: 7.0)
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.probe, ctl.no_drive, ctl.deadline = True, True, 10**18
    ctl.mode, ctl.walk_wall = "following", 90.0
    ctl.last_result = RouteResult("reached", ((0, 0, 0), (1, 0, 0)), 0, 0, "ok")
    assert ctl.stop_reason({"tick": 1}) is None
    assert (ctl.probe, ctl.no_drive, ctl.deadline) == (False, False, 97.0)


def test_probe_drive_ends_on_the_first_planner_verdict():
    from mclab.route_types import RouteResult

    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.probe, ctl.deadline, ctl.mode, ctl.last_result = True, 10**18, "planning", None
    assert ctl.stop_reason({"tick": 1}) is None  # verdict pending: keep parked
    ctl.last_result = RouteResult("unreachable", (), 0, 0, "no route")
    assert ctl.stop_reason({"tick": 2}) == "unreachable"
    ctl.last_result = RouteResult("incomplete", (), 0, 0, "partial")
    assert ctl.stop_reason({"tick": 2}) == "unreachable"
    ctl.mode = "incomplete"
    ctl.last_result = None
    assert ctl.stop_reason({"tick": 2}) == "unreachable"
    ctl.mode = "unreachable"  # a fresher yes beats the stale no
    ctl.last_result = RouteResult("reached", ((0, 0, 0),), 0, 0, "ok")
    assert ctl.stop_reason({"tick": 3}) is None and ctl.probe is False  # walks it
    ctl.probe, ctl.mode, ctl.last_result = True, "searching", None
    ctl.deadline = 0
    assert ctl.stop_reason({"tick": 4}) is None and ctl.probe is False  # walk anyway


def test_probe_finishes_map_transfer_without_resetting_five_second_watch(monkeypatch):
    from types import SimpleNamespace

    import mclab.companion_body as cb

    now = [0.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: now[0])
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.probe, ctl.deadline, ctl.mode = True, 2.5, "planning"
    ctl.last_result = SimpleNamespace(status="searching", reason="planner map transfer")
    ctl.guard = cb.StuckGuard()
    observation = {"tick": 1, "body": {"position": [0.5, 0, 0.5]}}
    ctl.walk_wall = 90.0
    assert ctl.stop_reason(observation) is None
    now[0] = 3.0
    # past the probe wall mid-upload: become the walk, keeping the upload
    assert ctl.stop_reason(observation) is None and ctl.no_drive is False
    assert ctl.deadline == 93.0
    now[0] = 5.0
    assert ctl.stop_reason(observation) is None  # wall time no longer ends it


def test_goto_controller_gives_up_on_a_search_that_never_yields_a_move():
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.unreachable_since, ctl.searching_since = 10**12, None, None
    ctl.mode = "searching"
    assert ctl.stop_reason({"tick": 100}) is None
    ctl.mode = "following"  # a move resets the clock
    assert ctl.stop_reason({"tick": 250}) is None
    ctl.mode = "searching"
    assert ctl.stop_reason({"tick": 300}) is None
    assert ctl.stop_reason({"tick": 300 + cb.SEARCH_STALL_TICKS}) == "stalled"


def test_companion_world_restores_the_lab_on_keyboard_interrupt(monkeypatch):
    import pytest

    from mclab import natural

    order = []
    ctx = __import__("contextlib").contextmanager

    @ctx
    def fake_world(seed):
        order.append("switch")
        try:
            yield
        finally:
            order.append("restore")

    monkeypatch.setattr(natural, "natural_world", fake_world)
    monkeypatch.setattr(natural, "_restore", lambda seed: order.append("copy"))
    monkeypatch.setattr(natural, "_start", lambda: order.append("start"))
    with pytest.raises(KeyboardInterrupt), cb.companion_world(1337):
        raise KeyboardInterrupt
    assert order == ["switch", "copy", "restore", "start"]


def test_companion_radius_accounts_for_actual_motor_arrival():
    for radius, reach, expected in [(2, None, 1.8), (0.1, None, 0), (2, 4.5, 2)]:
        controller = cb._GotoController.__new__(cb._GotoController)
        controller.radius = radius
        controller.interact_reach = reach
        assert controller.planner_radius == expected


def test_door_finalization_policy_restores_after_failure():
    import pytest

    b, _, _ = body()
    saved = b.terrain_policy
    with pytest.raises(ValueError), b.no_opening_doors():
        assert b.terrain_policy.use_doors is False
        raise ValueError("fixture failure")
    assert b.terrain_policy is saved


def test_goto_stance_searches_only_for_a_target_beyond_the_sensed_map(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]
    monkeypatch.setattr(b, "stances", lambda *a, **k: [])
    calls = []
    monkeypatch.setattr(b, "_navigate", lambda *a, **k: calls.append(a) or True)
    assert b.goto_stance((-5, 2, -5), 4.5) is False  # near, no stance: fail fast
    assert not calls
    assert b.goto_stance((200, 2, 200), 4.5) is True  # unsensed: search
    assert calls


def test_find_blocks_reads_a_pathing_grid_view_by_palette(tmp_path):
    from mclab.pathing_grid import PathingGrid

    grid = PathingGrid()
    stone = {"block": "minecraft:stone", "collision_boxes": [FULL]}
    log = {"block": "minecraft:oak_log", "collision_boxes": [FULL]}
    for pos, cell in (((1, 2, 3), log), ((-20, 64, 5), log), ((0, 0, 0), stone)):
        grid.put(pos, grid.state_id(cell), grid.provenance_id(1, "s", 0))
    air = {"block": "minecraft:air", "collision_boxes": []}
    for pos in ((1, 3, 3), (-20, 65, 5)):  # an open face on each log
        grid.put(pos, grid.state_id(air), grid.provenance_id(1, "s", 0))
    buried = {(x, y, z) for x in (4, 5, 6) for y in (4, 5, 6) for z in (4, 5, 6)}
    for pos in buried:  # the middle one has no open face
        grid.put(pos, grid.state_id(log), grid.provenance_id(1, "s", 0))
    b, _, memory = body()
    memory.cells = grid.view()
    found = b.find_blocks("minecraft:oak_log")
    assert (5, 5, 5) not in found
    assert {(-20, 64, 5), (1, 2, 3)} <= set(found) < buried | {(-20, 64, 5), (1, 2, 3)}


def test_a_bounded_scan_reads_only_the_sections_near_its_centres():
    # Iron run 20261112T184523101728Z: late in the run a stone scan listed every
    # stone cell of 2,040 sections (0.6-1.1 s of silence a call, part of all three
    # standstills) before the gather kept the few within 24 blocks of home.
    from mclab.pathing_grid import PathingGrid

    grid = PathingGrid()
    stone = {"block": "minecraft:stone", "collision_boxes": [FULL]}
    cells = [(0, 0, 0), (20, 5, -20), (40, 0, 0), (0, 40, 0), (-200, 0, 300)]
    for pos in cells:
        grid.put(pos, grid.state_id(stone), grid.provenance_id(1, "s", 0))
    b, _, memory = body()
    memory.cells = grid.view()
    assert set(b.find_all("minecraft:stone")) == set(cells)
    near = b.find_all("minecraft:stone", [((0, 0, 0), 24.0)])
    # whole sections: a cell just past the radius may come back, a far one never
    assert {(0, 0, 0), (20, 5, -20)} <= set(near) <= set(cells[:3])
    two = b.find_all("minecraft:stone", [((0, 0, 0), 24.0), ((-200, 0, 300), 2.0)])
    assert (-200, 0, 300) in two and (0, 40, 0) not in two
    read = []
    scan = b._scan
    b._scan = lambda block, within=None: read.append(within) or scan(block, within)
    b.find_blocks("minecraft:stone", within=[((0, 0, 0), 24.0)])
    assert read == [[((0, 0, 0), 24.0)]]


def test_a_stance_must_see_a_face_of_a_solid_neighbour():
    from mclab.companion_body import sees_support

    solid = {"block": "minecraft:oak_planks", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    lattice = {"block": "minecraft:scaffolding", "collision_boxes": []}
    target = (5, 69, 5)
    cells = {(4, 69, 5): solid}  # the neighbour west of the target

    assert sees_support(cells, (6, 66, 5), target)  # east of it: sees its east face
    assert not sees_support(cells, (3, 66, 5), target)  # west of it: sees its back
    assert sees_support({(5, 69, 4): lattice}, (5, 66, 3), target) is False
    assert sees_support({(5, 69, 4): lattice}, (5, 66, 6), target)  # scaffolding counts
    # the top of a scaffolding column is reached from the ground beside it
    assert sees_support({(5, 68, 5): lattice}, (7, 66, 5), target)


def test_a_face_hidden_behind_a_wall_does_not_make_a_stance():
    from mclab.companion_body import sees_support

    solid = {"block": "minecraft:oak_planks", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    target = (0, 1, 0)  # inside corner: floor below, walls west... seen past a wall
    cells = {(0, 0, 0): solid}  # the floor block under the target
    far = (3, 1, 3)

    assert sees_support(cells, far, target)
    cells[(1, 1, 1)] = cells[(1, 2, 1)] = cells[(2, 2, 2)] = cells[(1, 1, 0)] = solid
    cells[(0, 1, 1)] = cells[(1, 2, 0)] = cells[(0, 2, 1)] = solid
    assert not sees_support(cells, far, target)


def test_companion_plans_ask_for_doors_and_vines_and_old_requests_are_unchanged():
    from mclab.body_search import edit_payload
    from mclab.companion_body import DIG_TERRAIN, NATURAL_MOVEMENT
    from mclab.terrain_steps import TerrainPolicy

    edits = edit_payload(DIG_TERRAIN, [], [])
    assert edits["doors"] is True and "vines" not in edits  # vines climbable: default
    assert NATURAL_MOVEMENT.allow_climb  # ladders and scaffolding
    plain = edit_payload(TerrainPolicy(), [], [])
    assert "doors" not in plain and "vines" not in plain


def _stocked(slot, policy):
    b, bridge, _ = body()
    b.terrain_policy = policy
    b._obs["body"]["inventory"] = [
        {"item": "minecraft:dirt", "count": 64, "slot": slot},
        {"item": "minecraft:cobblestone", "count": 9, "slot": 0},
    ]
    b.observe = lambda: None
    return b, bridge


def test_stage_fill_equips_dirt_that_sits_outside_the_hotbar():
    # run 20261109T204446994225Z: dirt only in slots 14/16 -> placeSupply 0 -> the
    # planner refused every pillar edit and reported "unreachable" from a pit.
    b, bridge = _stocked(14, cb.DIG_TERRAIN)
    assert b.stage_fill() == "minecraft:dirt"
    assert bridge.sent[-1] == ("equip", {"item": "minecraft:dirt"})


def test_stage_fill_leaves_hotbar_stock_and_walk_only_policies_alone():
    b, bridge = _stocked(3, cb.DIG_TERRAIN)
    assert b.stage_fill() is None and bridge.sent == []
    b, bridge = _stocked(14, cb.WALK_TERRAIN)
    assert b.stage_fill() is None and bridge.sent == []


def test_find_blocks_skips_protected_ground():
    from dataclasses import replace

    b, _, memory = body()
    dirt = {"block": "minecraft:dirt", "collision_boxes": [FULL]}
    air = {"block": "minecraft:air", "collision_boxes": []}
    memory.cells = {(0, 0, 0): dirt, (0, 1, 0): air, (9, 0, 0): dirt, (9, 1, 0): air}
    b._scan = lambda block, within=None: [(0, 0, 0), (9, 0, 0)]
    b.terrain_policy = replace(cb.DIG_TERRAIN, protected_positions=((0, 0, 0),))
    assert b.find_blocks("minecraft:dirt") == [(9, 0, 0)]


def test_no_placing_turns_placement_off_and_restores_the_policy():
    from mclab.companion_body import DIG_TERRAIN

    b, _bridge, _memory = body()
    b.terrain_policy = DIG_TERRAIN
    with b.no_placing():
        p = b.terrain_policy
        assert not (p.allow_place or p.allow_pillar or p.allow_bridge)
        assert p.allow_break == DIG_TERRAIN.allow_break
        assert b.movement_policy.allow_climb
    assert b.terrain_policy is DIG_TERRAIN
    assert b.movement_policy.allow_climb


def test_stances_rank_walkable_cells_before_nearer_unreachable_ones(monkeypatch):
    b, _, _ = body()
    floor = {(x, 0, 0) for x in range(6)}
    ledge = (1, 3, 0)  # nearer to the body than the far floor, but 3 up: no walk
    monkeypatch.setattr(cb, "standable", lambda cells, n: n in floor or n == ledge)
    monkeypatch.setattr(cb, "interacts", lambda *a: True)
    monkeypatch.setattr(b, "_placing", lambda t: False)
    monkeypatch.setattr(b, "position", lambda: (0, 0, 0))
    assert ledge not in b._walkable({}, (0, 0, 0))
    found = b.stances((4, 2, 0), 4.5)
    assert ledge in found and (5, 0, 0) in found
    assert found.index((5, 0, 0)) < found.index(ledge)


def test_the_top_of_a_scaffolding_column_is_a_stance(monkeypatch):
    b, _, _ = body()
    scaff = {"block": "minecraft:scaffolding"}
    air = {"block": "minecraft:air"}
    cells = {(0, 0, 0): scaff, (0, 1, 0): scaff, (0, 2, 0): air, (0, 3, 0): air}
    assert cb._tower_top(cells, (0, 2, 0))
    assert not cb._tower_top(
        cells, (0, 1, 0)
    )  # inside the column is not a stable stance
    monkeypatch.setattr(b, "_cells_now", lambda: cells)
    monkeypatch.setattr(cb, "interacts", lambda *a: True)
    monkeypatch.setattr(b, "_placing", lambda t: False)
    assert b.stances((1, 2, 0), 4.5) == [(0, 2, 0)]


def test_scaffold_top_supports_feet_but_the_column_is_not_a_stance():
    air = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}
    cells = {
        (0, 0, 0): {
            "block": "minecraft:scaffolding",
            "fluid": "minecraft:empty",
            "collision_boxes": [[0, 0.875, 0, 1, 1, 1]],
        },
        (0, 1, 0): air,
        (0, 2, 0): air,
    }
    assert cb.standable(cells, (0, 1, 0))
    assert not cb.standable(cells, (0, 0, 0))


def test_fill_walk_can_disable_drops_and_restore_the_original_policy():
    b, _, _ = body()
    original = b.movement_policy
    with b.no_placing(max_drop=0):
        assert b.movement_policy.max_drop == 0
    assert b.movement_policy == original


def test_stand_height_uses_confirmed_placements_before_the_sensor_catches_up():
    b, _, _ = body()
    b._remember((1, 0, 0), "minecraft:dirt")
    assert b._stand_y(1, 1, 0) == 1


def _stale_idle_body(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]
    now = [100.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: now[0])
    b._navigation_idle = {"anchor": (20.5, 0.0, 20.5), "since": 10.0}  # 90 s stale
    seen = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        seen.append(dict(b._navigation_idle))
        b._navigation_idle.update(anchor=(1.0, 0.0, 1.0), since=now[0])
        now[0] += 3  # two walks inside one operation share one clock
        return False

    monkeypatch.setattr(b, "_navigate", navigate)
    return b, seen


@pytest.mark.parametrize(
    "op",
    [
        lambda b: b.goto_stance((2, 0, 2), 4.5),
        lambda b: b.walk_to((3, 0, 3)),
        lambda b: b.walk_to_drop((3, 0, 3)),
        lambda b: b.escape_hole((3, 0, 3)),
        lambda b: b.dig_to((3, 0, 3)),
    ],
)
def test_each_navigation_op_starts_with_a_fresh_idle_clock(monkeypatch, op):
    b, seen = _stale_idle_body(monkeypatch)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(3, 0, 3)])
    monkeypatch.setattr(b, "_stand_y", lambda x, y, z: y)
    b.terrain_policy = cb.replace(b.terrain_policy, allow_break=True)
    monkeypatch.setattr(cb, "standable", lambda cells, node: node[1] > 0)  # a rim
    op(b)
    assert seen[0] == {"anchor": None, "since": 100.0}


def test_walks_inside_one_op_share_the_idle_clock(monkeypatch):
    b, seen = _stale_idle_body(monkeypatch)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(3, 0, 3), (4, 0, 3)])
    b.goto_stance((2, 0, 2), 4.5)
    assert len(seen) == 2 and seen[1]["since"] == 100.0  # not reset between them


def test_drop_pickup_defers_an_item_still_falling_instead_of_giving_up(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [0.5, 70.0, 0.5]
    monkeypatch.setattr(b, "_stand_y", lambda *args: 70)  # ground 5 below the log
    monkeypatch.setattr(
        b, "_navigate", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    assert b.walk_to_drop((1, 75, 0), 0.8) is None  # not False: retry once it lands


def test_probe_with_a_partial_route_becomes_the_walk_unless_placing_is_off():
    # Iron runs 20261112T180613337809Z and 20261112T184523101728Z: every probe to a
    # far stance ended "incomplete, 4000 expanded" with a usable partial. A probe
    # never moves, so the body stood 32 s (then 7.4 s) while six stances were probed
    # in rotation, and the walk that followed was cut as "stuck" by the idle clock
    # the probes had used up. A partial is progress: the first one is walked.
    from mclab.route_types import RouteResult

    partial = RouteResult("incomplete", ((0, 0, 0), (1, 0, 0)), 4000, 0, "limit")

    def probe(walk_partial):
        ctl = cb._GotoController.__new__(cb._GotoController)
        ctl.probe, ctl.no_drive, ctl.deadline = True, True, 10**18
        ctl.mode, ctl.walk_wall, ctl.walk_partial = "following", 90.0, walk_partial
        ctl.last_result = partial
        return ctl

    walking = probe(True)
    assert walking.stop_reason({"tick": 1}) is None
    assert (walking.probe, walking.no_drive) == (False, False)
    # walk_only (building): an incomplete probe that gets no nearer is an
    # unreachable stance
    ctl = probe(False)
    ctl.goal = (0, 0, 0)
    assert ctl.stop_reason({"tick": 1}) == "unreachable"


def test_walk_only_probe_walks_a_partial_that_gains_ground():
    # iron-xray run 20261116T143915798373Z: plan-172 came back incomplete (10000
    # expanded, length 9), the driver dropped it and re-sent the same search.
    from mclab.route_types import RouteResult

    partial = RouteResult("incomplete", ((0, 0, 0), (1, 0, 0)), 10000, 0, "limit")
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.probe, ctl.no_drive, ctl.deadline = True, True, 10**18
    ctl.mode, ctl.walk_wall, ctl.walk_partial = "following", 90.0, False
    ctl.last_result, ctl.goal = partial, (9, 0, 0)
    assert ctl.stop_reason({"tick": 1}) is None
    assert (ctl.probe, ctl.no_drive) == (False, False)


def test_partial_that_moved_the_body_nowhere_is_not_walked_again():
    # run 20261116T155654335035Z: boxed in by its own wall, each partial ended
    # nearer on paper, was cancelled and the body snapped back (13 s standstill).
    from mclab.route_types import RouteResult

    partial = RouteResult("incomplete", ((0, 0, 0), (1, 0, 0)), 10000, 0, "limit")
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.goal, ctl.last_partial_walk = (9, 0, 0), ((0, 0, 0), (0, 0, 0))
    assert not ctl._partial_gains(partial.route)
    ctl.last_partial_walk = ((-3, 0, 0), (0, 0, 0))  # that walk did gain ground
    assert ctl._partial_gains(partial.route)
    ctl.last_partial_walk = ((0, 0, 0), (0, 0, 0))
    assert ctl._partial_gains(((5, 0, 0), (6, 0, 0)))  # a different start


@pytest.mark.parametrize("walk_only", [False, True])
def test_navigate_lets_a_probe_walk_a_partial_only_when_placing_is_on(
    monkeypatch, walk_only
):
    b, bridge, _ = body()
    b.walk_only = walk_only
    bridge.state = lambda: {}
    monkeypatch.setattr(b, "observe", lambda: {})
    seen = []

    class Controller:
        probe = False

        def __init__(self, *args, **kwargs):
            seen.append(kwargs["walk_partial"])

        def close(self):
            pass

    monkeypatch.setattr(cb, "_GotoController", Controller)
    monkeypatch.setattr(cb, "drive", lambda *a, **k: {})
    b._navigate((3, 0, 3), reach=None, radius=0.6, probe=True)
    assert seen == [not walk_only]


def test_find_blocks_tests_open_faces_only_where_asked():
    # Iron run 20261112T180613337809Z: every stone cell on the map got six face
    # lookups before the gather kept the few near home; 2.5-2.9 s of silence a call.
    b, _, memory = body()
    stone = {"block": "minecraft:stone", "collision_boxes": [FULL]}
    air = {"block": "minecraft:air", "collision_boxes": []}
    memory.cells = {(0, 0, 0): stone, (0, 1, 0): air, (9, 0, 0): stone, (9, 1, 0): air}
    b._scan = lambda block, within=None: [(0, 0, 0), (9, 0, 0)]
    tested = []
    face = b._open_face
    b._open_face = lambda p: tested.append(p) or face(p)
    assert b.find_blocks("minecraft:stone", lambda p: p[0] > 5) == [(9, 0, 0)]
    assert tested == [(9, 0, 0)]


def test_look_ahead_controller_is_taken_only_by_the_navigation_it_planned():
    from types import SimpleNamespace

    from mclab.companion_body import CompanionBody

    body = object.__new__(CompanionBody)
    body.log = []
    body.position = lambda: (0.5, 1, 0.5)
    closed = []
    body._close_controller = closed.append
    ahead = SimpleNamespace(deadline=0)
    body._ahead = (ahead, (3, 1, 0), 0.6, (0.5, 1, 0.5))
    assert body._take_ahead((4, 1, 0), None, 0.6, True, 5) is None  # other stance
    assert closed == [ahead] and body._ahead is None
    body._ahead = (ahead, (3, 1, 0), 0.6, (0.5, 1, 0.5))
    assert body._take_ahead((3, 1, 0), None, 0.6, True, 5) is ahead
    assert ahead.deadline > 0 and body._ahead is None


def test_free_slots_and_can_hold_come_from_the_per_slot_rows():
    b, _, _ = body()
    full = [{"slot": i, "item": "minecraft:dirt", "count": 64} for i in range(36)]
    b._set_inventory(full)
    assert b.free_slots() == 0 and not b.can_hold("minecraft:cobblestone")
    assert not b.can_hold("minecraft:dirt")  # every dirt stack is full
    b._set_inventory(full[:35] + [{"slot": 35, "item": "minecraft:dirt", "count": 10}])
    assert b.can_hold("minecraft:dirt") and not b.can_hold("minecraft:stone")
    helmet = {"slot": 36, "item": "minecraft:iron_helmet", "count": 1}
    b._set_inventory(full[:30] + [helmet])
    assert b.free_slots() == 6 and b.can_hold("minecraft:stone")  # armour is no slot


def test_planning_terrain_incomplete_cycle_stops_as_stalled():
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.unreachable_since, ctl.searching_since = 10**12, None, None
    for tick, mode in (
        (100, "planning"),
        (102, "unreachable"),
        (103, "terrain_planning"),
        (105, "terrain_incomplete"),
        (106, "planning"),
    ):
        ctl.mode = mode
        assert ctl.stop_reason({"tick": tick}) is None
    ctl.mode = "terrain_incomplete"
    assert ctl.stop_reason({"tick": 100 + cb.SEARCH_STALL_TICKS}) == "stalled"


def test_diagonal_hop_stays_within_sensed_radius_and_avoids_recursion(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [0.5, 1.0, 0.5]
    monkeypatch.setattr(b, "_cells_now", lambda: {})
    monkeypatch.setattr(cb, "standable", lambda cells, node: True)
    mid = b._hop_toward((35, 1, 35))
    assert mid is not None
    assert math.hypot(mid[0], mid[2]) <= cb.SENSED_RADIUS
    # Even if _hop_toward returned an over-radius point, _navigate refuses cleanly
    monkeypatch.setattr(b, "_hop_toward", lambda goal: (29, 1, 29))
    assert b._navigate((40, 1, 40), reach=None, radius=1.0) is False


def test_dig_to_straight_up_fails_fast_without_place_supply(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [69.5, 67.0, -52.5]
    b._set_inventory([{"slot": 0, "item": "minecraft:wooden_axe", "count": 1}])
    monkeypatch.setattr(b, "_cells_now", lambda: {})
    monkeypatch.setattr(
        b, "_navigate", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    assert b.dig_to((69, 69, -52)) is False


def test_goto_stance_skips_duplicate_distant_hops_and_fallback_when_hop_fails(
    monkeypatch,
):
    b, _, _ = body()
    b._obs["body"]["position"] = [47.5, 65.0, -28.5]
    monkeypatch.setattr(b, "_cells_now", lambda: {})
    monkeypatch.setattr(cb, "standable", lambda cells, node: True)
    monkeypatch.setattr(
        b, "stances", lambda *a, **k: [(90, 62, -59), (90, 62, -58), (89, 62, -59)]
    )
    calls = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        calls.append((goal, reach, probe))
        return False

    monkeypatch.setattr(b, "_navigate", navigate)
    assert b.goto_stance((90, 63, -59), 4.5) is False
    assert calls == [((90, 62, -59), None, True)]



def test_position_of_a_dead_body_raises_bodydied():
    import pytest

    from mclab.progress_watch import BodyDied

    b, _, _ = body()
    b._obs = {"tick": 5, "body": {"food": 17}}  # a dead body reports no position
    with pytest.raises(BodyDied):
        b.position()


def test_inventory_and_food_read_state_not_policy(monkeypatch):
    from mclab import companion_body

    b, bridge, _ = body()
    dirt = {"slot": 0, "item": "minecraft:dirt", "count": 2}
    stone = {"slot": 1, "item": "minecraft:dirt", "count": 3}
    state = {"tick": 100, "body": {"inventory": [dirt, stone], "food": 14}}
    bridge.state = lambda: state
    monkeypatch.setattr(
        companion_body,
        "policy_state",
        lambda client: pytest.fail("inventory()/food() must not call /policy"),
    )
    assert b.inventory() == {"minecraft:dirt": 5}
    assert b.food() == 14
    assert b.free_slots() == 34
    state["body"]["inventory"] = []  # emptied: still a read, not a fallback
    assert b.inventory() == {}


def test_companion_policies_have_every_movement_ability_on():
    from mclab.companion_body import DIG_TERRAIN, NATURAL_MOVEMENT, WALK_TERRAIN

    for terrain in (DIG_TERRAIN, WALK_TERRAIN):
        assert terrain.planner_swim and terrain.climb_vines
    b, _, _ = body()
    b.movement_policy, b.terrain_policy = NATURAL_MOVEMENT, DIG_TERRAIN
    with b.no_placing():
        assert b.movement_policy.allow_swim and b.movement_policy.allow_climb
        assert b.terrain_policy.planner_swim and b.terrain_policy.climb_vines
    assert NATURAL_MOVEMENT.allow_swim and NATURAL_MOVEMENT.allow_climb


def test_search_stall_outlasts_a_capped_planner_search():
    # run 20261116T163035614307Z: 10000-node searches finish in 58-66 ticks
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.unreachable_since, ctl.searching_since = 10**12, None, None
    ctl.mode = "searching"
    assert ctl.stop_reason({"tick": 100}) is None
    assert ctl.stop_reason({"tick": 100 + 70}) is None


def test_entities_read_state_not_policy(monkeypatch):
    from mclab import companion_body

    b, bridge, _ = body()
    cow = {"uuid": "u1", "type": "minecraft:cow", "position": [1.0, 0.0, 2.0]}
    bridge.state = lambda: {"tick": 100, "visible_entities": [cow]}
    monkeypatch.setattr(
        companion_body,
        "policy_state",
        lambda client: pytest.fail("entities() must not call /policy"),
    )
    assert b.entities() == [cow]


def test_walking_with_edits_keeps_stance_filtering_and_may_break():
    from mclab.companion_body import DIG_TERRAIN

    b, _bridge, _memory = body()
    b.terrain_policy = replace(DIG_TERRAIN, allow_break=False)
    b.walk_only = False
    with b.walking_with_edits():
        p = b.terrain_policy
        assert p.allow_place and p.allow_pillar and p.allow_bridge and p.allow_break
        assert b.walk_only  # goto_stance filters and caches failed probes
    assert not b.walk_only


def test_goto_stance_filters_candidates_while_walking_with_edits(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0, 20.5]
    goals = [(2, 0, 0), (3, 0, 1)]
    monkeypatch.setattr(b, "stances", lambda *a, **k: goals)
    monkeypatch.setattr(b, "_walkable", lambda *a: set(goals[1:]))
    seen = []
    monkeypatch.setattr(b, "_navigate", lambda goal, **kw: seen.append(goal) or False)
    with b.walking_with_edits():
        assert not b.goto_stance((2, 0, 2), 4.5)
        assert not b.goto_stance((2, 0, 2), 4.5)
    assert seen == goals[1:]  # unwalkable stance dropped, failed probe cached
