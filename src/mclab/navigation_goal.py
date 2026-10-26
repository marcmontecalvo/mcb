"""V8 navigation retargeted at a fixed goal point, for natural-terrain development runs.

Reuses V8's observed-cell pathing, frontier exploration and motor checks unchanged.
Differences: the target is a goal coordinate (never a log), exploration leans toward
it, and when progress stalls the controller digs a tunnel or staircase step toward
the goal. It cannot place blocks or fight. Not a qualified controller.
"""

import math

from mclab.navigation import (
    NavigationController,
    empty,
    path_to,
    rotation,
    walkable_nodes,
)

STUCK_DECISIONS = 30  # ~150 ticks at one decision per 5-tick command
PROGRESS_BLOCKS = 1.0
# ponytail: forget far cells so per-decision planning fits the 3-tick action offset;
# a longer memory needs incremental path search, not a bigger box.
MEMORY_RADIUS = 20
MEMORY_HEIGHT = 12
REACH = 4.2


def _node(position):
    return (math.floor(position[0]), round(position[1]), math.floor(position[2]))


class GoalController(NavigationController):
    def __init__(self, goal, radius=3):
        super().__init__()
        self.goal = tuple(goal)
        self.radius = radius
        self.best = math.inf
        self.stalled = 0
        self.mode = "exploring"

    def drain_events(self):
        return []

    def _observe(self, observation):
        super()._observe(observation)
        # V8 locks onto the nearest visible oak log; a goal run keeps its target.
        self.target, self.drop, self.broken, self.mining = self.goal, None, False, False
        x, _, z = observation["body"]["position"]
        self.explore_heading = math.atan2(
            -(self.goal[0] + 0.5 - x), self.goal[2] + 0.5 - z
        )
        y = observation["body"]["position"][1]
        self.cells = {
            p: c
            for p, c in self.cells.items()
            if abs(p[0] - x) <= MEMORY_RADIUS
            and abs(p[2] - z) <= MEMORY_RADIUS
            and abs(p[1] - y) <= MEMORY_HEIGHT
        }

    def _route(self, body):
        nodes = walkable_nodes(self.cells)
        start = _node(body["position"])
        if start not in nodes:
            return []
        goals = {
            n
            for n in nodes
            if math.hypot(n[0] - self.goal[0], n[2] - self.goal[2]) <= self.radius - 1
            and abs(n[1] - self.goal[1]) <= 3
        }
        return path_to(self.cells, start, goals, nodes=nodes) or self._frontier(
            nodes, start
        )

    def decide(self, observation):
        body = observation["body"]
        x, y, z = body["position"]
        distance = math.hypot(self.goal[0] + 0.5 - x, self.goal[2] + 0.5 - z)
        if distance <= self.radius - 0.5 and abs(y - self.goal[1]) <= 3:
            self.mode = "arrived"
            return {}
        if distance < self.best - PROGRESS_BLOCKS:
            self.best, self.stalled = distance, 0
        else:
            self.stalled += 1
        if self.stalled >= STUCK_DECISIONS:
            self._observe(observation)
            return self._dig(observation)
        # Logs picked up while digging must not trigger V8's "collected" stop.
        inventory = [
            i for i in body.get("inventory", []) if i.get("item") != "minecraft:oak_log"
        ]
        return super().decide({**observation, "body": {**body, "inventory": inventory}})

    def _step(self, node):
        dx, dz = self.goal[0] - node[0], self.goal[2] - node[2]
        if abs(dx) >= abs(dz):
            return int(math.copysign(1, dx)), 0
        return 0, int(math.copysign(1, dz))

    def _dig(self, observation):
        """Clear a tunnel (or a staircase step up) one block toward the goal."""
        self.mode = "digging"
        body = observation["body"]
        node = _node(body["position"])
        sx, sz = self._step(node)
        front = (node[0] + sx, node[1], node[2] + sz)
        climb = self.goal[1] > node[1] + 1 and not empty(self.cells.get(front))
        if climb:
            clear = [
                (node[0], node[1] + 2, node[2]),
                (front[0], node[1] + 2, front[2]),
                (front[0], node[1] + 1, front[2]),
            ]
        else:
            clear = [(front[0], node[1] + 1, front[2]), front]
        ray = body.get("ray") or {}
        eye = (body["position"][0], body["position"][1] + 1.62, body["position"][2])
        for cell in clear:
            if empty(self.cells.get(cell)) and cell in self.cells:
                continue
            center = [cell[0] + 0.5, cell[1] + 0.5, cell[2] + 0.5]
            if math.dist(center, eye) > REACH:
                break
            turn = rotation(center, body)
            aligned = abs(turn["yaw_delta"]) < 3 and abs(turn["pitch_delta"]) < 3
            # Anything the ray hits on the way to this cell is also in the way.
            hit = ray.get("block") not in (None, "minecraft:air")
            if aligned and not hit:
                # Looking straight at it and nothing is there: treat it as cleared.
                self.cells[cell] = {
                    "position": list(cell),
                    "block": "minecraft:air",
                    "observed_tick": observation["tick"],
                }
                continue
            return {**turn, "mine": aligned and hit}
        target = [front[0] + 0.5, front[1] + (1 if climb else 0), front[2] + 0.5]
        return {**rotation(target, body, ground=True), "forward": 1.0, "jump": climb}
