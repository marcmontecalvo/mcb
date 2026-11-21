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


def test_breaks_are_sorted_bottom_up_and_filter_skipped():
    # Template has air at (0, 0, 0), (0, 1, 0), (0, 2, 0) and a plank at (1, 0, 0)
    cells = {
        (0, 0, 0): AIR,
        (0, 1, 0): AIR,
        (0, 2, 0): AIR,
        (1, 0, 0): PLANKS,
    }
    t = bp.Template.from_build(make_build(cells, (2, 3, 1)), (0, 0, 0))
    w = flat_world()
    w[(0, 0, 0)] = "minecraft:oak_log"
    w[(0, 1, 0)] = "minecraft:oak_leaves"
    w[(0, 2, 0)] = "minecraft:oak_leaves"

    inv = {PLANKS: 5}

    # Breaks are sorted bottom-up
    acts = bp.next_actions(t, at(w), BLOCKS, inv)
    assert [a.pos for a in acts] == [(0, 0, 0), (0, 1, 0), (0, 2, 0)]

    # Partial skipped breaks are filtered
    acts = bp.next_actions(t, at(w), BLOCKS, inv, skipped={(0, 0, 0)})
    assert [a.pos for a in acts] == [(0, 1, 0), (0, 2, 0)]

    # If all breaks are skipped, next_actions proceeds to places
    skipped = {(0, 0, 0), (0, 1, 0), (0, 2, 0)}
    acts = bp.next_actions(t, at(w), BLOCKS, inv, skipped=skipped)
    assert len(acts) == 1 and acts[0].pos == (1, 0, 0)


def test_axis_logs_gated_by_neighbor_along_axis():
    # An axis=z log at (0, 1, 1) over solid ground (0, 0, 1) is NOT placeable
    # if it lacks a solid neighbor along Z.
    w = flat_world()  # Y=0 is solid grass
    w[(0, 0, 1)] = "minecraft:grass_block"

    # Target state has axis=z
    z_log = "minecraft:stripped_birch_log[axis=z]"
    assert bp._gate((0, 1, 1), z_log, at(w), BLOCKS) == "floating"

    # Once a neighbor along Z is placed at (0, 1, 0), it is placeable
    w[(0, 1, 0)] = "minecraft:stone_bricks"
    assert bp._gate((0, 1, 1), z_log, at(w), BLOCKS) is None


def test_floor_over_water_is_filled_with_dirt_before_it_is_placed():
    # A floor block over water is never placed on the water: dirt fills the column
    # first, from the bed up, and the fill stays in the build.
    w = flat_world()
    w[(2, 0, 0)] = "minecraft:water"
    w[(1, 1, 0)] = PLANKS  # land beside the floor cell
    template = bp.Template.from_build(
        make_build({(1, 1, 0): PLANKS, (2, 1, 0): PLANKS}, (3, 2, 1)), (0, 0, 0)
    )
    acts = bp.next_actions(template, at(w), BLOCKS, {"minecraft:dirt": 5, PLANKS: 5})
    assert acts == [bp.Scaffold((2, 0, 0), "minecraft:dirt")]

    # no dirt: ask for it rather than placing the floor over water
    acts = bp.next_actions(template, at(w), BLOCKS, {PLANKS: 5})
    assert all(not isinstance(a, bp.Place) for a in acts)


def test_work_in_reach_above_the_lowest_layer_is_offered_where_its_column_is_done():
    # Two columns built to y1 at x 0..1 and one far cell at x 12 still missing at y0.
    # The body stands at (0, 2, 0): the y2 cells over finished columns are in reach and
    # are offered; the far hole does not send it back down first.
    cells = {(x, y, 0): PLANKS for x in range(2) for y in range(3)} | {
        (12, 0, 0): PLANKS
    }
    t = bp.Template.from_build(make_build(cells, (13, 3, 1)), (0, 0, 0))
    w = flat_world(hi=14)
    for x in range(2):
        w[(x, 0, 0)] = w[(x, 1, 0)] = PLANKS

    acts = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 10}, near=(0, 2, 0))

    # the far hole is in another work zone: it waits until this one is done
    assert {a.pos for a in acts} == {(0, 2, 0), (1, 2, 0)}
    # without a body position the layer rule stands
    assert [a.pos for a in bp.next_actions(t, at(w), BLOCKS, {PLANKS: 10})] == [
        (12, 0, 0)
    ]


