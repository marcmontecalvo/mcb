import pytest
from test_build_plan import (
    AIR,
    BLOCKS,
    HOUSE,
    HOUSE_MATERIALS,
    flat_world,
    make_build,
    real_house,
)

from mclab import build_plan as bp
from mclab import build_runner as br

EYE_HEIGHT = 1.62


class FakeBody:
    """Dict-world body: reach (eye within 4.5 of the cell centre), a solid
    neighbour and a support check on every placement. ``fail`` maps a cell to
    ``(reason, times)`` and makes place/use/break there fail that many times.
    Stances are teleports to the nearest standable cell in reach."""

    def __init__(self, world, inventory, blocks, pos=(0, 0, 0), fail=None):
        self.w, self.inv, self.blocks = world, dict(inventory), blocks
        self.pos = pos
        self.fail = dict(fail or {})
        self.violations: list[str] = []
        self.stance_calls: list[tuple[bp.Pos, frozenset]] = []

    # --- BuildBody ---------------------------------------------------------
    def world(self, x, y, z):
        return self.w.get((x, y, z))

    def inventory(self):
        return dict(self.inv)

    def position(self):
        return self.pos

    def goto_stance(self, target_pos, reach, avoid=()):
        self.stance_calls.append((target_pos, frozenset(avoid)))
        if self.pos not in avoid and self._stance_ok(self.pos, target_pos, reach):
            return True
        tx, ty, tz = target_pos
        spots = [
            (tx + dx, ty + dy, tz + dz)
            for dx in range(-4, 5)
            for dy in range(-5, 3)
            for dz in range(-4, 5)
        ]
        spots = [s for s in spots if s not in avoid]
        spots = [s for s in spots if self._stance_ok(s, target_pos, reach)]
        if not spots:
            return False
        self.pos = min(spots, key=lambda s: (_d2(s, self.pos), s))
        return True

    def place_block(self, pos, state, item):
        if (bad := self._gate_op(pos)) is not None:
            return bad
        if not self._in_reach(pos):
            return br.Result(False, "out_of_reach")
        cur = self.w.get(pos)
        if cur is None or bp.parse_state(cur)[0] not in bp.REPLACEABLE:
            return br.Result(False, "occupied")
        need = bp.item_for_state(state, self.blocks) or (item, 1)
        if self.inv.get(need[0], 0) < need[1]:
            return br.Result(False, "missing_item")
        ident, props = bp.parse_state(state)
        if not any(
            bp._is_solid(self.w.get(bp._add(pos, v)), self.blocks)
            for v in bp._NEIGHBOURS
        ):
            self.violations.append(f"no neighbour: {pos} {state}")
            return br.Result(False, "no_support_face")
        kind = bp._support_kind(ident, props, self.blocks.get(ident, {}))
        sup = bp._support_cell(kind, props, pos) if kind else None
        if sup is not None and not bp._is_solid(self.w.get(sup), self.blocks):
            self.violations.append(f"before support: {pos} {state}")
            return br.Result(False, "no_support_face")
        self.w[pos] = state
        self.inv[need[0]] -= need[1]
        if props.get("half") == "lower" and ident.endswith("_door"):
            self.w[bp._add(pos, (0, 1, 0))] = state.replace("half=lower", "half=upper")
        if props.get("part") == "foot":
            head = bp._add(pos, bp._VEC[props["facing"]])
            self.w[head] = state.replace("part=foot", "part=head")
        return br.Result(True, data={"placed": state})

    def use_block(self, pos, item=None):
        if (bad := self._gate_op(pos)) is not None:
            return bad
        if not self._in_reach(pos):
            return br.Result(False, "out_of_reach")
        cur = self.w.get(pos, AIR)
        if cur == "minecraft:dirt" and item and item.endswith(("_hoe", "_shovel")):
            kind = "farmland" if item.endswith("_hoe") else "dirt_path"
            self.w[pos] = "minecraft:" + kind
            return br.Result(True)
        if cur == bp.POT and item and self.inv.get(item, 0) > 0:
            self.w[pos] = "minecraft:potted_" + item.removeprefix("minecraft:")
            self.inv[item] -= 1
            return br.Result(True)
        if "open=" not in cur:
            return br.Result(False, "nothing_happened")
        flip = {"open=true": "open=false", "open=false": "open=true"}
        key = "open=true" if "open=true" in cur else "open=false"
        self.w[pos] = cur.replace(key, flip[key])
        if "_door" in cur and "half=lower" in cur:
            up = bp._add(pos, (0, 1, 0))
            self.w[up] = self.w[up].replace(key, flip[key])
        return br.Result(True)

    def break_block(self, pos, tool=None):
        if (bad := self._gate_op(pos)) is not None:
            return bad
        if not self._in_reach(pos):
            return br.Result(False, "out_of_reach")
        cur = self.w.get(pos, AIR)
        if cur in bp.AIR:
            return br.Result(False, "no_block")
        self.w[pos] = AIR
        if "_door" in cur:
            other = (0, 1, 0) if "half=lower" in cur else (0, -1, 0)
            self.w[bp._add(pos, other)] = AIR
        return br.Result(True, data={"broken": cur})

    # --- helpers -----------------------------------------------------------
    def _gate_op(self, pos):
        reason, times = self.fail.get(pos, (None, 0))
        if times > 0:
            self.fail[pos] = (reason, times - 1)
            return br.Result(False, reason)
        return None

    def _in_reach(self, pos, stance=None):
        sx, sy, sz = stance or self.pos
        eye = (sx + 0.5, sy + EYE_HEIGHT, sz + 0.5)
        centre = (pos[0] + 0.5, pos[1] + 0.5, pos[2] + 0.5)
        dist2 = sum((a - b) ** 2 for a, b in zip(eye, centre, strict=True))
        return dist2 <= br.REACH**2

    def _stance_ok(self, s, target, reach):
        feet, head = s, (s[0], s[1] + 1, s[2])
        if target in (feet, head) or not self._in_reach(target, s):
            return False
        free = lambda p: self.w.get(p) is not None and not bp._is_solid(  # noqa: E731
            self.w[p], self.blocks
        )
        below = self.w.get((s[0], s[1] - 1, s[2]))
        return free(feet) and free(head) and bp._is_solid(below, self.blocks)


