from mclab import tree_removal as tr
from mclab.build_runner import Result


class FakeTreeBody:
    """Mock companion body tracking operations, positions, and inventory for tree removal."""

    def __init__(
        self,
        world: dict[tr.Pos, str],
        inventory: dict[str, int],
        pos: tr.Pos = (0, 64, 0),
    ):
        self.w = dict(world)
        self.inv = dict(inventory)
        self.pos = pos
        self.equipped: str | None = None
        self.break_log: list[tuple[tr.Pos, str | None]] = []
        self.place_log: list[tuple[tr.Pos, str]] = []
        self.moves: list[tr.Pos] = [pos]

    def world(self, x: int, y: int, z: int) -> str | None:
        return self.w.get((x, y, z))

    def inventory(self) -> dict[str, int]:
        return dict(self.inv)

    def position(self) -> tr.Pos:
        return self.pos

    def equip(self, item: str) -> Result:
        self.equipped = item
        return Result(True)

    def walk_to(self, pos: tr.Pos, radius: float = 1.0) -> bool:
        self.pos = pos
        self.moves.append(pos)
        return True

    def goto_stance(self, target_pos: tr.Pos, reach: float = 4.5, avoid=()) -> bool:
        self.pos = (target_pos[0] + 1, target_pos[1], target_pos[2])
        self.moves.append(self.pos)
        return True

    def break_block(self, pos: tr.Pos, tool: str | None = None) -> Result:
        self.break_log.append((pos, tool))
        self.w[pos] = "minecraft:air"
        return Result(True)

    def place_block(self, pos: tr.Pos, state: str, item: str) -> Result:
        self.place_log.append((pos, item))
        self.w[pos] = state
        return Result(True)

    def collect_drops(self, near) -> None:
        pass


def test_best_axe_preference():
    assert tr.best_axe({"minecraft:wooden_axe": 1}) == "minecraft:wooden_axe"
    assert (
        tr.best_axe({"minecraft:stone_axe": 1, "minecraft:wooden_axe": 1})
        == "minecraft:stone_axe"
    )
    assert (
        tr.best_axe({"minecraft:iron_axe": 1, "minecraft:stone_axe": 1})
        == "minecraft:iron_axe"
    )
    assert (
        tr.best_axe({"minecraft:diamond_axe": 1, "minecraft:iron_axe": 1})
        == "minecraft:diamond_axe"
    )
    assert tr.best_axe({"minecraft:dirt": 64}) is None


def test_tree_detection():
    # Build a simulated oak tree at (5, 64, 5) with height 5
    world = {
        (5, 63, 5): "minecraft:dirt",
        (5, 64, 5): "minecraft:oak_log",
        (5, 65, 5): "minecraft:oak_log",
        (5, 66, 5): "minecraft:oak_log",
        (5, 67, 5): "minecraft:oak_log",
        (5, 68, 5): "minecraft:oak_log",
        # Leaves around the top
        (5, 69, 5): "minecraft:oak_leaves",
        (6, 68, 5): "minecraft:oak_leaves",
        (4, 68, 5): "minecraft:oak_leaves",
    }
    trees = tr.find_trees(lambda x, y, z: world.get((x, y, z)), [(5, 65, 5)])
    assert len(trees) == 1
    tree = trees[0]
    assert tree.base == (5, 64, 5)
    assert len(tree.trunk) == 5
    assert tree.trunk == [(5, 64, 5), (5, 65, 5), (5, 66, 5), (5, 67, 5), (5, 68, 5)]
    assert (5, 69, 5) in tree.leaves


def test_clear_trees_in_footprint():
    world = {
        (0, 63, 0): "minecraft:dirt",
        (0, 64, 0): "minecraft:oak_log",
        (0, 65, 0): "minecraft:oak_log",
        (0, 66, 0): "minecraft:oak_log",
    }
    body = FakeTreeBody(world, {"minecraft:iron_axe": 1})
    count = tr.clear_trees_in_footprint(body, [(0, 64, 0), (1, 64, 0)])
    assert count == 1
    assert body.world(0, 64, 0) == "minecraft:air"