def test_a_cell_in_reach_is_never_placed_over_a_missing_cell_in_its_column():
    cells = {(0, y, 0): PLANKS for y in range(3)} | {(12, 0, 0): PLANKS}
    t = bp.Template.from_build(make_build(cells, (13, 3, 1)), (0, 0, 0))
    w = flat_world(hi=14)
    w[(0, 0, 0)] = PLANKS  # (0, 1, 0) is still a hole under (0, 2, 0)

    acts = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 10}, near=(1, 2, 0))

    assert (0, 2, 0) not in {a.pos for a in acts}


def test_a_far_break_waits_for_the_placing_in_reach():
    cells = {(0, 0, 0): PLANKS, (12, 0, 0): AIR}
    t = bp.Template.from_build(make_build(cells, (13, 1, 1)), (0, 0, 0))
    w = flat_world(hi=14)
    w[(12, 0, 0)] = COBBLE

    near = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 10}, near=(1, 1, 0))
    assert [type(a).__name__ for a in near] == ["Place"]
    far = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 10}, near=(30, 1, 0))
    assert [a.pos for a in far] == [(12, 0, 0)]


def test_details_wait_until_the_structure_is_up():
    # A person builds walls, floors and the roof first and puts the glass, lights
    # and pots in after; placed early they shut the body in on an eave slab
    # (runs 20261117T144843447702Z, 20261117T155135309814Z).
    cells = {(0, 0, 0): PANE, (1, 0, 0): PLANKS, (1, 1, 0): PLANKS}
    t = bp.Template.from_build(make_build(cells, (2, 2, 1)), (0, 0, 0))
    w = flat_world()
    inv = {PLANKS: 10, "minecraft:glass_pane": 1}
    first = bp.next_actions(t, at(w), BLOCKS, inv, near=(0, 0, 1))
    assert first and all(a.state == PLANKS for a in first)
    w[(1, 0, 0)] = w[(1, 1, 0)] = PLANKS
    assert [a.pos for a in bp.next_actions(t, at(w), BLOCKS, inv)] == [(0, 0, 0)]


def test_details_held_go_in_while_the_structure_waits_on_blocks_not_held():
    # run 20261117T184243178083Z: the structure was short of planks and stairs, the
    # details it carried were held back and filled the inventory, and it stopped
    cells = {(0, 0, 0): PANE, (1, 0, 0): PLANKS}
    t = bp.Template.from_build(make_build(cells, (2, 1, 1)), (0, 0, 0))
    acts = bp.next_actions(t, at(flat_world()), BLOCKS, {"minecraft:glass_pane": 1})
    assert (0, 0, 0) in [a.pos for a in acts]  # the runner drops the skipped one


def test_a_window_gap_does_not_hold_up_the_wall_above_it():
    cells = {(0, 0, 0): PANE, (0, 1, 0): PLANKS}
    t = bp.Template.from_build(make_build(cells, (1, 2, 1)), (0, 0, 0))
    w = flat_world()
    w[(1, 0, 0)] = w[(1, 1, 0)] = PLANKS  # a wall beside it to hang the plank on
    acts = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 1, "minecraft:glass_pane": 1})
    assert [a.pos for a in acts] == [(0, 1, 0)]


def test_a_block_that_takes_the_last_way_off_a_ledge_traps_the_body():
    # an eave slab 6 above the ground: pots either side, the window behind
    w = flat_world(hi=10)
    for y in range(0, 5):
        for x in range(0, 4):
            for z in range(0, 3):
                w[(x, y, z)] = AIR
    ledge = (2, 6, 1)
    w[(2, 5, 1)] = PLANKS  # the slab it stands on
    w[(1, 6, 1)] = w[(3, 6, 1)] = PLANKS  # the pots
    for y in (6, 7, 8):
        w[(1, y, 1)] = w[(3, y, 1)] = PLANKS
        w[(2, y, 0)] = PLANKS  # the wall in front
    w[(2, 8, 1)] = PLANKS  # the eave above
    w[(2, 5, 2)] = PLANKS  # the upper floor inside
    window = (2, 6, 2)
    assert bp.has_exit(at(w), ledge)  # in through the window
    assert bp.traps(at(w), ledge, window)
    assert not bp.traps(at(w), ledge, (3, 7, 2))  # a block elsewhere does not
    w[window] = PLANKS
    assert not bp.traps(at(w), ledge, (5, 6, 5))  # already shut in: no worse


