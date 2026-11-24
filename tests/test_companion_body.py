import contextlib
import math
import types
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
        "done",
        {"placed": "minecraft:dirt", "after": "minecraft:dirt"},
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
    state = {
        "block": "minecraft:air",
        "fluid": "minecraft:empty",
        "replaceable": True,
        "properties": {},
        "collision_boxes": [],
    }

    def observation(captured, sample):
        terrain = {
            "schema_version": 1,
            "encoding": "sparse_section_palette",
            "unknown_is_air": False,
            "sample_tick": sample,
            "palette": [state],
            "sections": [],
            "known_cells": 0,
            "bulk_cells": 0,
            "dense_sections": [],
            "uniform_sections": [],
            "encoding_ms": 0.1,
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
        return expand_terrain(
            {
                "tick": sample + 3,
                "sensor_profile": "awareness-v1",
                "body": {},
                "awareness": {
                    "config": awareness.payload(),
                    "revision": "r",
                    "coverage": {"sample_tick": sample},
                },
                "terrain_sections": terrain,
            }
        )

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


def test_a_body_with_no_move_from_its_spot_stops_trying_other_stances(monkeypatch):
    # Run 20261117T155135309814Z: on an eave slab boxed in by glass and flower pots
    # every stance was planned again (unreachable after 1 node), 28 s per target.
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]
    seen = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        seen.append(goal)
        b.boxed_in = True
        return False

    monkeypatch.setattr(b, "_navigate", navigate)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(2, 0, 0), (3, 0, 1)])
    assert b.goto_stance((2, 0, 2), 4.5) is False
    assert seen == [(2, 0, 0)]


@pytest.mark.parametrize(
    "status, expanded, moved, boxed, shut",
    [
        ("unreachable", 1, 0.0, True, True),  # no move at all from the spot
        ("unreachable", 9, 0.0, False, True),  # 20261122T024835715542Z: a closed eave
        # 20261122T125350762344Z: a roof slope of 40 nodes, every way off a drop of 5+
        ("unreachable", 40, 0.0, False, True),
        # a closed floor is shut in too, whatever its size; the runner picks towers
        # or a drop by where the goal is (build_runner._stance)
        ("unreachable", 400, 0.0, False, True),
        ("unreachable", 0, 0.0, False, False),  # a goal proof expands nothing
        ("incomplete", 9, 0.0, False, False),  # capped: more may lie beyond
        ("unreachable", 9, 2.0, False, False),  # it walked: not stuck where it was
    ],
)
def test_a_dead_end_is_boxed_in_when_no_move_and_shut_in_when_its_region_ran_out(
    status, expanded, moved, boxed, shut
):
    last = types.SimpleNamespace(status=status, expanded=expanded)
    assert cb.dead_end(last, (0.0, 70.0, 0.0), (moved, 70.0, 0.0)) == (boxed, shut)
    assert cb.dead_end(None, (0.0, 70.0, 0.0), (0.0, 70.0, 0.0)) == (False, False)


def test_getting_down_clears_shut_in_too(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 10.0, 20.5]
    x, y, z = b.position()
    monkeypatch.setattr(cb, "standable", lambda cells, n: n == (x + 1, y - 5, z))
    monkeypatch.setattr(b, "health", lambda: 20.0)
    monkeypatch.setattr(b, "_navigate", lambda goal, **k: True)
    b.shut_in = True
    assert b.get_down() is True and not b.shut_in and not b.boxed_in


def test_getting_down_takes_the_shortest_drop_beside_the_body(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 10.0, 20.5]
    x, y, z = b.position()
    low, lower = (x + 1, y - 5, z), (x, y - 7, z - 2)
    monkeypatch.setattr(cb, "standable", lambda cells, n: n in (low, lower))
    monkeypatch.setattr(b, "health", lambda: 20.0)
    before = b.movement_policy.max_drop
    tried = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        tried.append((goal, b.movement_policy.max_drop))
        return True

    monkeypatch.setattr(b, "_navigate", navigate)
    b.boxed_in = True
    assert b.get_down() is True and not b.boxed_in
    assert tried == [(low, 12)]
    assert b.movement_policy.max_drop == before
    monkeypatch.setattr(b, "health", lambda: 6.0)  # no health to spare
    tried.clear()
    assert b.get_down() is False and not tried


def test_getting_down_never_counts_a_spot_the_body_walks_to_anyway(monkeypatch):
    # Issue #11: the nearest lower spot can lie inside the region the body is shut
    # in. Walking there "succeeded", shut_in was cleared, and the body was as stuck
    # as before. Only a spot the walk alone cannot reach takes the body out.
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 10.0, 20.5]
    x, y, z = b.position()
    inside, out = (x + 1, y - 1, z), (x - 1, y - 5, z)
    monkeypatch.setattr(cb, "standable", lambda cells, n: n in (inside, out))
    monkeypatch.setattr(b, "_walkable", lambda cells, here: {here: 0, inside: 1})
    monkeypatch.setattr(b, "health", lambda: 20.0)
    tried = []

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        tried.append(goal)
        return True

    monkeypatch.setattr(b, "_navigate", navigate)
    b.shut_in = True
    assert b.get_down() is True and not b.shut_in
    assert tried == [out]


def test_a_stance_tried_from_a_shut_in_region_is_not_held_against_it(monkeypatch):
    # Issue #11, run 20261122T185137031693Z: shut in on the upper floor, every
    # ground stance for a scaffolding root came back unreachable and was barred for
    # STANCE_RETRY_S. Once down, only stances inside the pocket were left untried; the
    # body walked back up into it and the root was skipped for good.
    b, _, _ = body()
    ground = (2, 0, 0)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [ground])
    monkeypatch.setattr(cb, "interacts", lambda *a, **k: False)
    shut = {"on": True}

    def navigate(goal, *, reach, radius, probe=False, progress=None):
        b.shut_in = shut["on"]
        return not shut["on"]

    monkeypatch.setattr(b, "_navigate", navigate)
    monkeypatch.setattr(b, "_worth_searching", lambda target: False)
    assert b.goto_stance((2, 0, 2), 4.5) is False
    assert not b._failed_stances
    shut["on"] = False
    assert b.goto_stance((2, 0, 2), 4.5) is True


def test_roof_support_top_is_not_visible_from_recorded_floor_height():
    b, _, memory = body()
    b._obs["body"]["position"] = [-19.5, 65.0, 59.31]
    target, support = (-18, 67, 58), (-18, 66, 58)
    memory.cells[support] = {"block": "minecraft:oak_planks", "collision_boxes": [FULL]}
    assert not b.can_place_here(target, 4.5)


def _wall_beside_target():
    """A plank placed at (1, 0, 2); the next wall cell (1, 0, 3) shows the floor's top
    face past it to a body at (0, 0, 0), but the ray to the cell's centre runs through
    the plank (run 20261119T225430670386Z: 119 walks to cells the server accepted)."""
    b, _, memory = body()
    plank = {"block": "minecraft:oak_planks", "collision_boxes": [FULL]}
    memory.cells[(1, 0, 2)] = plank
    b.precise_stance = True
    return b, (1, 0, 3)


def test_a_cell_whose_centre_is_hidden_is_placed_from_a_visible_support_face():
    b, target = _wall_beside_target()
    assert not cb.interacts(b._cells_now(), (0, 0, 0), target, 4.5)  # the old test
    assert b.can_place_here(target, 4.5)
    assert (0, 0, 0) in b.stances(target, 4.5)


