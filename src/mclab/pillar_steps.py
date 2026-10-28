"""Bounded Survival jump-placement underfoot, with a previously verified exit."""

import math

from mclab.collision_geometry import clear_body
from mclab.navigation import empty, full_cube, rotation, standable
from mclab.terrain_steps import StepExecutor, StepPlan, building_supply


def executor_for(plan, policy):
    return (
        PillarExecutor(plan, policy)
        if plan.kind == "pillar"
        else StepExecutor(plan, policy)
    )


def prepare_pillar(cells, source, exit_node, policy, inventory, *, protected=()):
    x, y, z = source
    if (
        not policy.allow_pillar
        or not policy.allow_place
        or policy.place_budget < 1
        or exit_node is None
        or len(exit_node) != 3
        or abs(exit_node[0] - x) + abs(exit_node[2] - z) != 1
        or not 1 <= exit_node[1] - y <= policy.max_pillar_height + 1
        or not standable(cells, exit_node)
        or not standable(cells, source)
        or source in protected
        or source in policy.protected_positions
        or (cells.get(source) or {}).get("block") in policy.protected_blocks
        or not all(empty(cells.get((x, y + dy, z))) for dy in range(4))
        or not empty(cells.get((exit_node[0], exit_node[1] + 2, exit_node[2])))
        or building_supply(inventory, policy.reserve_blocks, policy.material_allowlist)
        is None
    ):
        return None
    return StepPlan(
        tuple(source),
        (x, y + 1, z),
        places=(tuple(source),),
        kind="pillar",
        exit=tuple(exit_node),
    )


class PillarExecutor:
    def __init__(self, plan, policy):
        if (
            plan.kind != "pillar"
            or not policy.allow_pillar
            or not policy.allow_place
            or policy.place_budget < 1
            or plan.places != (plan.source,)
            or plan.target != (plan.source[0], plan.source[1] + 1, plan.source[2])
            or plan.source in policy.protected_positions
            or plan.breaks
            or plan.exit is None
        ):
            raise ValueError("Pillar plan exceeds permissions/budgets")
        self.plan, self.policy = plan, policy
        self.started = self.launch_tick = self.issue_tick = None
        self.state, self.reason = "editing", ""
        self.issued_places = set()
        self.breaks_confirmed = self.places_confirmed = 0
        self.item = self.initial_count = self.initial_pickups = None

    def fail(self, reason):
        self.state, self.reason = "failed", reason
        return {}

    def decide(self, observation, fresh):
        if self.state in {"failed", "ready"}:
            return {}
        tick, body = observation["tick"], observation["body"]
        if self.started is None:
            self.started = tick
        if tick - self.started > self.policy.interaction_tick_limit:
            return self.fail("pillar interaction deadline")
        p = body["position"]
        sx, sy, sz = self.plan.source
        distance = math.hypot(p[0] - sx - 0.5, p[2] - sz - 0.5)
        if distance > 0.45 or p[1] < sy - 0.2 or p[1] > sy + 1.4:
            return self.fail("displaced during pillar work")
        if not standable(fresh, self.plan.exit):
            return self.fail("pillar exit changed or stale")
        if not clear_body(fresh, p[0], p[2], p[1], p[1] + 1.8):
            return self.fail("pillar body clearance changed or stale")
        inventory = body.get("inventory", [])
        if self.issue_tick is not None:
            cell = fresh.get(self.plan.source)
            pickups = body.get("building_pickups")
            if self.initial_pickups is not None and pickups is None:
                return self.fail("pillar pickup ledger missing")
            acquired = (
                0
                if self.initial_pickups is None
                else ((pickups or {}).get(self.item, 0) - self.initial_pickups)
            )
            count = sum(
                r.get("count", 0) for r in inventory if r.get("item") == self.item
            )
            if acquired < 0:
                return self.fail("pillar pickup ledger changed generation")
            if full_cube(cell):
                if cell.get("block") != self.item:
                    return self.fail("pillar placement geometry mismatch")
                if count == self.initial_count + acquired - 1:
                    self.places_confirmed = 1
            if self.places_confirmed and body.get("on_ground", False):
                if abs(p[1] - self.plan.target[1]) <= 0.1:
                    self.state = "ready"
                    return {}
                return self.fail("pillar did not land on confirmed support")
            if tick - self.issue_tick > 15 and not self.places_confirmed:
                return self.fail("pillar placement not confirmed")
            return {}
        if (
            prepare_pillar(
                fresh, self.plan.source, self.plan.exit, self.policy, inventory
            )
            is None
        ):
            return self.fail("pillar preconditions changed")
        eye = body.get("survival", {}).get("eye_height")
        velocity = body.get("velocity")
        if (
            type(eye) not in (float, int)
            or not math.isfinite(eye)
            or not 0.5 <= eye <= 2
            or not isinstance(velocity, list)
            or len(velocity) != 3
            or any(
                type(v) not in (float, int) or not math.isfinite(v) for v in velocity
            )
        ):
            return self.fail("pillar requires observed eye height and velocity")
        aim_body = {**body, "position": [p[0], p[1] + eye - 1.62, p[2]]}
        slot = building_supply(
            inventory, self.policy.reserve_blocks, self.policy.material_allowlist
        )
        if self.launch_tick is None and body.get("on_ground", False):
            if abs(p[1] - sy) > 0.1:
                return self.fail("pillar source height changed")
            if distance > 0.08:
                turn = rotation((sx + 0.5, sy, sz + 0.5), aim_body, ground=True)
                return {**turn, "forward": 0.2 if abs(turn["yaw_delta"]) < 8 else 0}
            if math.hypot(velocity[0], velocity[2]) > 0.03:
                return {}
        turn = rotation((sx + 0.5, sy - 0.001, sz + 0.5), aim_body)
        ray = body.get("ray", {})
        aligned = abs(turn["yaw_delta"]) < 2 and abs(turn["pitch_delta"]) < 2
        hit = (
            tuple(ray.get("position", ())) == (sx, sy - 1, sz)
            and str(ray.get("face", "")).upper() == "UP"
        )
        command = {**turn, "hotbar_slot": slot}
        if self.launch_tick is None:
            if not body.get("on_ground", False):
                return self.fail("pillar launch requires grounded stance")
            if aligned and hit:
                self.launch_tick = tick
                command["jump"] = True
            return command
        if body.get("on_ground", False):
            if tick - self.launch_tick > 12:
                return self.fail("pillar jump did not enter placement window")
            return command
        # Controls apply three ticks after this observation. In the ordinary
        # rising window the feet are already above the new block, and remain
        # clear at application. Late apex/descent observations cannot place.
        if p[1] - sy >= 1.0 and velocity[1] >= 0.05 and aligned and hit:
            row = next(r for r in inventory if r.get("slot") == slot)
            self.item = row["item"]
            self.initial_count = sum(
                r.get("count", 0) for r in inventory if r.get("item") == self.item
            )
            pickups = body.get("building_pickups")
            self.initial_pickups = (
                None if pickups is None else pickups.get(self.item, 0)
            )
            self.issue_tick = tick
            self.issued_places.add(self.plan.source)
            command["use_pulse"] = True
        return command
