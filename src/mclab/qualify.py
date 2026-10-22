"""Bounded development proofs. Results never count as scored task completion."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from mclab.bootstrap import sha256
from mclab.bridge import Bridge
from mclab.modbuild import source_hash
from mclab.paths import ROOT, digest, read_json, write_json


class QualificationCheckFailure(RuntimeError):
    """An observed mechanics result fell outside the declared tolerance."""


def qualify_commands() -> dict:
    profile = read_json(ROOT / "config/qualification.json")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/qualification" / stamp
    client = Bridge(destination / "events.jsonl")
    checks = []
    summary = {
        "schema_version": 1,
        "run_id": client.run_id,
        "scored": False,
        "profile_hash": digest(profile),
        "checks": checks,
        "G1": "unqualified_pending_full_C0_and_real_player_reference",
        "launch_id": client.launch_id,
        "qualifier_source_sha256": sha256(Path(__file__)),
        "mod_provenance": read_json(ROOT / "runtime/mod-build.json"),
    }

    def check(name, expected, observed, passed):
        value = {
            "name": name,
            "expected": expected,
            "observed": observed,
            "passed": bool(passed),
        }
        checks.append(value)
        client.record("check", value)
        if not passed:
            raise QualificationCheckFailure(
                f"Failed qualification layer: {name}; evidence preserved"
            )

    def fixture(log=False, dirt=0, mechanic="flat"):
        previous = client.state()["tick"]
        payload = client.send("fixture", log=log, dirt=dirt, mechanic=mechanic)
        client.acknowledge(payload, "received")
        return client.wait(lambda s: s["tick"] > previous + 4 and s["state"] == "ready")

    def command(name, duration):
        payload = client.send("command_proof", command=name, duration=duration)
        client.acknowledge(payload, "command_acknowledged")
        return next(
            e
            for e in reversed(client.events)
            if e["type"] == "command_acknowledged"
            and e["data"]["request_sequence"] == payload["sequence"]
        )

    def count(state, item):
        return sum(s["count"] for s in state["body"]["inventory"] if s["item"] == item)

    try:
        if summary["mod_provenance"]["source_hash"] != source_hash():
            raise RuntimeError(
                "Installed mod differs from source; rebuild and install first"
            )
        # Preserve the exact uncommitted development inputs, using a fixed allowlist.
        archive = destination / "sources"
        for relative in [
            "src/mclab",
            "mod/src",
            "config/qualification.json",
            "config/lab.json",
            "config/dependencies.lock.json",
            "mod/build.gradle",
        ]:
            source = ROOT / relative
            target = archive / relative
            if source.is_dir():
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        client.synchronize()
        client.send("spawn", via_command=True)
        state = client.wait(
            lambda s: s["state"] == "ready", timeout=profile["spawn_timeout_seconds"]
        )
        check(
            "command_spawn_survival_empty",
            "registered, Survival, health/food 20, empty",
            state["body"],
            state["body"]["mode"] == "survival"
            and state["body"]["health"] == 20
            and state["body"]["food"] == 20
            and state["body"]["inventory"] == [],
        )
        # Complete the startup scan before this mechanics fixture changes any blocks.
        client.wait(lambda s: s["tick"] >= 230, timeout=20)
        start = fixture()
        ack = command("forward", 20)
        end = client.wait(lambda s: s["tick"] >= ack["tick"] + 25)
        distance = end["body"]["position"][2] - start["body"]["position"][2]
        low, high = profile["walk_20_ticks_blocks"]
        check("command_walk_20_ticks", [low, high], distance, low <= distance <= high)

        start = fixture()
        ack = command("left", 20)
        end = client.wait(lambda s: s["tick"] >= ack["tick"] + 25)
        distance = end["body"]["position"][0] - start["body"]["position"][0]
        check("command_strafe_20_ticks", [low, high], distance, low <= distance <= high)

        fixture()
        command("look_south", 5)
        state = client.ticks(2)
        check(
            "command_look_south",
            [0, 0],
            [state["body"]["yaw"], state["body"]["pitch"]],
            state["body"]["yaw"] == 0 and state["body"]["pitch"] == 0,
        )

        start = fixture()
        ack = command("jump", 5)
        end = client.wait(lambda s: s["tick"] >= ack["tick"] + 20)
        samples = [s for s in client.samples if ack["tick"] <= s["tick"] <= end["tick"]]
        peak = (
            max(s["body"]["position"][1] for s in samples)
            - start["body"]["position"][1]
        )
        low, high = profile["jump_peak_blocks"]
        check("command_jump_height", [low, high], peak, low <= peak <= high)

        fixture(log=True)
        command("look_target", 5)
        state = client.ticks(1)
        check(
            "command_aim_target",
            "minecraft:oak_log",
            state["body"]["ray"],
            state["body"]["ray"].get("block") == "minecraft:oak_log",
        )
        ack = command("mine", 200)
        broken = client.wait(
            lambda s: s["body"]["qualification_target_block"] == "minecraft:air",
            timeout=10,
        )
        elapsed = broken["tick"] - ack["tick"]
        low, high = profile["log_break_ticks"]
        check("command_mine_duration", [low, high], elapsed, low <= elapsed <= high)
        command("stop", 1)
        command("forward", 8)
        collected = client.wait(lambda s: count(s, "minecraft:oak_log") >= 1, timeout=5)
        check(
            "command_pickup",
            "one log in initially empty inventory",
            count(collected, "minecraft:oak_log"),
            count(collected, "minecraft:oak_log") == 1,
        )

        fixture(log=False, dirt=16)
        command("look_floor", 5)
        client.ticks(1)
        command("place", 1)
        placed = client.wait(
            lambda s: s["body"]["qualification_target_block"] == "minecraft:dirt"
        )
        check(
            "command_place_inventory_decrement",
            15,
            count(placed, "minecraft:dirt"),
            count(placed, "minecraft:dirt") == 15,
        )

        fixture()
        command("forward", 200)
        client.ticks(55, heartbeat=False)
        released = [
            e
            for e in client.events
            if e["type"] == "released" and e["data"]["reason"] == "watchdog"
        ]
        check(
            "command_disconnect_watchdog",
            "release without a heartbeat",
            len(released),
            bool(released),
        )

        client.send("despawn")
        client.wait(lambda s: s["state"] == "idle")
        client.episode_id += "-api"
        client.body_id += "-api"
        client.send("spawn", via_command=False)
        client.wait(lambda s: s["state"] == "ready", timeout=40)
        client.send("trace")
        state = fixture()
        tick = state["tick"]
        payload = client.send(
            "controls",
            motor=True,
            **{
                "controls": {
                    "action_id": "walk-five",
                    "observation_tick": tick,
                    "intended_apply_tick": tick + 3,
                    "expires_tick": tick + 8,
                    "duration": 5,
                    "forward": 1,
                    "strafe": 0,
                    "yaw_delta": 0,
                    "pitch_delta": 0,
                    "jump": False,
                    "sneak": False,
                    "sprint": False,
                    "mine": False,
                    "attack_pulse": False,
                    "use_hold": False,
                    "use_pulse": False,
                    "hotbar_slot": 0,
                }
            },
        )
        client.acknowledge(payload, "applied")
        client.ticks(12)
        applied = next(
            e
            for e in client.events
            if e["type"] == "applied" and e["data"]["action_id"] == "walk-five"
        )
        check(
            "api_exact_apply_tick",
            tick + 3,
            applied["data"]["actual_apply_tick"],
            applied["data"]["actual_apply_tick"] == tick + 3,
        )
        phases = [
            e["phase"]
            for e in client.events
            if e["type"] == "phase" and e["tick"] == tick + 3
        ]
        expected = [
            "fabric_start",
            "carpet_action_head",
            "carpet_action_tail",
            "fabric_end_observation",
        ]
        check("callback_phase_order", expected, phases, phases == expected)
        expiries = [
            e
            for e in client.events
            if e["type"] == "released" and e["tick"] == tick + 8
        ]
        check(
            "api_auto_expiry", tick + 8, [e["tick"] for e in expiries], bool(expiries)
        )
        raw = dict(payload)
        client.send_raw(raw, motor=True)
        event = client.acknowledge(raw, "rejected")
        check(
            "duplicate_sequence_rejected",
            "duplicate_or_out_of_order_sequence",
            event["data"]["reason"],
            event["data"]["reason"] == "duplicate_or_out_of_order_sequence",
        )
        raw = client.envelope("controls", controls=dict(payload["controls"]))
        raw["episode_id"] = "wrong-episode"
        client.send_raw(raw, motor=True)
        event = client.acknowledge(raw, "rejected")
        check(
            "wrong_episode_rejected",
            "wrong_identity",
            event["data"]["reason"],
            event["data"]["reason"] == "wrong_identity",
        )
        state = client.state()
        controls = dict(
            payload["controls"],
            action_id="stale",
            observation_tick=state["tick"] - 6,
            intended_apply_tick=state["tick"] + 3,
            expires_tick=state["tick"] + 8,
        )
        raw = client.send("controls", motor=True, controls=controls)
        client.wait(
            lambda _s: any(
                e["type"] == "rejected_at_execution"
                and e["data"]["action_id"] == "stale"
                for e in client.events
            )
        )
        check("stale_observation_rejected_at_execution", True, True, True)

        def motor(action_id, *, duration=5, delay=3, until_tick=None, **values):
            now = client.state()["tick"]
            apply_tick = now + delay
            if until_tick is not None:
                duration = min(duration, until_tick - apply_tick)
                if duration < 1:
                    return None
            controls = dict(
                payload["controls"],
                action_id=action_id,
                observation_tick=now,
                intended_apply_tick=apply_tick,
                duration=duration,
                expires_tick=apply_tick + duration,
                forward=0,
            )
            controls.update(values)
            request = client.send("controls", motor=True, controls=controls)
            event = client.acknowledge(request, "applied")
            return event["tick"]

        # Demonstrate that releasing held input does not freeze physical motion.
        start = fixture()
        applied_tick = motor("jump-api", jump=True)
        end = client.wait(lambda s: s["tick"] >= applied_tick + 20)
        samples = [
            s for s in client.samples if applied_tick <= s["tick"] <= end["tick"]
        ]
        peak = (
            max(s["body"]["position"][1] for s in samples)
            - start["body"]["position"][1]
        )
        low, high = profile["jump_peak_blocks"]
        check("api_jump_after_input_expiry", [low, high], peak, low <= peak <= high)

        fixture(log=True)
        motor("aim-api", pitch_delta=29.25)
        client.ticks(1)
        state = client.state()
        check(
            "api_bounded_look",
            "minecraft:oak_log",
            state["body"]["ray"],
            state["body"]["ray"].get("block") == "minecraft:oak_log",
        )
        first_mine = None
        for index in range(40):
            if client.state()["body"]["qualification_target_block"] == "minecraft:air":
                break
            applied_tick = motor(f"mine-api-{index}", mine=True)
            first_mine = first_mine or applied_tick
        broken = client.wait(
            lambda s: s["body"]["qualification_target_block"] == "minecraft:air"
        )
        elapsed = broken["tick"] - first_mine
        low, high = profile["log_break_ticks"]
        check(
            "api_renewed_mining_preserves_progress",
            [low, high],
            elapsed,
            low <= elapsed <= high,
        )
        # Ordinary log drops scatter; keep approaching within a fixed 20-tick
        # movement budget instead of assuming every drop reaches us after five ticks.
        for index in range(4):
            if count(client.state(), "minecraft:oak_log") == 1:
                break
            motor(f"pickup-api-{index}", forward=1)
            client.ticks(5)
        collected = client.wait(lambda s: count(s, "minecraft:oak_log") == 1)
        check("api_pickup", 1, count(collected, "minecraft:oak_log"), True)

        fixture(dirt=16)
        motor("place-api", duration=1, pitch_delta=39.2, use_pulse=True)
        placed = client.wait(
            lambda s: s["body"]["qualification_target_block"] == "minecraft:dirt"
        )
        check(
            "api_place_inventory_decrement",
            15,
            count(placed, "minecraft:dirt"),
            count(placed, "minecraft:dirt") == 15,
        )

        for field, wrong in [("body_id", "wrong-body"), ("run_id", "wrong-run")]:
            request = client.envelope("heartbeat")
            request[field] = wrong
            client.send_raw(request)
            event = client.acknowledge(request, "rejected")
            check(
                f"{field}_rejected",
                "wrong_identity",
                event["data"]["reason"],
                event["data"]["reason"] == "wrong_identity",
            )

        def drive(prefix, count=7, **values):
            first = motor(f"{prefix}-0", **values)
            stop_tick = first + count * 3
            for index in range(1, count):
                if (
                    motor(
                        f"{prefix}-{index}",
                        until_tick=None if values.get("sneak") else stop_tick,
                        **values,
                    )
                    is None
                ):
                    break
            if values.get("sneak"):
                # Stop forward while still holding sneak; expiry must not release
                # the edge guard with residual forward velocity.
                for index in range(3):
                    motor(f"{prefix}-settle-{index}", delay=2, sneak=True)
                    client.ticks(2)
                return client.state()
            return client.wait(lambda s: s["tick"] >= stop_tick + 4)

        fixture()
        motor("move-before-release", forward=1)
        before = client.state()
        request = client.send("release")
        client.acknowledge(request, "received")
        after = client.ticks(3)
        coast = after["body"]["position"][2] - before["body"]["position"][2]
        check(
            "release_preserves_inertia",
            "positive residual travel",
            coast,
            coast > 0.001,
        )

        fixture(mechanic="collision")
        state = drive("collision", forward=1)
        z = state["body"]["position"][2]
        check(
            "solid_collision",
            f"z <= {profile['collision_max_z']}",
            z,
            989.69 <= z <= profile["collision_max_z"],
        )

        start = fixture(mechanic="step")
        first_tick = start["tick"]
        state = drive("step", forward=1)
        peak = (
            max(
                s["body"]["position"][1]
                for s in client.samples
                if first_tick <= s["tick"] <= state["tick"]
            )
            - 80
        )
        low, high = profile["step_peak_blocks"]
        check("half_block_step", [low, high], peak, low <= peak <= high)

        start = fixture()
        state = drive("sprint", forward=1, sprint=True)
        distance = state["body"]["position"][2] - start["body"]["position"][2]
        low, high = profile["sprint_21_ticks_blocks"]
        check("sprint_21_ticks", [low, high], distance, low <= distance <= high)

        fixture(mechanic="ledge")
        state = drive("sneak-ledge", count=14, forward=1, sneak=True)
        position = state["body"]["position"]
        check(
            "ledge_sneaking",
            "remain at y=80, z <= edge+0.3",
            position,
            abs(position[1] - 80) < 0.001
            and 989.95 <= position[2] <= profile["ledge_max_z"],
        )

        state = fixture(mechanic="fall")
        health = state["body"]["health"]
        low, high = profile["fall_health"]
        check("five_block_fall_damage", [low, high], health, low <= health <= high)

        fixture(log=True)
        command("look_target", 5)
        command("mine", 20)
        partial = client.ticks(25)
        check(
            "mining_interruption_leaves_log",
            "minecraft:oak_log",
            partial["body"]["qualification_target_block"],
            partial["body"]["qualification_target_block"] == "minecraft:oak_log",
        )
        ack = command("mine", 200)
        broken = client.wait(
            lambda s: s["body"]["qualification_target_block"] == "minecraft:air"
        )
        duration = broken["tick"] - ack["tick"]
        low, high = profile["log_break_ticks"]
        check(
            "mining_interruption_resets_progress",
            [low, high],
            duration,
            low <= duration <= high,
        )

        fixture(mechanic="combat_near")
        motor("aim-cow", pitch_delta=25)
        state = drive("hold-mine-cow", mine=True)
        cow = state["body"]["qualification_entities"][0]
        check(
            "continuous_attack_does_not_hit_entity",
            10,
            cow["health"],
            cow["health"] == 10,
        )
        initial_z = cow["position"][2]
        motor("discrete-entity-attack", attack_pulse=True)
        hit = client.ticks(2)
        cow = hit["body"]["qualification_entities"][0]
        check("discrete_entity_attack", 9, cow["health"], cow["health"] == 9)
        check(
            "entity_knockback",
            "positive z displacement",
            cow["position"][2] - initial_z,
            cow["position"][2] - initial_z > 0.01,
        )
        initial_strength = hit["body"]["attack_strength"]
        recovered = client.ticks(10)["body"]["attack_strength"]
        check(
            "attack_cooldown",
            "reset then recover to 1",
            [initial_strength, recovered],
            initial_strength < 1 and recovered == 1,
        )

        fixture(mechanic="combat_far")
        motor("aim-far-cow", pitch_delta=13)
        at = motor("out-of-reach-attack", attack_pulse=True)
        state = client.ticks(2)
        cow = state["body"]["qualification_entities"][0]
        guarded = [
            e
            for e in client.events
            if e["type"] == "interaction_rejected" and e["tick"] == at
        ]
        check(
            "entity_reach_guard",
            "no damage beyond real-player reach",
            {"health": cow["health"], "guard_events": len(guarded)},
            cow["health"] == 10 and bool(guarded),
        )

        state = fixture()
        before = state["body"]["position"]
        request = client.send("knockback_proof")
        client.acknowledge(request, "received")
        client.send("release")
        knocked = client.ticks(5)
        delta = knocked["body"]["position"][2] - before[2]
        check(
            "body_knockback_survives_release",
            "positive z displacement",
            delta,
            delta > 0.1,
        )

        state = fixture()
        old_name = state["body"]["name"]
        client.send("death_proof")
        dead = client.wait(lambda s: s["state"] in {"dead", "disconnected_or_dead"})
        check(
            "death_disconnect",
            "dead or removed",
            dead["body"],
            not dead["body"].get("registered") or not dead["body"].get("alive"),
        )
        client.send("despawn")
        client.wait(lambda s: s["state"] == "idle")
        client.episode_id += "-replacement"
        client.body_id += "-replacement"
        client.send("spawn", via_command=False)
        fresh = client.wait(lambda s: s["state"] == "ready", timeout=40)
        check(
            "fresh_body_after_death",
            "new identity, empty inventory, full health",
            fresh["body"],
            fresh["body"]["name"] != old_name
            and fresh["body"]["inventory"] == []
            and fresh["body"]["health"] == 20,
        )
        fixture()
        summary["status"] = "passed_development_subset"
    except Exception as error:
        summary["status"] = (
            "failed" if isinstance(error, QualificationCheckFailure) else "invalid"
        )
        summary["failure_category"] = (
            "execution" if isinstance(error, QualificationCheckFailure) else "unknown"
        )
        summary["error"] = str(error)
        client.record("failure", {"error": str(error)})
    finally:
        try:
            client.send("despawn")
            client.wait(lambda s: s["state"] == "idle", timeout=5)
            client.drain()
        except Exception as error:
            summary["cleanup_error"] = str(error)
        client.close()
        summary["requests"] = client.submitted
        summary["recording"] = str(destination / "events.jsonl")
        write_json(destination / "summary.json", summary)
    return summary