def test_goto_stance_does_not_walk_when_the_server_would_place_from_here(monkeypatch):
    b, target = _wall_beside_target()
    monkeypatch.setattr(
        b, "_navigate", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    assert b.goto_stance(target, 4.5) is True


def test_placement_sight_keeps_the_required_support_face():
    # a log along x hangs on its x neighbours only: the floor under it is no face
    b, target = _wall_beside_target()
    beside = {(0, 0, 3), (2, 0, 3)}
    assert not b.can_place_here(target, 4.5, beside)
    b.required_support = beside
    assert (0, 0, 0) not in b.stances(target, 4.5)


def test_placement_sight_uses_the_real_eye_not_the_stance_centre():
    b, _, memory = body()
    target = (5, 1, 0)  # on a plank 4.6 from the cell centre's eye
    plank = {"block": "minecraft:oak_planks", "collision_boxes": [FULL]}
    memory.cells[(5, 0, 0)] = plank
    b._obs["body"]["position"] = [0.5, 0.0, 0.5]
    assert not b.can_place_here(target, 4.5)
    b._obs["body"]["position"] = [0.99, 0.0, 0.5]  # same cell, its near edge
    assert b.can_place_here(target, 4.5)


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


def test_partial_ending_at_a_known_dead_end_is_not_walked_again_until_the_map_changes():
    # run 20261116T232024268879Z: a partial walked into a dead-end endpoint and back
    # out was re-walked five times; the endpoint is a map fact for that goal.
    from types import SimpleNamespace

    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.goal, ctl.last_partial_walk = (9, 0, 0), None
    ctl.map_view = SimpleNamespace(revision=7)
    ctl.dead_ends = {"revision": 7, "ends": [(1, 0, 0)]}
    assert not ctl._partial_gains(((0, 0, 0), (1, 0, 0)))
    assert ctl._partial_gains(((0, 0, 0), (3, 0, 0)))  # another endpoint
    ctl.map_view = SimpleNamespace(revision=8)  # the map changed: ask again
    assert ctl._partial_gains(((0, 0, 0), (1, 0, 0)))
    assert ctl.dead_ends["ends"] == []


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


def _sees(cells, node, target, scaffold=False):
    return cb.places_from(
        cells, cb.stance_eye(node), node, target, 4.5, scaffold=scaffold
    )


def test_a_stance_must_see_a_face_of_a_solid_neighbour():
    solid = {"block": "minecraft:oak_planks", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    lattice = {"block": "minecraft:scaffolding", "collision_boxes": []}
    target = (5, 69, 5)
    cells = {(4, 69, 5): solid}  # the neighbour west of the target

    assert _sees(cells, (6, 66, 5), target)  # east of it: sees its east face
    assert not _sees(cells, (3, 66, 5), target)  # west of it: sees its back
    assert not _sees({(5, 69, 4): lattice}, (5, 66, 3), target)
    assert _sees({(5, 69, 4): lattice}, (5, 66, 6), target)  # scaffolding counts
    # the top of a scaffolding column is reached from the ground beside it
    assert _sees({(5, 68, 5): lattice}, (7, 66, 5), target, scaffold=True)
    # only when placing scaffolding itself; a plank cannot extend scaffolding sideways
    assert not _sees({(5, 68, 5): lattice}, (7, 66, 5), target)


def test_a_face_hidden_behind_a_wall_does_not_make_a_stance():
    solid = {"block": "minecraft:oak_planks", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    target = (0, 1, 0)  # inside corner: floor below, walls west... seen past a wall
    cells = {(0, 0, 0): solid}  # the floor block under the target
    far = (3, 1, 3)

    assert _sees(cells, far, target)
    cells[(1, 1, 1)] = cells[(1, 2, 1)] = cells[(2, 2, 2)] = cells[(1, 1, 0)] = solid
    cells[(0, 1, 1)] = cells[(1, 2, 0)] = cells[(0, 2, 1)] = solid
    assert not _sees(cells, far, target)


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


def test_find_blocks_skips_protected_ground():
    from dataclasses import replace

    b, _, memory = body()
    dirt = {"block": "minecraft:dirt", "collision_boxes": [FULL]}
    air = {"block": "minecraft:air", "collision_boxes": []}
    memory.cells = {(0, 0, 0): dirt, (0, 1, 0): air, (9, 0, 0): dirt, (9, 1, 0): air}
    b._scan = lambda block, within=None: [(0, 0, 0), (9, 0, 0)]
    b.terrain_policy = replace(cb.DIG_TERRAIN, protected_positions=((0, 0, 0),))
    assert b.find_blocks("minecraft:dirt") == [(9, 0, 0)]


def test_protected_cells_holding_leaves_are_breakable_for_the_planner(monkeypatch):
    from dataclasses import replace

    b, _, _ = body()
    leaves = {"block": "minecraft:oak_leaves", "collision_boxes": [FULL]}
    planks = {"block": "minecraft:birch_planks", "collision_boxes": [FULL]}
    cells = {(0, 2, 0): leaves, (1, 2, 0): planks}
    monkeypatch.setattr(b, "_cells_now", lambda: cells)
    house = ((0, 2, 0), (1, 2, 0), (2, 2, 0))
    b.terrain_policy = replace(cb.DIG_TERRAIN, protected_positions=house)
    assert b._planner_terrain().protected_positions == ((1, 2, 0), (2, 2, 0))
    b.terrain_policy = replace(b.terrain_policy, allow_break=False)
    assert b._planner_terrain().protected_positions == house


def test_placed_build_blocks_are_costly_not_protected_for_the_planner(monkeypatch):
    from dataclasses import replace

    b, _, _ = body()
    cube = lambda name: {"block": name, "collision_boxes": [FULL]}  # noqa: E731
    cells = {
        (0, 2, 0): cube("minecraft:oak_planks"),
        (1, 2, 0): {
            "block": "minecraft:glass_pane",
            "collision_boxes": [[0.4, 0, 0, 0.6, 1, 1]],
        },
        (2, 2, 0): cube("minecraft:chest"),
        (3, 2, 0): {"block": "minecraft:air", "collision_boxes": []},
        (4, 2, 0): cube("minecraft:dirt"),  # not the template's block: stays protected
    }
    monkeypatch.setattr(b, "_cells_now", lambda: cells)
    house = tuple(sorted(cells))
    b.build_cells = {
        (0, 2, 0): "minecraft:oak_planks",
        (1, 2, 0): "minecraft:glass_pane[east=true]",
        (2, 2, 0): "minecraft:chest[facing=north]",
        (3, 2, 0): "minecraft:air",
        (4, 2, 0): "minecraft:oak_planks",
    }
    b.terrain_policy = replace(cb.DIG_TERRAIN, protected_positions=house)
    t = b._planner_terrain()
    assert t.costly_positions == ((0, 2, 0),)
    assert set(t.protected_positions) == set(house) - {(0, 2, 0)}
    b.terrain_policy = replace(b.terrain_policy, allow_break=False)
    assert b._planner_terrain().costly_positions == ()


def test_the_houses_air_cells_take_footholds_whether_or_not_breaking_is_on(
    monkeypatch,
):
    b, _, _ = body()
    house = ((0, 2, 0), (1, 2, 0))
    monkeypatch.setattr(b, "_cells_now", lambda: {})
    b.build_air = {(1, 2, 0)}
    b.terrain_policy = replace(cb.DIG_TERRAIN, protected_positions=house)
    assert b._planner_terrain().protected_positions == ((0, 2, 0),)
    b.terrain_policy = replace(b.terrain_policy, allow_break=False)
    assert b._planner_terrain().protected_positions == ((0, 2, 0),)


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


_ROW = [(x, 0, 4) for x in range(4, 7)] + [(x, 0, 5) for x in range(3, 7)]


def _covers(b, node, cover):
    cells = b._cells_now()
    return sum(cb.places_from(cells, cb.stance_eye(node), node, p, 4.5) for p in cover)


def test_stances_prefer_one_that_also_places_the_next_cells_of_the_sweep():
    """Nearest-first put the body at the edge of reach, so the next cell along the
    wall was out of reach again: one walk per block (run 20261119T225430670386Z,
    119 walks, 195 s). With the sweep's next cells as ``stance_cover`` the stance
    that places the most of them comes first."""
    b, _, _ = body()
    b.precise_stance = True
    target = (3, 0, 4)
    nearest = b.stances(target, 4.5)[0]
    b.stance_cover = dict.fromkeys(_ROW, (None, None))
    best = b.stances(target, 4.5)[0]
    assert cb.places_from(b._cells_now(), cb.stance_eye(best), best, target, 4.5)
    assert _covers(b, best, _ROW) > _covers(b, nearest, _ROW)
    assert _covers(b, best, _ROW) == len(_ROW)


def test_a_stance_inside_a_cell_of_the_sweep_does_not_count_it_covered():
    b, _, _ = body()
    b.precise_stance = True
    b.stance_cover = {(4, 0, 4): (None, None)}
    assert b._stance_covers(b._cells_now(), (4, 0, 4), 4.5) == 0
    assert b._stance_covers(b._cells_now(), (4, 0, 3), 4.5) == 1


def test_a_flower_cell_is_broken_not_placed_into(monkeypatch):
    """A flower is no cell to place into (the server refuses "occupied"), so its
    stance is a break's: the line to the flower, not a support face behind it. The
    flower hid every face, no break stance was found, and site prep stopped
    "blocked" on 16 tulips (run 20261120T075123420431Z)."""
    b, _, memory = body()
    b.precise_stance = True
    target = (3, 0, 0)
    memory.cells[target] = {"block": "minecraft:orange_tulip", "collision_boxes": []}
    assert not b._placing(target)
    assert b.stances(target, 4.5)


@pytest.mark.parametrize(
    "occupant", [None, "minecraft:air", "minecraft:water", "minecraft:short_grass"]
)
def test_an_empty_or_replaceable_cell_is_placed_into(occupant):
    b, _, memory = body()
    b.precise_stance = True
    target = (3, 0, 0)
    if occupant is None:
        memory.cells.pop(target)  # unknown
    else:
        memory.cells[target] = {"block": occupant, "collision_boxes": []}
    assert b._placing(target)
    # the server breaks grass before its clicks: it hides no support face
    assert b.stances(target, 4.5)


def test_stance_cover_does_not_outrank_a_walkable_stance(monkeypatch):
    b, _, _ = body()
    b.precise_stance = True
    target = (3, 0, 4)
    b.stance_cover = dict.fromkeys(_ROW, (None, None))
    walk = {(0, 0, 1)}
    monkeypatch.setattr(b, "_walkable", lambda *a: walk)
    assert b.stances(target, 4.5)[0] == (0, 0, 1)


TOP_SLAB = "minecraft:oak_slab[type=top,waterlogged=false]"
BOTTOM_SLAB = "minecraft:oak_slab[type=bottom,waterlogged=false]"
ON_FLOOR = (3, 0, 3)  # its one support is the floor: a click there is a bottom half


def test_stances_need_a_click_that_gives_the_requested_state():
    """Sight alone sent the body where only the wrong half of a face showed: 20 of
    28 wrong_state failures in run 20261120T083111010944Z were stairs and slabs."""
    b, _, _ = body()
    b.precise_stance = True
    assert b.stances(ON_FLOOR, 4.5)
    b.placing_state = BOTTOM_SLAB
    assert b.stances(ON_FLOOR, 4.5)
    assert b.can_place_here(ON_FLOOR, 4.5, None, BOTTOM_SLAB)
    b.placing_state = TOP_SLAB
    assert b.stances(ON_FLOOR, 4.5) == []
    assert not b.can_place_here(ON_FLOOR, 4.5, None, TOP_SLAB)


def test_stance_cover_counts_a_cell_only_where_its_state_can_be_made():
    b, _, _ = body()
    b.precise_stance = True
    b.stance_cover = {ON_FLOOR: (None, BOTTOM_SLAB)}
    assert b._stance_covers(b._cells_now(), (1, 0, 3), 4.5) == 1
    b.stance_cover = {ON_FLOOR: (None, TOP_SLAB)}
    assert b._stance_covers(b._cells_now(), (1, 0, 3), 4.5) == 0


def _two_stances(monkeypatch, b, sound, shaky):
    """``sound`` places from anywhere in its cell, ``shaky`` only from the exact
    centre; ``shaky`` is the nearer one."""
    monkeypatch.setattr(b, "position", lambda: (0, 0, 0))

    def places(cells, eye, node, target, reach, required=None, **kw):
        return node == sound or (node == shaky and eye == cb.stance_eye(shaky))

    monkeypatch.setattr(cb, "places_from", places)
    monkeypatch.setattr(b, "_walkable", lambda *a: {sound, shaky})


def test_a_stance_that_places_only_from_its_exact_centre_ranks_after_a_sound_one(
    monkeypatch,
):
    """The body stops within PRECISE_RADIUS of a stance's centre: where the face
    shows through a gap that narrow, the eye it arrives with misses it (run
    20261120T083111010944Z, [-17, 68, 59] from [-15, 69, 61]: in view from the
    centre, hidden 0.1 off it)."""
    b, _, _ = body()
    b.precise_stance = True
    sound, shaky = (5, 0, 3), (1, 0, 2)
    _two_stances(monkeypatch, b, sound, shaky)
    found = b.stances(ON_FLOOR, 4.5)
    assert set(found) == {sound, shaky}
    assert found == [sound, shaky]


def test_goto_stance_rechecks_on_arrival_and_walks_on_when_the_face_is_hidden(
    monkeypatch,
):
    """A block placed while passing can hide the face the stance was chosen for
    (run 20261120T083111010944Z: a log placed on the way beside [-7, 66, 67], then
    the click from the stance found no face). The body checks from where it
    arrived and chooses again on the fresh map, without a doomed click."""
    b, _, memory = body()
    b.precise_stance = True
    first, second = (1, 0, 3), (5, 0, 3)
    at = {"pos": (0, 0, 0)}
    monkeypatch.setattr(b, "position", lambda: at["pos"])
    hidden = set()

    def can(target, reach, required=None, state=None, **kw):
        return at["pos"] != (0, 0, 0) and at["pos"] not in hidden

    monkeypatch.setattr(b, "_places_from_here", can)
    picks = [[first, second], [second]]
    monkeypatch.setattr(b, "stances", lambda *a, **k: picks.pop(0))
    monkeypatch.setattr(b, "_walkable", lambda *a: {first, second})
    walked = []

    def navigate(node, **kw):
        walked.append(node)
        at["pos"] = node
        if node == first:
            hidden.add(first)  # placed on the way: the face is gone from here
        return True

    monkeypatch.setattr(b, "_navigate", navigate)
    assert b.goto_stance(ON_FLOOR, 4.5) is True
    assert walked == [first, second]
    assert picks == []  # chosen again on the map after the walk


def test_goto_stance_keeps_the_arrival_when_no_other_stance_is_found(monkeypatch):
    # nothing better on the fresh map: the server decides from where the body stands
    b, _, _ = body()
    b.precise_stance = True
    first = (1, 0, 3)
    at = {"pos": (0, 0, 0)}
    monkeypatch.setattr(b, "position", lambda: at["pos"])
    monkeypatch.setattr(b, "_places_from_here", lambda *a, **k: False)
    picks = [[first], []]
    monkeypatch.setattr(b, "stances", lambda *a, **k: picks.pop(0))
    monkeypatch.setattr(b, "_walkable", lambda *a: {first})
    monkeypatch.setattr(b, "_worth_searching", lambda t: False)

    def navigate(node, **kw):
        at["pos"] = node
        return True

    monkeypatch.setattr(b, "_navigate", navigate)
    assert b.goto_stance(ON_FLOOR, 4.5) is True
    assert at["pos"] == first


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


def test_a_placed_torch_or_trapdoor_does_not_make_the_cell_above_standable():
    # Run 20261117T003035144857Z: the overlay gave every placed block a full cube.
    b, _, _ = body()
    b._remember((1, 0, 0), "minecraft:torch", None, [])
    b._remember((3, 0, 0), "minecraft:oak_trapdoor", None, [[0, 0, 0, 1, 0.1875, 1]])
    b._remember((5, 0, 0), "minecraft:dirt", None, [[0, 0, 0, 1, 1, 1]])
    assert b._stand_y(1, 1, 0) != 1
    assert b._stand_y(5, 1, 0) == 1
    cells = b._cells_now()
    assert cells[(1, 0, 0)]["collision_boxes"] == []
    assert cells[(3, 0, 0)]["collision_boxes"] == [[0, 0, 0, 1, 0.1875, 1]]
    assert not cb.standable(cells, (1, 1, 0))


def test_stand_height_does_not_snap_up_onto_an_overhang():
    # Run 20261117T011432772067Z: the ground under a tree's leaves became a
    # canopy-top goal 3 up; a solid hillside still snaps up.
    b, _, _ = body()

    def column(x, *states):
        for y, state in enumerate(states):
            boxes = [] if state == "air" else [[0, 0, 0, 1, 1, 1]]
            b._remember((x, y, 0), f"minecraft:{state}", None, boxes)

    column(7, "dirt", "oak_leaves", "air", "oak_leaves", "air", "air")
    column(9, "dirt", "dirt", "dirt", "dirt", "air", "air")
    assert b._stand_y(7, 1, 0) == 1
    assert b._stand_y(9, 1, 0) == 4
    # one step up onto a lone leaf over a pond is no stance either (run
    # 20261117T033515924527Z); a step onto a block that rests on the ground is
    column(11, "air", "air", "oak_leaves", "air", "air")
    column(13, "dirt", "dirt", "air", "air")
    assert b._stand_y(11, 2, 0) == 2
    assert b._stand_y(13, 1, 0) == 2


def test_overlay_without_boxes_prefers_the_sensed_cell_of_the_same_block():
    b, _, _ = body()
    thin = [[0, 0, 0, 1, 0.1875, 1]]
    sensed = {"block": "minecraft:oak_trapdoor", "collision_boxes": thin}
    b.memory.cells[(3, 0, 0)] = sensed
    b._remember((3, 0, 0), "minecraft:oak_trapdoor[half=bottom]")
    assert b._cells_now()[(3, 0, 0)]["collision_boxes"] == thin


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


def _crafted_then_state(state_tick: int):
    """A craft done at server tick 190, then an inventory() read of a /state
    stamped ``state_tick`` that still holds the logs the craft used up."""
    b, bridge, _ = body()
    logs = [{"slot": 0, "item": "minecraft:birch_log", "count": 3}]
    planks = [{"slot": 0, "item": "minecraft:birch_planks", "count": 12}]
    bridge.state = lambda: {"tick": state_tick, "body": {"inventory": logs}}
    bridge.tick = 190
    bridge.reply["craft"] = ("done", {"inventory": planks})
    assert b.craft("minecraft:birch_planks", 3).ok
    return b


def test_a_state_stamped_with_the_ops_own_tick_predates_the_op():
    """The body runs ops at the start of a tick, before the server bumps its tick
    count, so a craft reports tick 190 while the /state stamped 190 was published
    at the end of the tick before it. Run 20261122T142224546926Z replanned from that
    snapshot and crafted planks again from logs it no longer held."""
    b = _crafted_then_state(190)
    assert b.inventory() == {"minecraft:birch_planks": 12}


def test_a_later_state_replaces_the_op_inventory():
    """A tick later the /state is the truth (the op result predates the pickup of
    its own drop)."""
    b = _crafted_then_state(191)
    assert b.inventory() == {"minecraft:birch_log": 3}


def test_an_observation_older_than_the_op_does_not_replace_it(monkeypatch):
    b, bridge, _ = body()
    bridge.tick = 190
    bridge.reply["craft"] = (
        "done",
        {"inventory": [{"slot": 0, "item": "minecraft:stick", "count": 4}]},
    )
    assert b.craft("minecraft:stick", 1).ok
    from mclab import companion_body

    obs = {"tick": 190, "body": {"position": [0.5, 0.0, 0.5], "inventory": []}}
    monkeypatch.setattr(companion_body, "policy_state", lambda _bridge: obs)
    b.observe()
    assert b._inv == {"minecraft:stick": 4}
    obs["tick"] = 191
    b.observe()
    assert b._inv == {}


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


def test_the_planner_footholds_are_dirt_then_cobblestone_then_spare_plain_blocks(
    monkeypatch,
):
    from dataclasses import replace

    from mclab import temporary_blocks as tb
    from mclab.companion_jobs import load_vanilla_pack

    b, _, _ = body()
    held = {
        "minecraft:scaffolding": 30,  # towers only: a planner foothold is solid
        "minecraft:stone": 64,  # breaks to cobblestone
        "minecraft:oak_log": 20,
        "minecraft:oak_planks": 10,  # all still wanted by the house
        "minecraft:cobblestone": 4,
    }
    monkeypatch.setattr(b, "inventory", lambda: held)
    monkeypatch.setattr(b, "world", lambda *p: "minecraft:air")
    b.build_cells = {(0, y, 0): "minecraft:oak_planks" for y in range(10)}
    b.temporary_ok = tb.eligible(load_vanilla_pack())
    b.terrain_policy = replace(cb.DIG_TERRAIN, allow_place=True)
    assert b.temporary_items() == [
        "minecraft:scaffolding",
        "minecraft:cobblestone",
        "minecraft:oak_log",
    ]
    assert b._with_temporaries(b.terrain_policy).material_allowlist == (
        "minecraft:cobblestone",
        "minecraft:oak_log",
    )


def test_the_water_plug_is_told_what_the_build_still_needs(monkeypatch):
    # Run 20261121T180806599463Z: the body's plug spent 21 of the house's cobblestone
    # on site-prep water and the foundation ran short.
    b, bridge, _ = body()
    cobble = "minecraft:cobblestone"
    built = set()
    monkeypatch.setattr(
        b, "world", lambda *p: cobble if p in built else "minecraft:air"
    )
    bridge.reply["break_block"] = ("done", {})
    b.break_block((9, 0, 9))
    assert [op for op, _ in bridge.sent] == ["break_block"]  # no build: no keep

    b.build_cells = {(0, 0, 0): cobble, (1, 0, 0): cobble}
    b.sync_plug_keep()
    b.break_block((9, 0, 9))
    keeps = [f for op, f in bridge.sent if op == "plug_keep"]
    assert keeps == [{"items": {cobble: 2}}]  # sent once, unchanged since

    built.add((0, 0, 0))
    b.place_block((5, 0, 5), "minecraft:dirt", "minecraft:dirt")
    keeps = [f for op, f in bridge.sent if op == "plug_keep"]
    assert keeps[-1] == {"items": {cobble: 1}}
    assert bridge.sent[-2][0] == "plug_keep" and bridge.sent[-1][0] == "place_block"


def test_a_build_carrying_scaffolding_climbs_on_scaffolding_only(monkeypatch):
    # With scaffolding on hand, dirt and cobblestone are never footholds, steps or
    # bridges: a place scaffolding cannot make is a rule to fix, not a dirt step left
    # on the roof (run 20261121T180806599463Z).
    from dataclasses import replace

    from mclab import body_search
    from mclab import temporary_blocks as tb
    from mclab.companion_jobs import load_vanilla_pack

    b, _, _ = body()
    held = {
        "minecraft:scaffolding": 128,
        "minecraft:dirt": 40,
        "minecraft:cobblestone": 60,
    }
    monkeypatch.setattr(b, "inventory", lambda: held)
    monkeypatch.setattr(b, "world", lambda *p: "minecraft:air")
    b.build_cells = {(0, y, 0): "minecraft:cobblestone" for y in range(39)}
    b.temporary_ok = tb.eligible(load_vanilla_pack())
    b.terrain_policy = replace(cb.DIG_TERRAIN, allow_place=True)
    b.scaffold_only = True
    assert b.temporary_items() == ["minecraft:scaffolding"]
    assert b.temporary_items(scaffolding=False) == []
    policy = b._with_temporaries(b.terrain_policy)
    assert policy.material_allowlist == ()
    assert policy.scaffold_materials == ("minecraft:scaffolding",)
    rows = [{"slot": i, "item": k, "count": n} for i, (k, n) in enumerate(held.items())]
    edits = body_search.edit_payload(policy, rows, ())
    assert edits["materials"] == []
    assert edits["place_supply"] == edits["scaffold_supply"] == 128  # no solid places

    held["minecraft:scaffolding"] = 0  # all of it out in the world: still none
    assert b.temporary_items() == [] and b.temporary_items(scaffolding=False) == []


def test_a_prop_may_be_a_solid_block_while_the_build_steps_on_scaffolding_only(
    monkeypatch,
):
    # Run 20261121T222050257793Z: two hoppers facing each other each need a block
    # to click against. Scaffolding with a rail under it does not place, and under
    # scaffold_only the dirt held was no temporary, so both were skipped. A prop is
    # put down and taken back in the same placement: nothing is left behind.
    from mclab import temporary_blocks as tb
    from mclab.companion_jobs import load_vanilla_pack

    b, _, _ = body()
    held = {"minecraft:scaffolding": 64, "minecraft:dirt": 2}
    monkeypatch.setattr(b, "inventory", lambda: held)
    monkeypatch.setattr(b, "world", lambda *p: "minecraft:air")
    b.build_cells = {}
    b.temporary_ok = tb.eligible(load_vanilla_pack())
    b.scaffold_only = True
    assert b.temporary_items(scaffolding=False) == []
    assert b.temporary_items(scaffolding=False, prop=True) == ["minecraft:dirt"]


def test_plug_keep_is_a_body_op():
    from mclab.bridge import BODY_OPS

    assert "plug_keep" in BODY_OPS


def test_every_op_the_companion_sends_is_routed_to_the_body_mod():
    # An op missing from BODY_OPS goes to /lab/admin, which the play server (no
    # mcb-lab) refuses: the first armed fight on play would have raised.
    import re
    from pathlib import Path

    from mclab.bridge import BODY_OPS

    source = Path(cb.__file__).read_text(encoding="utf-8")
    sent = set(re.findall(r'self\.op\(\s*"(\w+)"', source))
    assert {"fight", "escape", "attack_entity", "break_block"} <= sent
    assert sent - BODY_OPS == set()


def test_a_hurt_drop_keeps_three_hearts_and_is_put_back(monkeypatch):
    b, _, _ = body()
    cases = ((20.0, 12), (15.0, 12), (12.0, 9), (9.0, 6), (6.0, 3), (4.0, 3))
    for health, drop in cases:
        monkeypatch.setattr(b, "health", lambda h=health: h)
        assert b.hurt_drop() == drop, health
    monkeypatch.setattr(b, "health", lambda: 20.0)
    before = b.movement_policy.max_drop
    with b.allowing_hurt_drops() as drop:
        assert b.movement_policy.max_drop == drop == 12
    assert b.movement_policy.max_drop == before


def test_a_hurt_body_eats_its_most_filling_food(monkeypatch):
    from mclab import build_runner as br

    b, _, _ = body()
    eaten = []
    monkeypatch.setattr(
        b, "eat", lambda item=None: eaten.append(item) or br.Result(True)
    )
    monkeypatch.setattr(
        b, "inventory", lambda: {"minecraft:bread": 2, "minecraft:cooked_beef": 1}
    )
    b.foods = {
        "minecraft:bread": {"nutrition": 5},
        "minecraft:cooked_beef": {"nutrition": 8},
    }
    monkeypatch.setattr(b, "food", lambda: 17)
    monkeypatch.setattr(b, "health", lambda: 20.0)
    assert not b.eat_when_needed() and not eaten  # not hurt
    monkeypatch.setattr(b, "health", lambda: 9.0)
    assert b.eat_when_needed() and eaten == ["minecraft:cooked_beef"]
    eaten.clear()
    monkeypatch.setattr(b, "food", lambda: 18)
    assert not b.eat_when_needed()  # hurt, but full enough to regenerate
    monkeypatch.setattr(b, "health", lambda: 20.0)
    monkeypatch.setattr(b, "food", lambda: 14)
    assert b.eat_when_needed() and eaten == ["minecraft:cooked_beef"]  # hungry
    monkeypatch.setattr(b, "food", lambda: 20)
    assert not b.eat_when_needed()  # full: cannot eat


def test_drops_seen_once_are_collected_even_when_out_of_sight_from_the_pit(monkeypatch):
    # Scaffold course 20261118T013316689139Z: from the pit floor a stack on its
    # steps, three up, was neither in sight nor within two blocks of height;
    # 3 of 64 scaffolding were left behind.
    b, _, _ = body()
    pos = [0.5, 0.0, 0.5]
    monkeypatch.setattr(b, "position", lambda: tuple(pos))
    monkeypatch.setattr(b, "wait_ticks", lambda n: None)
    floor = {"uuid": "a", "item": "minecraft:scaffolding", "position": [2.5, 0.0, 0.5]}
    step = {"uuid": "b", "item": "minecraft:scaffolding", "position": [4.5, 3.0, 0.5]}
    held = {"a", "b"}
    # The step stack is seen only from the rim, before the body goes down.
    views = iter([[floor, step]])
    monkeypatch.setattr(
        b,
        "entities",
        lambda: [e for e in next(views, [floor]) if e["uuid"] in held],
    )
    walked = []

    def walk(cell, radius):
        walked.append(cell)
        pos[:] = [cell[0] + 0.5, cell[1], cell[2] + 0.5]
        held.discard("a" if cell[0] == 2 else "b")
        return True

    monkeypatch.setattr(b, "walk_to_drop", walk)
    b.collect_drops([(0, 0, 0)], radius=8.0)
    assert walked == [(2, 0, 0), (4, 3, 0)]


def test_drops_seen_in_an_earlier_collect_are_still_collected(monkeypatch):
    # Scaffold course 20261118T014026041814Z: the take-down calls collect_drops once per
    # round; standing on the pit's steps the floor was out of sight and 4 were left.
    b, _, _ = body()
    pos = [0.5, 0.0, 0.5]
    monkeypatch.setattr(b, "position", lambda: tuple(pos))
    monkeypatch.setattr(b, "wait_ticks", lambda n: None)
    far = {"uuid": "f", "item": "minecraft:scaffolding", "position": [6.5, 0.0, 0.5]}
    views = iter([[far], [far]])  # in view while the drop wait looks, then gone
    monkeypatch.setattr(b, "entities", lambda: next(views, []))
    walked = []

    def walk(cell, radius):
        walked.append(cell)
        if len(walked) == 1:
            return False  # the first try is cut short
        pos[:] = [cell[0] + 0.5, cell[1], cell[2] + 0.5]
        return True

    monkeypatch.setattr(b, "walk_to_drop", walk)
    b.collect_drops([(0, 0, 0)], radius=8.0)
    b.collect_drops([(0, 0, 0)], radius=8.0)
    assert walked == [(6, 0, 0), (6, 0, 0)]
    b.collect_drops([(0, 0, 0)], radius=8.0)
    assert len(walked) == 2  # reached and not seen there: gone


def test_a_remembered_drop_last_seen_falling_is_forgotten_not_waited_on(monkeypatch):
    # Scaffold course 20261118T014508840305Z: a stack remembered mid-fall (or merged
    # into
    # another) used up every pickup of each round, and 17 of 64 were left behind.
    b, _, _ = body()
    pos = [0.5, 0.0, 0.5]
    monkeypatch.setattr(b, "position", lambda: tuple(pos))
    waits = []
    monkeypatch.setattr(b, "wait_ticks", lambda n: waits.append(n))
    ghost = {"uuid": "g", "item": "minecraft:scaffolding", "position": [3.5, 3.0, 0.5]}
    real = {"uuid": "r", "item": "minecraft:scaffolding", "position": [5.5, 0.0, 0.5]}
    views = iter([[ghost]])
    monkeypatch.setattr(
        b, "entities", lambda: next(views, [] if pos[0] == 5.5 else [real])
    )
    walked = []

    def walk(cell, radius):
        if cell == (3, 3, 0):
            return None  # above its ground: "still falling"
        walked.append(cell)
        pos[:] = [cell[0] + 0.5, cell[1], cell[2] + 0.5]
        return True

    monkeypatch.setattr(b, "walk_to_drop", walk)
    b.collect_drops([(0, 0, 0)], radius=8.0)
    assert walked == [(5, 0, 0)]
    assert len(waits) <= 3  # the drop wait, at most a couple of looks: not 16


def test_a_drop_resting_on_a_rim_edge_is_picked_up_from_beside_it(monkeypatch):
    # Scaffold course 20261118T015734978753Z: an item at a pit's rim rested on the next
    # block's edge; its own cell is over the pit, so it read as falling all leg.
    b, _, _ = body()
    pos = [0.5, 0.0, 0.5]
    monkeypatch.setattr(b, "position", lambda: tuple(pos))
    monkeypatch.setattr(b, "wait_ticks", lambda n: None)
    rim = {"uuid": "r", "item": "minecraft:scaffolding", "position": [3.9, 0.0, 0.5]}
    monkeypatch.setattr(b, "entities", lambda: [] if pos[0] == 4.5 else [rim])
    monkeypatch.setattr(b, "_stand_y", lambda x, y, z: -4 if x == 3 else 0)
    walked = []

    def walk(cell, radius):
        walked.append(cell)
        pos[:] = [cell[0] + 0.5, cell[1], cell[2] + 0.5]
        return True

    monkeypatch.setattr(b, "walk_to", walk)
    b.collect_drops([(0, 0, 0)], radius=8.0)
    assert walked and walked[-1][0] == 4 and walked[-1][1] == 0


def test_a_walk_planned_ahead_is_priced_with_what_the_body_holds(monkeypatch):
    """plan_ahead searches before the controller's first decide; with an empty
    inventory the planner got place_supply 0 and placing was off while the body
    held dirt and scaffolding (run 20261118T111231396410Z, 10,000-node walks)."""
    from mclab import body_search

    b, _, _ = body()
    b._set_inventory(
        [
            {"slot": 2, "item": "minecraft:scaffolding", "count": 40},
            {"slot": 7, "item": "minecraft:dirt", "count": 31},
        ]
    )
    b.terrain_policy = replace(
        b.terrain_policy,
        allow_place=True,
        allow_pillar=True,
        place_budget=64,
        material_allowlist=("minecraft:dirt",),
        scaffold_materials=("minecraft:scaffolding",),
    )

    class Bare:  # the controller as plan_ahead has it: never decided yet
        def __init__(self, goal, memory, *, terrain_policy, **_):
            self.terrain_policy, self.inventory = terrain_policy, ()

    monkeypatch.setattr(cb, "_GotoController", Bare)
    controller = b._goto_controller((5, 64, 5), None, 0.6, True, 5.0)
    edits = body_search.edit_payload(
        controller.terrain_policy, controller.inventory, ()
    )
    assert edits["allow_place"] and edits["materials"] == ["minecraft:dirt"]
    assert edits["scaffold_supply"] == 40


def test_tall_scaffolding_column_is_reachable_from_ground_stances():
    b, _, _ = body()
    scaff = {
        "block": "minecraft:scaffolding",
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0.875, 0, 1, 1, 1]],
    }
    air = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}
    stone = {
        "block": "minecraft:stone",
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0, 0, 1, 1, 1]],
    }
    cells = {}
    for x in range(-4, 5):
        for z in range(-4, 5):
            cells[(x, -1, z)] = stone
            for y in range(0, 13):
                cells[(x, y, z)] = air
    for y in range(0, 10):
        cells[(0, y, 0)] = scaff
    b._cells_now = lambda: cells
    b.precise_stance = True
    b.placing_scaffold = True
    b._obs["body"]["position"] = [2.5, 0.0, 0.5]
    assert b.can_place_here((0, 10, 0), 4.5)
    found = b.stances((0, 10, 0), 4.5)
    assert (2, 0, 0) in found


