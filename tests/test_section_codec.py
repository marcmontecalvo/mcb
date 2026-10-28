import json
import zlib

from mclab.section_codec import (
    decode_section,
    encode_section,
    merge_section,
    section_key,
)


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


def test_sparse_merge_matches_full_rewrite_and_prunes_superseded_palette():
    original = [
        {
            "position": [-16 + x, -1, 31],
            "block": "minecraft:stone",
            "properties": {"state": "old"},
            "observed_tick": 2,
            "observation_session": "prior",
        }
        for x in range(16)
    ]
    key = (-1, -1, 1)
    payload = encode_section(original)
    expected = {tuple(c["position"]): c for c in original}
    for tick in range(3, 23):
        changed = {
            "position": [-16, -1, 31],
            "block": "minecraft:air",
            "properties": {"state": str(tick)},
            "observed_tick": tick,
            "observed_at_ns": tick * 1000,
            "observation_session": "new",
        }
        added = {"position": [-16, -2, 31], "block": "minecraft:air"}
        changes = [changed, added]
        payload = merge_section(payload, changes)
        expected.update((tuple(c["position"]), c) for c in changes)
        rewritten = encode_section(expected.values())
        assert decode_section(key, payload) == decode_section(key, rewritten)
        palette, rows = json.loads(zlib.decompress(payload))
        assert len(palette) == 3
        assert len(rows) == 17
    # No initial payload and a restored payload both follow last-observation wins.
    duplicate = {**changed, "block": "minecraft:dirt"}
    assert decode_section(key, merge_section(None, [changed, duplicate])) == [duplicate]
