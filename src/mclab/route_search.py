"""Resumable development A* over immutable observed geometry.

Search slices and output segments are bounded independently. Search can move
away from the goal to find a detour. Partial results contain only checked edges;
unknown cells are never invented as air. The motor must revalidate local cells.
"""

import heapq
import math
import time
from dataclasses import dataclass

from mclab.navigation import DIRECTIONS, empty, reconstruct, standable


@dataclass(frozen=True)
class RouteResult:
    status: str
    route: tuple
    map_revision: int
    expanded: int
    reason: str


class RouteSearch:
    def __init__(
        self, cells, start, goal, *, map_revision, goal_radius=0, owned_snapshot=False
    ):
        # Caller hands an independent snapshot; retain our own nested copy.
        import copy

        self.cells = cells if owned_snapshot else copy.deepcopy(cells)
        self.start, self.goal = tuple(start), tuple(goal)
        self.revision = map_revision
        if len(self.start) != 3 or len(self.goal) != 3 or goal_radius < 0:
            raise ValueError("Invalid route endpoints")
        self.radius = goal_radius
        self.frontier = [(self._h(self.start), 0.0, self.start)]
        self.costs, self.previous = {self.start: 0.0}, {}
        self.expanded = 0
        self.best = self.start
        self.terminal = None
        if not standable(self.cells, self.start):
            self.frontier.clear()
            self.terminal = RouteResult(
                "unreachable", (), self.revision, 0, "start is not known standable"
            )

    def _h(self, node):
        # Distance to the horizontal goal disk, with exact standing height.
        return max(
            max(
                0,
                math.hypot(node[0] - self.goal[0], node[2] - self.goal[2])
                - self.radius,
            ),
            abs(node[1] - self.goal[1]),
        )

    def _edges(self, node):
        x, y, z = node
        for dx, dz in DIRECTIONS:
            for dy in (0, 1, -1):
                target = (x + dx, y + dy, z + dz)
                if not standable(self.cells, target):
                    continue
                if dy == 1 and not all(
                    empty(self.cells.get(p))
                    for p in (
                        (x, y + 2, z),
                        (x, y + 3, z),
                        (x + dx, y + 3, z + dz),
                    )
                ):
                    continue
                if dy == -1 and not empty(self.cells.get((x + dx, y + 1, z + dz))):
                    continue
                yield target, 1.4 if dy else 1.0

    def advance(self, *, node_limit=256, budget_ms=5, segment_length=16):
        if (
            type(node_limit) is not int
            or node_limit < 1
            or type(segment_length) is not int
            or segment_length < 2
            or not math.isfinite(budget_ms)
            or budget_ms <= 0
        ):
            raise ValueError("Invalid route search budgets")
        if self.terminal is not None:
            return self._segment(self.terminal, segment_length)
        deadline = time.perf_counter() + budget_ms / 1000
        count = 0
        while self.frontier and count < node_limit and time.perf_counter() < deadline:
            _, cost, node = heapq.heappop(self.frontier)
            count += 1
            if cost != self.costs[node]:
                continue
            self.expanded += 1
            if self._h(node) < self._h(self.best):
                self.best = node
            if self._h(node) == 0:
                self.terminal = RouteResult(
                    "reached",
                    tuple(reconstruct(self.previous, node)),
                    self.revision,
                    self.expanded,
                    "known goal route",
                )
                return self._segment(self.terminal, segment_length)
            for target, edge in self._edges(node):
                total = cost + edge
                if total < self.costs.get(target, math.inf):
                    self.costs[target], self.previous[target] = total, node
                    heapq.heappush(
                        self.frontier, (total + self._h(target), total, target)
                    )
        status = "searching" if self.frontier else "unreachable"
        result = RouteResult(
            status,
            tuple(reconstruct(self.previous, self.best)),
            self.revision,
            self.expanded,
            "slice budget" if self.frontier else "known graph exhausted",
        )
        if not self.frontier:
            self.terminal = result
        return self._segment(result, segment_length)

    @staticmethod
    def _segment(result, length):
        route = result.route[:length]
        status = result.status
        if status == "reached" and len(route) < len(result.route):
            status = "segment"
        return RouteResult(
            status, route, result.map_revision, result.expanded, result.reason
        )
