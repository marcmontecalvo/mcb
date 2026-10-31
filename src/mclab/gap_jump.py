"""Checked level gap edges and bounded ordinary run-up/jump/landing control."""

import math
from dataclasses import dataclass

from mclab.collision_geometry import clear_body, clear_column
from mclab.grid_cache import EMPTY, FULL, SAFE, classify
from mclab.navigation import rotation, standable


@dataclass(frozen=True)
class GapJumpPlan:
    source: tuple
    target: tuple
    width: int
    direction: tuple
    runway: tuple
    requires_sprint: bool


def prepare_gap(cells, source, target, policy):
    if len(source) != 3 or len(target) != 3 or source[1] != target[1]:
        return None
    if any(
        type(v) not in (int, float) or not math.isfinite(v) or int(v) != v
        for v in (*source, *target)
    ):
        return None
    sx, sy, sz = source
    tx, _, tz = target
    span = abs(tx - sx) + abs(tz - sz)
    if (tx != sx and tz != sz) or span not in (2, 3):
        return None
    width = int(span - 1)
    if width > policy.max_gap or width == 2 and not policy.allow_sprint:
        return None
    return gap_plan(cells, source, target, policy, span)


def gap_plan(cells, source, target, policy, span):
    """prepare_gap after validation: integral level source/target, span 2 or 3."""
    sx, sy, sz = source
    tx, _, tz = target
    width = int(span - 1)
    dx, dz = int((tx - sx) / span), int((tz - sz) / span)
    flags = getattr(cells, "cflags", None) or (lambda p: classify(cells.get(p)))
    if not flags((sx, sy - 1, sz)) & FULL or not flags((tx, sy - 1, tz)) & FULL:
        return None
    for offset in range(1, int(span)):
        if not flags((sx + dx * offset, sy - 1, sz + dz * offset)) & EMPTY:
            return None
    runway = (sx - dx, sy, sz - dz)
    for offset in range(-1, int(span) + 3):
        x, z = sx + dx * offset, sz + dz * offset
        if 0 < offset < span:
            if not (flags((x, sy - 1, z)) & EMPTY and flags((x, sy - 2, z)) & EMPTY):
                return None
        else:
            below = flags((x, sy - 1, z))
            if not (below & SAFE and below & FULL):
                return None
        if not clear_column(cells, x, z, sy, sy + 3.2):
            return None
        if not (0 < offset < span) and not standable(cells, (x, sy, z)):
            return None
    return GapJumpPlan(
        tuple(source), tuple(target), width, (dx, dz), runway, width == 2
    )


class GapJumpExecutor:
    def __init__(self, plan, policy):
        if (
            plan.width > policy.max_gap
            or plan.requires_sprint
            and not policy.allow_sprint
        ):
            raise ValueError("Gap jump exceeds movement permissions")
        self.plan, self.policy = plan, policy
        self.state, self.reason, self.phase = "active", "", "positioning"
        self.started = self.launch_tick = None
        self.air_seen = False

    def fail(self, reason):
        self.state, self.reason = "failed", reason
        return {}

    def decide(self, observation, fresh):
        if self.state != "active":
            return {}
        tick, body = observation["tick"], observation["body"]
        if self.started is None:
            self.started = tick
        if tick - self.started > 120:
            return self.fail("gap jump deadline")
        if prepare_gap(fresh, self.plan.source, self.plan.target, self.policy) is None:
            return self.fail("gap support/clearance changed or stale")
        if self.plan.requires_sprint and body.get("food", 0) <= 6:
            return self.fail("gap sprint requires food")
        p, velocity = body["position"], body.get("velocity")
        if (
            not isinstance(velocity, list)
            or len(velocity) != 3
            or any(
                type(v) not in (int, float) or not math.isfinite(v) for v in velocity
            )
        ):
            return self.fail("gap jump requires observed velocity")
        sx, sy, sz = self.plan.source
        dx, dz = self.plan.direction
        progress = (p[0] - sx - 0.5) * dx + (p[2] - sz - 0.5) * dz
        cross = (p[0] - sx - 0.5) * dz - (p[2] - sz - 0.5) * dx
        span = self.plan.width + 1
        if (
            abs(cross) > 0.22
            or p[1] < sy - 0.1
            or p[1] > sy + 1.4
            or not -1.5 <= progress <= span + 1.5
        ):
            return self.fail("gap jump displaced or fell below landing")
        if not clear_body(fresh, p[0], p[2], p[1], p[1] + 1.8):
            return self.fail("gap body clearance changed or stale")
        if self.phase == "positioning":
            if not body.get("on_ground", False) or abs(p[1] - sy) > 0.1:
                return self.fail("gap takeoff requires grounded runway")
            runway = self.plan.runway
            distance = math.hypot(p[0] - runway[0] - 0.5, p[2] - runway[2] - 0.5)
            if distance > 0.08:
                turn = rotation(
                    (runway[0] + 0.5, sy, runway[2] + 0.5), body, ground=True
                )
                return {
                    **turn,
                    "forward": min(0.3, max(0.08, distance / 2))
                    if abs(turn["yaw_delta"]) < 8
                    else 0,
                }
            if math.hypot(velocity[0], velocity[2]) > 0.03:
                return {}
            self.phase = "runup"
        target = (self.plan.target[0] + 0.5, sy, self.plan.target[2] + 0.5)
        turn = rotation(target, body, ground=True)
        command = {**turn, "forward": 1.0, "sprint": self.plan.requires_sprint}
        if self.phase == "runup":
            if abs(turn["yaw_delta"]) > 4:
                return turn
            if not body.get("on_ground", False):
                return self.fail("gap runway lost ground before jump")
            speed = velocity[0] * dx + velocity[2] * dz
            # The unchanged driver applies this command three ticks later.
            predicted = progress + 3 * speed + 0.10
            if predicted > 0.72:
                return self.fail("gap takeoff window missed")
            if predicted >= -0.05 and speed > 0.1:
                command["jump"] = True
                self.launch_tick, self.phase = tick, "flight"
            return command
        if not body.get("on_ground", False):
            self.air_seen = True
        elif self.air_seen and progress >= span - 0.45 and abs(p[1] - sy) < 0.1:
            self.state = "ready"
            return {}
        elif not self.air_seen and tick - self.launch_tick > 12:
            return self.fail("gap jump did not take off")
        return command