def test_tree_feller_clears_leaves_on_no_line_of_sight():
    world = {
        (0, 63, 0): "minecraft:dirt",
        (0, 64, 0): "minecraft:oak_log",  # Log 1
        (0, 65, 0): "minecraft:oak_log",  # Log 2
        (0, 66, 0): "minecraft:oak_log",  # Log 3
        (0, 65, 1): "minecraft:oak_leaves",  # Leaf between stance and log
    }

    class LOSBody(FakeTreeBody):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.failed_once = False

        def break_block(self, pos, tool=None):
            if pos == (0, 66, 0) and not self.failed_once:
                self.failed_once = True
                return Result(False, "no_line_of_sight")
            return super().break_block(pos, tool)

    body = LOSBody(world, {"minecraft:iron_axe": 1}, pos=(0, 64, 1))
    trees = tr.find_trees(body.world, [(0, 64, 0)])
    feller = tr.TreeFeller(body)
    ok = feller.fell(trees[0])
    assert ok
    assert body.world(0, 64, 0) == "minecraft:air"
    assert body.world(0, 65, 0) == "minecraft:air"
    assert body.world(0, 66, 0) == "minecraft:air"


def test_tree_removal_ignores_placed_template_blocks():
    # Placed house logs in world matching template
    world = {
        (0, 63, 0): "minecraft:stone_bricks",
        (0, 64, 0): "minecraft:oak_log[axis=y]",
        (0, 65, 0): "minecraft:oak_log[axis=y]",
        (0, 66, 0): "minecraft:oak_log[axis=y]",
    }

    class MockTemplate:
        cells = {
            (0, 64, 0): "minecraft:oak_log[axis=y]",
            (0, 65, 0): "minecraft:oak_log[axis=y]",
            (0, 66, 0): "minecraft:oak_log[axis=y]",
        }

    template = MockTemplate()
    body = FakeTreeBody(world, {"minecraft:iron_axe": 1}, pos=(1, 64, 0))

    trees = tr.find_trees(body.world, list(template.cells.keys()), template=template)
    assert len(trees) == 0

    cleared = tr.clear_trees_in_footprint(
        body, list(template.cells.keys()), template=template
    )
    assert cleared == 0
    # Placed blocks must remain intact
    assert body.world(0, 64, 0) == "minecraft:oak_log[axis=y]"
    assert body.world(0, 65, 0) == "minecraft:oak_log[axis=y]"
    assert body.world(0, 66, 0) == "minecraft:oak_log[axis=y]"
    assert len(body.break_log) == 0


def test_unreachable_log_gives_up_without_a_break_retry_loop():
    # run 20261109T225208810361Z: ~640 failed breaks (no_line_of_sight/out_of_reach)
    # on one canopy while leaves stayed unbroken
    log, leaf = "minecraft:oak_log", "minecraft:oak_leaves"
    world = {(0, 70, 0): log, (1, 70, 0): leaf, (-1, 70, 0): leaf, (0, 71, 0): leaf}
    body = FakeTreeBody(world, {})
    calls = []

    def refuse(pos, tool=None):
        calls.append(pos)
        return Result(False, "no_line_of_sight")

    body.break_block = refuse
    feller = tr.TreeFeller(body)
    assert feller._break_log((0, 70, 0), None) is False
    assert len(calls) <= 2


def test_fell_does_not_walk_into_a_log_it_could_not_break():
    world = {(0, 63, 0): "minecraft:dirt"} | {
        (0, y, 0): "minecraft:oak_log" for y in (64, 65, 66, 67)
    }

    class Stuck(FakeTreeBody):
        walks: list = []

        def break_block(self, pos, tool=None):
            return Result(False, "out_of_reach")

        def walk_to(self, pos, *a, **k):
            self.walks.append(pos)
            return super().walk_to(pos, *a, **k)

    body = Stuck(world, {"minecraft:iron_axe": 1}, pos=(1, 64, 0))
    body.walks = []
    trees = tr.find_trees(body.world, [(0, 64, 0)])
    assert tr.TreeFeller(body).fell(trees[0]) is False
    assert (0, 65, 0) not in body.walks
    assert tr.clear_trees_in_footprint(body, [(0, 64, 0)]) == 0


# --- column method (run 20261110T003408754165Z) -------------------------------

LOG, VINE = "minecraft:oak_log", "minecraft:vine[east=true]"