def _d2(a, b):
    return sum((p - q) ** 2 for p, q in zip(a, b, strict=True))


def small_house_body(**kw):
    w = flat_world()
    inv = {item: 100 for item in {i["item"] for i in BLOCKS.values()}}
    return FakeBody(w, inv, BLOCKS, **kw), bp.Template.from_build(HOUSE, (1, 0, 1))


def test_small_house_reaches_empty_diff_with_the_expected_materials():
    body, t = small_house_body()
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done" and report.skipped == {}
    assert report.progress() == 1.0 and report.steps > 0
    d = bp.diff(t, body.world)
    assert (d.breaks, d.places, d.unknown, d.uses) == ([], [], [], [])
    assert body.violations == []
    assert {k: 100 - v for k, v in body.inv.items() if v != 100} == HOUSE_MATERIALS


@pytest.mark.parametrize("refuse", [False, True])
def test_cleanup_opened_door_is_restored_before_final_success(monkeypatch, refuse):
    body, template = small_house_body()

    def cleanup(runner, template, scaffolds):
        for pos, state in body.w.items():
            if "_door[" in state and "half=lower" in state:
                assert body.use_block(pos).ok
                if refuse:
                    body.use_block = lambda *a, **k: br.Result(False, "refused")
                return True
        raise AssertionError("Fixture has no built door")

    monkeypatch.setattr(br, "_clean_up", cleanup)
    report = br.run_build(template, body, BLOCKS)
    assert report.stop == ("blocked" if refuse else "done")
    assert bool(bp.diff(template, body.world).uses) is refuse
    assert (report.progress() == 1) is not refuse


