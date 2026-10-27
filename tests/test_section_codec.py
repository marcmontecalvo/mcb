import json

from mclab.section_codec import decode_section, encode_section, section_key


def test_sparse_negative_section_preserves_air_unknown_and_all_facts():
    cells = [
        {"position": [-16, -1, 31], "block": "minecraft:air", "observed_tick": 3},
        {
            "position": [-1, -16, 16],
            "block": "minecraft:stone",
            "properties": {"axis": "y"},
            "collision_boxes": [[0, 0, 0, 1, 1, 1]],
            "observation_session": "first",
        },
    ]
    assert section_key(cells[0]["position"]) == (-1, -1, 1)
    assert decode_section((-1, -1, 1), encode_section(cells)) == cells


def test_dense_section_compresses_and_decoded_records_are_independent():
    cells = [
        {"position": [x, y, z], "block": "minecraft:stone", "properties": {"a": 1}}
        for x in range(16)
        for y in range(16)
        for z in range(16)
    ]
    payload = encode_section(cells)
    assert len(payload) < len(json.dumps(cells).encode()) // 5
    decoded = decode_section((0, 0, 0), payload)
    assert decoded == cells
    decoded[0]["properties"]["a"] = 2
    assert decoded[1]["properties"]["a"] == 1
