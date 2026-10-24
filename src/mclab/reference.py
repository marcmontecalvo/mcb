"""Bounded real-player reference capture; input labels come from client input state."""

from datetime import UTC, datetime
from math import hypot

from mclab.bridge import Bridge
from mclab.paths import ROOT, write_json
from mclab.recorder import arena_guard

MECHANICS = ("collision", "step", "ledge", "fall")


def reference_plan(mechanic: str) -> dict:
    instructions = {
        "collision": "Hold W only into the wall, then release. Do not jump or turn.",
        "step": "Hold W only to walk over the slab, then release. Do not jump.",
        "ledge": (
            "Hold Shift+W toward the gap, then release. Keep sneak held until stopped."
        ),
        "fall": (
            "Release all keys before setup; let the five-block fall land naturally."
        ),
    }
    if mechanic not in instructions:
        raise ValueError("Unknown reference mechanic")
    return {
        "mechanic": mechanic,
        "scored": False,
        "setup": (
            "Rebuilds the bounded C0 development fixture, restores health/food to 20, "
            "and relocates the named human facing south. Inventory is preserved. "
            "Requires ordinary Overworld Survival, no armor/effects, and no other "
            "human inside the fixture. The geometry remains after capture."
        ),
        "instructions": instructions[mechanic],
        "comparison": (
            "Review against the existing fake-player proof; "
            "never advances G1 automatically"
        ),
    }


