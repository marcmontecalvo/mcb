"""Scaffold challenges: geometry a bot or a human attempts, and its verdicts."""

import pytest

from mclab import scaffold_challenges as sch

NAMES = [c.name for c in sch.CHALLENGES]


def test_the_set_runs_basic_to_advanced():
    assert NAMES == [
        "climb",
        "descend",
        "reach-out",
        "bridge",
        "bridge-l",
        "bridge-u",
        "span",
        "stairs",
    ]
    levels = [c.level for c in sch.CHALLENGES]
    assert levels == sorted(levels, key=["basic", "advanced"].index)
    assert sch.select(level="basic") == [
        sch.by_name(n) for n in ("climb", "descend", "reach-out")
    ]
    assert sch.select(names=["span", "climb"]) == [
        sch.by_name("span"),
        sch.by_name("climb"),
    ]
    with pytest.raises(KeyError):
        sch.by_name("nope")


@pytest.mark.parametrize("name", NAMES)
def test_a_challenge_starts_on_clear_ground_and_fits_the_lab_kit(name):
    c = sch.by_name(name)
    assert (0, 0, 0) not in c.cells and (0, 1, 0) not in c.cells
    assert c.state((0, -1, 0)) == sch.STONE  # the start stands on the flat spot's floor
    dx, dy, dz = c.clear_extent()
    assert 0 < dx <= 24 and 0 < dz <= 24 and 0 < dy <= 16
    for x, y, z in c.cells:
        assert abs(x) <= dx and abs(z) <= dz and y <= dy
    batches = c.place_batches()
    assert all(0 < len(b) <= 60 for b in batches)
    placed = [tuple(e["offset"]) for b in batches for e in b]
    assert sorted(placed) == sorted(c.cells)
    assert [p[1] for p in placed] == sorted(p[1] for p in placed)  # bottom up
    assert c.task and c.scaffolding > 0 and 1 <= c.goal_radius <= 8


@pytest.mark.parametrize("name", NAMES)
def test_restoring_returns_the_flat_spot(name):
    c = sch.by_name(name)
    back = c.restore_cells()
    assert set(back) == set(c.cells)
    assert all(s == (sch.STONE if p[1] < 0 else sch.AIR) for p, s in back.items())


@pytest.mark.parametrize("name", NAMES)
def test_the_goal_is_off_the_start_and_needs_scaffolding_or_stairs(name):
    c = sch.by_name(name)
    assert (
        c.state(c.goal) == sch.AIR
        and c.state((c.goal[0], c.goal[1] + 1, c.goal[2])) == sch.AIR
    )
    # walking alone (no placing, no falls over 3) never gets there, except by stairs
    assert sch.walkable(c, c.goal) == (name == "stairs")


def test_climb_is_a_ledge_five_up():
    c = sch.by_name("climb")
    assert c.goal[1] == 5 and c.state((c.goal[0], 4, c.goal[2])) == sch.STONE


def test_descend_is_a_shaft_too_deep_to_drop():
    c = sch.by_name("descend")
    assert (
        c.goal[1] <= -8 and c.state((c.goal[0], c.goal[1] - 1, c.goal[2])) == sch.STONE
    )
    assert c.no_damage


def test_reach_out_hovers_four_from_every_rim():
    c = sch.by_name("reach-out")
    gx, gy, gz = c.goal
    assert c.state((gx, gy - 1, gz)) == sch.AIR  # nothing to stand on until it is built
    for step in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        k = 1
        while c.state((gx + step[0] * k, -1, gz + step[1] * k)) == sch.AIR:
            k += 1
        assert k - 1 == 3  # three pit cells, then the rim: four out


@pytest.mark.parametrize(
    ("name", "turns"), [("bridge", 0), ("bridge-l", 1), ("bridge-u", 2)]
)
def test_bridges_need_their_turns_within_scaffold_distance(name, turns):
    c = sch.by_name(name)
    assert sch.bridge_turns(c) == turns
    assert c.no_damage  # dropping into the pit and climbing out is not a bridge


