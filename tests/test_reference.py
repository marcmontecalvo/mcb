from mclab.reference import analyze, analyze_interactions


def sample(tick, y, grounded, *, jump=False, forward=False, z=0):
    return {
        "tick": tick,
        "data": {
            "position": [0, y, z],
            "on_ground": grounded,
            "yaw": 0,
            "buttons": {
                "forward": forward,
                "backward": False,
                "left": False,
                "right": False,
                "jump": jump,
                "sneak": False,
                "sprint": False,
            },
        },
    }


def test_reference_jump_requires_received_jump_input():
    values = [
        sample(1, 80, True),
        sample(2, 80.4, False, jump=True),
        sample(3, 81.25, False),
        sample(4, 80, True),
    ]
    assert analyze(values)["jump_peak_heights"] == [1.25]
    values[1]["data"]["buttons"]["jump"] = False
    assert analyze(values)["jump_peak_heights"] == []


def test_reference_walk_rejects_gaps_and_sprint():
    values = [sample(i, 80, True, forward=True, z=i * 0.215) for i in range(21)]
    assert analyze(values)["walk_20_tick_distances"] == [4.3]
    values[5]["data"]["buttons"]["sprint"] = True
    assert analyze(values)["walk_20_tick_distances"] == []
    values[5]["data"]["buttons"]["sprint"] = False
    values[-1]["tick"] = 22
    assert analyze(values)["walk_20_tick_distances"] == []


def test_interactions_preserve_actual_break_ticks_and_inventory_decrement():
    events = [
        {
            "type": "dig_packet",
            "tick": 10,
            "data": {"position": [1, 80, 2], "action": "START_DESTROY_BLOCK"},
        },
        {
            "type": "block_broken",
            "tick": 70,
            "data": {"position": [1, 80, 2], "block": "minecraft:oak_log"},
        },
        {
            "type": "use_packet",
            "tick": 80,
            "data": {
                "client_sequence": 3,
                "held_item": "minecraft:dirt",
                "held_count": 16,
                "face": "up",
            },
        },
        {
            "type": "use_result",
            "tick": 80,
            "data": {
                "client_sequence": 3,
                "held_count": 15,
                "adjacent_block": "minecraft:dirt",
            },
        },
    ]
    result = analyze_interactions(events)
    assert result["mining"] == [{"block": "minecraft:oak_log", "ticks": 60}]
    assert result["placements"][0]["inventory_decrement"] == 1
    assert result["placements"][0]["face"] == "up"


def test_aborted_human_mining_is_not_reported_as_a_completed_mine():
    events = [
        {
            "type": "dig_packet",
            "tick": 10,
            "data": {"position": [1, 80, 2], "action": "START_DESTROY_BLOCK"},
        },
        {
            "type": "dig_packet",
            "tick": 20,
            "data": {"position": [1, 80, 2], "action": "ABORT_DESTROY_BLOCK"},
        },
        {
            "type": "block_broken",
            "tick": 70,
            "data": {"position": [1, 80, 2], "block": "minecraft:oak_log"},
        },
    ]
    assert analyze_interactions(events)["mining"] == []
