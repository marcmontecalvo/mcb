"""Optional two-backend C1 controller; tandem superiority is an experiment.

The first planner adapter selects a declared strategy through Choice. This is a
bounded planning baseline, not an unrestricted structured-plan implementation.
The existing single-model controller and motor remain independently usable.
"""

import copy
import math
import queue
import threading
import time
from dataclasses import asdict, dataclass

from mclab.backends import BackendError, ChoiceRequest, Decision
from mclab.tactical import TacticalController, summary

PROFILE = "optional-tandem-choice-planner-c1-v1"


class StalePlannerContext(BackendError):
    pass


@dataclass(frozen=True)
class _ContextDecision(Decision):
    context_revision: int


class _ContextBackend:
    def __init__(self, backend, context):
        self.backend, self.context = backend, context
        self.deadline = backend.deadline

    @property
    def exchanges(self):
        return self.backend.exchanges

    def _redact(self, value):
        redact = getattr(self.backend, "_redact", None)
        return redact(value) if callable(redact) else "Backend error"

    def decide(self, request):
        revision, plan = self.context()
        state = {**copy.deepcopy(request.observation), "planner_context": plan}
        decision = self.backend.decide(
            ChoiceRequest(state, request.candidates, request.instructions)
        )
        if revision != self.context()[0]:
            raise StalePlannerContext(
                "Planner context changed during tactical inference"
            )
        return _ContextDecision(**asdict(decision), context_revision=revision)

    def close(self):
        self.backend.close()


