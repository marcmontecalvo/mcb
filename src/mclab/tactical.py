"""Bounded visible-target tactical baseline, not learned motor competence.

Candidates are fixed; targeting uses only currently visible policy facts. The
game/motor loop never waits for inference. Each accepted commitment lasts at
most 40 ticks/two seconds and is revalidated while a replacement is pending.
"""

import copy
import math
import queue
import threading
import time
from dataclasses import asdict

from mclab.backends import BackendError, ChoiceRequest

PROFILE = "bounded-visible-target-tactical-v1"
CANDIDATES = {
    "stop": "Release controls",
    "forward": "Walk forward briefly if visible terrain supports safe movement",
    "turn_left": "Turn left 30 degrees once",
    "turn_right": "Turn right 30 degrees once",
    "aim_log": "Aim once at the nearest currently visible oak log",
    "mine": "Hold mining at the currently aimed oak log, renewed by new choices",
    "collect": "Walk briefly toward the nearest currently visible oak log item",
}


def summary(observation):
    """Explicit allowlist: no evaluator information or episode identities."""
    body = observation["body"]
    return {
        "tick": observation["tick"],
        "body": {key: copy.deepcopy(body[key]) for key in ("position", "yaw", "pitch")},
        "ray": {
            key: copy.deepcopy(body.get("ray", {})[key])
            for key in ("block", "position")
            if key in body.get("ray", {})
        },
        "visible_logs": [
            list(block["position"])
            for block in observation.get("visible_blocks", [])
            if block["block"] == "minecraft:oak_log"
        ],
        "visible_drops": [
            {key: copy.deepcopy(entity[key]) for key in ("uuid", "position")}
            for entity in observation.get("visible_entities", [])
            if entity.get("item") == "minecraft:oak_log"
        ],
        "forward_safe": safe_forward(observation),
    }


def nearest(values, position):
    return min(
        values,
        key=lambda value: sum(
            (a - b) ** 2 for a, b in zip(value, position, strict=True)
        ),
        default=None,
    )


def aim(position, body):
    dx, dz = position[0] - body["position"][0], position[2] - body["position"][2]
    yaw = math.degrees(math.atan2(-dx, dz))
    pitch = -math.degrees(
        math.atan2(position[1] - body["position"][1] - 1.62, math.hypot(dx, dz))
    )
    return {
        "yaw_delta": max(-45, min(45, (yaw - body["yaw"] + 180) % 360 - 180)),
        "pitch_delta": max(-45, min(45, pitch - body["pitch"])),
    }


def safe_forward(observation):
    """Conservative flat-ground swept footprint: unknown or stale means stop."""
    body = observation["body"]
    px, py, pz = body["position"]
    yaw = math.radians(body["yaw"])
    cells = {
        tuple(block["position"]): block
        for block in observation.get("visible_blocks", [])
        if 0 <= observation["tick"] - block.get("observed_tick", -100) <= 5
    }
    # Include three scheduling ticks plus five held ticks at ordinary walking
    # speed, body width, and intermediate points. Corners/edges fail closed.
    for distance in (0, 0.25, 0.5, 0.8, 1.1, 1.4, 1.7, 2.0):
        x, z = px - math.sin(yaw) * distance, pz + math.cos(yaw) * distance
        for ox in (-0.3, 0.3):
            for oz in (-0.3, 0.3):
                cx, cz = math.floor(x + ox), math.floor(z + oz)
                floor_y = math.floor(py - 0.01)
                support = cells.get((cx, floor_y, cz))
                if not support or support.get("fluid") != "minecraft:empty":
                    return False
                local_x, local_z = x + ox - cx, z + oz - cz
                if not any(
                    box[0] <= local_x <= box[3]
                    and box[2] <= local_z <= box[5]
                    and abs(floor_y + box[4] - py) < 0.02
                    for box in support.get("collision_boxes", [])
                ):
                    return False
                for cy in range(math.floor(py), math.floor(py + 1.8) + 1):
                    cell = cells.get((cx, cy, cz))
                    if (
                        cell is None
                        or cell.get("fluid") != "minecraft:empty"
                        or cell.get("collision_boxes")
                    ):
                        return False
    return True


