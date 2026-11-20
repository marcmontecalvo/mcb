import contextlib
import functools
import math

import pytest
from test_build_plan import (
    AIR,
    BLOCKS,
    HOUSE,
    HOUSE_MATERIALS,
    PLANKS,
    flat_world,
    make_build,
    real_house,
)

from mclab import build_plan as bp
from mclab import build_runner as br
from mclab import scaffolding as sc

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
        # CompanionBody.stance_cover: the next cells a stance should also reach
        self.stance_cover: dict = {}
        self.covers: list[set] = []
        self.ranked = True

    # --- BuildBody ---------------------------------------------------------
    def world(self, x, y, z):
        return self.w.get((x, y, z))

    def inventory(self):
        return dict(self.inv)

    def position(self):
        return self.pos

    def stances(self, target_pos, reach, avoid=()):
        tx, ty, tz = target_pos
        spots = [
            (tx + dx, ty + dy, tz + dz)
            for dx in range(-4, 5)
            for dy in range(-5, 3)
            for dz in range(-4, 5)
        ]
        spots = [s for s in spots if s not in avoid]
        return [s for s in spots if self._stance_ok(s, target_pos, reach)]

    def goto_stance(self, target_pos, reach, avoid=()):
        self.covers.append(set(self.stance_cover))
        self.stance_calls.append((target_pos, frozenset(avoid)))
        if self.pos not in avoid and self._stance_ok(self.pos, target_pos, reach):
            return True
        spots = self.stances(target_pos, reach, avoid)
        if not spots:
            return False
        self.pos = min(spots, key=lambda s: (-self._covered(s), _d2(s, self.pos), s))
        return True

    def _covered(self, s):
        """How many stance_cover cells stance ``s`` reaches (CompanionBody.stances
        ranks by it after walkability)."""
        if not self.ranked:
            return 0
        body = (s, (s[0], s[1] + 1, s[2]))
        return sum(self._in_reach(p, s) and p not in body for p in self.stance_cover)

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


def test_survey_walks_only_toward_cells_the_sensor_has_not_delivered():
    body, t = small_house_body()
    seen = {"far": False}
    known = body.world
    # the far half of the footprint stays unknown until the body walks near it
    body.world = lambda x, y, z: (
        None if x >= 5 and not seen["far"] else known(x, y, z)
    )
    walked, placing, mode = [], [], {"placing": True}

    @contextlib.contextmanager
    def no_placing(**_):
        mode["placing"] = False
        yield
        mode["placing"] = True

    body.no_placing = no_placing

    def walk_to(p, radius=1.5):
        walked.append(p)
        placing.append(mode["placing"])
        seen["far"] = seen["far"] or p[0] >= 5
        return True

    body.walk_to = walk_to
    report = br.run_build(t, body, BLOCKS, survey=True)
    # perimeter order starts at the near corner, which is within SURVEY_NEAR too;
    # the walking stops as soon as nothing is unknown
    assert walked and walked == br.perimeter(t)[: len(walked)]
    assert any(p[0] >= 5 for p in walked) and report.surveyed == len(walked)
    assert not any(placing)  # a survey walk never builds its way to a point
    assert report.stop == "done" and report.unknown == []


def test_survey_does_not_walk_when_the_footprint_is_already_delivered():
    body, t = small_house_body()
    walked = []
    body.walk_to = lambda p, radius=1.5: walked.append(p) or True
    report = br.run_build(t, body, BLOCKS, survey=True)
    assert walked == [] and report.surveyed == 0 and report.stop == "done"


def test_unknown_cells_are_reported_when_the_survey_cannot_see_them():
    t = small_house_body()[1]
    short = FakeBody(flat_world(-2, 4), {k: 9 for k in BLOCKS}, BLOCKS)
    cut = br.run_build(t, short, BLOCKS)
    assert cut.unknown and cut.counts["unknown"] == len(cut.unknown)


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

        def can_place_here(self, pos, reach, required, state=None):
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


def test_runner_places_from_here_when_the_body_says_the_server_would_accept_it():
    """can_place_here is the server's own test (real eye, support faces, reach):
    a cell five cells along the floor is placed from where the body stands, not
    after a goto that ends at the same spot (run 20261119T225430670386Z)."""

    class Reaches(_Pit):
        def world(self, *pos):
            return "minecraft:air"

        def __init__(self):
            super().__init__(can_escape=False)
            self.placements, self.gotos = [], []

        def can_place_here(self, pos, reach, required, state=None):
            return True

        def goto_stance(self, pos, reach, avoid=()):
            self.gotos.append(pos)
            return True

        def place_block(self, pos, state, item):
            self.placements.append(pos)
            return br.Result(True)

    body = Reaches()
    report = br.BuildReport()
    runner = br._Runner(body, report, 100, BLOCKS)
    runner.attempt(bp.Place((4, 1, 1), "minecraft:oak_planks", "minecraft:oak_planks"))
    assert body.placements == [(4, 1, 1)]
    assert body.gotos == []
    assert report.placed == [(4, 1, 1)]


def test_runner_still_walks_to_break_a_cell_beyond_reach():
    class Breaks(_Pit):
        def world(self, *pos):
            return "minecraft:air"

        def __init__(self):
            super().__init__(can_escape=False)
            self.breaks, self.gotos = [], []

        def can_place_here(self, pos, reach, required, state=None):
            return True

        def goto_stance(self, pos, reach, avoid=()):
            self.gotos.append(pos)
            return True

        def break_block(self, pos):
            self.breaks.append(pos)
            return br.Result(True)

    body = Breaks()
    runner = br._Runner(body, br.BuildReport(), 100, BLOCKS)
    runner.attempt(bp.Break((6, 0, 0)))
    assert body.gotos == [(6, 0, 0)] and body.breaks == [(6, 0, 0)]


# --- scaffolding towers to stand on -------------------------------------------
SCAFF = "minecraft:scaffolding[bottom=false,distance=0,waterlogged=false]"