def test_the_span_cannot_be_bridged_from_one_side_but_a_middle_tower_can():
    c = sch.by_name("span")
    assert sch.bridge_turns(c) is None
    pit = [x for x in range(-24, 25) if c.state((x, -1, 0)) == sch.AIR]
    assert len(pit) == 13  # the full span: six each side of a middle tower
    floor = min(y for (x, y, z), s in c.cells.items() if s == sch.AIR)
    middle = pit[len(pit) // 2]
    tower = {(middle, y, 0): sch.SCAFFOLDING for y in range(floor, 1)}
    assert sch.bridge_turns(c, extra=tower) == 0


def test_stairs_reach_the_upper_floor_and_the_ceiling_is_solid_under_the_goal():
    c = sch.by_name("stairs")
    gx, gy, gz = c.goal
    assert c.state((gx, gy - 1, gz)) == sch.STONE
    ceiling = [y for y in range(0, gy) if c.state((gx, y, gz)) != sch.AIR]
    assert ceiling == [gy - 1]  # one solid floor between the storeys, open below it
    assert c.keep_built  # the house is not the subject's to edit


def test_bridge_turns_reads_blocking_glass_and_ground():
    # a straight pit in a glass ring: ground pad x -2..1, pit x 2..4, far ground x 5..6
    cells = {(x, -1, z): sch.AIR for x in range(2, 5) for z in range(-3, 4)}
    cells |= {(x, -2, z): sch.STONE for x in range(2, 5) for z in range(-3, 4)}
    cells |= {(x, 0, z): sch.GLASS for x in range(-3, 8) for z in (-4, 4)}
    cells |= {(x, 0, z): sch.GLASS for x in (-3, 7) for z in range(-4, 5)}
    c = sch.Challenge("t", "basic", "t", cells, goal=(6, 0, 0), no_damage=True)
    assert sch.bridge_turns(c) == 0
    walled = dict(cells) | {(3, 0, z): sch.GLASS for z in range(-3, 4)}
    w = sch.Challenge("t", "basic", "t", walled, goal=(6, 0, 0), no_damage=True)
    assert sch.bridge_turns(w) is None
    assert sch.bridge_turns(c, limit=2) is None  # three pit cells need distance 3


def test_absolute_positions_follow_the_origin():
    c = sch.by_name("climb")
    assert c.at((10, 64, -5), c.goal) == (
        10 + c.goal[0],
        64 + c.goal[1],
        -5 + c.goal[2],
    )


def test_the_kit_is_scaffolding_and_a_pickaxe():
    c = sch.by_name("span")
    assert sch.kit(c) == [
        {"item": sch.SCAFFOLDING, "count": c.scaffolding},
        {"item": "minecraft:iron_pickaxe", "count": 1},
    ]


def test_the_verdict_needs_the_goal_and_no_damage_where_asked():
    c = sch.by_name("descend")
    ok = sch.verdict(c, reached=True, damage=0.0, scaffolding_used=6, built_changed=0)
    assert ok["passed"] and ok["problems"] == []
    hurt = sch.verdict(c, reached=True, damage=3.0, scaffolding_used=0, built_changed=0)
    assert not hurt["passed"] and hurt["problems"] == ["took 3.0 damage"]
    miss = sch.verdict(
        c, reached=False, damage=0.0, scaffolding_used=6, built_changed=0
    )
    assert miss["problems"] == ["goal not reached"]


def test_the_stairs_verdict_rejects_edits_to_the_house_and_scaffolding():
    c = sch.by_name("stairs")
    bad = sch.verdict(c, reached=True, damage=0.0, scaffolding_used=2, built_changed=1)
    assert bad["problems"] == [
        "changed 1 house cells",
        "placed 2 scaffolding (stairs were there)",
    ]
    good = sch.verdict(c, reached=True, damage=0.0, scaffolding_used=0, built_changed=0)
    assert good["passed"]