class TacticalController:
    """One inference at a time; bounded commitments revalidate every motor tick."""

    def __init__(self, backend):
        if backend.deadline > 1:
            raise ValueError("Tactical profile requires deadline <=1 second")
        self.backend = backend
        self.results = queue.Queue(maxsize=1)
        self.events = []
        self.worker = None
        self.last_admission = float("-inf")
        self.active = None
        self.closed = False

    def _request(self, observation):
        state = summary(observation)
        started = time.monotonic()
        exchange_start = len(self.backend.exchanges)
        self.last_admission = started
        self.events.append(("tactical_request", {"profile": PROFILE, "state": state}))

        def work():
            try:
                decision = self.backend.decide(
                    ChoiceRequest(
                        state,
                        CANDIDATES,
                        "Collect one oak log using only visible information. "
                        "When no log or drop is visible, explore: move forward "
                        "when forward_safe is true, otherwise turn to seek safe "
                        "visible space. An unseen goal is not a reason to stop. "
                        "When a log is visible, aim at it, approach safely until "
                        "the ray hits it, then keep choosing mine until it breaks. "
                        "Collect a visible log drop after breaking. Stop only "
                        "when movement is unsafe and no useful turn or mining "
                        "action is available, or collection is complete. "
                        "Choose one bounded behavior. Return only compact JSON "
                        '{"choice":"ID"} with no whitespace or explanation.',
                    )
                )
                result = {"decision": asdict(decision)}
            except Exception as error:
                result = {"error": type(error).__name__}
                # HttpBackend errors deliberately omit raw provider bodies and
                # redact echoes. Custom adapters without a redactor retain only
                # the exception type, never arbitrary exception text.
                redact = getattr(self.backend, "_redact", None)
                if isinstance(error, BackendError) and callable(redact):
                    result["reason"] = redact(str(error))
            result.update(
                state=state,
                started=started,
                exchanges=copy.deepcopy(self.backend.exchanges[exchange_start:]),
            )
            self.results.put(result)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def decide(self, observation):
        if self.closed:
            raise BackendError("Tactical controller is closed")
        if observation.get("sensor_profile") != "local-visibility-v1":
            raise ValueError("Unsupported tactical sensor")
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            result = None
        if result is not None:
            self.events.append(("tactical_response", result))
            self.worker = None
            if "error" in result:
                self.active = None
                raise BackendError(
                    "Tactical inference failed: "
                    + result.get("reason", result["error"])
                )
            age = observation["tick"] - result["state"]["tick"]
            if not 0 <= age <= 40 or time.monotonic() - result["started"] > 2:
                self.active = None
                raise BackendError("Expired tactical decision discarded")
            self.active = {
                "choice": result["decision"]["choice"],
                "state": result["state"],
                "tick": observation["tick"],
                "accepted": time.monotonic(),
            }
        if self.worker is None and time.monotonic() - self.last_admission >= 0.5:
            self._request(observation)
        if self.active is not None:
            choice = self.active["choice"]
            duration = 40 if choice == "mine" else 10
            if (
                observation["tick"] - self.active["tick"] < duration
                and time.monotonic() - self.active["accepted"] <= 2
            ):
                motor = self._motor(observation)
                if motor is not None:
                    return motor
            self.active = None
        return {}

    def _motor(self, observation):
        choice = self.active["choice"]
        state, current = self.active["state"], summary(observation)
        body = observation["body"]
        if choice == "stop":
            return {}
        if choice == "forward":
            return {"forward": 1} if safe_forward(observation) else None
        if choice in {"turn_left", "turn_right"}:
            if observation["tick"] != self.active["tick"]:
                return {}
            return {"yaw_delta": -30 if choice == "turn_left" else 30}
        if choice in {"aim_log", "mine"}:
            target = nearest(state["visible_logs"], state["body"]["position"])
            if target is None or target not in current["visible_logs"]:
                return None
            if choice == "aim_log":
                if observation["tick"] != self.active["tick"]:
                    return {}
                return aim([p + 0.5 for p in target], body)
            ray = current["ray"]
            if ray.get("block") != "minecraft:oak_log":
                return None
            if list(ray.get("position", [])) != target:
                return None
            return {"mine": True}
        if choice == "collect":
            candidates = {item["uuid"] for item in state["visible_drops"]}
            drops = [
                item["position"]
                for item in current["visible_drops"]
                if item["uuid"] in candidates
            ]
            target = nearest(drops, body["position"])
            if target is None:
                return None
            motor = aim(target, body)
            if abs(motor["yaw_delta"]) < 5 and safe_forward(observation):
                motor["forward"] = 1
            return motor
        raise BackendError("Unknown tactical choice")

    def close(self):
        self.closed = True
        self.active = None
        self.backend.close()
        if self.worker is not None:
            self.worker.join(timeout=1.1)
            if self.worker.is_alive():
                raise BackendError("Tactical inference worker did not stop")
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            return
        self.events.append(("tactical_response_discarded_on_close", result))
