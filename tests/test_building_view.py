from mclab.building_view import BuildingView


def test_permissions_transform_only_known_hand_openable_doors_and_disabled_vines():
    door = {"block": "minecraft:oak_door", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    iron = {"block": "minecraft:iron_door", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    vine = {"block": "minecraft:vine", "collision_boxes": []}
    raw = {(0, 0, 0): door, (1, 0, 0): iron, (2, 0, 0): vine}
    enabled = BuildingView(raw, doors=True, vines=False)
    assert enabled[(0, 0, 0)]["collision_boxes"] == []
    assert enabled[(1, 0, 0)] == iron
    assert enabled[(2, 0, 0)]["block"] == "minecraft:air"
    assert enabled.get((99, 0, 0)) is None
    assert raw[(0, 0, 0)] == door and raw[(2, 0, 0)] == vine
    disabled = BuildingView(raw, doors=False, vines=True)
    assert disabled[(0, 0, 0)] == door and disabled[(2, 0, 0)] == vine