def test_walk_only_keeps_climbed_scaffolding_stance_and_eye_cell_sees_past_rim(
    monkeypatch,
):
    b, _, _ = body()
    scaff = {
        "block": "minecraft:scaffolding",
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0.875, 0, 1, 1, 1]],
    }
    air = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}
    stone = {
        "block": "minecraft:stone",
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0, 0, 1, 1, 1]],
    }
    cells = {}
    for x in range(-2, 4):
        for z in range(-2, 3):
            cells[(x, -1, z)] = stone
            for y in range(0, 11):
                cells[(x, y, z)] = air
    for y in range(0, 9):
        cells[(0, y, 0)] = scaff
    # Target (1, 7, 0) hanging under chain at (1, 8, 0): out of reach from ground y=0;
    # stance inside (0, 6, 0) has its eye inside (0, 7, 0), below the y=0.875 rim.
    cells[(1, 8, 0)] = stone
    monkeypatch.setattr(b, "_cells_now", lambda: cells)
    b._obs["body"]["position"] = [2.5, 0.0, 0.5]
    b.precise_stance = True
    b.walk_only = True
    b.required_support = (1, 8, 0)
    assert cb.interacts(cells, (0, 6, 0), (1, 7, 0), 4.5)
    walked = []

    def arrive(goal, **k):  # an arrival puts the body (and its eye) on the stance
        walked.append(goal)
        b._obs["body"]["position"] = [goal[0] + 0.5, goal[1], goal[2] + 0.5]
        return True

    monkeypatch.setattr(b, "_navigate", arrive)
    assert b.goto_stance((1, 7, 0), 4.5)
    assert walked == [(0, 6, 0)]


