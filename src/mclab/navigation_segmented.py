"""Development navigation: durable observations -> bounded search -> fresh motor.

Known walking routes precede bounded cut/foothold plans. The executor confirms
geometry before moving and never substitutes unconditional goal-directed digging.
"""

import math
import time
from collections import deque
from dataclasses import replace
from functools import partial

from mclab.navigation import NavigationController, standable
from mclab.observation_worker import ObservationWorker
from mclab.planning_worker import BackgroundSearch, PlanningWorker
from mclab.route_search import RouteSearch
from mclab.terrain_route import TerrainRouteSearch, known_step
from mclab.terrain_steps import StepExecutor, prepare_step


class SegmentedController(NavigationController):
    def __init__(
        self,
        goal,
        memory,
        *,
        radius=2,
        search_nodes=128,
        search_ms=3,
        terrain_policy=None,
        background_planning=False,
    ):
        super().__init__(
            sensor_profile=memory.archive.identity.awareness.sensor_profile
        )
        self.memory = memory
        self.goal = tuple(goal)
        self.radius = radius
        self.search_nodes, self.search_ms = search_nodes, search_ms
        self.search = None
        self.route = []
        self.awareness_revision = None
        self.mode = "planning"
        self.cancelled = False
        self.reason = ""
        self.last_result = None
        self.terrain_policy = terrain_policy
        self.terrain = None
        self.edits_broken = self.edits_placed = 0
        self.breaks_reserved = self.places_reserved = 0
        self.failed_edges = set()
        self.events = []
        self.committed_step = None
        self.commit_started = None
        self.own_footholds = set()
        self.terrain_search = None
        self.terrain_queue = []
        self.terrain_forecast = ()
        self.terrain_queue_policy = None
        if type(background_planning) is not bool:
            raise ValueError("background_planning must be a boolean")
        self.planner_lane = PlanningWorker() if background_planning else None
        self.observation_lane = (
            ObservationWorker(memory) if background_planning else None
        )
        self.map_view = self.observation_lane.latest if self.observation_lane else None
        self.motor_cells = {}
        self.motor_sample = None
        self.goal_revision = 0
        self.prefetch = None
        self.closed = False
        self.prefetch_started = self.prefetch_adopted = 0
        self.motor_latencies = deque(maxlen=2048)

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def cancel(self):
        self.goal_revision += 1
        if self.planner_lane is not None:
            self.planner_lane.cancel()
        self.prefetch = None
        if self.terrain is not None:
            self.edits_broken += self.terrain.breaks_confirmed
            self.edits_placed += self.terrain.places_confirmed
        self._clear_terrain_forecast()
        self.cancelled = True
        self.route.clear()
        self.search = None
        self.air_waypoint = None
        self.terrain = None
        self.committed_step = None
        self.mode = "cancelled"

    def resume(self):
        if self.closed:
            raise RuntimeError("Navigation controller is closed")
        self.cancelled = False
        self.mode = "planning"

    def replace_goal(self, goal):
        self.cancel()
        self.goal = tuple(goal)
        self.resume()

    def status(self):
        samples = sorted(self.motor_latencies)
        memory_status = self.map_view.status if self.map_view else self.memory.status()
        return {
            "mode": self.mode,
            "reason": self.reason,
            "goal": self.goal,
            "route_remaining": len(self.route),
            "active_cells": memory_status["active_cells"],
            "persistence_queue": memory_status["persistence_queue"],
            "map_revision": self.map_view.revision
            if self.map_view
            else self.memory.revision,
            "memory": memory_status,
            "admission_queue": self.observation_lane.pending.qsize()
            if self.observation_lane
            else 0,
            "search_expanded": self.search.expanded if self.search else 0,
            "breaks_confirmed": self.edits_broken,
            "places_confirmed": self.edits_placed,
            "break_budget_reserved": self.breaks_reserved,
            "place_budget_reserved": self.places_reserved,
            "committed_step": self.committed_step,
            "terrain_forecast": [
                {
                    "source": s.source,
                    "target": s.target,
                    "breaks": s.breaks,
                    "places": s.places,
                }
                for s in self.terrain_forecast
            ],
            "background_planning": self.planner_lane is not None,
            "planner": self.planner_lane.status() if self.planner_lane else None,
            "prefetch_pending": self.prefetch is not None,
            "prefetch_started": self.prefetch_started,
            "prefetch_adopted": self.prefetch_adopted,
            "motor_latency_ms": {
                name: samples[int((len(samples) - 1) * q)] if samples else None
                for name, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
            },
            "motor_latency_samples": len(samples),
        }

    def close(self):
        if self.closed:
            return
        self.cancel()
        try:
            if self.planner_lane is not None:
                self.planner_lane.close()
        finally:
            try:
                if self.observation_lane is not None:
                    self.observation_lane.close()
            finally:
                self.closed = True

    def _planning_snapshot(self):
        if self.map_view is not None:
            if (
                self.memory.policy.memory_mode == "current"
                or not self.memory.policy.use_historical_geometry
            ):
                return self.map_view.revision, self._fresh(self.motor_tick)
            return self.map_view.revision, self.map_view.cells
        return self.memory.planning_snapshot()

    def _known_cells(self):
        return self._planning_snapshot()[1] if self.map_view else self.memory.cells

    def _admit(self, observation):
        if self.observation_lane is None:
            self.memory.ingest(observation)
            return
        self.observation_lane.submit(observation)
        self.map_view = self.observation_lane.poll()
        self.motor_tick = observation["tick"]
        sample = (
            observation.get("awareness", {}).get("revision"),
            observation.get("awareness", {}).get("coverage", {}).get("sample_tick"),
        )
        if sample[1] is not None and sample == self.motor_sample:
            return
        cells = {}
        for cell in observation.get("visible_blocks", []):
            p = cell.get("position")
            if (
                not isinstance(p, list)
                or len(p) != 3
                or any(type(v) is not int for v in p)
            ):
                raise ValueError("Invalid observed cell position")
            cells[tuple(p)] = cell
        self.motor_cells = cells
        self.motor_sample = sample

    def _new_search(self, cells, node, revision, factory=RouteSearch):
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

    def _discard_prefetch(self):
        if self.prefetch is not None:
            self.planner_lane.cancel()
            self.prefetch = None

    def _start_prefetch(self):
        if (
            self.planner_lane is None
            or self.prefetch is not None
            or self.last_result is None
            or self.last_result.status != "segment"
            or len(self.route) < 3
        ):
            return
        end = self.route[-1]
        if (
            end[1] == self.goal[1]
            and math.hypot(end[0] - self.goal[0], end[2] - self.goal[2]) <= self.radius
        ):
            return
        revision, cells = self._planning_snapshot()
        self.prefetch = self._new_search(cells, end, revision)
        self.prefetch_started += 1
        self.events.append(
            (
                "route_prefetch_started",
                {
                    "start": end,
                    "map_revision": revision,
                    "goal_revision": self.goal_revision,
                },
            )
        )

    def _follow_route(self, observation):
        """Keep speed through a known straight segment; preserve corner/step stops."""
        start = self.route[0]
        waypoints = self.route[1:2]
        first = waypoints[0]
        if first[1] == start[1]:
            direction = (first[0] - start[0], first[2] - start[2])
            previous = first
            for candidate in self.route[2:4]:
                if (
                    candidate[1] != start[1]
                    or (candidate[0] - previous[0], candidate[2] - previous[2])
                    != direction
                ):
                    break
                waypoints.append(candidate)
                previous = candidate
        for target in reversed(waypoints):
            command = self._move(
                observation, [target[0] + 0.5, target[1], target[2] + 0.5]
            )
            if command.get("forward") or abs(command.get("yaw_delta", 0)) > 8:
                return command
        return command

    def _fresh(self, tick):
        if self.observation_lane is not None:
            return {
                p: cell
                for p, cell in self.motor_cells.items()
                if 0 <= tick - cell.get("observed_tick", self.motor_tick) <= 5
            }
        return self.memory.fresh(tick)

    def _next_step(self, node, observation):
        policy = replace(
            self.terrain_policy,
            break_budget=max(
                0, self.terrain_policy.break_budget - self.breaks_reserved
            ),
            place_budget=max(
                0, self.terrain_policy.place_budget - self.places_reserved
            ),
        )
        if self.terrain_search is None or self.terrain_search.start != node:
            self._discard_prefetch()
            revision, cells = self._planning_snapshot()
            factory = partial(
                TerrainRouteSearch,
                policy=policy,
                inventory=tuple(
                    dict(r) for r in observation["body"].get("inventory", [])
                ),
                protected=frozenset(self.own_footholds),
                excluded=frozenset(self.failed_edges),
            )
            self.terrain_search = self._new_search(cells, node, revision, factory)
        result = self.terrain_search.advance(
            node_limit=self.search_nodes, budget_ms=self.search_ms
        )
        self.reason = result.reason
        if result.status == "searching":
            self.mode = "terrain_planning"
            return None, policy
        self.terrain_search = None
        if not result.steps:
            self.mode = "terrain_incomplete"
            return None, policy
        self.terrain_forecast = result.steps
        self.terrain_queue = list(result.steps[1:])
        self.terrain_queue_policy = policy
        self.breaks_reserved += sum(len(s.breaks) for s in result.steps)
        self.places_reserved += sum(len(s.places) for s in result.steps)
        self.events.append(
            (
                "terrain_route_planned",
                {
                    "map_revision": result.map_revision,
                    "horizon": 4,
                    "status": result.status,
                    "steps": [
                        {
                            "source": s.source,
                            "target": s.target,
                            "breaks": s.breaks,
                            "places": s.places,
                        }
                        for s in result.steps
                    ],
                },
            )
        )
        return result.steps[0], policy

    def _plan_terrain_step(self, node, observation):
        plan, policy = self._next_step(node, observation)
        if plan is not None:
            self.terrain = StepExecutor(plan, policy)
            self.events.append(
                (
                    "terrain_step_planned",
                    {
                        "source": plan.source,
                        "target": plan.target,
                        "breaks": plan.breaks,
                        "places": plan.places,
                    },
                )
            )
            self.mode = "terrain_editing"

    def _clear_terrain_forecast(self):
        # Queued steps have issued no inputs. Release their holds when rolling
        # the horizon forward; the active/uncertain step's reservation remains.
        self.breaks_reserved -= sum(len(s.breaks) for s in self.terrain_queue)
        self.places_reserved -= sum(len(s.places) for s in self.terrain_queue)
        self.terrain_queue = []
        self.terrain_forecast = ()
        self.terrain_search = None

    def _begin_queued_step(self, node, observation):
        plan = self.terrain_queue[0]
        fresh = self._fresh(observation["tick"])
        valid = node == plan.source
        if valid and (plan.breaks or plan.places):
            actual = prepare_step(
                fresh,
                plan.source,
                plan.target,
                self.terrain_queue_policy,
                observation["body"].get("inventory", []),
                protected=self.own_footholds,
            )
            valid = (
                actual is not None
                and set(actual.breaks) == set(plan.breaks)
                and actual.places == plan.places
            )
        elif valid:
            valid = known_step(fresh, plan.source, plan.target)
        if not valid:
            self._clear_terrain_forecast()
            self.reason = "forecast preconditions changed; replanning"
            self.mode = "terrain_replanning"
            return
        self.terrain_queue.pop(0)
        self.terrain = StepExecutor(plan, self.terrain_queue_policy)
        self.mode = (
            "terrain_editing" if plan.breaks or plan.places else "terrain_planned_walk"
        )
        self.events.append(
            (
                "terrain_step_planned",
                {
                    "source": plan.source,
                    "target": plan.target,
                    "breaks": plan.breaks,
                    "places": plan.places,
                    "remaining_forecast_moves": len(self.terrain_queue),
                },
            )
        )

    def decide(self, observation):
        began = time.perf_counter()
        try:
            return self._decide(observation)
        finally:
            duration = (time.perf_counter() - began) * 1000
            self.motor_latencies.append(duration)
            self.events.append(
                (
                    "navigation_tick",
                    {
                        "tick": observation["tick"],
                        "motor_ms": duration,
                        "mode": self.mode,
                    },
                )
            )

    def _decide(self, observation):
        if self.cancelled:
            return {}
        revision = observation.get("awareness", {}).get("revision")
        if self.memory.archive.identity.awareness.preset != "legacy" and not revision:
            raise ValueError("Missing awareness revision")
        if self.awareness_revision is None:
            self.awareness_revision = revision
        elif revision != self.awareness_revision:
            raise ValueError("Awareness revision changed; fresh controller required")
        self._admit(observation)
        body = observation["body"]
        p = body["position"]
        node = (math.floor(p[0]), round(p[1]), math.floor(p[2]))
        if self.terrain is not None:
            command = self.terrain.decide(observation, self._fresh(observation["tick"]))
            # An issued placement may succeed before confirmation is complete.
            # Recovery must not immediately mine a possible newly built foothold.
            self.own_footholds.update(self.terrain.issued_places)
            self.mode = "terrain_" + self.terrain.state
            if self.terrain.state in {"ready", "failed"}:
                self.edits_broken += self.terrain.breaks_confirmed
                self.edits_placed += self.terrain.places_confirmed
                self.events.append(
                    (
                        "terrain_step_finished",
                        {
                            "state": self.terrain.state,
                            "reason": self.terrain.reason,
                            "target": self.terrain.plan.target,
                            "breaks_confirmed": self.terrain.breaks_confirmed,
                            "places_confirmed": self.terrain.places_confirmed,
                        },
                    )
                )
                if self.terrain.state == "failed":
                    self.failed_edges.add(
                        (self.terrain.plan.source, self.terrain.plan.target)
                    )
                    self._clear_terrain_forecast()
                else:
                    self.committed_step = self.terrain.plan.target
                    self.commit_started = observation["tick"]
                    self.own_footholds.update(self.terrain.plan.places)
                self.terrain = None
                self.search = None
                self.route.clear()
            return command
        if self.committed_step is not None:
            target = self.committed_step
            if (
                body.get("on_ground", True)
                and node == target
                and math.hypot(p[0] - target[0] - 0.5, p[2] - target[2] - 0.5) <= 0.35
                and abs(p[1] - target[1]) <= 0.2
            ):
                self.committed_step = None
                self.air_waypoint = None
                if not self.terrain_queue:
                    self._clear_terrain_forecast()
            elif observation["tick"] - self.commit_started > 80:
                self.reason = "confirmed terrain step movement deadline"
                self.mode = "terrain_motion_failed"
                self.committed_step = None
                self.air_waypoint = None
                self._clear_terrain_forecast()
                self.route.clear()
                self.search = None
                return {}
            else:
                self.mode = "terrain_step_motion"
                return self._move(
                    observation, [target[0] + 0.5, target[1], target[2] + 0.5]
                )
        if self.terrain_queue:
            self._begin_queued_step(node, observation)
            return {}
        if self.terrain_search is not None:
            self._plan_terrain_step(node, observation)
            return {}
        if (
            math.hypot(p[0] - self.goal[0] - 0.5, p[2] - self.goal[2] - 0.5)
            <= self.radius
            and abs(p[1] - self.goal[1]) <= 0.25
            and body.get("on_ground", True)
        ):
            self.mode = "arrived"
            return {}
        if not body.get("on_ground", True) and self.air_waypoint is not None:
            return self._move(observation, self.air_waypoint)
        self.air_waypoint = None
        if node in self.route:
            self.route = self.route[self.route.index(node) :]
        else:
            self.route.clear()
            if self.prefetch is not None and self.prefetch.start != node:
                self._discard_prefetch()
        if self.route and not all(
            standable(self._known_cells(), n) for n in self.route[:3]
        ):
            self.route.clear()
            self.search = None
            self._discard_prefetch()
        if (
            self.last_result is not None
            and self.last_result.status == "unreachable"
            and standable(self._known_cells(), self.goal)
            and math.hypot(node[0] - self.goal[0], node[2] - self.goal[2]) > 16
        ):
            # Acquisition may expose the distant goal while we follow an older
            # partial route. Refresh early enough to prefetch, rather than waiting
            # until that route's last few nodes to discover a complete path.
            self.route.clear()
            self.search = None
            self._discard_prefetch()
        if (
            self.terrain_policy is not None
            and self.last_result is not None
            and self.last_result.status == "unreachable"
            and 1 < len(self.route) <= 4
        ):
            self.route.clear()
            self.search = None
        if len(self.route) < 2:
            if self.prefetch is not None and self.prefetch.start == node:
                self.search, self.prefetch = self.prefetch, None
                self.prefetch_adopted += 1
                self.events.append(
                    (
                        "route_prefetch_adopted",
                        {"start": node, "goal_revision": self.goal_revision},
                    )
                )
            if self.search is None or self.search.start != node:
                revision, cells = self._planning_snapshot()
                self.search = self._new_search(cells, node, revision)
            self.last_result = self.search.advance(
                node_limit=self.search_nodes,
                budget_ms=self.search_ms,
            )
            self.route = list(self.last_result.route)
            self.reason = self.last_result.reason
            if (
                self.terrain_policy is not None
                and self.last_result.status == "searching"
            ):
                # Complete the bounded known-walking search before choosing an edit.
                # Following each provisional "closest" node can chase the hill's
                # perimeter forever and discard the search before it exhausts.
                self.route.clear()
                self.mode = "planning"
                return {}
            if (
                self.terrain_policy is not None
                and self.last_result.status == "unreachable"
                and self.route
                and (
                    len(self.route) <= 4 or self.route[-1][1] < node[1] <= self.goal[1]
                )
            ):
                # Do not immediately throw away a constructed height gain for a
                # closer-but-lower dead end when the next safe macro can extend it.
                self.route = [node]
            if len(self.route) < 2:
                self.mode = self.last_result.status
                if self.mode == "unreachable":
                    if self.terrain_policy is not None:
                        self._plan_terrain_step(node, observation)
                    # New acquisition may expose an edge next observation.
                    self.search = None
                return {}
        self.mode = "following"
        self._start_prefetch()
        command = self._follow_route(observation)
        if not command.get("forward") and abs(command.get("yaw_delta", 0)) <= 8:
            self.mode = "awaiting_fresh_geometry"
            self.route.clear()
            self.search = None
            self._discard_prefetch()
        return command
