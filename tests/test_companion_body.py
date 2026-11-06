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
    ctl.mode = "unreachable"  # a fresher yes beats the stale no
    ctl.last_result = RouteResult("reached", ((0, 0, 0),), 0, 0, "ok")
    assert ctl.stop_reason({"tick": 3}) == "probed"
    ctl.mode, ctl.last_result = "searching", None
    ctl.deadline = 0
    assert ctl.stop_reason({"tick": 4}) == "probed"  # no verdict: walk it as before


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
