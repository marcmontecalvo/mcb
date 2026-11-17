import math

import pytest
from test_build_plan import AIR, BLOCKS, HOUSE
from test_build_runner import FakeBody

from mclab import build_plan as bp
from mclab import build_runner as br
from mclab import site_prep as sp
from mclab.progress_watch import ProgressWatch, StallError, WatchedBody

DIRT = "minecraft:dirt"
STONE = "minecraft:stone"
LOG = "minecraft:oak_log"
LEAVES = "minecraft:oak_leaves"
WATER = "minecraft:water[level=0]"


def template(rotation=0, mirror=False, origin=(1, 0, 1)):
    return bp.Template.from_build(HOUSE, origin, rotation, mirror)


def extent(t):
    xs, zs = [p[0] for p in t.cells], [p[2] for p in t.cells]
    return min(xs), max(xs), min(zs), max(zs)


@pytest.mark.parametrize("margin", [0, 1, 2, 10])
@pytest.mark.parametrize(
    "rotation,mirror", [(0, False), (90, False), (180, True), (270, True)]
)
def test_site_covers_footprint_plus_margin(margin, rotation, mirror):
    t = template(rotation, mirror)
    x0, x1, z0, z1 = extent(t)
    s = sp.site_area(t, margin)
    assert (s.x0, s.x1, s.z0, s.z1) == (
        x0 - margin,
        x1 + margin,
        z0 - margin,
        z1 + margin,
    )
    assert s.ground_y == min(p[1] for p in t.cells) - 1
    assert len(list(s.columns())) == (x1 - x0 + 1 + 2 * margin) * (
        z1 - z0 + 1 + 2 * margin
    )


def test_rotation_swaps_footprint_axes():
    a, b = extent(template(0)), extent(template(90))
    assert (a[1] - a[0], a[3] - a[2]) == (b[3] - b[2], b[1] - b[0])


@pytest.mark.parametrize("bad", [-1, -10, 1.5, "1", True])
def test_invalid_margin_rejected(bad):
    with pytest.raises(ValueError):
        sp.site_area(template(), bad)


def mixed_world(t, margin=1):
    s = sp.site_area(t, margin)
    w = {}
    for x in range(s.x0 - 7, s.x1 + 4):
        for z in range(s.z0 - 3, s.z1 + 4):
            for y in range(s.ground_y - 3, s.top_y + 4):
                w[(x, y, z)] = (
                    STONE if y < s.ground_y else (STONE if y == s.ground_y else AIR)
                )
    return s, w


def prepare(t, w, inv, margin=1, protected=(), refill=None):
    body = FakeBody(w, inv, BLOCKS, pos=(t.origin[0], t.origin[1], t.origin[2]))
    report = br.run_build(
        t,
        body,
        BLOCKS,
        survey=False,
        site_margin=margin,
        protected=protected,
        on_need_materials=refill,
        max_steps=5000,
    )
    return body, report


def test_site_retries_early_no_stance_after_surrounding_ground_is_ready():
    from types import SimpleNamespace

    t = template()
    site, world = mixed_world(t)
    cell = (site.x0, site.ground_y, site.z0)
    world[cell] = AIR
    body = FakeBody(world, {DIRT: 10}, BLOCKS, pos=(cell[0] - 1, cell[1] + 1, cell[2]))
    report = br.BuildReport()
    report.skipped[cell] = "no_stance"
    runner = SimpleNamespace(
        report=report, max_steps=100, fails={cell: 3}, avoid={cell: set()}
    )
    result = sp.prepare_site(t, body, runner, site)
    assert result.stop == "ready"
    assert world[cell] == DIRT
    assert cell not in report.skipped


def test_a_skipped_fill_is_never_buried_and_is_retried_bottom_up():
    # run 20261117T043111369210Z: a pond-bottom fill was skipped from a far stance
    # (state_unreachable); the next round filled the cell above it, burying it,
    # and the site ended blocked
    from types import SimpleNamespace

    t = template()
    site, world = mixed_world(t)
    x, g, z = site.x0, site.ground_y, site.z0
    low, top = (x, g - 1, z), (x, g, z)
    world[low], world[top] = AIR, AIR
    body = FakeBody(world, {DIRT: 10}, BLOCKS, pos=(x - 1, g + 1, z))
    report = br.BuildReport()
    report.skipped[low] = "state_unreachable"
    held = sp._round_plan(site, t, body, frozenset(), report.skipped)
    assert top not in held.fills and low in held.held
    runner = SimpleNamespace(
        report=report, max_steps=100, fails={low: 3}, avoid={low: set()}
    )
    result = sp.prepare_site(t, body, runner, site)
    assert result.stop == "ready"
    assert world[low] == DIRT and world[top] == DIRT
    assert low not in report.skipped


