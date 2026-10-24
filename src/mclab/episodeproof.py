"""Adversarial live C1 evaluator checks; intentional failures are unscored."""

import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab import gates
from mclab.bootstrap import sha256
from mclab.execution import RecordedBridge, terminal_event
from mclab.modbuild import source_hash
from mclab.paths import ROOT, read_json, write_json
from mclab.protocolproof import controls
from mclab.recorder import EpisodeRecorder, exclusive_arena


def qualify_episodes() -> dict:
    with exclusive_arena():
        return _qualify_episodes()


def _qualify_episodes() -> dict:
    profile = gates.require_initial_profile("C1")
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError(
            "Installed mod differs from source; rebuild and install first"
        )
    destination = (
        ROOT / "results/qualification" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    client = RecordedBridge(destination / "events.jsonl")
    client.budget = 2500
    recorder = EpisodeRecorder(ROOT / "results/episodes.sqlite3")
    configuration = {
        "suite": "episode_adversarial_v1",
        "scored": False,
        "mod_provenance": provenance,
    }
    recorder.begin_run(client.run_id, configuration)
    client.recorder = recorder
    summary = {
        "schema_version": 1,
        **configuration,
        "run_id": client.run_id,
        "launch_id": client.launch_id,
        "checks": [],
        "episodes": [],
        "recording": str(destination / "events.jsonl"),
        "qualifier_source_sha256": sha256(Path(__file__)),
    }
    active = None
    attempt = 0

    def check(name, observed, passed):
        value = {"name": name, "observed": observed, "passed": bool(passed)}
        summary["checks"].append(value)
        client.record("check", value)
        if not passed:
            raise RuntimeError(f"Adversarial check failed: {name}")

    def reset():
        nonlocal active, attempt
        attempt += 1
        client.episode_id = "adversarial-" + uuid4().hex[:12]
        client.body_id = "body-" + uuid4().hex[:12]
        recorder.begin(
            client.run_id,
            attempt,
            client.episode_id,
            client.body_id,
            destination / "events.jsonl",
        )
        active = client.episode_id
        request = client.send("course_reset", profile_id=profile["profile_id"])
        client.acknowledge(request, "course_reset_started", timeout=10)
        client.acknowledge(request, "course_reset_complete", timeout=90)

    def finish(status=None, reason=None):
        nonlocal active
        terminal = terminal_event(client)
        client.flush_recording()
        data = (
            terminal["data"]
            if terminal and status is None
            else {"status": status, "reason": reason}
        )
        recorder.finish(active, data["status"], data["reason"], terminal or data)
        summary["episodes"].append({"episode_id": active, **data})
        active = None
        return data

    def begin():
        request = client.send("course_begin")
        started = client.acknowledge(request, "course_started", timeout=15)
        client.wait(lambda state: state["state"] == "running")
        return started

    def reject(name, request, reason, *, motor=True, kind="rejected"):
        client.send_raw(request, motor=motor)
        event = client.acknowledge(request, kind)
        actual = event["data"]["reason"]
        check(name, actual, actual == reason)

    def stop():
        request = client.send("course_end")
        client.acknowledge(request, "received")
        client.wait(lambda _: terminal_event(client) is not None)
        return finish()

    try:
        for relative in ("src/mclab", "mod/src", "config/initial_body_profile.json"):
            source, target = ROOT / relative, destination / "sources" / relative
            if source.is_dir():
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        client.synchronize()
        reset()
        request = client.send("course_test", kind="sensor_check")
        verification = client.acknowledge(request, "course_sensor_checked", timeout=15)[
            "data"
        ]["verification"]
        for name, passed in verification["checks"].items():
            check("sensor_" + name, passed, passed)
        finish("invalid", "intentional_sensor_fixture_requires_reset")
        reset()
        tick = client.state()["tick"]
        reject(
            "policy_closed_until_begin",
            client.envelope("controls", controls=controls(tick, "before-begin")),
            "course_policy_admission_closed",
        )
        started = begin()
        check(
            "fixed_600_tick_budget",
            started["data"],
            started["data"]["deadline_tick"] - started["data"]["start_tick"] == 600,
        )
        policy = client.http("/policy")
        forbidden_keys = {
            "target",
            "spec_hash",
            "geometry",
            "drop_uuid",
            "safe_route",
            "target_broken",
            "picked_up",
        }
        check(
            "policy_has_no_evaluator_fields",
            sorted(policy),
            not forbidden_keys.intersection(policy)
            and not forbidden_keys.intersection(policy.get("episode", {})),
        )
        visible = policy["visible_blocks"]
        floor_y = int(policy["body"]["position"][1]) - 1
        check(
            "sensor_floor_occludes_buried_cells",
            {
                "visible_cells": len(visible),
                "minimum_y": min(b["position"][1] for b in visible),
            },
            bool(visible) and all(b["position"][1] >= floor_y for b in visible),
        )
        for name, changes in (
            ("reject_place_hold", {"use_hold": True}),
            ("reject_place_pulse", {"use_pulse": True}),
            ("reject_entity_attack", {"attack_pulse": True}),
            ("reject_hotbar", {"hotbar_slot": 1}),
        ):
            tick = client.state()["tick"]
            reject(
                name,
                client.envelope("controls", controls=controls(tick, name, **changes)),
                "course_forbidden_control",
            )
        tick = client.state()["tick"]
        wrong = client.envelope("controls", controls=controls(tick, "wrong-body"))
        wrong["body_id"] = "stale-body"
        reject("reject_stale_body", wrong, "wrong_identity")
        tick = client.state()["tick"]
        reject(
            "reject_stale_observation",
            client.envelope(
                "controls",
                controls=controls(tick, "old-state", observation_tick=tick - 20),
            ),
            "stale_or_mistimed_action",
            kind="rejected_at_execution",
        )
        request = client.envelope("fixture", log=True, dirt=16)
        reject(
            "reject_setup_during_episode",
            request,
            "course_policy_admission_closed",
            motor=False,
        )
        # A downward native mining attempt must never destroy the platform.
        tick = client.state()["tick"]
        request = client.send(
            "controls",
            motor=True,
            controls=controls(
                tick, "look-down", pitch_delta=45, duration=5, expires_tick=tick + 8
            ),
        )
        client.acknowledge(request, "applied")
        tick = client.state()["tick"]
        request = client.send(
            "controls",
            motor=True,
            controls=controls(
                tick,
                "look-down-again",
                pitch_delta=45,
                duration=5,
                expires_tick=tick + 8,
            ),
        )
        client.acknowledge(request, "applied")
        before = client.state()["body"]["position"]
        # Bare-hand stone needs roughly 150 ticks before destroyBlock is called;
        # renew long enough to exercise the actual mutation guard, not only aim.
        for index in range(80):
            tick = client.state()["tick"]
            request = client.send(
                "controls",
                motor=True,
                controls=controls(
                    tick,
                    f"illegal-mine-{index}",
                    mine=True,
                    duration=5,
                    expires_tick=tick + 8,
                ),
            )
            client.acknowledge(request, "applied")
        state = client.state()
        attempts = [
            e
            for e in client.events
            if e.get("episode_id") == active
            and e["type"] == "course_interaction_attempt"
            and e["data"].get("kind") == "break"
        ]
        check(
            "illegal_floor_break_rejected",
            {"attempts": len(attempts), "position": state["body"]["position"]},
            bool(attempts)
            and all(not e["data"]["allowed"] for e in attempts)
            and state["body"]["position"] == before,
        )
        outcome = stop()
        check(
            "cancel_is_failed_not_success",
            outcome,
            outcome["status"] == "failed" and not outcome["picked_up"],
        )

        reset()
        request = client.send("course_test", kind="planted_item")
        client.acknowledge(request, "course_test_prepared")
        request = client.send("course_begin")
        rejection = client.acknowledge(request, "rejected", timeout=15)
        check(
            "planted_log_cannot_begin_or_score",
            rejection["data"],
            "residual" in rejection["data"]["reason"]
            or "unexpected" in rejection["data"]["reason"],
        )
        finish("invalid", "intentional_planted_item")

        reset()
        request = client.send("course_test", kind="target_missing")
        client.acknowledge(request, "course_test_prepared")
        request = client.send("course_begin")
        rejection = client.acknowledge(request, "course_begin_failed", timeout=15)
        check(
            "changed_target_cannot_begin",
            rejection["data"],
            "mismatch" in rejection["data"]["reason"]
            or "target" in rejection["data"]["reason"],
        )
        finish("invalid", "intentional_target_mutation")

        reset()
        begin()
        client.wait(lambda _: terminal_event(client) is not None, timeout=45)
        outcome = finish()
        check(
            "tick_deadline_is_failed",
            outcome,
            outcome["status"] == "failed"
            and outcome["reason"] == "tick_limit"
            and outcome["end_tick"] == outcome["deadline_tick"],
        )

        reset()
        begin()
        client.wait(
            lambda _: terminal_event(client) is not None, timeout=6, heartbeat=False
        )
        outcome = finish()
        check(
            "disconnected_controller_is_failed",
            outcome,
            outcome["status"] == "failed"
            and outcome["reason"] == "controller_watchdog",
        )

        reset()
        begin()
        # Leave sideways so the designated log cannot act as a stop wall.
        for index in range(2):
            tick = client.state()["tick"]
            request = client.send(
                "controls",
                motor=True,
                controls=controls(
                    tick,
                    f"turn-to-edge-{index}",
                    yaw_delta=45,
                    duration=5,
                    expires_tick=tick + 8,
                ),
            )
            client.acknowledge(request, "applied")
        for index in range(60):
            tick = client.state()["tick"]
            if terminal_event(client):
                break
            request = client.send(
                "controls",
                motor=True,
                controls=controls(
                    tick,
                    f"leave-arena-{index}",
                    forward=1,
                    duration=5,
                    expires_tick=tick + 8,
                ),
            )
            client.wait(
                lambda _state, seq=request["sequence"]: terminal_event(client)
                is not None
                or any(
                    e["type"] == "applied"
                    and int(e["data"].get("request_sequence", -1)) == seq
                    for e in client.events
                )
            )
        client.wait(lambda _: terminal_event(client) is not None, timeout=15)
        outcome = finish()
        check(
            "bounds_or_pit_is_failed",
            outcome,
            outcome["status"] == "failed"
            and outcome["reason"] in {"out_of_bounds", "pit", "death_or_disconnect"},
        )
        summary["status"] = "passed_development_subset"
    except Exception as error:
        summary.update(status="invalid", error=str(error))
        if active:
            recorder.finish(
                active,
                "invalid",
                "qualifier_error",
                {
                    "error": str(error),
                    "observed_native_terminal": terminal_event(client),
                },
            )
            summary["episodes"].append(
                {"episode_id": active, "status": "invalid", "reason": "qualifier_error"}
            )
            active = None
    finally:
        try:
            request = client.send("despawn")
            client.acknowledge(request, "received")
            client.wait(lambda state: state["state"] == "idle")
        except Exception as error:
            summary.update(status="invalid", cleanup_error=str(error))
        summary["requests"] = client.submitted
        try:
            client.close()
        except Exception as error:
            summary.update(status="invalid", recording_error=str(error))
        finally:
            recorder.finish_run(client.run_id, summary["status"])
            recorder.close()
            write_json(destination / "summary.json", summary)
    return summary
