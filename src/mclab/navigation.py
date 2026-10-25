"""Candidate local-map navigation motor; no manifest or evaluator inputs.

The map contains only observed cells. Planning may use older observed geometry,
but controls revalidate immediate geometry against the current five-tick sensor
window. This profile supports ordinary full-cube ground and one-block changes.
"""

import heapq
import math

PROFILE = "local-map-navigation-v1"
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def delta(target, current):
    return (target - current + 180) % 360 - 180


def rotation(target, body, *, ground=False):
    dx, dz = target[0] - body["position"][0], target[2] - body["position"][2]
    yaw = math.degrees(math.atan2(-dx, dz))
    pitch = (
        0
        if ground
        else -math.degrees(
            math.atan2(target[1] - body["position"][1] - 1.62, math.hypot(dx, dz))
        )
    )
    return {
        "yaw_delta": max(-45, min(45, delta(yaw, body["yaw"]))),
        "pitch_delta": max(-45, min(45, pitch - body["pitch"])),
    }


def full_cube(cell):
    return (
        cell is not None
        and cell.get("fluid", "minecraft:empty") == "minecraft:empty"
        and any(box == [0, 0, 0, 1, 1, 1] for box in cell.get("collision_boxes", []))
    )


def empty(cell):
    if cell is None or cell.get("fluid", "minecraft:empty") != "minecraft:empty":
        return False
    return cell.get("block") == "minecraft:air" or cell.get("collision_boxes") == []


def standable(cells, node):
    x, y, z = node
    return full_cube(cells.get((x, y - 1, z))) and all(
        empty(cells.get((x, cy, z))) for cy in (y, y + 1)
    )


def walkable_nodes(cells):
    return {
        (x, y + 1, z)
        for (x, y, z), cell in cells.items()
        if full_cube(cell) and standable(cells, (x, y + 1, z))
    }


def neighbors(cells, nodes, node):
    x, y, z = node
    for dx, dz in DIRECTIONS:
        for height in (0, 1, -1):
            candidate = (x + dx, y + height, z + dz)
            if candidate not in nodes:
                continue
            if height == 1 and not all(
                empty(cells.get((cx, cy, cz)))
                for cx, cz, low in ((x, z, y), (x + dx, z + dz, y + 1))
                for cy in range(low + 2, y + 4)
            ):
                continue
            if height == -1 and not empty(cells.get((x + dx, y + 1, z + dz))):
                continue
            yield candidate, 1.4 if height else 1.0


def path_to(cells, start, goals):
    """Shortest known cardinal path; unknown cells never supply support/air."""
    nodes = walkable_nodes(cells)
    if start not in nodes or not goals:
        return []
    goals = set(goals) & nodes
    if not goals:
        return []
    costs, previous = paths_from(cells, nodes, start, goals)
    reached = min(goals & costs.keys(), key=costs.get, default=None)
    return reconstruct(previous, reached) if reached is not None else []


def reconstruct(previous, node):
    route = [node]
    while node in previous:
        node = previous[node]
        route.append(node)
    return list(reversed(route))


def paths_from(cells, nodes, start, goals=()):
    frontier = [(0.0, start)]
    costs, previous = {start: 0.0}, {}
    while frontier:
        cost, node = heapq.heappop(frontier)
        if cost != costs[node]:
            continue
        if node in goals:
            break
        for candidate, edge_cost in neighbors(cells, nodes, node):
            total = cost + edge_cost
            if total < costs.get(candidate, math.inf):
                costs[candidate], previous[candidate] = total, node
                heapq.heappush(frontier, (total, candidate))
    return costs, previous