class TowerBody(FakeBody):
    """FakeBody that can also stand inside a scaffolding column (the motor climbs
    it) and whose column drops with its root, as in the game."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.placed_from: dict[bp.Pos, bp.Pos] = {}
        self.planner_placing = True

    def no_placing(self, wall=10.0):
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
            pillar = getattr(self, "pillar_cells", set())
            stood = [c for c in pillar if self.w.get(c) not in (None, AIR)]
            tops = {(x, y + 1, z) for x, y, z in stood}
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
            if not super().place_block((x, cy, z), item, item).ok:
                return False
            self.pillar_cells = getattr(self, "pillar_cells", set()) | {(x, cy, z)}
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
        if "minecraft:scaffolding" not in items:
            return False  # the opening foothold stock: none to be had here
        body.inv["minecraft:scaffolding"] = 64
        return True

    report = br.run_build(t, body, BLOCKS, on_need_materials=refill)
    stock = {"minecraft:dirt": br.FOOTHOLD_STOCK}  # asked first, at the start
    assert [a for a in asked if a != stock] == [{"minecraft:scaffolding": 6}]
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


def test_a_dirt_tower_is_walked_to_before_pillaring_up_it():
    # Run 20261118T023332974582Z asked the pillar search from 18 blocks off: with no
    # bridging and 6 blocks to place, 140 searches ran into the node limit.
    body, t = tall_column_body()
    _no_scaffold_items(body)
    body.inv["minecraft:dirt"] = 20
    order = []

    def walk_to(cell, radius=1.5):
        order.append(("walk", tuple(cell)))
        body.pos = tuple(cell)
        return True

    pillar = body.pillar_to

    def pillar_to(feet, item, n):
        order.append(("pillar", (feet[0], feet[2]), body.pos[::2]))
        return pillar(feet, item, n)

    body.walk_to, body.pillar_to = walk_to, pillar_to
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done"
    [(_, column, stood)] = [o for o in order if o[0] == "pillar"]
    assert stood == column  # pillared from the column's own foot
    assert order.index(("walk", (column[0], 0, column[1]))) < len(order) - 1


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


class _Drops(TowerBody):
    """TowerBody whose broken scaffolding comes back to the inventory (the drops
    picked up, as collect_drops does live)."""

    def break_block(self, pos, tool=None):
        column, cell = 0, pos
        while str(self.w.get(cell)).startswith("minecraft:scaffolding"):
            column += 1
            cell = (cell[0], cell[1] + 1, cell[2])
        res = super().break_block(pos, tool)
        if res.ok:
            self.inv[SCAFFOLDING] = self.inv.get(SCAFFOLDING, 0) + column
        return res


SCAFFOLDING = "minecraft:scaffolding"


def _idle_towers(*columns, held_scaffolding=0):
    body, t = tall_column_body()
    body.__class__ = _Drops
    body.inv[SCAFFOLDING] = held_scaffolding
    body.inv["minecraft:dirt"] = 20
    towers = {(x, y, z) for x, z in columns for y in range(6)}
    for c in towers:
        body.w[c] = SCAFF
    runner = br._Runner(body, br.BuildReport(), 300, BLOCKS)
    runner.scaffolds, runner.template = set(towers), t
    return body, runner


def test_a_tower_short_of_scaffolding_takes_an_idle_one_down_rather_than_dirt():
    """Run 20261119T225430670386Z: scaffolding stood idle in the unfinished wing
    while dirt pillars went up the walls (``short`` counted the dirt)."""
    body, runner = _idle_towers((9, 9), held_scaffolding=2)
    assert runner._tower((3, 6, 3), set())
    assert getattr(body, "pillared", 0) == 0  # no dirt column
    assert not any(body.w[(9, y, 9)].startswith(SCAFFOLDING) for y in range(6))
    assert any(
        str(body.w.get((x, y, z))).startswith(SCAFFOLDING)
        for x in (2, 3, 4)
        for z in (2, 3, 4)
        for y in range(6)
    )


def test_only_the_nearest_idle_towers_the_new_one_needs_come_down():
    body, runner = _idle_towers((6, 6), (12, 12))
    assert runner._tower((3, 6, 3), set())
    assert not str(body.w[(6, 0, 6)]).startswith(SCAFFOLDING)
    assert all(body.w[(12, y, 12)] == SCAFF for y in range(6))
    assert {(12, y, 12) for y in range(6)} <= runner.scaffolds


def test_a_failed_pillar_takes_down_its_own_column_only():
    body, t = tall_column_body()
    _no_scaffold_items(body)
    body.inv["minecraft:dirt"] = 20
    idle = {(9, y, 9) for y in range(3)}
    for c in idle:
        body.w[c] = SCAFF
    runner = br._Runner(body, br.BuildReport(), 300, BLOCKS)
    runner.scaffolds, runner.template = set(idle), t
    goto = body.goto_stance
    pos = (3, 6, 3)
    body.goto_stance = lambda target, reach, avoid=(): (
        False if target == pos else goto(target, reach, avoid)
    )
    column = [(2, y, 3) for y in range(6)]
    assert not runner._pillar(pos, column, "minecraft:dirt", set())
    assert all(body.w[c] == SCAFF for c in idle)  # the other tower stays up
    assert runner.scaffolds == idle
    assert not any(body.w[c] == "minecraft:dirt" for c in column)


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
    # 9 with stances ranked by the next cells they reach; 17 by work zone with a
    # stance three cells ahead; 31 by whole layers; 54 for the order before that
    assert body.walks <= 0.1 * len(order), body.walks
    # consecutive placements are neighbours or within reach: no hop across the ring
    # (by whole layers each layer's last straggler was one)
    jumps = [_d2(a, b) ** 0.5 for a, b in zip(order, order[1:], strict=False)]
    assert not [j for j in jumps if j > br.REACH * 2], sorted(jumps)[-5:]
    # 209 by zone (each zone's two courses go out and back along its corner from
    # about the same stances); 163 by whole layers; 475 for the zigzag order
    assert sum(jumps) <= 2.5 * len(order), sum(jumps)


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


def test_the_column_the_body_stands_on_is_dug_down_top_first():
    # Up on its own two-high dirt foothold, the lower block is out of sight behind
    # the one stood on: breaking it first failed 20 times and the run ended trapped.
    body, _t = tall_column_body()
    x, y, z = body.position()
    under = [(x, y - 1, z), (x, y - 2, z)]
    for c in under:
        body.w[c] = "minecraft:dirt"
    tried = []
    real = body.break_block

    def logged(pos, *a, **k):
        tried.append(pos)
        return real(pos, *a, **k)

    body.break_block = logged
    runner = br._Runner(body, br.BuildReport(), 100, BLOCKS)
    runner.scaffolds = set(under)
    assert runner._unwedge()
    assert tried == under  # the block stood on first, then the one below it


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


def test_no_walk_is_planned_ahead_for_a_cell_the_server_would_place_from_here():
    planned = []

    class Ahead(_CoverBody):
        def plan_ahead(self, pos, reach):
            planned.append(pos)

    w = flat_world(-2, 12)
    w[(4, 0, 1)] = PLANKS
    body = Ahead(w, {PLANKS: 8}, BLOCKS, pos=(0, 0, 2))
    acts = [bp.Place((4, 1, 1), PLANKS, PLANKS), bp.Place((4, 0, 2), PLANKS, PLANKS)]
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    br._plan_ahead(runner, acts[1], [acts[0]])
    assert planned == []  # (4, 1, 1): cell distance 18, but in the eye's reach
    br._plan_ahead(runner, acts[0], [bp.Place((9, 0, 2), PLANKS, PLANKS)])
    assert planned == [(9, 0, 2)]


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


def test_stages_are_published_through_site_prep_and_placing():
    body, t = small_house_body()
    body.w[(2, 1, 2)] = "minecraft:dirt"
    seen = []

    def stage(name, **kw):
        seen.append((name, kw.get("detail")))

    br.run_build(t, body, BLOCKS, on_stage=stage)
    names = [n for n, _ in seen]
    assert names[0] == "clearing trees" and "placing" in names
    assert names[-1] in ("placing", "placing details")
    assert any(n == "site prep" and d.startswith("site round") for n, d in seen)


def test_dirt_the_planner_placed_to_walk_is_taken_down_at_the_end():
    body, t = tall_column_body(top=2)
    stray = (9, 1, 9)
    body.w[stray] = "minecraft:dirt"
    body.stray_footholds = {stray}
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done"
    assert body.w.get(stray) in (None, "minecraft:air")
    assert not body.stray_footholds


def test_a_foothold_in_one_of_the_houses_air_cells_is_a_scaffold_taken_back():
    body, t = small_house_body()
    air = next(p for p, st in t.cells.items() if bp.parse_state(st)[0] in bp.AIR)
    solid = next(p for p, st in t.cells.items() if bp.parse_state(st)[0] not in bp.AIR)
    scaffolds = set()
    body.stray_footholds = {air, solid, (40, 1, 40)}
    br._adopt_footholds(body, t, scaffolds)
    assert scaffolds == {air, (40, 1, 40)}  # the house's own cell is the house's
    body.w[air] = "minecraft:dirt"
    body.stray_footholds = {air}
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done"
    assert body.w.get(air) in (None, "minecraft:air")


def test_blocks_the_planner_broke_are_put_back_and_template_cells_left_alone():
    body, t = small_house_body()
    dug, template_cell = (7, 0, 7), next(iter(t.cells))
    body.w[dug] = "minecraft:air"
    body.stray_breaks = {dug: "minecraft:dirt", template_cell: "minecraft:dirt"}
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done"
    assert body.w.get(dug) == "minecraft:dirt" and dug in report.restored
    assert template_cell not in report.restored
    assert template_cell not in report.unrestored
    assert not body.stray_breaks


def test_a_break_whose_item_is_not_held_is_reported_not_failed():
    body, t = small_house_body()
    body.inv.pop("minecraft:dirt", None)
    dug = (7, 0, 7)
    body.stray_breaks = {dug: "minecraft:dirt"}
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done"
    assert dug in report.unrestored and dug not in report.restored


def test_a_cell_with_no_tower_site_asks_for_dirt_when_nothing_is_held(monkeypatch):
    body, t = tall_column_body()
    _no_scaffold_items(body)
    body.inv["minecraft:cobblestone"] = 0
    monkeypatch.setattr(bp, "tower_cells", lambda *a, **k: [])
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "need_materials"
    assert "minecraft:dirt" in report.needed


def test_a_broken_stone_is_put_back_as_the_cobblestone_it_dropped():
    body, t = small_house_body()
    body.inv.pop("minecraft:stone", None)
    dug = (7, 0, 7)
    body.w[dug] = "minecraft:air"
    body.stray_breaks = {dug: "minecraft:stone"}
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done"
    assert dug in report.restored and dug not in report.unrestored
    assert body.w[dug] == "minecraft:cobblestone"


def test_a_break_is_unrestored_only_when_neither_it_nor_its_drop_is_held():
    body, t = small_house_body()
    body.w[(7, 0, 7)] = "minecraft:air"
    body.stray_breaks = {(7, 0, 7): "minecraft:deepslate"}
    report = br.run_build(t, body, BLOCKS)
    assert (7, 0, 7) in report.unrestored


def test_a_door_opened_by_the_last_cleanup_walk_is_closed_despite_a_stale_sensor(
    monkeypatch,
):
    body, template = small_house_body()
    stale: dict = {}
    reads = [0]

    def world(x, y, z):
        reads[0] += 1
        if stale and reads[0] > len(template.cells):  # one audit later it has caught up
            stale.clear()
        return stale.get((x, y, z), body.w.get((x, y, z)))

    body.world = world
    body.wait_ticks = lambda n: stale.clear()  # or when the build waits for it

    def cleanup(runner, template, scaffolds):
        for pos, state in body.w.items():
            if "_door[" in state and "half=lower" in state:
                stale[pos] = state  # what the sensor still shows
                reads[0] = 0
                body.w[pos] = state.replace("open=false", "open=true")
                return True
        raise AssertionError("Fixture has no built door")

    monkeypatch.setattr(br, "_clean_up", cleanup)
    report = br.run_build(template, body, BLOCKS)
    assert report.stop == "done" and not bp.diff(template, body.world).uses


def test_a_final_audit_waits_for_the_sensor_before_calling_a_built_house_blocked(
    monkeypatch,
):
    # run 20261116T232024268879Z: stop "blocked" with all 84 correct
    body, template = small_house_body()
    stale: dict = {}
    waits = [0]

    def world(x, y, z):
        return stale.get((x, y, z), body.w.get((x, y, z)))

    def wait_ticks(n):
        waits[0] += 1
        if waits[0] >= 2:  # the sensor catches up only on a later wait
            stale.clear()

    body.world = world
    body.wait_ticks = wait_ticks

    def cleanup(runner, template, scaffolds):
        pos = next(p for p, s in body.w.items() if "planks" in s)
        stale[pos] = "minecraft:air"  # what the sensor still shows
        waits[0] = 0

    monkeypatch.setattr(br, "_clean_up", cleanup)
    report = br.run_build(template, body, BLOCKS)
    assert report.stop == "done"


def test_cleanup_may_open_doors_and_a_blocked_loop_still_repairs_them(
    monkeypatch,
):
    from contextlib import contextmanager

    body, template = small_house_body()
    state = {"depth": 0, "in_cleanup": []}

    @contextmanager
    def no_opening_doors():
        state["depth"] += 1
        try:
            yield
        finally:
            state["depth"] -= 1

    body.no_opening_doors = no_opening_doors
    real_diff = bp.diff
    first = [True]

    def diff(t, w, *rest):  # the main loop's first empty-action audit sees a gap
        d = real_diff(t, w, *rest)
        if not rest and first[0] and not d.places and not d.breaks:
            first[0] = False
            d.places.append(next(iter(t.cells)))
        return d

    def cleanup(runner, template, scaffolds):
        state["in_cleanup"].append(state["depth"])
        for pos, s in body.w.items():
            if "_door[" in s and "half=lower" in s:
                body.w[pos] = s.replace("open=false", "open=true")
                return True
        raise AssertionError("no door")

    monkeypatch.setattr(bp, "diff", diff)
    monkeypatch.setattr(br, "_clean_up", cleanup)
    report = br.run_build(template, body, BLOCKS)
    # cleanup walks use doors; the body closes them behind it
    assert state["in_cleanup"] == [0]
    assert report.stop == "done" and not real_diff(template, body.world).uses


class ReclaimBody(TowerBody):
    """TowerBody whose broken scaffolding comes back into the inventory."""

    def break_block(self, pos, tool=None):
        column = []
        above = pos
        while str(self.w.get(above)).startswith("minecraft:scaffolding"):
            column.append(above)
            above = (above[0], above[1] + 1, above[2])
        res = super().break_block(pos, tool)
        if res.ok:
            self.inv["minecraft:scaffolding"] = self.inv.get(
                "minecraft:scaffolding", 0
            ) + len(column)
        return res


def test_one_stack_of_scaffolding_is_taken_down_and_used_again_for_the_next_tower():
    # run 20261117T130121185147Z: every tower stood to the end of the build, so the
    # body ran out of scaffolding and climbed the walls on dirt instead
    w = flat_world(hi=14)
    cells = {(x, y, 0): "minecraft:oak_planks" for x in (0, 8) for y in range(7)}
    t = bp.Template.from_build(make_build(cells, (9, 7, 1)), (3, 0, 3))
    inv = {item: 100 for item in {i["item"] for i in BLOCKS.values()}}
    for item in bp.SCAFFOLD_ITEMS:
        inv[item] = 0
    inv["minecraft:scaffolding"] = 6  # one tower's worth
    inv["minecraft:dirt"] = 64  # held, as in the run: the old code pillared it
    body = ReclaimBody(w, inv, BLOCKS)
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done", report.stop
    assert (3, 6, 3) in report.placed and (11, 6, 3) in report.placed
    assert not getattr(body, "pillared", 0)  # no dirt column instead of a tower
    assert not [p for p, s in body.w.items() if s.startswith("minecraft:scaffolding")]


def test_a_tower_refill_that_fails_skips_that_cell_and_the_build_goes_on():
    # Run 20261117T140114411942Z: one upper-floor cell wanted 2 dirt for a pillar,
    # none could be had, and the build stopped at 416 of 1227 placed.
    body, t = tall_column_body()
    _no_scaffold_items(body)
    asked = []
    report = br.run_build(
        t, body, BLOCKS, on_need_materials=lambda m: asked.append(dict(m)) or False
    )
    stock = {"minecraft:dirt": br.FOOTHOLD_STOCK}  # asked first, at the start
    assert [a for a in asked if a != stock] == [{"minecraft:scaffolding": 6}]
    assert report.stop != "need_materials"
    assert report.skipped.get((3, 6, 3)) == "no_material"
    assert [p for p in t.cells if p[1] < 5 and p in report.placed]


def test_without_scaffolding_dirt_or_cobblestone_a_tower_is_of_a_spare_plain_block():
    from mclab import temporary_blocks as tb
    from mclab.companion_jobs import load_vanilla_pack

    body, t = tall_column_body()
    _no_scaffold_items(body)
    body.inv["minecraft:stone"] = 64  # breaks to cobblestone: never a temporary
    body.inv["minecraft:oak_log"] = 20  # comes back as itself: a temporary
    allowed = tb.eligible(load_vanilla_pack())
    body.temporary_ok = allowed
    body.still_needed = lambda: {"minecraft:oak_planks": 100}  # none spare
    body.temporary_items = lambda scaffolding=True: tb.order(
        body.inv, body.still_needed(), allowed
    )
    used = []
    pillar = body.pillar_to
    body.pillar_to = lambda feet, item, n: used.append(item) or pillar(feet, item, n)
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done" and (3, 6, 3) in report.placed
    assert used and set(used) == {"minecraft:oak_log"}, used


def test_a_body_boxed_in_on_a_ledge_gets_down_then_builds_on():
    # Runs 20261117T144843447702Z and 20261117T155135309814Z: on an eave slab 6
    # above the ground, the house's glass and flower pots on every side; every plan
    # was unreachable after one node and the build stalled.
    w = flat_world()
    t = bp.Template.from_build(
        make_build({(0, 0, 0): "minecraft:oak_planks"}, (1, 1, 1)), (3, 0, 3)
    )
    inv = {item: 100 for item in {i["item"] for i in BLOCKS.values()}}
    body = FakeBody(w, inv, BLOCKS)
    body.boxed_in = True
    reach, downs = body.goto_stance, []

    def goto_stance(pos, reach_, avoid=()):
        return not body.boxed_in and reach(pos, reach_, avoid)

    def get_down():
        downs.append(1)
        body.boxed_in = False
        return True

    body.goto_stance = goto_stance
    body.get_down = get_down
    report = br.run_build(t, body, BLOCKS)
    assert report.stop == "done" and (3, 0, 3) in report.placed
    assert downs == [1]


def test_a_drop_is_never_the_easy_way_while_the_body_can_move():
    body, t = tall_column_body()
    _no_scaffold_items(body)
    downs = []
    body.boxed_in = False
    body.get_down = lambda: downs.append(1) or True
    runner = br._Runner.__new__(br._Runner)
    runner.body, runner.short, runner.scaffolds = body, {}, set()
    body.goto_stance = lambda *a, **k: False
    runner._tower = lambda *a, **k: False
    runner._unwedge = lambda: False
    body.escape_hole = lambda pos: False
    assert runner._stance((3, 6, 3), set()) is False
    assert not downs


def test_the_build_starts_holding_spare_footholds():
    # Run 20261117T144843447702Z: site prep spent all its dirt; the build began with
    # nothing to pillar or step down on.
    body, t = tall_column_body()
    _no_scaffold_items(body)
    asked = []
    br.run_build(t, body, BLOCKS, on_need_materials=lambda m: asked.append(dict(m)))
    assert asked and asked[0] == {"minecraft:dirt": br.FOOTHOLD_STOCK}

    body, t = tall_column_body()  # holds 100 dirt: nothing to ask for
    asked = []
    br.run_build(t, body, BLOCKS, on_need_materials=lambda m: asked.append(dict(m)))
    assert {"minecraft:dirt": br.FOOTHOLD_STOCK} not in asked


def test_footholds_spent_mid_build_are_topped_up_and_a_dry_chest_is_not_pestered():
    # Run 20261117T165351012696Z: the 32 were spent on pillars and the last 300 s
    # planned every climb with nothing to place (58 searches of 10,000 nodes).
    class Runner:
        def __init__(self):
            self.held = {"minecraft:dirt": 3}
            self.body = self

        def inventory(self):
            return dict(self.held)

        def _temporaries(self, scaffolding=True):
            return ["minecraft:dirt"]

    runner, asks, stock = Runner(), [], {}

    def refill(items):  # the chests hand over what is asked
        asks.append(dict(items))
        runner.held["minecraft:dirt"] += items.get("minecraft:dirt", 0)

    br._keep_footholds(runner, refill, lambda _m: None, stock)
    assert asks == [{"minecraft:dirt": br.FOOTHOLD_STOCK - 3}]
    br._keep_footholds(runner, refill, lambda _m: None, stock)
    assert len(asks) == 1  # stocked: nothing more to ask

    runner.held["minecraft:dirt"] = 0  # spent again, and now the chests are dry
    dry = lambda items: asks.append(dict(items))  # noqa: E731
    for _ in range(br.FOOTHOLD_RETRY + 1):
        br._keep_footholds(runner, dry, lambda _m: None, stock)
    assert len(asks) == 2  # one dry ask, then a wait of FOOTHOLD_RETRY actions
    br._keep_footholds(runner, dry, lambda _m: None, stock)
    assert len(asks) == 3


def test_a_starving_body_with_no_food_goes_to_the_chests_once():
    class Body:
        def __init__(self):
            self.ate, self.food_held = 0, 0

        def eat_when_needed(self):
            self.ate += 1

        def held_food(self):
            return self.food_held

        def food(self):
            return 4

    body, asks, hunger = Body(), [], {}
    ask = lambda items: asks.append(items) or False  # noqa: E731
    for _ in range(3):
        br._tend_hunger(body, ask, hunger)
    assert body.ate == 3 and asks == [{}]  # one trip, not one per action
    body.food_held = 5  # handed some food: a later hunger may ask again
    br._tend_hunger(body, ask, hunger)
    body.food_held = 0
    br._tend_hunger(body, ask, hunger)
    assert asks == [{}, {}]


def test_a_block_that_would_shut_the_body_on_its_ledge_is_placed_from_elsewhere():
    # Run 20261117T155135309814Z: glass set behind an eave slab between flower pots
    # left the body no way off but a 6-block fall.
    w = flat_world(hi=10)
    for y in range(0, 5):
        for x in range(0, 4):
            for z in range(0, 3):
                w[(x, y, z)] = AIR
    ledge = (2, 6, 1)
    w[(2, 5, 1)] = PLANKS
    for y in (6, 7, 8):
        w[(1, y, 1)] = w[(3, y, 1)] = w[(2, y, 0)] = PLANKS
    w[(2, 8, 1)] = PLANKS
    for x in range(-2, 8):
        for z in range(2, 8):
            w[(x, 5, z)] = PLANKS  # the upper floor inside
    window = (2, 6, 2)
    body = FakeBody(w, {PLANKS: 5}, BLOCKS, pos=ledge)
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    runner.attempt(bp.Place(window, PLANKS, PLANKS, 1))
    assert window in runner.report.placed
    assert body.pos != ledge
    assert all(ledge in avoid for _, avoid in body.stance_calls)


def test_scaffolding_already_gone_is_not_broken_again_to_unwedge():
    # Run 20261117T200337044791Z: stale scaffold cells beside the body were broken
    # over and over (nothing_to_break), each "freeing" it for another failed walk.
    body, _t = tall_column_body()
    x, y, z = body.position()
    gone = (x + 1, y, z)
    body.w[gone] = "minecraft:air"
    tried = []
    real = body.break_block

    def logged(pos, *a, **k):
        tried.append(pos)
        return real(pos, *a, **k)

    body.break_block = logged
    runner = br._Runner(body, br.BuildReport(), 100, BLOCKS)
    runner.scaffolds = {gone}
    assert not runner._unwedge()
    assert tried == []
    assert runner.scaffolds == set()


class HopperBody(FakeBody):
    """A hopper faces the block it was clicked against: placed only with a block
    in the cell its facing points to."""

    def place_block(self, pos, state, item):
        ident, props = bp.parse_state(state)
        if ident == "minecraft:hopper":
            ahead = bp._add(pos, bp._VEC[props["facing"]])
            if bp.parse_state(self.w.get(ahead) or AIR)[0] in bp.AIR:
                return br.Result(False, "state_unreachable")
        return super().place_block(pos, state, item)

    def break_block(self, pos):
        cur = self.w.get(pos)
        self.w[pos] = AIR
        if cur:
            self.inv[cur] = self.inv.get(cur, 0) + 1
        return br.Result(True)


def test_two_hoppers_facing_each_other_go_in_against_a_temporary_block():
    # Run 20261118T070135016043Z: each hopper needed the other to click against;
    # both were skipped state_unreachable.
    w = flat_world()
    east = "minecraft:hopper[enabled=true,facing=east]"
    west = "minecraft:hopper[enabled=true,facing=west]"
    held = {"minecraft:hopper": 2, "minecraft:dirt": 4}
    body = HopperBody(w, held, BLOCKS, pos=(1, 0, 2))
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    runner.attempt(bp.Place((1, 0, 0), east, "minecraft:hopper", 1))
    runner.attempt(bp.Place((2, 0, 0), west, "minecraft:hopper", 1))
    assert w[(1, 0, 0)] == east and w[(2, 0, 0)] == west
    assert body.inv["minecraft:dirt"] == 4  # the temporary block was taken back
    assert not runner.report.skipped


class BlindBody(TowerBody):
    """A cell whose faces are all hidden from below (a chain under the roof behind
    stairs): placing it from more than two below fails state_unreachable with
    nothing visible, as the server diagnoses it."""

    hidden = (3, 5, 3)

    def place_block(self, pos, state, item):
        if pos == self.hidden and self.pos[1] < pos[1] - 2:
            source = {"points_in_reach": 9, "points_visible": 0}
            return br.Result(
                False, "state_unreachable", {"diagnosis": {"sources": [source]}}
            )
        return super().place_block(pos, state, item)


def test_a_cell_hidden_from_every_floor_stance_is_placed_from_a_tower_beside_it():
    # run 20261118T094938114426Z: a gable chain was tried three times from the
    # floor five below, through the roof stairs, then given up.
    body, t = tall_column_body(top=5)
    body.__class__ = BlindBody
    report = br.run_build(t, body, BLOCKS)
    assert (3, 5, 3) in report.placed and (3, 5, 3) not in report.skipped
    assert body.placed_from[(3, 5, 3)][1] >= 3
    assert report.stop == "done"


def test_roof_level_tower_allows_roof_stances_and_restores_placing_scaffold():
    # Interior ceiling at (4, 4, 4) surrounded at y=3: tower goes to y=4 at an adjacent
    # column, roof stance (4, 5, 2) or tower top (col_x, 5, col_z) is above pos[1], and
    # placing_scaffold is restored to False for placing the target plank.
    w = flat_world(hi=10)
    target = (4, 4, 4)
    reserved = {
        (4, 3, 4),
        (5, 3, 4),
        (3, 3, 4),
        (4, 3, 5),
        (4, 3, 3),
        (5, 4, 4),
        (3, 4, 4),
        (4, 4, 5),
        (4, 4, 3),
    }

    class RoofStanceBody(TowerBody):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.placing_scaffold = False
            self.seen_placing_scaffold = None
            self.avoided_on_target = None

        def stances(self, pos, reach, avoid=()):
            if pos == target:
                return [(4, 1, 4), (4, 5, 3), (2, 5, 4)]
            return super().stances(pos, reach, avoid)

        def goto_stance(self, target_pos, reach, avoid=()):
            if target_pos == target and self.scaffolds_built:
                self.seen_placing_scaffold = self.placing_scaffold
                self.avoided_on_target = set(avoid)
            return super().goto_stance(target_pos, reach, avoid)

    cells = {p: PLANKS for p in reserved}
    cells[target] = PLANKS
    t = bp.Template.from_build(make_build(cells, (8, 8, 8)), (0, 0, 0))
    body = RoofStanceBody(w, {PLANKS: 20, "minecraft:scaffolding": 64}, BLOCKS)
    body.scaffolds_built = False
    orig_attempt = br._Runner.attempt

    runner = br._Runner(body, br.BuildReport(), 100, BLOCKS)
    runner.scaffolds = set()
    runner.template = t
    runner.reserved = reserved | {target}

    def wrapped_attempt(act):
        if isinstance(act, bp.Scaffold):
            body.scaffolds_built = True
        return orig_attempt(runner, act)

    runner.attempt = wrapped_attempt
    body.placing_scaffold = False
    assert runner._tower(target, set())
    assert body.seen_placing_scaffold is False
    assert (4, 1, 4) in body.avoided_on_target
    assert (4, 5, 3) not in body.avoided_on_target


def test_tall_scaffolding_placed_directly_when_can_place_here_reaches_column():
    w = flat_world(hi=14)
    for y in range(0, 10):
        w[(0, y, 0)] = "minecraft:scaffolding"

    class ColumnBody(FakeBody):
        placing_scaffold = False

        def can_place_here(self, pos, reach, required=None, state=None):
            return pos == (0, 10, 0) and self.placing_scaffold and self.pos == (2, 0, 0)

        def place_block(self, pos, state, item):
            if self.can_place_here(pos, br.REACH):
                self.w[pos] = state
                self.inv[item] -= 1
                return br.Result(True, data={"placed": state})
            return super().place_block(pos, state, item)

    body = ColumnBody(w, {"minecraft:scaffolding": 64}, BLOCKS, pos=(2, 0, 0))
    runner = br._Runner(body, br.BuildReport(), 20, BLOCKS)
    runner.scaffolds = {(0, y, 0) for y in range(0, 10)}
    runner.attempt(bp.Scaffold((0, 10, 0), "minecraft:scaffolding"))
    assert (0, 10, 0) in runner.report.scaffolded
    assert not body.stance_calls


def test_mutual_hoppers_move_stance_to_see_temporary_face_and_reclaim_dirt():
    w = flat_world()
    east = "minecraft:hopper[enabled=true,facing=east]"
    west = "minecraft:hopper[enabled=true,facing=west]"
    held = {"minecraft:hopper": 2, "minecraft:dirt": 4}

    class SightHopperBody(HopperBody):
        required_support = None
        placing_scaffold = False

        def can_place_here(self, pos, reach, required=None, state=None):
            if required is None:
                return True
            sup = tuple(required)
            # Only see the west face of sup when standing west of pos, etc.
            if sup[0] > pos[0]:
                return self.pos[0] <= pos[0]
            if sup[0] < pos[0]:
                return self.pos[0] >= pos[0]
            return True

        def goto_stance(self, target_pos, reach, avoid=()):
            super().goto_stance(target_pos, reach, avoid)
            req = self.required_support
            if req is not None:
                sup = tuple(req)
                if sup[0] > target_pos[0]:
                    self.pos = (target_pos[0] - 1, target_pos[1], target_pos[2])
                elif sup[0] < target_pos[0]:
                    self.pos = (target_pos[0] + 1, target_pos[1], target_pos[2])
            else:
                self.pos = (target_pos[0], target_pos[1], target_pos[2] + 1)
            return True

        def place_block(self, pos, state, item):
            ident, props = bp.parse_state(state)
            if ident == "minecraft:hopper":
                ahead = bp._add(pos, bp._VEC[props["facing"]])
                if not self.can_place_here(pos, br.REACH, ahead):
                    return br.Result(False, "state_unreachable")
            return super().place_block(pos, state, item)

    body = SightHopperBody(w, held, BLOCKS, pos=(0, 0, 2))
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    runner.scaffolds = set()
    runner.attempt(bp.Place((1, 0, 0), east, "minecraft:hopper", 1))
    runner.attempt(bp.Place((2, 0, 0), west, "minecraft:hopper", 1))
    assert w[(1, 0, 0)] == east and w[(2, 0, 0)] == west
    assert body.inv["minecraft:dirt"] == 4
    assert not runner.report.skipped


def test_reclaim_does_not_poison_skipped_for_unreachable_scaffolds():
    w = flat_world()
    w[(5, 0, 5)] = "minecraft:dirt"
    t = bp.Template.from_build(make_build({(0, 0, 0): PLANKS}, (6, 4, 6)), (0, 0, 0))

    class ReclaimBody(FakeBody):
        def goto_stance(self, target_pos, reach, avoid=()):
            if target_pos == (5, 0, 5):
                return False
            return super().goto_stance(target_pos, reach, avoid)

    body = ReclaimBody(w, {PLANKS: 4}, BLOCKS, pos=(0, 0, 2))
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    runner.template = t
    runner.scaffolds = {(5, 0, 5)}
    runner._reclaim((0, 2, 0), (0, 0, 0), set(), (frozenset({"minecraft:dirt"}), 1))
    assert (5, 0, 5) not in runner.report.skipped
    assert (5, 0, 5) in runner.scaffolds


def test_run_build_emits_detail_and_cleanup_stages_and_long_stops():
    w = flat_world()
    cells = {
        (0, 0, 0): PLANKS,
        (1, 0, 0): "minecraft:oak_door[facing=north,half=lower,hinge=left,open=false]",
        (1, 1, 0): "minecraft:oak_door[facing=north,half=upper,hinge=left,open=false]",
    }
    t = bp.Template.from_build(make_build(cells, (4, 4, 4)), (0, 0, 0))
    held = {PLANKS: 4, "minecraft:oak_door": 1}
    tick = [0.0]

    class SlowFakeBody(FakeBody):
        def place_block(self, pos, state, item):
            tick[0] += 6.5 if pos == (0, 0, 0) else 0.1
            return super().place_block(pos, state, item)

    body = SlowFakeBody(w, held, BLOCKS)
    w[(3, 0, 3)] = "minecraft:dirt"
    body.stray_footholds = {(3, 0, 3)}
    stages = []
    report = br.run_build(
        t,
        body,
        BLOCKS,
        site_margin=None,
        stall_seconds=5.0,
        clock=lambda: tick[0],
        on_stage=lambda name, *a, **k: stages.append(name),
    )
    assert report.stop == "done"
    assert "placing" in stages
    assert "placing details" in stages
    assert "cleanup" in stages
    assert len(report.long_stops) == 1
    assert report.long_stops[0]["kind"] == "slow_op"
    assert report.long_stops[0]["duration_s"] == 6.5


class _Recorder(FakeBody):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.tried: list = []

    def place_block(self, pos, state, item):
        self.tried.append(pos)
        return super().place_block(pos, state, item)


def test_blocks_are_never_placed_into_the_route_the_body_is_walking():
    # Run 20261119T133618892664Z: every route_target_unknown_or_changed and
    # route_swept_geometry_unknown_or_unsafe came within 40 ticks of a block placed
    # while passing; one walk had 12 blocks placed into the steps ahead of the body.
    route = [
        (0, 0, 0),
        (1, 0, 0),
        (2, 0, 0),
        (3, 0, 0),
        (4, 0, 0),
        (5, 1, 0),  # a step up: the take-off column needs head room for the jump
        (6, 1.5, 0),  # a slab (fractional y): the body reaches into y 3
        (7, 1.5, 1),  # a diagonal: both corner columns are swept
    ]
    in_path = [
        (0, 1, 0),  # the body's own head
        (2, 0, 0),  # feet
        (3, 1, 0),  # head
        (4, 2, 0),  # jump room over the take-off
        (5, 2, 0),  # head on the step
        (6, 3, 0),  # head over the slab
        (6, 2, 1),  # diagonal corner
        (7, 1, 0),  # diagonal corner
    ]
    beside = [(2, 0, 1), (3, 0, -1), (5, 0, 1)]
    body = _Recorder(flat_world(hi=10), {PLANKS: 64}, BLOCKS)
    runner = br._Runner(body, br.BuildReport(), 100, BLOCKS)
    pool = [bp.Place(p, PLANKS, PLANKS, 1) for p in in_path + beside]
    on_pass = br._passing(runner, pool, set())
    for position in [(0.5, 0, 0.5), (3.5, 0, 0.5), (6.5, 1.5, 0.5)]:
        body.pos = tuple(math.floor(v) for v in position)
        for _ in range(len(in_path + beside)):
            on_pass(position, route=route)
    assert not set(body.tried) & set(in_path)
    assert set(beside) <= {p for p in body.tried if body.w[p] == PLANKS}


def test_the_route_ahead_starts_at_the_edge_being_driven():
    # cells already walked past are free to build in again
    route = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)]
    body = _Recorder(flat_world(hi=10), {PLANKS: 64}, BLOCKS)
    runner = br._Runner(body, br.BuildReport(), 100, BLOCKS)
    pool = [bp.Place((0, 0, 0), PLANKS, PLANKS, 1)]
    on_pass = br._passing(runner, pool, set())
    on_pass((2.5, 0, 0.5), route=route[1:])
    assert body.tried == [(0, 0, 0)]


class _CoverBody(FakeBody):
    """FakeBody with CompanionBody's ``can_place_here``: reach from where it
    stands, never into its own cells."""

    def __init__(self, *a, ranked=True, **kw):
        super().__init__(*a, **kw)
        self.ranked = ranked

    def can_place_here(self, pos, reach, required=None, state=None):
        x, y, z = self.pos
        return self._in_reach(pos) and pos not in (self.pos, (x, y + 1, z))


def _row_sweep(ranked):
    w = flat_world(-2, 24)
    body = _CoverBody(w, {PLANKS: 64}, BLOCKS, pos=(0, 0, 4), ranked=ranked)
    pool = [bp.Place((x, 0, 0), PLANKS, PLANKS) for x in range(20)]
    runner = br._Runner(body, br.BuildReport(), 500, BLOCKS)
    moves = []
    goto = body.goto_stance

    def counting(target, reach, avoid=()):
        before = body.pos
        ok = goto(target, reach, avoid)
        moves.extend([body.pos] if body.pos != before else [])
        return ok

    body.goto_stance = counting
    br._sweep_pool(runner, pool, set(), None, False)
    return body, runner.report, moves


def test_a_sweep_stands_where_one_walk_places_several_cells_of_the_wall():
    """Run 20261119T225430670386Z: 119 walks to stances at the edge of reach, one
    block per walk. The sweep names the next cells; the stance covers them."""
    body, report, moves = _row_sweep(ranked=True)
    _, _, nearest_moves = _row_sweep(ranked=False)
    assert sorted(report.placed) == [(x, 0, 0) for x in range(20)]
    assert body.covers and all(body.covers)  # every walk named the cells to cover
    assert len(moves) < len(nearest_moves)
    assert body.stance_cover == {}  # not left set for the next caller


def test_the_sweep_places_what_the_server_would_accept_without_walking():
    w = flat_world(-2, 12)
    w[(4, 0, 1)] = PLANKS  # a face to click
    body = _CoverBody(w, {PLANKS: 8}, BLOCKS, pos=(0, 0, 2))
    act = bp.Place((4, 1, 1), PLANKS, PLANKS)  # cell distance 18 > (REACH-0.5)^2
    assert body.can_place_here(act.pos, br.REACH)
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    br._sweep_pool(runner, [act], set(), None, False)
    assert runner.report.placed == [act.pos]
    assert body.stance_calls == []


SLAB = "minecraft:oak_slab"
TOP_SLAB = SLAB + "[type=top,waterlogged=false]"
BOTTOM_SLAB = SLAB + "[type=bottom,waterlogged=false]"
EAST_HOPPER = "minecraft:hopper[enabled=true,facing=east]"


class _StateBody(_CoverBody):
    """Records the block state each sight check and each stance search was for:
    a slab's half, a log's axis or a hopper's facing decides which faces can make
    it (place_state; run 20261120T083111010944Z, 28 wrong_state failures)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.required_support = None
        self.placing_scaffold = False
        self.placing_state = None
        self.asked: list = []
        self.aimed: list = []

    def can_place_here(self, pos, reach, required=None, state=None):
        self.asked.append((pos, state))
        return super().can_place_here(pos, reach, required, state)

    def goto_stance(self, target_pos, reach, avoid=()):
        self.aimed.append(self.placing_state)
        return super().goto_stance(target_pos, reach, avoid)