def test_stances_prefer_clean_feet_over_occupied_trapdoor(monkeypatch):
    b, _, _ = body()
    air = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}
    planks = {
        "block": "minecraft:birch_planks",
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0, 0, 1, 1, 1]],
    }
    trapdoor = {
        "block": "minecraft:oak_trapdoor",
        "fluid": "minecraft:empty",
        "collision_boxes": [[0.8125, 0, 0, 1, 1, 1]],
    }
    cells = {}
    for x in range(-2, 4):
        for z in range(-2, 4):
            cells[(x, -1, z)] = planks
            for y in range(0, 4):
                cells[(x, y, z)] = air
    # Both (1, 0, 0) (open trapdoor) and (0, 0, 0) (clean air) reach (2, 1, 0),
    # and neither is in walkable set when starting from roof y=5.
    cells[(1, 0, 0)] = trapdoor
    cells[(2, 2, 0)] = planks
    monkeypatch.setattr(b, "_cells_now", lambda: cells)
    monkeypatch.setattr(b, "_walkable", lambda *a: set())
    b._obs["body"]["position"] = [1.5, 5.0, 0.5]
    b.precise_stance = True
    ranked = b.stances((2, 1, 0), 4.5)
    assert (1, 0, 0) in ranked and (0, 0, 0) in ranked
    assert ranked.index((0, 0, 0)) < ranked.index((1, 0, 0))


