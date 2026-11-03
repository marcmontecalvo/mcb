"""Test-only Python planner oracle for legacy controller behavior fixtures.

Production SegmentedController remains body-planner-only. This adapter lets
controller motor and queue tests feed the existing Python reference searches;
it is deliberately unavailable through production configuration and CLI.
"""

from functools import partial

from mclab.navigation_segmented import SegmentedController
from mclab.planning_worker import BackgroundSearch, PlanningWorker
from mclab.route_search import RouteSearch
from mclab.terrain_route import TerrainRouteSearch


class ReferenceSegmentedController(SegmentedController):
    requires_body_planner = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.planner_lane = PlanningWorker() if self.background_planning else None

    def _reference_search(self, cells, node, revision, factory):
        if self.planner_lane is not None:
            return BackgroundSearch(
                self.planner_lane,
                cells,
                node,
                self.goal,
                map_revision=revision,
                goal_revision=self.goal_revision,
                goal_radius=self.radius,
                node_limit=self.search_nodes,
                slice_ms=self.search_ms,
                search_factory=factory,
            )
        return factory(
            cells,
            node,
            self.goal,
            map_revision=revision,
            goal_radius=self.radius,
            owned_snapshot=True,
        )

    def _new_search(self, cells, node, revision):
        factory = partial(
            RouteSearch,
            favored=frozenset(self.previous_route),
            movement_policy=self.movement_policy,
            excluded=frozenset(self.failed_edges),
            terrain_policy=None
            if self.terrain_policy is None
            else self._remaining_policy(),
            inventory=self.inventory,
            protected=frozenset(self.own_footholds),
            interact_reach=self.interact_reach,
            threats=self.threats,
        )
        return self._reference_search(cells, node, revision, factory)

    def _next_step(self, node, observation):
        if self.terrain_search is None or self.terrain_search.start != node:
            self._discard_prefetch()
            revision, cells = self._planning_snapshot()
            factory = partial(
                TerrainRouteSearch,
                policy=self._remaining_policy(),
                inventory=tuple(
                    dict(r) for r in observation["body"].get("inventory", [])
                ),
                protected=frozenset(self.own_footholds),
                excluded=frozenset(self.failed_edges),
                movement_policy=self.movement_policy,
            )
            self.terrain_search = self._reference_search(cells, node, revision, factory)
        return super()._next_step(node, observation)

    def _discard_prefetch(self):
        if self.prefetch is not None and self.planner_lane is not None:
            self.planner_lane.cancel(getattr(self.prefetch, "token", None))
        self.prefetch = None

    def cancel(self):
        if self.planner_lane is not None:
            self.planner_lane.cancel()
        super().cancel()

    def close(self):
        try:
            super().close()
        finally:
            if self.planner_lane is not None:
                self.planner_lane.close()

    def status(self):
        result = super().status()
        result["planner"] = self.planner_lane.status() if self.planner_lane else None
        return result