def test_a_half_slab_where_a_double_slab_goes_gets_its_second_half_not_a_break():
    # run 20261118T070135016043Z: double slabs were never made (state_unreachable)
    double = "minecraft:oak_slab[type=double,waterlogged=false]"
    for half in ("bottom", "top"):
        current = f"minecraft:oak_slab[type={half},waterlogged=false]"
        assert bp._classify(double, current, bp.DEFAULT_IGNORE, False) == "place"
    other = "minecraft:birch_slab[type=bottom,waterlogged=false]"
    assert bp._classify(double, other, bp.DEFAULT_IGNORE, False) == "break"


def test_a_cell_given_up_on_does_not_keep_the_door_out():
    # run 20261118T070135016043Z: both doors stayed off for five skipped cells
    cells = {(0, 0, 0): DOOR_LO, (0, 1, 0): DOOR_UP, (2, 0, 0): PLANKS}
    t = bp.Template.from_build(make_build(cells, (3, 2, 1)), (0, 0, 0))
    inv = {PLANKS: 1, "minecraft:oak_door": 1}
    held = bp.next_actions(t, at(flat_world()), BLOCKS, inv)
    assert [a.pos for a in held] == [(2, 0, 0)]
    acts = bp.next_actions(t, at(flat_world()), BLOCKS, inv, skipped={(2, 0, 0)})
    assert (0, 0, 0) in [a.pos for a in acts]  # the runner drops the skipped one


def test_a_tower_reaches_a_gable_lantern_eleven_up():
    # run 20261118T070135016043Z: gable-end chains 11 up had no stance (towers <= 8)
    w = flat_world(hi=16)
    cells = bp.tower_cells((3, 11, 3), at(w), BLOCKS)
    assert cells and cells[0][1] == 0 and cells[-1][1] == 10


def test_a_lantern_hanging_from_a_cell_given_up_on_does_not_keep_the_door_out():
    # run 20261118T094938114426Z: the gable chain was skipped, the lantern under it
    # waited on it for ever, and both doors stayed off
    lantern = "minecraft:lantern[hanging=true,waterlogged=false]"
    cells = {
        (0, 0, 0): DOOR_LO,
        (0, 1, 0): DOOR_UP,
        (2, 1, 0): PLANKS,
        (2, 0, 0): lantern,
    }
    t = bp.Template.from_build(make_build(cells, (3, 2, 1)), (0, 0, 0))
    inv = {PLANKS: 1, "minecraft:oak_door": 1, "minecraft:lantern": 1}
    blocks = {**BLOCKS, "minecraft:lantern": {"item": "minecraft:lantern"}}
    acts = bp.next_actions(t, at(flat_world()), blocks, inv, skipped={(2, 1, 0)})
    assert (0, 0, 0) in [a.pos for a in acts]


def test_skipped_cells_are_neither_placed_nor_asked_for():
    # Skipped cells must never be offered by next_actions even if held inventory covers
    # them, and must not trigger NeedMaterials when non-skipped cells are finished.
    cells = {(0, 0, 0): PLANKS, (2, 0, 0): PLANKS, (4, 0, 0): COBBLE}
    t = bp.Template.from_build(make_build(cells, (5, 1, 1)), (0, 0, 0))
    w = flat_world()

    acts = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 10}, skipped={(0, 0, 0)})
    assert [a.pos for a in acts] == [(2, 0, 0)]

    w[(2, 0, 0)] = PLANKS
    acts = bp.next_actions(
        t, at(w), BLOCKS, {PLANKS: 1}, skipped={(0, 0, 0), (4, 0, 0)}
    )
    assert acts == []


def test_tower_cells_finds_nearby_roof_column_when_immediate_neighbors_are_reserved():
    # Interior ceiling cell at y=4 whose 4 immediate horizontal neighbors at y=3 are
    # reserved: tower_cells climbs an adjacent open column up to y=4 (roof level).
    w = flat_world(hi=10)
    pos = (4, 4, 4)
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
    assert bp.tower_cells(pos, at(w), BLOCKS, reserved) == []
    cells = bp.tower_cells(pos, at(w), BLOCKS, reserved, roof=True)
    assert cells
    col = (cells[0][0], cells[0][2])
    assert cells == [(col[0], h, col[1]) for h in range(0, 5)]
    assert (col[0], 5, col[1]) not in reserved and (col[0], 6, col[1]) not in reserved


