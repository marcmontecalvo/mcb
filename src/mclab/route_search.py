"""Resumable development A* over immutable observed geometry.

Search slices and output segments are bounded independently. Search can move
away from the goal to find a detour. Partial results contain only checked edges;
unknown cells are never invented as air. The motor must revalidate local cells.

Step C planner core (D11), each part behind its own TerrainPolicy flag:

- ``planner_tick_costs``: edge costs are game ticks (D2), reimplemented from
  Baritone's ActionCosts (D12), and break time follows block hardness and tool.
- ``planner_unknown``: a known node next to unknown space may end the search,
  charging ``unknown_penalty`` x walking time for its remaining distance (D3).
  The route itself still contains only known, checked cells.
- ``planner_edits``: cut-steps and footholds (``prepare_step``) compete with
  walking in this search (D4). Each edit edge carries its StepPlan; the
  controller executes and confirms it with the existing step executor.
"""

import functools
import gc
import heapq
import math
import time
from dataclasses import dataclass

from mclab import collision_geometry, route_moves, water_movement
from mclab.gap_jump import gap_plan
from mclab.grid_cache import EMPTY, FULL, KNOWN, LAVA, SOLID, Grid, OverlayGrid
from mclab.movement_policy import MovementPolicy
from mclab.navigation import DIRECTIONS, empty, reconstruct, standable
from mclab.terrain_steps import (
    BREAK_AIM_TICKS,
    BREAK_EXTRA_TICKS,
    EDIT_STEP_TICKS,
    PLACE_TICKS,
    Inventory,
    break_ticks,
    excavatable,
    falling_stack,
    plan_edit,
)

# Baritone ActionCosts (api/pathing/movement/ActionCosts.java), reimplemented.
WALK = 20 / 4.317  # WALK_ONE_BLOCK_COST
SPRINT = 20 / 5.612  # SPRINT_ONE_BLOCK_COST
SWIM = 20 / 2.2  # D2
WALK_OFF = WALK * 0.8  # WALK_OFF_BLOCK_COST
CENTER_AFTER_FALL = WALK - WALK_OFF  # CENTER_AFTER_FALL_COST


def fall_ticks(distance):
    """ActionCosts.distanceToTicks: ticks to fall a distance from rest."""
    if distance == 0:
        return 0.0
    remaining, tick = distance, 0
    while True:
        speed = (0.98**tick - 1) * -3.92  # ActionCosts.velocity
        if remaining <= speed:
            return tick + remaining / speed
        remaining -= speed
        tick += 1


JUMP = fall_ticks(1.25) - fall_ticks(0.25)  # JUMP_ONE_BLOCK_COST
# Baritone Settings / search constants, reimplemented (D12).
COST_HEURISTIC = 3.563  # GoalXZ per-block heuristic factor
JUMP_PENALTY = 2.0  # Settings.jumpPenalty
FAVOR = 0.5  # Settings.backtrackCostFavoringCoefficient
CUTOFF_FACTOR, CUTOFF_MIN = 0.9, 30  # pathCutoffFactor, pathCutoffMinimumLength
COEFFICIENTS = (1.5, 2, 2.5, 3, 4, 5, 10)  # AbstractNodeCostSearch.COEFFICIENTS
MIN_DIST_PATH = 5  # AbstractNodeCostSearch.MIN_DIST_PATH
SQRT2 = math.sqrt(2)
NEAR_REFRESH = (8, 4)  # mcb-body AwarenessSensor near box: horizontal, vertical
STACK_PLACE_TICKS = 20.0  # MCB-measured, run 20261030T015237755787Z
# MCB-measured, run 20261030T020748319789Z: side-anchored bridge placements took
# ~55 ticks per block live (R3, R5 valley bridges) against ~39 modelled.
BRIDGE_PLACE_TICKS = 15.0
EDIT_CHAIN_TICKS = 10.0  # extra ticks per earlier edit on the same route
# One staircase step out of buried ground: an edit step breaking two stone
# blocks, then the ascend (live dig-outs ran ~90 ticks per block).
DIG_RISE = (
    EDIT_STEP_TICKS
    + 2 * (break_ticks("minecraft:stone") + BREAK_EXTRA_TICKS + BREAK_AIM_TICKS)
    + max(JUMP, WALK)
)


