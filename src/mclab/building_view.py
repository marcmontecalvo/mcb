"""Apply route-local door and vine permissions to observed geometry."""

from collections.abc import Mapping

VINES = frozenset(
    {
        "minecraft:vine",
        "minecraft:twisting_vines",
        "minecraft:twisting_vines_plant",
        "minecraft:weeping_vines",
        "minecraft:weeping_vines_plant",
        "minecraft:cave_vines",
        "minecraft:cave_vines_plant",
    }
)


def view(cell, doors, vines):
    if cell is None:
        return None
    block = cell.get("block")
    if (
        doors
        and isinstance(block, str)
        and block != "minecraft:iron_door"
        and (block.endswith("_door") or block.endswith("_fence_gate"))
        and cell.get("collision_boxes") != []
    ):
        return {**cell, "collision_boxes": []}
    if not vines and block in VINES:
        return {**cell, "block": "minecraft:air"}
    return cell


class BuildingView(Mapping):
    def __init__(self, cells, doors, vines):
        self.cells, self.doors, self.vines = cells, doors, vines

    def __getitem__(self, key):
        return view(self.cells[key], self.doors, self.vines)

    def __iter__(self):
        return iter(self.cells)

    def __len__(self):
        return len(self.cells)