def test_axis_y_chain_and_lantern_under_skipped_chain_do_not_keep_doors_out():
    chain = "minecraft:iron_chain[axis=y,waterlogged=false]"
    lantern = "minecraft:lantern[hanging=true,waterlogged=false]"
    cells = {
        (0, 0, 0): DOOR_LO,
        (0, 1, 0): DOOR_UP,
        (2, 3, 0): PLANKS,
        (2, 2, 0): chain,
        (2, 1, 0): chain,
        (2, 0, 0): lantern,
    }
    w = flat_world()
    w[(2, 3, 0)] = PLANKS
    t = bp.Template.from_build(make_build(cells, (3, 4, 1)), (0, 0, 0))
    inv = {"minecraft:oak_door": 1, "minecraft:iron_chain": 2, "minecraft:lantern": 1}
    blocks = {
        **BLOCKS,
        "minecraft:iron_chain": {"item": "minecraft:iron_chain", "solid": True},
        "minecraft:lantern": {"item": "minecraft:lantern"},
    }
    held = bp.next_actions(t, at(w), blocks, inv)
    assert [a.pos for a in held] == [(2, 2, 0)]
    acts = bp.next_actions(t, at(w), blocks, inv, skipped={(2, 2, 0)})
    assert (0, 0, 0) in [a.pos for a in acts]


POT = "minecraft:flower_pot"


def _balcony(rise=0):
    """A wall at z=1 with a two-high window gap at x=2, a balcony floor at z=2 with
    a flower pot on it: the pot is reached only through the window. ``rise`` lifts
    the balcony floor (and the pot) that many layers."""
    cells = {}
    for x in range(5):
        cells[(x, rise, 2)] = PLANKS  # the balcony floor
        for y in (1, 2):
            cells[(x, y, 1)] = PANE if x == 2 else PLANKS
    cells[(1, rise + 1, 2)] = POT
    world = flat_world(hi=6)
    for p, s in cells.items():
        if s == PLANKS:
            world[p] = s
    template = bp.Template.from_build(make_build(cells, (5, 3, 3)), (0, 0, 0))
    blocks = {**BLOCKS, POT: {"item": POT}}
    return template, world, blocks


def test_a_window_is_glazed_after_the_details_it_is_the_way_to():
    # run 20261120T141244368734Z: the balcony window's low panes went in first; the
    # pots outside them were then out of sight from the room and the body went round
    # by a tree to reach them
    template, world, blocks = _balcony()
    held = {"minecraft:glass_pane": 2, POT: 1}
    acts = bp.next_actions(template, at(world), blocks, held, near=(2, 1, 0))
    assert [a.pos for a in acts] == [(1, 1, 2)]
    world[(1, 1, 2)] = POT
    acts = bp.next_actions(template, at(world), blocks, held, near=(2, 1, 0))
    assert {a.pos for a in acts} == {(2, 1, 1)}


def test_a_window_waits_for_a_detail_a_layer_up_with_the_body_away():
    # review of 5bb4812: the pot one layer above the low pane was held by the layer
    # rule (the pane's layer was the lowest), so it did not hold the pane, and the
    # pane went in first with the body out of reach. The held panes must not hold
    # the layer either, or neither ever goes in.
    template, world, blocks = _balcony(rise=1)
    held = {"minecraft:glass_pane": 2, POT: 1}
    away = (2, 1, -5)
    acts = bp.next_actions(template, at(world), blocks, held, near=away)
    assert [a.pos for a in acts] == [(1, 2, 2)]
    world[(1, 2, 2)] = POT  # then the window closes, bottom up
    acts = bp.next_actions(template, at(world), blocks, held, near=away)
    assert [a.pos for a in acts] == [(2, 1, 1)]
    world[(2, 1, 1)] = PANE
    acts = bp.next_actions(template, at(world), blocks, held, near=away)
    assert [a.pos for a in acts] == [(2, 2, 1)]