def test_leaf_in_future_house_cell_is_cleared_before_filling_below_it():
    t = template()
    site, world = mixed_world(t)
    leaf = next(
        p
        for p, state in t.cells.items()
        if p[1] == site.ground_y + 1 and state not in bp.AIR
    )
    hole = (leaf[0], site.ground_y, leaf[2])
    world[leaf], world[hole] = LEAVES, AIR
    body = FakeBody(world, {DIRT: 10}, BLOCKS, pos=(leaf[0] - 1, leaf[1], leaf[2]))
    place = body.place_block

    def guarded_place(pos, *args, **kwargs):
        if pos == hole:
            assert world[leaf] in bp.AIR, "leaf occludes the fill placement"
        return place(pos, *args, **kwargs)

    body.place_block = guarded_place
    from types import SimpleNamespace

    runner = SimpleNamespace(report=br.BuildReport(), max_steps=100)
    result = sp.prepare_site(t, body, runner, site)
    assert result.stop == "ready"
    assert world[hole] == DIRT


def test_mixed_site_becomes_level():
    t = template()
    s, w = mixed_world(t)
    g = s.ground_y
    cx = (s.x0 + s.x1) // 2
    w[(s.x0, g + 1, s.z0)] = LOG
    w[(s.x0, g + 2, s.z0)] = LOG
    w[(s.x0, g + 3, s.z0)] = LEAVES  # tree on the margin
    w[(s.x1, g + 1, s.z1)] = DIRT  # raised dirt
    w[(s.x1, g, s.z1)] = DIRT
    w[(cx, g, s.z0)] = AIR  # hole
    w[(cx, g, s.z0 + 1)] = WATER  # water at ground level
    w[(s.x0 - 6, g + 1, s.z0)] = LOG  # outside the area: untouched
    w[(s.x0 - 6, g, s.z0 - 3)] = AIR
    body, report = prepare(t, w, {DIRT: 50})
    assert report.site["stop"] == "ready", report.site
    for x, z in s.columns():
        assert bp.parse_state(w[(x, g, z)])[0] not in bp.FLUIDS | bp.AIR
        for y in range(g + 1, s.top_y + 1):
            if not s.contains((x, y, z)) or (x, y, z) in t.cells:
                continue
            assert w[(x, y, z)] in bp.AIR, (x, y, z)
    assert report.site["filled"] >= 2 and report.site["cleared"] >= 1
    assert w[(s.x0 - 6, g + 1, s.z0)] == LOG and w[(s.x0 - 6, g, s.z0 - 3)] == AIR


def test_protected_cells_survive_and_are_reported():
    t = template()
    s, w = mixed_world(t)
    chest = (s.x0, s.ground_y + 1, s.z0)
    w[chest] = "minecraft:chest"
    body, report = prepare(t, w, {DIRT: 50}, protected=[chest])
    assert w[chest] == "minecraft:chest"
    assert report.stop == "site_not_ready" and report.site["stop"] == "blocked"
    assert "protected" in report.site["blocked"][str(chest)]


def test_insufficient_fill_is_not_success():
    t = template()
    s, w = mixed_world(t)
    w[(s.x0, s.ground_y, s.z0)] = AIR
    body, report = prepare(t, w, {DIRT: 0})
    assert report.stop == "site_not_ready"
    assert report.site["stop"] == "need_materials" and report.site["needed"]


def test_unknown_terrain_is_not_success():
    t = template()
    s, w = mixed_world(t)
    del w[(s.x0, s.ground_y + 1, s.z0)]
    body, report = prepare(t, w, {DIRT: 5})
    assert report.stop == "site_not_ready" and report.site["unknown"] >= 1


def test_forbidden_edit_is_reported_blocked():
    t = template()
    s, w = mixed_world(t)
    cell = (s.x0, s.ground_y + 1, s.z0)
    w[cell] = LOG
    body = FakeBody(w, {DIRT: 5}, BLOCKS, pos=(1, 0, 1), fail={cell: ("forbidden", 99)})
    report = br.run_build(t, body, BLOCKS, survey=False, site_margin=1)
    assert report.stop == "site_not_ready" and str(cell) in report.site["blocked"]
    assert w[cell] == LOG


def test_site_then_build_finishes():
    t = template()
    s, w = mixed_world(t)
    w[(s.x0, s.ground_y + 1, s.z0)] = LOG
    inv = {i["item"]: 100 for i in BLOCKS.values()}
    body, report = prepare(t, w, inv)
    assert report.stop == "done" and report.site["stop"] == "ready"
    assert bp.diff(t, body.world).places == []


# --- progress watch ---------------------------------------------------------
class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class Pit:
    pos = (0, 0, 0)
    escaped = 0

    def position(self):
        return self.pos

    def break_block(self, pos):
        return br.Result(False, "no_progress")

    def escape_hole(self, target):
        self.escaped += 1
        return False


