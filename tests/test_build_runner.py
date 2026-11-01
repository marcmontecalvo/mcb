import pytest
from test_build_plan import (
    AIR,
    BLOCKS,
    HOUSE,
    HOUSE_MATERIALS,
    flat_world,
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
    assert len(calls) == br.MAX_TRIES
    assert [len(a) for a in calls] == [0, 1, 2]  # each retry excludes the last stance
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
    report = br.run_build(t, body, blocks)
    assert report.skipped == {} and report.stop == "done", report
    d = bp.diff(t, body.world)
    assert (d.breaks, d.places, d.unknown, d.uses) == ([], [], [], [])
    assert body.violations == [] and report.progress() == 1.0
    assert bool(report.scaffolded) == (origin[1] > 0)
    assert all(body.w[p] in bp.AIR for p in report.scaffolded if p not in t.cells)
    assert report.used  # trapdoors opened, pots planted