def test_held_bars_do_not_count_as_structure_left():
    # iron bars are glazing but not a detail: held for the pot, they must not keep
    # the details back as structure still to place (each would wait on the other)
    template, world, blocks = _balcony(rise=1)
    bars = "minecraft:iron_bars"
    cells = {p: s for p, s in template.cells.items()}
    for p, s in cells.items():
        if s == PANE:
            cells[p] = bars
    template = bp.Template.from_build(make_build(cells, (5, 3, 3)), (0, 0, 0))
    blocks = {**blocks, bars: {"item": bars}}
    held = {bars: 2, POT: 1}
    acts = bp.next_actions(template, at(world), blocks, held, near=(2, 1, -5))
    assert [a.pos for a in acts] == [(1, 2, 2)]


@pytest.mark.parametrize("glazing", ["minecraft:glass_pane", "minecraft:iron_bars"])
def test_a_held_pane_not_in_hand_does_not_send_the_body_to_fetch(glazing):
    # second review of the glazing fix: the fetch-first rule still read every cell,
    # so a held pane that was not in the inventory sent the body to the chests while
    # the pot it waits for was in hand and placeable
    template, world, blocks = _balcony(rise=1)
    if glazing != PANE:
        cells = {p: (glazing if s == PANE else s) for p, s in template.cells.items()}
        template = bp.Template.from_build(make_build(cells, (5, 3, 3)), (0, 0, 0))
        blocks = {**blocks, glazing: {"item": glazing}}
    acts = bp.next_actions(
        template, at(world), blocks, {POT: 1}, near=(2, 1, -5), fetch_first=True
    )
    assert [a.pos for a in acts if isinstance(a, bp.Place)] == [(1, 2, 2)]
    assert not any(isinstance(a, bp.NeedMaterials) for a in acts)


def test_a_detail_that_cannot_go_in_does_not_keep_the_window_open():
    template, world, blocks = _balcony()
    skipped = bp.next_actions(
        template,
        at(world),
        blocks,
        {"minecraft:glass_pane": 2, POT: 1},
        near=(2, 1, 0),
        skipped={(1, 1, 2)},
    )
    short = bp.next_actions(
        template, at(world), blocks, {"minecraft:glass_pane": 2}, near=(2, 1, 0)
    )
    assert (2, 1, 1) in [a.pos for a in skipped]
    assert (2, 1, 1) in [a.pos for a in short if isinstance(a, bp.Place)]


def test_a_tower_top_needs_room_for_the_head():
    # run 20261120T141244368734Z: a hopper at head height under the first floor's
    # ceiling got a two-block tower whose top put the head in the ceiling; the body
    # never got onto it and the tower came down again
    w = flat_world(hi=6)
    for x in range(-2, 6):
        for z in range(-2, 6):
            w[(x, 3, z)] = PLANKS  # the floor above, three over the ground
    assert bp.tower_cells((3, 2, 3), at(w), BLOCKS) == []
    assert bp.tower_cells((3, 2, 3), at(w), BLOCKS, roof=True) == []
    w[(4, 3, 3)] = AIR  # a hole over the column: the head fits through it
    assert bp.tower_cells((3, 2, 3), at(w), BLOCKS) == [(4, 0, 3), (4, 1, 3)]


def test_a_dirt_tower_top_needs_room_for_the_last_jump():
    # review of 5bb4812: a dirt column is pillared by jumping (pillar_to); the
    # planner's pillar edge wants feet+2 and feet+3 empty (RoutePlanner.pillar), so
    # the cell over the crown must be clear too, or pillar_to searches to its cap.
    # A scaffolding tower is climbed, not jumped: the crown is enough.
    w = flat_world(hi=6)
    for x in range(-2, 6):
        for z in range(-2, 6):
            w[(x, 3, z)] = PLANKS  # a ceiling two over the crown's floor
    assert bp.tower_cells((3, 1, 3), at(w), BLOCKS) == [(3, 0, 2)]
    assert bp.tower_cells((3, 1, 3), at(w), BLOCKS, scaffolding=False) == []
    w[(4, 3, 3)] = AIR
    assert bp.tower_cells((3, 1, 3), at(w), BLOCKS, scaffolding=False) == [(4, 0, 3)]


