from copy import deepcopy
from types import SimpleNamespace

import pytest

from mclab.execution import policy_state, record_pending_policy
from mclab.terrain_transport import expand_terrain


def observation():
    return {
        "run_id": "run",
        "episode_id": "episode",
        "body_id": "body",
        "tick": 10,
        "sensor_profile": "awareness-v1",
        "awareness": {"coverage": {"sample_tick": 7}},
        "terrain_sections": {
            "schema_version": 1,
            "encoding": "sparse_section_palette",
            "unknown_is_air": False,
            "sample_tick": 7,
            "known_cells": 1,
            "palette": [
                {
                    "block": "minecraft:air",
                    "fluid": "minecraft:empty",
                    "replaceable": True,
                    "properties": {},
                    "collision_boxes": [],
                }
            ],
            "sections": [{"section": [-1, -1, 1], "cells": [[255, 0, 6]]}],
            "encoding_ms": 0.2,
        },
    }


def test_sparse_negative_section_expands_known_air_without_filling_unknowns():
    source = observation()
    before = deepcopy(source)
    result = expand_terrain(source)
    assert source == before
    assert "terrain_sections" not in result
    assert len(result["visible_blocks"]) == 1
    assert result["visible_blocks"][0]["position"] == [-16, -1, 31]
    assert result["visible_blocks"][0]["block"] == "minecraft:air"
    assert result["visible_blocks"][0]["observed_tick"] == 6
    legacy = {"sensor_profile": "local-visibility-v1", "visible_blocks": []}
    assert expand_terrain(legacy) is legacy


@pytest.mark.parametrize("row", [[4096, 0, 6], [255, 5, 6], [255, 0, 8], [True, 0, 6]])
def test_invalid_offsets_palettes_ages_and_types_fail_closed(row):
    source = observation()
    source["terrain_sections"]["sections"][0]["cells"] = [row]
    with pytest.raises(ValueError, match="compact terrain"):
        expand_terrain(source)


@pytest.mark.parametrize(
    "change", ["duplicate", "incomplete", "profile", "mixed", "epoch"]
)
def test_gaps_duplicates_and_mixed_contracts_cannot_be_silently_admitted(change):
    source = observation()
    batch = source["terrain_sections"]
    if change == "duplicate":
        batch["known_cells"] = 2
        batch["sections"][0]["cells"].append([255, 0, 6])
    elif change == "incomplete":
        batch["known_cells"] = 2
    elif change == "profile":
        source["sensor_profile"] = "local-visibility-v1"
    elif change == "mixed":
        source["visible_blocks"] = []
    else:
        source["awareness"]["coverage"]["sample_tick"] = 8
    with pytest.raises(ValueError, match="compact terrain"):
        expand_terrain(source)


def test_driver_records_compact_wire_and_supplies_expanded_facts_to_controller():
    recorded = []
    source = observation()
    client = SimpleNamespace(
        run_id="run",
        episode_id="episode",
        body_id="body",
        state=lambda: None,
        http=lambda _: source,
        record=lambda kind, payload: recorded.append((kind, payload)),
        last_resource_tick=100,
    )
    visible = policy_state(client, record=False)
    assert visible["visible_blocks"][0]["position"] == [-16, -1, 31]
    record_pending_policy(client)
    assert recorded[0][0] == "policy_observation"
    assert "terrain_sections" in recorded[0][1]
    assert "visible_blocks" not in recorded[0][1]
    assert client.pending_policy_wire is None


def test_transport_latency_uses_high_resolution_clock_not_coarse_windows_uptime(
    monkeypatch,
):
    from mclab import execution

    ticks = iter([1_000, 1_005_000, 2_000_000, 2_500_000])
    monkeypatch.setattr(execution.time, "perf_counter_ns", lambda: next(ticks))
    monkeypatch.setattr(execution.time, "monotonic_ns", lambda: 17)
    source = observation()
    client = SimpleNamespace(
        run_id="run",
        episode_id="episode",
        body_id="body",
        state=lambda: None,
        http=lambda _: source,
    )
    policy_state(client, record=False)
    assert client.pending_policy[1] == 1.004
    assert client.terrain_decode_ms == 0.5
    assert client.pending_policy[2] == 17
