"""Motor control loops: observe, decide, send controls. No model is called here.

`lockstep-three-tick-v1` (legacy, unchanged): one command at a time, each applied
three ticks after its observation; wait for its ack before observing again.

`dev-pipelined-v1` (development): decide on every new observation tick without
waiting for earlier acks, at most `max_in_flight` commands outstanding. Each
command still applies three ticks after its observation, lasts five ticks and
then expires. A later applied command replaces the body controls, so newer
commands supersede older ones. Late or stale acks are discarded, never retried.

Relative turns compound across in-flight commands, so the controller sees the
predicted orientation at apply time (observed yaw/pitch plus unapplied deltas).
Controllers that are not `pipeline_safe` (executors that must see the result of
each interaction) get a barrier instead: no new decision until earlier commands
are acknowledged and an observation at or after their apply tick has arrived.
"""

import copy
import time

from mclab.backends import BackendError
from mclab.execution import ControllerFailure, policy_state, terminal_event
from mclab.protocolproof import controls

LOCKSTEP = "lockstep-three-tick-v1"
PIPELINED = "dev-pipelined-v1"
NATIVE = "dev-native-route-v1"
PROFILES = (LOCKSTEP, PIPELINED, NATIVE)
LEAD_TICKS = 3
DURATION_TICKS = 5
STALE_LIMIT = 3
ACK_TIMEOUT_S = 5
POLL_S = 0.004
TIMING_REJECTIONS = {"late_or_distant_action", "stale_or_mistimed_action"}
# A decision with any of these must be seen to land before the next decision:
# a queued second jump/pulse could fire after the first one already landed.
INTERACTIONS = ("jump", "mine", "attack_pulse", "use_pulse", "use_hold")
# The body died or the episode ended in the same tick; the terminal event is
# about to arrive and is the true outcome (pipelined profile only).
ENDED_REJECTIONS = {"body_not_registered", "episode_not_running"}
ENDED_WAIT_S = 2
ACK_TYPES = {"applied", "rejected", "rejected_at_execution"}


def record_selector(client, controller):
    for kind, payload in controller.drain_events():
        client.record(kind, payload)


def _command(tick, index, decision):
    apply_tick = tick + LEAD_TICKS
    return controls(
        tick,
        f"action-{index}",
        duration=DURATION_TICKS,
        intended_apply_tick=apply_tick,
        expires_tick=apply_tick + DURATION_TICKS,
        **decision,
    )


def _fail(client, error):
    """Record, release held controls and end the attempt; never fall back."""
    client.last_controller_error = str(error)
    client.record(
        "controller_error",
        {
            "error": str(error),
            "category": "timing"
            if isinstance(error, TimeoutError)
            or "deadline" in str(error).lower()
            or any(
                reason in str(error)
                for reason in (*TIMING_REJECTIONS, "stale_queued_request")
            )
            else "decision",
        },
    )
    if terminal_event(client) is None:
        # Release also clears every scheduled command in the mod.
        release = client.send("release")
        client.acknowledge(release, "received")
        end = client.send("course_end", reason="controller_execution_error")
        client.acknowledge(end, "received")
        client.wait(lambda _: terminal_event(client) is not None)
    return terminal_event(client)


def _running(client, observation):
    """The terminal event, or None while the episode is still running."""
    terminal = terminal_event(client)
    if terminal is not None:
        return terminal
    if observation["state"] == "episode_terminal":
        client.wait(lambda _: terminal_event(client) is not None, timeout=2)
        return terminal_event(client)
    if observation["state"] != "running":
        raise RuntimeError("Policy admission closed before terminal result")
    return None


