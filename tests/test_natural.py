import math

from mclab import natural


def _cells():
    # A dry ridge field: height varies along x, with a pond in one corner.
    cells = []
    for x in range(-160, 161, 4):
        for z in range(-160, 161, 4):
            water = x > 120 and z > 120
            height = 70 + int(12 * math.sin(x / 20))
            cells.append([x, z, height, water, "water" if water else "grass_block"])
    return cells


def test_routes_are_dry_spread_and_in_length_band():
    routes = natural.pick_routes(_cells(), count=5)
    assert len(routes) == 5
    for route in routes:
        assert natural.ROUTE_MIN <= route["length"] <= natural.ROUTE_MAX
        assert route["water_fraction"] <= 0.1
        assert -180 <= route["yaw"] < 180
    for a in routes:
        for b in routes:
            if a is not b:
                assert math.dist(a["start"][::2], b["start"][::2]) >= 48


def test_property_edit_only_touches_named_keys():
    text = "level-name=world\nlevel-type=minecraft\\:flat\nserver-port=25575\n"
    edited = natural._set_properties(text, {"level-name": "natural-1"})
    assert edited == text.replace("level-name=world", "level-name=natural-1")
