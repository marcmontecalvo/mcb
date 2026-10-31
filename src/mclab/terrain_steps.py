"""Bounded full-cube hill steps and confirmed Survival interaction execution."""

import math
from dataclasses import dataclass

from mclab.grid_cache import EMPTY, FALLING, FALLS, FULL, KNOWN, WET, classify
from mclab.navigation import empty, full_cube, rotation, standable
from mclab.terrain_placement import placement_target

BUILDING = ("minecraft:cobblestone", "minecraft:dirt")
EXCAVATABLE = {
    "minecraft:stone": "minecraft:iron_pickaxe",
    "minecraft:cobblestone": "minecraft:iron_pickaxe",
    "minecraft:dirt": "minecraft:iron_shovel",
    "minecraft:grass_block": "minecraft:iron_shovel",
}
_PICK, _SHOVEL, _AXE = (
    "minecraft:iron_pickaxe",
    "minecraft:iron_shovel",
    "minecraft:iron_axe",
)
_COLORS = (
    "white orange magenta light_blue yellow lime pink gray light_gray cyan "
    "purple blue brown green red black"
).split()
# Step C (Phase 1): block -> (hardness, tool, tool is effective). Vanilla 1.21
# hardness values. Iron tools mine all of these; Mojang's breaking formula is in
# break_ticks. The mcb-body StepMotor TOOLS map must list the same blocks.
WIDE_EXCAVATABLE = {
    **{f"minecraft:{c}_terracotta": (1.25, _PICK, True) for c in _COLORS},
    "minecraft:terracotta": (1.25, _PICK, True),
    "minecraft:stone": (1.5, _PICK, True),
    "minecraft:cobblestone": (2.0, _PICK, True),
    "minecraft:andesite": (1.5, _PICK, True),
    "minecraft:diorite": (1.5, _PICK, True),
    "minecraft:granite": (1.5, _PICK, True),
    "minecraft:tuff": (1.5, _PICK, True),
    "minecraft:calcite": (0.75, _PICK, True),
    "minecraft:deepslate": (3.0, _PICK, True),
    "minecraft:sandstone": (0.8, _PICK, True),
    "minecraft:red_sandstone": (0.8, _PICK, True),
    "minecraft:coal_ore": (3.0, _PICK, True),
    "minecraft:iron_ore": (3.0, _PICK, True),
    "minecraft:dirt": (0.5, _SHOVEL, True),
    "minecraft:coarse_dirt": (0.5, _SHOVEL, True),
    "minecraft:rooted_dirt": (0.5, _SHOVEL, True),
    "minecraft:grass_block": (0.6, _SHOVEL, True),
    "minecraft:podzol": (0.5, _SHOVEL, True),
    "minecraft:mycelium": (0.6, _SHOVEL, True),
    "minecraft:clay": (0.6, _SHOVEL, True),
    "minecraft:mud": (0.5, _SHOVEL, True),
    "minecraft:snow_block": (0.2, _SHOVEL, True),
    "minecraft:sand": (0.5, _SHOVEL, True),
    "minecraft:red_sand": (0.5, _SHOVEL, True),
    "minecraft:gravel": (0.6, _SHOVEL, True),
    **{
        f"minecraft:{w}_log": (2.0, _AXE, True)
        for w in "oak spruce birch jungle acacia dark_oak mangrove cherry".split()
    },
    # Leaves: no axe speed bonus, but the axe slot is a harmless selection.
    **{
        f"minecraft:{w}_leaves": (0.2, _AXE, False)
        for w in "oak spruce birch jungle acacia dark_oak mangrove cherry azalea".split(
            " "
        )
    },
}
IRON_SPEED = 6.0  # ItemTier IRON mining speed; the natural kit carries iron tools
BREAK_EXTRA_TICKS = 2.0  # Baritone blockBreakAdditionalPenalty (default 2)
PLACE_TICKS = 20.0  # Baritone placeBlockCost (default 20)
# MCB-specific, measured: each edit step stops, aims, confirms and restarts.
# Live run 20261030T004308126362Z R3: ~36 ticks per two-block tunnel step
# against ~24 modelled from break times alone.
EDIT_STEP_TICKS = 15.0
# MCB-specific, measured: re-aiming at each further block of a step. Live R2 of
# that run broke stone in ~16 ticks per block against 10 modelled.
BREAK_AIM_TICKS = 6.0


