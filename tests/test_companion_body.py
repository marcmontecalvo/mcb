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

    def ingest(self, obs):
        self.ingested.append(obs)


class FakeBridge:
    """Answers every op with the event kind set in ``reply`` (default: done)."""

    def __init__(self):
        self.events, self.sent, self.reply = [], [], {}
        self.sequence = 0

    def send(self, op, **fields):
        self.sequence += 1
        self.sent.append((op, fields))
        kind, data = self.reply.get(op, ("done", {}))
        if kind != "silent":
            suffix = {"done": f"{op}_done", "failed": f"{op}_failed"}.get(kind, kind)
            self.events.append(
                {"type": suffix, "data": {"request_sequence": self.sequence, **data}}
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

    def navigate(goal, *, reach, radius):
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


def test_navigation_alerts_at_five_wall_seconds_even_when_ticks_are_slow(
    monkeypatch, capsys
):
    from types import SimpleNamespace

    now = [0.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: now[0])
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.mode = 100, "following"
    ctl.guard = cb.StuckGuard()
    obs = {"tick": 1, "body": {"position": [0.5, 62, 0.5]}}
    assert ctl.stop_reason(obs) is None
    now[0] = 4.99
    assert ctl.stop_reason(obs) is None
    now[0] = 5.0
    assert ctl.stop_reason(obs) == "stuck"
    assert "STALL 5.0s navigation idle" in capsys.readouterr().out
    # A live native edit can legitimately keep the feet stationary.
    obs["step_follower"] = {"state": "following", "issued": True}
    ctl.guard = SimpleNamespace(stuck=lambda *a: False)
    now[0] = 6
    assert ctl.stop_reason(obs) is None
    obs["step_follower"]["state"] = "edits_ready"
    now[0] = 11
    assert ctl.stop_reason(obs) == "stuck"


def test_probes_cannot_restart_the_five_second_idle_clock(monkeypatch, capsys):
    now = [0.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: now[0])
    shared = {"anchor": None, "since": 0.0}

    def probe(deadline):
        ctl = cb._GotoController.__new__(cb._GotoController)
        ctl.probe, ctl.deadline, ctl.mode, ctl.last_result = (
            True,
            deadline,
            "planning",
            None,
        )
        ctl.idle_state = shared
        return ctl

    obs = {"tick": 1, "body": {"position": [0.5, 62, 0.5]}}
    first = probe(2.5)
    assert first.stop_reason(obs) is None
    now[0] = 2.51
    assert first.stop_reason(obs) == "probed"
    second = probe(10)
    now[0] = 4.99
    assert second.stop_reason(obs) is None
    now[0] = 5.0
    assert second.stop_reason(obs) == "stuck"
    assert "STALL 5.0s navigation idle" in capsys.readouterr().out
    # Give the recovery action a new bounded opportunity to make progress.
    now[0] = 6.0
    assert probe(10).stop_reason(obs) is None


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


def test_navigation_enables_water_recovery_when_a_dry_walk_enters_deep_water(
    monkeypatch,
):
    from mclab.movement_policy import MovementPolicy

    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.mode = 10**12, "following"
    ctl.guard = cb.StuckGuard()
    ctl.movement_policy = MovementPolicy(allow_swim=False)
    monkeypatch.setattr(ctl, "_known_cells", lambda: {})
    obs = {
        "tick": 1,
        "body": {"position": [-17.14, 62, 71.33], "survival": {"in_water": True}},
    }
    assert ctl.stop_reason(obs) is None
    assert ctl.movement_policy.allow_swim


def test_navigation_idle_timer_resets_on_motion_but_not_a_waiting_step(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: now[0])
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.mode = 100, "following"
    ctl.guard = cb.StuckGuard()
    obs = {
        "tick": 1,
        "body": {"position": [0.5, 62, 0.5]},
        "step_follower": {"state": "following", "issued": False},
    }
    assert ctl.stop_reason(obs) is None
    now[0] = 4
    obs["body"]["position"][0] += 0.6
    assert ctl.stop_reason(obs) is None
    now[0] = 8.99
    assert ctl.stop_reason(obs) is None
    now[0] = 9
    assert ctl.stop_reason(obs) == "stuck"


def test_goto_stance_walks_known_stances_before_any_interaction_search(monkeypatch):
    b, _, _ = body()
    b._obs["body"]["position"] = [20.5, 0.0, 20.5]  # far: not in reach
    calls = []

    def navigate(goal, *, reach, radius, probe=False):
        if not probe:
            calls.append((goal, reach))
        return probe  # verdict: routable; the walk then fails

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

    def navigate(goal, *, reach, radius, probe=False):
        seen.append((goal, probe))
        return not (probe and goal == (2, 0, 0))  # no route to that first stance

    monkeypatch.setattr(b, "_navigate", navigate)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(2, 0, 0), (3, 0, 1)])
    assert b.goto_stance((2, 0, 2), 4.5) is True
    assert ((2, 0, 0), True) in seen
    assert ((2, 0, 0), False) not in seen  # never walked
    assert ((3, 0, 1), True) in seen and ((3, 0, 1), False) in seen


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
    assert ctl.stop_reason({"tick": 3}) == "probed"
    ctl.mode, ctl.last_result = "searching", None
    ctl.deadline = 0
    assert ctl.stop_reason({"tick": 4}) == "probed"  # no verdict: walk it as before


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
    assert ctl.stop_reason(observation) is None
    now[0] = 3.0
    assert ctl.stop_reason(observation) is None  # preserve the in-flight generation
    ctl.last_result.reason = "body plan pending"
    assert ctl.stop_reason(observation) == "probed"  # committed map can be reused
    ctl.last_result.reason = "planner map transfer"
    now[0] = 5.0
    assert ctl.stop_reason(observation) == "stuck"  # still bounded by wall time


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


