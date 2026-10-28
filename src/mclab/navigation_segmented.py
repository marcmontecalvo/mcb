"""Development navigation: durable observations -> bounded search -> fresh motor.

Known walking routes precede bounded cut/foothold plans. The executor confirms
geometry before moving and never substitutes unconditional goal-directed digging.
"""

import math
import time
from collections import deque
from dataclasses import replace
from functools import partial

from mclab import collision_geometry, water_movement
from mclab.gap_jump import GapJumpExecutor, prepare_gap
from mclab.movement_policy import MovementPolicy
from mclab.navigation import NavigationController, empty, standable
from mclab.observation_worker import ObservationWorker
from mclab.pillar_steps import executor_for, prepare_pillar
from mclab.planning_worker import BackgroundSearch, PlanningWorker
from mclab.route_search import RouteSearch
from mclab.terrain_route import TerrainRouteSearch, known_step
from mclab.terrain_steps import prepare_step
from mclab.terrain_transport import ExpandedTerrain


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
        movement_policy=None,
    ):
        super().__init__(
            sensor_profile=memory.archive.identity.awareness.sensor_profile
        )
        self.memory = memory
        self.movement_policy = (
            MovementPolicy() if movement_policy is None else movement_policy
        )
        if not isinstance(self.movement_policy, MovementPolicy):
            raise ValueError("Invalid movement policy")
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
        self.gap_jump = None
        self.gap_jumps_completed = 0
        self.air_recovering = False
        self.air_recoveries_completed = 0
        self.water_controls = 0
        self.edits_broken = self.edits_placed = 0
        self.breaks_reserved = self.places_reserved = 0
        self.failed_edges = set()
        self.events = []
        self.committed_step = None
        self.committed_source = None
        self.motion_origin_height = None
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

    @property
    def pipeline_safe(self):
        """Plain walking only. Terrain, pillar, gap, committed steps, swimming and
        air recovery keep their validated one-command-at-a-time timing."""
        return (
            self.gap_jump is None
            and self.terrain is None
            and self.committed_step is None
            and self.air_waypoint is None
            and not self.terrain_queue
            and self.terrain_search is None
            and not self.air_recovering
            and not self.movement_policy.allow_swim
            and not self.mode.startswith(("gap", "terrain", "air"))
        )

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def cancel(self):
        self.goal_revision += 1
        if self.planner_lane is not None:
            self.planner_lane.cancel()
        self.prefetch = None
        self.gap_jump = None
        self.air_recovering = False
        if self.terrain is not None:
            self.edits_broken += self.terrain.breaks_confirmed
            self.edits_placed += self.terrain.places_confirmed
        self._clear_terrain_forecast()
        self.cancelled = True
        self.route.clear()
        self.search = None
        self.air_waypoint = None
        self.motion_origin_height = None
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
            "movement_policy": self.movement_policy.payload(),
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
                    "kind": s.kind,
                    "exit": s.exit,
                }
                for s in self.terrain_forecast
            ],
            "gap_jump_phase": self.gap_jump.phase if self.gap_jump else None,
            "gap_jumps_completed": self.gap_jumps_completed,
            "air_recovering": self.air_recovering,
            "air_recoveries_completed": self.air_recoveries_completed,
            "water_controls": self.water_controls,
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
        if isinstance(observation, ExpandedTerrain):
            self.motor_cells = observation.cell_index
            self.motor_sample = sample
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
        if factory is RouteSearch:
            factory = partial(
                RouteSearch,
                movement_policy=self.movement_policy,
                excluded=frozenset(self.failed_edges),
            )
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
        target = self.route[1]
        dx, dz = target[0] - start[0], target[2] - start[2]
        if (dx == 0 or dz == 0) and abs(dx) + abs(dz) > 1:
            plan = prepare_gap(
                self._fresh(observation["tick"]), start, target, self.movement_policy
            )
            if plan is None:
                self.mode = "awaiting_gap_geometry"
                self.reason = "gap preconditions changed or stale"
                self.route.clear()
                self.search = None
                return {}
            self.gap_jump = GapJumpExecutor(plan, self.movement_policy)
            self.mode = "gap_jump"
            self.events.append(
                (
                    "gap_jump_started",
                    {"source": start, "target": target, "width": plan.width},
                )
            )
            return self.gap_jump.decide(observation, self._fresh(observation["tick"]))
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

    def _safe_motion(self, observation, waypoint, forward, jumping):
        if not self.movement_policy.allow_shapes:
            return self._safe_full_motion(observation, waypoint, forward, jumping)
        body = observation["body"]
        px, py, pz = body["position"]
        cells = self._fresh(observation["tick"])
        origin = py if self.motion_origin_height is None else self.motion_origin_height
        target = (math.floor(waypoint[0]), waypoint[1], math.floor(waypoint[2]))
        if not collision_geometry.standable(cells, target):
            return False
        yaw = math.radians(body["yaw"])
        horizon = min(
            math.hypot(waypoint[0] - px, waypoint[2] - pz) + 0.05, 1.8 * forward
        )
        for fraction in (0, 0.25, 0.5, 0.75, 1):
            x, z = (
                px - math.sin(yaw) * horizon * fraction,
                pz + math.cos(yaw) * horizon * fraction,
            )
            support_heights = []
            radius = collision_geometry.BODY_RADIUS - collision_geometry.EPS
            for ox in (-radius, radius):
                for oz in (-radius, radius):
                    cx, cz = math.floor(x + ox), math.floor(z + oz)
                    heights = collision_geometry.surfaces(
                        cells,
                        cx,
                        cz,
                        origin - self.movement_policy.max_drop,
                        max(origin + 0.6, waypoint[1]),
                    )
                    admitted = [
                        h
                        for h in heights
                        if collision_geometry.clear_column(
                            cells, cx, cz, h, max(py, h) + 1.8 + (1.2 if jumping else 0)
                        )
                    ]
                    if not admitted:
                        return False
                    support_heights.append(max(admitted))
            height = max(support_heights)
            body_bottom = max(py, height)
            if not collision_geometry.clear_body(
                cells, x, z, body_bottom, body_bottom + 1.8 + (1.2 if jumping else 0)
            ):
                return False
        return True

    def _safe_full_motion(self, observation, waypoint, forward, jumping):
        body = observation["body"]
        px, py, pz = body["position"]
        if self.movement_policy.max_drop == 0:
            origin = (
                round(py)
                if self.motion_origin_height is None
                else self.motion_origin_height
            )
            fresh = self._fresh(observation["tick"])
            yaw = math.radians(body["yaw"])
            horizon = min(
                math.hypot(waypoint[0] - px, waypoint[2] - pz) + 0.05, 1.8 * forward
            )
            for fraction in (0, 0.25, 0.5, 0.75, 1):
                x, z = (
                    px - math.sin(yaw) * horizon * fraction,
                    pz + math.cos(yaw) * horizon * fraction,
                )
                for ox in (-0.29, 0.29):
                    for oz in (-0.29, 0.29):
                        if not any(
                            standable(
                                fresh, (math.floor(x + ox), y, math.floor(z + oz))
                            )
                            for y in range(
                                math.floor(origin), math.ceil(max(py, waypoint[1])) + 1
                            )
                        ):
                            return False
        drop = py - waypoint[1]
        if jumping or drop < 0.5:
            return super()._safe_motion(observation, waypoint, forward, jumping)
        if drop > self.movement_policy.max_drop + 0.1:
            return False
        cells = self._fresh(observation["tick"])
        target = (math.floor(waypoint[0]), round(waypoint[1]), math.floor(waypoint[2]))
        if not standable(cells, target):
            return False
        yaw = math.radians(body["yaw"])
        horizon = min(
            math.hypot(waypoint[0] - px, waypoint[2] - pz) + 0.05, 1.8 * forward
        )
        for fraction in (0, 0.25, 0.5, 0.75, 1):
            x, z = (
                px - math.sin(yaw) * horizon * fraction,
                pz + math.cos(yaw) * horizon * fraction,
            )
            for ox in (-0.29, 0.29):
                for oz in (-0.29, 0.29):
                    cx, cz = math.floor(x + ox), math.floor(z + oz)
                    if not any(
                        standable(cells, (cx, y, cz))
                        and all(
                            empty(cells.get((cx, cy, cz)))
                            for cy in range(y, math.ceil(py) + 2)
                        )
                        for y in range(target[1], math.floor(py) + 1)
                    ):
                        return False
        return True

    def _move(self, observation, waypoint):
        if self.movement_policy.allow_swim:
            cells = self._fresh(observation["tick"])
            body = observation["body"]
            target = (math.floor(waypoint[0]), waypoint[1], math.floor(waypoint[2]))
            if (
                body.get("survival", {}).get("in_water") is True
                or water_movement.surface_height(cells, body["position"]) is not None
                or water_movement.surface_node(cells, target)
            ):
                command = water_movement.swim_command(
                    cells, body, waypoint, self.movement_policy
                )
                if command.get("forward") or command.get("jump"):
                    self.water_controls += 1
                return command
        command = super()._move(observation, waypoint)
        body = observation["body"]
        if (
            command.get("forward", 0) > 0
            and body.get("on_ground", True)
            and (command.get("jump") or waypoint[1] < body["position"][1] - 0.25)
        ):
            self.motion_origin_height = body["position"][1]
        if command.get("forward", 0) > 0 and waypoint[1] < body["position"][1] - 0.25:
            self.air_waypoint = waypoint
        if (
            self.movement_policy.allow_sprint
            and command.get("forward", 0) >= 0.99
            and not command.get("jump")
            and body.get("on_ground", False)
            and body.get("food", 0) > 6
            and abs(waypoint[1] - body["position"][1]) < 0.1
            and math.hypot(
                waypoint[0] - body["position"][0], waypoint[2] - body["position"][2]
            )
            > 2
            and self._safe_motion(observation, waypoint, 1.3, False)
        ):
            command["sprint"] = True
        return command

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
                movement_policy=self.movement_policy,
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
                            "kind": s.kind,
                            "exit": s.exit,
                        }
                        for s in result.steps
                    ],
                },
            )
        )
        return result.steps[0], policy

    def _terrain_approach_nodes(self):
        # Normal walking can look ahead three nodes at once. Preserve enough
        # approach for a staircase's height gain before that lookahead skips
        # past its entry stance. The default one-block behavior stays at four.
        return max(4, self.terrain_policy.max_support_stack + 3)

    def _node_supported(self, cells, node):
        if self.movement_policy.allow_swim:
            return water_movement.supported_node(
                cells, node, shapes=self.movement_policy.allow_shapes
            )
        return (
            collision_geometry.standable
            if self.movement_policy.allow_shapes
            else standable
        )(cells, node)

    def _plan_terrain_step(self, node, observation):
        plan, policy = self._next_step(node, observation)
        if plan is not None:
            self.terrain = executor_for(plan, policy)
            self.events.append(
                (
                    "terrain_step_planned",
                    {
                        "source": plan.source,
                        "target": plan.target,
                        "breaks": plan.breaks,
                        "places": plan.places,
                        "kind": plan.kind,
                        "exit": plan.exit,
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
            actual = (prepare_pillar if plan.kind == "pillar" else prepare_step)(
                fresh,
                plan.source,
                plan.exit if plan.kind == "pillar" else plan.target,
                self.terrain_queue_policy,
                observation["body"].get("inventory", []),
                protected=self.own_footholds,
            )
            valid = (
                actual is not None
                and set(actual.breaks) == set(plan.breaks)
                and actual.places == plan.places
                and actual.kind == plan.kind
                and actual.exit == plan.exit
            )
        elif valid:
            valid = known_step(
                fresh, plan.source, plan.target, max_drop=self.movement_policy.max_drop
            )
        if not valid:
            self._clear_terrain_forecast()
            self.reason = "forecast preconditions changed; replanning"
            self.mode = "terrain_replanning"
            return
        self.terrain_queue.pop(0)
        self.terrain = executor_for(plan, self.terrain_queue_policy)
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
                    "kind": plan.kind,
                    "exit": plan.exit,
                    "remaining_forecast_moves": len(self.terrain_queue),
                },
            )
        )

    def decide(self, observation):
        began = time.perf_counter()
        cpu_began = time.thread_time_ns()
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
                        "motor_cpu_ms": (time.thread_time_ns() - cpu_began) / 1_000_000,
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
        if self.gap_jump is not None:
            jump = self.gap_jump
            command = jump.decide(observation, self._fresh(observation["tick"]))
            self.mode = "gap_" + jump.phase
            if jump.state in {"ready", "failed"}:
                if jump.state == "ready":
                    self.gap_jumps_completed += 1
                else:
                    self.failed_edges.add((jump.plan.source, jump.plan.target))
                self.reason = jump.reason
                self.events.append(
                    (
                        "gap_jump_finished",
                        {
                            "source": jump.plan.source,
                            "target": jump.plan.target,
                            "width": jump.plan.width,
                            "state": jump.state,
                            "reason": jump.reason,
                        },
                    )
                )
                self.gap_jump = None
                self.search = None
                self.route.clear()
                self.air_waypoint = None
                self.motion_origin_height = None
            return command
        p = body["position"]
        survival = body.get("survival", {})
        air, maximum_air = survival.get("air"), survival.get("max_air")
        if (
            self.movement_policy.allow_swim
            and type(air) is int
            and type(maximum_air) is int
            and maximum_air > 0
        ):
            if air < maximum_air * 0.5 and not self.air_recovering:
                self.air_recovering = True
                self.events.append(
                    ("air_recovery_started", {"tick": observation["tick"], "air": air})
                )
            if self.air_recovering:
                if air >= maximum_air * 0.9:
                    self.air_recovering = False
                    self.air_recoveries_completed += 1
                    self.events.append(
                        (
                            "air_recovery_finished",
                            {"tick": observation["tick"], "air": air},
                        )
                    )
                else:
                    self.mode = "air_recovery"
                    if survival.get("in_water") is True and water_movement.can_surface(
                        self._fresh(observation["tick"]), body
                    ):
                        self.water_controls += 1
                        return {"jump": True}
                    return {}
        height = (
            round(p[1] * 2) / 2 if self.movement_policy.allow_shapes else round(p[1])
        )
        if self.movement_policy.allow_swim:
            water_height = water_movement.surface_height(self._known_cells(), p)
            if water_height is not None:
                height = water_height
        node = (math.floor(p[0]), height, math.floor(p[2]))
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
                    self.committed_source = self.terrain.plan.source
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
                self.motion_origin_height = None
                if not self.terrain_queue:
                    self._clear_terrain_forecast()
            elif observation["tick"] - self.commit_started > 80:
                if self.committed_source is not None:
                    self.failed_edges.add((self.committed_source, target))
                self.reason = "confirmed terrain step movement deadline"
                self.mode = "terrain_motion_failed"
                self.committed_step = None
                self.air_waypoint = None
                self.motion_origin_height = None
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
        if self.air_waypoint is not None:
            distance = math.hypot(
                p[0] - self.air_waypoint[0], p[2] - self.air_waypoint[2]
            )
            height_debt = self.air_waypoint[1] - p[1]
            if not body.get("on_ground", True) or (
                (self.movement_policy.allow_shapes or height_debt < -0.2)
                and -self.movement_policy.max_drop - 0.1 <= height_debt <= 0.6
                and distance <= 1.5
                and (distance > 0.35 or abs(height_debt) > 0.2)
            ):
                # A slab can catch the body's leading edge before its center
                # enters the destination column. Finish the checked commitment.
                return self._move(observation, self.air_waypoint)
        self.air_waypoint = None
        self.motion_origin_height = None
        if node in self.route:
            self.route = self.route[self.route.index(node) :]
        else:
            self.route.clear()
            if self.prefetch is not None and self.prefetch.start != node:
                self._discard_prefetch()
        if self.route and not all(
            self._node_supported(self._known_cells(), n) for n in self.route[:3]
        ):
            self.route.clear()
            self.search = None
            self._discard_prefetch()
        if (
            self.last_result is not None
            and self.last_result.status == "unreachable"
            and self._node_supported(self._known_cells(), self.goal)
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
            and 1 < len(self.route) <= self._terrain_approach_nodes()
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
                    len(self.route) <= self._terrain_approach_nodes()
                    or self.route[-1][1] < node[1] <= self.goal[1]
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
