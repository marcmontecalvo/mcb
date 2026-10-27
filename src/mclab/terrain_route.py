"""Bounded multi-move terrain forecasts over immutable admitted geometry.

The four-move beam contains walking and edit steps together. Each branch carries
its predicted edits, remaining stock and protected footholds. Predictions guide
planning only; the executor still confirms every edit against fresh geometry.
"""

import math
import time
from collections import ChainMap
from dataclasses import dataclass, replace

from mclab.navigation import DIRECTIONS, empty, standable
from mclab.terrain_steps import BUILDING, StepPlan, building_supply, prepare_step


def known_step(cells, source, target):
    x, y, z = source
    tx, ty, tz = target
    if abs(tx - x) + abs(tz - z) != 1 or abs(ty - y) > 1:
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
    if ty < y and not empty(cells.get((tx, y + 1, tz))):
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
    ):
        import copy

        if type(horizon) is not int or not 3 <= horizon <= 6:
            raise ValueError("Terrain horizon must cover 3..6 moves")
        if type(beam_width) is not int or not 1 <= beam_width <= 64:
            raise ValueError("Invalid terrain beam width")
        self.cells = cells if owned_snapshot else copy.deepcopy(cells)
        self.start, self.goal = tuple(start), tuple(goal)
        self.revision, self.policy, self.radius = map_revision, policy, goal_radius
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
        self.terminal = None

    def _goal(self, node):
        return (
            node[1] == self.goal[1]
            and math.hypot(node[0] - self.goal[0], node[2] - self.goal[2])
            <= self.radius
        )

    def _rank(self, branch):
        node = branch.node
        # Height debt matters over a short horizon: a cheap tunnel that loses
        # the necessary ascent is not useful progress toward a hill summit.
        tail = (
            abs(node[0] - self.goal[0])
            + abs(node[2] - self.goal[2])
            + 4 * abs(node[1] - self.goal[1])
        )
        return branch.cost + tail, branch.broken + branch.placed, branch.node

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
                if known_step(view, branch.node, target):
                    plan = StepPlan(branch.node, target)
                else:
                    plan = prepare_step(
                        view,
                        branch.node,
                        target,
                        policy,
                        branch.inventory,
                        protected=branch.protected,
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
                    slot = building_supply(inventory, policy.reserve_blocks)
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
                cost = (1.4 if dy else 1) + 3 * len(plan.breaks) + 4 * len(plan.places)
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

    def advance(self, *, node_limit=128, budget_ms=3, segment_length=16):
        if type(node_limit) is not int or node_limit < 1 or not 0 < budget_ms <= 1000:
            raise ValueError("Invalid terrain search budget")
        if self.terminal is not None:
            return self.terminal
        deadline = time.perf_counter() + budget_ms / 1000
        spent = 0
        while spent < node_limit and time.perf_counter() < deadline:
            if self.index == len(self.layer):
                self.depth += 1
                if self.depth == self.horizon or not self.next_layer:
                    if self.goals:
                        best = min(self.goals, key=lambda b: b.cost)
                        self.terminal = self._result(
                            "reached", best, "goal within terrain horizon"
                        )
                    elif self.depth == self.horizon and self.next_layer:
                        edited = [b for b in self.next_layer if b.broken or b.placed]
                        self.terminal = self._result(
                            "segment" if edited else "incomplete",
                            min(edited, key=self._rank) if edited else None,
                            "four-move terrain forecast"
                            if edited
                            else "no permitted terrain work within horizon",
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
                if self._goal(child.node):
                    self.goals.append(child)
                else:
                    self.next_layer.append(child)
        return self._result("searching", None, "terrain search slice")
