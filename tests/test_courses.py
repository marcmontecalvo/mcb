from collections import deque

import pytest

from mclab.courses import build, compile_build, generate, verify_generated
from mclab.paths import read_json


@pytest.mark.parametrize("course", ["C0", "C1", "C2", "C3", "C4"])
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
