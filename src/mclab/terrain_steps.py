"""Bounded full-cube hill steps and confirmed Survival interaction execution."""

import math
from collections import ChainMap
from dataclasses import dataclass

from mclab.navigation import empty, full_cube, rotation, standable
from mclab.terrain_placement import placement_target

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
            )
        ):
            raise ValueError("Terrain permissions must be booleans")
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


def building_supply(inventory, reserve, materials=BUILDING):
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


def prepare_step(cells, source, target, policy, inventory, *, protected=()):
    """Plan a bounded support stack and four headroom cuts; unknown is forbidden."""
    x, y, z = source
    tx, ty, tz = target
    if abs(tx - x) + abs(tz - z) != 1 or ty - y not in (0, 1):
        return None
    if not standable(cells, source):
        return None
    support = (tx, ty - 1, tz)
    protected = set(protected) | set(policy.protected_positions)
    places = []
    if not full_cube(cells.get(support)):
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
        stock = [dict(row) for row in inventory]
        predicted = {}
        for point in places:
            cell = cells.get(point)
            slot = building_supply(
                stock, policy.reserve_blocks, policy.material_allowlist
            )
            if (
                point in protected
                or (cell or {}).get("block") in policy.protected_blocks
                or not empty(cell)
                or slot is None
                or placement_target(
                    ChainMap(predicted, cells),
                    source,
                    point,
                    allow_bridge=policy.allow_bridge,
                )
                is None
            ):
                return None
            row = next(row for row in stock if row["slot"] == slot)
            row["count"] -= 1
            predicted[point] = {
                "block": row["item"],
                "collision_boxes": [[0, 0, 0, 1, 1, 1]],
            }
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
            or block in policy.protected_blocks
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
            self.slot = hotbar(inventory, EXCAVATABLE.get(cell.get("block")))
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
            self.slot = hotbar(inventory, EXCAVATABLE[fresh[hit_position]["block"]])
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
