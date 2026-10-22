"""Supplementary live G1 protocol checks; never advance a gate or score a task."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from mclab.bootstrap import sha256
from mclab.bridge import Bridge
from mclab.modbuild import source_hash
from mclab.paths import ROOT, read_json, write_json
from mclab.qualify import QualificationCheckFailure


def controls(tick: int, action_id: str, **changes) -> dict:
    value = {
        "action_id": action_id,
        "observation_tick": tick,
        "intended_apply_tick": tick + 3,
        "expires_tick": tick + 4,
        "duration": 1,
        "forward": 0,
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
    value.update(changes)
    return value


def qualify_protocol() -> dict:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/qualification" / stamp
    client = Bridge(destination / "events.jsonl")
    checks = []
    summary = {
        "schema_version": 1,
        "suite": "supplementary_protocol_v1",
        "run_id": client.run_id,
        "launch_id": client.launch_id,
        "scored": False,
        "G1": "in_progress_human_reference_deferred",
        "checks": checks,
        "qualifier_source_sha256": sha256(Path(__file__)),
        "mod_provenance": read_json(ROOT / "runtime/mod-build.json"),
    }

    def check(name, expected, observed, passed):
        result = dict(
            name=name, expected=expected, observed=observed, passed=bool(passed)
        )
        checks.append(result)
        client.record("check", result)
        if not passed:
            raise QualificationCheckFailure(f"Protocol check failed: {name}")

    def rejected(name, request, reason, *, motor=True, kind="rejected"):
        client.send_raw(request, motor=motor)
        event = client.acknowledge(request, kind)
        check(name, reason, event["data"]["reason"], event["data"]["reason"] == reason)

    try:
        if summary["mod_provenance"]["source_hash"] != source_hash():
            raise RuntimeError(
                "Installed mod differs from source; rebuild and install first"
            )
        for relative in (
            "src/mclab",
            "mod/src",
            "config/lab.json",
            "config/dependencies.lock.json",
            "config/qualification.json",
            "mod/build.gradle",
        ):
            source = ROOT / relative
            target = destination / "sources" / relative
            if source.is_dir():
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        client.synchronize()
        request = client.send("spawn", via_command=False)
        client.acknowledge(request, "spawn_requested")
        client.wait(lambda state: state["state"] == "ready", timeout=40)

        invalid = [
            ("forward_range", {"forward": 1.01}),
            ("strafe_range", {"strafe": -1.01}),
            ("yaw_range", {"yaw_delta": 45.01}),
            ("pitch_range", {"pitch_delta": -45.01}),
            ("hotbar_negative", {"hotbar_slot": -1}),
            ("hotbar_high", {"hotbar_slot": 9}),
            ("duration_zero", {"duration": 0}),
            ("duration_long", {"duration": 6}),
            ("expiry_mismatch", {"expires_tick": 0}),
            ("mine_attack_conflict", {"mine": True, "attack_pulse": True}),
            ("use_conflict", {"use_hold": True, "use_pulse": True}),
            ("sneak_sprint_conflict", {"sneak": True, "sprint": True}),
        ]
        for name, change in invalid:
            tick = client.state()["tick"]
            request = client.envelope(
                "controls", controls=controls(tick, name, **change)
            )
            rejected(name, request, "invalid_controls")

        tick = client.state()["tick"]
        request = client.envelope("controls", controls=controls(tick, "bad id"))
        rejected("action_id_syntax", request, "action_id")
        request = client.envelope("heartbeat")
        request["schema_version"] = 2
        rejected("schema_version", request, "schema_version", motor=False)
        request = client.envelope("despawn")
        rejected("policy_cannot_despawn", request, "controls_endpoint_only")
        request = client.envelope("arbitrary_console_command")
        rejected("admin_allowlist", request, "unknown_admin_operation", motor=False)

        tick = client.state()["tick"]
        request = client.envelope(
            "controls", controls=controls(tick, "future", intended_apply_tick=tick + 50)
        )
        request["controls"]["expires_tick"] = tick + 51
        rejected("future_action", request, "late_or_distant_action")
        tick = client.state()["tick"]
        request = client.envelope(
            "controls", controls=controls(tick, "past", intended_apply_tick=tick - 1)
        )
        request["controls"]["expires_tick"] = tick
        rejected("past_action", request, "late_or_distant_action")

        tick = client.state()["tick"]
        request = client.send("controls", motor=True, controls=controls(tick, "unique"))
        client.acknowledge(request, "applied")
        client.ticks(2)
        tick = client.state()["tick"]
        request = client.envelope("controls", controls=controls(tick, "unique"))
        rejected("duplicate_action_id", request, "duplicate_action_id")

        # Put movement and its cancellation in the inbox before the future apply tick.
        state = client.state()
        tick = state["tick"]
        before = state["body"]["position"]
        movement = client.send(
            "controls",
            motor=True,
            controls=controls(
                tick,
                "cancel-before-apply",
                intended_apply_tick=tick + 5,
                expires_tick=tick + 10,
                duration=5,
                forward=1,
            ),
        )
        release = client.send("release")
        client.acknowledge(movement, "received")
        client.acknowledge(release, "received")
        state = client.wait(lambda s: s["tick"] >= tick + 11)
        applied = [
            event
            for event in client.events
            if event["type"] == "applied"
            and event["data"]["action_id"] == "cancel-before-apply"
        ]
        rejected_movement = [
            event
            for event in client.events
            if event["type"] in {"rejected", "rejected_at_execution"}
            and int(event["data"].get("request_sequence", -1)) == movement["sequence"]
        ]
        check(
            "cancelled_action_was_admitted",
            "no rejection of the queued movement",
            rejected_movement,
            not rejected_movement,
        )
        check("release_clears_pending_action", [], applied, not applied)
        after = state["body"]["position"]
        delta = sum((a - b) ** 2 for a, b in zip(after, before, strict=True)) ** 0.5
        check(
            "cancelled_movement_no_displacement", "< 0.001 blocks", delta, delta < 0.001
        )

        old_request = client.envelope("release")
        request = client.send("despawn")
        client.acknowledge(request, "received")
        client.wait(lambda state: state["state"] == "idle")
        client.episode_id += "-next"
        client.body_id += "-next"
        request = client.send("spawn", via_command=False)
        client.acknowledge(request, "spawn_requested")
        client.wait(lambda state: state["state"] == "ready", timeout=40)
        rejected("retired_episode_release", old_request, "wrong_identity", motor=False)
        request = client.send("heartbeat")
        event = client.acknowledge(request, "received")
        check(
            "new_session_survives_old_request",
            client.episode_id,
            event["episode_id"],
            event["episode_id"] == client.episode_id,
        )
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
            client.wait(lambda state: state["state"] == "idle", timeout=5)
            client.drain()
        except Exception as error:
            summary["cleanup_error"] = str(error)
            summary["status"] = "invalid"
            summary["failure_category"] = "infrastructure"
        client.close()
        summary["requests"] = client.submitted
        summary["recording"] = str(destination / "events.jsonl")
        write_json(destination / "summary.json", summary)
    return summary
