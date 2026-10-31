"""Step C planner core: tick costs, planning through unknown space, edit edges."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from mclab import native_driver
from mclab.navigation import standable
from mclab.route_search import SPRINT, WALK, RouteSearch
from mclab.terrain_steps import WIDE_EXCAVATABLE, TerrainPolicy, break_ticks

KIT = (
    {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
    {"slot": 1, "item": "minecraft:iron_shovel", "count": 1},
    {"slot": 2, "item": "minecraft:iron_axe", "count": 1},
    {"slot": 3, "item": "minecraft:cobblestone", "count": 64},
)


def cell(p, block):
    solid = block != "minecraft:air"
    return {
        "position": list(p),
        "block": block,
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0, 0, 1, 1, 1]] if solid else [],
    }


def world(xs, zs, solid, top=10):
    """Cells for every (x, y, z) in the box; solid(x, y, z) -> block or None."""
    return {
        (x, y, z): cell((x, y, z), solid(x, y, z) or "minecraft:air")
        for x in xs
        for z in zs
        for y in range(0, top + 1)
    }


def search(cells, start, goal, policy, **kwargs):
    s = RouteSearch(
        cells,
        start,
        goal,
        map_revision=1,
        terrain_policy=policy,
        inventory=KIT,
        **kwargs,
    )
    while (
        result := s.advance(node_limit=4096, budget_ms=1000, segment_length=200)
    ).status == "searching":
        pass
    return result


def test_tick_costs_are_baritone_action_costs_and_vanilla_break_times():
    assert WALK == pytest.approx(4.633, abs=1e-3)
    assert SPRINT == pytest.approx(3.564, abs=1e-3)
    # Iron pickaxe: hardness 1.25 -> ceil(30 * 1.25 / 6) = 7 ticks.
    assert break_ticks("minecraft:terracotta") == 7
    assert break_ticks("minecraft:stone") == 8
    assert break_ticks("minecraft:dirt") == 3


@pytest.mark.parametrize(("opening", "cuts"), [(1, False), (5, True)])
def test_cut_competes_with_a_detour_on_tick_cost(opening, cuts):
    # A dirt wall at x=2 blocks z < opening; a 2-block detour beats cutting
    # (~10 extra ticks), a 10-block detour does not.
    def solid(x, y, z):
        if y == 0:
            return "minecraft:stone"
        if x == 2 and y in (1, 2) and z < opening:
            return "minecraft:dirt"
        return None

    policy = TerrainPolicy(planner_tick_costs=True, planner_edits=True)
    result = search(
        world(range(-1, 7), range(-1, 7), solid, 4), (0, 1, 0), (4, 1, 0), policy
    )
    assert result.status == "reached"
    if cuts:
        # Cheapest edit: climb onto the dirt and cut only the head block.
        assert result.steps and all(s.breaks for s in result.steps)
        assert all(p[2] < opening for p in result.route)
    else:
        assert result.steps == () and (2, 1, 1) in result.route


def test_unknown_space_is_planned_through_but_never_routed():
    def floor(x, y, z):
        return "minecraft:stone" if y == 0 else None

    known = world(range(0, 13), range(-1, 2), floor, 4)
    off = search(known, (0, 1, 0), (30, 1, 0), TerrainPolicy())
    on = search(known, (0, 1, 0), (30, 1, 0), TerrainPolicy(planner_unknown=True))
    assert off.status == "unreachable"
    assert on.status == "segment" and on.reason.startswith("optimistic")
    assert on.route[-1][0] == 12  # The edge of knowledge toward the goal.
    assert all(standable(known, p) for p in on.route)


def test_unknown_cells_the_next_refresh_will_observe_are_not_exits():
    # Live R1/R2 ping-pong: a not-yet-refreshed hole beside the body made the
    # neighbour an exit, and from the neighbour the start was one. Both flipped.
    # East is a known deep pit, so the only real exit is far to the west.
    def floor(x, y, z):
        return "minecraft:stone" if y == 0 and x <= 3 else None

    cells = {
        (x, y, z): cell((x, y, z), floor(x, y, z) or "minecraft:air")
        for x in range(-12, 13)
        for z in range(-1, 2)
        for y in range(-8, 7)
    }
    del cells[1, 0, 1]
    result = search(cells, (0, 1, 0), (40, 1, 0), TerrainPolicy(planner_unknown=True))
    assert result.status == "segment"
    assert result.route[-1][0] <= -9


def test_chained_cut_steps_climb_a_two_per_block_terracotta_slope():
    # R5 shape: the hill rises two blocks per column; walking cannot climb it.
    def height(x):
        return 0 if x <= 0 else min(6, 2 * x)

    def solid(x, y, z):
        return "minecraft:terracotta" if y <= height(x) else None

    cells = world(range(-1, 11), range(-1, 2), solid)
    walking = search(
        cells, (0, 1, 0), (8, 7, 0), TerrainPolicy(planner_tick_costs=True)
    )
    assert walking.status == "unreachable"
    # Unknown-space exits are off: this narrow box borders unknown everywhere.
    edits = TerrainPolicy(break_budget=32, planner_tick_costs=True, planner_edits=True)
    result = search(cells, (0, 1, 0), (8, 7, 0), edits)
    assert result.status == "reached"
    assert len(result.steps) >= 3
    assert all(s.kind == "step" and (s.breaks or s.places) for s in result.steps)
    assert all(
        cells[p]["block"] == "minecraft:terracotta"
        for s in result.steps
        for p in s.breaks
    )
    # Each edit edge is a real hop of the route.
    pairs = set(zip(result.route, result.route[1:], strict=False))
    assert all((s.source, s.target) in pairs for s in result.steps)


def test_legacy_flags_off_keep_the_four_block_breakable_set():
    def solid(x, y, z):
        if y == 0:
            return "minecraft:stone"
        return "minecraft:terracotta" if x == 2 and y in (1, 2) else None

    cells = world(range(-1, 6), range(-1, 2), solid, 4)
    legacy = TerrainPolicy(planner_edits=True)  # Unit costs, legacy breakables.
    assert search(cells, (0, 1, 0), (4, 1, 0), legacy).status == "unreachable"
    wide = TerrainPolicy(planner_edits=True, planner_tick_costs=True)
    assert search(cells, (0, 1, 0), (4, 1, 0), wide).status == "reached"


def test_native_motor_is_offered_only_the_route_before_a_planned_edit():
    plan = SimpleNamespace(source=(2, 1, 0), target=(3, 2, 0))
    route = [(0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 2, 0), (4, 2, 0)]
    controller = SimpleNamespace(
        route=route,
        route_steps={(plan.source, plan.target): plan},
        last_result=None,
        terrain_policy=None,
    )
    controller.walkable_route = lambda: route[:3]
    assert native_driver.offered_route(controller) == route[:3]


def test_body_step_motor_allows_exactly_the_python_breakable_set():
    java = (
        Path(__file__).parents[1]
        / "mcb-body/src/main/java/com/vulpine/mcb/body/StepMotor.java"
    )
    entries = dict(re.findall(r'Map\.entry\("([^"]+)","([^"]+)"\)', java.read_text()))
    assert entries == {block: spec[1] for block, spec in WIDE_EXCAVATABLE.items()}


def test_depth_below_the_goal_is_priced_like_baritone_goal_block():
    # The old max(horizontal, vertical) estimate made a 20-block drop toward a
    # goal 30 blocks away look free; live runs fell into ravines (y=36 for 68).
    floor = world(range(-1, 2), range(-1, 2), lambda x, y, z: None, 2)
    floor[0, 0, 0] = cell((0, 0, 0), "minecraft:stone")
    for unknown in (False, True):
        policy = TerrainPolicy(planner_tick_costs=True, planner_unknown=unknown)
        s = RouteSearch(
            floor, (0, 1, 0), (30, 21, 0), map_revision=1, terrain_policy=policy
        )
        level, deep = s._h((10, 21, 0)), s._h((10, 1, 0))
        assert deep > level + 20 * 3  # At least JUMP_ONE_BLOCK_COST per block.


def test_each_further_edit_on_a_route_costs_more():
    # Long edit chains are a resource and risk commitment on unseen terrain.
    def height(x):
        return 0 if x <= 0 else min(6, 2 * x)

    def solid(x, y, z):
        return "minecraft:terracotta" if y <= height(x) else None

    cells = world(range(-1, 11), range(-1, 2), solid)
    policy = TerrainPolicy(break_budget=32, planner_tick_costs=True, planner_edits=True)
    s = RouteSearch(
        cells,
        (0, 1, 0),
        (8, 7, 0),
        map_revision=1,
        terrain_policy=policy,
        inventory=KIT,
    )
    while (r := s.advance(node_limit=4096, budget_ms=1000)).status == "searching":
        pass
    assert r.status == "reached"
    assert s.edit_depth[r.route[-1]] == len(r.steps) >= 2
    first, later = r.steps[0], r.steps[-1]
    step_cost = {
        (a, b): s.costs[b] - s.costs[a]
        for a, b in zip(r.route, r.route[1:], strict=False)
    }
    assert step_cost[later.source, later.target] > step_cost[first.source, first.target]


def test_exit_under_solid_cover_prices_its_rise_as_a_dig_out():
    # Goal at y=10. Column x=0: cave under stone up to y=9 (buried).
    # x=1: a 2-block overhang with open air above it. x=2: open sky.
    def solid(x, y, z):
        if y == 0 or (x == 0 and y >= 4) or (x == 1 and y in (5, 6)):
            return "minecraft:stone"
        return None

    cells = world(range(0, 3), range(0, 1), solid, top=10)
    s = RouteSearch(
        cells,
        (2, 1, 0),
        (2, 10, 30),
        map_revision=1,
        terrain_policy=TerrainPolicy(planner_tick_costs=True, planner_unknown=True),
    )
    assert s._buried(cells, (0, 1, 0))
    assert not s._buried(cells, (1, 1, 0))
    assert not s._buried(cells, (2, 1, 0))
    open_sky = s._remaining((2, 1, 0), 2, exit_estimate=True)
    assert s._remaining((2, 1, 0), 2, exit_estimate=True, buried=True) > 3 * open_sky


# --- Baritone v1.20.0 search mechanics (baritone_search / baritone_diagonals) ---

BASE = {"planner_tick_costs": True}
BARITONE = TerrainPolicy(**BASE, baritone_search=True)
FLOOR = lambda x, y, z: "minecraft:stone" if y == 0 else None  # noqa: E731


def _search_obj(policy, goal=(3, 1, 5), solid=FLOOR, movement=None, **kw):
    from mclab.movement_policy import MovementPolicy  # noqa: F401

    return RouteSearch(
        world(range(-2, max(8, goal[0] + 3)), range(-2, 8), solid, 4),
        (0, 1, 0),
        goal,
        map_revision=1,
        terrain_policy=policy,
        movement_policy=movement,
        **kw,
    )


def test_octile_heuristic_replaces_hypot_under_baritone_search():
    import math

    octile = (2 + 3 * math.sqrt(2)) * 3.563
    on = _search_obj(BARITONE)._h((0, 1, 0))
    off = _search_obj(TerrainPolicy(**BASE))._h((0, 1, 0))
    assert on == pytest.approx(octile)
    assert off == pytest.approx(math.hypot(3, 5) * SPRINT)


def _diag_world(hole_below=None, block_side=False):
    # Floor at y=0; the side cell (1,1,0) is open air over a hole at (1,0,0).
    def solid(x, y, z):
        if y == 0 and (x, z) == (1, 0):
            return hole_below
        if block_side and (x, y, z) == (0, 1, 1):
            return "minecraft:stone"
        return "minecraft:stone" if y == 0 else None

    return solid


def _diag_targets(policy, solid):
    from mclab.movement_policy import MovementPolicy

    s = _search_obj(
        policy,
        solid=solid,
        movement=MovementPolicy(allow_diagonals=True, allow_sprint=True),
    )
    return dict(s._edges((0, 1, 0)))


def _dip_edges(policy, dip_depth, block_side=False):
    """Floor top at y=1; the side column (1, *, 0) dips dip_depth blocks."""
    from mclab.movement_policy import MovementPolicy

    def solid(x, y, z):
        if block_side and (x, y, z) == (0, 2, 1):
            return "minecraft:stone"
        if (x, z) == (1, 0):
            return "minecraft:stone" if y <= 1 - dip_depth else None
        return "minecraft:stone" if y <= 1 else None

    cells = world(range(-1, 3), range(-1, 3), solid)
    s = RouteSearch(
        cells,
        (0, 2, 0),
        (5, 2, 5),
        map_revision=1,
        terrain_policy=policy,
        movement_policy=MovementPolicy(
            allow_diagonals=True, allow_sprint=True, max_drop=1
        ),
    )
    return dict(s._edges((0, 2, 0)))


def test_diagonal_cuts_over_a_shallow_known_dip_only_with_baritone_rule():
    legacy = _dip_edges(TerrainPolicy(**BASE), 1)
    baritone = _dip_edges(TerrainPolicy(**BASE, baritone_diagonals=True), 1)
    assert (1, 2, 1) not in legacy
    assert baritone[(1, 2, 1)] == pytest.approx(SPRINT * 2**0.5)


def test_diagonal_accepts_deep_holes_and_edging_at_walk_pace():
    # Baritone MovementDiagonal: sides need only be passable at feet and head.
    policy = TerrainPolicy(**BASE, baritone_diagonals=True)
    assert _dip_edges(policy, 5)[(1, 2, 1)] == pytest.approx(
        SPRINT * 2**0.5
    )  # deep hole
    edging = _dip_edges(policy, 0, block_side=True)[(1, 2, 1)]
    assert edging == pytest.approx(WALK * 2**0.5 * (2**0.5 - 0.001))


def test_diagonal_rejects_unknown_side_cell_and_two_blocked_sides():
    from mclab.movement_policy import MovementPolicy

    def solid(x, y, z):
        if (x, y, z) in ((1, 2, 0), (0, 2, 1)) and unknown_blocks:
            return "minecraft:stone"
        return "minecraft:stone" if y <= 1 else None

    policy = TerrainPolicy(**BASE, baritone_diagonals=True)
    movement = MovementPolicy(allow_diagonals=True, allow_sprint=True, max_drop=1)

    def edges(cells):
        return dict(
            RouteSearch(
                cells,
                (0, 2, 0),
                (5, 2, 5),
                map_revision=1,
                terrain_policy=policy,
                movement_policy=movement,
            )._edges((0, 2, 0))
        )

    unknown_blocks = False
    cells = world(range(-1, 3), range(-1, 3), solid)
    assert (1, 2, 1) in edges(cells)
    del cells[1, 3, 0]  # head cell of the side unknown
    assert (1, 2, 1) not in edges(cells)
    unknown_blocks = True
    assert (1, 2, 1) not in edges(
        world(range(-1, 3), range(-1, 3), solid)
    )  # both sides blocked


def test_jump_penalty_on_ascend_and_gap_only_with_flag():
    off, on = _search_obj(TerrainPolicy(**BASE)), _search_obj(BARITONE)
    assert on._height(1) == pytest.approx(off._height(1) + 2)
    assert on._gap(1) == pytest.approx(off._gap(1) + 2)
    assert on._height(0) == off._height(0) and on._height(-1) == off._height(-1)


def _corridor(length):
    return lambda x, y, z: (
        "minecraft:stone" if y == 0 or (y in (1, 2) and z != 0) else None
    )


def test_partial_end_uses_coefficients_and_min_distance():
    s = _search_obj(BARITONE, goal=(60, 1, 0), solid=_corridor(60))
    s.advance(node_limit=3, budget_ms=1000)
    assert s._partial_end() == s.best  # nothing expanded beyond 5 blocks yet
    s.advance(node_limit=400, budget_ms=1000)
    end = s._partial_end()
    assert end is not s.start and end[0] > 5
    assert end == next(n for _, n in s.coef_best if n and n[0] > 5)


def test_previous_route_favouring_halves_edge_cost():
    # Two routes around a wall: the short gap at z=1, a longer one at z=-2.
    def solid(x, y, z):
        if y == 0:
            return "minecraft:stone"
        if x == 3 and y in (1, 2) and z not in (1, -2):
            return "minecraft:stone"
        return None

    long_route = [
        (0, 1, 0), (0, 1, -1), (0, 1, -2), (1, 1, -2), (2, 1, -2), (3, 1, -2),
        (4, 1, -2), (4, 1, -1), (4, 1, 0), (5, 1, 0),
    ]  # fmt: skip
    kwargs = {"solid": solid, "goal": (5, 1, 0)}

    def run(policy, favored=()):
        s = _search_obj(policy, favored=favored, **kwargs)
        while (r := s.advance(node_limit=4096, budget_ms=1000)).status == "searching":
            pass
        return r.route

    assert (3, 1, 1) in run(BARITONE)
    assert tuple(run(BARITONE, favored=long_route)) == tuple(long_route)
    assert (3, 1, 1) in run(TerrainPolicy(**BASE), favored=long_route)  # flag off


def test_tail_cutoff_drops_last_tenth_of_long_partial_routes():
    s = _search_obj(BARITONE, goal=(70, 1, 0), solid=_corridor(70))
    result = s.advance(node_limit=60, budget_ms=1000, segment_length=200)
    full = s._result("searching", s._partial_end(), "").route
    assert result.status == "searching" and len(full) >= 30
    assert len(result.route) == int(len(full) * 0.9)
    assert result.route == full[: len(result.route)]
    off = _search_obj(TerrainPolicy(**BASE), goal=(70, 1, 0), solid=_corridor(70))
    plain = off.advance(node_limit=60, budget_ms=1000, segment_length=200)
    assert len(plain.route) > 0.9 * len(plain.route) and plain.route[-1] == off.best


def test_diagonal_rejects_hazard_under_cut_over_cell():
    from mclab.movement_policy import MovementPolicy

    def solid(x, y, z):
        if (x, z) == (1, 0):
            return "minecraft:stone" if y == 0 else None
        return "minecraft:stone" if y <= 1 else None

    cells = world(range(-1, 3), range(-1, 3), solid)
    cells[1, 1, 0] = {
        **cell((1, 1, 0), "minecraft:air"),
        "block": "minecraft:lava",
        "fluid": "minecraft:lava",
    }
    s = RouteSearch(
        cells,
        (0, 2, 0),
        (5, 2, 5),
        map_revision=1,
        terrain_policy=TerrainPolicy(**BASE, baritone_diagonals=True),
        movement_policy=MovementPolicy(
            allow_diagonals=True, allow_sprint=True, max_drop=1
        ),
    )
    assert (1, 2, 1) not in dict(s._edges((0, 2, 0)))
