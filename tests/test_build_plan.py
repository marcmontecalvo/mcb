import base64
import json
import struct
from pathlib import Path

import pytest

from mclab import build_plan as bp
from mclab import schematic

AIR = "minecraft:air"
PLANKS = "minecraft:oak_planks"
COBBLE = "minecraft:cobblestone"
DOOR_LO = (
    "minecraft:oak_door[facing=south,half=lower,hinge=left,open=false,powered=false]"
)
DOOR_UP = DOOR_LO.replace("half=lower", "half=upper")
PANE = (
    "minecraft:glass_pane[east=false,north=false,south=false,"
    "waterlogged=false,west=false]"
)
TORCH = "minecraft:wall_torch[facing=east]"
STAIR = (
    "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]"
)
SAND = "minecraft:sand"

BLOCKS = {
    PLANKS: {"item": PLANKS},
    COBBLE: {"item": COBBLE},
    "minecraft:stone": {"item": "minecraft:stone"},
    "minecraft:dirt": {"item": "minecraft:dirt"},
    "minecraft:oak_stairs": {"item": "minecraft:oak_stairs"},
    "minecraft:oak_slab": {"item": "minecraft:oak_slab"},
    "minecraft:glass_pane": {"item": "minecraft:glass_pane"},
    "minecraft:oak_door": {"item": "minecraft:oak_door", "needs_support": "below"},
    "minecraft:wall_torch": {
        "item": "minecraft:torch",
        "needs_support": "facing_opposite",
    },
    SAND: {"item": SAND, "gravity": True},
}


def make_build(cells, size):
    sx, sy, sz = size
    palette = [AIR]
    idx = []
    for y in range(sy):
        for z in range(sz):
            for x in range(sx):
                s = cells.get((x, y, z), AIR)
                if s not in palette:
                    palette.append(s)
                idx.append(palette.index(s))
    raw = struct.pack(f"<{len(idx)}H", *idx)
    return {
        "schema": 1,
        "name": "t",
        "size": list(size),
        "palette": palette,
        "blocks": base64.b64encode(raw).decode(),
    }


def house_cells():
    c = {}
    for x in range(5):
        for z in range(5):
            c[(x, 0, z)] = PLANKS
            c[(x, 4, z)] = PLANKS
            for y in (1, 2, 3):
                if x in (0, 4) or z in (0, 4):
                    c[(x, y, z)] = COBBLE
    for x in range(5):
        c[(x, 4, 0)] = STAIR
    c[(2, 1, 0)], c[(2, 2, 0)] = DOOR_LO, DOOR_UP
    c[(0, 2, 2)] = PANE
    c[(1, 2, 3)] = TORCH
    c[(3, 1, 1)] = c[(3, 2, 1)] = SAND
    c[(2, 4, 2)] = "minecraft:oak_slab[type=bottom,waterlogged=false]"
    c[(2, 4, 3)] = "minecraft:oak_slab[type=double,waterlogged=false]"
    return c


HOUSE = make_build(house_cells(), (5, 5, 5))
# 25 floor + 18 roof planks; 45 cobble; 5 stairs; 1+2 slabs; door, pane, torch;
# 2 sand
HOUSE_MATERIALS = {
    PLANKS: 43,
    COBBLE: 45,
    "minecraft:oak_stairs": 5,
    "minecraft:oak_slab": 3,
    "minecraft:oak_door": 1,
    "minecraft:glass_pane": 1,
    "minecraft:torch": 1,
    SAND: 2,
}


def flat_world(lo=-2, hi=8, floor_y=-1):
    w = {}
    for x in range(lo, hi):
        for z in range(lo, hi):
            for y in range(floor_y, hi):
                w[(x, y, z)] = "minecraft:stone" if y == floor_y else AIR
    return w


def solid(w, pos):
    s = w.get(pos)
    return s is not None and not s.startswith("minecraft:air")


