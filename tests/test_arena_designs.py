"""The four arena generators, their helpers, the committed plans, previews and the CLI."""

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

from mclab import cli
from mclab.arena import designs, preview
from mclab.arena import plan as ap

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("vault", "duel", "hall", "crossroads", "crash_site")


@pytest.fixture(scope="module")
def built():
    return {name: designs.build(name) for name in NAMES}


def test_registry():
    assert tuple(designs.NAMES) == NAMES
    with pytest.raises(KeyError):
        designs.build("nonsense")


@pytest.mark.parametrize("name", NAMES)
def test_design_validates_cleanly(built, name):
    assert ap.validate(built[name]) == []


@pytest.mark.parametrize("name", NAMES)
def test_design_is_deterministic(name):
    assert ap.to_json(designs.build(name)) == ap.to_json(designs.build(name))


@pytest.mark.parametrize("name", NAMES)
def test_fills_respect_limits_and_palette(built, name):
    plan = built[name]
    assert plan.name == name
    assert all(ap.fill_volume(f) <= ap.MAX_FILL_VOLUME for f in plan.fills)
    assert plan.fills[0].block == "minecraft:air"
    assert plan.size[1] <= ap.MAX_HEIGHT
    assert len({f.block for f in plan.fills}) <= ap.MAX_PALETTE


@pytest.mark.parametrize(
    ("name", "teams", "per_team", "mobs"),
    [
        ("vault", 1, 4, 15),
        ("duel", 2, 2, 6),
        ("hall", 2, 10, 30),
        ("crossroads", 4, 10, 24),
        ("crash_site", 2, 4, 12),
    ],
)
def test_capacities(built, name, teams, per_team, mobs):
    cap = built[name].capacity
    assert (cap.teams, cap.per_team, cap.mobs) == (teams, per_team, mobs)
    assert set(built[name].teams) == set(ap.TEAM_NAMES[:teams])
    assert all(len(t.pads) >= per_team for t in built[name].teams.values())


# --- crash_site: a broken airliner on a round island ---------------------------


@pytest.fixture(scope="module")
def crash(built):
    plan = built["crash_site"]
    grid = ap.voxelize(plan)
    pads = [p for t in plan.teams.values() for p in t.pads]
    return plan, grid, pads, ap.flood(grid, ap.PLAYER, [pads[0]])


def test_crash_site_is_a_round_island_about_ninety_across(crash):
    plan, grid, _, reach = crash
    xs = [c[0] for c in reach]
    zs = [c[2] for c in reach]
    assert 78 <= max(xs) - min(xs) <= 92 and 78 <= max(zs) - min(zs) <= 92
    # round, not square: the corners of the bounding square are rock
    x0, z0 = min(xs), min(zs)
    assert not any(c[0] - x0 + c[2] - z0 < 12 for c in reach)


def test_crash_site_teams_start_on_glowing_pads_either_side(crash):
    plan, grid, pads, _ = crash
    red, blue = plan.teams["red"].pads, plan.teams["blue"].pads
    assert len(red) == len(blue) >= 4
    for x, y, z in pads:
        assert ap.emission(grid.base(x, y - 1, z)) > 0
    centre = plan.size[0] // 2
    assert all(x < centre - 25 for x, _, _ in red)
    assert all(x > centre + 25 for x, _, _ in blue)
    assert sorted(centre - x for x, _, _ in red) == sorted(
        x - centre for x, _, _ in blue
    )


def test_crash_site_has_high_ground_crater_and_cover(crash):
    _, grid, pads, reach = crash
    ground = pads[0][1]
    # the tail fin rises a dozen blocks over the ground, walkable to its top
    assert max(c[1] for c in reach) - ground >= 12
    # the fuselage roof: a long walkable strip six blocks up
    assert sum(1 for c in reach if c[1] == ground + 6) >= 60
    # the crater sinks two below the ground
    assert min(c[1] for c in reach) <= ground - 2
    # covered cells: the walk-through cabin, under the wing and stabilisers, the huts
    covered = [
        c
        for c in reach
        if any(grid.is_solid(c[0], y, c[2]) for y in range(c[1] + 2, c[1] + 6))
    ]
    assert len(covered) >= 150


def test_crash_site_floor_is_patchy_earth(crash):
    _, grid, _, reach = crash
    ground = min(p[1] for p in crash[2])
    soils = {grid.base(x, y - 1, z) for x, y, z in reach if y == ground}
    earth = {"coarse_dirt", "podzol", "mud", "packed_mud", "rooted_dirt", "moss_block"}
    assert len(soils & earth) >= 5


