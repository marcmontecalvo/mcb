"""Summarize recorded development navigation timing without promoting qualification."""

import json


def percentiles(values):
    values = sorted(values)
    return {
        "samples": len(values),
        **{
            name: values[int((len(values) - 1) * fraction)] if values else None
            for name, fraction in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
        },
        "max": values[-1] if values else None,
    }


def summarize(summary, events):
    motor, processing, acquisition, mspt, http, payload_bytes = [], [], [], [], [], []
    wire_bytes, decoding, encoding = [], [], []
    last_decode = {}
    decode_clocks = set()
    control = {}
    motor_cpu = []
    seen = set()
    stale = 0
    controller_errors = set()
    timing_errors = set()
    for event in events:
        kind, payload = event["type"], event["payload"]
        if kind == "navigation_tick":
            motor.append(payload["motor_ms"])
            if "motor_cpu_ms" in payload:
                motor_cpu.append(payload["motor_cpu_ms"])
            key = (event.get("episode_id"), payload["tick"])
            if key in last_decode:
                processing.append(payload["motor_ms"] + last_decode.pop(key))
        elif kind == "control_processing":
            if payload.get("clock") == "perf_counter_ns":
                for name, value in payload.items():
                    if name.endswith("_ms"):
                        control.setdefault(name, []).append(value)
        elif kind == "policy_observation":
            key = (event.get("episode_id"), payload["tick"])
            if "terrain_decode_ms" in payload:
                clock = payload.get("terrain_decode_clock", "unspecified")
                decode_clocks.add(clock)
                if clock == "perf_counter_ns":
                    last_decode[key] = payload["terrain_decode_ms"]
                decoding.append(payload["terrain_decode_ms"])
        elif kind == "discarded_stale_action":
            stale += 1
        elif kind == "controller_error":
            episode = event.get("episode_id")
            controller_errors.add(episode)
            if payload.get(
                "category"
            ) == "timing" or "stale_queued_request" in payload.get("error", ""):
                timing_errors.add(episode)
        elif kind == "resource_sample":
            http.append(payload["policy_http_latency_ms"])
            if payload.get("policy_wire_bytes") is not None:
                wire_bytes.append(payload["policy_wire_bytes"])
        elif kind == "server_event" and payload.get("type") == "policy_sample":
            sample = payload["data"]
            if not payload.get("episode_id", "").startswith("hill-"):
                continue
            resources = sample.get("resources", {})
            if "average_mspt" in resources:
                mspt.append(resources["average_mspt"])
            coverage = sample.get("awareness", {}).get("coverage", {})
            key = (payload.get("episode_id"), coverage.get("sample_tick"))
            if sample.get("map_update") and key not in seen:
                seen.add(key)
                if "sample_ms" in coverage:
                    acquisition.append(coverage["sample_ms"])
                if "terrain_sections" in sample:
                    encoding.append(sample["terrain_sections"]["encoding_ms"])
                payload_bytes.append(
                    len(json.dumps(sample, separators=(",", ":")).encode())
                )
    return {
        "schema_version": 1,
        "development_only": True,
        "full_sprint_acceptance": False,
        "expected_outcome": summary.get("expected_outcome", "arrival"),
        "configured_refusals_verified": sum(
            a.get("refusal_verified") is True for a in summary["attempts"]
        ),
        "bridges_verified": sum(
            a.get("bridge_verified") is True for a in summary["attempts"]
        ),
        "staircases_verified": sum(
            a.get("staircase_verified") is True for a in summary["attempts"]
        ),
        "pillars_verified": sum(
            a.get("pillar_verified") is True for a in summary["attempts"]
        ),
        "gaps_verified": sum(
            a.get("gap_verified") is True for a in summary["attempts"]
        ),
        "water_verified": sum(
            a.get("water_verified") is True for a in summary["attempts"]
        ),
        "cohort_status": summary["status"],
        "lab_restored": summary.get("lab_restored", False),
        "attempts_recorded": len(summary["attempts"]),
        "routes_reached": sum(a["status"] == "success" for a in summary["attempts"]),
        "invalid_attempts": sum(a["status"] == "invalid" for a in summary["attempts"]),
        "discarded_stale_actions": stale,
        "controller_error_attempts": len(controller_errors),
        "timing_error_attempts": len(timing_errors),
        "percentile_method": "sorted floor((n-1)*q), matching controller status",
        "motor_ms": percentiles(motor),
        "motor_cpu_ms": percentiles(motor_cpu),
        "control_processing": {k: percentiles(v) for k, v in control.items()},
        "decode_plus_motor_ms": percentiles(processing),
        "decode_clocks": sorted(decode_clocks),
        "terrain_decode_ms": percentiles(decoding),
        "terrain_encode_ms_off_server_thread": percentiles(encoding),
        "native_sample_ms": percentiles(acquisition),
        "server_rolling_average_mspt": percentiles(mspt),
        "policy_http_ms": percentiles(http),
        "policy_wire_bytes": percentiles(wire_bytes),
        "recorded_map_sample_json_bytes": percentiles(payload_bytes),
        "limits": [
            "Native sample_ms measures terrain sampling; "
            "not all snapshot/serialization work.",
            "MSPT values are server rolling averages, "
            "not individual tick costs or TPS.",
            "Payload sizes are canonical recorded JSON, "
            "not measured network wire bytes.",
            "Thread CPU samples are diagnostic only: Windows GetThreadTimes "
            "was observed to advance in 15.625 ms steps despite its clock resolution.",
            "before_send_ms includes observation HTTP/event/recording work; "
            "it is not server observation age or the decode-plus-motor target.",
            "These fixtures do not establish natural-terrain, "
            "client-access or sprint parity.",
        ],
    }
