import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mclab.backends import BackendError, Decision, HttpBackend
from mclab.tandem import TandemController


def observation(tick=10):
    return {
        "tick": tick,
        "sensor_profile": "local-visibility-v1",
        "body": {
            "position": [0, 0, 0],
            "yaw": 0,
            "pitch": 0,
            "ray": {"block": "minecraft:oak_log", "position": [0, 0, 3]},
            "evaluator_secret": "private",
        },
        "visible_blocks": [{"position": [0, 0, 3], "block": "minecraft:oak_log"}],
        "visible_entities": [],
        "target": "hidden-target",
    }


class Backend:
    def __init__(self, choice, *, blocked=False, fail=False, deadline=1):
        self.choice, self.fail, self.deadline = choice, fail, deadline
        self.ready, self.entered = threading.Event(), threading.Event()
        if not blocked:
            self.ready.set()
        self.exchanges, self.calls = [], []
        self.requests = 0
        self.closed = False

    def decide(self, request):
        self.requests += 1
        self.calls.append(request)
        self.entered.set()
        assert self.ready.wait(2)
        self.exchanges.append({"request": self.requests})
        if self.fail:
            raise BackendError("provider failed secret")
        return Decision(self.choice, {}, "test", 0, "fake")

    def _redact(self, value):
        return value.replace("secret", "[REDACTED]")

    def close(self):
        self.closed = True
        self.ready.set()

    def provenance(self):
        return {"requests": self.requests}


def controller(planner=None, tactical=None, **kwargs):
    return TandemController(
        planner or Backend("search"),
        tactical or Backend("mine"),
        planner_candidates={"search": "Find and collect the observed oak log"},
        planner_instructions="Choose the next task strategy.",
        **kwargs,
    )


def finish(worker):
    worker.join(1)
    assert not worker.is_alive()


def test_slow_planner_does_not_block_tactical_or_motor():
    planner, tactical = Backend("search", blocked=True), Backend("mine")
    c = controller(planner, tactical)
    try:
        assert c.decide(observation()) == {}
        assert planner.entered.wait(1) and tactical.entered.wait(1)
        finish(c.worker)
        assert c.decide(observation(11)) == {"mine": True}
        assert c.planner_worker.is_alive()
        assert planner.requests == tactical.requests == 1
    finally:
        c.close()


def test_plan_change_discards_old_tactical_reply_and_sends_new_context():
    planner, tactical = Backend("search", blocked=True), Backend("mine", blocked=True)
    c = controller(planner, tactical)
    try:
        c.decide(observation())
        assert planner.entered.wait(1) and tactical.entered.wait(1)
        planner.ready.set()
        finish(c.planner_worker)
        assert c.decide(observation(11)) == {}
        tactical.ready.set()
        finish(c.worker)
        c.last_admission -= 0.6
        assert c.decide(observation(12)) == {}
        finish(c.worker)
        assert c.decide(observation(13)) == {"mine": True}
        assert tactical.calls[-1].observation["planner_context"]["choice"] == "search"
        assert tactical.calls[-1].observation["planner_context"]["revision"] == 1
        assert any(
            k == "tactical_response_discarded_context" for k, _ in c.drain_events()
        )
    finally:
        c.close()


def test_both_roles_receive_only_permitted_facts():
    planner, tactical = Backend("search"), Backend("mine")
    c = controller(planner, tactical)
    try:
        c.decide(observation())
        finish(c.planner_worker)
        finish(c.worker)
        for backend in (planner, tactical):
            assert "hidden-target" not in str(backend.calls)
            assert "evaluator_secret" not in str(backend.calls)
    finally:
        c.close()


def test_planner_failure_is_recorded_redacted_and_fails_attempt_by_default():
    c = controller(Backend("search", fail=True))
    try:
        c.decide(observation())
        finish(c.planner_worker)
        with pytest.raises(BackendError, match="Planner inference failed"):
            c.decide(observation(11))
        assert c.active is None
        events = c.drain_events()
        assert "secret" not in str(events)
        assert any(k == "planner_response" for k, _ in events)
    finally:
        c.close()


def test_replan_discards_old_planner_result_without_overwriting_context():
    planner = Backend("search", blocked=True)
    c = controller(planner)
    try:
        c.decide(observation())
        assert planner.entered.wait(1)
        c.request_replan("new_observation")
        planner.ready.set()
        finish(c.planner_worker)
        assert c.decide(observation(11)) == {}
        assert c.plan is None
        assert any(
            k == "planner_response_discarded_context" for k, _ in c.drain_events()
        )
    finally:
        c.close()


