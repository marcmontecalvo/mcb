"""Bounded full-cube hill steps and confirmed Survival interaction execution."""

import math
from dataclasses import dataclass

from mclab.navigation import empty, full_cube, rotation, standable

BUILDING = ("minecraft:cobblestone", "minecraft:dirt")
EXCAVATABLE = {
    "minecraft:stone": "minecraft:iron_pickaxe",
    "minecraft:cobblestone": "minecraft:iron_pickaxe",
    "minecraft:dirt": "minecraft:iron_shovel",
    "minecraft:grass_block": "minecraft:iron_shovel",
}


@dataclass(frozen=True)
class TerrainPolicy:
    allow_break: bool = True
    allow_place: bool = True
    break_budget: int = 8
    place_budget: int = 8
    reserve_blocks: int = 0
    interaction_tick_limit: int = 160

    def __post_init__(self):
        if type(self.allow_break) is not bool or type(self.allow_place) is not bool:
            raise ValueError("Terrain permissions must be booleans")
        for name in ("break_budget", "place_budget", "reserve_blocks"):
            if (
                type(getattr(self, name)) is not int
                or not 0 <= getattr(self, name) <= 64
            ):
                raise ValueError("Invalid terrain budget")
        if (
            type(self.interaction_tick_limit) is not int
            or not 20 <= self.interaction_tick_limit <= 400
        ):
            raise ValueError("Invalid terrain interaction deadline")


@dataclass(frozen=True)
class StepPlan:
    source: tuple
    target: tuple
    breaks: tuple = ()
    places: tuple = ()

    @property
    def cost(self):
        return 1.4 + 5 * len(self.breaks) + 8 * len(self.places)


def hotbar(inventory, item):
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


def building_supply(inventory, reserve):
    total = sum(
        row.get("count", 0)
        for row in inventory
        if row.get("item") in BUILDING and 0 <= row.get("slot", -1) < 9
    )
    if total <= reserve:
        return None
    return next(
        (
            hotbar(inventory, item)
            for item in BUILDING
            if hotbar(inventory, item) is not None
        ),
        None,
    )


def prepare_step(cells, source, target, policy, inventory, *, protected=()):
    """Plan at most one foothold and four headroom cuts; unknown is forbidden."""
    x, y, z = source
    tx, ty, tz = target
    if abs(tx - x) + abs(tz - z) != 1 or ty - y not in (0, 1):
        return None
    if not standable(cells, source):
        return None
    support = (tx, ty - 1, tz)
    places = []
    if not full_cube(cells.get(support)):
        if (
            not policy.allow_place
            or policy.place_budget < 1
            or not empty(cells.get(support))
            or not full_cube(cells.get((tx, ty - 2, tz)))
            or building_supply(inventory, policy.reserve_blocks) is None
        ):
            return None
        places.append(support)
    clearance = [(tx, ty, tz), (tx, ty + 1, tz)]
    if ty > y:
        clearance += [(x, y + 2, z), (x, y + 3, z), (tx, ty + 2, tz)]
    cuts = []
    for point in dict.fromkeys(clearance):
        cell = cells.get(point)
        if empty(cell):
            continue
        block = (cell or {}).get("block")
        if (
            point in protected
            or not policy.allow_break
            or block not in EXCAVATABLE
            or not full_cube(cell)
            or hotbar(inventory, EXCAVATABLE[block]) is None
        ):
            return None
        # Removing a block must not release unknown/fluid/falling neighbors.
        px, py, pz = point
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, -1)):
            neighbor = cells.get((px + dx, py + dy, pz + dz))
            if (
                neighbor is None
                or neighbor.get("fluid", "minecraft:empty") != "minecraft:empty"
                or neighbor.get("block") in {"minecraft:sand", "minecraft:gravel"}
            ):
                return None
        cuts.append(point)
    if len(cuts) > min(4, policy.break_budget):
        return None
    if not cuts and not places:
        return None
    return StepPlan(tuple(source), tuple(target), tuple(cuts), tuple(places))


class StepExecutor:
    """No movement until edits are observed. Issue normal reach/face-checked inputs."""

    def __init__(self, plan, policy):
        if (
            (plan.breaks and not policy.allow_break)
            or (plan.places and not policy.allow_place)
            or len(plan.breaks) > policy.break_budget
            or len(plan.places) > policy.place_budget
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
                    cell.get("block") not in BUILDING
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
        if kind == "break":
            allowed = prepare_step(
                fresh, self.plan.source, self.plan.target, self.policy, inventory
            )
            if allowed is None or point not in allowed.breaks:
                return self.fail("cut preconditions changed")
            self.slot = hotbar(inventory, EXCAVATABLE.get(cell.get("block")))
            aim = [v + 0.5 for v in point]
            expected = point
            face = None
        else:
            self.slot = building_supply(inventory, self.policy.reserve_blocks)
            below = (point[0], point[1] - 1, point[2])
            if not empty(cell) or not full_cube(fresh.get(below)):
                return self.fail("foothold support changed")
            # Click the upper face of the existing ground, not the future block.
            aim = [point[0] + 0.5, point[1] - 0.001, point[2] + 0.5]
            expected, face = below, "UP"
        if self.slot is None:
            return self.fail("required hotbar supply unavailable")
        if math.dist(aim, [position[0], position[1] + 1.62, position[2]]) > min(
            4.2, body.get("block_reach", 4.2)
        ):
            return self.fail("terrain interaction out of reach")
        turn = rotation(aim, body)
        ray = body.get("ray", {})
        aligned = abs(turn["yaw_delta"]) < 2 and abs(turn["pitch_delta"]) < 2
        hit_position = tuple(ray.get("position", ()))
        hit = hit_position == expected
        if kind == "break" and aligned and hit_position in allowed.breaks:
            # A planned headroom cut can occlude the center of another planned
            # cut. Mine the admitted occluder and confirm its own cell, rather
            # than waiting forever or mining an arbitrary ray obstruction.
            hit = True
            self.slot = hotbar(inventory, EXCAVATABLE[fresh[hit_position]["block"]])
        if (
            not aligned
            or not hit
            or (face is not None and str(ray.get("face", "")).upper() != face)
        ):
            return {**turn, "hotbar_slot": self.slot}
        command = {**turn, "hotbar_slot": self.slot}
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