def test_the_runner_asks_for_a_stance_that_makes_the_state_it_places():
    body = _StateBody(flat_world(-2, 12), {SLAB: 4}, BLOCKS, pos=(0, 0, 8))
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    runner.attempt(bp.Place((0, 0, 0), TOP_SLAB, SLAB))
    assert ((0, 0, 0), TOP_SLAB) in body.asked  # the direct check
    assert body.aimed == [TOP_SLAB]  # the walk
    assert runner.report.placed == [(0, 0, 0)]


def test_a_scaffold_stance_is_asked_for_scaffolding():
    body = _StateBody(flat_world(-2, 12), {sc.BLOCK: 4}, BLOCKS, pos=(0, 0, 8))
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    runner.attempt(bp.Scaffold((0, 1, 0), sc.BLOCK))
    assert body.aimed[:1] == [sc.BLOCK]


def test_the_sweep_covers_each_next_cell_with_the_state_it_needs():
    body = _StateBody(flat_world(-2, 12), {SLAB: 4}, BLOCKS)
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    acts = [
        bp.Place((0, 1, 0), TOP_SLAB, SLAB),
        bp.Place((1, 1, 0), BOTTOM_SLAB, SLAB),
        bp.Place((2, 1, 0), TOP_SLAB, SLAB),
    ]
    assert br._cover(runner, acts[0], acts) == {
        (1, 1, 0): (None, BOTTOM_SLAB),
        (2, 1, 0): (None, TOP_SLAB),
    }