@pytest.mark.parametrize("name", NAMES)
def test_arenas_are_lit_from_blocks_and_have_no_glass_roof(built, name):
    grid = ap.voxelize(built[name])
    top = grid.sy - 1
    assert not any(
        grid.glass[grid.idx(x, top, z)] for x in range(grid.sx) for z in range(grid.sz)
    )
    lights = sum(1 for e in grid.emit if e)
    assert lights > 40


@pytest.mark.parametrize("name", NAMES)
def test_spectator_gallery_has_a_glass_window(built, name):
    grid = ap.voxelize(built[name])
    assert any(grid.glass)
    gallery = ap.flood(grid, ap.PLAYER, [built[name].spectator])
    floor = {c for t in built[name].teams.values() for c in t.pads}
    assert floor.isdisjoint(gallery)


@pytest.mark.parametrize("name", NAMES)
def test_committed_plan_matches_generator(built, name):
    committed = (ROOT / "arenas" / f"{name}.json").read_text(encoding="utf-8")
    assert committed == ap.to_json(built[name])
    assert ap.validate(ap.from_json(committed)) == []


def test_parameterised_variants_still_validate():
    assert ap.validate(designs.build("hall", pillars_per_row=2)) == []
    assert ap.validate(designs.build("vault", pillars=4)) == []


# --- helpers ----------------------------------------------------------------


def make_builder():
    return designs.Builder("t", (20, 16, 20))


def cells(builder):
    return ap.voxelize(builder.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))


def test_builder_box_splits_big_fills_and_orders_corners():
    b = designs.Builder("t", (60, 40, 60))
    b.box((59, 39, 59), (0, 0, 0), "minecraft:stone")
    assert all(ap.fill_volume(f) <= ap.MAX_FILL_VOLUME for f in b.fills)
    grid = ap.voxelize(b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))
    assert grid.base(0, 0, 0) == grid.base(59, 39, 59) == "stone"


def test_builder_rejects_out_of_bounds():
    with pytest.raises(ValueError):
        make_builder().box((0, 0, 0), (20, 1, 1), "minecraft:stone")


def test_pillar_with_light_bands_runs_floor_to_ceiling():
    b = make_builder()
    b.pillar(
        5,
        5,
        2,
        12,
        "minecraft:stone_bricks",
        size=2,
        light_ys=(4,),
        light_block="minecraft:sea_lantern",
    )
    grid = ap.voxelize(b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))
    for y in range(2, 13):
        assert grid.base(5, y, 5) == ("sea_lantern" if y == 4 else "stone_bricks")
        assert grid.base(6, y, 6) == ("sea_lantern" if y == 4 else "stone_bricks")
    assert grid.base(7, 5, 5) == "air"


def test_low_wall_two_high_has_stepped_ends():
    b = make_builder()
    b.box((0, 0, 0), (19, 1, 19), "minecraft:stone")
    b.low_wall((5, 5), (5, 10), feet=2, block="minecraft:stone_bricks", height=2)
    grid = ap.voxelize(b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))
    assert (
        grid.base(5, 2, 5) == "stone_bricks" and grid.base(5, 3, 5) == "air"
    )  # step end
    assert grid.base(5, 3, 7) == "stone_bricks"  # full height in the middle
    assert grid.base(5, 2, 10) == "stone_bricks" and grid.base(5, 3, 10) == "air"
    reach = ap.flood(grid, ap.PLAYER, [(2, 2, 7)])
    assert (5, 4, 7) in reach  # the top is reachable by the steps


def test_low_wall_one_high_is_flat_and_rejects_diagonals():
    b = make_builder()
    b.low_wall((3, 4), (7, 4), feet=2, block="minecraft:stone_bricks", height=1)
    grid = ap.voxelize(b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))
    assert all(
        grid.base(x, 2, 4) == "stone_bricks" and grid.base(x, 3, 4) == "air"
        for x in range(3, 8)
    )
    with pytest.raises(ValueError):
        b.low_wall((1, 1), (4, 4), feet=2, block="minecraft:stone", height=1)
    with pytest.raises(ValueError):
        b.low_wall((1, 1), (4, 1), feet=2, block="minecraft:stone", height=3)