def test_terrain_in_the_footprint_is_broken_first():
    body, t = small_house_body()
    body.w[(2, 1, 2)] = "minecraft:dirt"
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done" and report.broken == [(2, 1, 2)]


def test_state_unreachable_retries_other_stances_then_skips():
    pane = (1, 2, 3)  # the glass pane, a leaf of the house
    body, t = small_house_body(fail={pane: ("state_unreachable", 99)})
    report = br.run_build(t, body, BLOCKS)
    assert report.skipped == {pane: "state_unreachable"}
    assert report.stop == "blocked" and report.progress() < 1.0
    calls = [avoid for target, avoid in body.stance_calls if target == pane]
    # MAX_TRIES per round, a fresh round (no stances avoided) RETRY_ROUNDS more times
    assert len(calls) == br.MAX_TRIES * (1 + br.RETRY_ROUNDS)
    # Each attempt excludes the previous stance.
    assert [len(a) for a in calls[: br.MAX_TRIES]] == [0, 1, 2]
    assert body.w[pane] in bp.AIR


def test_transient_failure_is_retried_and_not_skipped():
    pane = (1, 2, 3)
    body, t = small_house_body(fail={pane: ("out_of_reach", 2)})
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done" and report.skipped == {}


def test_need_materials_calls_hook_and_stops_on_false():
    body, t = small_house_body()
    del body.inv["minecraft:oak_door"]
    seen = []
    report = br.run_build(t, body, BLOCKS, on_need_materials=lambda m: seen.append(m))
    assert seen == [{"minecraft:oak_door": 1}]
    assert report.stop == "need_materials" and report.needed == seen[0]


def test_need_materials_hook_can_refill_and_resume():
    body, t = small_house_body()
    body.inv["minecraft:oak_door"] = 0

    def refill(items):
        body.inv["minecraft:oak_door"] = items["minecraft:oak_door"]
        return True

    report = br.run_build(t, body, BLOCKS, on_need_materials=refill)
    assert report.stop == "done" and report.progress() == 1.0


def test_max_steps_stops_the_loop():
    body, t = small_house_body()
    report = br.run_build(t, body, BLOCKS, max_steps=5)
    assert report.stop == "max_steps" and report.steps == 5


@pytest.mark.parametrize("origin", [(1, 0, 1), (1, 3, 1)])
def test_real_house_reaches_empty_diff(origin):
    t, blocks, materials = real_house(origin)
    w = flat_world(-2, 30, -1)
    inv = {**materials, "minecraft:dirt": materials.get("minecraft:dirt", 0) + 300}
    body = FakeBody(w, inv, blocks)
    report = br.run_build(
        t, body, blocks, site_margin=None
    )  # floating origin: scaffolds
    assert report.skipped == {} and report.stop == "done", report
    d = bp.diff(t, body.world)
    assert (d.breaks, d.places, d.unknown, d.uses) == ([], [], [], [])
    assert body.violations == [] and report.progress() == 1.0
    assert bool(report.scaffolded) == (origin[1] > 0)
    assert all(body.w[p] in bp.AIR for p in report.scaffolded if p not in t.cells)
    assert report.used  # trapdoors opened, pots planted
    assert not set(report.scaffolded) & {
        p for p, s in t.cells.items() if s not in bp.AIR
    }


FARM = "minecraft:farmland[moisture=0]"


def farm_template():
    return bp.Template.from_build(make_build({(1, 0, 1): FARM}, (3, 1, 3)), (0, 0, 0))


def test_farmland_is_dirt_then_a_hoe_use_and_the_hoe_is_a_need():
    t = farm_template()
    assert bp.tool_needs(t, {}) == {"minecraft:wooden_hoe": 1}
    assert bp.tool_needs(t, {"minecraft:stone_hoe": 1}) == {}
    body = FakeBody(
        flat_world(), {"minecraft:dirt": 1, "minecraft:stone_hoe": 1}, BLOCKS
    )
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done", report
    assert body.w[(1, 0, 1)] == "minecraft:farmland"
    assert report.placed == [(1, 0, 1)] and report.used == [(1, 0, 1)]