def test_stall_over_five_seconds_recovers_then_fails_visibly():
    clock, lines, body = Clock(), [], Pit()
    watch = ProgressWatch(clock=clock, log=lines.append)
    wb = WatchedBody(body, watch)
    wb.break_block((1, 1, 1))  # starts the clock
    with pytest.raises(StallError):
        for _ in range(10):
            clock.t += 6
            wb.break_block((1, 1, 1))
    assert body.escaped == 2 and len(watch.events) == 3
    assert all(line.startswith("STALL") for line in lines)


def test_one_thirty_second_pause_stops_the_run():
    clock, body = Clock(), Pit()
    wb = WatchedBody(body, ProgressWatch(clock=clock, log=lambda m: None))
    wb.break_block((1, 1, 1))
    clock.t += 31
    with pytest.raises(StallError, match="dead"):
        wb.break_block((1, 1, 1))


def test_successful_escape_resets_stall_streak_and_records_outcome():
    clock, body = Clock(), Pit()
    body.escape_hole = lambda target: True
    watch = ProgressWatch(clock=clock, log=lambda m: None)
    watch.check(body, "walk_to", (1, 0, 0), False)
    for _ in range(5):
        clock.t += 6
        watch.check(body, "walk_to", (1, 0, 0), False)
    assert len(watch.events) == 5
    assert all(e["recovery"] == "succeeded" for e in watch.events)
    assert watch._streak == watch._spot_n == 0


def test_vines_in_margin_are_required_site_work():
    t = template()
    s, w = mixed_world(t)
    p = (s.x0, s.ground_y + 1, s.z0)
    w[p] = "minecraft:vine"
    assert p in sp.plan_site(s, t, lambda *q: w.get(q)).breaks


def test_seagrass_under_a_pond_is_filled_through_not_built_on():
    # seagrass is not REPLACEABLE by name, but it has no collision box to click
    t = template()
    s, w = mixed_world(t)
    x, g, z = s.x0, s.ground_y, s.z0
    w[(x, g, z)], w[(x, g - 1, z)], w[(x, g - 2, z)] = (
        WATER,
        WATER,
        "minecraft:seagrass",
    )
    by_name = sp.plan_site(s, t, lambda *q: w.get(q)).fills
    assert [p for p in by_name if p[::2] == (x, z)] == [(x, g - 1, z), (x, g, z)]

    def face(*q):
        state = w.get(q) or ""
        return not any(n in state for n in ("air", "water", "seagrass"))

    real = sp.plan_site(s, t, lambda *q: w.get(q), face=face).fills
    column = [p for p in real if p[::2] == (x, z)]
    assert column == [(x, g - 2, z), (x, g - 1, z), (x, g, z)]  # lowest first


def test_short_wait_or_progress_is_not_a_stall():
    clock, lines, body = Clock(), [], Pit()
    watch = ProgressWatch(clock=clock, log=lines.append)
    wb = WatchedBody(body, watch)
    wb.break_block((1, 1, 1))
    clock.t += 4.9
    wb.break_block((1, 1, 1))
    clock.t += 100  # moving counts as progress
    body.pos = (5, 0, 0)
    wb.break_block((1, 1, 1))
    assert lines == [] and watch.events == []


def test_unreachable_unknown_cell_is_tried_once_and_reported():
    t = template()
    s, w = mixed_world(t)
    cell = (s.x0, s.ground_y + 1, s.z0)
    del w[cell]
    body = FakeBody(w, {DIRT: 5}, BLOCKS, pos=(1, 0, 1))
    walks = []
    body.walk_to = lambda p, *a, **k: walks.append(p) or False
    report = br.run_build(t, body, BLOCKS, survey=False, site_margin=1)
    assert report.stop == "site_not_ready" and len(walks) <= 2
    assert report.site["blocked"].get(str(cell)) == "unreachable"


def test_different_unreachable_targets_do_not_escalate_to_a_stall_failure():
    clock, body = Clock(), Pit()
    watch = ProgressWatch(clock=clock, log=lambda m: None, max_stalls=3)
    wb = WatchedBody(body, watch)
    wb.break_block((1, 1, 1))
    for i in range(6):
        clock.t += 6
        wb.break_block((i, 1, 1))  # a new target each time: bounded failures
    assert len(watch.events) == 6


def test_fill_grows_outward_from_the_body():
    t = template()
    s, w = mixed_world(t)
    for x, z in [(s.x0 + i, s.z0 + j) for i in range(3) for j in range(3)] + [
        (s.x1 - i, s.z1 - j) for i in range(3) for j in range(3)
    ]:
        w[(x, s.ground_y, z)] = AIR  # two 3x3 pits, one at each end of the site
    body = FakeBody(w, {DIRT: 400}, BLOCKS, pos=(s.x0, s.ground_y + 1, s.z0))
    order = []
    real = body.place_block

    def spy(pos, *a, **k):
        order.append(pos)
        return real(pos, *a, **k)

    body.place_block = spy
    report = br.run_build(t, body, BLOCKS, survey=False, site_margin=1)
    assert report.site["filled"] >= 18
    assert (
        order[0][0] <= s.x0 + 2 and order[0][2] <= s.z0 + 2
    )  # the pit beside the body first