@arena_guard
def record_reference(
    player: str,
    seconds: int,
    interactions: bool = False,
    combat: bool = False,
    mechanic: str = "none",
) -> dict:
    if not 5 <= seconds <= 120:
        raise ValueError("Reference capture must last 5 to 120 seconds")
    guide = None if mechanic == "none" else reference_plan(mechanic)
    if guide and (interactions or combat):
        raise ValueError(
            "Named mechanics cannot be combined with interaction/combat preparation"
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/references" / stamp
    client = Bridge(destination / "events.jsonl")
    result = {
        "schema_version": 1,
        "scored": False,
        "player": player,
        "run_id": client.run_id,
        "seconds": seconds,
        "prepare_interactions": interactions or combat,
        "combat_wall": combat,
        "reference_guide": guide,
        "alignment": "server-observed buttons, client timestamp/network delay unknown",
        "recording": str(destination / "events.jsonl"),
    }
    try:
        client.synchronize()
        request = client.send(
            "reference_start",
            player=player,
            seconds=seconds,
            prepare_interactions=interactions or combat,
            prepare_combat=combat,
            **({"reference_mechanic": mechanic} if guide else {}),
        )
        started = client.acknowledge(request, "reference_started")
        setup = started["data"].get("setup", {}) if started else {}
        result["setup"] = setup
        if guide and (
            setup.get("version") != "human-reference-geometry-v1"
            or setup.get("mechanic") != mechanic
        ):
            raise RuntimeError(
                "Installed mod did not acknowledge the named reference fixture"
            )
        client.wait(
            lambda s: s["state"] in {"reference_complete", "reference_invalid"},
            timeout=seconds * 2 + 10,
        )
        client.drain()
        samples = [e for e in client.events if e["type"] == "reference_sample"]
        invalid = [e for e in client.events if e["type"] == "reference_invalid"]
        result.update(
            status="invalid" if invalid or not samples else "recorded",
            samples=len(samples),
            analysis=analyze(samples),
            interactions=analyze_interactions(client.events),
            fixture_measurements=measure_fixture(samples, setup) if guide else {},
        )
    except Exception as error:
        result.update(status="invalid", error=str(error))
    finally:
        try:
            client.send("reference_stop")
            client.acknowledge({"sequence": client.sequence}, "received")
        except Exception as error:
            result["cleanup_error"] = str(error)
        client.close()
        write_json(destination / "summary.json", result)
    return result


def measure_fixture(samples: list[dict], setup: dict) -> dict:
    """Retain observations for review; measurements never assert body equivalence."""
    if not samples:
        return {"status": "no_samples", "mechanic": setup["mechanic"]}
    values = [e["data"] for e in samples]
    base_y = setup["origin"][1]
    grounded = [v["position"][1] for v in values if v["on_ground"]]
    forward = [v for v in values if v["buttons"]["forward"]]
    landing = None
    airborne = False
    for event in samples:
        value = event["data"]
        if not value["on_ground"] and value["position"][1] > base_y + 0.1:
            airborne = True
        if airborne and value["on_ground"] and value["position"][1] <= base_y + 0.05:
            landing = {
                "tick": event["tick"],
                "health": value["health"],
                "position": value["position"],
            }
            break
    return {
        "status": "review_required",
        "mechanic": setup["mechanic"],
        "position_bounds": {
            axis: [
                min(v["position"][i] for v in values),
                max(v["position"][i] for v in values),
            ]
            for i, axis in enumerate(("x", "y", "z"))
        },
        "grounded_height_above_platform": (
            [min(grounded) - base_y, max(grounded) - base_y] if grounded else []
        ),
        "maximum_z_with_forward_input": max(v["position"][2] for v in forward)
        if forward
        else None,
        "sneak_forward_samples": sum(v["buttons"]["sneak"] for v in forward),
        "jump_input_samples": sum(v["buttons"]["jump"] for v in values),
        "horizontal_collision_samples": sum(
            v.get("horizontal_collision", False) for v in values
        ),
        "health_range": [
            min(v["health"] for v in values),
            max(v["health"] for v in values),
        ],
        "first_landing_after_airborne": landing,
        "contiguous_ticks": all(
            b["tick"] == a["tick"] + 1
            for a, b in zip(samples[:-1], samples[1:], strict=True)
        ),
        "caveats": (
            "Other inputs, network timing, gaps and terrain require review; "
            "not a gate pass"
        ),
    }


def analyze_interactions(events: list[dict]) -> dict:
    starts, uses = {}, {}
    mining, placements, attacks = [], [], []
    interruptions = []
    attack = None
    last_attack_tick = None
    for event in events:
        value = event["data"]
        kind = event["type"]
        if kind == "dig_packet":
            position = tuple(value["position"])
            if value["action"] == "START_DESTROY_BLOCK":
                starts[position] = event["tick"]
            elif value["action"] == "ABORT_DESTROY_BLOCK":
                first = starts.pop(position, None)
                if first is not None:
                    interruptions.append(
                        {
                            "position": list(position),
                            "start_tick": first,
                            "abort_tick": event["tick"],
                            "ticks": event["tick"] - first,
                        }
                    )
        elif kind == "block_broken":
            first = starts.pop(tuple(value["position"]), None)
            if first is not None:
                mining.append({"block": value["block"], "ticks": event["tick"] - first})
        elif kind == "use_packet":
            uses[value["client_sequence"]] = value
        elif kind == "use_result":
            first = uses.pop(value["client_sequence"], None)
            if first:
                placements.append(
                    {
                        "item": first["held_item"],
                        "face": first["face"],
                        "inventory_decrement": first["held_count"]
                        - value["held_count"],
                        "adjacent_block": value["adjacent_block"],
                    }
                )
        elif kind == "attack_packet":
            attack = event if value.get("uuid") else None
        elif (
            kind == "attack_result"
            and attack
            and attack["data"]["uuid"] == value.get("uuid")
        ):
            tick = attack["tick"]
            attacks.append(
                {
                    "tick": tick,
                    "ticks_since_previous_recorded_attack": None
                    if last_attack_tick is None
                    else tick - last_attack_tick,
                    "health_decrement": attack["data"]["health"] - value["health"],
                    "within_entity_range": attack["data"]["within_entity_range"],
                }
            )
            if "attack_strength" in attack["data"] and "attack_strength" in value:
                attacks[-1].update(
                    attack_strength_before=attack["data"]["attack_strength"],
                    attack_strength_after=value["attack_strength"],
                )
            last_attack_tick = tick
            attack = None
    samples = [e["data"] for e in events if e["type"] == "reference_sample"]
    return {
        "mining": mining,
        "mining_interruptions": interruptions,
        "placements": placements,
        "attacks": attacks,
        "attack_timing_caveat": (
            "Damage and packet intervals alone cannot distinguish attack cooldown "
            "from target invulnerability, misses or other damage modifiers"
        ),
        "block_reach_attributes": sorted(
            {s["block_reach"] for s in samples if "block_reach" in s}
        ),
        "entity_reach_attributes": sorted(
            {s["entity_reach"] for s in samples if "entity_reach" in s}
        ),
    }


def _distance(a: dict, b: dict) -> float:
    first, last = a["data"]["position"], b["data"]["position"]
    return hypot(last[0] - first[0], last[2] - first[2])


def _buttons(event: dict) -> set[str]:
    return {key for key, pressed in event["data"]["buttons"].items() if pressed}


def _movement_windows(
    samples: list[dict], buttons: set[str], *, require_sprint_state: bool = False
) -> list[float]:
    distances = []
    for i in range(20, len(samples)):
        segment = samples[i - 20 : i + 1]
        yaw = segment[0]["data"]["yaw"]
        if all(
            e["tick"] == segment[0]["tick"] + offset
            and e["data"]["on_ground"]
            and _buttons(e) == buttons
            and (not require_sprint_state or e["data"].get("sprinting") is True)
            and abs((e["data"]["yaw"] - yaw + 180) % 360 - 180) < 1
            for offset, e in enumerate(segment)
        ):
            distances.append(_distance(segment[0], segment[-1]))
    return distances


def _release_segments(samples: list[dict]) -> list[dict]:
    segments = []
    current = None
    for previous, event in zip(samples[:-1], samples[1:], strict=True):
        contiguous = event["tick"] == previous["tick"] + 1
        grounded = previous["data"]["on_ground"] and event["data"]["on_ground"]
        if not contiguous or not grounded or _buttons(event):
            current = None
            continue
        if current is None:
            if not _buttons(previous) & {"forward", "backward", "left", "right"}:
                continue
            current = {
                "start_tick": event["tick"],
                "end_tick": event["tick"],
                "horizontal_path_distance": 0.0,
            }
            segments.append(current)
        current["end_tick"] = event["tick"]
        current["horizontal_path_distance"] += _distance(previous, event)
    return segments


def analyze(samples: list[dict]) -> dict:
    """Report comparable segments without declaring complete C0 qualification."""
    jumps = []
    start_y = None
    peak_y = None
    for i, event in enumerate(samples):
        value = event["data"]
        y = value["position"][1]
        if start_y is None and value["buttons"]["jump"] and i > 0:
            previous = samples[i - 1]["data"]
            if previous["on_ground"]:
                start_y = previous["position"][1]
                peak_y = y
        if start_y is not None:
            peak_y = max(peak_y, y)
            if value["on_ground"] and y <= start_y + 0.05:
                jumps.append(peak_y - start_y)
                start_y = peak_y = None
    return {
        "jump_peak_heights": jumps,
        "walk_20_tick_distances": _movement_windows(samples, {"forward"}),
        "strafe_20_tick_distances": {
            direction: _movement_windows(samples, {direction})
            for direction in ("left", "right")
        },
        "sprint_input_20_tick_distances": _movement_windows(
            samples, {"forward", "sprint"}
        ),
        "sprint_state_20_tick_distances": _movement_windows(
            samples, {"forward", "sprint"}, require_sprint_state=True
        ),
        "attack_strength_samples": [
            {"tick": e["tick"], "value": e["data"]["attack_strength"]}
            for e in samples
            if "attack_strength" in e["data"]
        ],
        "release_segments": _release_segments(samples),
        "G1": "not_qualified_by_this_capture",
        "caveats": (
            "Client/server timing uncertainty; collisions, terrain and "
            "sample gaps require review. Sprint button is not authoritative "
            "sprint state. "
            "Overlapping movement windows are not independent trials; "
            "release distances can include other forces and terrain effects"
        ),
    }