_WIDE_TOOLS = {block: spec[1] for block, spec in WIDE_EXCAVATABLE.items()}


def excavatable(policy):
    """Breakable block -> tool. Tick-cost planning widens the legacy set."""
    if getattr(policy, "planner_tick_costs", False):
        return _WIDE_TOOLS
    return EXCAVATABLE


def break_ticks(block):
    """Vanilla breaking time with the block's iron tool, in game ticks.

    Per tick damage is speed / hardness / 30 when the tool can harvest the
    block; a block breaks when damage reaches 1 (instant if one tick suffices).
    """
    hardness, _, effective = WIDE_EXCAVATABLE[block]
    damage = (IRON_SPEED if effective else 1.0) / hardness / 30
    return 1 if damage >= 1 else math.ceil(1 / damage)


@dataclass(frozen=True)
class TerrainPolicy:
    allow_break: bool = True
    allow_place: bool = True
    break_budget: int = 8
    place_budget: int = 8
    reserve_blocks: int = 0
    interaction_tick_limit: int = 160
    material_allowlist: tuple = BUILDING
    protected_blocks: tuple = ()
    protected_positions: tuple = ()
    break_penalty: float = 3.0
    placement_penalty: float = 4.0
    tool_wear_penalty: float = 0.0
    allow_bridge: bool = False
    max_support_stack: int = 1
    allow_pillar: bool = False
    max_pillar_height: int = 3
    # Step C planner core (D11): each part has its own flag for ablations.
    planner_tick_costs: bool = False
    planner_unknown: bool = False
    planner_edits: bool = False
    unknown_penalty: float = 2.0
    # Baritone v1.20.0 search mechanics (octile heuristic, jump penalty, partial
    # path coefficients, previous-path favouring, tail cutoff) and its diagonal rule.
    baritone_search: bool = False
    baritone_diagonals: bool = False
    # Baritone movement kinds missing from the planner: dig down, descend with
    # breaks, planner pillar, ladders/vines, water landings, falling-block pricing.
    baritone_moves: bool = False
    # Swim edges priced in ticks (SWIM_TICKS x water multiplier), flowing water,
    # surface->land exits and water landings. Needs movement_policy.allow_swim.
    planner_swim: bool = False
    # 0: legacy weighting (remaining distance priced like unknown space).
    # 1..10: Baritone-form A* heuristic at sprint rate times this weight.
    heuristic_weight: float = 0.0

    @classmethod
    def from_payload(cls, value):
        if (
            not isinstance(value, dict)
            or not value.keys() <= cls.__dataclass_fields__.keys()
        ):
            raise ValueError("Invalid terrain profile fields")
        values = dict(value)
        for name in ("material_allowlist", "protected_blocks", "protected_positions"):
            if name in values:
                if not isinstance(values[name], (list, tuple)):
                    raise ValueError("Invalid terrain profile list")
                values[name] = tuple(
                    tuple(p)
                    if name == "protected_positions" and isinstance(p, (list, tuple))
                    else p
                    for p in values[name]
                )
        return cls(**values)

    def __post_init__(self):
        if any(
            type(v) is not bool
            for v in (
                self.allow_break,
                self.allow_place,
                self.allow_bridge,
                self.allow_pillar,
                self.planner_tick_costs,
                self.planner_unknown,
                self.planner_edits,
                self.baritone_search,
                self.baritone_diagonals,
                self.baritone_moves,
                self.planner_swim,
            )
        ):
            raise ValueError("Terrain permissions must be booleans")
        if (
            type(self.unknown_penalty) not in (int, float)
            or not 1 <= self.unknown_penalty <= 10
        ):
            raise ValueError("Unknown-space penalty must be between 1 and 10")
        if type(self.heuristic_weight) not in (int, float) or not (
            self.heuristic_weight == 0 or 1 <= self.heuristic_weight <= 10
        ):
            raise ValueError("Heuristic weight must be 0 (legacy) or 1..10")
        for name in ("break_budget", "place_budget", "reserve_blocks"):
            if (
                type(getattr(self, name)) is not int
                or not 0 <= getattr(self, name) <= 64
            ):
                raise ValueError("Invalid terrain budget")
        if (
            type(self.max_support_stack) is not int
            or not 1 <= self.max_support_stack <= 3
        ):
            raise ValueError("Support stacks must contain one through three blocks")
        if (
            type(self.max_pillar_height) is not int
            or not 1 <= self.max_pillar_height <= 3
        ):
            raise ValueError("Pillars must contain one through three blocks")
        if (
            type(self.interaction_tick_limit) is not int
            or not 20 <= self.interaction_tick_limit <= 400
        ):
            raise ValueError("Invalid terrain interaction deadline")
        for name in ("material_allowlist", "protected_blocks"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(
                type(v) is not str or not v.startswith("minecraft:") for v in values
            ):
                raise ValueError("Invalid terrain block policy")
        if any(v not in BUILDING for v in self.material_allowlist):
            raise ValueError("Unsupported building material")
        if not isinstance(self.protected_positions, tuple) or any(
            not isinstance(p, tuple)
            or len(p) != 3
            or any(type(v) is not int for v in p)
            for p in self.protected_positions
        ):
            raise ValueError("Invalid protected positions")
        for value in (
            self.break_penalty,
            self.placement_penalty,
            self.tool_wear_penalty,
        ):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= 1000
            ):
                raise ValueError("Invalid terrain cost")


