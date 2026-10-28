"""Bounded multi-move terrain forecasts over immutable admitted geometry.

The four-move beam contains walking and edit steps together. Each branch carries
its predicted edits, remaining stock and protected footholds. Predictions guide
planning only; the executor still confirms every edit against fresh geometry.
"""

import math
import time
from collections import ChainMap
from dataclasses import dataclass, replace
from itertools import chain

from mclab.movement_policy import MovementPolicy
from mclab.navigation import DIRECTIONS, empty, full_cube, standable
from mclab.pillar_steps import prepare_pillar
from mclab.terrain_steps import BUILDING, StepPlan, building_supply, prepare_step


def known_step(cells, source, target, *, max_drop=1):
    x, y, z = source
    tx, ty, tz = target
    if abs(tx - x) + abs(tz - z) != 1 or not -max_drop <= ty - y <= 1:
        return False
    if not standable(cells, source) or not standable(cells, target):
        return False
    if ty > y and not all(
        empty(cells.get(p))
        for p in (
            (x, y + 2, z),
            (x, y + 3, z),
            (tx, ty + 2, tz),
        )
    ):
        return False
    if ty < y and not all(
        empty(cells.get((tx, cy, tz))) for cy in range(math.floor(ty), math.ceil(y) + 2)
    ):
        return False
    return True


@dataclass(frozen=True)
class TerrainRouteResult:
    status: str
    route: tuple
    map_revision: int
    expanded: int
    reason: str
    steps: tuple = ()


@dataclass(frozen=True)
class Branch:
    node: tuple
    steps: tuple
    edits: dict
    inventory: tuple
    protected: frozenset
    visited: frozenset
    broken: int = 0
    placed: int = 0
    cost: float = 0