def test_companion_plans_ask_for_doors_and_no_vines_and_old_requests_are_unchanged():
    from mclab.body_search import edit_payload
    from mclab.companion_body import DIG_TERRAIN, NATURAL_MOVEMENT
    from mclab.terrain_steps import TerrainPolicy

    edits = edit_payload(DIG_TERRAIN, [], [])
    assert edits["doors"] is True and edits["vines"] is False
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
    b._scan = lambda block: [(0, 0, 0), (9, 0, 0)]
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
        assert not b.movement_policy.allow_climb  # no vines up a swamp tree
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


def test_no_placing_can_keep_climbing_for_scaffolding_towers():
    b, _, _ = body()
    with b.no_placing(climb=True):
        assert b.movement_policy.allow_climb
        assert not b.terrain_policy.allow_place
    assert b.movement_policy.allow_climb


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


def test_a_body_floating_in_water_plans_with_swimming_on(monkeypatch):
    b, _, _ = body()
    assert not b._move_policy().allow_swim
    wet = {**b._obs.get("body", {}), "survival": {"in_water": True}}
    b._obs = {**b._obs, "body": wet}
    monkeypatch.setattr(cb, "standable", lambda cells, node: False)
    assert b._move_policy().allow_swim and not b.movement_policy.allow_swim
    monkeypatch.setattr(cb, "standable", lambda cells, node: True)
    assert not b._move_policy().allow_swim


def test_real_shallow_water_geometry_keeps_swimming_disabled():
    b, _, memory = body()
    p = b.position()
    memory.cells[p] = {
        "block": "minecraft:water",
        "fluid": "minecraft:water",
        "properties": {"level": "0"},
        "collision_boxes": [],
    }
    b._obs["body"]["survival"] = {"in_water": True}
    assert not b._move_policy().allow_swim


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

    def navigate(goal, *, reach, radius, probe=False):
        seen.append(dict(b._navigation_idle))
        b._navigation_idle.update(anchor=(1.0, 0.0, 1.0), since=now[0])
        now[0] += 3  # a probe, then the walk: one operation, one shared clock
        return probe and len(seen) < 2

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


def test_probe_and_walk_inside_one_op_share_the_idle_clock(monkeypatch):
    b, seen = _stale_idle_body(monkeypatch)
    monkeypatch.setattr(b, "stances", lambda *a, **k: [(3, 0, 3)])
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


def test_parked_probe_upload_extension_is_capped_even_while_the_body_drifts(
    monkeypatch,
):
    from types import SimpleNamespace

    now = [0.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: now[0])
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.probe, ctl.deadline, ctl.mode = True, 2.5, "planning"
    ctl.last_result = SimpleNamespace(status="searching", reason="planner map transfer")
    ctl.guard = cb.StuckGuard()
    seen = []
    for t in (1.0, 3.0, 4.9, 5.1):  # bobbing in water: 0.6 block each check
        now[0] = t
        x = 0.5 + 0.6 * len(seen)
        seen.append(ctl.stop_reason({"tick": 1, "body": {"position": [x, 62, 0.5]}}))
    assert seen == [None, None, None, "probed"]


@pytest.mark.parametrize(
    "y, deep, swim", [(0.42, False, False), (0.9, False, False), (0.0, True, True)]
)
def test_jumping_in_shallow_water_does_not_enable_swimming(y, deep, swim):
    from mclab.movement_policy import MovementPolicy

    memory = FakeMemory()
    water = {
        "block": "minecraft:water",
        "fluid": "minecraft:water",
        "properties": {"level": "0"},
        "collision_boxes": [],
    }
    memory.cells[(0, 0, 0)] = water
    if deep:  # the floor is gone: a pond, not a puddle
        memory.cells[(0, -1, 0)] = water
    ctl = cb._GotoController.__new__(cb._GotoController)
    ctl.deadline, ctl.mode = 10**12, "following"
    ctl.guard = cb.StuckGuard()
    ctl.movement_policy = MovementPolicy(allow_swim=False)
    ctl._known_cells = lambda: memory.cells
    obs = {
        "tick": 1,
        "body": {"position": [0.5, y, 0.5], "survival": {"in_water": True}},
    }
    ctl.stop_reason(obs)
    assert ctl.movement_policy.allow_swim is swim