def test_reach_checks_while_sweeping_and_passing_name_the_state():
    body = _StateBody(flat_world(-2, 12), {SLAB: 4}, BLOCKS, pos=(0, 0, 2))
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    act = bp.Place((1, 1, 0), BOTTOM_SLAB, SLAB)
    assert br._in_reach(runner, act, body.pos)
    assert body.asked == [((1, 1, 0), BOTTOM_SLAB)]
    body.asked.clear()
    br._passing(runner, [act], set())((0.5, 0, 2.5), route=[])
    assert body.asked == [((1, 1, 0), BOTTOM_SLAB)]


def test_a_propped_hopper_aims_the_temporary_block_then_the_hopper():
    w = flat_world(-2, 12)

    class PropBody(HopperBody, _StateBody):
        pass

    body = PropBody(w, {"minecraft:hopper": 1, "minecraft:dirt": 2}, BLOCKS)
    body.pos = (1, 0, 2)
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    runner.scaffolds = set()
    runner.attempt(bp.Place((1, 0, 0), EAST_HOPPER, "minecraft:hopper", 1))
    assert w[(1, 0, 0)] == EAST_HOPPER
    asked = [s for p, s in body.asked]
    first_dirt = asked.index("minecraft:dirt")
    assert body.asked[first_dirt] == ((2, 0, 0), "minecraft:dirt")
    assert EAST_HOPPER in asked[first_dirt:]  # then the hopper against it
    assert body.placing_state == EAST_HOPPER  # restored for the caller