def test_stalled_fill_cell_is_given_up_not_fatal():
    t = template()
    s, w = mixed_world(t)
    body = FakeBody(w, {DIRT: 400}, BLOCKS, pos=(1, 0, 1))
    real = body.place_block
    bad = set()

    def boom(pos, *a, **k):
        if not bad and pos[1] < s.ground_y:
            bad.add(pos)
        if pos in bad:
            raise StallError("synthetic stall")
        return real(pos, *a, **k)

    body.place_block = boom
    report = br.run_build(t, body, BLOCKS, survey=False, site_margin=1)
    assert report.stop != "stalled"  # the stalled cell is skipped, the build goes on
    assert any("stalled" in str(why) for why in report.skipped.values()) or not bad


def test_site_cutter_cuts_in_slices_without_filling():
    t = template()
    s, w = mixed_world(t)
    w[(s.x0, s.ground_y + 1, s.z0)] = STONE  # raised block
    w[(s.x0 + 3, s.ground_y, s.z0 + 3)] = AIR  # hole: a cutter must not fill it
    body = FakeBody(w, {}, BLOCKS, pos=(t.origin[0], t.origin[1], t.origin[2]))
    cutter = br.SiteCutter(t, body, BLOCKS, 1)
    for _ in range(20):
        if not cutter.slice(rounds=1):
            break
    assert cutter.done
    assert w[(s.x0, s.ground_y + 1, s.z0)] == AIR
    assert w[(s.x0 + 3, s.ground_y, s.z0 + 3)] == AIR  # filling is the later phase


def test_sweep_breaks_everything_in_reach_without_moving_between_blocks():
    t = template()
    s, w = mixed_world(t)
    g = s.ground_y
    mound = [(s.x1, g + 1, z) for z in range(s.z0, s.z0 + 3)] + [
        (s.x1, g + 2, z) for z in range(s.z0, s.z0 + 3)
    ]
    for cell in mound:
        w[cell] = DIRT
    body = FakeBody(w, {DIRT: 50}, BLOCKS, pos=(1, 0, 1))
    moves, pickups = [], []
    stance = body.goto_stance
    body.goto_stance = lambda *a, **k: moves.append(a[0]) or stance(*a, **k)
    body.collect_drops = lambda near: pickups.append(len(near))
    report = br.run_build(t, body, BLOCKS, survey=False, site_margin=1)
    assert report.site["stop"] == "ready", report.site
    assert report.site["cleared"] == len(mound)
    assert all(w[cell] in bp.AIR for cell in mound)
    # six blocks, one sweep: one drop pickup, and fewer moves than blocks
    assert len(pickups) == 1 and len([m for m in moves if m in mound]) <= 3


def test_overhead_leaf_is_reached_by_one_climb_not_a_stance_search(monkeypatch):
    monkeypatch.setattr(sp, "HIGH", 3)
    monkeypatch.setattr(sp, "REACH_BREAK", 2.5)
    t = template()
    s, w = mixed_world(t)
    leaf = (s.x1, s.top_y, s.z1)
    w[leaf] = LEAVES
    body = FakeBody(w, {DIRT: 50}, BLOCKS, pos=(1, 0, 1))
    climbs, stances = [], []
    body.walk_to = lambda p, r=1.5: setattr(body, "pos", tuple(p)) or True
    body.dig_to = lambda p: climbs.append(p) or setattr(body, "pos", tuple(p)) or True
    stance = body.goto_stance
    body.goto_stance = lambda *a, **k: stances.append(a[0]) or stance(*a, **k)
    report = br.run_build(t, body, BLOCKS, survey=False, site_margin=1)
    assert w[leaf] in bp.AIR and report.site["stop"] == "ready", report.site
    assert climbs == [(leaf[0], leaf[1] - 3, leaf[2])] and leaf not in stances


def test_fill_sweep_places_all_in_reach_without_moving_between_blocks():
    class Body:
        pos, moves, placed = (0, 64, 0), [], []
        flowers = {(2, 63, 0)}  # a flower in the hole is broken before the dirt

        def position(self):
            return self.pos

        def world(self, *pos):
            if pos in self.flowers:
                return "minecraft:poppy"
            return DIRT if pos in self.placed or pos == (0, 63, 0) else AIR

        def break_block(self, pos):
            self.flowers.discard(pos)
            return br.Result(True, None)

        def place_block(self, pos, state, item):
            # a cell is placeable only beside the shore (x=1) or a block just placed
            ok = pos[0] == 1 or (pos[0] - 1, pos[1], pos[2]) in self.placed
            ok = ok and pos not in self.flowers
            if ok:
                self.placed.append(pos)
            return br.Result(ok, None if ok else "state_unreachable")

        def walk_to(self, goal, radius=0.5):
            self.moves.append(goal)
            self.pos = goal
            return True

    class Runner:
        report, max_steps = br.BuildReport(), 10_000

    body = Body()
    fills = [(x, 63, 0) for x in range(1, 7)]
    assert sp._fill_sweep(body, Runner, fills, 64, 63) == 6
    assert body.placed[:3] == [(1, 63, 0), (2, 63, 0), (3, 63, 0)]  # no walk between
    assert len(body.moves) <= 2


