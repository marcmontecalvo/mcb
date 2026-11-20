"""Attach one body planner to any development motor profile."""

import base64
import hashlib
import json
import time
import urllib.error
import zlib

from mclab import plan_sets
from mclab.plan_sets import PlanSetStore
from mclab.planner_map_transport import PlannerMapCoordinator


def _send(client, op, **fields):
    """Send; a 429 (body inbox full) means not admitted, so retry after a tick."""
    for attempt in range(40):
        try:
            return client.send(op, **fields)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 39:
                raise
            time.sleep(0.05)


def _send_and_ack(client, op, expected_kind, max_attempts=4, **fields):
    for attempt in range(max_attempts):
        request = _send(client, op, **fields)
        try:
            return client.acknowledge(request, expected_kind)
        except RuntimeError as err:
            if "stale_queued_request" in str(err) and attempt < max_attempts - 1:
                continue
            raise


def _plan_sets(client):
    """This client's upload record for the body it drives now (a new body has none)."""
    identity = (
        getattr(client, "run_id", None),
        getattr(client, "episode_id", None),
        getattr(client, "body_id", None),
        getattr(client, "planner_map_epoch", 0),
    )
    store = getattr(client, "plan_set_store", None)
    if store is None or store.identity != identity:
        store = client.plan_set_store = PlanSetStore(identity)
    return store


def _upload_set(client, set_id, raw):
    try:
        for frame in plan_sets.frames(set_id, raw):
            _send_and_ack(client, "planner_set", "planner_set_accepted", **frame)
    except Exception as error:
        try:
            _send_and_ack(client, "planner_set", "planner_set_accepted", kind="abort")
        except Exception as cleanup_error:
            error.add_note(f"Plan set cleanup failed: {cleanup_error}")
        raise


def submit_plan(client, payload):
    """Send one plan request; large edit sets go by reference (mclab.plan_sets).

    A body that does not hold a referenced set refuses the plan (plan_set_unknown,
    e.g. after a respawn): the sets are uploaded again and the plan resent once.
    """
    store = _plan_sets(client)
    for attempt in range(2):
        request = store.hoist(
            payload, lambda set_id, raw: _upload_set(client, set_id, raw)
        )
        try:
            _submit(client, request)
            return
        except RuntimeError as error:
            if "plan_set_unknown" not in str(error) or attempt:
                raise
            store.forget()


def _submit(client, payload):
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) <= 12_000:
        try:
            _send_and_ack(client, "plan_route", "plan_accepted", **payload)
        except Exception as error:
            try:
                _send_and_ack(
                    client, "cancel_plan", "plan_cancelled", plan_id=payload["plan_id"]
                )
            except Exception as cleanup_error:
                error.add_note(f"Plan cancellation failed: {cleanup_error}")
            raise
        return
    encoded = base64.b64encode(zlib.compress(raw)).decode("ascii")
    plan_id = payload["plan_id"]

    def frame(**fields):
        _send_and_ack(
            client,
            "planner_request",
            "planner_request_accepted",
            plan_id=plan_id,
            **fields,
        )

    try:
        frame(
            kind="begin",
            encoded_length=len(encoded),
            raw_length=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        for offset in range(0, len(encoded), 10_000):
            frame(kind="part", offset=offset, data=encoded[offset : offset + 10_000])
        _send_and_ack(
            client, "plan_route", "plan_accepted", plan_id=plan_id, plan_ref=plan_id
        )
    except Exception as error:
        try:
            frame(kind="abort")
        except Exception as cleanup_error:
            error.add_note(f"Plan request cleanup failed: {cleanup_error}")
        try:
            _send_and_ack(client, "cancel_plan", "plan_cancelled", plan_id=plan_id)
        except Exception as cleanup_error:
            error.add_note(f"Plan cancellation failed: {cleanup_error}")
        raise


def attach(client, controller):
    def send_map(frame):
        event = _send_and_ack(client, "planner_map", "planner_map_accepted", **frame)
        return event["data"]["generation"]

    def send_maps(frames):
        # A stale rejection is not retried here: a later frame may already be
        # applied, so the transfer aborts and resends the generation instead.
        requests = [_send(client, "planner_map", **frame) for frame in frames]
        return [
            client.acknowledge(request, "planner_map_accepted")["data"]["generation"]
            for request in requests
        ]

    def cancel_plan(plan_id):
        _send_and_ack(client, "cancel_plan", "plan_cancelled", plan_id=plan_id)

    identity = (
        client.run_id,
        client.episode_id,
        client.body_id,
        getattr(client, "planner_map_epoch", 0),
    )
    if getattr(client, "planner_map_identity", None) != identity:
        old = getattr(client, "planner_map_coordinator", None)
        if old is not None:
            old.close()
        client.planner_map_coordinator = PlannerMapCoordinator(send_map, send_maps)
        client.planner_map_identity = identity
    controller.submit_plan = lambda payload: submit_plan(client, payload)
    controller.cancel_plan = cancel_plan
    controller.planner_map = client.planner_map_coordinator
    policy = controller.terrain_policy
    try:
        _send_and_ack(
            client,
            "route_policy",
            "route_policy_accepted",
            use_doors=bool(policy and policy.use_doors),
            climb_vines=bool(policy is None or policy.climb_vines),
        )
    except Exception as error:
        try:
            _send_and_ack(
                client,
                "route_policy",
                "route_policy_accepted",
                use_doors=False,
                climb_vines=True,
            )
        except Exception as cleanup_error:
            error.add_note(f"Route policy cleanup failed: {cleanup_error}")
        controller.planner_map = None
        controller.submit_plan = None
        controller.cancel_plan = None
        raise
    controller.planner_policy_client = client


def detach(controller):
    from mclab.body_search import BodySearch

    failures = []
    for name in ("search", "prefetch", "terrain_search"):
        search = getattr(controller, name, None)
        if isinstance(search, BodySearch):
            try:
                search.cancel()
            except Exception as error:
                failures.append(error)
    coordinator = getattr(controller, "planner_map", None)
    if coordinator is not None:
        try:
            coordinator.cancel_active()
        except Exception as error:
            failures.append(error)
        finally:
            controller.planner_map = None
    client = getattr(controller, "planner_policy_client", None)
    if client is not None:
        try:
            # The mod accepts route_policy only for a ready body, and a spawn
            # resets the policy anyway: a body that died mid-drive has nothing to
            # restore. Sending it raised RuntimeError that masked the death
            # (run 20261116T125602216319Z).
            if client.state(heartbeat=False)["state"] == "ready":
                _send_and_ack(
                    client,
                    "route_policy",
                    "route_policy_accepted",
                    use_doors=False,
                    climb_vines=True,
                )
        except Exception as error:
            failures.append(error)
        finally:
            controller.planner_policy_client = None
    if failures:
        for error in failures[1:]:
            failures[0].add_note(f"Additional planner cleanup failure: {error}")
        raise failures[0]
