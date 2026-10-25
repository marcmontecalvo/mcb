import threading
import time

import pytest

from mclab.backends import BackendError, Decision
from mclab.tactical import TacticalController, safe_forward, summary


def observation(tick=10):
    blocks = []
    for x in (-1, 0):
        for z in (-1, 0, 1, 2):
            for y in (-1, 0, 1):
                blocks.append(
                    {
                        "position": [x, y, z],
                        "block": "minecraft:stone" if y == -1 else "minecraft:air",
                        "collision_boxes": [[0, 0, 0, 1, 1, 1]] if y == -1 else [],
                        "fluid": "minecraft:empty",
                        "observed_tick": tick,
                    }
                )
    blocks.append({"position": [0, 0, 3], "block": "minecraft:oak_log"})
    return {
        "tick": tick,
        "sensor_profile": "local-visibility-v1",
        "body": {
            "position": [0, 0, 0],
            "yaw": 0,
            "pitch": 0,
            "ray": {"block": "minecraft:oak_log", "position": [0, 0, 3]},
            "evaluator_secret": "hidden",
        },
        "visible_blocks": blocks,
        "visible_entities": [],
        "run_id": "private-run",
        "target": "evaluator-only",
    }


class Backend:
    deadline = 1

    def __init__(self, choice="mine", blocking=False, fail=False):
        self.choice = choice
        self.ready = threading.Event()
        if not blocking:
            self.ready.set()
        self.exchanges = []
        self.requests = []
        self.fail = fail

    def decide(self, request):
        self.requests.append(request)
        self.ready.wait(1)
        if self.fail:
            raise BackendError("failure")
        return Decision(self.choice, {}, "test", 0, "test")

    def close(self):
        self.ready.set()


def complete(controller):
    controller.worker.join(1)
    assert not controller.worker.is_alive()


def test_inference_does_not_block_motor_and_single_outstanding():
    backend = Backend(blocking=True)
    controller = TacticalController(backend)
    start = time.monotonic()
    assert controller.decide(observation()) == {}
    assert controller.decide(observation(11)) == {}
    assert time.monotonic() - start < 0.1
    assert len(backend.requests) == 1
    controller.close()


def test_summary_allowlist_and_no_script_candidates():
    value = summary(observation())
    assert "evaluator" not in str(value)
    assert "private-run" not in str(value)
    controller = TacticalController(Backend())
    controller.decide(observation())
    complete(controller)
    request = controller.backend.requests[0]
    assert "complete_course" not in request.candidates
    assert "goto_log" not in request.candidates
    assert "explore" in request.instructions
    assert "forward_safe is true" in request.instructions
    assert "unseen goal is not a reason to stop" in request.instructions
    controller.close()


def test_stale_result_fails_closed():
    controller = TacticalController(Backend())
    controller.decide(observation())
    complete(controller)
    with pytest.raises(BackendError, match="Expired"):
        controller.decide(observation(51))
    assert controller.active is None
    assert controller.drain_events()[-1][0] == "tactical_response"
    controller.close()


def test_mine_revalidates_ray_and_target_without_moving():
    controller = TacticalController(Backend())
    controller.decide(observation())
    complete(controller)
    assert controller.decide(observation(11)) == {"mine": True}
    changed = observation(12)
    changed["body"]["ray"]["position"] = [0, 0, 4]
    assert controller.decide(changed) == {}
    controller.close()


def test_safe_movement_rejects_cliff_obstacle_unknown_and_old_cells():
    assert safe_forward(observation())
    cliff = observation()
    cliff["visible_blocks"] = [
        cell for cell in cliff["visible_blocks"] if cell["position"] != [0, -1, 1]
    ]
    assert not safe_forward(cliff)
    obstacle = observation()
    obstacle["visible_blocks"][4]["collision_boxes"] = [[0, 0, 0, 1, 1, 1]]
    assert not safe_forward(obstacle)
    stale = observation(20)
    for block in stale["visible_blocks"]:
        block["observed_tick"] = 10
    assert not safe_forward(stale)


def test_backend_failure_is_fatal_and_recorded():
    controller = TacticalController(Backend(fail=True))
    controller.decide(observation())
    complete(controller)
    with pytest.raises(BackendError, match="inference failed"):
        controller.decide(observation(11))
    assert controller.drain_events()[-1][1]["error"] == "BackendError"
    controller.close()


def test_previous_mine_remains_fresh_while_next_request_pending():
    backend = Backend()
    controller = TacticalController(backend)
    controller.decide(observation())
    complete(controller)
    assert controller.decide(observation(11)) == {"mine": True}
    controller.last_admission -= 0.6
    backend.ready.clear()
    assert controller.decide(observation(12)) == {"mine": True}
    assert controller.worker.is_alive()
    assert controller.decide(observation(52)) == {}
    controller.close()


def test_response_records_only_its_own_exchange_slice():
    class ExchangingBackend(Backend):
        def decide(self, request):
            self.exchanges.append({"request": len(self.requests)})
            return super().decide(request)

    backend = ExchangingBackend()
    backend.exchanges.append({"historical": True})
    controller = TacticalController(backend)
    controller.decide(observation())
    complete(controller)
    controller.decide(observation(11))
    response = controller.drain_events()[-1][1]
    assert response["exchanges"] == [{"request": 0}]
    backend.exchanges[-1]["changed"] = True
    assert response["exchanges"] == [{"request": 0}]
    controller.close()


def test_sanitized_backend_failure_reason_is_preserved():
    class RedactingBackend(Backend):
        def decide(self, request):
            raise BackendError("deadline exceeded secret")

        def _redact(self, value):
            return value.replace("secret", "[REDACTED]")

    controller = TacticalController(RedactingBackend())
    controller.decide(observation())
    complete(controller)
    with pytest.raises(BackendError, match="deadline exceeded \\[REDACTED\\]"):
        controller.decide(observation(11))
    response = controller.drain_events()[-1][1]
    assert response["reason"] == "deadline exceeded [REDACTED]"
    assert "secret" not in str(response)
    controller.close()


def test_close_retains_cancelled_inflight_result():
    class CancellingBackend(Backend):
        def decide(self, request):
            self.ready.wait(1)
            self.exchanges.append({"status": "deadline_or_cancelled"})
            raise BackendError("closed")

    controller = TacticalController(CancellingBackend(blocking=True))
    controller.decide(observation())
    controller.close()
    kind, result = controller.drain_events()[-1]
    assert kind == "tactical_response_discarded_on_close"
    assert result["error"] == "BackendError"
    assert result["exchanges"] == [{"status": "deadline_or_cancelled"}]