class SightBody(FakeTreeBody):
    """Reach and sight like the live body: a break needs the eye within 4.5 of the
    block centre and a clear cell-by-cell ray; a refusal names the occluder."""

    def break_block(self, pos, tool=None):
        self.break_log.append((pos, tool))
        eye = (self.pos[0] + 0.5, self.pos[1] + 1.62, self.pos[2] + 0.5)
        centre = tuple(c + 0.5 for c in pos)
        near = [min(max(e, c), c + 1) for e, c in zip(eye, pos, strict=True)]
        dist = sum((a - b) ** 2 for a, b in zip(eye, near, strict=True)) ** 0.5
        if dist > 4.5:
            return Result(False, "out_of_reach", {})
        for i in range(1, 200):
            cell = tuple(
                int((e + (c - e) * i / 200) // 1)
                for e, c in zip(eye, centre, strict=True)
            )
            if cell == pos:
                break
            state = self.w.get(cell)
            if state and state != "minecraft:air" and cell != self.pos:
                return Result(
                    False,
                    "no_line_of_sight",
                    {"blocker": list(cell), "blocker_state": state},
                )
        self.w[pos] = "minecraft:air"
        return Result(True)

    def goto_stance(self, target_pos, reach=4.5, avoid=()):
        self.pos = (
            target_pos[0] + self.gap,
            min(target_pos[1], self.ground),
            target_pos[2],
        )
        self.moves.append(self.pos)
        return True

    ground, gap = 64, 1


def tree_at(x, z, height, ground=64):
    world = {(x, ground - 1, z): "minecraft:dirt"}
    world.update({(x, ground + i, z): LOG for i in range(height)})
    return world


def test_fell_breaks_the_base_first_then_the_column_from_inside_the_trunk():
    body = SightBody(tree_at(0, 0, 7), {"minecraft:iron_axe": 1}, pos=(6, 64, 0))
    body.gap = 3  # the top log is out of reach from this stance
    tree = tr.find_trees(body.world, [(0, 64, 0)])[0]

    assert tr.TreeFeller(body).fell(tree)

    broken = [pos for pos, _ in body.break_log if body.w.get(pos) == "minecraft:air"]
    assert broken[0] == (0, 64, 0)  # bottom log first, not left standing
    assert not [p for p in tree.trunk if tr.is_log(body.world(*p))]
    assert (0, 64, 0) in body.moves  # finished from where the trunk was
    assert not body.place_log  # no pillar, nothing placed in the world


def test_fell_breaks_the_vine_the_body_names_then_the_log():
    world = tree_at(0, 0, 3)
    world[(0, 64, 0)] = "minecraft:air"  # base already gone: stand in the column
    world[(0, 63, 0)] = "minecraft:dirt"
    world[(1, 65, 0)] = VINE  # between the stance at x=1 and the log at head height
    body = SightBody(world, {}, pos=(5, 64, 0))
    tree = tr.Tree(base=(0, 65, 0), trunk=[(0, 65, 0), (0, 66, 0)])

    assert tr.TreeFeller(body).fell(tree)
    assert body.w[(1, 65, 0)] == "minecraft:air"


def test_fell_does_not_break_a_hard_occluder():
    world = tree_at(0, 0, 2)
    world[(1, 64, 0)] = "minecraft:air"
    body = SightBody(world, {}, pos=(5, 64, 0))
    body.goto_stance = lambda *a, **k: setattr(body, "pos", (2, 64, 0)) or True
    world[(1, 64, 0)] = body.w[(1, 64, 0)] = "minecraft:chest"
    world[(1, 65, 0)] = body.w[(1, 65, 0)] = "minecraft:chest"
    tree = tr.find_trees(body.world, [(0, 64, 0)])[0]

    assert not tr.TreeFeller(body).fell(tree)
    assert body.w[(1, 64, 0)] == "minecraft:chest"
    assert len(body.break_log) <= 4  # bounded: no retry loop on a refused break


def test_three_trees_in_a_row_are_felled_nearest_first():
    world = {}
    for x in (4, 12, 20):
        world.update(tree_at(x, 0, 5))
    body = SightBody(world, {"minecraft:iron_axe": 1}, pos=(0, 64, 0))
    footprint = [(x, 64, 0) for x in range(0, 24)]

    assert tr.clear_trees_in_footprint(body, footprint) == 3
    assert not [p for p, state in body.w.items() if tr.is_log(state)]
    bases = list(dict.fromkeys(p for p, _ in body.break_log if p[1] == 64))
    assert bases == [(4, 64, 0), (12, 64, 0), (20, 64, 0)]


def test_tall_trunk_is_climbed_once_and_the_pillar_taken_down():
    body = SightBody(tree_at(0, 0, 10), {"minecraft:iron_axe": 1}, pos=(6, 64, 0))
    climbs = []

    def dig_to(feet):
        climbs.append(feet)
        for y in range(64, feet[1]):
            body.w[(feet[0], y, feet[2])] = "minecraft:dirt"
        body.pos = feet
        return True

    def break_block(pos, tool=None, _real=body.break_block):
        result = _real(pos, tool)
        if result.ok and pos == (body.pos[0], body.pos[1] - 1, body.pos[2]):
            body.pos = pos  # the body falls into the cell it broke
        return result

    body.dig_to, body.break_block = dig_to, break_block
    tree = tr.find_trees(body.world, [(0, 64, 0)])[0]
    assert tr.TreeFeller(body).fell(tree)
    assert len(climbs) == 1 and body.pos == (0, 64, 0)
    assert not any(s == "minecraft:dirt" and p[1] >= 64 for p, s in body.w.items())


def test_trees_rooted_outside_the_footprint_are_left_alone():
    world = tree_at(0, 0, 4) | tree_at(9, 0, 4)
    body = SightBody(world, {"minecraft:iron_axe": 1}, pos=(3, 64, 0))
    footprint = [(x, 64, z) for x in range(-2, 7) for z in range(-2, 3)]
    assert tr.clear_trees_in_footprint(body, footprint) == 1
    assert tr.is_log(body.w[(9, 64, 0)]) and not tr.is_log(body.w.get((0, 64, 0)))


def test_a_tree_rooted_outside_with_a_leaf_in_the_footprint_is_felled_whole():
    world = tree_at(9, 0, 4)
    world[(6, 66, 0)] = world[(7, 66, 0)] = world[(8, 66, 0)] = "minecraft:oak_leaves"
    body = SightBody(world, {"minecraft:iron_axe": 1}, pos=(3, 64, 0))
    footprint = [(x, y, 0) for x in range(-2, 7) for y in range(64, 70)]
    assert tr.clear_trees_in_footprint(body, footprint) == 1
    assert not [p for p, state in body.w.items() if tr.is_log(state)]


def test_fell_breaks_the_leaf_hiding_a_log_and_takes_every_log():
    # Run 2026-11-10: the top and bottom logs came down, a leaf blocked the rest.
    world = tree_at(0, 0, 5)
    world[(1, 65, 0)] = world[(1, 66, 0)] = "minecraft:oak_leaves"
    body = SightBody(world, {"minecraft:iron_axe": 1}, pos=(6, 64, 0))
    tree = tr.find_trees(body.world, [(0, 64, 0)])[0]

    assert tr.TreeFeller(body).fell(tree)
    assert not [p for p, s in body.w.items() if tr.is_log(s)]


def test_fell_retries_a_leaf_whose_first_break_failed():
    world = tree_at(0, 0, 3)
    world[(1, 65, 0)] = "minecraft:oak_leaves"
    body = SightBody(world, {"minecraft:iron_axe": 1}, pos=(6, 64, 0))
    refused = []

    def flaky(pos, tool=None, _real=body.break_block):
        if pos == (1, 65, 0) and not refused:
            refused.append(pos)
            return Result(False, "timeout")
        return _real(pos, tool)

    body.break_block = flaky
    tree = tr.find_trees(body.world, [(0, 64, 0)])[0]
    assert tr.TreeFeller(body).fell(tree)
    assert not [p for p, s in body.w.items() if tr.is_log(s)]


def test_a_leafless_trunk_is_a_tree_only_when_asked():
    w = tree_at(0, 0, 3)

    def world(x, y, z):
        return w.get((x, y, z))

    t = type("T", (), {"cells": {}})()  # no template cells
    assert not tr.find_trees(world, [(0, 64, 0)], template=t)
    assert tr.find_trees(world, [(0, 64, 0)], template=t, leafless=True)