class TandemController(TacticalController):
    """Independent asynchronous lanes with explicit context invalidation.

    decide(), request_replan() and close() belong to the motor/orchestration
    thread. Worker threads perform only provider calls and publish results.
    """

    def __init__(
        self,
        planner_backend,
        tactical_backend,
        *,
        planner_candidates,
        planner_instructions,
        planner_interval_s=5.0,
        plan_ttl_ticks=200,
        planner_failure="fail_attempt",
    ):
        if planner_backend is tactical_backend:
            raise ValueError("Tandem roles require separate backend instances")
        if (
            type(planner_interval_s) not in {int, float}
            or not math.isfinite(planner_interval_s)
            or planner_interval_s < 0.5
            or type(plan_ttl_ticks) is not int
            or not 1 <= plan_ttl_ticks <= 1200
            or planner_failure not in {"fail_attempt", "continue_without_plan"}
            or not isinstance(planner_candidates, dict)
            or not isinstance(planner_instructions, str)
            or not planner_instructions
            or not 0 < planner_backend.deadline <= 10
        ):
            raise ValueError("Invalid tandem planner configuration")
        ChoiceRequest({}, planner_candidates, planner_instructions)
        self.planner_candidates = copy.deepcopy(planner_candidates)
        self.planner_instructions = planner_instructions
        self.planner_backend = planner_backend
        self.tactical_backend = tactical_backend
        self.planner_interval_s = planner_interval_s
        self.plan_ttl_ticks = plan_ttl_ticks
        self.planner_failure = planner_failure
        self.planner_results = queue.Queue(maxsize=1)
        self.planner_worker = None
        self.last_planner_admission = float("-inf")
        self.planning_epoch = 0
        self.context_revision = 0
        self.context_lock = threading.Lock()
        self.plan = None
        self.planner_failures = 0
        self.degraded_events = 0
        super().__init__(_ContextBackend(tactical_backend, self._context))

    def _context(self):
        with self.context_lock:
            return self.context_revision, copy.deepcopy(self.plan)

    def _invalidate(self):
        with self.context_lock:
            self.plan = None
            self.context_revision += 1
        self.planning_epoch += 1
        self.active = None

    def request_replan(self, reason):
        if self.closed:
            raise BackendError("Tandem controller is closed")
        if not isinstance(reason, str) or not reason:
            raise ValueError("Replanning requires a reason")
        self._invalidate()
        self.events.append(
            (
                "planner_context_invalidated",
                {"reason": reason, "epoch": self.planning_epoch},
            )
        )

    def _request_plan(self, observation):
        state = {**summary(observation), "active_plan": self._context()[1]}
        started = time.monotonic()
        epoch = self.planning_epoch
        exchange_start = len(self.planner_backend.exchanges)
        self.last_planner_admission = started
        self.events.append(
            ("planner_request", {"profile": PROFILE, "epoch": epoch, "state": state})
        )

        def work():
            try:
                decision = self.planner_backend.decide(
                    ChoiceRequest(
                        state, self.planner_candidates, self.planner_instructions
                    )
                )
                if decision.choice not in self.planner_candidates:
                    raise BackendError("Planner chose an unknown strategy")
                result = {"decision": asdict(decision)}
            except Exception as error:
                result = {"error": type(error).__name__}
                redact = getattr(self.planner_backend, "_redact", None)
                if isinstance(error, BackendError) and callable(redact):
                    result["reason"] = redact(str(error))
            result.update(
                epoch=epoch,
                state=state,
                elapsed_s=time.monotonic() - started,
                exchanges=copy.deepcopy(
                    self.planner_backend.exchanges[exchange_start:]
                ),
            )
            self.planner_results.put(result)

        self.planner_worker = threading.Thread(target=work, daemon=True)
        self.planner_worker.start()

    def _poll_plan(self, observation):
        try:
            result = self.planner_results.get_nowait()
        except queue.Empty:
            return
        self.planner_worker = None
        if result["epoch"] != self.planning_epoch:
            self.events.append(("planner_response_discarded_context", result))
            return
        self.events.append(("planner_response", result))
        age = observation["tick"] - result["state"]["tick"]
        if "error" not in result and (
            result["elapsed_s"] > self.planner_backend.deadline
            or not 0 <= age < self.plan_ttl_ticks
        ):
            result["error"] = "ExpiredPlannerResult"
            result["reason"] = (
                "ExpiredPlannerResult: planner deadline/state age exceeded"
            )
        if "error" in result:
            self.planner_failures += 1
            self._invalidate()
            if self.planner_failure == "fail_attempt":
                raise BackendError(
                    "Planner inference failed: " + result.get("reason", result["error"])
                )
            self.degraded_events += 1
            self.events.append(("planner_degraded", {"policy": self.planner_failure}))
            return
        choice = result["decision"]["choice"]
        with self.context_lock:
            self.context_revision += 1
            self.plan = {
                "choice": choice,
                "description": self.planner_candidates[choice],
                "revision": self.context_revision,
                "source_tick": result["state"]["tick"],
                "expires_tick": result["state"]["tick"] + self.plan_ttl_ticks,
            }
        self.active = None
        self.events.append(("planner_context_accepted", copy.deepcopy(self.plan)))

    def _discard_stale_tactical(self):
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            return
        revision = self._context()[0]
        if result.get("error") == "StalePlannerContext" or (
            "decision" in result
            and result["decision"].get("context_revision") != revision
        ):
            self.worker = None
            self.active = None
            self.events.append(("tactical_response_discarded_context", result))
        else:
            self.results.put_nowait(result)

    def decide(self, observation):
        if self.closed:
            raise BackendError("Tandem controller is closed")
        if observation.get("sensor_profile") != "local-visibility-v1":
            raise ValueError("Unsupported tandem sensor profile")
        if self.plan is not None and observation["tick"] >= self.plan["expires_tick"]:
            self.request_replan("plan_expired")
        self._poll_plan(observation)
        if (
            self.planner_worker is None
            and time.monotonic() - self.last_planner_admission
            >= self.planner_interval_s
        ):
            self._request_plan(observation)
        self._discard_stale_tactical()
        return super().decide(observation)

    def close(self):
        errors = []
        try:
            self.planner_backend.close()
        except Exception as error:
            errors.append(error)
        try:
            super().close()
        except Exception as error:
            errors.append(error)
        if self.planner_worker is not None:
            self.planner_worker.join(timeout=self.planner_backend.deadline + 0.1)
            if self.planner_worker.is_alive():
                errors.append(BackendError("Planner inference worker did not stop"))
        try:
            result = self.planner_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.events.append(("planner_response_discarded_on_close", result))
        if errors:
            raise errors[0]
