# ruff: noqa: E501
from mclab.companion_jobs import load_vanilla_pack
from mclab.gather_fake import FakeGatherBody
from mclab.gather_options import GatherOptions
from mclab.gather_runner import GatherState, _Exec
from mclab.gather_strip import Strip, border_step
from mclab.gather_tasks import Gather
from mclab.knowledge import best_y

K = load_vanilla_pack()
M = "minecraft:"
IRON, COAL = M + "iron_ore", M + "coal_ore"
PICK = {M + "wooden_pickaxe": 1, M + "stone_pickaxe": 1}
AIR = M + "air"
TOP = 30  # surface: iron's best height is 16


def world(ores):
    w = {
        (x, y, z): M + "stone"
        for x in range(-4, 60)
        for y in range(8, TOP + 1)
        for z in range(-4, 40)
    }
    w.update(ores)
    return w


def run(opts, count, ores):
    body = FakeGatherBody(K, world(ores), dict(PICK), pos=(3, TOP + 1, 3))
    ex = _Exec(body, K, GatherState(), 64, opts=opts)
    task = Gather(IRON, count, "pickaxe", "stone", M + "raw_iron", count)
    return ex, body, ex.gather(task)


def opts(**kw):
    return GatherOptions(ore_vision=False, strip_leg=16, strip_max_legs=2, **kw)


def test_target_height_and_border_helpers():
    assert best_y(K, IRON, TOP - 4) == 16
    assert [border_step(v) for v in (0, 1, 8, 9, 15, 17, -1)] == [0, -1, -8, 7, 1, -1, 1]


def test_strip_descends_tunnels_the_border_and_mines_iron_and_coal():
    ores = {(17, 16, 10): IRON, (17, 17, 14): IRON, (15, 17, 12): COAL, (15, 16, 13): COAL}
    ex, body, res = run(opts(), 99, ores)
    assert res.reason == "leg_limit" and body.inv[M + "raw_iron"] == 2  # legs run out
    assert body.inv.get(M + "coal", 0) >= 1  # greedy takes the coal too
    air = lambda c: body.world(*c) == AIR  # noqa: E731
    # leg along the x = 16 border, 2 high, one wide
    assert all(air((16, y, z)) for z in range(3, 20) for y in (16, 17))
    assert not air((16, 18, 10)) and not air((16, 15, 10))
    # connector along z = 19 to the next chunk border x = 32, then the leg back
    assert all(air((x, y, 19)) for x in range(16, 33) for y in (16, 17))
    assert all(air((32, y, z)) for z in range(3, 20) for y in (16, 17))
    assert body.position()[1] == 16


def test_specific_leaves_coal_and_count_stops_the_strip():
    ores = {(17, 16, 10): IRON, (15, 17, 12): COAL, (17, 16, 14): IRON}
    ex, body, res = run(opts(ore_collect="specific"), 1, ores)
    assert res.ok and body.inv[M + "raw_iron"] == 1
    assert body.inv.get(M + "coal", 0) == 0 and body.world(15, 17, 12) == COAL
    assert body.world(17, 16, 14) == IRON  # the strip ended at the count


def test_no_pickaxe_is_missing_tool():
    body = FakeGatherBody(K, world({}), {}, pos=(3, TOP + 1, 3))
    ex = _Exec(body, K, GatherState(), 64, opts=opts())
    task = Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1)
    assert ex.gather(task).reason == "missing_tool"


def notes(body):
    return [r for r in body.log if isinstance(r, dict) and "gather" in r]


def test_gravel_ceiling_is_mined_and_the_leg_continues():
    class Falling(FakeGatherBody):
        def break_block(self, pos, tool=None, drops_optional=False):
            res = super().break_block(pos, tool, drops_optional)
            up = (pos[0], pos[1] + 1, pos[2])
            if res.ok and self.w.get(up, "").endswith("gravel"):
                self.w[pos], self.w[up] = self.w[up], AIR  # the column drops one
            return res

    w = world({(16, 18, 10): M + "gravel", (16, 19, 10): M + "gravel"})
    body = Falling(K, w, dict(PICK), pos=(3, TOP + 1, 3))
    ex = _Exec(body, K, GatherState(), 64, opts=opts())
    task = Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1)
    assert ex.gather(task).reason == "leg_limit"
    assert all(body.world(16, y, 15) == AIR for y in (16, 17))  # past the gravel
    assert body.world(16, 19, 10) == M + "gravel"  # the ceiling is left alone