def test_a_tower_leaves_the_aim_of_the_cell_it_was_built_for():
    body = _StateBody(flat_world(-2, 14), {sc.BLOCK: 16}, BLOCKS, pos=(0, 0, 4))
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    runner.scaffolds = set()
    body.placing_state, body.required_support = TOP_SLAB, (0, 6, 1)
    runner._tower((0, 6, 0), set())
    assert (body.placing_state, body.required_support) == (TOP_SLAB, (0, 6, 1))


def _zone(p):
    return p[0] // bp.ZONE


def test_one_wing_is_finished_before_the_body_walks_to_the_other():
    # Run 20261119T133618892664Z built by global layer: 824 walks, 47 of them 12-24
    # blocks across the house between two placements.
    cells = {(x, y, 0): PLANKS for x in (0, 1, 16, 17) for y in range(3)}
    t = bp.Template.from_build(make_build(cells, (18, 3, 1)), (0, 0, 0))
    body = _Recorder(flat_world(hi=22), {PLANKS: 64}, BLOCKS, pos=(0, 0, 2))
    report = br.run_build(t, body, BLOCKS, site_margin=None)
    assert report.stop == "done"
    zones = [_zone(p) for p in report.placed]
    switches = sum(a != b for a, b in zip(zones, zones[1:], strict=False))
    assert switches == 1, zones