def drive(
    client, controller, wall_timeout=90, *, trace_processing=False, profile=LOCKSTEP
):
    """Renew motor commands independently of inference; never retry old commands."""
    if profile == NATIVE:
        if trace_processing:
            raise ValueError("Processing traces are recorded only in lockstep")
        from mclab.native_driver import drive_native

        return drive_native(client, controller, wall_timeout)
    if profile == PIPELINED:
        if trace_processing:
            raise ValueError("Processing traces are recorded only in lockstep")
        return drive_pipelined(client, controller, wall_timeout)
    if profile != LOCKSTEP:
        raise ValueError("Unknown control profile")
    deadline = time.monotonic() + wall_timeout
    action_index = discarded = 0
    client.last_controller_error = None
    try:
        while time.monotonic() < deadline:
            if trace_processing:
                began = time.perf_counter_ns()
                cpu_began = time.thread_time_ns()
            observation = policy_state(client)
            terminal = _running(client, observation)
            if terminal is not None:
                return terminal
            if trace_processing:
                observed = time.perf_counter_ns()
                cpu_observed = time.thread_time_ns()
            try:
                decision = controller.decide(observation)
            finally:
                record_selector(client, controller)
            action_index += 1
            tick = observation["tick"]
            if trace_processing:
                decided = time.perf_counter_ns()
                cpu_decided = time.thread_time_ns()
            request = client.send(
                "controls", motor=True, controls=_command(tick, action_index, decision)
            )
            if trace_processing:
                submitted = time.perf_counter_ns()
            client.wait(
                lambda _, sequence=request["sequence"]: terminal_event(client)
                is not None
                or any(
                    e["type"] in ACK_TYPES
                    and int(e["data"].get("request_sequence", -1)) == sequence
                    for e in client.events
                ),
                timeout=ACK_TIMEOUT_S,
            )
            if trace_processing:
                acknowledged = time.perf_counter_ns()
                client.record(
                    "control_processing",
                    {
                        "tick": tick,
                        "request_sequence": request["sequence"],
                        "clock": "perf_counter_ns",
                        "cpu_clock": "thread_time_ns",
                        "observation_ms": (observed - began) / 1_000_000,
                        "observation_cpu_ms": (cpu_observed - cpu_began) / 1_000_000,
                        "decision_record_ms": (decided - observed) / 1_000_000,
                        "decision_record_cpu_ms": (cpu_decided - cpu_observed)
                        / 1_000_000,
                        "before_send_ms": (decided - began) / 1_000_000,
                        "send_http_ms": (submitted - decided) / 1_000_000,
                        "ack_wait_ms": (acknowledged - submitted) / 1_000_000,
                    },
                )
            rejection = next(
                (
                    e
                    for e in reversed(client.events)
                    if e["type"] in {"rejected", "rejected_at_execution"}
                    and int(e["data"].get("request_sequence", -1))
                    == request["sequence"]
                ),
                None,
            )
            if rejection and terminal_event(client) is None:
                reason = rejection["data"]["reason"]
                if reason in TIMING_REJECTIONS:
                    discarded += 1
                    client.record(
                        "discarded_stale_action",
                        {
                            "request_sequence": request["sequence"],
                            "reason": reason,
                            "count": discarded,
                            "limit": STALE_LIMIT,
                        },
                    )
                    if discarded <= STALE_LIMIT:
                        release = client.send("release")
                        client.acknowledge(release, "received")
                        continue
                raise ControllerFailure("Controller action rejected: " + reason)
        raise ControllerFailure("Controller wall deadline exceeded")
    except (BackendError, ControllerFailure, TimeoutError) as error:
        return _fail(client, error)


def predicted(observation, in_flight):
    """Observation with yaw/pitch as they will be once unapplied turns land.

    A command applied at tick T is already in the observation of tick T.
    """
    tick = observation["tick"]
    pending = [turn for apply, *turn in in_flight.values() if apply > tick]
    if not pending:
        return observation
    body = dict(observation["body"])
    body["yaw"] = body["yaw"] + sum(yaw for yaw, _ in pending)
    body["pitch"] = max(-90.0, min(90.0, body["pitch"] + sum(p for _, p in pending)))
    # Shallow copy keeps the observation type (e.g. ExpandedTerrain's cell index).
    result = copy.copy(observation)
    result["body"] = body
    return result