def test_tower_cells_rejects_non_sturdy_stairs_for_scaffolding():
    w = flat_world(hi=8)  # known up to the dirt tower's last jump
    blocks = {
        **BLOCKS,
        "minecraft:oak_stairs": {"item": "minecraft:oak_stairs", "full_cube": False},
        "minecraft:oak_slab": {"item": "minecraft:oak_slab", "full_cube": False},
    }
    pos = (3, 4, 3)
    # Surround (3, 4, 3) with bottom oak stairs at y=1..2 on all horizontal neighbors
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        w[(3 + dx, 1, 3 + dz)] = "minecraft:oak_stairs[facing=north,half=bottom]"
    assert bp.tower_cells(pos, at(w), blocks, frozenset(), scaffolding=True) == []
    assert bp.tower_cells(pos, at(w), blocks, frozenset(), scaffolding=False)
    # Top slab on one side provides a sturdy up-face for scaffolding
    w[(4, 1, 3)] = "minecraft:oak_slab[type=top]"
    cells = bp.tower_cells(pos, at(w), blocks, frozenset(), scaffolding=True)
    assert cells == [(4, 2, 3), (4, 3, 3)]


def test_next_actions_breaks_trapped_foothold_before_placing_underneath():
    cells = {(1, 0, 1): PLANKS, (1, 2, 1): PLANKS}
    w = flat_world(hi=5)
    w[(1, 2, 1)] = PLANKS
    w[(1, 1, 1)] = "minecraft:dirt"
    t = bp.Template.from_build(make_build(cells, (4, 4, 4)), (0, 0, 0))
    scaffolds = {(1, 1, 1)}
    acts = bp.next_actions(
        t, at(w), BLOCKS, {PLANKS: 4}, scaffolds=scaffolds, near=(50, 0, 50)
    )
    assert [type(a) for a in acts] == [bp.Break]
    assert acts[0].pos == (1, 1, 1)


def _two_wings(hi=20):
    """Two 2-column wings three high, 16 apart along x: different work zones."""
    cells = {(x, y, 0): PLANKS for x in (0, 1, 16, 17) for y in range(3)}
    t = bp.Template.from_build(make_build(cells, (18, 3, 1)), (0, 0, 0))
    return t, flat_world(hi=hi)


def test_work_stays_in_the_zone_the_body_is_in():
    # A person finishes one wing before walking to the other: with the near wing's
    # floor down, its next course is offered, not the far wing's floor (run
    # 20261119T133618892664Z: 47 walks of 12-24 blocks between placements).
    t, w = _two_wings()
    w[(0, 0, 0)] = w[(1, 0, 0)] = PLANKS

    acts = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 20}, near=(0, 1, 1))
    assert {a.pos for a in acts} == {(0, 1, 0), (1, 1, 0)}

    acts = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 20}, near=(17, 0, 1))
    assert {a.pos for a in acts} == {(16, 0, 0), (17, 0, 0)}
    # without a body position the global layer rule stands
    acts = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 20})
    assert {a.pos for a in acts} == {(16, 0, 0), (17, 0, 0)}


def test_the_zone_worked_last_is_finished_before_a_nearer_one():
    # A body on the border between two zones keeps to the one it was working in.
    t, w = _two_wings()
    acts = bp.next_actions(
        t, at(w), BLOCKS, {PLANKS: 20}, near=(15, 0, 1), focus=(1, 0, 0)
    )
    assert {a.pos for a in acts} == {(0, 0, 0), (1, 0, 0)}
    # a finished focus zone gives way to the zone the body stands in
    for x in (0, 1):
        for y in range(3):
            w[(x, y, 0)] = PLANKS
    acts = bp.next_actions(
        t, at(w), BLOCKS, {PLANKS: 20}, near=(15, 0, 1), focus=(1, 0, 0)
    )
    assert {a.pos for a in acts} == {(16, 0, 0), (17, 0, 0)}


def test_a_zone_never_builds_over_a_missing_cell_in_its_columns():
    t, w = _two_wings()
    w[(0, 0, 0)] = PLANKS  # (1, 0, 0) still missing
    acts = bp.next_actions(t, at(w), BLOCKS, {PLANKS: 20}, near=(0, 1, 1))
    offered = {a.pos for a in acts}
    assert (1, 0, 0) in offered and (1, 1, 0) not in offered
    assert all(p[0] < 8 for p in offered)