def check_and_apply(w, inv, act, blocks=BLOCKS):
    if isinstance(act, bp.Break):
        w[act.pos] = AIR
        return
    if isinstance(act, bp.Use):
        _apply_use(w, inv, act)
        return
    assert not isinstance(act, bp.NeedMaterials), act
    x, y, z = act.pos
    if isinstance(act, bp.Scaffold):
        assert solid(w, (x, y - 1, z)), "scaffold must rest on something"
        w[act.pos] = act.item
        inv[act.item] -= 1
        return
    ident, props = bp.parse_state(act.state)
    assert any(solid(w, bp._add(act.pos, v)) for v in bp._NEIGHBOURS), act
    kind = bp._support_kind(ident, props, blocks.get(ident, {}))
    sup = bp._support_cell(kind, props, act.pos) if kind else None
    assert sup is None or solid(w, sup), f"{act.state} before its support"
    if props.get("part") == "foot":
        head = bp._add(act.pos, bp._VEC[props["facing"]])
        w[head] = act.state.replace("part=foot", "part=head")
    if ident == "minecraft:wall_torch":
        face = bp._VEC[props["facing"]]
        assert solid(w, (x - face[0], y, z - face[2])), "torch before its wall"
    if ident == SAND:
        assert solid(w, (x, y - 1, z)), "sand over air"
    w[act.pos] = act.state
    if props.get("half") == "lower":
        w[(x, y + 1, z)] = act.state.replace("half=lower", "half=upper")
    inv[act.item] -= act.count


def _flip(state):
    old, new = ("true", "false") if "open=true" in state else ("false", "true")
    return state.replace(f"open={old}", f"open={new}")


def _apply_use(w, inv, act):
    cur = w[act.pos]
    if cur == bp.POT:
        assert act.item, act
        w[act.pos] = "minecraft:potted_" + act.item.removeprefix("minecraft:")
        inv[act.item] -= 1
        return
    assert "open=" in cur, act
    w[act.pos] = _flip(cur)
    if "_door" in cur and "half=lower" in cur:
        up = bp._add(act.pos, (0, 1, 0))
        w[up] = _flip(w[up])


def run(template, w, inv, scaffolds=None, limit=8, blocks=BLOCKS):
    scaffolds = set() if scaffolds is None else scaffolds
    for _ in range(1500):
        acts = bp.next_actions(
            template, lambda *p: w.get(p), blocks, inv, limit=limit, scaffolds=scaffolds
        )
        if not acts or isinstance(acts[0], bp.NeedMaterials):
            return acts
        for a in acts:
            check_and_apply(w, inv, a, blocks)
    raise AssertionError("did not terminate")


@pytest.mark.parametrize(
    "rotation,mirror", [(0, False), (90, False), (180, True), (270, True)]
)
def test_house_loop_terminates_with_empty_diff(rotation, mirror):
    t = bp.Template.from_build(HOUSE, (1, 0, 1), rotation, mirror)
    w = flat_world()
    w[(2, 1, 2)] = "minecraft:dirt"  # terrain inside the footprint must be broken first
    inv = {k: 100 for k in BLOCKS.values() and {i["item"] for i in BLOCKS.values()}}
    assert run(t, w, inv) == []
    d = bp.diff(t, lambda *p: w.get(p))
    assert (d.breaks, d.places, d.unknown) == ([], [], [])
    assert bp.progress(t, lambda *p: w.get(p))["missing"] == 0
    assert {k: 100 - v for k, v in inv.items() if v != 100} == HOUSE_MATERIALS


def test_materials_exclude_upper_half_and_count_double_slab():
    assert bp.item_for_state(DOOR_UP, BLOCKS) is None
    assert bp.item_for_state(DOOR_LO, BLOCKS) == ("minecraft:oak_door", 1)
    assert bp.item_for_state(house_cells()[(2, 4, 3)], BLOCKS) == (
        "minecraft:oak_slab",
        2,
    )


