from mclab import scaffolding as sc


def ground(y):
    return lambda p: p[1] == y  # sturdy below at the given y (the floor is y - 1)


def tower(n, x=0, z=0, y=0):
    return {(x, y + i, z) for i in range(n)}


def test_a_tower_stands_on_the_ground_and_is_one_root():
    cells = tower(4)

    assert set(sc.distances(cells, ground(0)).values()) == {0}
    assert sc.roots(cells, ground(0)) == [(0, 0, 0)]


def test_breaking_the_root_brings_down_the_whole_tower():
    cells = tower(4)

    assert sc.survivors(cells, ground(0), removed=[(0, 0, 0)]) == set()
    # breaking the top only takes the top
    assert sc.survivors(cells, ground(0), removed=[(0, 3, 0)]) == tower(3)


def test_a_bridge_hangs_from_its_column_up_to_six_blocks():
    column = tower(3)
    bridge = {(i, 2, 0) for i in range(1, 7)}  # six blocks sideways: distance 6
    too_long = bridge | {(7, 2, 0)}

    assert max(sc.distances(column | bridge, ground(0)).values()) == 6
    assert sc.distances(column | too_long, ground(0))[(7, 2, 0)] == sc.UNSTABLE
    assert sc.can_place((6, 2, 0), column | {(i, 2, 0) for i in range(1, 6)}, ground(0))
    assert not sc.can_place((7, 2, 0), column | bridge, ground(0))


def test_the_bridge_falls_with_its_column():
    column, bridge = tower(3), {(1, 2, 0), (2, 2, 0)}

    assert sc.survivors(column | bridge, ground(0), removed=[(0, 0, 0)]) == set()


def test_a_second_support_keeps_the_bridge_up():
    column, other = tower(3), tower(3, x=3)
    bridge = {(1, 2, 0), (2, 2, 0)}

    left = sc.survivors(column | other | bridge, ground(0), removed=[(0, 0, 0)])

    # the column's top block still touches the bridge, so it hangs from it
    assert left == other | bridge | {(0, 2, 0)}
