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
        "stairs",
    ]
    levels = [c.level for c in sch.CHALLENGES]
    assert levels == sorted(levels, key=["basic", "advanced"].index)
    assert sch.select(level="basic") == [
        sch.by_name(n) for n in ("climb", "descend", "reach-out")
    ]
    assert sch.select(names=["stairs", "climb"]) == [
        sch.by_name("stairs"),
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


BRIDGES = [("bridge", 0, 6), ("bridge-l", 1, 5), ("bridge-u", 2, 6)]


@pytest.mark.parametrize(("name", "turns", "length"), BRIDGES)
def test_bridges_need_their_turns_within_scaffold_distance(name, turns, length):
    c = sch.by_name(name)
    assert sch.bridge_turns(c) == turns
    assert sch.bridge_length(c) == length  # the root on the pad and the cells out
    assert c.no_damage


@pytest.mark.parametrize(("name", "turns", "length"), BRIDGES)
def test_a_bridge_course_is_closed_and_the_bridge_is_the_only_way(name, turns, length):
    # review of 5bb4812: a short tower put the subject on a 3-high fence to walk
    # along it, and a column down to the 6-deep pit floor and another up at the
    # island stood in for the bridge
    c = sch.by_name(name)
    space, leaks = sch.open_space(c)
    assert not leaks  # no way round the course
    assert max(y for _, y, _ in space) == sch.ROOF_Y - 1  # nothing over the fences
    # a column to the floor (MOAT_DEPTH cells, the same again up at the island, or
    # the one taken down and built again) is more than the kit holds
    assert length < c.scaffolding < sch.MOAT_DEPTH
    island = (c.goal[0], c.goal[2])
    below = [
        y for y in range(-1, -20, -1) if c.state((island[0], y, island[1])) != sch.AIR
    ]
    assert below[:1] == [-1]  # the island stands on the pit floor


@pytest.mark.parametrize(("name", "turns", "length"), BRIDGES)
def test_the_roof_leaves_room_to_step_up_onto_the_bridge_root(name, turns, length):
    # Live run 20261120T173256582204Z: with the roof at feet+3 of the pad the bot never
    # left it (9 nodes, unreachable). A rise needs the launch cell clear to feet+3 and
    # the landing to feet+2 (RoutePlanner.planEdit and pillar, StepMotor's jump).
    c = sch.by_name(name)
    for y in range(1, 4):
        assert c.state((0, y, 0)) == sch.AIR  # over the start
        assert c.state((1, y, 0)) == sch.AIR  # over the root on the pad's edge


SC = sch.SCAFFOLDING
# the shortest bridge of each course (root on the pad's edge first)
SHORTEST = {
    "bridge": [(1, 0, 0), *((x, 0, 0) for x in range(2, 7))],
    "bridge-l": [(1, 0, -1), (2, 0, -1), (3, 0, -1), (4, 0, -1), (4, 0, -2)],
    "bridge-u": [(1, 0, 1), (2, 0, 1), (2, 0, 2), (3, 0, 2), (4, 0, 2), (4, 0, 1)],
}


@pytest.mark.parametrize(("name", "turns", "length"), BRIDGES)
def test_a_bridge_that_stands_to_the_island_is_the_bridge(name, turns, length):
    c = sch.by_name(name)
    cells = SHORTEST[name]
    assert len(cells) == length
    assert c.bridge
    assert sch.bridge_turns(c, extra=dict.fromkeys(cells, SC)) is not None
    assert sch.bridge_stands(c, set(cells))
    assert set(cells) <= set(sch.bridge_area(c))
    assert len(sch.bridge_area(c)) <= 2048  # one lab kit probe


@pytest.mark.parametrize(("name", "turns", "length"), BRIDGES)
def test_a_jump_is_not_a_bridge(name, turns, length):
    # review of e029b74: a person can sprint-jump from the pad's corner to the
    # bridge-l island (no scaffolding), or bridge-u's one-turn L and jump the last
    # gap; the verdict reads back the bridge itself
    c = sch.by_name(name)
    cells = SHORTEST[name]
    assert not sch.bridge_stands(c, set())
    assert not sch.bridge_stands(c, set(cells[:-1]))  # one short: the last gap jumped
    assert not sch.bridge_stands(c, set(cells[1:]))  # no root: it fell
    island_side = {cells[-1]}  # a cell by the island alone, set from the island
    assert not sch.bridge_stands(c, island_side)


def test_the_bridge_verdict_needs_the_bridge_standing():
    c = sch.by_name("bridge-l")
    ok = sch.verdict(
        c,
        reached=True,
        damage=0.0,
        scaffolding_used=5,
        built_changed=0,
        bridge_stands=True,
    )
    assert ok["passed"]
    jumped = sch.verdict(
        c,
        reached=True,
        damage=0.0,
        scaffolding_used=0,
        built_changed=0,
        bridge_stands=False,
    )
    assert not jumped["passed"]
    assert jumped["problems"] == [
        "no scaffolding bridge stands from the pad to the island"
    ]
    assert not sch.by_name("climb").bridge


@pytest.mark.parametrize("name", NAMES)
def test_pits_are_walled_and_floored_in_laid_stone(name):
    # the lab spot may be a slab over nothing: the course never relies on what is
    # under it
    c = sch.by_name(name)
    for (x, y, z), s in c.cells.items():
        if s != sch.AIR or y >= 0:
            continue
        for n in (
            (x + 1, y, z),
            (x - 1, y, z),
            (x, y - 1, z),
            (x, y, z + 1),
            (x, y, z - 1),
        ):
            assert n in c.cells, (name, n)


def test_open_space_finds_a_leak():
    c = sch.by_name("climb")
    assert sch.open_space(c)[1]  # an open course: the flood reaches its edge


def test_stairs_reach_the_upper_floor_and_the_ceiling_is_solid_under_the_goal():
    c = sch.by_name("stairs")
    gx, gy, gz = c.goal
    assert c.state((gx, gy - 1, gz)) == sch.STONE
    ceiling = [y for y in range(0, gy) if c.state((gx, y, gz)) != sch.AIR]
    assert ceiling == [gy - 1]  # one solid floor between the storeys, open below it
    assert c.scaffold_free


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
    assert sch.bridge_length(c) == 4  # a root on the rim and three out
    assert sch.bridge_length(w) is None


def test_absolute_positions_follow_the_origin():
    c = sch.by_name("climb")
    assert c.at((10, 64, -5), c.goal) == (
        10 + c.goal[0],
        64 + c.goal[1],
        -5 + c.goal[2],
    )


def test_the_kit_is_scaffolding_alone():
    c = sch.by_name("bridge")
    assert sch.kit(c) == [{"item": sch.SCAFFOLDING, "count": c.scaffolding}]


def test_built_cells_are_the_laid_cells_that_are_not_air():
    c = sch.by_name("descend")
    assert c.built() and all(c.cells[p] != sch.AIR for p in c.built())
    assert len(c.built()) == sum(s != sch.AIR for s in c.cells.values())


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
    dug = sch.verdict(c, reached=True, damage=0.0, scaffolding_used=6, built_changed=2)
    assert dug["problems"] == ["changed 2 course cells"]  # every course, not the house


def test_the_stairs_verdict_rejects_edits_to_the_house_and_scaffolding():
    c = sch.by_name("stairs")
    bad = sch.verdict(c, reached=True, damage=0.0, scaffolding_used=2, built_changed=1)
    assert bad["problems"] == [
        "changed 1 course cells",
        "placed 2 scaffolding (stairs were there)",
    ]
    good = sch.verdict(c, reached=True, damage=0.0, scaffolding_used=0, built_changed=0)
    assert good["passed"]