def test_need_materials_lists_remaining_counts():
    t = bp.Template.from_build(HOUSE, (1, 0, 1))
    w = flat_world()
    inv = {i["item"]: 100 for i in BLOCKS.values()}
    inv["minecraft:oak_door"] = 0
    acts = run(t, w, inv)
    assert acts == [bp.NeedMaterials({"minecraft:oak_door": 1})]
    # short on planks: remaining counts are the full remainder, not the shortfall
    w = flat_world()
    inv = {i["item"]: 100 for i in BLOCKS.values()}
    inv[PLANKS] = 40
    (need,) = run(t, w, inv)
    remaining = {}
    for _pos, state in bp.diff(t, lambda *p: w.get(p)).places:
        got = bp.item_for_state(state, BLOCKS)
        if got:
            remaining[got[0]] = remaining.get(got[0], 0) + got[1]
    assert need.items == remaining
    assert need.items[PLANKS] == 3


def test_torch_waits_for_wall():
    b = make_build(
        {(0, 0, 0): COBBLE, (1, 0, 0): "minecraft:wall_torch[facing=east]"}, (2, 1, 1)
    )
    # wall at x=0 (west), torch at x=1 facing east, attached to the wall on its west
    t = bp.Template.from_build(b, (0, 0, 0))
    w = flat_world(floor_y=-1)
    inv = {COBBLE: 5, "minecraft:torch": 5}
    first = bp.next_actions(t, lambda *p: w.get(p), BLOCKS, inv)
    assert [type(a) for a in first] == [bp.Place] and first[0].item == COBBLE
    w[(0, 0, 0)] = COBBLE
    second = bp.next_actions(t, lambda *p: w.get(p), BLOCKS, inv)
    assert second[0].item == "minecraft:torch"


def test_sand_not_placed_over_air():
    b = make_build({(0, 0, 0): SAND}, (1, 1, 1))
    t = bp.Template.from_build(b, (0, 1, 0))
    w = flat_world()
    w[(1, 1, 0)] = "minecraft:stone"  # solid neighbour, but air below the sand
    inv = {SAND: 1, "minecraft:dirt": 1}
    # never the sand itself: a scaffold goes under it first
    assert bp.next_actions(t, lambda *p: w.get(p), BLOCKS, inv) == [
        bp.Scaffold((0, 0, 0), "minecraft:dirt")
    ]
    w[(0, 0, 0)] = "minecraft:stone"
    (act,) = bp.next_actions(t, lambda *p: w.get(p), BLOCKS, {SAND: 1})
    assert act.pos == (0, 1, 0)


def test_rotation_and_mirror_of_states():
    one = lambda s: make_build({(0, 0, 0): s}, (1, 1, 1))  # noqa: E731
    stair = "minecraft:oak_stairs[facing=north,half=bottom,shape=inner_left]"
    t90 = bp.Template.from_build(one(stair), (0, 0, 0), 90)
    assert "facing=east" in t90.cells[(0, 0, 0)]
    tm = bp.Template.from_build(one(stair.replace("north", "east")), (0, 0, 0), 0, True)
    assert (
        "facing=west" in tm.cells[(0, 0, 0)]
        and "shape=inner_right" in tm.cells[(0, 0, 0)]
    )
    tm = bp.Template.from_build(one(DOOR_LO), (0, 0, 0), 0, True)
    assert "hinge=right" in tm.cells[(0, 0, 0)]
    assert "axis=z" in bp.transform_state("minecraft:oak_log[axis=x]", 1, False)
    assert "rotation=4" in bp.transform_state(
        "minecraft:oak_sign[rotation=0]", 1, False
    )
    assert "rotation=12" in bp.transform_state(
        "minecraft:oak_sign[rotation=4]", 0, True
    )
    fence = "minecraft:oak_fence[north=true,east=false,south=false,west=false]"
    assert "east=true" in bp.transform_state(fence, 1, False)
    assert "shape=north_west" in bp.transform_state(
        "minecraft:rail[shape=north_east]", 0, True
    )
    assert "shape=east_west" in bp.transform_state(
        "minecraft:rail[shape=north_south]", 1, False
    )
    assert (
        bp.transform_state("minecraft:chest[foo=bar]", 1, True)
        == "minecraft:chest[foo=bar]"
    )


