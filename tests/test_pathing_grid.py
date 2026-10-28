import copy
import itertools

from test_collision_geometry import slab_map
from test_gap_jump import gap_map
from test_route_search import floor_map
from test_staircase_steps import staircase_map
from test_water_movement import water_map

from mclab import collision_geometry, navigation, pathing_grid, water_movement
from mclab.pathing_grid import PathingGrid
from mclab.route_search import RouteSearch


def grid_of(cells, tick=1, session="s"):
    grid = PathingGrid()
    for p, cell in cells.items():
        grid.put(p, grid.state_id(cell), grid.provenance_id(tick, session, None))
    return grid


def neighbourhood(cells, pad=2):
    xs, ys, zs = zip(*cells, strict=True)
    return itertools.product(
        range(min(xs) - pad, max(xs) + pad + 1),
        range(min(ys) - pad, max(ys) + pad + 1),
        range(min(zs) - pad, max(zs) + pad + 1),
    )


def test_predicates_read_identically_through_the_grid():
    fixtures = [floor_map([(x, z) for x in range(4) for z in range(3)]), slab_map()]
    fixtures += [staircase_map(), gap_map(width=2), water_map()]
    for cells in fixtures:
        view = grid_of(cells).snapshot()
        for p in neighbourhood(cells):
            for predicate in (
                navigation.standable,
                collision_geometry.standable,
                water_movement.supported_node,
            ):
                assert predicate(view, p) == predicate(cells, p), (predicate, p)
            assert navigation.empty(view.get(p)) == navigation.empty(cells.get(p))
            assert navigation.full_cube(view.get(p)) == navigation.full_cube(
                cells.get(p)
            )
            x, y, z = p
            assert collision_geometry.surfaces(
                view, x, z, y - 3, y + 1
            ) == collision_geometry.surfaces(cells, x, z, y - 3, y + 1)
            assert (p in view) == (p in cells)


def test_unknown_and_half_block_coordinates_never_read_as_cells():
    cells = floor_map([(0, 0)])
    view = grid_of(cells).snapshot()
    assert view.get((0, 0.5, 0)) is None
    assert view.get((5, 0, 0)) is None and (5, 0, 0) not in view
    assert view.get((0, 0.0, 0))["block"] == "minecraft:stone"
    assert view.get(("x", 0, 0)) is None
    assert view.flags((5, 0, 0)) == 0  # unknown
    assert view.flags((0, 0.5, 0)) == 0
    # Air in an otherwise known section stays unknown unless observed.
    assert view.get((1, 1, 1)) is None


def test_flags_match_predicates():
    cells = {
        **floor_map([(0, 0)]),
        **slab_map(),
        **water_map(),
        (9, 9, 9): {"block": "minecraft:sand", "collision_boxes": [[0, 0, 0, 1, 1, 1]]},
        (9, 9, 8): {"block": "minecraft:lava", "fluid": "minecraft:lava"},
        (9, 9, 7): {"block": "minecraft:magma_block", "collision_boxes": []},
    }
    view = grid_of(cells).snapshot()
    for p, cell in cells.items():
        flags = view.flags(p)
        assert bool(flags & pathing_grid.PASSABLE) == navigation.empty(cell)
        assert bool(flags & pathing_grid.FULL_SOLID) == navigation.full_cube(cell)
        assert bool(flags & pathing_grid.HAZARD) == (
            not collision_geometry.safe_cell(cell)
        )
    assert view.flags((9, 9, 9)) & pathing_grid.FALLING
    assert view.flags((9, 9, 8)) & pathing_grid.LAVA


def test_snapshots_are_copy_on_write_and_deepcopy_free():
    grid = grid_of(floor_map([(0, 0)]))
    frozen = grid.snapshot()
    air = grid.state_id({"block": "minecraft:air", "collision_boxes": []})
    grid.put((0, 0, 0), air, grid.provenance_id(2, "s", None))
    grid.put((40, 0, 0), air, grid.provenance_id(2, "s", None))
    assert frozen[(0, 0, 0)]["block"] == "minecraft:stone"
    assert (40, 0, 0) not in frozen
    assert grid.view()[(0, 0, 0)]["block"] == "minecraft:air"
    assert copy.deepcopy(frozen) is frozen
    assert len(frozen) == 5 and len(grid.view()) == 6


def test_route_search_on_grid_matches_dict():
    positions = [(0, z) for z in range(9)] + [(x, 8) for x in range(1, 9)]
    cells = floor_map(positions)
    view = grid_of(cells).snapshot()
    expected = RouteSearch(cells, (0, 1, 0), (8, 1, 8), map_revision=1).advance(
        node_limit=1000, budget_ms=1000, segment_length=100
    )
    actual = RouteSearch(view, (0, 1, 0), (8, 1, 8), map_revision=1).advance(
        node_limit=1000, budget_ms=1000, segment_length=100
    )
    assert actual.status == expected.status == "reached"
    assert actual.route == expected.route


def test_records_keep_provenance_and_position():
    grid = grid_of(floor_map([(3, -2)]), tick=7, session="old")
    assert "position" not in grid.view()[(3, 0, -2)]  # planners get facts only
    record = grid.view().record((3, 0, -2))
    assert record["position"] == [3, 0, -2]
    assert record["observed_tick"] == 7 and record["observation_session"] == "old"
    assert "observed_at_ns" not in record


def test_negative_coordinates_use_their_own_sections():
    cells = {
        (x, y, z): {"block": "minecraft:stone", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
        for x in (-17, -16, -1, 0, 15, 16)
        for y in (-65, -64, -1, 0)
        for z in (-1, 0)
    }
    view = grid_of(cells).snapshot()
    assert set(view) == set(cells)
    for x, y, z in cells:
        for dx in (-1, 1):
            assert ((x + dx, y, z) in view) == ((x + dx, y, z) in cells)


def test_unusual_facts_use_exact_keys_or_fail_closed():
    grid = PathingGrid()
    nested = {"block": "minecraft:x", "properties": {"shape": ["a", "b"]}}
    flat = {"block": "minecraft:x", "properties": {"shape": "a"}}
    assert grid.state_id(nested) != grid.state_id(flat)
    assert grid.state_id(nested) == grid.state_id(copy.deepcopy(nested))
    assert grid.state_id({"block": "minecraft:x", "properties": "odd"})
    try:
        grid.state_id({"block": "minecraft:x", "extra": object()})
    except TypeError:
        pass
    else:
        raise AssertionError("non-JSON facts must not be admitted")
