from mclab import scaffold_course as sc

ORIGIN = (990, 80, 988)


def test_fixture_geometry():
    cells = sc.hole_cells(ORIGIN)
    assert len(cells) == 11 * 3 * 5  # four deep plus the floor laid under it
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


def test_edit_problems():
    assert sc.edit_problems("1", 5, 0) == []
    assert sc.edit_problems("1", 10, 0) == []  # up to twice the minimum
    assert sc.edit_problems("1", 11, 0)
    assert sc.edit_problems("0", 1, 0)  # a ground layer on a walk-through leg
    assert sc.edit_problems("3", 2, 2)  # broke scaffolding before the take-down
    assert sc.edit_problems("7", 0, 6) == []


def test_event_stats_and_standstills():
    events = [
        (1.0, "plan_finished", {"expanded": 50}),
        (2.0, "plan_finished", {"expanded": 900}),
        (9.0, "plan_finished", {"expanded": 7}),
        (2.0, "request", "heartbeat"),
    ]
    got = sc.event_stats(events, {"a": (0.0, 5.0), "b": (5.0, 10.0)})
    assert got == {
        "a": {"plans": 2, "max_expanded": 900},
        "b": {"plans": 1, "max_expanded": 7},
    }
    rows = [
        {"kind": "VIOLATION", "ticks": [110, 190], "unjustified_s": 4.0},
        {"kind": "VIOLATION", "ticks": [900, 950], "unjustified_s": 3.5},
        {"kind": "EXEMPT", "ticks": [10, 20], "unjustified_s": 0.0},
    ]
    by_leg, outside = sc.standstills(rows, {"a": (0, 100), "b": (100, 200)})
    assert list(by_leg) == ["b"] and len(outside) == 1


def test_finish_folds_in_caps_and_standstills():
    legs = {f"{i} x": {"passed": True} for i in range(8)}
    ranges = {n: (i * 100, i * 100 + 99) for i, n in enumerate(legs)}
    ok = sc.finish(legs, {}, ("NONE", []), [], True, ranges, {})
    assert ok["passed"]
    assert not sc.finish(
        legs, {}, ("VIOLATION 1 near cap hits", ["x"]), [], True, ranges, {}
    )["passed"]
    assert not sc.finish(
        legs, {}, ("NONE (1 far cap hits listed)", ["x"]), [], False, ranges, {}
    )["passed"]
    stall = [{"kind": "VIOLATION", "ticks": [310, 400], "unjustified_s": 4.0}]
    res = sc.finish(legs, {}, ("NONE", []), stall, True, ranges, {})
    assert res["failed_legs"] == ["3 x"] and legs["3 x"]["standstill_s"] == 4.0


def test_pillar_followed_by_bridge_keeps_pillar_target_as_exit():
    from mclab.navigation_segmented import SegmentedController
    from mclab.terrain_steps import StepPlan

    def make_nav(route, steps):
        nav = SegmentedController.__new__(SegmentedController)
        nav.route = list(route)
        nav.route_steps = {(s.source, s.target): s for s in steps}
        nav.terrain_queue = []
        nav.terrain_forecast = ()
        nav.terrain_queue_policy = None
        nav.breaks_reserved = 0
        nav.places_reserved = 0
        nav.terrain = object()
        nav.failed_edges = set()
        nav.search = object()
        nav._remaining_policy = lambda: None
        nav._begin_queued_step = lambda node, obs: None
        nav._begin_route_edit((0, 1, 0), {"body": {}})
        return nav

    pillar = StepPlan(
        (0, 1, 0),
        (0, 2, 0),
        places=((0, 1, 0),),
        kind="pillar",
        scaffold=((0, 1, 0),),
    )
    bridge = StepPlan(
        (0, 2, 0),
        (1, 2, 0),
        places=((1, 1, 0),),
        kind="step",
        scaffold=((1, 1, 0),),
    )
    nav_bridge = make_nav([(0, 1, 0), (0, 2, 0), (1, 2, 0)], [pillar, bridge])
    assert nav_bridge.terrain_queue[0].exit == (0, 2, 0)

    pillar_up = StepPlan(
        (0, 2, 0),
        (0, 3, 0),
        places=((0, 2, 0),),
        kind="pillar",
        scaffold=((0, 2, 0),),
    )
    nav_tower = make_nav([(0, 1, 0), (0, 2, 0), (0, 3, 0)], [pillar, pillar_up])
    assert nav_tower.terrain_queue[0].exit == (0, 3, 0)

    nav_walk = make_nav([(0, 1, 0), (0, 2, 0), (1, 2, 0)], [pillar])
    assert nav_walk.terrain_queue[0].exit == (1, 2, 0)


def test_live_scaffold_course_holds_and_allows_dirt():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "check-companion-scaffold-course.py"
    ).read_text(encoding="utf-8")
    assert 'DIRT = "minecraft:dirt"' in source
    assert '{"item": DIRT, "count": START}' in source
    assert "material_allowlist=(DIRT,)" in source
    assert "dirt_held == START" in source