def test_rotation_moves_cells_clockwise():
    b = make_build({(0, 0, 0): COBBLE, (1, 0, 0): PLANKS}, (2, 1, 1))
    t = bp.Template.from_build(b, (0, 0, 0), 90)
    assert t.size == (1, 1, 2)
    assert (
        t.cells[(0, 0, 0)] == COBBLE and t.cells[(0, 0, 1)] == PLANKS
    )  # west end -> north end


def test_diff_ignores_recomputed_properties():
    t = bp.Template.from_build(make_build({(0, 0, 0): DOOR_LO}, (1, 1, 1)), (0, 0, 0))
    powered = DOOR_LO.replace("powered=false", "powered=true")
    assert bp.diff(t, lambda *p: powered) == bp.Diff([], [], [])
    assert bp.diff(
        t, lambda *p: DOOR_LO.replace("facing=south", "facing=north")
    ).breaks == [(0, 0, 0)]


def test_unknown_cells_block_and_are_not_support():
    b = make_build({(0, 0, 0): COBBLE, (1, 0, 0): COBBLE}, (2, 1, 1))
    t = bp.Template.from_build(b, (0, 1, 0))
    w = flat_world()
    w[(1, 1, 0)] = None  # template cell itself unknown
    d = bp.diff(t, lambda *p: w.get(p))
    assert d.unknown == [(1, 1, 0)] and [p for p, _ in d.places] == [(0, 1, 0)]
    # unknown neighbour defers placement even though the floor below is solid
    acts = bp.next_actions(t, lambda *p: w.get(p), BLOCKS, {COBBLE: 5})
    assert acts == []
    # and an unknown cell is never counted as support for a floating cell
    assert bp._gate((0, 1, 0), COBBLE, lambda *p: w.get(p), BLOCKS) == "unknown"
    w2 = {(1, 1, 0): None, (0, 1, 0): AIR}
    assert not bp._is_solid(w2[(1, 1, 0)], BLOCKS)


def test_scaffold_for_floating_block_and_cleanup():
    b = make_build({(0, 0, 0): COBBLE}, (1, 1, 1))
    t = bp.Template.from_build(b, (2, 5, 2))
    w = flat_world()
    inv = {COBBLE: 1, "minecraft:dirt": 10}
    scaffolds: set = set()
    first = bp.next_actions(t, lambda *p: w.get(p), BLOCKS, inv, scaffolds=scaffolds)
    assert first == [
        bp.Scaffold((2, 0, 2), "minecraft:dirt")
    ]  # lowest cell on the ground
    assert run(t, w, inv, scaffolds) == []
    assert w[(2, 5, 2)] == COBBLE and scaffolds == {(2, y, 2) for y in range(5)}
    assert not bp.diff(t, lambda *p: w.get(p)).places
    for act in bp.cleanup_actions(t, lambda *p: w.get(p), scaffolds):
        check_and_apply(w, inv, act)
    assert all(w[(2, y, 2)] == AIR for y in range(5))
    assert bp.cleanup_actions(t, lambda *p: w.get(p), scaffolds) == []


def test_scaffold_needs_material():
    t = bp.Template.from_build(make_build({(0, 0, 0): COBBLE}, (1, 1, 1)), (2, 5, 2))
    w = flat_world()
    acts = bp.next_actions(t, lambda *p: w.get(p), BLOCKS, {COBBLE: 1})
    assert acts == [bp.NeedMaterials({"minecraft:dirt": 1}, scaffold=True)]