def _settle(client, in_flight, scanned, stats):
    limit = stats["discard_limit"]
    """Consume new acks; discard late/stale ones, fail on any other rejection."""
    events = client.events
    for event in events[scanned:]:
        if event["type"] not in ACK_TYPES:
            continue
        sequence = int(event["data"].get("request_sequence", -1))
        if in_flight.pop(sequence, None) is None or event["type"] == "applied":
            continue
        reason = event["data"].get("reason")
        if reason in ENDED_REJECTIONS:
            client.record(
                "rejection_at_episode_end",
                {"request_sequence": sequence, "reason": reason},
            )
            # TimeoutError (no terminal event) still fails the attempt.
            client.wait(
                lambda _: terminal_event(client) is not None, timeout=ENDED_WAIT_S
            )
            return len(events)
        if reason not in TIMING_REJECTIONS:
            raise ControllerFailure(f"Controller action rejected: {reason}")
        stats["discarded"] += 1
        client.record(
            "discarded_stale_action",
            {
                "request_sequence": sequence,
                "reason": reason,
                "count": stats["discarded"],
                "limit": limit,
                "retried": False,
            },
        )
        if stats["discarded"] > limit:
            raise ControllerFailure(f"Controller action rejected: {reason}")
    return len(events)


def drive_pipelined(client, controller, wall_timeout=90, *, max_in_flight=3):
    if type(max_in_flight) is not int or not 1 <= max_in_flight <= LEAD_TICKS:
        # More than LEAD_TICKS would need two commands for one apply tick.
        raise ValueError("In-flight bound must be between 1 and the lead ticks")
    deadline = time.monotonic() + wall_timeout
    in_flight = {}  # request sequence -> (apply tick, yaw delta, pitch delta)
    sent_at = {}
    stats = {
        "profile": PIPELINED,
        "max_in_flight": max_in_flight,
        "decisions": 0,
        "barrier_decisions": 0,
        "discarded": 0,
        # One server hiccup can make every in-flight command late at once.
        "discard_limit": STALE_LIMIT * max_in_flight,
        "first_tick": None,
        "last_tick": None,
        "peak_in_flight": 0,
    }
    scanned = len(client.events)
    last_tick = barrier_tick = None
    client.last_controller_error = None
    try:
        while time.monotonic() < deadline:
            # Cheap tick poll (also drains events). Fetch, decode and record the
            # full policy observation only once per new tick.
            server_tick = client.state()["tick"]
            terminal = terminal_event(client)
            if terminal is not None:
                return terminal
            scanned = _settle(client, in_flight, scanned, stats)
            for sequence in [s for s in sent_at if s not in in_flight]:
                del sent_at[sequence]
            if sent_at and time.monotonic() - min(sent_at.values()) > ACK_TIMEOUT_S:
                raise TimeoutError("Control acknowledgement exceeded its deadline")
            if (
                last_tick is not None
                and server_tick <= last_tick
                or len(in_flight) >= max_in_flight
                or barrier_tick is not None
                and (in_flight or server_tick < barrier_tick)
            ):
                time.sleep(POLL_S)
                continue
            observation = policy_state(client)
            terminal = _running(client, observation)
            if terminal is not None:
                return terminal
            tick = observation["tick"]
            if last_tick is not None and tick <= last_tick:
                continue  # The snapshot has not caught up with /state yet.
            if barrier_tick is not None and tick < barrier_tick:
                continue
            barrier_tick = None
            try:
                decision = controller.decide(predicted(observation, in_flight))
            finally:
                record_selector(client, controller)
            stats["decisions"] += 1
            request = client.send(
                "controls",
                motor=True,
                controls=_command(tick, stats["decisions"], decision),
            )
            in_flight[request["sequence"]] = (
                tick + LEAD_TICKS,
                decision.get("yaw_delta", 0),
                decision.get("pitch_delta", 0),
            )
            sent_at[request["sequence"]] = time.monotonic()
            stats["peak_in_flight"] = max(stats["peak_in_flight"], len(in_flight))
            if stats["first_tick"] is None:
                stats["first_tick"] = tick
            stats["last_tick"] = last_tick = tick
            if not getattr(controller, "pipeline_safe", False) or any(
                decision.get(name) for name in INTERACTIONS
            ):
                stats["barrier_decisions"] += 1
                barrier_tick = tick + LEAD_TICKS
        raise ControllerFailure("Controller wall deadline exceeded")
    except (BackendError, ControllerFailure, TimeoutError) as error:
        return _fail(client, error)
    finally:
        client.control_loop_summary = stats
        client.record("control_loop_summary", stats)