def _reclaim_case(done_far_wing: bool, held: dict):
    cells = {(0, 0, 0): PLANKS, (18, 0, 0): PLANKS}
    t = bp.Template.from_build(make_build(cells, (19, 1, 1)), (0, 0, 0))
    w = flat_world(hi=22)
    if done_far_wing:
        w[(18, 0, 0)] = PLANKS
    tower = {(18, 0, 2), (18, 1, 2)}
    for c in tower:
        w[c] = "minecraft:dirt"
    body = FakeBody(w, held, BLOCKS, pos=(1, 0, 2))
    runner = br._Runner(body, br.BuildReport(), 50, BLOCKS)
    runner.template, runner.scaffolds = t, set(tower)
    return body, runner


def test_a_tower_in_an_unfinished_wing_stays_up_while_scaffolding_is_held():
    # A person keeps the scaffolding up until that section is done; taking it down
    # and putting it up again was walks across the house.
    body, runner = _reclaim_case(False, {"minecraft:scaffolding": 64})
    runner._reclaim((0, 2, 0), (0, 0, 1), set(), None)
    assert body.w[(18, 0, 2)] == "minecraft:dirt"
    assert runner.scaffolds == {(18, 0, 2), (18, 1, 2)}
    assert not body.stance_calls  # no walk for it either