TRAP_OPEN = "minecraft:oak_trapdoor[facing=north,half=bottom,open=true,powered=false]"
TRAP_SHUT = TRAP_OPEN.replace("open=true", "open=false")


def at(w):
    return lambda *p: w.get(p)


def test_open_mismatch_is_a_use_not_a_break():
    t = bp.Template.from_build(make_build({(0, 0, 0): TRAP_OPEN}, (1, 1, 1)), (0, 0, 0))
    shut = lambda *p: TRAP_SHUT  # noqa: E731
    d = bp.diff(t, shut)
    assert (d.breaks, d.places, d.uses) == ([], [], [((0, 0, 0), None)])
    assert bp.progress(t, shut)["wrong"] == 1
    assert bp.next_actions(t, shut, BLOCKS, {}) == [bp.Use((0, 0, 0))]
    powered = TRAP_OPEN.replace("powered=false", "powered=true")
    assert bp.diff(t, lambda *p: powered).uses == []
    wrong = TRAP_SHUT.replace("north", "east")  # a different facing is a wrong block
    assert bp.diff(t, lambda *p: wrong).breaks == [(0, 0, 0)]


def test_door_is_placed_closed_then_used_once():
    open_lo = DOOR_LO.replace("open=false", "open=true")
    open_up = open_lo.replace("half=lower", "half=upper")
    cells = {(0, 0, 0): open_lo, (0, 1, 0): open_up}
    t = bp.Template.from_build(make_build(cells, (1, 2, 1)), (0, 0, 0))
    w = flat_world()
    inv = {"minecraft:oak_door": 1}
    (place,) = bp.next_actions(t, at(w), BLOCKS, inv)
    assert "open=false" in place.state
    check_and_apply(w, inv, place)
    assert bp.next_actions(t, at(w), BLOCKS, inv) == [bp.Use((0, 0, 0))]
    check_and_apply(w, inv, bp.Use((0, 0, 0)))
    assert bp.next_actions(t, at(w), BLOCKS, inv) == []


def test_potted_plant_places_pot_then_uses_plant():
    pot = "minecraft:potted_blue_orchid"
    t = bp.Template.from_build(make_build({(0, 0, 0): pot}, (1, 1, 1)), (0, 0, 0))
    w = flat_world()
    inv = {bp.POT: 1}
    assert bp.next_actions(t, at(w), BLOCKS, inv) == [
        bp.NeedMaterials({bp.POT: 1, "minecraft:blue_orchid": 1})
    ]
    inv["minecraft:blue_orchid"] = 1
    (place,) = bp.next_actions(t, at(w), BLOCKS, inv)
    assert (place.state, place.item) == (bp.POT, bp.POT)
    check_and_apply(w, inv, place)
    use = bp.Use((0, 0, 0), "minecraft:blue_orchid")
    assert bp.next_actions(t, at(w), BLOCKS, inv) == [use]
    check_and_apply(w, inv, use)
    assert w[(0, 0, 0)] == pot
    assert bp.next_actions(t, at(w), BLOCKS, inv) == []


def test_lantern_hangs_from_above_or_stands_on_a_block():
    hang = "minecraft:lantern[hanging=true,waterlogged=false]"
    stand = hang.replace("true", "false")
    blocks = {"minecraft:lantern": {"item": "minecraft:lantern"}, **BLOCKS}
    w = flat_world()
    w[(1, 1, 0)] = COBBLE  # a solid neighbour, but nothing above or below
    assert bp._gate((0, 1, 0), hang, at(w), blocks) == "support"
    assert bp._gate((0, 1, 0), stand, at(w), blocks) == "below"
    w[(0, 2, 0)] = COBBLE
    assert bp._gate((0, 1, 0), hang, at(w), blocks) is None
    assert bp._gate((0, 1, 0), stand, at(w), blocks) == "below"
    w[(0, 0, 0)] = COBBLE
    assert bp._gate((0, 1, 0), stand, at(w), blocks) is None