def test_walk_only_fallback_tries_multiple_candidates_when_walk_empty(monkeypatch):
    b, _, _ = body()
    b.walk_only = True
    b._obs["body"]["position"] = [10.5, 5.0, 10.5]
    goals = [(1, 0, 0), (0, 0, 0), (0, 0, 1)]
    monkeypatch.setattr(b, "stances", lambda *a, **k: goals)
    monkeypatch.setattr(b, "_walkable", lambda *a: set())
    seen = []
    monkeypatch.setattr(
        b, "_navigate", lambda goal, **k: seen.append(goal) or goal == goals[1]
    )
    assert b.goto_stance((2, 1, 0), 4.5)
    assert seen == goals[:2]


# --- collect_drops waits only for items that are there -------------------------------


def _item(uuid, y, x=2.5):
    return {"uuid": uuid, "item": "minecraft:dirt", "position": [x, y, 0.5]}


def test_collect_drops_does_not_wait_when_nothing_dropped(monkeypatch):
    # Run 20261119T133618892664Z: 31 blind 40-tick waits (62 s) after breaks.
    b, bridge, _ = body()
    waits, notes = [], []
    monkeypatch.setattr(b, "wait_ticks", waits.append)
    bridge.record = lambda kind, data: notes.append(data)
    monkeypatch.setattr(b, "entities", lambda: [])
    b.collect_drops([(0, 0, 0)])
    assert waits == [] and notes == []