def test_farmland_without_a_hoe_asks_for_one():
    body = FakeBody(flat_world(), {"minecraft:dirt": 1}, BLOCKS)
    report = br.run_build(farm_template(), body, BLOCKS)
    assert report.stop == "need_materials"
    assert report.needed == {"minecraft:wooden_hoe": 1}


def test_scaffold_never_goes_into_template_cells():
    w = flat_world()
    world = lambda x, y, z: w.get((x, y, z))  # noqa: E731
    reserved = {(1, y, 1) for y in range(3)}
    assert bp._scaffold_pos((1, 3, 1), world, BLOCKS) == (1, 0, 1)
    side = bp._scaffold_pos((1, 3, 1), world, BLOCKS, reserved, sideways=True)
    assert side is not None and side not in reserved and side[1] == 0
    assert bp._scaffold_pos((1, 3, 1), world, BLOCKS, reserved) is None


def test_survey_walks_the_perimeter_and_reports_unknown_cells():
    body, t = small_house_body()
    report = br.run_build(t, body, BLOCKS, survey=True)
    targets = {target for target, _ in body.stance_calls}
    corners = {(0, 0, 0), (6, 0, 0), (0, 0, 6), (6, 0, 6)}
    assert corners <= targets and report.surveyed == len(br.perimeter(t))
    assert report.stop == "done" and report.unknown == []
    short = FakeBody(flat_world(-2, 4), {k: 9 for k in BLOCKS}, BLOCKS)
    cut = br.run_build(t, short, BLOCKS)
    assert cut.unknown and cut.counts["unknown"] == len(cut.unknown)


def test_survey_prefers_walk_to_when_the_body_has_it():
    body, t = small_house_body()
    walked = []
    body.walk_to = lambda p, radius=1.5: walked.append(p) or True
    br.run_build(t, body, BLOCKS, survey=True)
    assert walked == br.perimeter(t) and not any(
        c[0] in walked for c in body.stance_calls
    )


def test_small_house_fixture_builds_with_the_kit_of_its_material_list():
    import json
    from pathlib import Path

    root = Path(__file__).parent
    build = json.loads(
        (root / "fixtures/schematics/small-house.build.json").read_text(
            encoding="utf-8"
        )
    )
    blocks = json.loads(
        (root.parent / "knowledge/26.3-f7dba04d8b5b/blocks.json").read_text(
            encoding="utf-8"
        )
    )["blocks"]
    t = bp.Template.from_build(build, (2, 0, 2))
    body = FakeBody(
        flat_world(-2, 12), {**build["materials"], "minecraft:dirt": 20}, blocks
    )
    report = br.run_build(t, body, blocks, survey=True)
    assert report.stop == "done" and report.skipped == {}, report
    assert report.progress() == 1.0 and body.violations == []
    assert report.used  # the trapdoor is opened


def test_support_axis_blocks():
    act_z = bp.Place(
        (0, 1, 1),
        "minecraft:stripped_birch_log[axis=z]",
        "minecraft:stripped_birch_log",
    )
    sup_z = br._support(act_z, BLOCKS)
    assert sup_z == {(0, 1, 0), (0, 1, 2)}

    act_x = bp.Place(
        (1, 1, 0),
        "minecraft:stripped_birch_log[axis=x]",
        "minecraft:stripped_birch_log",
    )
    sup_x = br._support(act_x, BLOCKS)
    assert sup_x == {(0, 1, 0), (2, 1, 0)}


class _Pit:
    """A body in a pit: no stance is reachable until it has escaped."""

    def __init__(self, can_escape=True):
        self.escaped, self.can_escape = False, can_escape

    def position(self):
        return (0, 0, 0)

    def goto_stance(self, pos, reach, avoid=()):
        return self.escaped

    def escape_hole(self, pos):
        if self.can_escape:
            self.escaped = True
        return self.can_escape