def test_a_tower_in_a_finished_wing_stays_up_while_scaffolding_is_held():
    """Run 20261120T101447633151Z: 52 mid-build take-downs cost 741 s. Roof
    footholds of a finished zone are on the way to the next zone's roof: the next
    walk put them back and the next tower took them down again. With nothing
    short, what stands comes down once, at cleanup."""
    body, runner = _reclaim_case(True, {"minecraft:scaffolding": 64})
    runner._reclaim((0, 2, 0), (0, 0, 1), set(), None)
    assert body.w[(18, 0, 2)] == "minecraft:dirt"
    assert runner.scaffolds == {(18, 0, 2), (18, 1, 2)}
    assert not body.stance_calls


def test_a_short_tower_takes_a_ground_tower_down_before_a_roof_foothold():
    """A foothold up on a roof gives one block back for a climb; one break at the
    foot of a ground tower gives back the whole column."""
    body, runner = _idle_towers((12, 12), held_scaffolding=5)
    body.w[(6, 5, 7)] = PLANKS  # a roof the foothold stands on
    body.w[(6, 6, 7)] = SCAFF
    runner.scaffolds.add((6, 6, 7))
    assert runner._lacking(6, True) == (frozenset({SCAFFOLDING}), 1)
    assert runner._tower((3, 6, 3), set())
    assert body.w[(6, 6, 7)] == SCAFF  # nearer the cell, but up on the roof
    assert not str(body.w[(12, 0, 12)]).startswith(SCAFFOLDING)