def test_collect_drops_ignores_items_out_of_range(monkeypatch):
    b, _, _ = body()
    waits = []
    monkeypatch.setattr(b, "wait_ticks", waits.append)
    monkeypatch.setattr(b, "entities", lambda: [_item("far", 0.0, x=50.5)])
    b.collect_drops([(0, 0, 0)], radius=8.0)
    assert waits == []


def test_collect_drops_stops_waiting_once_the_item_has_landed(monkeypatch):
    b, _, _ = body()
    waits = []
    monkeypatch.setattr(b, "wait_ticks", waits.append)
    heights = iter([3.0, 1.0, 0.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(b, "entities", lambda: [_item("a", next(heights, 0.0))])
    monkeypatch.setattr(b, "walk_to_drop", lambda cell, r: True)
    b.collect_drops([(0, 0, 0)], radius=8.0)
    assert 0 < sum(waits) < cb.DROP_WAIT_TICKS


def test_collect_drops_waiting_is_bounded_when_an_item_never_settles(monkeypatch):
    b, _, _ = body()
    waits, y = [], [1000.0]

    def entities():
        y[0] -= 1.0  # falls forever
        return [_item("a", y[0])]

    monkeypatch.setattr(b, "wait_ticks", waits.append)
    monkeypatch.setattr(b, "entities", entities)
    monkeypatch.setattr(b, "walk_to_drop", lambda cell, r: True)
    b.collect_drops([(0, 0, 0)], radius=8.0)
    assert sum(waits) <= cb.DROP_WAIT_TICKS + cb.MAX_PICKUPS * 5


def test_collect_drops_stops_when_the_item_was_picked_up_meanwhile(monkeypatch):
    b, _, _ = body()
    waits = []
    monkeypatch.setattr(b, "wait_ticks", waits.append)
    views = iter([[_item("a", 1.0, x=0.9)], []])  # picked up under the body
    monkeypatch.setattr(b, "entities", lambda: next(views, []))
    monkeypatch.setattr(b, "walk_to_drop", pytest.fail)
    b._known_drops = {}
    b.collect_drops([(0, 0, 0)], radius=8.0)
    assert sum(waits) <= 5


# --- goto_stance: per-candidate budget, memory --------------------------------------


def test_each_stance_candidate_gets_its_own_progress_budget(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(2, 0, 0), (3, 0, 1), (3, 0, 2)])
    seen = []

    def navigate(goal, *, progress=None, **kw):
        seen.append(progress)
        return False

    monkeypatch.setattr(b, "_navigate", navigate)
    assert b.goto_stance((2, 0, 2), 4.5) is False
    probes = seen[:3]
    assert len({id(p) for p in probes}) == 3  # none shared between candidates
    assert all(
        p["tick"] is None and p["limit_ticks"] == cb.CANDIDATE_PROGRESS_TICKS
        for p in probes
    )
    assert cb.CANDIDATE_PROGRESS_TICKS <= 200  # about 10 s


def test_stances_that_failed_are_not_retried_by_the_next_call(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(2, 0, 0), (3, 0, 1)])
    seen = []

    def navigate(goal, **kw):
        seen.append(goal)
        if goal == (3, 0, 1):
            b._obs["body"]["position"] = [18.5, 0.0, 18.5]  # moved before failing
        return False

    monkeypatch.setattr(b, "_navigate", navigate)
    assert not b.goto_stance((2, 0, 2), 4.5)
    assert not b.goto_stance((2, 0, 2), 4.5)  # from another spot, same target
    assert seen[:2] == [(2, 0, 0), (3, 0, 1)]
    assert (2, 0, 0) not in seen[2:] and (3, 0, 1) not in seen[2:]
    b._remember((0, 0, 0), "minecraft:stone", 100)  # the world changed: try again
    b.goto_stance((2, 0, 2), 4.5)
    assert (2, 0, 0) in seen[2:]


class _FakeController:
    def __init__(self):
        self.failed_edges = set()
        self.probe, self.mode, self.last_result = False, "following", None
        self.last_partial_walk, self.map_view = None, None
        self.goal_progress_ticks = None
        self.closed = False

    def close(self):
        self.closed = True


def _navigating_body(monkeypatch):
    b, bridge, _ = body()
    bridge.state = lambda: {}
    made, drove = [], []

    def make(*a, **k):
        made.append(_FakeController())
        return made[-1]

    def drive(bridge, controller, wall, profile=None):
        drove.append((set(controller.failed_edges), controller.goal_progress_ticks))
        controller.failed_edges.add(((0, 0, 0), (1, 0, len(drove))))
        return {"type": "navigation_stopped", "data": {"reason": "failed"}}

    monkeypatch.setattr(b, "_goto_controller", make)
    monkeypatch.setattr(cb, "drive", drive)
    monkeypatch.setattr(b, "observe", lambda: {})
    monkeypatch.setattr(b, "_hop_toward", lambda goal: None)
    return b, drove


def test_failed_edges_carry_across_the_candidates_of_one_goto(monkeypatch):
    b, drove = _navigating_body(monkeypatch)
    b._goto_edges = set()  # what goto_stance opens around its candidate loop
    b._navigate((2, 0, 0), reach=None, radius=0.6, probe=True)
    b._navigate((3, 0, 1), reach=None, radius=0.6, probe=True)
    assert drove[0][0] == set()
    assert drove[1][0] == {((0, 0, 0), (1, 0, 1))}


def test_failed_edges_do_not_leak_past_the_goto(monkeypatch):
    b, drove = _navigating_body(monkeypatch)
    b._navigate((2, 0, 0), reach=None, radius=0.6, probe=True)
    b._navigate((3, 0, 1), reach=None, radius=0.6, probe=True)
    assert drove[1][0] == set()


def test_every_walk_shares_the_body_s_failed_edge_memory(monkeypatch):
    b, _ = _navigating_body(monkeypatch)
    seen = []
    monkeypatch.setattr(
        cb,
        "drive",
        lambda bridge, controller, wall, profile=None: (
            seen.append(controller.edge_failures),
            {"type": "navigation_stopped", "data": {"reason": "failed"}},
        )[1],
    )
    b._navigate((2, 0, 0), reach=None, radius=0.6, probe=True)
    b._navigate((3, 0, 1), reach=None, radius=0.6, probe=True)
    assert seen[0] is seen[1] is b.edge_failures