def test_runner_escapes_a_pit_then_reaches_the_stance():
    body, report = _Pit(), br.BuildReport()
    runner = br._Runner(body, report, 100, BLOCKS)
    assert runner._stance((1, 1, 1), set())
    assert report.escapes == 1


def test_runner_reports_no_stance_when_the_pit_cannot_be_left():
    body, report = _Pit(can_escape=False), br.BuildReport()
    runner = br._Runner(body, report, 100, BLOCKS)
    assert not runner._stance((1, 1, 1), set())
    assert report.escapes == 0


def test_runner_does_not_direct_place_from_an_invisible_roof_stance():
    class RoofBody(_Pit):
        def __init__(self):
            super().__init__(can_escape=False)
            self.placements = []

        def can_place_here(self, pos, reach, required):
            return False

        def place_block(self, pos, state, item):
            self.placements.append(pos)
            return br.Result(True)

    body = RoofBody()
    report = br.BuildReport()
    runner = br._Runner(body, report, 100, BLOCKS)
    runner.attempt(bp.Place((1, 2, 0), "minecraft:oak_stairs", "minecraft:oak_stairs"))
    assert not body.placements
    assert report.failures[0][1] == "no_stance"


# --- scaffolding towers to stand on -------------------------------------------
SCAFF = "minecraft:scaffolding[bottom=false,distance=0,waterlogged=false]"


