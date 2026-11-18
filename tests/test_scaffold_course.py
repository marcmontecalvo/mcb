from mclab import scaffold_course as sc

ORIGIN = (990, 80, 988)


def test_fixture_geometry():
    cells = sc.hole_cells(ORIGIN)
    assert len(cells) == 11 * 3 * 4
    assert sc.TOWER_B - sc.TOWER_A <= 12  # every span cell within distance 6 of a tower
    assert max(min(dx - sc.TOWER_A, sc.TOWER_B - dx) for dx in sc.HOLE_X) <= 6
    air = sc.protected_air(ORIGIN)
    assert all(cells[p] == "minecraft:air" for p in air)
    # the towers stand on the rims, the span runs above the hole
    assert not set(sc.tower_cells(ORIGIN, sc.TOWER_A)) & set(cells)
    assert set(sc.span_cells(ORIGIN)) & air == set()  # the span is above the hole


def test_foreign_and_verdict():
    good = {(0, 0, 0): sc.SCAFFOLDING}
    legs = {str(i): {"passed": True} for i in range(8)}
    assert sc.verdict(legs, good)["passed"]
    assert not sc.verdict(legs, {**good, (1, 0, 0): "minecraft:dirt"})["passed"]
    legs["3"] = {"passed": False}
    assert sc.verdict(legs, good)["failed_legs"] == ["3"]
    assert not sc.verdict({"0": {"passed": True}}, good)["passed"]


def test_pose_and_height_checks():
    assert sc.never_crouched(["STANDING", "STANDING"])
    assert not sc.never_crouched(["STANDING", "CROUCHING"])
    assert sc.stayed_up([84.0, 85.0], 84)
    assert not sc.stayed_up([84.0, 80.0], 84)
    assert not sc.stayed_up([], 84)