def test_the_failed_edge_memory_forgets_a_move_whose_blocks_the_body_changed(
    monkeypatch,
):
    # A ban holds only while the blocks around the move are as they were when it
    # failed; the memory reads the body's map with its own recent edits on top.
    b, _ = _navigating_body(monkeypatch)
    edge = ((0, 1, 0), (1, 1, 0))
    b.edge_failures.fail(edge, 0)
    assert not b.edge_failures.stale(edge)
    b.memory.cells[(1, 0, 0)] = {"block": "minecraft:cobblestone"}
    assert b.edge_failures.stale(edge)


def test_the_candidate_budget_reaches_the_drive_as_its_progress_limit(monkeypatch):
    b, drove = _navigating_body(monkeypatch)
    progress = {"distance": math.inf, "tick": None, "limit_ticks": 180}
    b._navigate((2, 0, 0), reach=None, radius=0.6, probe=True, progress=progress)
    assert drove[0][1] == 180


def test_placement_sight_uses_the_body_real_eye(monkeypatch):
    # the server clicks from player.getEyePosition(): the body's real position, not
    # its cell centre. Standing at the edge of its cell the body sees a face that the
    # centre does not.
    b, _, _ = body()
    full = [[0, 0, 0, 1, 1, 1]]
    cells = {(1, 0, 0): {"block": "minecraft:dirt", "collision_boxes": full}}
    for y in range(1, 4):
        for x in (0, 1, 2):
            cells.setdefault((x, y, 0), {"block": "minecraft:air"})
    monkeypatch.setattr(b, "_cells_now", lambda: cells)
    target = (2, 0, 0)  # beside the dirt: its east face at x=2 is the only support
    b._obs["body"]["position"] = [1.5, 1.0, 0.5]  # eye at x=1.5: behind that face
    assert not b.sees_placement(target)
    b._obs["body"]["position"] = [2.2, 1.0, 0.5]  # past the plane
    assert b.sees_placement(target)
    assert not b.sees_placement(target, eye=(1.5, 2.62, 0.5))


LATTICE = {"block": "minecraft:scaffolding", "collision_boxes": []}


def test_a_bridge_block_is_placed_from_on_top_of_the_tower_beside_it():
    # On top of a tower the side face toward the next cell is under the feet; the
    # top face, clicked not sneaking along the heading, extends the run sideways
    # (vanilla ScaffoldingBlockItem; BodyBuild's bridge sources).
    tower = {(0, y, 0): LATTICE for y in range(6)}
    assert _sees(tower, (0, 6, 0), (1, 5, 0), scaffold=True)
    assert not _sees(tower, (0, 6, 0), (1, 5, 0))  # a plank needs that side face
    bridge = {**tower, (1, 5, 0): LATTICE}
    assert _sees(bridge, (0, 6, 0), (2, 5, 0), scaffold=True)  # past the run
    assert _sees(bridge, (1, 6, 0), (1, 5, 1), scaffold=True)  # an L turn
    assert not _sees(tower, (0, 6, 0), (8, 5, 0), scaffold=True)  # out of reach


def _roof_eave():
    """A tower at (0, 0..5, 0); a plank at (0, 6, -1) the eave cell (-1, 6, -1)
    hangs on by its west face. From the tower's top that face is turned away; one
    block west of the top it shows (house-large run 20261120T141244368734Z)."""
    b, _, memory = body()
    air = {"block": "minecraft:air", "collision_boxes": []}
    for x in range(-4, 5):
        for y in range(0, 10):
            for z in range(-4, 5):
                memory.cells[(x, y, z)] = air
    for y in range(6):
        memory.cells[(0, y, 0)] = LATTICE
    memory.cells[(0, 6, -1)] = {
        "block": "minecraft:oak_planks",
        "collision_boxes": [FULL],
    }
    b.precise_stance = True
    b._obs["body"]["position"] = [0.5, 6.0, 0.5]
    return b, (-1, 6, -1)


def test_a_cell_out_of_sight_from_the_tower_top_has_a_bridge_stance_beside_it():
    b, target = _roof_eave()
    assert b.stances(target, 4.5) == []
    found = b.bridge_stances(target, 4.5)
    assert (-1, 6, 0) in found
    assert all(
        b._cells_now()[(n[0], n[1] - 1, n[2])]["block"] == "minecraft:air"
        for n in found
    )
    assert (-1, 6, 0) not in b.bridge_stances(target, 4.5, avoid={(-1, 6, 0)})


def test_a_bridge_stance_needs_room_for_the_body():
    b, target = _roof_eave()
    b.memory.cells[(-1, 7, 0)] = {"block": "minecraft:stone", "collision_boxes": [FULL]}
    assert (-1, 6, 0) not in b.bridge_stances(target, 4.5)


def test_markers_point_at_the_block_acted_on_and_the_stance_walked_to(monkeypatch):
    from mclab.markers import Markers

    b, bridge, _ = body()
    sent = []
    client = type("C", (), {"send": lambda self, op, **f: sent.append(f)})()
    marks = Markers(client, lambda: True)
    b.markers = marks
    bridge.reply["place_block"] = ("done", {"placed": "minecraft:dirt"})
    b.place_block((1, 0, 1), "minecraft:dirt", "minecraft:dirt")
    assert marks.target == (1, 0, 1) and {"target": [1, 0, 1]} in sent
    monkeypatch.setattr(
        b, "_hop_toward", lambda goal: (_ for _ in ()).throw(StopIteration)
    )
    with contextlib.suppress(StopIteration):
        b._navigate((5, 0, 5), reach=None, radius=0.6)
    assert marks.goal == (5, 0, 5)


def test_prop_stances_and_can_place_with_prop_here_solve_hopper_room():
    # Exact layout of house-large hopper room: wall on z=0, ceiling at y=3,
    # target (2, 2, 1) hopper facing west, prop cell (1, 2, 1) air.
    b, _, _ = body()
    b.precise_stance = True
    cells = b.memory.cells
    for x in range(-1, 6):
        for z in range(-1, 6):
            for y in range(-1, 5):
                cells[(x, y, z)] = {"block": "minecraft:air", "collision_boxes": []}
            cells[(x, -1, z)] = {"block": "minecraft:stone", "collision_boxes": [FULL]}
            cells[(x, 3, z)] = {
                "block": "minecraft:birch_planks",
                "collision_boxes": [FULL],
            }
        for y in range(0, 3):
            cells[(x, y, 0)] = {
                "block": "minecraft:oak_planks",
                "collision_boxes": [FULL],
            }
    west = "minecraft:hopper[enabled=true,facing=west]"
    b.placing_state = west
    b._obs["body"]["position"] = [2.5, 0.0, 2.5]
    assert b.stances((2, 2, 1), 4.5) == []  # no solid west neighbour to click
    found = b.prop_stances((1, 2, 1), "minecraft:dirt", (2, 2, 1), west, 4.5)
    assert (2, 0, 2) in found
    assert b.can_place_with_prop_here((1, 2, 1), "minecraft:dirt", (2, 2, 1), west, 4.5)
    # West of the prop cell, the east face of (1, 2, 1) is hidden behind it.
    b._obs["body"]["position"] = [0.5, 0.0, 2.5]
    assert not b.can_place_with_prop_here(
        (1, 2, 1), "minecraft:dirt", (2, 2, 1), west, 4.5
    )


def test_can_reach_from_tower_rejects_eye_above_chain_down_face_and_wrong_side():
    b, _, _ = body()
    b.precise_stance = True
    cells = b.memory.cells
    for x in range(-3, 4):
        for z in range(-3, 4):
            for y in range(0, 12):
                cells[(x, y, z)] = {"block": "minecraft:air", "collision_boxes": []}
            cells[(x, -1, z)] = {"block": "minecraft:stone", "collision_boxes": [FULL]}
    # Chain target (0, 9, 0) hanging under (0, 10, 0) whose down face is at y=10.0.
    cells[(0, 10, 0)] = {
        "block": "minecraft:iron_chain",
        "collision_boxes": [[0.40625, 0.0, 0.40625, 0.59375, 1.0, 0.59375]],
    }
    b.required_support = {(0, 10, 0), (0, 8, 0)}
    b.placing_state = "minecraft:iron_chain[axis=y,waterlogged=false]"
    # Standing at feet y=9 puts eye at 10.62 > 10.0: cannot see the down face!
    tall = [(-1, h, 0) for h in range(0, 9)]
    assert b.can_reach_from_tower((0, 9, 0), 4.5, (-1, 9, 0), tall) == 0
    # Standing at feet y=6..8 puts eye below 10.0 and sees the down face soundly.
    short = [(-1, h, 0) for h in range(0, 6)]
    assert b.can_reach_from_tower((0, 9, 0), 4.5, (-1, 6, 0), short) == 2
    # Solid wall at z=1 blocks an outside tower at (0, *, 2) from reaching (0, 2, 0).
    for x in range(-3, 4):
        for y in range(0, 6):
            cells[(x, y, 1)] = {
                "block": "minecraft:oak_planks",
                "collision_boxes": [FULL],
            }
    cells[(0, 2, -1)] = {"block": "minecraft:oak_planks", "collision_boxes": [FULL]}
    b.required_support = None
    b.placing_state = "minecraft:oak_planks"
    outside = [(0, 0, 2), (0, 1, 2)]
    assert b.can_reach_from_tower((0, 2, 0), 4.5, (0, 2, 2), outside) == 0


