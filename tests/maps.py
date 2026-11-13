"""Small cell maps and observations shared by several test modules."""


def floor_map(positions):
    cells = {}
    for x, z in positions:
        for y in range(5):
            cells[x, y, z] = {
                "position": [x, y, z],
                "block": "minecraft:stone" if y == 0 else "minecraft:air",
                "collision_boxes": [[0, 0, 0, 1, 1, 1]] if y == 0 else [],
            }
    return cells


def observation(cells, *, x=0.5, tick=1):
    return {
        "sensor_profile": "local-visibility-v1",
        "tick": tick,
        "awareness": {"coverage": {"sample_tick": tick}},
        "visible_blocks": list(cells.values()),
        "body": {"position": [x, 1, 0.5], "yaw": -90, "pitch": 0},
    }