class TerrainRouteSearch:
    """Incremental beam: horizon 4, fixed beam width, no global hypothetical map.

    This is a local rolling horizon after known-route search. It cannot certify
    a distant goal; it returns a plan for several moves or explicit incompleteness.
    """

    def __init__(
        self,
        cells,
        start,
        goal,
        *,
        map_revision,
        policy,
        inventory,
        goal_radius=0,
        owned_snapshot=False,
        protected=(),
        excluded=(),
        horizon=4,
        beam_width=24,
        movement_policy=None,
    ):
        import copy

        if type(horizon) is not int or not 3 <= horizon <= 6:
            raise ValueError("Terrain horizon must cover 3..6 moves")
        if type(beam_width) is not int or not 1 <= beam_width <= 64:
            raise ValueError("Invalid terrain beam width")
        self.cells = cells if owned_snapshot else copy.deepcopy(cells)
        self.start, self.goal = tuple(start), tuple(goal)
        self.revision, self.policy, self.radius = map_revision, policy, goal_radius
        self.movement_policy = (
            MovementPolicy() if movement_policy is None else movement_policy
        )
        if not isinstance(self.movement_policy, MovementPolicy):
            raise ValueError("Invalid movement policy")
        self.excluded = frozenset(excluded)
        self.horizon, self.width = horizon, beam_width
        self.expanded = 0
        self.depth = 0
        self.layer = [
            Branch(
                self.start,
                (),
                {},
                tuple(dict(r) for r in inventory),
                frozenset(protected),
                frozenset((self.start,)),
            )
        ]
        self.index = 0
        self.next_layer = []
        self.goals = []
        self.walking_costs = {self.start: 0}
        # A staircase needs its approach space before reaching a sheer ledge.
        # Enumerate only straight bounded macros with an observed solid exit;
        # this is not an unbounded search through hypothetical world edits.
        staircases = (
            ("staircase", dx, dz, rise, distance)
            for dx, dz in DIRECTIONS
            for rise in range(2, min(4, policy.max_support_stack + 1) + 1)
            for distance in range(rise, 13)
            if policy.max_support_stack > 1
            and (self.goal[0] - self.start[0]) * dx
            + (self.goal[2] - self.start[2]) * dz
            > 0
            and self.start[1] + rise <= self.goal[1]
        )
        pillars = (
            ("pillar", dx, dz, rise, distance)
            for dx, dz in DIRECTIONS
            for rise in range(2, policy.max_pillar_height + 2)
            for distance in range(1, 13)
            if policy.allow_pillar
            and (self.goal[0] - self.start[0]) * dx
            + (self.goal[2] - self.start[2]) * dz
            > 0
            and self.start[1] + rise <= self.goal[1]
        )
        self.macro_candidates = chain(staircases, pillars)
        self.macro_searching = True
        self.macros = []
        self.macro_unknown = False
        self.terminal = None

    def _goal(self, node):
        return (
            node[1] == self.goal[1]
            and math.hypot(node[0] - self.goal[0], node[2] - self.goal[2])
            <= self.radius
        )

    def _distance(self, node):
        # Height debt matters over a short horizon: a cheap tunnel that loses
        # the necessary ascent is not useful progress toward a hill summit.
        return (
            abs(node[0] - self.goal[0])
            + abs(node[2] - self.goal[2])
            + 4 * abs(node[1] - self.goal[1])
        )

    def _rank(self, branch):
        return (
            branch.cost + self._distance(branch.node),
            branch.broken + branch.placed,
            self._distance(branch.node),
            branch.node,
        )

    def _children(self, branch):
        view = ChainMap(branch.edits, self.cells)
        x, y, z = branch.node
        policy = replace(
            self.policy,
            break_budget=self.policy.break_budget - branch.broken,
            place_budget=self.policy.place_budget - branch.placed,
        )
        for dx, dz in DIRECTIONS:
            for dy in (0, 1, -1):
                target = (x + dx, y + dy, z + dz)
                if target in branch.visited or (branch.node, target) in self.excluded:
                    continue
                if dy < -self.movement_policy.max_drop:
                    continue
                if known_step(
                    view, branch.node, target, max_drop=self.movement_policy.max_drop
                ):
                    plan = StepPlan(branch.node, target)
                else:
                    plan = prepare_step(
                        view,
                        branch.node,
                        target,
                        policy,
                        branch.inventory,
                        # Preserve the forecast's retreat corridor. Placing into
                        # an already traversed body cell manufactures progress
                        # by looping back onto a new obstacle of our own making.
                        protected=branch.protected | branch.visited,
                    )
                if plan is None:
                    continue
                edits = dict(branch.edits)
                for p in plan.breaks:
                    edits[p] = {
                        "position": list(p),
                        "block": "minecraft:air",
                        "fluid": "minecraft:empty",
                        "collision_boxes": [],
                    }
                inventory = [dict(r) for r in branch.inventory]
                possible = True
                for p in plan.places:
                    slot = building_supply(
                        inventory, policy.reserve_blocks, policy.material_allowlist
                    )
                    if slot is None:
                        possible = False
                        break
                    row = next(r for r in inventory if r["slot"] == slot)
                    if row["item"] not in BUILDING:
                        possible = False
                        break
                    row["count"] -= 1
                    edits[p] = {
                        "position": list(p),
                        "block": row["item"],
                        "fluid": "minecraft:empty",
                        "collision_boxes": [[0, 0, 0, 1, 1, 1]],
                    }
                if not possible:
                    continue
                after = ChainMap(edits, self.cells)
                if not known_step(after, branch.node, target):
                    continue
                cost = (
                    (1.4 if dy else 1)
                    + (policy.break_penalty + policy.tool_wear_penalty)
                    * len(plan.breaks)
                    + policy.placement_penalty * len(plan.places)
                )
                yield Branch(
                    target,
                    (*branch.steps, plan),
                    edits,
                    tuple(inventory),
                    branch.protected | frozenset(plan.places),
                    branch.visited | frozenset((target,)),
                    branch.broken + len(plan.breaks),
                    branch.placed + len(plan.places),
                    branch.cost + cost,
                )

    def _result(self, status, branch, reason):
        steps = branch.steps if branch is not None else ()
        return TerrainRouteResult(
            status,
            (self.start, *(s.target for s in steps)),
            self.revision,
            self.expanded,
            reason,
            steps,
        )

    def _staircase(self, dx, dz, rise, distance):
        x, y, z = self.start
        exit_node = (x + dx * distance, y + rise, z + dz * distance)
        if not standable(self.cells, exit_node):
            ex, ey, ez = exit_node
            body = (self.cells.get(exit_node), self.cells.get((ex, ey + 1, ez)))
            if (
                full_cube(self.cells.get((ex, ey - 1, ez)))
                and any(c is None for c in body)
                and all(c is None or empty(c) for c in body)
            ):
                self.macro_unknown = True
            return None
        branch = self.layer[0]
        for offset in range(1, max(distance, self.horizon) + 1):
            target = (
                x + dx * offset,
                y + min(rise, max(0, offset - (distance - rise))),
                z + dz * offset,
            )
            bx, by, bz = branch.node
            tx, ty, tz = target
            clearance = [(tx, ty, tz), (tx, ty + 1, tz)]
            if ty > by:
                clearance += [(bx, by + 2, bz), (bx, by + 3, bz), (tx, ty + 2, tz)]
            if any(p not in self.cells and p not in branch.edits for p in clearance):
                self.macro_unknown = True
                return None
            branch = next((b for b in self._children(branch) if b.node == target), None)
            if branch is None:
                return None
            if self._goal(branch.node):
                break
        # Pure walking belongs to the normal route search. This macro must
        # explicitly account for construction and exit onto unchanged ground.
        if not branch.placed or not standable(self.cells, exit_node):
            return None
        return branch

    def _pillar(self, dx, dz, rise, distance):
        x, y, z = self.start
        exit_node = (x + dx * distance, y + rise, z + dz * distance)
        ex, ey, ez = exit_node
        if not standable(self.cells, exit_node):
            if full_cube(self.cells.get((ex, ey - 1, ez))) and any(
                self.cells.get(p) is None for p in (exit_node, (ex, ey + 1, ez))
            ):
                self.macro_unknown = True
            return None
        branch = self.layer[0]
        for offset in range(1, distance):
            target = (x + dx * offset, y, z + dz * offset)
            branch = next((b for b in self._children(branch) if b.node == target), None)
            if branch is None:
                return None
        for _ in range(rise - 1):
            view = ChainMap(branch.edits, self.cells)
            bx, by, bz = branch.node
            if any((bx, by + h, bz) not in view for h in range(4)):
                self.macro_unknown = True
                return None
            remaining = replace(
                self.policy, place_budget=self.policy.place_budget - branch.placed
            )
            plan = prepare_pillar(
                view,
                branch.node,
                exit_node,
                remaining,
                branch.inventory,
                protected=branch.protected,
            )
            if plan is None or (plan.source, plan.target) in self.excluded:
                return None
            inventory = [dict(row) for row in branch.inventory]
            slot = building_supply(
                inventory, remaining.reserve_blocks, remaining.material_allowlist
            )
            row = next(row for row in inventory if row["slot"] == slot)
            row["count"] -= 1
            edits = dict(branch.edits)
            edits[plan.source] = {
                "position": list(plan.source),
                "block": row["item"],
                "fluid": "minecraft:empty",
                "collision_boxes": [[0, 0, 0, 1, 1, 1]],
            }
            branch = replace(
                branch,
                node=plan.target,
                steps=(*branch.steps, plan),
                edits=edits,
                inventory=tuple(inventory),
                protected=branch.protected | {plan.source},
                visited=branch.visited | {plan.target},
                placed=branch.placed + 1,
                cost=branch.cost + 1.4 + remaining.placement_penalty,
            )
        offset = distance
        while len(branch.steps) < self.horizon or offset == distance:
            target = (x + dx * offset, y + rise, z + dz * offset)
            branch = next((b for b in self._children(branch) if b.node == target), None)
            if branch is None:
                return None
            if self._goal(branch.node):
                break
            offset += 1
        return branch

    def advance(self, *, node_limit=128, budget_ms=3, segment_length=16):
        if type(node_limit) is not int or node_limit < 1 or not 0 < budget_ms <= 1000:
            raise ValueError("Invalid terrain search budget")
        if self.terminal is not None:
            return self.terminal
        deadline = time.perf_counter() + budget_ms / 1000
        spent = 0
        while spent < node_limit and time.perf_counter() < deadline:
            if self.macro_searching:
                candidate = next(self.macro_candidates, None)
                if candidate is None:
                    self.macro_searching = False
                    if self.macros:
                        best = min(self.macros, key=self._rank)
                        steps = best.steps[: self.horizon]
                        self.terminal = self._result(
                            "reached" if self._goal(steps[-1].target) else "segment",
                            replace(best, steps=steps),
                            "verified construction macro; four-move forecast",
                        )
                        return self.terminal
                    if self.macro_unknown:
                        self.terminal = self._result(
                            "incomplete",
                            None,
                            "construction macro needs known approach/exit geometry",
                        )
                        return self.terminal
                    continue
                self.expanded += 1
                spent += 1
                kind, *parameters = candidate
                macro = (self._pillar if kind == "pillar" else self._staircase)(
                    *parameters
                )
                if macro is not None:
                    self.macros.append(macro)
                continue
            if self.index == len(self.layer):
                self.depth += 1
                if self.depth == self.horizon or not self.next_layer:
                    if self.goals:
                        best = min(self.goals, key=lambda b: b.cost)
                        self.terminal = self._result(
                            "reached", best, "goal within terrain horizon"
                        )
                    elif self.depth == self.horizon and self.next_layer:
                        candidates = [
                            b
                            for b in self.next_layer
                            if (
                                (b.broken or b.placed)
                                and self.walking_costs.get(b.node, math.inf) > b.cost
                                and self._distance(b.node) < self._distance(self.start)
                            )
                            or (
                                not b.broken
                                and not b.placed
                                and self._distance(b.node) < self._distance(self.start)
                            )
                        ]
                        self.terminal = self._result(
                            "segment" if candidates else "incomplete",
                            min(candidates, key=self._rank) if candidates else None,
                            "four-move terrain forecast"
                            if candidates
                            else "no terrain work or walking progress within horizon",
                        )
                    else:
                        self.terminal = self._result(
                            "incomplete", None, "insufficient known terrain horizon"
                        )
                    return self.terminal
                self.layer = sorted(self.next_layer, key=self._rank)[: self.width]
                self.next_layer, self.index = [], 0
            branch = self.layer[self.index]
            self.index += 1
            self.expanded += 1
            spent += 1
            for child in self._children(branch):
                if not child.broken and not child.placed:
                    self.walking_costs[child.node] = min(
                        child.cost, self.walking_costs.get(child.node, math.inf)
                    )
                if self._goal(child.node):
                    self.goals.append(child)
                else:
                    self.next_layer.append(child)
        return self._result("searching", None, "terrain search slice")