@dataclass(frozen=True)
class StepPlan:
    source: tuple
    target: tuple
    breaks: tuple = ()
    places: tuple = ()
    kind: str = "step"
    exit: tuple | None = None

    @property
    def cost(self):
        return 1.4 + 5 * len(self.breaks) + 8 * len(self.places)


class Inventory(tuple):
    """Immutable inventory rows with memoised hotbar and supply lookups."""

    def __init__(self, rows=()):
        super().__init__()
        self.hot, self.supply = {}, {}
        for row in self:
            slot = row.get("slot", -1)
            if 0 <= slot < 9 and row.get("count", 0) > 0:
                self.hot.setdefault(row.get("item"), slot)


def hotbar(inventory, item):
    hot = getattr(inventory, "hot", None)
    if hot is not None:
        return hot.get(item)
    return next(
        (
            r["slot"]
            for r in inventory
            if r.get("item") == item
            and 0 <= r.get("slot", -1) < 9
            and r.get("count", 0) > 0
        ),
        None,
    )


def building_supply(inventory, reserve, materials=BUILDING):
    memo = getattr(inventory, "supply", None)
    if memo is not None:
        key = (reserve, materials)
        if key not in memo:
            memo[key] = _building_supply(inventory, reserve, materials)
        return memo[key]
    return _building_supply(inventory, reserve, materials)


def _building_supply(inventory, reserve, materials):
    total = sum(
        row.get("count", 0)
        for row in inventory
        if row.get("item") in materials and 0 <= row.get("slot", -1) < 9
    )
    if total <= reserve:
        return None
    return next(
        (
            hotbar(inventory, item)
            for item in materials
            if hotbar(inventory, item) is not None
        ),
        None,
    )


def stack_supply(inventory, count, reserve, materials):
    """Can ``count`` blocks be placed in a row from the hotbar supply?"""
    memo = getattr(inventory, "supply", None)
    key = (count, reserve, materials)
    if memo is not None and key in memo:
        return memo[key]
    stock = [dict(row) for row in inventory]
    ok = True
    for _ in range(count):
        slot = _building_supply(stock, reserve, materials)
        if slot is None:
            ok = False
            break
        next(row for row in stock if row["slot"] == slot)["count"] -= 1
    if memo is not None:
        memo[key] = ok
    return ok