def test_a_short_tower_takes_a_finished_zones_column_before_one_still_in_use():
    # Run 20261119T133618892664Z took towers down mid-wing and walked back to put
    # them up again.
    body, runner = _idle_towers((6, 6), (13, 3), held_scaffolding=0)
    cells = {(0, y, 0): PLANKS for y in range(7)} | {(9, 0, 0): PLANKS}
    runner.template = bp.Template.from_build(make_build(cells, (10, 7, 1)), (3, 0, 3))
    body.w[(12, 0, 3)] = PLANKS  # the far zone's only cell, placed
    zone = functools.partial(bp.zone_of, runner.template)
    assert zone((13, 0, 3)) != zone((6, 0, 6)) == zone((3, 6, 3))
    assert runner._tower((3, 6, 3), set())
    assert not str(body.w[(13, 0, 3)]).startswith(SCAFFOLDING)  # its zone is done
    assert all(body.w[(6, y, 6)] == SCAFF for y in range(6))


def test_standing_towers_come_down_when_the_new_one_is_short():
    body, runner = _reclaim_case(False, {})
    runner._reclaim((0, 2, 0), (0, 0, 1), set(), runner._lacking(6, False))
    assert body.w[(18, 0, 2)] in bp.AIR


# --- bridges off a standing tower ---------------------------------------------


class BridgeBody(_Drops):
    """A tower body whose ``only_from`` cells place from the listed stances only (a
    face seen from one side, as the roof's eave stairs), and that lists bridge
    stances as CompanionBody.bridge_stances does: cells in the air that would be
    stances with a scaffolding block under them."""

    only_from: dict = {}

    def _stance_ok(self, s, target, reach):
        allowed = self.only_from.get(target)
        if allowed is not None and s not in allowed:
            return False
        return super()._stance_ok(s, target, reach)

    def bridge_stances(self, target, reach, avoid=()):
        tx, ty, tz = target
        found = []
        for dx in range(-4, 5):
            for dy in range(-5, 3):
                for dz in range(-4, 5):
                    s = (tx + dx, ty + dy, tz + dz)
                    floor = (s[0], s[1] - 1, s[2])
                    if s in avoid or floor == target or self.w.get(floor) != AIR:
                        continue
                    self.w[floor] = SCAFF
                    try:
                        if self._stance_ok(s, target, reach):
                            found.append(s)
                    finally:
                        self.w[floor] = AIR
        return sorted(found, key=lambda s: (_d2(s, self.pos), s))


ROOT = {(3, y, 3) for y in range(7)}  # a standing tower, its top block at y=6


def _eave(pos, only_from, scaffolding=10):
    """The body on top of ROOT; ``pos`` hangs on a plank column below it and places
    only from ``only_from``."""
    w = flat_world(hi=14)
    for c in ROOT:
        w[c] = SCAFF
    for y in range(pos[1]):
        w[(pos[0], y, pos[2])] = PLANKS
    body = BridgeBody(w, {PLANKS: 10, SCAFFOLDING: scaffolding}, BLOCKS, pos=(3, 7, 3))
    body.only_from = {pos: set(only_from)}
    runner = br._Runner(body, br.BuildReport(), 300, BLOCKS)
    runner.scaffolds = set(ROOT)
    under = (pos[0], pos[1] - 1, pos[2])
    runner.template = bp.Template.from_build(
        make_build({(0, 0, 0): PLANKS}, (1, 1, 1)), under
    )
    runner.reserved = frozenset({pos, under})
    return body, runner


def _new_scaffolding(body):
    return {p for p, s in body.w.items() if s.startswith(SCAFFOLDING)} - ROOT


def test_a_cell_seen_only_from_beside_the_tower_top_gets_one_block_off_it():
    # House-large run 20261120T141244368734Z: the eave stair placed only from one
    # block off the tower the body stood on; a second tower went up beside it,
    # after a walk across the site to take an idle one down.
    pos = (5, 7, 3)
    body, runner = _eave(pos, {(4, 7, 3)})
    assert not body.goto_stance(pos, br.REACH)
    assert runner._tower(pos, set())
    assert body.pos == (4, 7, 3)
    assert _new_scaffolding(body) == {(4, 6, 3)} == runner.scaffolds - ROOT
    assert runner.report.scaffolded == [(4, 6, 3)]


def test_a_bridge_turns_a_corner():
    # Scaffolding hangs from its neighbours in any direction: an L is as good as a
    # straight run.
    pos = (5, 7, 5)
    body, runner = _eave(pos, {(4, 7, 5)})
    assert runner._tower(pos, set())
    assert body.pos == (4, 7, 5)
    path = runner.report.scaffolded
    assert len(path) == 3 and path[-1] == (4, 6, 5)
    assert path[0] in {(4, 6, 3), (3, 6, 4)}
    steps = zip(path, path[1:], strict=False)
    assert all(
        sum(abs(a - b) for a, b in zip(p, q, strict=True)) == 1 for p, q in steps
    )
    assert len({p[0] for p in path}) > 1 and len({p[2] for p in path}) > 1  # bent


def test_a_bridge_short_of_scaffolding_takes_an_idle_tower_down_never_its_own_root():
    pos = (5, 7, 3)
    body, runner = _eave(pos, {(4, 7, 3)}, scaffolding=0)
    idle = {(9, y, 9) for y in range(3)}
    for c in idle:
        body.w[c] = SCAFF
    runner.scaffolds |= idle
    assert runner._tower(pos, set())
    assert all(body.w[c] == SCAFF for c in ROOT)
    assert not body.w[(9, 0, 9)].startswith(SCAFFOLDING)
    assert _new_scaffolding(body) == {(4, 6, 3)}


def test_a_dropped_bridge_waits_for_the_fall_and_picks_its_scaffolding_up():
    # review of 5bb4812: a bridge nobody could stand on was broken at its root and
    # left where it fell (2-3 scaffolding lay on the ground after run
    # 20261120T141244368734Z); tower teardowns pick theirs up
    body, runner = _eave((5, 7, 5), {(4, 7, 5)})
    calls = []
    body.wait_ticks = lambda n: calls.append(("wait", n))
    body.collect_drops = lambda cells: calls.append(("collect", sorted(cells)))
    cells = [(4, 6, 3), (4, 6, 4)]
    for c in cells:
        body.w[c] = SCAFF
    runner.scaffolds |= set(cells)
    runner._drop(cells)
    assert calls == [("wait", br.COLLAPSE_TICKS), ("collect", sorted(cells))]
    assert (4, 6, 3) not in runner.scaffolds


def test_a_bridge_root_that_is_gone_is_not_collected_for():
    body, runner = _eave((5, 7, 5), {(4, 7, 5)})
    calls = []
    body.collect_drops = lambda cells: calls.append(cells)
    runner._drop([(4, 6, 3)])  # never placed
    assert calls == []


def test_a_tower_is_built_when_no_bridge_reaches_the_stance():
    pos = (5, 7, 12)  # nine blocks off the root: farther than scaffolding hangs
    body, runner = _eave(pos, {(5, 7, 11)})
    for y in range(7):
        body.w[(5, y, 11)] = AIR
    assert runner._tower(pos, set())
    # a new column from the ground
    assert any(p[1] == 0 for p in _new_scaffolding(body))