# --- bridges off a standing scaffolding tower ---------------------------------

SCAFFOLD = "minecraft:scaffolding[bottom=false,distance=0,waterlogged=false]"


def _tower_world(top=5, hi=12):
    """Flat stone at y=-1 and a scaffolding column at (0, 0..top, 0)."""
    w = flat_world(lo=-2, hi=hi)
    for y in range(top + 1):
        w[(0, y, 0)] = SCAFFOLD
    return w


def test_a_floor_beside_a_tower_top_is_one_scaffolding_block_off_it():
    # House-large run 20261120T141244368734Z: a roof stair placed only from one
    # block off the side of the tower the body stood on; a second tower went up
    # beside the first instead of that one block.
    w = _tower_world()
    assert bp.bridge_cells((1, 5, 0), at(w), BLOCKS) == [(1, 5, 0)]


def test_a_bridge_runs_root_side_first_up_to_six_blocks_off_the_tower():
    w = _tower_world()
    assert bp.bridge_cells((3, 5, 0), at(w), BLOCKS) == [
        (1, 5, 0),
        (2, 5, 0),
        (3, 5, 0),
    ]
    assert len(bp.bridge_cells((6, 5, 0), at(w), BLOCKS)) == 6
    assert bp.bridge_cells((7, 5, 0), at(w), BLOCKS) == []  # distance 7 falls


def test_a_bridge_goes_round_template_cells_and_never_into_them():
    w = _tower_world()
    path = bp.bridge_cells((2, 5, 0), at(w), BLOCKS, frozenset({(1, 5, 0)}))
    assert path and path[-1] == (2, 5, 0) and (1, 5, 0) not in path
    assert len(path) == 4  # (0,5,1) or (0,5,-1) side, then across
    assert bp.bridge_cells((2, 5, 0), at(w), BLOCKS, frozenset({(2, 5, 0)})) == []


def test_a_bridge_leaves_only_from_a_tower_top_with_room_to_walk_out():
    w = _tower_world()
    assert bp.bridge_cells((1, 3, 0), at(w), BLOCKS) == []  # mid-column
    w[(1, 7, 0)] = PLANKS  # no headroom over the bridge
    assert bp.bridge_cells((1, 5, 0), at(w), BLOCKS) == []


def test_a_bridge_counts_the_distance_its_root_already_hangs_at():
    w = _tower_world()
    for x in (1, 2, 3):
        w[(x, 5, 0)] = SCAFFOLD  # an earlier bridge, distance 3 at its end
    w[(0, 6, 0)] = AIR
    assert len(bp.bridge_cells((6, 5, 0), at(w), BLOCKS)) == 3
    assert bp.bridge_cells((7, 5, 0), at(w), BLOCKS) == []


def test_no_bridge_over_unknown_or_solid_cells():
    w = _tower_world()
    del w[(1, 5, 0)]
    assert bp.bridge_cells((1, 5, 0), at(w), BLOCKS) == []
    w[(1, 5, 0)] = PLANKS
    assert bp.bridge_cells((1, 5, 0), at(w), BLOCKS) == []


def test_prop_cells_finds_absent_click_face_for_mutual_hoppers_and_top_slab():
    # Run 20261120T175636584118Z: mutually facing hoppers had no solid face on
    # the facing side; stance search returned empty and never triggered _propped.
    w = flat_world(hi=6)
    w[(2, 2, 0)] = PLANKS  # north wall behind (-16, 67, 60)
    w[(1, 2, 0)] = PLANKS  # north wall behind (-17, 67, 60)
    west = "minecraft:hopper[enabled=true,facing=west]"
    east = "minecraft:hopper[enabled=true,facing=east]"
    blocks = {**BLOCKS, "minecraft:hopper": {"item": "minecraft:hopper"}}
    assert bp.prop_cells((2, 2, 1), west, at(w), blocks) == [(1, 2, 1)]
    assert bp.prop_cells((1, 2, 1), east, at(w), blocks) == [(2, 2, 1)]
    # Once one hopper stands, the other needs no prop.
    w[(2, 2, 1)] = west
    assert bp.prop_cells((1, 2, 1), east, at(w), blocks) == []
    # Top slab over only a floor needs a side prop (clicking up makes bottom).
    top_slab = "minecraft:oak_slab[type=top,waterlogged=false]"
    slab_props = bp.prop_cells((4, 0, 4), top_slab, at(w), BLOCKS)
    assert slab_props and all(p[1] == 0 for p in slab_props)
    w[(5, 0, 4)] = PLANKS
    assert bp.prop_cells((4, 0, 4), top_slab, at(w), BLOCKS) == []
    # Blocks with permanent needs_support (e.g. hanging lantern) never prop.
    lantern = "minecraft:lantern[hanging=true,waterlogged=false]"
    assert bp.prop_cells((4, 2, 4), lantern, at(w), BLOCKS) == []