def test_a_cell_the_leak_plug_filled_counts_as_filled_not_refused():
    # run 20261117T033515924527Z: seagrass broken under water, the body's plug
    # reflex put dirt there, then every place_block was refused "occupied"
    class Body:
        pos, placed, plugged = (0, 64, 0), [], set()
        seagrass = {(1, 63, 0)}

        def position(self):
            return self.pos

        def world(self, *pos):
            if pos in self.seagrass:
                return "minecraft:seagrass"
            solid = pos in self.placed or pos in self.plugged or pos == (0, 63, 0)
            return DIRT if solid else AIR

        def break_block(self, pos):
            self.seagrass.discard(pos)
            self.plugged.add(pos)
            return br.Result(False, "water", {"plugged": DIRT})

        def place_block(self, pos, state, item):
            if pos in self.plugged:
                return br.Result(False, "occupied")
            self.placed.append(pos)
            return br.Result(True)

        def walk_to(self, goal, radius=0.5):
            self.pos = goal
            return True

    class Runner:
        report, max_steps = br.BuildReport(), 10_000

    body = Body()
    fills = [(1, 63, 0), (2, 63, 0)]
    assert sp._fill_sweep(body, Runner, fills, 64, 63) == 2
    assert body.placed == [(2, 63, 0)] and Runner.report.placed == fills


def test_fill_does_not_build_walls_from_below_the_working_surface():
    class Body:
        pos = (0, 62, 0)
        actions = []

        def position(self):
            return self.pos

        def world(self, x, y, z):
            return STONE if (x, y, z) == (2, 63, 0) else AIR

        def walk_to(self, goal, radius=0.5):
            self.actions.append(("walk", goal))
            self.pos = goal
            return True

        def place_block(self, pos, state, item):
            assert self.pos[1] == 64
            self.actions.append(("place", pos))
            return br.Result(True)

    class Runner:
        report, max_steps = br.BuildReport(), 100

    b = Body()
    assert sp._fill_sweep(b, Runner, [(1, 63, 0)], 1, 63) == 1
    assert [a[0] for a in b.actions] == ["walk", "place"]