def test_blocked_connector_turns_the_other_way_and_legs_are_logged():
    body = FakeGatherBody(K, world({(17, 17, 20): M + "lava"}), dict(PICK), pos=(3, TOP + 1, 3))
    ex = _Exec(body, K, GatherState(), 64, opts=opts(strip_turn=3))
    task = Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1)
    res = ex.gather(task)
    assert res.reason == "leg_limit" and not res.ok
    assert body.world(13, 16, 19) == AIR and body.world(13, 16, 10) == AIR  # leg back
    ends = [(r["leg"], r["why"]) for r in notes(body) if r["gather"] == "strip_leg_end"]
    assert ends[:3] == [(0, "leg_done"), ("0c", "hazard:lava"), ("0c", "leg_done")]
    assert sum(r["gather"] == "strip_leg_start" for r in notes(body)) == len(ends)


def test_no_planned_dig_after_the_strip():
    body = FakeGatherBody(K, world({}), dict(PICK), pos=(3, TOP + 1, 3), can_dig=True)
    ex = _Exec(body, K, GatherState(), 64, opts=opts())
    task = Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1)
    assert ex.gather(task).reason == "leg_limit"
    assert not [r for r in body.log if isinstance(r, tuple) and r[0] == "dig_to"]


def deep(veins, can_dig):
    body = FakeGatherBody(K, world(veins), dict(PICK), pos=(3, TOP + 1, 3), can_dig=can_dig)
    calls = []
    real = body.dig_to

    def dig_to(feet):
        calls.append((feet, body.position()[1]))
        return real(feet)

    body.dig_to = dig_to
    ex = _Exec(body, K, GatherState(), 64, opts=GatherOptions(ore_vision=True))
    task = Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1)
    return body, calls, ex.gather(task)


def test_deep_ore_descends_first_then_one_short_plan():
    body, calls, res = deep({(10, 12, 3): IRON}, True)
    assert res.ok and body.inv[M + "raw_iron"] == 1
    assert len(calls) == 1 and calls[0][1] <= 12 + 3  # planned only after the staircase


def test_failed_ore_is_tried_once_nearest_3d_first():
    veins = {(20, 26, 3): IRON, (21, 26, 3): IRON, (20, 26, 4): IRON, (5, 12, 3): IRON}
    body, calls, res = deep(veins, False)
    tops = [c[0] for c in calls]
    assert tops[0] == (20, 27, 3) and tops.count((20, 27, 3)) == 1  # 3D-nearest, once
    assert not {(21, 27, 3), (20, 27, 4)} & set(tops)  # not once per cell
    assert not res.ok


def test_vision_off_short_descent_logs_retries_and_never_digs_to_buried_ore():
    # run 20261113T172130173999Z: silent None, then dig_to_stone x-rayed buried ore
    class Short(FakeGatherBody):
        def walk_to(self, pos, radius=1.5):  # the staircase never gets down
            return False if pos[1] < TOP else super().walk_to(pos, radius)

    body = Short(K, world({(20, 16, 10): IRON}), dict(PICK), pos=(3, TOP + 1, 3), can_dig=True)
    ex = _Exec(body, K, GatherState(), 64, opts=opts())
    res = ex.gather(Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1))
    assert res.reason.startswith("descent_failed:") and not res.ok
    assert [r["gather"] for r in notes(body)].count("strip_skipped") == 3
    assert not [r for r in body.log if isinstance(r, tuple) and r[0] == "dig_to"]


def test_known_deep_ore_stairs_down_before_one_plan():
    # run 20261113T171138236559Z: exposed ore 20+ below went straight to a capped plan
    w = world({(10, 10, 3): IRON, (10, 10, 4): AIR})
    body = FakeGatherBody(K, w, dict(PICK), pos=(3, TOP + 1, 3))
    ys = []
    real = body.goto_stance
    body.goto_stance = lambda *a, **k: (ys.append(body.position()[1]), real(*a, **k))[1]
    ex = _Exec(body, K, GatherState(), 64, opts=GatherOptions(ore_vision=True))
    ex.gather(Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1))
    assert ys and ys[0] <= 10 + 3