def test_plan_expiry_invalidates_existing_motor_commitment():
    c = controller(plan_ttl_ticks=20)
    try:
        c.decide(observation())
        finish(c.planner_worker)
        finish(c.worker)
        c.decide(observation(11))
        c.last_admission -= 0.6
        c.decide(observation(12))
        finish(c.worker)
        assert c.decide(observation(13)) == {"mine": True}
        assert c.decide(observation(31)) == {}
        assert c.plan is None and c.active is None
    finally:
        c.close()


def test_close_closes_both_roles_and_retains_inflight_results():
    planner, tactical = Backend("search", blocked=True), Backend("mine", blocked=True)
    c = controller(planner, tactical)
    c.decide(observation())
    assert planner.entered.wait(1) and tactical.entered.wait(1)
    c.close()
    assert planner.closed and tactical.closed
    events = c.drain_events()
    assert any(k == "planner_response_discarded_on_close" for k, _ in events)
    assert any(k == "tactical_response_discarded_on_close" for k, _ in events)
    with pytest.raises(BackendError, match="closed"):
        c.decide(observation(11))


def test_tandem_requires_separate_backend_instances():
    backend = Backend("search")
    with pytest.raises(ValueError, match="separate"):
        controller(backend, backend)


@pytest.mark.parametrize(
    "kwargs", [{"planner_interval_s": 0}, {"plan_ttl_ticks": True}]
)
def test_invalid_tandem_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        controller(**kwargs)


def test_unavailable_planner_policy_is_explicit_and_recorded():
    c = controller(
        Backend("search", fail=True), planner_failure="continue_without_plan"
    )
    try:
        c.decide(observation())
        finish(c.planner_worker)
        finish(c.worker)
        assert c.decide(observation(11)) == {}
        c.last_admission -= 0.6
        c.decide(observation(12))
        finish(c.worker)
        assert c.decide(observation(13)) == {"mine": True}
        assert c.planner_failures == c.degraded_events == 1
        assert any(k == "planner_degraded" for k, _ in c.drain_events())
    finally:
        c.close()


def test_expired_planner_result_fails_without_admitting_a_new_context():
    c = controller(plan_ttl_ticks=20)
    try:
        c.decide(observation())
        finish(c.planner_worker)
        with pytest.raises(BackendError, match="ExpiredPlannerResult"):
            c.decide(observation(31))
        assert c.plan is None and c.active is None
    finally:
        c.close()


def test_unknown_strategy_is_rejected_and_reported():
    c = controller(Backend("unknown"))
    try:
        c.decide(observation())
        finish(c.planner_worker)
        with pytest.raises(BackendError, match="unknown strategy"):
            c.decide(observation(11))
        assert c.plan is None
    finally:
        c.close()


def test_planner_shutdown_error_does_not_leave_tactical_backend_open():
    class BrokenClose(Backend):
        def close(self):
            super().close()
            raise BackendError("planner shutdown failed")

    planner, tactical = BrokenClose("search"), Backend("mine")
    c = controller(planner, tactical)
    with pytest.raises(BackendError, match="planner shutdown failed"):
        c.close()
    assert planner.closed and tactical.closed


def test_two_http_model_routes_overlap_without_blocking_tactical_controls():
    planner_gate, planner_entered = threading.Event(), threading.Event()
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(payload)
            if payload["model"] == "planner-model":
                planner_entered.set()
                planner_gate.wait(2)
                choice = "search"
            else:
                choice = "mine"
            candidates = payload["questions"]["q"]["criteria"]
            body = json.dumps(
                {
                    "model": payload["model"],
                    "answers": {
                        "q": {
                            "choice": choice,
                            "probabilities": {
                                k: float(k == choice) for k in candidates
                            },
                        }
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    spec = {
        "name": "test-http",
        "kind": "systemone",
        "root": f"http://127.0.0.1:{server.server_port}",
        "request_limit": 2,
    }
    planner = HttpBackend({**spec, "model": "planner-model", "deadline_s": 2})
    tactical = HttpBackend({**spec, "model": "tactical-model", "deadline_s": 1})
    c = controller(planner, tactical)
    try:
        c.decide(observation())
        assert planner_entered.wait(1)
        finish(c.worker)
        assert c.decide(observation(11)) == {"mine": True}
        assert c.planner_worker.is_alive()
        assert {request["model"] for request in received} == {
            "planner-model",
            "tactical-model",
        }
    finally:
        planner_gate.set()
        finish(c.planner_worker)
        c.close()
        server.shutdown()
        server.server_close()
        server_thread.join(1)