@dataclass(frozen=True)
class RouteResult:
    status: str
    route: tuple
    map_revision: int
    expanded: int
    reason: str
    steps: tuple = ()  # StepPlans for planned edit edges on this route


@functools.cache
def _break_cost(block):
    return break_ticks(block) + BREAK_EXTRA_TICKS + BREAK_AIM_TICKS


class RouteSearch:
    def __init__(
        self,
        cells,
        start,
        goal,
        *,
        map_revision,
        goal_radius=0,
        owned_snapshot=False,
        movement_policy=None,
        excluded=(),
        terrain_policy=None,
        inventory=(),
        protected=(),
        favored=(),
    ):
        # Caller hands an independent snapshot; retain our own nested copy.
        import copy

        self.cells = cells if owned_snapshot else copy.deepcopy(cells)
        self.grid = Grid(self.cells)  # memoised view; the snapshot stays unmutated
        self.cut_memo = {}  # point -> may be broken, valid for the base grid
        self._ovl = {}  # node -> OverlayGrid of that node's planned edits
        self.start, self.goal = tuple(start), tuple(goal)
        self.revision = map_revision
        if len(self.start) != 3 or len(self.goal) != 3 or goal_radius < 0:
            raise ValueError("Invalid route endpoints")
        self.radius = goal_radius
        self.movement_policy = (
            MovementPolicy() if movement_policy is None else movement_policy
        )
        if not isinstance(self.movement_policy, MovementPolicy):
            raise ValueError("Invalid movement policy")
        self.excluded = frozenset(excluded)
        self.policy = terrain_policy
        self.ticks = bool(terrain_policy and terrain_policy.planner_tick_costs)
        self.unknown = bool(terrain_policy and terrain_policy.planner_unknown)
        self.edits = bool(
            terrain_policy
            and terrain_policy.planner_edits
            and (terrain_policy.allow_break or terrain_policy.allow_place)
        )
        self.bs = bool(terrain_policy and terrain_policy.baritone_search)
        self.bdiag = bool(terrain_policy and terrain_policy.baritone_diagonals)
        self.moves = bool(terrain_policy and terrain_policy.baritone_moves)
        self.pswim = bool(terrain_policy and terrain_policy.planner_swim)
        self.favored = frozenset(favored) if self.bs else frozenset()
        # Per coefficient: (estimate + cost / coefficient, node) minimum so far.
        self.coef_best = [(math.inf, None)] * len(COEFFICIENTS)
        self.inventory = Inventory(dict(r) for r in inventory)
        self.protected = frozenset(protected)
        self._protected_all = set(self.protected) | set(
            terrain_policy.protected_positions if terrain_policy else ()
        )
        self._tools = excavatable(terrain_policy) if terrain_policy else {}
        self._heights = {}
        self._unknown_memo = {}
        self._gap_xz = all(
            type(v) in (int, float) and math.isfinite(v) and int(v) == v
            for v in (self.start[0], self.start[2])
        )
        # Per node: the edit plan of the edge that reached it (its cells are built
        # lazily by _view). Later edges from that node see those edits (ponytail:
        # only the last edge's edits, not the whole path's; execution re-checks fresh).
        self.plans, self.overlay = {}, {}
        self.edit_depth = {}  # edit edges on the best path to each node
        self.frontier = [(self._h(self.start), 0.0, 0, self.start)]  # 1 = frontier exit
        self.costs, self.previous = {self.start: 0.0}, {}
        self.expanded = 0
        self.best = self.start
        # Cheapest exit into unknown space seen so far: (estimated total, node).
        # A capped or budget-cut search follows it instead of the closest node.
        self.exit = (math.inf, None)
        self.terminal = None
        self._standable = (
            collision_geometry.standable
            if self.movement_policy.allow_shapes
            else standable
        )
        if self.movement_policy.allow_swim:
            self._standable = lambda cells, node: water_movement.supported_node(
                cells, node, shapes=self.movement_policy.allow_shapes
            )
        if not self._standable(self.grid, self.start):
            self.frontier.clear()
            self.terminal = RouteResult(
                "unreachable", (), self.revision, 0, "start is not known standable"
            )

    def _distance(self, node):
        # Distance to the horizontal goal disk, with exact standing height.
        return max(
            max(
                0,
                math.hypot(node[0] - self.goal[0], node[2] - self.goal[2])
                - self.radius,
            ),
            abs(node[1] - self.goal[1]),
        )

    def _h(self, node):
        weight = self.policy.heuristic_weight if self.policy else 0
        if self.unknown and not weight:
            # Weighted A*: price remaining distance like unknown space, so known
            # detours and frontier exits compete on one scale. Without this the
            # search sweeps every cell cheaper than the best frontier, including
            # deep digging (R5 replay: 23,666 expansions, 13 s). Routes cost at
            # most this weight / sprint rate (~2.6x) more than optimal. Live,
            # these routes wandered (run 20261030T013611935507Z: R2 345 blocks
            # for 114); heuristic_weight 1 is plain A* with an expansion cap.
            return self._remaining(node, self.policy.unknown_penalty)
        return self._remaining(node, weight or 1, rate=SPRINT)

    def _closer(self, node, other):
        """Best partial route end: Baritone-style estimate under tick costs."""
        if self.ticks:
            return self._remaining(node, 1) < self._remaining(other, 1)
        return self._distance(node) < self._distance(other)

    def _remaining(self, node, weight, rate=None, exit_estimate=False, buried=False):
        """Estimated cost to the goal, scaled by weight.

        Tick costs use Baritone's GoalBlock form: horizontal plus a separate
        vertical term (GoalYLevel: JUMP_ONE_BLOCK_COST per block up, half of
        FALL_N_BLOCKS_COST[2] per block down). The legacy max(horizontal,
        vertical) made a deep drop toward the goal look free; live runs then
        fell into ravines and dug back out a block per ~90 ticks (Step C run
        20261030T011203175248Z: R2 to y=36 for a y=68 goal, R3 to y=57).
        """
        if not self.ticks:
            return weight * self._distance(node)
        if self.bs:  # GoalXZ.calculate: octile distance (straight + diagonal*sqrt2)
            ax, az = abs(node[0] - self.goal[0]), abs(node[2] - self.goal[2])
            horizontal = max(0, abs(ax - az) + min(ax, az) * SQRT2 - self.radius)
        else:
            horizontal = max(
                0,
                math.hypot(node[0] - self.goal[0], node[2] - self.goal[2])
                - self.radius,
            )
        rise = self.goal[1] - node[1]
        vertical = rise * JUMP if rise > 0 else -rise * fall_ticks(2) / 2
        if rate is None:
            rate = WALK if self.unknown else SPRINT
        if self.bs and rate == SPRINT:
            rate = COST_HEURISTIC  # exit estimates keep their WALK rate
        if buried and rise > 0:
            vertical = rise * DIG_RISE
        elif exit_estimate and rise > horizontal:
            # Climbing more than the horizontal distance needs extra ascend moves,
            # each a block of walking too. Without this a cave exit right under
            # the goal looked cheap (run 20261030T033614954664Z R3: down to y=43,
            # 63 blocks dug). Exit estimates only; the A* heuristic is Baritone's.
            vertical += (rise - horizontal) * WALK
        return weight * (rate * horizontal + vertical)

    # Edge costs: legacy unitless values, or Baritone-derived ticks.
    def _flat(self, diagonal=False):
        if not self.ticks:
            return math.sqrt(2) if diagonal else 1
        cost = SPRINT if self.movement_policy.allow_sprint else WALK
        return cost * (math.sqrt(2) if diagonal else 1)

    def _height(self, dy):
        cached = self._heights.get(dy)
        if cached is None:
            cached = self._heights[dy] = self._height_cost(dy)
        return cached

    def _height_cost(self, dy):
        if not self.ticks:
            return 1 + 0.4 * abs(dy)
        if dy > 0.5:  # MovementAscend (+ jumpPenalty)
            return max(JUMP, WALK) + self._jump_penalty()
        if dy < -0.5:  # MovementDescend / MovementFall
            return WALK_OFF + max(fall_ticks(-dy), CENTER_AFTER_FALL)
        return self._flat()

    def _jump_penalty(self):
        return JUMP_PENALTY if self.bs and self.ticks else 0.0

    def _gap(self, width):
        # MovementParkour.costFromJumpDistance for a jump of width + 1 blocks.
        if not self.ticks:
            return width + 3.5
        return WALK * (width + 1) + self._jump_penalty()

    def _edit_cost(self, plan, dy, view, base=None):
        if not self.ticks:
            policy = self.policy
            return (
                (1.4 if dy else 1)
                + (policy.break_penalty + policy.tool_wear_penalty) * len(plan.breaks)
                + policy.placement_penalty * len(plan.places)
            )
        get = view.get
        breaks = plan.breaks
        breaking = sum(_break_cost(get(p)["block"]) for p in breaks)
        if self.moves and breaks:  # getMiningDurationTicks includeFalling
            breaking += sum(
                break_ticks(get(q)["block"]) + BREAK_EXTRA_TICKS
                for p in breaks
                for q in falling_stack(view, p)
            )
        return (
            (self._height(dy) if base is None else base)
            + EDIT_STEP_TICKS
            + breaking
            + PLACE_TICKS * len(plan.places)
            # Measured: a stacked second placement needs an edge approach and
            # sneak (~80-90 live ticks for two against ~60 modelled).
            + STACK_PLACE_TICKS * max(0, len(plan.places) - 1)
            + BRIDGE_PLACE_TICKS * sum(self._bridged(p, view) for p in plan.places)
        )

    def _chain(self, node):
        # Resource/risk term (D2): each further edit on one route costs more.
        return EDIT_CHAIN_TICKS * self.edit_depth.get(node, 0) if self.ticks else 0

    def _priced(self, plan, dy, view, node, base=None):
        return self._edit_cost(plan, dy, view, base) + self._chain(node)

    @staticmethod
    def _bridged(point, view):
        """A placement with nothing under it is anchored on a side face."""
        return not view.cflags((point[0], point[1] - 1, point[2])) & SOLID

    def _view(self, node):
        plan = self.overlay.get(node)
        if plan is None:
            return self.grid
        entry = self._ovl.get(node)
        if entry is None or entry[0] is not plan:
            # Built on first expansion: most generated edges are never expanded.
            entry = self._ovl[node] = (
                plan,
                OverlayGrid(self._changes(plan), self.grid),
            )
        return entry[1]

    def _is_frontier(self, view, node):
        """A known node whose possible moves read unknown cells.

        Cells in the body's always-refreshed box around the search start
        (+-8 horizontal, +-4 vertical; AwarenessSensor near refresh) are not
        frontier: they are observed within one update, before the body could
        get there. Counting them made exits flip between two adjacent nodes on
        every refresh (Step C run 20261030T002908756029Z, R1 and R2).
        """
        x, y, z = node
        drop = max(self.movement_policy.max_drop, 1 if self.moves else 0)
        low = math.floor(y) - drop - 1
        high = math.floor(y) + (4 if self.moves else 3)  # pillar reads y+3
        memo = self._unknown_memo
        for dx, dz in DIRECTIONS:
            key = (x + dx, z + dz, low, high)
            unknown = memo.get(key)
            if unknown is None:
                unknown = memo[key] = self._column_unknown(*key)
            if unknown:
                return True
        return False

    def _column_unknown(self, cx, cz, low, high):
        """Unknown cell in a neighbour column outside the always-refreshed box.

        Planned edits only change known cells, so the base grid answers for
        every overlay too; the answer is shared by the nodes beside the column.
        """
        sx, sy, sz = self.start
        flags = self.grid.cflags
        near = abs(cx - sx) <= NEAR_REFRESH[0] and abs(cz - sz) <= NEAR_REFRESH[0]
        floor_sy = math.floor(sy)
        for cy in range(low, high):
            if near and abs(cy - floor_sy) <= NEAR_REFRESH[1]:
                continue
            if not flags((cx, cy, cz)) & KNOWN:
                return True
        return False

    def _buried(self, view, node):
        """Known solid cover over node, with no known air below goal height.

        The rise out of such a node is a dig-out, not a walk: R3 live followed
        cave exits under 15+ blocks of terracotta to y=39 and spent its whole
        break budget digging back up (run 20261030T040009493308Z, rep 2). An
        overhang with open air above it is not buried.
        ponytail: one column only; a thick overhang reaching goal height counts.
        """
        x, z = node[0], node[2]
        covered = False
        for y in range(math.floor(node[1]) + 2, math.floor(self.goal[1])):
            cell = view.get((x, y, z))
            if cell is None:
                continue
            if cell.get("collision_boxes"):
                covered = True
            elif covered:
                return False
        return covered

    def _edges(self, node):
        for target, cost, _ in self._moves(node):
            yield target, cost

    def _moves(self, node):
        """(target, cost, StepPlan or None) for every checked edge from node."""
        x, y, z = node
        cells = self._view(node)
        flags = cells.cflags
        if self.moves and route_moves.is_climb_only(cells, node):
            yield from route_moves.extra_moves(self, cells, node)
            return
        mpolicy = self.movement_policy
        max_gap, allow_sprint = mpolicy.max_gap, mpolicy.allow_sprint
        # prepare_gap accepts only integral coordinates (x, z fixed per search).
        gap_room = bool(
            self._gap_xz and y == math.floor(y) and flags((x, y - 1, z)) & FULL
        )
        # prepare_step needs a full-block stance on a standable source.
        edit_ready = self.edits and y == math.floor(y) and standable(cells, node)
        for dx, dz in DIRECTIONS:
            reached = set()
            if self.movement_policy.allow_swim:
                target = (x + dx, y, z + dz)
                if (node, target) not in self.excluded:
                    cost = route_moves.swim_cost(self, cells, node, target)
                    if cost is not None:
                        yield target, cost, None
                if water_movement.surface_node(cells, node) or (
                    self.pswim and route_moves.flowing_surface(cells, node)
                ):
                    yield from route_moves.water_exit(self, cells, node, dx, dz)
                    continue
            # A gap needs a hole under the adjacent cell and a full-cube support.
            if gap_room and flags((x + dx, y - 1, z + dz)) & EMPTY:
                for width in range(1, min(max_gap, 2) + 1):
                    target = (x + dx * (width + 1), y, z + dz * (width + 1))
                    if (
                        (width < 2 or allow_sprint)
                        and (node, target) not in self.excluded
                        and gap_plan(cells, node, target, mpolicy, width + 1)
                        is not None
                    ):
                        yield target, self._gap(width), None
            if self.movement_policy.allow_shapes:
                for ty in collision_geometry.surfaces(
                    cells, x + dx, z + dz, y - self.movement_policy.max_drop, y + 1
                ):
                    target = (x + dx, ty, z + dz)
                    if (
                        node,
                        target,
                    ) not in self.excluded and collision_geometry.step_clear(
                        cells, node, target
                    ):
                        reached.add(target)
                        yield target, self._height(ty - y), None
            else:
                for dy in (0, 1, *range(-1, -self.movement_policy.max_drop - 1, -1)):
                    target = (x + dx, y + dy, z + dz)
                    if (node, target) in self.excluded:
                        continue
                    if not standable(cells, target):
                        continue
                    if dy == 1 and not all(
                        empty(cells.get(p))
                        for p in (
                            (x, y + 2, z),
                            (x, y + 3, z),
                            (x + dx, y + 3, z + dz),
                        )
                    ):
                        continue
                    if dy < 0 and not all(
                        empty(cells.get((x + dx, cy, z + dz)))
                        for cy in range(y + dy, y + 2)
                    ):
                        continue
                    reached.add(target)
                    yield target, self._height(dy), None
            if edit_ready:
                yield from self._edit_edges(cells, node, dx, dz, reached)
        if self.movement_policy.allow_diagonals:
            for dx, dz in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                target = (x + dx, y, z + dz)
                if (node, target) in self.excluded:
                    continue
                # Both swept side columns require support and two-block clearance.
                if self.bdiag:
                    cost = self._diagonal_cost(cells, node, dx, dz)
                    if cost is not None:
                        yield target, cost, None
                elif all(
                    self._standable(cells, p)
                    for p in (target, (x + dx, y, z), (x, y, z + dz))
                ):
                    yield target, self._flat(diagonal=True), None
        if self.moves:
            yield from route_moves.extra_moves(self, cells, node)

    def _side(self, cells, x, y, z):
        """None unknown, 0 blocked, 1 passable, -1 passable but lava/hazard underneath.

        Reimplements the side test of Baritone MovementDiagonal.cost: a side need only
        be passable at feet and head, never standable; the body may cut over a hole.
        """
        flags = cells.cflags
        feet, head = flags((x, y, z)), flags((x, y + 1, z))
        if not feet & KNOWN or not head & KNOWN:
            return None
        if self.movement_policy.allow_shapes:
            ok = collision_geometry.clear_column(cells, x, z, y, y + 1.8)
        else:
            ok = feet & EMPTY and head & EMPTY
        if not ok:
            return 0
        # Hazard directly below (the body's hazardBelow scans max_drop + 1 cells);
        # unknown cells below are allowed and the scan stops at the first solid.
        for cy in range(
            math.floor(y) - 1, math.floor(y) - 2 - self.movement_policy.max_drop, -1
        ):
            below = flags((x, cy, z))
            if not below & KNOWN:
                continue
            if below & LAVA:
                return -1
            if below & SOLID:
                break
        return 1

    def _diagonal_cost(self, cells, node, dx, dz):
        """MovementDiagonal.cost: sides need only be passable; one side may edge."""
        x, y, z = node
        if not self._standable(cells, (x + dx, y, z + dz)):
            return None
        sides = (self._side(cells, x + dx, y, z), self._side(cells, x, y, z + dz))
        if None in sides or -1 in sides or sides == (0, 0):
            return None
        if 0 in sides:  # edging past one blocked side: walk pace, no sprint
            return WALK * math.sqrt(2) * (math.sqrt(2) - 0.001)
        return self._flat(diagonal=True)

    def _edit_edges(self, cells, node, dx, dz, reached):
        x, y, z = node
        if y != math.floor(y):
            return  # Edit steps are full-block stances only.
        for dy in (0, 1):
            target = (x + dx, y + dy, z + dz)
            if target in reached or (node, target) in self.excluded:
                continue
            plan = plan_edit(
                cells,
                node,
                target,
                self.policy,
                self.inventory,
                self._protected_all,
                self._tools,
                self.cut_memo,
            )
            if plan is None:
                continue
            # Resource/risk term (D2): each further edit on one route costs more.
            # Live runs committed to 50-block causeways and 30-step dig-outs on
            # terrain they could not see (run 20261030T030553486229Z R2, R5).
            yield target, self._priced(plan, dy, cells, node), plan

    def _changes(self, plan):
        edits = {}
        for p in plan.breaks:
            edits[p] = {
                "position": list(p),
                "block": "minecraft:air",
                "fluid": "minecraft:empty",
                "collision_boxes": [],
            }
        if self.moves:  # the falling stack drops in and is broken too
            for p in plan.breaks:
                for q in falling_stack(self.cells, p):
                    edits[q] = {**edits[p], "position": list(q)}
        for p in plan.places:
            edits[p] = {
                "position": list(p),
                "block": self.policy.material_allowlist[0],
                "fluid": "minecraft:empty",
                "collision_boxes": [[0, 0, 0, 1, 1, 1]],
            }
        return edits

    def _result(self, status, node, reason):
        route = tuple(reconstruct(self.previous, node))
        steps = tuple(
            self.plans[b]
            for a, b in zip(route, route[1:], strict=False)
            if b in self.plans and self.plans[b].source == a
        )
        return RouteResult(status, route, self.revision, self.expanded, reason, steps)

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
        # The snapshot is millions of live dicts: a cyclic collection inside a
        # slice costs tens of ms. A slice is short and its garbage is acyclic.
        collect = gc.isenabled()
        gc.disable()
        try:
            return self._advance(node_limit, budget_ms, segment_length)
        finally:
            if collect:
                gc.enable()

    def _advance(self, node_limit, budget_ms, segment_length):
        deadline = time.perf_counter() + budget_ms / 1000
        count = 0
        while self.frontier and count < node_limit and time.perf_counter() < deadline:
            _, cost, exit_unknown, node = heapq.heappop(self.frontier)
            count += 1
            if exit_unknown:
                if cost != self.costs[node]:
                    continue
                self.terminal = self._trim(
                    self._result(
                        "segment", node, "optimistic route through unknown space"
                    )
                )
                return self._segment(self.terminal, segment_length)
            if cost != self.costs[node]:
                continue
            self.expanded += 1
            if self._closer(node, self.best):
                self.best = node
            if self.bs:
                self._track(node, cost)
            if self._distance(node) == 0:
                self.terminal = self._result("reached", node, "known goal route")
                return self._segment(self.terminal, segment_length)
            if (
                self.unknown
                and node != self.start
                and self._is_frontier(self._view(node), node)
            ):
                # The unknown remainder costs a penalty over walking time (D3).
                remainder = self._remaining(
                    node,
                    self.policy.unknown_penalty,
                    exit_estimate=True,
                    buried=self.ticks and self._buried(self._view(node), node),
                )
                heapq.heappush(self.frontier, (cost + remainder, cost, 1, node))
                self.exit = min(self.exit, (cost + remainder, node))
            for target, edge, plan in self._moves(node):
                total = cost + edge * (FAVOR if target in self.favored else 1)
                if total < self.costs.get(target, math.inf):
                    self.costs[target], self.previous[target] = total, node
                    if plan is None:
                        self.plans.pop(target, None)
                        self.edit_depth[target] = self.edit_depth.get(node, 0)
                        self.overlay.pop(target, None)
                    else:
                        self.plans[target] = plan
                        self.edit_depth[target] = self.edit_depth.get(node, 0) + 1
                        self.overlay[target] = plan
                    heapq.heappush(
                        self.frontier, (total + self._h(target), total, 0, target)
                    )
        status = "searching" if self.frontier else "unreachable"
        end = self.exit[1]
        if end is None:
            end = self._partial_end() if self.bs else self.best
        result = self._trim(
            self._result(
                status,
                end,
                "slice budget" if self.frontier else "known graph exhausted",
            )
        )
        if not self.frontier:
            self.terminal = result
        return self._segment(result, segment_length)

    def _track(self, node, cost):
        estimate = self._remaining(node, 1)
        for i, c in enumerate(COEFFICIENTS):
            value = estimate + cost / c
            if value < self.coef_best[i][0]:
                self.coef_best[i] = (value, node)

    def _partial_end(self):
        """AbstractNodeCostSearch.bestSoFar: first coefficient whose best node is
        more than MIN_DIST_PATH from the start; else the closest-node rule.

        A frontier exit (planner_unknown) still wins when one exists: exits are
        priced with the unknown penalty, a scale this coefficient metric does not
        model, and the coefficient metric would otherwise always beat them.
        """
        for _, node in self.coef_best:
            if node is not None and math.dist(node, self.start) > MIN_DIST_PATH:
                return node
        return self.best

    def _trim(self, result):
        """Settings.pathCutoff*: drop the last 10% of long non-goal routes."""
        n = len(result.route)
        if not self.bs or result.status == "reached" or n < CUTOFF_MIN:
            return result
        route = result.route[: max(2, int(n * CUTOFF_FACTOR))]
        targets = set(route[1:])
        steps = tuple(s for s in result.steps if s.target in targets)
        return RouteResult(
            result.status,
            route,
            result.map_revision,
            result.expanded,
            result.reason,
            steps,
        )

    @staticmethod
    def _segment(result, length):
        route = result.route[:length]
        status = result.status
        if status == "reached" and len(route) < len(result.route):
            status = "segment"
        targets = set(route[1:])
        return RouteResult(
            status,
            route,
            result.map_revision,
            result.expanded,
            result.reason,
            tuple(s for s in result.steps if s.target in targets),
        )