def test_stair_ramp_rises_one_per_cell_and_is_walkable():
    b = make_builder()
    b.box((0, 0, 0), (19, 1, 19), "minecraft:stone")
    # platform east of x=8: top at y=3 (feet 4); ramp climbs eastwards from x=6
    b.box((8, 2, 0), (19, 3, 19), "minecraft:stone_bricks")
    b.stair_ramp(
        (6, 2),
        "east",
        width=3,
        rise=2,
        floor_top=1,
        block="minecraft:stone_bricks",
        stairs="minecraft:stone_brick_stairs",
    )
    grid = ap.voxelize(b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))
    assert grid.block(6, 2, 3) == "minecraft:stone_brick_stairs[facing=east]"
    assert (
        grid.base(7, 2, 3) == "stone_bricks"
        and grid.block(7, 3, 3) == "minecraft:stone_brick_stairs[facing=east]"
    )
    assert (10, 4, 3) in ap.flood(grid, ap.PLAYER, [(2, 2, 3)])


@pytest.mark.parametrize(
    ("direction", "end"),
    [
        ("east", (4, 2, 3)),
        ("west", (-4, 2, 3)),
        ("north", (3, 2, -4)),
        ("south", (3, 2, 4)),
    ],
)
def test_ramp_directions_face_the_climb(direction, end):
    b = designs.Builder("t", (24, 12, 24))
    b.stair_ramp(
        (10, 10),
        direction,
        width=1,
        rise=1,
        floor_top=1,
        block="minecraft:stone",
        stairs="minecraft:stone_stairs",
    )
    grid = ap.voxelize(b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))
    assert grid.block(10, 2, 10) == f"minecraft:stone_stairs[facing={direction}]"


def test_light_scatter_and_band_and_banner():
    b = make_builder()
    b.box((0, 0, 0), (19, 15, 19), "minecraft:stone")
    b.scatter([(3, 3), (9, 3)], 1, "minecraft:glowstone")
    b.band(5, "minecraft:gold_block")
    b.banner(2, 6, 2, "red", "east")
    grid = ap.voxelize(b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))
    assert grid.base(3, 1, 3) == "glowstone" and grid.base(9, 1, 3) == "glowstone"
    assert grid.base(0, 5, 7) == "gold_block" and grid.base(19, 5, 7) == "gold_block"
    assert grid.block(2, 6, 2) == "minecraft:red_wall_banner[facing=east]"


def test_disc_and_annulus():
    b = make_builder()
    b.disc(10, 10, 4, 1, 1, "minecraft:stone")
    b.disc(10, 10, 2, 1, 1, "minecraft:gold_block", inner=0)
    b.disc(10, 10, 4, 5, 5, "minecraft:stone", inner=3)
    grid = ap.voxelize(b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))
    assert grid.base(10, 1, 10) == "gold_block"
    assert grid.base(14, 1, 10) == "stone" and grid.base(15, 1, 10) == "air"
    assert grid.base(10, 5, 10) == "air" and grid.base(10, 5, 14) == "stone"
    assert grid.base(13, 1, 13) == "air"  # corner outside the circle


def test_rotation_turns_boxes_and_facings():
    b = designs.Builder("t", (21, 12, 21))
    with b.rotated(1, (10, 10)):
        b.box((8, 1, 2), (8, 1, 2), "minecraft:stone")
        b.stair_ramp(
            (10, 3),
            "north",
            width=1,
            rise=1,
            floor_top=0,
            block="minecraft:stone",
            stairs="minecraft:stone_stairs",
        )
        b.banner(10, 5, 1, "blue", "south")
    grid = ap.voxelize(b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0)))
    # (8,2) is (-2,-8) from the centre; a quarter turn clockwise sends it to (+8,-2) -> (18, 8)
    assert grid.base(18, 1, 8) == "stone"
    assert grid.block(17, 1, 10) == "minecraft:stone_stairs[facing=east]"
    assert grid.block(19, 5, 10) == "minecraft:blue_wall_banner[facing=west]"


def test_ensure_light_adds_floor_lamps_until_lit():
    b = designs.Builder("t", (20, 10, 20))
    b.box((0, 0, 0), (19, 9, 19), "minecraft:stone")
    b.box((2, 2, 2), (17, 7, 17), "minecraft:air")
    b.ensure_light("minecraft:sea_lantern")
    plan = b.to_plan(ap.Capacity(1, 1, 0), spectator=(0, 0, 0))
    grid = ap.voxelize(plan)
    light = ap.light_levels(grid)
    floor = [c for c in grid.standable() if c[1] == 2]
    assert floor and all(light[grid.idx(*c)] >= ap.MIN_LIGHT for c in floor)


