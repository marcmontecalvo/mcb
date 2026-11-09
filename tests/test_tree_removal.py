import pytest

from mclab import tree_removal as tr
from mclab.build_runner import Result


class FakeTreeBody:
    """Mock companion body tracking operations, positions, and inventory for tree removal."""

    def __init__(self, world: dict[tr.Pos, str], inventory: dict[str, int], pos: tr.Pos = (0, 64, 0)):
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
    assert tr.best_axe({"minecraft:stone_axe": 1, "minecraft:wooden_axe": 1}) == "minecraft:stone_axe"
    assert tr.best_axe({"minecraft:iron_axe": 1, "minecraft:stone_axe": 1}) == "minecraft:iron_axe"
    assert tr.best_axe({"minecraft:diamond_axe": 1, "minecraft:iron_axe": 1}) == "minecraft:diamond_axe"
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


def test_tree_feller_algorithm_standard_tree():
    # 5 logs tall tree: (0, 64, 0) up to (0, 68, 0)
    world = {
        (0, 63, 0): "minecraft:dirt",
        (0, 64, 0): "minecraft:oak_log",  # Log 1 (base)
        (0, 65, 0): "minecraft:oak_log",  # Log 2
        (0, 66, 0): "minecraft:oak_log",  # Log 3
        (0, 67, 0): "minecraft:oak_log",  # Log 4
        (0, 68, 0): "minecraft:oak_log",  # Log 5
    }
    inv = {"minecraft:iron_axe": 1, "minecraft:dirt": 16}
    body = FakeTreeBody(world, inv, pos=(2, 64, 0))

    trees = tr.find_trees(body.world, [(0, 64, 0)])
    assert len(trees) == 1
    feller = tr.TreeFeller(body)
    ok = feller.fell(trees[0])

    assert ok
    # Verify axe was equipped
    assert body.equipped == "minecraft:iron_axe"

    # Verify breaking sequence:
    # 1. Logs 2, 3, 4 broken first (while standing on ground)
    # 2. Step onto Log 1
    # 3. Log 5 broken from on top of Log 1
    # 4. Step down
    # 5. Log 1 broken LAST!
    broken_positions = [b[0] for b in body.break_log]
    assert broken_positions[0] == (0, 65, 0)  # Log 2
    assert broken_positions[1] == (0, 66, 0)  # Log 3
    assert broken_positions[2] == (0, 67, 0)  # Log 4
    assert broken_positions[3] == (0, 68, 0)  # Log 5
    assert broken_positions[-1] == (0, 64, 0)  # Log 1 (base) broken last!

    # Verify axe was passed as tool to all log breaks
    for pos, tool in body.break_log:
        assert tool == "minecraft:iron_axe"

    # Verify world has no logs left
    for y in range(64, 69):
        assert body.world(0, y, 0) == "minecraft:air"


def test_tree_feller_branching_tree():
    # Branching oak tree with branch logs
    world = {
        (10, 63, 10): "minecraft:dirt",
        (10, 64, 10): "minecraft:oak_log",  # Log 1
        (10, 65, 10): "minecraft:oak_log",  # Log 2
        (10, 66, 10): "minecraft:oak_log",  # Log 3
        (10, 67, 10): "minecraft:oak_log",  # Log 4
        (10, 68, 10): "minecraft:oak_log",  # Log 5
        (11, 68, 10): "minecraft:oak_log",  # Branch log
        (11, 68, 11): "minecraft:oak_leaves",  # Leaf beside branch
    }
    inv = {"minecraft:stone_axe": 1}
    body = FakeTreeBody(world, inv, pos=(12, 64, 10))

    trees = tr.find_trees(body.world, [(10, 65, 10)])
    assert len(trees) == 1
    assert len(trees[0].branches) == 1
    feller = tr.TreeFeller(body)
    ok = feller.fell(trees[0])

    assert ok
    # All logs must be broken
    assert body.world(10, 64, 10) == "minecraft:air"
    assert body.world(11, 68, 10) == "minecraft:air"
    # Log 1 was broken last
    assert body.break_log[-1][0] == (10, 64, 10)


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


def test_tree_feller_carves_corridor_through_leaves():
    # Bot stands outside tree canopy with leaves blocking the path to the trunk
    world = {
        (0, 63, 0): "minecraft:dirt",
        (0, 64, 0): "minecraft:oak_log",  # Log 1
        (0, 65, 0): "minecraft:oak_log",  # Log 2
        (0, 66, 0): "minecraft:oak_log",  # Log 3
        # Leaves at feet and head height along approach path x=1 and x=2
        (1, 64, 0): "minecraft:oak_leaves",
        (1, 65, 0): "minecraft:oak_leaves",
        (2, 64, 0): "minecraft:oak_leaves",
        (2, 65, 0): "minecraft:oak_leaves",
    }
    body = FakeTreeBody(world, {"minecraft:iron_axe": 1}, pos=(4, 64, 0))
    trees = tr.find_trees(body.world, [(0, 64, 0)])
    feller = tr.TreeFeller(body)
    ok = feller.fell(trees[0])
    assert ok
    # Leaves in corridor must have been broken
    assert body.world(1, 64, 0) == "minecraft:air"
    assert body.world(1, 65, 0) == "minecraft:air"
    # All logs felled
    assert body.world(0, 64, 0) == "minecraft:air"
    assert body.world(0, 65, 0) == "minecraft:air"
    assert body.world(0, 66, 0) == "minecraft:air"


def test_tree_feller_protects_log1_if_upper_logs_remain():
    # Tree where upper log fails to break
    world = {
        (0, 63, 0): "minecraft:dirt",
        (0, 64, 0): "minecraft:oak_log",  # Log 1
        (0, 65, 0): "minecraft:oak_log",  # Log 2
        (0, 66, 0): "minecraft:oak_log",  # Log 3
    }

    class StubbornBody(FakeTreeBody):
        def break_block(self, pos, tool=None):
            if pos == (0, 66, 0):
                return Result(False, "unbreakable")
            return super().break_block(pos, tool)

    body = StubbornBody(world, {"minecraft:iron_axe": 1}, pos=(1, 64, 0))
    trees = tr.find_trees(body.world, [(0, 64, 0)])
    feller = tr.TreeFeller(body)
    feller.fell(trees[0])
    # Log 3 failed to break, so Log 1 MUST NOT have been broken!
    # Log 1 is never in break_log
    broken_targets = [b[0] for b in body.break_log]
    assert (0, 64, 0) not in broken_targets


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

    cleared = tr.clear_trees_in_footprint(body, list(template.cells.keys()), template=template)
    assert cleared == 0
    # Placed blocks must remain intact
    assert body.world(0, 64, 0) == "minecraft:oak_log[axis=y]"
    assert body.world(0, 65, 0) == "minecraft:oak_log[axis=y]"
    assert body.world(0, 66, 0) == "minecraft:oak_log[axis=y]"
    assert len(body.break_log) == 0


