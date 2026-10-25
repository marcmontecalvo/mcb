import copy
from collections import deque

import pytest

from mclab.courses import build, compile_build, generate, verify_generated
from mclab.paths import read_json


@pytest.mark.parametrize("course", ["C0", "C1", "C2", "C3", "C4", "C5"])
def test_development_course_is_reproducible_and_fills_are_bounded(course):
    for seed in range(20):
        spec = generate(course, seed, [1000, 80, 1000])
        assert spec == generate(course, seed, [1000, 80, 1000])
        assert spec["initial_inventory"] == []
        assert spec["place_whitelist"] == []
        for line in compile_build(spec):
            fields = line.split()
            coordinates = [int(v) for v in fields[1:7]]
            volume = 1
            for i in range(3):
                volume *= coordinates[i + 3] - coordinates[i] + 1
            assert 1 <= volume <= 32768
            assert "~" not in line and "^" not in line


def test_c1_exact_straight_target_and_support():
    spec = generate("C1", 0, [1000, 80, 1000])
    assert spec["yaw"] == 0
    assert spec["target"] == [0, 0, 12]
    assert spec["tick_limit"] == 600
    platform = spec["geometry"][0]
    assert platform["from"][1] == -4 and platform["to"][1] == -1
    assert spec["clear_region"]["min"][0] < platform["from"][0]
    assert spec["spec_hash"] == (
        "8632e6132b76947bc406ac6c5bbee311d1fe569c9b07cd1fdaa8afae93ea9bdf"
    )


@pytest.mark.parametrize(
    "course,seed,expected",
    [
        ("C0", 0, "7dcf7cb9abe7256995b099d23763f285d97e4f293a69ced56c6ea10ed1a0c646"),
        ("C0", 7, "9567451a01629131d82678a8492d72ddbaf61ea4549577ee141fec1f603ecd5a"),
        ("C0", 19, "a16cb49e80e1a3515e35d1ff5db5a416edd28be18bac8f0dfad788b53b244222"),
        ("C2", 0, "3c06c32d929ec43f11f34c9909ce2bc4301e255ec29bc4d49fb9a0710c7c221d"),
        ("C2", 7, "dcdc209d2fd4bd82325e6053529f2d3037f519962791775c7717876681b43d31"),
        ("C2", 19, "49233eb2a3f277ab596e7c8f4b5b1b342162748ade238246e3e4840b55ccca43"),
    ],
)
def test_unchanged_course_specification_hashes(course, seed, expected):
    assert generate(course, seed, [1000, 80, 1000])["spec_hash"] == expected


def test_c2_changes_both_heading_and_target_over_development_seeds():
    specs = [generate("C2", seed, [1000, 80, 1000]) for seed in range(20)]
    assert len({tuple(s["target"]) for s in specs}) == 4
    assert len({s["yaw"] for s in specs}) > 10


def test_c3_has_a_walkable_detour_and_blocks_the_direct_route():
    spec = generate("C3", 0, [1000, 80, 1000])
    obstacle = spec["geometry"][1]
    assert obstacle["to"][1] - obstacle["from"][1] + 1 >= 2
    occupied = {
        (x, z)
        for x in range(obstacle["from"][0], obstacle["to"][0] + 1)
        for z in range(obstacle["from"][2], obstacle["to"][2] + 1)
    }
    assert any((0, z) in occupied for z in range(12))
    queue = deque([((0, 0), 0)])
    seen = {(0, 0)}
    while queue:
        (x, z), distance = queue.popleft()
        if (x, z) == (0, 12):
            assert distance > 12
            break
        for nx, nz in [(x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)]:
            if (
                -16 <= nx <= 16
                and -16 <= nz <= 16
                and (nx, nz) not in occupied
                and (nx, nz) not in seen
            ):
                seen.add((nx, nz))
                queue.append(((nx, nz), distance + 1))
    else:
        pytest.fail("C3 has no walkable detour")


def test_c4_rise_spans_permitted_width():
    spec = generate("C4", 0, [1000, 80, 1000])
    rise = spec["geometry"][1]
    assert rise["from"] == [-16, 0, 5]
    assert rise["to"] == [16, 0, 8]
    assert rise["to"][2] < spec["target"][2]