def cut_allowed(cells, point, policy, inventory, protected, tools):
    """May this block be broken? Unknown, fluid and falling neighbours decide.

    With baritone_moves a falling block directly above is allowed (its stack is
    priced by falling_stack) and so is a side falling block with known support
    (Baritone MovementHelper.avoidBreaking/getMiningDurationTicks); an
    unsupported side one is still refused.
    """
    flags = getattr(cells, "cflags", None) or (lambda p: classify(cells.get(p)))
    block = (cells.get(point) or {}).get("block")
    if (
        point in protected
        or block in policy.protected_blocks
        or not policy.allow_break
        or block not in tools
        or not flags(point) & FULL
        or hotbar(inventory, tools[block]) is None
    ):
        return False
    px, py, pz = point
    for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, -1)):
        found = flags((px + dx, py + dy, pz + dz))
        if not found & KNOWN or found & WET:
            return False
        if found & FALLS:
            if not policy.baritone_moves:
                return False
            if dy == 0 and not flags((px + dx, py - 1, pz + dz)) & FULL:
                return False
    return True


def cut_ok(cells, point, policy, inventory, protected, tools, memo):
    """cut_allowed, memoised: shared while no planned edit lies within one cell of
    point, else per overlay (``cells.o_cut``).

    The memo is only valid for one (policy, inventory, protected) triple, which
    is the caller's responsibility (a RouteSearch owns exactly one).
    """
    near = getattr(cells, "near", None)
    if near is None or memo is None:
        return cut_allowed(cells, point, policy, inventory, protected, tools)
    if point in near:
        memo = cells.o_cut  # valid for this overlay only
    allowed = memo.get(point)
    if allowed is None:
        allowed = memo[point] = cut_allowed(
            cells, point, policy, inventory, protected, tools
        )
    return allowed


def falling_stack(cells, point):
    """Falling blocks stacked directly above point (they drop into the cut)."""
    stack, (x, y, z) = [], point
    flags = getattr(cells, "cflags", None)
    if flags is not None:  # Grid: gravity bit, memoised
        while flags((x, y + len(stack) + 1, z)) & FALLS:
            stack.append((x, y + len(stack) + 1, z))
        return stack
    while ((cells.get((x, y + len(stack) + 1, z)) or {}).get("block")) in FALLING:
        stack.append((x, y + len(stack) + 1, z))
    return stack


def prepare_step(
    cells, source, target, policy, inventory, *, protected=(), cut_memo=None
):
    """Plan a bounded support stack and four headroom cuts; unknown is forbidden."""
    x, y, z = source
    tx, ty, tz = target
    if abs(tx - x) + abs(tz - z) != 1 or ty - y not in (0, 1):
        return None
    if not standable(cells, source):
        return None
    support = (tx, ty - 1, tz)
    protected = set(protected) | set(policy.protected_positions)
    flags = getattr(cells, "cflags", None) or (lambda p: classify(cells.get(p)))
    places = []
    if not flags(support) & FULL:
        if not policy.allow_place:
            return None
        places.append(support)
        # Find a known base, or an explicitly permitted side anchor. The
        # hypothetical stack is local and every block is confirmed by execution.
        while (
            placement_target(
                cells, source, places[-1], allow_bridge=policy.allow_bridge
            )
            is None
        ):
            if len(places) >= policy.max_support_stack:
                return None
            px, py, pz = places[-1]
            places.append((px, py - 1, pz))
        places.reverse()
        if len(places) > policy.place_budget:
            return None
        # Every stacked point has the previous (predicted full) block below it and
        # the lowest passed the anchor test above, so placement_target cannot fail.
        for point in places:
            if (
                point in protected
                or (cells.get(point) or {}).get("block") in policy.protected_blocks
                or not flags(point) & EMPTY
            ):
                return None
        if not stack_supply(
            inventory, len(places), policy.reserve_blocks, policy.material_allowlist
        ):
            return None
    clearance = [(tx, ty, tz), (tx, ty + 1, tz)]
    if ty > y:
        clearance += [(x, y + 2, z), (x, y + 3, z), (tx, ty + 2, tz)]
    tools = excavatable(policy)
    cuts = []
    for point in dict.fromkeys(clearance):
        if flags(point) & EMPTY:
            continue
        if not cut_ok(cells, point, policy, inventory, protected, tools, cut_memo):
            return None
        cuts.append(point)
    if len(cuts) > min(4, policy.break_budget):
        return None
    if not cuts and not places:
        return None
    return StepPlan(tuple(source), tuple(target), tuple(cuts), tuple(places))