def test_inventory_full_is_the_real_slot_state_not_a_guess():
    ores = {(17, 16, 10): IRON}
    for junk, stops in ((34, True), (10, False)):  # 2 pickaxe slots + junk stacks
        inv = dict(PICK) | {M + "cobblestone": 64 * junk}
        body = FakeGatherBody(K, world(ores), inv, pos=(3, TOP + 1, 3))
        ex = _Exec(body, K, GatherState(), 64, opts=opts())
        task = Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1)
        res = Strip(ex, task, 1).run()
        assert (res.data["stop"] == "inventory_full") is stops, res
        assert (body.inv.get(M + "raw_iron", 0) == 0) is stops


def test_floor_hole_is_bridged_not_a_leg_end():
    # runs 20261116T124031122360Z, 20261114T203457845925Z: legs ended no_floor at 17/64
    inv = dict(PICK) | {M + "cobblestone": 4}
    w = world({(16, 15, 8): AIR, (16, 15, 9): AIR})
    body = FakeGatherBody(K, w, inv, pos=(3, TOP + 1, 3))
    ex = _Exec(body, K, GatherState(), 64, opts=opts())
    ex.gather(Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1))
    ends = [r["why"] for r in notes(body) if r["gather"] == "strip_leg_end"]
    assert ends[0] == "leg_done" and body.world(16, 15, 8) == M + "cobblestone"


def test_vein_cell_refused_before_its_neighbour_is_mined_is_retried():
    """Run 20261116T124031122360Z: iron at (122,19,-64) under the tunnel floor and a 2x2
    vein beside it. The upper vein cell has no stance from the tunnel (the ceiling
    blocks the sight line), and the lower one is blocked by the upper. Once the floor
    iron is mined the body drops into the hole and sees the upper cell; the cell
    refused first must be tried again."""
    w = {(x, y, z): M + "stone" for x in range(115, 130) for y in range(15, 24) for z in range(-70, -58)}
    w.update({(x, y, -64): AIR for x in range(115, 130) for y in (20, 21)})
    w.update({(x, y, -63): IRON for x in (122, 123) for y in (19, 20)})
    w[(122, 19, -64)] = IRON
    body = FakeGatherBody(K, w, dict(PICK), pos=(122, 20, -64), strict_stance=True)
    ex = _Exec(body, K, GatherState(), 64, opts=opts())
    Strip(ex, Gather(IRON, 99, "pickaxe", "stone", M + "raw_iron", 99), 99).mine()
    assert body.inv[M + "raw_iron"] == 5
    assert IRON not in {body.world(x, y, -63) for x in (122, 123) for y in (19, 20)}


def test_bridge_places_from_the_current_stance_when_no_other_stance_exists():
    # run 20261116T135356630645Z: no_floor on a cave floor beside the body, bridge placed nothing
    class NoStance(FakeGatherBody):
        def goto_stance(self, *a, **k):
            return False

    body = NoStance(K, world({(16, 15, 6): AIR}), dict(PICK) | {M + "cobblestone": 4}, pos=(16, 16, 5))
    ex = _Exec(body, K, GatherState(), 64, opts=opts())
    strip = Strip(ex, Gather(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1), 1)
    assert strip.bridge((0, 1)) and body.world(16, 15, 6) == M + "cobblestone"


def test_failed_approach_to_the_chunk_border_still_runs_the_legs():
    body = FakeGatherBody(K, world({}), dict(PICK), pos=(3, TOP + 1, 3))
    ex = _Exec(body, K, GatherState(), 64, opts=opts())
    strip = Strip(ex, Gather(IRON, 99, "pickaxe", "stone", M + "raw_iron", 99), 99)
    real, calls = strip.tunnel, []
    strip.tunnel = lambda d, n: "no_floor" if not calls.append(d) and len(calls) == 1 else real(d, n)
    strip.digger.descend(16)
    assert strip.serpentine() == "legs_done"
    assert "strip_leg_start" in [r["gather"] for r in notes(body)]


def test_strip_that_dug_no_tunnel_reports_its_own_stop_reason(monkeypatch):
    from mclab.gather_dig import Digger

    step = Digger.step
    monkeypatch.setattr(Digger, "step", lambda self, d, tunnel: "no_floor" if tunnel else step(self, d, tunnel))
    ex, body, res = run(opts(), 99, {})
    assert res.reason == "no_floor"
