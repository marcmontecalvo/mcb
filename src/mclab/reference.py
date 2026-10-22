"""Bounded real-player reference capture; input labels come from client input state."""

from datetime import UTC, datetime

from mclab.bridge import Bridge
from mclab.paths import ROOT, write_json


def record_reference(player: str, seconds: int, interactions: bool = False) -> dict:
    if not 5 <= seconds <= 120:
        raise ValueError("Reference capture must last 5 to 120 seconds")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/references" / stamp
    client = Bridge(destination / "events.jsonl")
    result = {
        "schema_version": 1,
        "scored": False,
        "player": player,
        "run_id": client.run_id,
        "seconds": seconds,
        "alignment": "server-observed buttons, client timestamp/network delay unknown",
        "recording": str(destination / "events.jsonl"),
    }
    try:
        client.synchronize()
        request = client.send(
            "reference_start",
            player=player,
            seconds=seconds,
            prepare_interactions=interactions,
        )
        client.acknowledge(request, "reference_started")
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


def analyze_interactions(events: list[dict]) -> dict:
    starts, uses = {}, {}
    mining, placements, attacks = [], [], []
    attack = None
    for event in events:
        value = event["data"]
        kind = event["type"]
        if kind == "dig_packet":
            position = tuple(value["position"])
            if value["action"] == "START_DESTROY_BLOCK":
                starts[position] = event["tick"]
            elif value["action"] == "ABORT_DESTROY_BLOCK":
                starts.pop(position, None)
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
            attack = value
        elif (
            kind == "attack_result"
            and attack
            and attack.get("uuid") == value.get("uuid")
        ):
            attacks.append(
                {
                    "health_decrement": attack["health"] - value["health"],
                    "within_entity_range": attack["within_entity_range"],
                }
            )
            attack = None
    samples = [e["data"] for e in events if e["type"] == "reference_sample"]
    return {
        "mining": mining,
        "placements": placements,
        "attacks": attacks,
        "block_reach_attributes": sorted(
            {s["block_reach"] for s in samples if "block_reach" in s}
        ),
        "entity_reach_attributes": sorted(
            {s["entity_reach"] for s in samples if "entity_reach" in s}
        ),
    }


def analyze(samples: list[dict]) -> dict:
    """Report comparable segments without declaring complete C0 qualification."""
    jumps = []
    start_y = None
    peak_y = None
    walks = []
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
        if i >= 20:
            segment = samples[i - 20 : i + 1]
            if event["tick"] - segment[0]["tick"] == 20 and all(
                e["data"]["on_ground"]
                and e["data"]["buttons"]["forward"]
                and not any(
                    e["data"]["buttons"][key]
                    for key in ["backward", "left", "right", "jump", "sneak", "sprint"]
                )
                and abs(e["data"]["yaw"] - value["yaw"]) < 1
                for e in segment
            ):
                a, b = segment[0]["data"]["position"], value["position"]
                walks.append(((a[0] - b[0]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5)
    return {
        "jump_peak_heights": jumps,
        "walk_20_tick_distances": walks,
        "G1": "not_qualified_by_this_capture",
        "caveats": (
            "Client/server timing uncertainty; collisions, terrain and "
            "sample gaps require review"
        ),
    }