def _anchored(flags, source, point, allow_bridge):
    """placement_target(...) is not None, from cell flags."""
    x, y, z = point
    if flags((x, y - 1, z)) & FULL:
        return True
    sx, sy, sz = source
    dx, dz = x - sx, z - sz
    return bool(
        allow_bridge
        and y == sy - 1
        and abs(dx) + abs(dz) == 1
        and flags((x, y - 1, z)) & EMPTY
        and flags((sx, sy - 1, sz)) & FULL
    )


def plan_edit(cells, source, target, policy, inventory, protected, tools, cut_memo):
    """prepare_step for a standable full-block source and adjacent target.

    RouteSearch hot path: ``protected`` is already merged with the policy's
    protected positions and ``tools`` is ``excavatable(policy)``.
    """
    x, y, z = source
    tx, ty, tz = target
    flags = cells.cflags
    support = (tx, ty - 1, tz)
    places = ()
    if not flags(support) & FULL:
        if not policy.allow_place:
            return None
        stack = [support]
        while not _anchored(flags, source, stack[-1], policy.allow_bridge):
            if len(stack) >= policy.max_support_stack:
                return None
            px, py, pz = stack[-1]
            stack.append((px, py - 1, pz))
        stack.reverse()
        if len(stack) > policy.place_budget:
            return None
        # Each stacked point rests on the previous predicted block, so only the
        # lowest needed the anchor test (placement_target cannot fail above it).
        for point in stack:
            if (
                point in protected
                or (cells.get(point) or {}).get("block") in policy.protected_blocks
                or not flags(point) & EMPTY
            ):
                return None
        if not stack_supply(
            inventory, len(stack), policy.reserve_blocks, policy.material_allowlist
        ):
            return None
        places = tuple(stack)
    if ty == y:
        clearance = ((tx, ty, tz), (tx, ty + 1, tz))
    else:
        clearance = (
            (tx, ty, tz),
            (tx, ty + 1, tz),
            (x, y + 2, z),
            (x, y + 3, z),
            (tx, ty + 2, tz),
        )
    cuts = []
    for point in clearance:
        if flags(point) & EMPTY:
            continue
        if not cut_ok(cells, point, policy, inventory, protected, tools, cut_memo):
            return None
        cuts.append(point)
    if len(cuts) > min(4, policy.break_budget) or not (cuts or places):
        return None
    return StepPlan(source, target, tuple(cuts), places)