@pytest.mark.parametrize("height,expected_drop", [(64, 0), (67, None)])
def test_fill_can_descend_from_borrow_hill_before_locking_work_surface(
    monkeypatch, height, expected_drop
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    world = {(x, 63, z): STONE for x in range(-1, 9) for z in range(-2, 3)}
    world.update(
        {(x, y, z): AIR for x in range(-1, 9) for y in (64, 65) for z in range(-2, 3)}
    )
    hole = (6, 63, 0)
    world[hole] = AIR
    body = FakeBody(world, {DIRT: 1}, BLOCKS, pos=(0, height, 0))
    drops = []

    @contextmanager
    def policy(body, **kwargs):
        drops.append(kwargs["max_drop"])
        yield

    def walk(goal, radius):
        body.pos = goal
        return True

    monkeypatch.setattr(sp, "walking_with_edits", policy)
    body.walk_to = walk
    runner = SimpleNamespace(report=br.BuildReport(), max_steps=100)
    assert sp._fill_sweep(body, runner, [hole], 1, 63) == 1
    assert drops == [expected_drop]
    assert world[hole] == DIRT


def test_fill_movement_without_edits_is_bounded_and_hands_over_to_stance_search():
    class Body:
        pos = (0, 64, 0)

        def __init__(self):
            self.attempts, self.moves = [], []

        def position(self):
            return self.pos

        def world(self, x, y, z):
            return (
                STONE
                if y == 63 and (x, z) not in {(i, 0) for i in (1, 5, 9, 13)}
                else AIR
            )

        def walk_to(self, goal, radius=0.5):
            self.moves.append(goal)
            # A failed route can shuffle before returning to the same column.
            return False

        def place_block(self, pos, state, item):
            self.attempts.append((pos, self.pos))
            return br.Result(False, "state_unreachable")

    class Runner:
        report, max_steps = br.BuildReport(), 100

    b = Body()
    assert sp._fill_sweep(b, Runner, [(i, 63, 0) for i in (1, 5, 9, 13)], 64, 63) == 0
    assert len(b.moves) == 4
    assert len(b.attempts) == len(set(b.attempts)) == 1


def test_a_deep_pond_cell_in_face_reach_is_filled_from_the_rim():
    # Run 20261117T045722501942Z: a cell two below the ground, 4.24 eye to centre
    # but 3.9 to its face, was never tried and the pond stayed unfilled.
    deep = (0, 61, 1)

    class Body:
        placed = []

        def position(self):
            return (0, 64, 0)

        def world(self, x, y, z):
            return AIR if (x, z) == (0, 1) and y >= 61 else STONE

        def place_block(self, pos, state, item):
            self.placed.append(pos)
            return br.Result(True, "")

    class Runner:
        report, max_steps = br.BuildReport(), 100

    assert sp._eye_dist((0.5, 65.62, 0.5), deep) > sp.REACH_BREAK
    assert sp._fill_sweep(Body(), Runner, [deep], 1, 63) == 1
    assert Body.placed == [deep]


def test_fill_never_caps_a_column_after_its_lower_cell_failed():
    class Body:
        attempts = []

        def position(self):
            return (0, 64, 0)

        def world(self, x, y, z):
            return AIR

        def place_block(self, pos, state, item):
            self.attempts.append(pos)
            return br.Result(pos[1] == 63, "state_unreachable")

    class Runner:
        report, max_steps = br.BuildReport(), 100

    b = Body()
    assert sp._fill_sweep(b, Runner, [(1, 62, 0), (1, 63, 0)], 64, 63) == 0
    assert b.attempts == [(1, 62, 0)]


def test_hill_dirt_is_raised_ground_outside_the_area_nearest_first():
    site = sp.Site(0, 2, 0, 2, 63, 70, 1)
    world = {
        (5, 64, 1): "minecraft:grass_block",  # raised, outside: taken
        (9, 64, 1): "minecraft:dirt",  # raised, outside, farther
        (5, 63, 1): "minecraft:dirt",  # ground level: a pit, never taken
        (1, 64, 1): "minecraft:dirt",  # inside the area: the clearing's job
        (6, 64, 1): "minecraft:stone",
    }

    class Body:
        def world(self, x, y, z):
            return world.get((x, y, z), "minecraft:air")

    got = sp._hill_dirt(site, Body(), frozenset(), (3, 64, 1), 10)
    assert got == [(5, 64, 1), (9, 64, 1)]


def test_a_flower_in_a_ground_level_hole_is_a_hole_to_fill():
    site = sp.Site(0, 0, 0, 0, 63, 66, 0)
    world = {(0, 63, 0): "minecraft:orange_tulip", (0, 62, 0): "minecraft:dirt"}
    plan = sp.plan_site(
        site,
        bp.Template({}, (1, 1, 1), (0, 64, 0)),
        lambda *p: world.get(p, "minecraft:air"),
    )
    assert plan.fills == [(0, 63, 0)]


def test_stalls_at_one_spot_on_different_targets_stop_as_trapped():
    from mclab.progress_watch import SPOT_STALLS, ProgressWatch, StallError

    now = [0.0]

    class Still:
        def position(self):
            return (0, 64, 0)

    watch = ProgressWatch(clock=lambda: now[0], log=lambda m: None)
    watch.progress((0, 64, 0))
    with pytest.raises(StallError, match="trapped"):
        for i in range(SPOT_STALLS + 1):
            now[0] += 10
            watch.check(Still(), "goto_stance", (i, 70, 0), False)
    assert len(watch.events) == SPOT_STALLS


def test_a_tree_log_in_a_cell_the_house_wants_is_still_cleared():
    site = sp.Site(0, 0, 0, 0, 63, 66, 0)
    cells = {(0, 64, 0): "minecraft:crafting_table", (0, 65, 0): "minecraft:oak_log"}
    world = {
        (0, 63, 0): "minecraft:dirt",
        (0, 64, 0): "minecraft:oak_log",
        (0, 65, 0): "minecraft:oak_log",
    }
    plan = sp.plan_site(
        site,
        bp.Template(cells, (1, 2, 1), (0, 64, 0)),
        lambda *p: world.get(p, "minecraft:air"),
        protected=frozenset(cells),
    )
    assert plan.breaks == [(0, 64, 0)] and not plan.blocked


class LoggedBody(FakeBody):
    """FakeBody that records every break/place in order."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.ops: list[tuple[str, tuple, str]] = []

    def break_block(self, pos, tool=None):
        self.ops.append(("break", pos, self.w.get(pos)))
        return super().break_block(pos, tool)

    def place_block(self, pos, state, item):
        self.ops.append(("place", pos, state))
        return super().place_block(pos, state, item)


def test_site_prep_runs_logs_then_cuts_then_fills_then_leaves():
    t = template()
    s, w = mixed_world(t)
    g = s.ground_y
    for dy in (1, 2, 3):
        w[(s.x0, g + dy, s.z0)] = LOG  # a tree with a canopy on the neighbours
    for pos in ((s.x0 + 1, g + 3, s.z0), (s.x0 + 1, g + 4, s.z0)):
        w[pos] = LEAVES
    w[(s.x1, g + 1, s.z1)] = DIRT  # a cut
    w[((s.x0 + s.x1) // 2, g, s.z0 + 2)] = AIR  # a fill
    body = LoggedBody(w, {DIRT: 50}, BLOCKS, pos=t.origin)
    report = br.run_build(t, body, BLOCKS, survey=False, site_margin=1, max_steps=5000)
    assert report.site["stop"] == "ready", report.site

    def first(pred):
        return min(i for i, op in enumerate(body.ops) if pred(op))

    def last(pred):
        return max(i for i, op in enumerate(body.ops) if pred(op))

    is_log = lambda op: op[0] == "break" and op[2] == LOG  # noqa: E731
    is_leaf = lambda op: op[0] == "break" and op[2] == LEAVES  # noqa: E731
    is_cut = lambda op: op[0] == "break" and op[2] == DIRT  # noqa: E731
    is_fill = lambda op: op[0] == "place" and op[2] == DIRT  # noqa: E731
    assert last(is_log) < first(is_cut) < first(is_fill) < first(is_leaf)


def test_a_leafless_trunk_in_the_site_is_felled_whole_before_any_cut():
    t = template()
    s, w = mixed_world(t)
    g = s.ground_y
    column = [(s.x0, g + dy, s.z0) for dy in range(1, 5)]
    for p in column:
        w[p] = LOG  # no canopy: the old tree finder skipped it
    w[(s.x1, g + 1, s.z1)] = DIRT
    body = LoggedBody(w, {DIRT: 50}, BLOCKS, pos=t.origin)
    br.run_build(t, body, BLOCKS, survey=False, site_margin=1, max_steps=5000)
    assert all(w[p] == AIR for p in column)
    logs = [i for i, op in enumerate(body.ops) if op[2] == LOG]
    cut = next(i for i, op in enumerate(body.ops) if op[2] == DIRT and op[0] == "break")
    assert logs and max(logs) < cut


class HillWalk:
    """Dome hill of dirt on flat ground: a walking body that breaks what is in reach."""

    G = 63

    def __init__(self, r=6, h=5, start=(-2, 64, 7)):
        self.w, self.cells = {}, []
        for x in range(-3, 18):
            for z in range(-3, 18):
                self.w[(x, self.G, z)] = DIRT
                for y in range(self.G + 1, self.G + 12):
                    self.w[(x, y, z)] = AIR
                d = math.hypot(x - 7, z - 7)
                for y in range(self.G + 1, self.G + 1 + max(0, round(h * (1 - d / r)))):
                    self.w[(x, y, z)] = DIRT
                    self.cells.append((x, y, z))
        self.pos, self.moves, self.report = start, [start], br.BuildReport()

    def position(self):
        return self.pos

    def world(self, x, y, z):
        return self.w.get((x, y, z))

    def _free(self, x, z):
        return all(self.w.get((x, self.G + dy, z), AIR) == AIR for dy in (1, 2))

    def walk_to(self, goal, radius=1.0):
        spots = [
            (math.hypot(x - goal[0], z - goal[2]), x, z)
            for x in range(goal[0] - 4, goal[0] + 5)
            for z in range(goal[2] - 4, goal[2] + 5)
            if self._free(x, z)
        ]
        spots = [s for s in spots if s[0] <= radius]
        if not spots:
            return False
        _, x, z = min(spots)
        self.pos = (x, self.G + 1, z)
        self.moves.append(self.pos)
        return True

    def break_block(self, pos, tool=None):
        eye = (self.pos[0] + 0.5, self.pos[1] + 1.62, self.pos[2] + 0.5)
        if math.dist(eye, (pos[0] + 0.5, pos[1] + 0.5, pos[2] + 0.5)) > 4.5:
            return br.Result(False, "out_of_reach")
        self.w[pos] = AIR
        return br.Result(True)

    def attempt(self, act):  # runner stand-in: nearest free stance in reach, then break
        near = sorted(
            (
                (x, self.G + 1, z)
                for x in range(act.pos[0] - 3, act.pos[0] + 4)
                for z in range(act.pos[2] - 3, act.pos[2] + 4)
                if self._free(x, z)
            ),
            key=lambda c: math.dist(c, act.pos),
        )
        for c in near:
            self.pos = c
            if self.break_block(act.pos).ok:
                self.moves.append(c)
                self.report.broken.append(act.pos)
                return

    max_steps = 10_000

    def walked(self):
        return sum(
            math.dist(a, b) for a, b in zip(self.moves, self.moves[1:], strict=False)
        )

    def left(self):
        return sum(self.w[c] != AIR for c in self.cells)


class _Act:
    def __init__(self, pos):
        self.pos = pos


def test_cutting_a_hill_is_worked_through_without_back_and_forth_hops():
    swept = HillWalk()
    sp._sweep(swept, swept, list(swept.cells), swept.G)

    raster = HillWalk()  # layer by layer, row by row: what a plain sort gives
    for p in sorted(raster.cells, key=lambda p: (-p[1], p[0], p[2])):
        raster.attempt(_Act(p))

    assert swept.left() == 0
    assert swept.walked() < raster.walked() / 5  # measured 24 vs 443
    hops = [math.dist(a, b) for a, b in zip(swept.moves, swept.moves[1:], strict=False)]
    assert max(hops) < 8  # no cross-hill jumps


def test_fill_stock_is_any_dug_up_solid_the_house_does_not_use():
    template = bp.Template({(0, 64, 0): "minecraft:cobblestone"}, (1, 1, 1), (0, 64, 0))
    held = {"minecraft:cobblestone": 9, "minecraft:stone": 3, "minecraft:dirt": 2}
    held["minecraft:gravel"] = 5  # falls: never fill

    assert sp.fill_stock(held, template) == {"minecraft:dirt": 2, "minecraft:stone": 3}


def test_fill_walk_from_a_pit_may_place_blocks_to_get_out():
    """Run 20261116T220215760808Z: 2 below the surface under no_placing, every walk
    out failed and the fill stalled. The walk now runs with edits allowed."""
    from contextlib import contextmanager

    class Body:
        pos, edits_on = (0, 62, 0), False

        def position(self):
            return self.pos

        def world(self, x, y, z):
            return STONE if (x, y, z) == (2, 63, 0) else AIR

        @contextmanager
        def walking_with_edits(self, **kw):
            self.edits_on = True
            yield
            self.edits_on = False

        def walk_to(self, goal, radius=0.5):
            if not self.edits_on:
                return False
            self.pos = goal
            return True

        def place_block(self, pos, state, item):
            assert self.pos[1] == 64
            return br.Result(True)

    class Runner:
        report, max_steps = br.BuildReport(), 100

    assert sp._fill_sweep(Body(), Runner, [(1, 63, 0)], 1, 63) == 1


def test_an_unknown_cell_over_a_pond_is_approached_from_dry_footing():
    # run 20261117T015107803188Z: the walk goal snapped above the unknown pond cell,
    # where no stance can be, and each plan for it ran the 10,000-node limit
    t = template()
    s, w = mixed_world(t)
    pond = [(s.x0 + i, s.z0 + j) for i in range(3) for j in range(3)]
    for x, z in pond:
        w[(x, s.ground_y, z)] = WATER
        w[(x, s.ground_y - 1, z)] = WATER
    cell = (s.x0 + 1, s.ground_y + 1, s.z0 + 1)
    del w[cell]
    body = FakeBody(w, {DIRT: 5}, BLOCKS, pos=(1, 0, 1))
    walks = []
    body.walk_to = lambda p, *a, **k: walks.append(p) or False
    br.run_build(t, body, BLOCKS, survey=False, site_margin=1)
    approach = [
        p for p in walks if abs(p[0] - cell[0]) <= 4 and abs(p[2] - cell[2]) <= 4
    ]
    assert approach
    assert all(w[(x, y - 1, z)] == STONE for x, y, z in approach)
    assert all((x, z) not in pond for x, _, z in approach)


def test_ground_standing_where_the_house_goes_is_cut_for_fill_not_left_to_the_builder():
    # run 20261117T130121185147Z: a dirt mound under the ground floor was left for
    # the builder while the fill ran dry and the body carved the hillside for dirt
    site = sp.Site(0, 1, 0, 0, 63, 66, 0)
    cells = {
        (0, 64, 0): "minecraft:oak_planks",
        (0, 65, 0): "minecraft:oak_planks",
        (1, 64, 0): "minecraft:chest",
    }
    world = {
        (0, 63, 0): DIRT,
        (0, 64, 0): "minecraft:grass_block",
        (0, 65, 0): "minecraft:oak_planks",  # already built: left alone
        (1, 63, 0): DIRT,
        (1, 64, 0): "minecraft:barrel",  # a container is the builder's business
    }
    plan = sp.plan_site(
        site,
        bp.Template(cells, (2, 2, 1), (0, 64, 0)),
        lambda *p: world.get(p, "minecraft:air"),
    )
    assert plan.breaks == [(0, 64, 0)] and not plan.blocked


def test_a_stall_after_the_lab_episode_ended_says_so_at_once():
    # Run 20261117T200337044791Z: the episode's tick limit cancelled every plan and
    # the run ended four stalls later as "trapped" on an open upper floor.
    from mclab.progress_watch import ProgressWatch, StallError

    now = [0.0]

    class Ended:
        def position(self):
            return (0, 69, 0)

        def episode_ended(self):
            return True

    watch = ProgressWatch(clock=lambda: now[0], log=lambda m: None)
    watch.progress((0, 69, 0))
    now[0] += 10
    with pytest.raises(StallError, match="episode ended"):
        watch.check(Ended(), "goto_stance", (3, 73, 0), False)