def test_tower_cells_with_can_reach_picks_shortest_sound_height_and_skips_unreachable():
    # Run 20261120T175636584118Z: chain (-21, 74, 63) hangs from (-21, 75, 63);
    # feet at y=74 put eye at 75.62 (above down face 75.0), and roof fallback built
    # a tower on the wrong side of a wall for an indoor hopper.
    w = flat_world(hi=16)
    target = (3, 10, 3)

    def chain_reach(foot, cells):
        # Eye must stay below the down face at y=11.0 (feet <= 9) and within reach.
        if foot[1] >= 10:
            return 0
        if foot[1] == 5:
            return 1  # marginal reach
        return 2 if 6 <= foot[1] <= 9 else 0

    cells = bp.tower_cells(
        target, at(w), BLOCKS, roof=True, can_reach=chain_reach, allow_roof_lift=False
    )
    assert cells
    assert cells[0][1] == 0 and cells[-1][1] == 5  # feet at y=6, shortest sound tower
    # When nothing can reach the target and no roof stance exists, build no tower.
    assert (
        bp.tower_cells(
            target,
            at(w),
            BLOCKS,
            roof=True,
            can_reach=lambda foot, cells: 0,
            allow_roof_lift=False,
        )
        == []
    )


def test_sealed_air_marks_enclosed_attic_cavity_and_leaves_doored_room_open():
    # A 1-high enclosed attic cavity above a room with a door.
    cells = {}
    for x in range(5):
        for z in range(5):
            cells[(x, 0, z)] = PLANKS  # floor
            cells[(x, 3, z)] = PLANKS  # ceiling / attic floor
            cells[(x, 5, z)] = PLANKS  # roof over 1-high attic
    for x in range(5):
        for y in (1, 2, 4):
            cells[(x, y, 0)] = PLANKS
            cells[(x, y, 4)] = PLANKS
    for z in range(5):
        for y in (1, 2, 4):
            cells[(0, y, z)] = PLANKS
            cells[(4, y, z)] = PLANKS
    cells[(2, 1, 0)] = DOOR_LO
    cells[(2, 2, 0)] = DOOR_UP
    t = bp.Template.from_build(make_build(cells, (5, 6, 5)), (0, 0, 0))
    sealed = bp.sealed_air(t, BLOCKS)
    assert (2, 4, 2) in sealed  # enclosed attic cavity
    assert (2, 1, 2) not in sealed  # room reachable through the door
    assert (2, 2, 2) not in sealed


def test_next_actions_breaks_foothold_in_sealed_cavity_before_sealing_roof():
    # Run 20261120T175636584118Z: dirt at (-12, 75, 63) was sealed in when the roof
    # plank above it was placed.
    cells = {
        (1, 0, 1): PLANKS,
        (0, 1, 1): PLANKS,
        (2, 1, 1): PLANKS,
        (1, 1, 0): PLANKS,
        (1, 1, 2): PLANKS,
        (1, 2, 1): PLANKS,
    }
    w = flat_world(hi=5)
    for p in ((1, 0, 1), (0, 1, 1), (2, 1, 1), (1, 1, 0), (1, 1, 2)):
        w[p] = PLANKS
    w[(1, 1, 1)] = "minecraft:dirt"  # foothold inside the cavity before roof closes
    t = bp.Template.from_build(make_build(cells, (3, 3, 3)), (0, 0, 0))
    scaffolds = {(1, 1, 1)}
    acts = bp.next_actions(
        t, at(w), BLOCKS, {PLANKS: 4}, scaffolds=scaffolds, near=(50, 0, 50)
    )
    assert [type(a) for a in acts] == [bp.Break]
    assert acts[0].pos == (1, 1, 1)