class TowerBody(FakeBody):
    """FakeBody that can also stand inside a scaffolding column (the motor climbs
    it) and whose column drops with its root, as in the game."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.placed_from: dict[bp.Pos, bp.Pos] = {}
        self.planner_placing = True

    def no_placing(self, wall=10.0, climb=False):
        import contextlib

        @contextlib.contextmanager
        def ctx():
            self.planner_placing = False
            try:
                yield
            finally:
                self.planner_placing = True

        return ctx()

    def goto_stance(self, target_pos, reach, avoid=()):
        # Walking only (as the real planner under no_placing): the top of a solid
        # dirt column the body put up cannot be stepped or climbed onto.
        if not self.planner_placing:
            tops = {(x, y + 1, z) for (x, y, z), v in self.w.items() if v == self.DIRT}
            avoid = set(avoid) | (tops - {self.pos})
        return super().goto_stance(target_pos, reach, avoid)

    DIRT = "minecraft:dirt"

    def pillar_to(self, feet, item, n):
        """Pillar straight up the column under ``feet`` with ``item`` only."""
        self.pillared = getattr(self, "pillared", 0) + 1
        x, y, z = feet
        base = y
        while not bp._is_solid(self.w.get((x, base - 1, z)), self.blocks):
            base -= 1
        if y - base > n:
            return False
        for cy in range(base, y):
            self.pos = (x, cy, z)
            if not super().place_block((x, cy, z), self.DIRT, item).ok:
                return False
        self.pos = feet
        return True

    def _stance_ok(self, s, target, reach):
        if str(self.w.get(s)).startswith("minecraft:scaffolding"):
            head = self.w.get((s[0], s[1] + 1, s[2]))
            # only the top of a column is a stance
            return head == AIR and target != s and self._in_reach(target, s)
        return super()._stance_ok(s, target, reach)

    def place_block(self, pos, state, item):
        res = super().place_block(pos, state, item)
        if res.ok:
            self.placed_from[pos] = self.pos
        return res

    def break_block(self, pos, tool=None):
        res = super().break_block(pos, tool)
        above = (pos[0], pos[1] + 1, pos[2])
        while res.ok and str(self.w.get(above)).startswith("minecraft:scaffolding"):
            self.w[above] = AIR
            above = (above[0], above[1] + 1, above[2])
        return res


def tall_column_body(top=6):
    """A column of planks 0..top: the cell at y=6 has no stance on the ground."""
    w = flat_world(hi=14)
    cells = {(0, y, 0): "minecraft:oak_planks" for y in range(top + 1)}
    t = bp.Template.from_build(make_build(cells, (1, top + 1, 1)), (3, 0, 3))
    inv = {item: 100 for item in {i["item"] for i in BLOCKS.values()}}
    inv["minecraft:scaffolding"] = 64
    return TowerBody(w, inv, BLOCKS), t


def test_a_cell_with_no_stance_gets_a_scaffolding_tower_and_it_comes_down():
    body, t = tall_column_body()
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done" and (3, 6, 3) in report.placed
    assert report.scaffolded  # the tower was built...
    top = max(report.scaffolded, key=lambda p: p[1])
    assert body.placed_from[(3, 6, 3)][1] >= 4  # ...and the act done from up on it
    assert top[1] == 5
    d = bp.diff(t, body.world)
    assert (d.breaks, d.places, d.unknown, d.uses) == ([], [], [], [])
    assert not [p for p, s in body.w.items() if s.startswith("minecraft:scaffolding")]


def _no_scaffold_items(body):
    for item in bp.SCAFFOLD_ITEMS:
        body.inv[item] = 0


def test_a_tower_without_scaffolding_stops_the_build_asking_for_it():
    # Run 20261112T143613464413Z: no scaffolding held, every roof cell was probed
    # for a stance again and again (27 cells, 5-12 s of standing still each).
    body, t = tall_column_body()
    _no_scaffold_items(body)
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "need_materials"
    assert report.needed == {"minecraft:scaffolding": 6}
    unreachable = [target for target, _ in body.stance_calls if target[1] >= 5]
    assert len(unreachable) <= 2, unreachable  # asked once, not once per cell


def test_a_tower_short_of_scaffolding_is_built_after_the_refill():
    body, t = tall_column_body()
    _no_scaffold_items(body)
    asked = []

    def refill(items):
        asked.append(dict(items))
        body.inv["minecraft:scaffolding"] = 64
        return True

    report = br.run_build(t, body, BLOCKS, on_need_materials=refill)
    assert asked == [{"minecraft:scaffolding": 6}]
    assert report.stop == "done" and (3, 6, 3) in report.placed


def test_a_tower_is_built_of_dirt_when_no_scaffolding_is_held_and_taken_down():
    body, t = tall_column_body()
    _no_scaffold_items(body)
    body.inv["minecraft:dirt"] = 20
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done" and (3, 6, 3) in report.placed
    assert report.scaffolded
    assert not [p for p in report.scaffolded if body.w.get(p) == "minecraft:dirt"]
    # the body pillared up the column itself (a dirt top cannot be walked onto) and
    # no dirt column is left standing
    assert body.pillared == 1
    assert not [p for p, s in body.w.items() if s == "minecraft:dirt" and p[1] >= 0]


def test_the_retry_round_towers_a_cell_whose_first_tower_was_short():
    body, t = tall_column_body()
    _no_scaffold_items(body)
    body.inv["minecraft:dirt"] = 3  # fewer than the 6-cell tower
    runner = br._Runner(body, br.BuildReport(), 100, BLOCKS)
    runner.scaffolds, runner.template = set(), t
    assert not runner._tower((3, 6, 3), set()) and runner.short
    body.inv["minecraft:dirt"] = 20
    runner.short = {}
    runner.towered.clear()  # what the retry round does
    assert runner._tower((3, 6, 3), set())


def test_the_planner_places_no_dirt_while_the_build_runs():
    body, t = tall_column_body(top=2)
    seen = []
    orig = body.place_block

    def spy(pos, state, item):
        seen.append(body.planner_placing)
        return orig(pos, state, item)

    body.place_block = spy
    br.run_build(t, body, BLOCKS)
    assert seen and not any(seen)


def test_tower_cells_use_the_margin_but_never_template_cells():
    w = flat_world(hi=14)
    at = lambda x, y, z: w.get((x, y, z))  # noqa: E731
    pos = (3, 6, 3)
    around = {(4, 6, 3), (2, 6, 3), (3, 6, 4)}  # three neighbours are house
    cells = bp.tower_cells(pos, at, BLOCKS, around)
    assert cells == [(3, y, 2) for y in range(0, 6)]  # the free one: margin side
    assert bp.tower_cells(pos, at, BLOCKS, around | {(3, 6, 2)}) == []
    assert bp.tower_cells((3, 20, 3), at, BLOCKS) == []  # beyond MAX_TOWER / the map


def _ring_run(size=14, height=2):
    """A hollow cobblestone ring ``size`` wide and ``height`` high on flat ground."""
    cells = {
        (x, y, z): bp_cobble
        for y in range(height)
        for x in range(size)
        for z in range(size)
        if x in (0, size - 1) or z in (0, size - 1)
    }
    t = bp.Template.from_build(make_build(cells, (size, height, size)), (1, 0, 1))
    body = FakeBody(flat_world(-2, size + 6), {bp_cobble: 999}, BLOCKS)
    order, body.walks = [], 0
    place, goto = body.place_block, body.goto_stance

    def counted(*a, **kw):
        before = body.pos
        ok = goto(*a, **kw)
        body.walks += body.pos != before
        return ok

    body.goto_stance = counted

    def logged(pos, state, item):
        result = place(pos, state, item)
        if result.ok:
            order.append(pos)
        return result

    body.place_block = logged
    return t, body, order, br.run_build(t, body, BLOCKS, site_margin=None)


bp_cobble = "minecraft:cobblestone"


def test_ring_is_swept_along_the_wall_not_zigzagged():
    t, body, order, report = _ring_run()
    assert report.stop == "done" and report.skipped == {}
    d = bp.diff(t, body.world)
    assert (d.breaks, d.places, d.unknown, d.uses) == ([], [], [], [])
    assert len(order) == len(report.placed) == 2 * 4 * 13
    # far fewer walks than blocks (it was about one stance per block or two)
    assert body.walks <= 0.4 * len(order), body.walks  # 35; the old order: 54
    # consecutive placements are neighbours or within reach; only a layer's last
    # straggler may be across the ring (not a cross-house hop every few blocks)
    jumps = [_d2(a, b) ** 0.5 for a, b in zip(order, order[1:], strict=False)]
    assert sum(j > br.REACH * 2 for j in jumps) <= 3, sorted(jumps)[-5:]
    assert sum(jumps) <= 2 * len(order), sum(jumps)  # 163; the old order: 475


def test_scaffolding_touching_the_body_is_broken_to_free_it():
    body, _t = tall_column_body()
    x, y, z = body.position()
    wedge, far = (x + 1, y, z), (x + 6, y, z)
    body.w[wedge] = body.w[far] = "minecraft:scaffolding"
    runner = br._Runner(body, br.BuildReport(), 100, BLOCKS)
    runner.scaffolds = {wedge, far}
    assert runner._unwedge()
    assert not body.w[wedge].startswith("minecraft:scaffolding")
    assert runner.scaffolds == {far}  # a tower away from the body is left alone


def test_fetch_first_asks_for_a_lower_layers_item_before_building_above():
    # floor cell (y 1) wants planks that are not held; the wall cell above a placed
    # neighbour is affordable. Without fetch_first the wall goes up over the gap.
    cells = {
        (0, 1, 0): "minecraft:birch_planks",
        (1, 1, 0): "minecraft:stone",
        (1, 2, 0): "minecraft:stone",
    }
    template = bp.Template(cells, (2, 3, 1), (0, 0, 0))
    placed = {(1, 1, 0): "minecraft:stone"}

    def world(x, y, z):
        if y <= 0:
            return "minecraft:stone"
        return placed.get((x, y, z), "minecraft:air")

    blocks = {
        "minecraft:stone": {"solid": True, "item": "minecraft:stone"},
        "minecraft:birch_planks": {"solid": True, "item": "minecraft:birch_planks"},
        "minecraft:air": {"solid": False},
    }
    held = {"minecraft:stone": 4}
    above = bp.next_actions(template, world, blocks, held)
    assert [a.pos for a in above] == [(1, 2, 0)]
    asked = bp.next_actions(template, world, blocks, held, fetch_first=True)
    assert isinstance(asked[0], bp.NeedMaterials)
    assert asked[0].items["minecraft:birch_planks"] == 1


def test_entity_in_way_asks_for_another_stance():
    # a sheep between eye and block: step aside, do not wait for the next round
    pane = (1, 2, 3)
    body, t = small_house_body(fail={pane: ("entity_in_way", 2)})
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done" and report.skipped == {}
    calls = [avoid for target, avoid in body.stance_calls if target == pane]
    assert any(calls[1:])  # a later stance avoids the one the mob blocked


def test_the_next_stance_is_planned_before_the_current_block_is_placed():
    events = []

    class Ahead(FakeBody):
        def plan_ahead(self, pos, reach):
            events.append(("ahead", pos))

        def place_block(self, pos, state, item):
            events.append(("place", pos))
            return super().place_block(pos, state, item)

    inv = {item: 100 for item in {i["item"] for i in BLOCKS.values()}}
    body = Ahead(flat_world(), inv, BLOCKS)
    t = bp.Template.from_build(HOUSE, (1, 0, 1))
    assert br.run_build(t, body, BLOCKS).stop == "done"
    ahead = [i for i, e in enumerate(events) if e[0] == "ahead"]
    assert ahead
    i = ahead[0]
    # the walk to the next target is planned while the current block is still unplaced
    nxt = events[i][1]
    assert not any(e == ("place", nxt) for e in events[:i])
    assert events[i + 1][0] == "place" and events[i + 1][1] != nxt


def test_blocks_that_come_within_reach_are_placed_while_the_body_walks_past():
    class Walker(FakeBody):
        """goto_stance walks in unit steps, calling on_pass at each (the drive)."""

        on_pass = None

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.passed = 0
            self.start = self.pos
            self.placed_log = []

        def goto_stance(self, target_pos, reach, avoid=()):
            before = len(self.stance_calls)
            ok = super().goto_stance(target_pos, reach, avoid)
            if len(self.stance_calls) > before and self.on_pass is not None:
                end = self.pos
                x0, y0, z0 = self.start
                steps = max(abs(end[0] - x0), abs(end[2] - z0), 1)
                for i in range(1, steps + 1):
                    t = i / steps
                    self.pos = (
                        round(x0 + (end[0] - x0) * t),
                        end[1],
                        round(z0 + (end[2] - z0) * t),
                    )
                    n = len(self.placed_log)
                    self.on_pass(self.pos)
                    self.passed += len(self.placed_log) - n
                self.pos = end
            self.start = self.pos
            return ok

        def place_block(self, pos, state, item):
            result = super().place_block(pos, state, item)
            if result.ok:
                self.placed_log.append(pos)
            return result

    size = 14
    cells = {
        (x, y, z): bp_cobble
        for y in range(2)
        for x in range(size)
        for z in range(size)
        if x in (0, size - 1) or z in (0, size - 1)
    }
    t = bp.Template.from_build(make_build(cells, (size, 2, size)), (1, 0, 1))
    body = Walker(flat_world(-2, size + 6), {bp_cobble: 999}, BLOCKS)
    report = br.run_build(t, body, BLOCKS, site_margin=None)
    assert report.stop == "done" and report.skipped == {}
    d = bp.diff(t, body.world)
    assert (d.breaks, d.places, d.unknown, d.uses) == ([], [], [], [])
    assert body.passed > 0, "nothing was placed while passing"
    assert body.on_pass is None  # the hook is removed when the sweep ends