class StepExecutor:
    """No movement until edits are observed. Issue normal reach/face-checked inputs."""

    def __init__(self, plan, policy):
        if (
            plan.kind != "step"
            or (plan.breaks and not policy.allow_break)
            or (plan.places and not policy.allow_place)
            or len(plan.breaks) > policy.break_budget
            or len(plan.places) > policy.place_budget
            or len(plan.places) > policy.max_support_stack
            or any(
                p in policy.protected_positions for p in (*plan.breaks, *plan.places)
            )
        ):
            raise ValueError("Terrain plan exceeds permissions/budgets")
        self.plan, self.policy = plan, policy
        self.operations = [("break", p) for p in plan.breaks] + [
            ("place", p) for p in plan.places
        ]
        self.index = 0
        self.started = None
        self.state = "editing"
        self.reason = ""
        self.issued = False
        self.initial_count = None
        self.slot = None
        self.breaks_confirmed = 0
        self.places_confirmed = 0
        self.last_issue_tick = -100
        self.issued_breaks = set()
        self.confirmed_breaks = set()
        self.issued_places = set()
        self.placement_item = None
        self.initial_item_total = None
        self.initial_pickups = None
        self.inventory_debit_seen = False

    def fail(self, reason):
        self.state, self.reason = "failed", reason
        return {}

    def decide(self, observation, fresh):
        if self.state == "failed":
            return {}
        tick = observation["tick"]
        if self.started is None:
            self.started = tick
        if tick - self.started > self.policy.interaction_tick_limit:
            return self.fail("terrain interaction deadline")
        body = observation["body"]
        if not body.get("on_ground", True):
            return self.fail("displaced during terrain work")
        position = body["position"]
        eye_height = body.get("survival", {}).get("eye_height", 1.62)
        if (
            type(eye_height) not in (int, float)
            or not math.isfinite(eye_height)
            or not 0.5 <= eye_height <= 2
        ):
            return self.fail("invalid eye-height observation")
        source = self.plan.source
        if (
            math.hypot(position[0] - source[0] - 0.5, position[2] - source[2] - 0.5)
            > 0.8
            or abs(position[1] - source[1]) > 0.2
        ):
            return self.fail("left terrain work stance")
        inventory = body.get("inventory", [])
        for point in self.issued_breaks - self.confirmed_breaks:
            if empty(fresh.get(point)):
                self.confirmed_breaks.add(point)
                self.breaks_confirmed += 1
        if self.index == len(self.operations):
            if standable(fresh, self.plan.target):
                self.state = "ready"
            return {}
        kind, point = self.operations[self.index]
        current_count = next(
            (r["count"] for r in inventory if r.get("slot") == self.slot), 0
        )
        if kind == "place" and self.issued:
            pickups = body.get("building_pickups")
            if self.initial_pickups is not None and pickups is not None:
                acquired = pickups.get(self.placement_item, 0) - self.initial_pickups
                total = sum(
                    r.get("count", 0)
                    for r in inventory
                    if r.get("item") == self.placement_item
                )
                if acquired < 0:
                    return self.fail("inventory pickup ledger changed generation")
                self.inventory_debit_seen = (
                    total == self.initial_item_total + acquired - 1
                )
            elif current_count == self.initial_count - 1:
                # The inventory debit may precede the next geometry refresh.
                self.inventory_debit_seen = True
        cell = fresh.get(point)
        if cell is None:
            return {}  # Fresh confirmation cannot be inferred from a missing delta.
        complete = empty(cell) if kind == "break" else full_cube(cell)
        if complete:
            if self.issued:
                if kind == "place" and (
                    cell.get("block") not in self.policy.material_allowlist
                    or self.placement_item is not None
                    and cell.get("block") != self.placement_item
                    or not self.inventory_debit_seen
                ):
                    return self.fail("placement geometry/inventory mismatch")
                if kind == "place":
                    self.places_confirmed += 1
            elif kind == "place":
                return self.fail("foothold changed before interaction")
            self.index += 1
            self.issued = False
            self.slot = None
            self.placement_item = self.initial_item_total = self.initial_pickups = None
            self.inventory_debit_seen = False
            return {}
        if not standable(fresh, self.plan.source):
            return self.fail("terrain stance is no longer safe")
        if kind == "place" and self.issued and self.inventory_debit_seen:
            # The paid-for block can precede its next terrain delta. Replanning
            # the full remaining stack here would charge that same block twice.
            # Hold the stance until geometry confirms it or the deadline expires.
            return {"sneak": True}
        approach = None
        if kind == "break":
            allowed = prepare_step(
                fresh, self.plan.source, self.plan.target, self.policy, inventory
            )
            if allowed is None or point not in allowed.breaks:
                return self.fail("cut preconditions changed")
            self.slot = hotbar(
                inventory, excavatable(self.policy).get(cell.get("block"))
            )
            aim = [v + 0.5 for v in point]
            expected = point
            face = None
        else:
            allowed = prepare_step(
                fresh, self.plan.source, self.plan.target, self.policy, inventory
            )
            if allowed is None or point not in allowed.places:
                return self.fail("placement preconditions changed")
            if (
                point in self.policy.protected_positions
                or cell.get("block") in self.policy.protected_blocks
            ):
                return self.fail("placement is protected")
            self.slot = building_supply(
                inventory, self.policy.reserve_blocks, self.policy.material_allowlist
            )
            placement = placement_target(
                fresh, self.plan.source, point, allow_bridge=self.policy.allow_bridge
            )
            if not empty(cell) or placement is None:
                return self.fail("foothold support changed")
            aim, expected, face, approach = (
                placement.aim,
                placement.support,
                placement.face,
                placement.approach,
            )
            if approach is not None and "eye_height" not in body.get("survival", {}):
                return self.fail("edge placement requires observed eye height")
        if self.slot is None:
            return self.fail("required hotbar supply unavailable")
        if math.dist(aim, [position[0], position[1] + eye_height, position[2]]) > min(
            4.2, body.get("block_reach", 4.2)
        ):
            return self.fail("terrain interaction out of reach")
        aim_body = {
            **body,
            "position": [position[0], position[1] + eye_height - 1.62, position[2]],
        }
        if kind == "place" and point[1] >= source[1]:
            dx, dz = point[0] - source[0], point[2] - source[2]
            progress = (position[0] - source[0] - 0.5) * dx + (
                position[2] - source[2] - 0.5
            ) * dz
            if progress > 0.1:
                # Low stack layers require an edge view, but a layer at feet
                # height would intersect that same leaning body. Return to the
                # known source footprint before issuing the upper placement.
                retreat = rotation(
                    (source[0] + 0.5, source[1], source[2] + 0.5),
                    aim_body,
                    ground=True,
                )
                return {
                    **retreat,
                    "sneak": True,
                    "hotbar_slot": self.slot,
                    "forward": 0.3 if abs(retreat["yaw_delta"]) < 8 else 0,
                }
        turn = rotation(aim, aim_body)
        stance = {"sneak": True} if approach is not None else {}
        if approach is not None:
            dx, dz = point[0] - source[0], point[2] - source[2]
            progress = (position[0] - source[0] - 0.5) * dx + (
                position[2] - source[2] - 0.5
            ) * dz
            if progress < 0.6:
                approach_turn = rotation(approach, aim_body, ground=True)
                return {
                    **approach_turn,
                    **stance,
                    "hotbar_slot": self.slot,
                    "forward": 0.3 if abs(approach_turn["yaw_delta"]) < 8 else 0,
                }
        ray = body.get("ray", {})
        aligned = abs(turn["yaw_delta"]) < 2 and abs(turn["pitch_delta"]) < 2
        hit_position = tuple(ray.get("position", ()))
        hit = hit_position == expected
        if kind == "break" and aligned and hit_position in allowed.breaks:
            # A planned headroom cut can occlude the center of another planned
            # cut. Mine the admitted occluder and confirm its own cell, rather
            # than waiting forever or mining an arbitrary ray obstruction.
            hit = True
            self.slot = hotbar(
                inventory, excavatable(self.policy)[fresh[hit_position]["block"]]
            )
        if (
            not aligned
            or not hit
            or (face is not None and str(ray.get("face", "")).upper() != face)
        ):
            return {**turn, **stance, "hotbar_slot": self.slot}
        command = {**turn, **stance, "hotbar_slot": self.slot}
        if kind == "break":
            command["mine"] = True
            self.issued_breaks.add(hit_position)
        elif tick - self.last_issue_tick >= 10:
            if self.issued:
                return self.fail("placement not confirmed")
            self.initial_count = next(
                r["count"] for r in inventory if r["slot"] == self.slot
            )
            self.placement_item = next(
                r["item"] for r in inventory if r["slot"] == self.slot
            )
            self.initial_item_total = sum(
                r.get("count", 0)
                for r in inventory
                if r.get("item") == self.placement_item
            )
            pickups = body.get("building_pickups")
            self.initial_pickups = (
                pickups.get(self.placement_item, 0) if pickups is not None else None
            )
            self.inventory_debit_seen = False
            self.issued_places.add(point)
            command["use_pulse"] = True
            self.last_issue_tick = tick
        else:
            return command
        self.issued = True
        return command