@pytest.mark.parametrize("course", ["C3", "C4", "C5"])
def test_navigation_geometry_has_twenty_five_physical_layouts(course):
    from mclab.paths import digest

    specs = [generate(course, seed, [1000, 80, 1000]) for seed in range(25)]
    # Exclude seed, orientation and metadata: geometry itself must differ.
    assert len({digest(spec["geometry"]) for spec in specs}) == 25
    for spec in specs:
        assert spec["generator_version"] == "0.2.0"
        assert spec["development_only"] and spec["qualification"] == "not_run"
        assert spec["sensor_profile"] == "pending-qualification"
        assert spec["geometry"][-1]["from"] == spec["target"]
        for feature in spec["geometry"]:
            for axis in range(3):
                assert spec["clear_region"]["min"][axis] <= feature["from"][axis]
                assert feature["from"][axis] <= feature["to"][axis]
                assert feature["to"][axis] <= spec["clear_region"]["max"][axis]


@pytest.mark.parametrize("course", ["C3", "C5"])
def test_every_detour_blocks_direct_ground_route_but_has_both_side_routes(course):
    for seed in range(25):
        spec = generate(course, seed, [1000, 80, 1000])
        wall = spec["geometry"][1]
        assert wall["from"][0] <= 0 <= wall["to"][0]
        assert wall["from"][1] == 0 and wall["to"][1] >= 1
        assert 0 < wall["from"][2] <= wall["to"][2] < spec["target"][2]
        # Cell-centered routes left and right retain at least a full free column
        # around a two-block-high wall, all supported by the platform.
        assert -16 <= wall["from"][0] - 1
        assert wall["to"][0] + 1 <= 16


@pytest.mark.parametrize("course", ["C4", "C5"])
def test_every_crest_requires_one_block_ascent_and_descent_before_target(course):
    for seed in range(25):
        spec = generate(course, seed, [1000, 80, 1000])
        crest = spec["geometry"][-2]
        assert crest["from"][0] == -16 and crest["to"][0] == 16
        assert crest["from"][1] == crest["to"][1] == 0
        assert 0 < crest["from"][2] <= crest["to"][2] < spec["target"][2]
        if course == "C5":
            wall = spec["geometry"][1]
            assert wall["to"][2] + 1 < crest["from"][2]
            assert spec["tick_limit"] == 1800
    if course == "C5":
        assert (
            len({generate(course, seed, [1000, 80, 1000])["yaw"] for seed in range(25)})
            > 10
        )


def test_generation_does_not_mutate_input_profiles_or_share_geometry(monkeypatch):
    from mclab import courses

    profiles = read_json(courses.ROOT / "courses/profiles.json")
    original = copy.deepcopy(profiles)
    monkeypatch.setattr(courses, "read_json", lambda _: profiles)
    first = generate("C3", 1, [1000, 80, 1000])
    generate("C3", 12, [1000, 80, 1000])
    generate("C5", 14, [1000, 80, 1000])
    first["geometry"][0]["from"][0] = -999
    first["geometry"][1]["from"][0] = -999
    assert profiles == original
    assert generate("C3", 1, [1000, 80, 1000])["geometry"][0]["from"][0] == -16


def test_compilation_and_tamper_detection(tmp_path):
    build("C1", 0, tmp_path)
    assert not verify_generated(tmp_path)["authoritative_world_verified"]
    metadata = read_json(tmp_path / "pack.mcmeta")
    assert metadata["pack"]["min_format"] == [121, 0]
    function = tmp_path / "data/mclab/function/c1_build.mcfunction"
    function.write_text(
        function.read_text().replace("minecraft:oak_log", "minecraft:air")
    )
    with pytest.raises(ValueError, match="commands"):
        verify_generated(tmp_path)


@pytest.mark.parametrize(
    "course,origin", [("C9", [1000, 80, 1000]), ("C1", [0, 0, 0]), ("C1", [0.5, 80, 0])]
)
def test_invalid_courses_and_origins_are_rejected(course, origin):
    with pytest.raises(ValueError):
        generate(course, 0, origin)
