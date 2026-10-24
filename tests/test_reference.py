from mclab import reference
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


def test_strafe_windows_reject_turns_gaps_and_diagonal_input():
    values = [sample(i, 80, True) for i in range(21)]
    for i, event in enumerate(values):
        event["data"]["position"][0] = i * 0.215
        event["data"]["buttons"]["left"] = True
    assert analyze(values)["strafe_20_tick_distances"]["left"] == [4.3]
    values[10]["data"]["buttons"]["forward"] = True
    assert analyze(values)["strafe_20_tick_distances"]["left"] == []
    values[10]["data"]["buttons"]["forward"] = False
    values[10]["data"]["yaw"] = 20
    assert analyze(values)["strafe_20_tick_distances"]["left"] == []
    values[10]["data"]["yaw"] = 0
    values[10]["tick"] = 11
    assert analyze(values)["strafe_20_tick_distances"]["left"] == []


def test_sprint_windows_use_received_button_and_retain_timing_caveat():
    values = [sample(i, 80, True, forward=True, z=i * 0.28) for i in range(21)]
    assert analyze(values)["sprint_input_20_tick_distances"] == []
    for event in values:
        event["data"]["buttons"]["sprint"] = True
    distances = analyze(values)["sprint_input_20_tick_distances"]
    assert len(distances) == 1 and abs(distances[0] - 5.6) < 1e-9
    assert "sprint state" in analyze(values)["caveats"]


def test_release_tracks_residual_travel_without_counting_a_button_change_as_stop():
    values = [sample(0, 80, True, forward=True, z=0)]
    values += [sample(i, 80, True, z=0.1 + i * 0.01) for i in range(1, 7)]
    release = analyze(values)["release_segments"][0]
    assert release["start_tick"] == 1
    assert release["end_tick"] == 6
    assert abs(release["horizontal_path_distance"] - 0.16) < 1e-9
    values[3]["data"]["buttons"]["left"] = True
    assert len(analyze(values)["release_segments"]) == 2
    values[1]["tick"] = 2
    assert all(r["start_tick"] != 2 for r in analyze(values)["release_segments"])


def test_mining_restart_measures_from_new_start_and_retains_abort():
    events = [
        {
            "type": "dig_packet",
            "tick": tick,
            "data": {"position": [1, 80, 2], "action": action},
        }
        for tick, action in [
            (10, "START_DESTROY_BLOCK"),
            (30, "ABORT_DESTROY_BLOCK"),
            (50, "START_DESTROY_BLOCK"),
        ]
    ]
    events.append(
        {
            "type": "block_broken",
            "tick": 110,
            "data": {"position": [1, 80, 2], "block": "minecraft:oak_log"},
        }
    )
    result = analyze_interactions(events)
    assert result["mining"] == [{"block": "minecraft:oak_log", "ticks": 60}]
    assert result["mining_interruptions"] == [
        {"position": [1, 80, 2], "start_tick": 10, "abort_tick": 30, "ticks": 20}
    ]


def test_attack_timing_ignores_missing_target_and_pairs_each_result_once():
    events = [
        {"type": "attack_packet", "tick": 0, "data": {"target_present": False}},
        {"type": "attack_result", "tick": 0, "data": {"target_present": False}},
    ]
    for tick, before, after in [(10, 10, 9), (12, 9, 9), (60, 9, 8)]:
        for kind, health in [("attack_packet", before), ("attack_result", after)]:
            events.append(
                {
                    "type": kind,
                    "tick": tick,
                    "data": {
                        "uuid": "cow",
                        "health": health,
                        "within_entity_range": True,
                    },
                }
            )
    result = analyze_interactions(events)
    assert [a["health_decrement"] for a in result["attacks"]] == [1, 0, 1]
    assert [a["ticks_since_previous_recorded_attack"] for a in result["attacks"]] == [
        None,
        2,
        48,
    ]
    assert "invulnerability" in result["attack_timing_caveat"]


def test_sprint_state_requires_direct_confirmation_for_every_sample():
    values = [sample(i, 80, True, forward=True, z=i * 0.28) for i in range(21)]
    for event in values:
        event["data"]["buttons"]["sprint"] = True
    assert analyze(values)["sprint_state_20_tick_distances"] == []
    for event in values:
        event["data"]["sprinting"] = True
    assert len(analyze(values)["sprint_state_20_tick_distances"]) == 1
    values[10]["data"]["sprinting"] = False
    assert analyze(values)["sprint_state_20_tick_distances"] == []


def test_direct_attack_strength_is_preserved_separately_from_damage():
    events = [
        {
            "type": kind,
            "tick": 10,
            "data": {
                "uuid": "cow",
                "health": health,
                "attack_strength": strength,
                "within_entity_range": True,
            },
        }
        for kind, health, strength in [
            ("attack_packet", 10, 1.0),
            ("attack_result", 10, 0.0),
        ]
    ]
    attack = analyze_interactions(events)["attacks"][0]
    assert attack["health_decrement"] == 0
    assert attack["attack_strength_before"] == 1.0
    assert attack["attack_strength_after"] == 0.0
    values = [sample(10, 80, True), sample(11, 80, True)]
    values[1]["data"]["attack_strength"] = 0.2
    assert analyze(values)["attack_strength_samples"] == [{"tick": 11, "value": 0.2}]


def test_combat_reference_requests_interaction_preparation_and_wall(
    tmp_path, monkeypatch
):
    requests = []

    class FakeBridge:
        def __init__(self, output):
            self.run_id = "test"
            self.sequence = 0
            self.events = [dict(sample(1, 80, True), type="reference_sample")]

        def synchronize(self):
            pass

        def send(self, op, **fields):
            self.sequence += 1
            requests.append({"op": op, **fields})
            return {"sequence": self.sequence}

        def acknowledge(self, *args):
            pass

        def wait(self, predicate, timeout):
            assert predicate({"state": "reference_complete"})

        def drain(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(reference, "ROOT", tmp_path)
    monkeypatch.setattr(reference, "Bridge", FakeBridge)
    result = reference.record_reference("Human", 5, combat=True)
    assert result["status"] == "recorded"
    assert result["combat_wall"] is True
    assert requests[0] == {
        "op": "reference_start",
        "player": "Human",
        "seconds": 5,
        "prepare_interactions": True,
        "prepare_combat": True,
    }
    assert requests[-1]["op"] == "reference_stop"