PACK = Path(__file__).parents[1] / "knowledge" / "26.3-f7dba04d8b5b" / "blocks.json"
HOUSE_FILE = (
    Path(__file__).parent / "fixtures" / "schematics" / "house-154fc7cd.litematic"
)


def real_house(origin=(1, 0, 1)):
    """(template, blocks.json blocks, converter material list) for the real house."""
    blocks = json.loads(PACK.read_text("utf-8"))["blocks"]
    schem = schematic.load_litematic(HOUSE_FILE)
    build = schematic.to_build_json(schem, {"blocks": blocks})
    return bp.Template.from_build(build, origin), blocks, build["materials"]


@pytest.mark.parametrize("origin", [(1, 0, 1), (1, 3, 1)])  # on the ground, floating
def test_real_house_builds_with_empty_diff_and_no_scaffold_left(origin):
    t, blocks, materials = real_house(origin)
    w = flat_world(-2, 30, -1)
    inv = {**materials, "minecraft:dirt": materials.get("minecraft:dirt", 0) + 300}
    scaffolds: set = set()
    assert run(t, w, inv, scaffolds, blocks=blocks) == []
    assert bool(scaffolds) == (origin[1] > 0)
    d = bp.diff(t, at(w))
    assert (d.breaks, d.places, d.unknown, d.uses) == ([], [], [], [])
    assert bp.progress(t, at(w))["missing"] == 0
    for act in bp.cleanup_actions(t, at(w), scaffolds):
        check_and_apply(w, inv, act, blocks)
    assert bp.cleanup_actions(t, at(w), scaffolds) == []
    for p in scaffolds:
        assert w[p] in bp.AIR or bp._same(t.cells[p], w[p], bp.DEFAULT_IGNORE)
    assert all(v >= 0 for v in inv.values())
    open_traps = [p for p, v in t.cells.items() if "trapdoor" in v and "open=true" in v]
    assert len(open_traps) == 12  # all end open, as in the file
    assert all("open=true" in w[p] for p in open_traps)


def test_cleanup_breaks_only_the_scaffolding_that_stands_on_the_ground():
    scaff = "minecraft:scaffolding[bottom=false,distance=0,waterlogged=false]"
    w = flat_world()
    tower = [(0, y, 0) for y in range(3)]
    for p in tower:
        w[p] = scaff
    t = bp.Template.from_build(make_build({}, (1, 1, 1)), (5, 0, 5))

    acts = bp.cleanup_actions(t, at(w), set(tower))

    assert acts == [bp.Break((0, 0, 0))]  # the rest drops with it


def test_a_missing_lower_cell_holds_back_the_layer_above():
    cells = {(x, 0, 0): PLANKS for x in range(3)} | {
        (x, 1, 0): PLANKS for x in range(3)
    }
    t = bp.Template.from_build(make_build(cells, (3, 2, 1)), (0, 0, 0))
    w = flat_world()
    w[(0, 0, 0)] = w[(2, 0, 0)] = PLANKS  # (1, 0, 0) is still a hole

    acts = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 10})

    assert [a.pos for a in acts] == [(1, 0, 0)]  # not the planks that would cover it


def test_inside_corner_means_two_neighbours_at_right_angles():
    cells = {(x, 0, 0): PLANKS for x in range(3)} | {
        (0, 0, z): PLANKS for z in range(3)
    }
    t = bp.Template.from_build(make_build(cells, (3, 1, 3)), (0, 0, 0))

    assert bp.inside_corner(t, (0, 0, 0))  # (1,0,0) and (0,0,1) meet here
    assert not bp.inside_corner(t, (1, 0, 0))  # a straight run: neighbours opposite
    assert not bp.inside_corner(t, (2, 0, 0))  # an end: one neighbour