class NavigationController:
    """Discover, explore, route, aim, mine and collect from permitted facts."""

    def __init__(self):
        self.cells = {}
        self.target = None
        self.drop = None
        self.broken = False
        self.mining = False
        self.visits = {}
        self.last_tick = None
        self.last_plan = []
        self.mode = "exploring"
        self.air_waypoint = None
        self.explore_heading = None

    def _observe(self, observation):
        tick = observation["tick"]
        visible = observation.get("visible_blocks", [])
        for cell in visible:
            self.cells[tuple(cell["position"])] = {
                **cell,
                "observed_tick": cell.get("observed_tick", tick),
            }
        body = observation["body"]
        if self.explore_heading is None:
            self.explore_heading = math.radians(body["yaw"])
        position = body["position"]
        logs = [
            tuple(c["position"]) for c in visible if c["block"] == "minecraft:oak_log"
        ]
        if self.target is None and logs:
            self.target = min(
                logs,
                key=lambda p: sum((p[i] + 0.5 - position[i]) ** 2 for i in range(3)),
            )
        if self.mining and self.target is not None:
            seen = next(
                (c for c in visible if tuple(c["position"]) == self.target), None
            )
            if seen is not None and seen["block"] == "minecraft:air":
                self.broken = True
        drops = [
            e
            for e in observation.get("visible_entities", [])
            if e.get("item") == "minecraft:oak_log"
        ]
        if drops:
            self.drop = list(
                min(
                    drops,
                    key=lambda e: sum(
                        (a - b) ** 2
                        for a, b in zip(e["position"], position, strict=True)
                    ),
                )["position"]
            )
        if self.last_tick != tick:
            node = (
                math.floor(position[0]),
                round(position[1]),
                math.floor(position[2]),
            )
            self.visits[node] = self.visits.get(node, 0) + 1
            self.last_tick = tick

    def _fresh(self, tick):
        return {
            p: c for p, c in self.cells.items() if 0 <= tick - c["observed_tick"] <= 5
        }

    def _unseen_floor(self, x, y, z, radius=8, step=2):
        # ponytail: sampled every 2 blocks (81 probes per node); exact counting
        # only if exploration needs finer ranking.
        # A column counts as unseen when neither this floor level nor the one
        # below it was observed, so ledge-top nodes still value the lower floor.
        return sum(
            (x + dx, y - 1, z + dz) not in self.cells
            and (x + dx, y - 2, z + dz) not in self.cells
            for dx in range(-radius, radius + 1, step)
            for dz in range(-radius, radius + 1, step)
        )

    def _frontier(self, nodes, start):
        choices = []
        if start not in nodes:
            return []
        costs, previous = paths_from(self.cells, nodes, start)
        for node in nodes:
            if node == start or node not in costs:
                continue
            x, y, z = node
            unknown = sum(
                any((x + dx, cy, z + dz) not in self.cells for cy in (y - 1, y, y + 1))
                for dx, dz in DIRECTIONS
            )
            if unknown:
                # One shared search keeps planning bounded as observed memory
                # grows. Visitation penalizes views that revealed nothing new.
                # Prefer views that reveal unexplored floor, not just the nearest
                # edge of memory; nearest-edge alone walks the arena perimeter.
                gain = self._unseen_floor(x, y, z)
                score = (
                    costs[node]
                    + self.visits.get(node, 0) * 3
                    - unknown * 0.2
                    - gain * 0.3
                )
                score -= (
                    (node[0] - start[0]) * -math.sin(self.explore_heading)
                    + (node[2] - start[2]) * math.cos(self.explore_heading)
                ) * 0.05
                if self.target is not None:
                    score += (
                        math.hypot(node[0] - self.target[0], node[2] - self.target[2])
                        * 0.2
                    )
                choices.append((score, node))
        return reconstruct(previous, min(choices)[1]) if choices else []

    def _route(self, body):
        position = body["position"]
        nodes = walkable_nodes(self.cells)
        start = (math.floor(position[0]), round(position[1]), math.floor(position[2]))
        if start not in nodes:
            nearby = [
                n
                for n in nodes
                if abs(n[0] + 0.5 - position[0]) <= 1
                and abs(n[2] + 0.5 - position[2]) <= 1
                and abs(n[1] - position[1]) <= 1
            ]
            if not nearby:
                return []
            start = min(
                nearby,
                key=lambda n: sum(
                    (a - b) ** 2
                    for a, b in zip(
                        (n[0] + 0.5, n[1], n[2] + 0.5), position, strict=True
                    )
                ),
            )
        destination = self.drop or (
            [p + 0.5 for p in self.target] if self.broken else None
        )
        if destination is not None:
            goals = {
                n
                for n in nodes
                if math.hypot(n[0] + 0.5 - destination[0], n[2] + 0.5 - destination[2])
                < 0.8
            }
        elif self.target is not None:
            goals = {
                n
                for n in nodes
                if math.hypot(n[0] - self.target[0], n[2] - self.target[2]) <= 2.8
                and n[:1] + n[2:] != self.target[:1] + self.target[2:]
            }
        else:
            goals = set()
        route = path_to(self.cells, start, goals)
        if route and len(route) > 1:
            return route
        return self._frontier(nodes, start)

    def _safe_motion(self, observation, waypoint, forward, jumping):
        cells = self._fresh(observation["tick"])
        body = observation["body"]
        px, py, pz = body["position"]
        target_node = (
            math.floor(waypoint[0]),
            round(waypoint[1]),
            math.floor(waypoint[2]),
        )
        if not standable(cells, target_node):
            return False
        yaw = math.radians(body["yaw"])
        distance = math.hypot(waypoint[0] - px, waypoint[2] - pz)
        horizon = min(distance + 0.05, 1.8 * forward)
        for fraction in (0, 0.25, 0.5, 0.75, 1):
            x, z = (
                px - math.sin(yaw) * horizon * fraction,
                pz + math.cos(yaw) * horizon * fraction,
            )
            for ox in (-0.29, 0.29):
                for oz in (-0.29, 0.29):
                    cx, cz = math.floor(x + ox), math.floor(z + oz)
                    base = round(py)
                    if jumping:
                        # A one-block step can occupy feet level during takeoff;
                        # its top is permitted only with clear jumping headroom.
                        support_levels = [
                            y
                            for y in (base - 1, base, base + 1)
                            if full_cube(cells.get((cx, y - 1, cz)))
                            and all(
                                empty(cells.get((cx, cy, cz)))
                                for cy in (y, y + 1, y + 2)
                            )
                        ]
                    else:
                        support_levels = [
                            y for y in (base, base - 1) if standable(cells, (cx, y, cz))
                        ]
                    if not support_levels:
                        return False
        return True

    def _move(self, observation, waypoint):
        body = observation["body"]
        turn = rotation(waypoint, body, ground=True)
        distance = math.hypot(
            waypoint[0] - body["position"][0], waypoint[2] - body["position"][2]
        )
        if abs(turn["yaw_delta"]) > 8:
            return turn
        forward = min(0.7, max(0.12, distance / 1.8))
        jumping = waypoint[1] > body["position"][1] + 0.25
        if self._safe_motion(observation, waypoint, forward, jumping):
            if jumping:
                self.air_waypoint = waypoint
            return {**turn, "forward": forward, "jump": jumping}
        return turn

    def decide(self, observation):
        if observation.get("sensor_profile") != "local-visibility-v1":
            raise ValueError("Unsupported navigation sensor profile")
        self._observe(observation)
        body = observation["body"]
        if any(
            i.get("item") == "minecraft:oak_log" and i.get("count", 0) > 0
            for i in body.get("inventory", [])
        ):
            self.mode = "collected"
            return {}
        if not body.get("on_ground", True) and self.air_waypoint is not None:
            self.mode = "jumping"
            return self._move(observation, self.air_waypoint)
        self.air_waypoint = None
        if self.target is not None and not self.broken:
            center = [p + 0.5 for p in self.target]
            visible = any(
                tuple(c["position"]) == self.target
                and c["block"] == "minecraft:oak_log"
                for c in observation.get("visible_blocks", [])
            )
            reach = math.sqrt(
                sum(
                    (a - b) ** 2
                    for a, b in zip(
                        center,
                        [
                            body["position"][0],
                            body["position"][1] + 1.62,
                            body["position"][2],
                        ],
                        strict=True,
                    )
                )
            )
            if visible and reach <= 4.2:
                self.mode = "mining"
                turn = rotation(center, body)
                ray = body.get("ray", {})
                mine = (
                    ray.get("block") == "minecraft:oak_log"
                    and tuple(ray.get("position", ())) == self.target
                )
                self.mining = self.mining or mine
                return {
                    **turn,
                    "mine": mine
                    and abs(turn["yaw_delta"]) < 2
                    and abs(turn["pitch_delta"]) < 2,
                }
        self.mode = (
            "collecting"
            if self.broken or self.drop
            else "approaching"
            if self.target
            else "exploring"
        )
        self.last_plan = self._route(body)
        if not self.last_plan:
            return {"yaw_delta": 30}
        waypoint = self.last_plan[1] if len(self.last_plan) > 1 else self.last_plan[0]
        return self._move(
            observation, [waypoint[0] + 0.5, waypoint[1], waypoint[2] + 0.5]
        )