def test_gallery_helper_builds_a_sealed_window_box():
    b = designs.Builder("t", (30, 14, 30))
    b.shell("minecraft:stone_bricks", interior=((2, 2, 2), (27, 11, 27)))
    spectator = b.gallery_north(
        15, width=5, depth=5, raise_by=2, wall="minecraft:stone_bricks"
    )
    assert spectator == (15, 4, 4)
    plan = b.to_plan(ap.Capacity(1, 1, 0), spectator=spectator)
    grid = ap.voxelize(plan)
    assert any(grid.glass)
    assert ap.flood(grid, ap.PLAYER, [spectator]) <= {
        (x, y, z) for x in range(30) for y in range(14) for z in range(0, 8)
    }


# --- preview ----------------------------------------------------------------


def read_png(path):
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(raw):
        length, kind = struct.unpack(">I4s", raw[pos : pos + 8])
        body = raw[pos + 8 : pos + 8 + length]
        assert struct.unpack(">I", raw[pos + 8 + length : pos + 12 + length])[
            0
        ] == zlib.crc32(kind + body)
        if kind == b"IHDR":
            size = struct.unpack(">II", body[:8])
        if kind == b"IDAT":
            idat += body
        pos += 12 + length
    return size, zlib.decompress(idat)


def test_png_writer_round_trips(tmp_path):
    canvas = preview.Canvas(4, 3, (10, 20, 30))
    canvas.rect(1, 1, 2, 2, (200, 0, 0))
    path = tmp_path / "x.png"
    canvas.save(path)
    (w, h), data = read_png(path)
    assert (w, h) == (4, 3)
    rows = [data[i * (1 + 12) : (i + 1) * (1 + 12)] for i in range(3)]
    assert rows[0][1:4] == bytes((10, 20, 30))
    assert rows[1][1 + 3 : 1 + 6] == bytes((200, 0, 0))


@pytest.mark.parametrize("name", NAMES)
def test_previews_render_nonblank_pngs(built, tmp_path, name):
    top, side = preview.render(built[name], tmp_path)
    assert top.name == f"{name}-top.png" and side.name == f"{name}-side.png"
    for path in (top, side):
        (w, h), data = read_png(path)
        assert w > 100 and h > 50
        assert len(set(data[1::7])) > 5  # more than a flat fill


def test_block_colours_are_stable_and_distinct():
    assert preview.block_color("glass") != preview.block_color("stone")
    assert preview.block_color("mystery_block") == preview.block_color("mystery_block")


# --- CLI --------------------------------------------------------------------


def run_cli(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["mcplay", *argv])
    code = cli.main()
    return code, capsys.readouterr().out


def test_cli_plan_one_writes_json_and_previews(monkeypatch, capsys, tmp_path):
    code, out = run_cli(
        monkeypatch, capsys, "arena", "plan", "duel", "--out", str(tmp_path)
    )
    assert code == 0
    assert json.loads(out)["arenas"][0]["name"] == "duel"
    assert (
        ap.validate(ap.from_json((tmp_path / "duel.json").read_text(encoding="utf-8")))
        == []
    )
    assert (tmp_path / "previews" / "duel-top.png").exists()
    assert (tmp_path / "previews" / "duel-side.png").exists()


def test_cli_plan_all_and_no_previews(monkeypatch, capsys, tmp_path):
    code, out = run_cli(
        monkeypatch,
        capsys,
        "arena",
        "plan",
        "all",
        "--out",
        str(tmp_path),
        "--no-previews",
    )
    assert code == 0
    assert [a["name"] for a in json.loads(out)["arenas"]] == list(NAMES)
    assert all((tmp_path / f"{n}.json").exists() for n in NAMES)
    assert not (tmp_path / "previews").exists()


def test_cli_reports_validation_failures(monkeypatch, capsys, tmp_path):
    original = designs.DESIGNS["duel"]

    def broken(**_):
        plan = original()
        return ap.Plan(**{**plan.__dict__, "schema": 9})

    monkeypatch.setitem(designs.DESIGNS, "duel", broken)
    code, out = run_cli(
        monkeypatch,
        capsys,
        "arena",
        "plan",
        "duel",
        "--out",
        str(tmp_path),
        "--no-previews",
    )
    assert code == 1
    assert json.loads(out)["arenas"][0]["problems"]
    assert not (tmp_path / "duel.json").exists()


def test_cli_rejects_unknown_arena(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        sys, "argv", ["mcplay", "arena", "plan", "nope", "--out", str(tmp_path)]
    )
    code = cli.main()
    assert code == 1