def test_walkable_bfs_ranks_same_side_of_wall_before_walk_around_wall():
    # A wall at z=1 spans x=0..6 with a doorway only at x=7: from (1, 0, 2) inside,
    # (1, 0, 0) outside is crow-closer (2.0) than (3, 0, 2) inside (2.0), and walking
    # around to (1, 0, 0) takes ~14 steps vs 2 steps inside.
    b, _, _ = body()
    b.precise_stance = True
    cells = b.memory.cells
    for x in range(-1, 10):
        for z in range(-1, 5):
            for y in range(0, 4):
                cells[(x, y, z)] = {"block": "minecraft:air", "collision_boxes": []}
            cells[(x, -1, z)] = {"block": "minecraft:stone", "collision_boxes": [FULL]}
    for x in (*range(-1, 7), 8, 9):
        for y in (0, 1, 2):
            cells[(x, y, 1)] = {
                "block": "minecraft:oak_planks",
                "collision_boxes": [FULL],
            }
    for z in range(1, 5):
        for y in (0, 1, 2):
            cells[(-1, y, z)] = {
                "block": "minecraft:oak_planks",
                "collision_boxes": [FULL],
            }
            cells[(9, y, z)] = {
                "block": "minecraft:oak_planks",
                "collision_boxes": [FULL],
            }
    for x in range(-1, 10):
        for y in (0, 1, 2):
            cells[(x, y, 4)] = {
                "block": "minecraft:oak_planks",
                "collision_boxes": [FULL],
            }
    # Window opening at (2, 1, 1) (solid below at y=0 and lintel above at y=2):
    # blocks walking through the wall, while both inside (2, 0, 2) (walk=1) and
    # outside (2, 0, 0) (walk=14) can place into it. Inside must rank first!
    cells[(2, 1, 1)] = {"block": "minecraft:air", "collision_boxes": []}
    # Wooden door at x=7, z=1: BFS must treat it as passable, not a wall.
    door = {"block": "minecraft:oak_door", "collision_boxes": [[0, 0, 0, 1, 1, 0.2]]}
    cells[(7, 0, 1)] = dict(door)
    cells[(7, 1, 1)] = dict(door)
    b._obs["body"]["position"] = [1.5, 0.0, 2.5]
    walk = b._walkable(cells, (1, 0, 2))
    assert isinstance(walk, dict)
    assert walk[(3, 0, 2)] == 2
    assert walk[(1, 0, 0)] == 15  # 14 steps + 1 door-open penalty through (7, 0, 1)
    b.placing_state = "minecraft:oak_planks"
    ranked = b.stances((2, 1, 1), 4.5)
    assert ranked.index((2, 0, 2)) < ranked.index((2, 0, 0))
    assert b.walk_distance((2, 0, 2)) == 0


def test_pillar_to_a_released_cell_places_exactly_the_item_there(monkeypatch):
    # The build's own block put under the body in its own template cell (build_runner
    # _climb_out): the one cell is open to the pillar, the item is the template's
    # block, never scaffolding or a temporary swapped in for it.
    from dataclasses import replace

    b, _, _ = body()
    own, other = (0, 5, 0), (1, 5, 0)
    b.terrain_policy = replace(
        b.terrain_policy,
        allow_place=True,
        protected_positions=(own, other),
        scaffold_materials=("minecraft:scaffolding",),
    )
    b.temporary_ok = frozenset({"minecraft:dirt", "minecraft:scaffolding"})
    b.temporary_items = lambda scaffolding=True, prop=False: (
        ["minecraft:scaffolding", "minecraft:dirt"]
        if scaffolding
        else ["minecraft:dirt"]
    )
    seen = {}

    def fake_navigate(goal, reach=None, radius=0.6, probe=False, progress=None):
        policy = b._planner_terrain()
        seen.update(
            goal=goal,
            protected=set(policy.protected_positions),
            items=tuple(policy.material_allowlist),
            scaffold=tuple(policy.scaffold_materials),
        )
        return True

    monkeypatch.setattr(b, "_navigate", fake_navigate)
    assert b.pillar_to((0, 6, 0), "minecraft:oak_planks", 1, release=own)
    assert seen == {
        "goal": (0, 6, 0),
        "protected": {other},
        "items": ("minecraft:oak_planks",),
        "scaffold": (),
    }
    assert own in b.terrain_policy.protected_positions  # restored after


def test_pillar_to_disables_partial_walk_away_from_column(monkeypatch):
    from types import SimpleNamespace

    b, _, _ = body()
    seen = {}

    def fake_navigate(goal, reach=None, radius=0.6, probe=False, progress=None):
        seen["probe"] = probe
        seen["walk_only"] = b.walk_only
        seen["pillar_only"] = b._pillar_only
        return True

    monkeypatch.setattr(b, "_navigate", fake_navigate)
    assert b.pillar_to((0, 6, 0), "minecraft:dirt", 6)
    assert seen == {"probe": True, "walk_only": True, "pillar_only": True}
    # And with allow_partial_gain=False, an incomplete probe returns unreachable
    # rather than walking a partial route away from the column.
    ctrl = cb._GotoController.__new__(cb._GotoController)
    ctrl.probe = True
    ctrl.walk_partial = False
    ctrl.allow_partial_gain = False
    ctrl.mode = "incomplete"
    ctrl.route_steps = []
    ctrl.last_result = SimpleNamespace(
        status="incomplete", route=[(0, 0, 0), (5, 0, 0)]
    )
    ctrl.goal = (0, 10, 0)
    assert ctrl._probe_verdict() == "unreachable"


def test_fight_sends_only_the_given_fields_and_waits_long_enough():
    b, bridge, _ = body()
    bridge.reply["fight"] = (
        "done",
        {"kills": 1, "hits": 3, "ticks": 80, "end": "clear"},
    )
    result = b.fight(radius=12, floor=6, max_ticks=200, weapon="minecraft:iron_sword")
    assert result.ok and result.data["kills"] == 1
    assert bridge.sent[-1] == (
        "fight",
        {"radius": 12, "floor": 6, "max_ticks": 200, "weapon": "minecraft:iron_sword"},
    )
    b.fight(
        entity="u-1",
        radius=12,
        floor=6,
        max_ticks=200,
        anchor=(1.5, 64.0, 2.5),
        leash=6,
    )
    assert bridge.sent[-1][1]["anchor"] == [1.5, 64.0, 2.5]
    assert bridge.sent[-1][1]["entity"] == "u-1" and bridge.sent[-1][1]["leash"] == 6
    # the longest fight the body accepts (2400 ticks) must not time out the op
    assert cb.OP_TIMEOUTS["fight"] >= 2400 / 20
    b.fight(radius=12, floor=6, max_ticks=200, tactic={"style": "melee", "crit": True})
    assert bridge.sent[-1][1]["tactic"] == {"style": "melee", "crit": True}


def test_escape_sends_its_fields_and_waits_long_enough():
    b, bridge, _ = body()
    bridge.reply["escape"] = ("done", {"end": "clear", "ticks": 60})
    result = b.escape(radius=24, clear=16, max_ticks=200)
    assert result.ok and result.data["end"] == "clear"
    assert bridge.sent[-1] == ("escape", {"radius": 24, "clear": 16, "max_ticks": 200})
    assert cb.OP_TIMEOUTS["escape"] >= 1200 / 20


def test_wear_asks_the_body_to_dress_and_rows_keep_the_slots():
    b, bridge, _ = body()
    bridge.reply["wear"] = ("done", {"put_on": ["minecraft:iron_chestplate"],
                                     "worn": {"chest": "minecraft:iron_chestplate"},
                                     "armor": 6})  # fmt: skip
    result = b.wear()
    assert result.ok and result.data["armor"] == 6
    assert bridge.sent[-1] == ("wear", {})
    rows = [{"slot": 38, "item": "minecraft:iron_chestplate", "count": 1}]
    b._set_inventory(rows)
    assert b.rows() == rows and b.rows() is not b._rows


def test_trial_sends_the_scene_as_given():
    b, bridge, _ = body()
    bridge.reply["trial"] = ("done", {"given": 1, "summoned": []})
    spec = {
        "clear": 24,
        "heal": True,
        "summon": [{"type": "minecraft:pig", "offset": [3, 0, 0]}],
    }
    assert b.trial(**spec).ok
    assert bridge.sent[-1] == ("trial", spec)
