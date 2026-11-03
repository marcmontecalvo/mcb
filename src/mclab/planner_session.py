"""Attach one body planner to any development motor profile."""

import base64
import hashlib
import json
import zlib

from mclab.planner_map_transport import PlannerMapCoordinator


def submit_plan(client, payload):
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) <= 12_000:
        try:
            request = client.send("plan_route", **payload)
            client.acknowledge(request, "plan_accepted")
        except Exception as error:
            try:
                request = client.send("cancel_plan", plan_id=payload["plan_id"])
                client.acknowledge(request, "plan_cancelled")
            except Exception as cleanup_error:
                error.add_note(f"Plan cancellation failed: {cleanup_error}")
            raise
        return
    encoded = base64.b64encode(zlib.compress(raw)).decode("ascii")
    plan_id = payload["plan_id"]

    def frame(**fields):
        request = client.send("planner_request", plan_id=plan_id, **fields)
        client.acknowledge(request, "planner_request_accepted")

    try:
        frame(
            kind="begin",
            encoded_length=len(encoded),
            raw_length=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        for offset in range(0, len(encoded), 10_000):
            frame(kind="part", offset=offset, data=encoded[offset : offset + 10_000])
        request = client.send("plan_route", plan_id=plan_id, plan_ref=plan_id)
        client.acknowledge(request, "plan_accepted")
    except Exception as error:
        try:
            frame(kind="abort")
        except Exception as cleanup_error:
            error.add_note(f"Plan request cleanup failed: {cleanup_error}")
        try:
            request = client.send("cancel_plan", plan_id=plan_id)
            client.acknowledge(request, "plan_cancelled")
        except Exception as cleanup_error:
            error.add_note(f"Plan cancellation failed: {cleanup_error}")
        raise


def attach(client, controller):
    def send_map(frame):
        request = client.send("planner_map", **frame)
        event = client.acknowledge(request, "planner_map_accepted")
        return event["data"]["generation"]

    def cancel_plan(plan_id):
        request = client.send("cancel_plan", plan_id=plan_id)
        client.acknowledge(request, "plan_cancelled")

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
        client.planner_map_coordinator = PlannerMapCoordinator(send_map)
        client.planner_map_identity = identity
    controller.submit_plan = lambda payload: submit_plan(client, payload)
    controller.cancel_plan = cancel_plan
    controller.planner_map = client.planner_map_coordinator
    policy = controller.terrain_policy
    try:
        request = client.send(
            "route_policy",
            use_doors=bool(policy and policy.use_doors),
            climb_vines=bool(policy is None or policy.climb_vines),
        )
        client.acknowledge(request, "route_policy_accepted")
    except Exception as error:
        try:
            request = client.send("route_policy", use_doors=False, climb_vines=True)
            client.acknowledge(request, "route_policy_accepted")
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
            request = client.send("route_policy", use_doors=False, climb_vines=True)
            client.acknowledge(request, "route_policy_accepted")
        except Exception as error:
            failures.append(error)
        finally:
            controller.planner_policy_client = None
    if failures:
        for error in failures[1:]:
            failures[0].add_note(f"Additional planner cleanup failure: {error}")
        raise failures[0]
