"""A dict-world body for tests and dry runs of the gather and build executors.

Implements ``gather_runner.GatherBody`` and ``build_runner.BuildBody``. Rules are
simple on purpose: stances teleport, reach is checked only for entities, a break needs
a tool whose tier gives drops in the knowledge pack, crafts and smelts apply recipes
from the pack, and drops become item entities that ``walk_to`` picks up.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from itertools import count

from mclab import build_plan as bp
from mclab.build_runner import Result
from mclab.knowledge import Knowledge

Pos = tuple[int, int, int]
TABLE_REACH = 4.5
ENTITY_REACH = 3.0
PICKUP = 1.5
OFFSETS = [(1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (2, 0, 0), (0, 0, 2)]


class FakeGatherBody:
    def __init__(
        self,
        knowledge: Knowledge,
        world: dict[Pos, str],
        inventory: dict[str, int] | None = None,
        *,
        pos: Pos = (0, 0, 0),
        entities: list[dict] | None = None,
        food: int = 20,
        default: str | None = "minecraft:air",
        drift: dict[str, int] | None = None,
        food_per_op: int = 0,
        sight: float | None = None,
        can_dig: bool = False,
    ):
        self.can_dig = can_dig  # dig_to works (a real body with DIG_TERRAIN)
        self.sight = sight  # find_blocks / entities only within this distance
        self.k, self.w, self.inv = knowledge, dict(world), dict(inventory or {})
        self.pos, self.ents, self._food = pos, [dict(e) for e in entities or []], food
        self.default, self.drift = default, dict(drift or {})
        self.food_per_op = food_per_op
        self.furnaces: dict[Pos, dict] = {}
        self.boxes: dict[Pos, dict[str, int]] = {}
        self.sheared: set[str] = set()
        self.log: list[tuple] = []
        self.violations: list[str] = []
        self._ids = count(1)

    # --- queries ---------------------------------------------------------
    def world(self, x, y, z):
        return self.w.get((x, y, z), self.default)

    def position(self):
        return self.pos

    def inventory(self):
        return {i: n for i, n in self.inv.items() if n > 0}

    def food(self):
        return self._food

    def _seen(self, at) -> bool:
        return self.sight is None or math.dist(at, self.pos) <= self.sight

    def entities(self):
        return [dict(e) for e in self.ents if self._seen(e["position"])]

    def find_blocks(self, block):
        return [
            p
            for p, s in self.w.items()
            if bp.parse_state(s)[0] == block and self._seen(p)
        ]

    def find_all(self, block):
        return self.find_blocks(block)  # the fake has no occlusion either way

    def wait_ticks(self, ticks):
        self.log.append(("wait", ticks))

    # --- movement --------------------------------------------------------
    def goto_stance(self, target_pos, reach, avoid: Collection[Pos] = ()):
        self.log.append(("goto", tuple(target_pos)))
        tx, ty, tz = target_pos
        for dx, dy, dz in OFFSETS:
            spot = (tx + dx, ty + dy, tz + dz)
            if spot not in avoid:
                self.pos = spot
                return True
        return False

    def dig_to(self, feet):
        """Planner dig stand-in: clear the feet and head cells, stand there."""
        if not self.can_dig:
            return False
        self.log.append(("dig_to", tuple(feet)))
        x, y, z = feet
        for cell in ((x, y, z), (x, y + 1, z)):
            if cell in self.w:
                self.w[cell] = "minecraft:air"
        return self.walk_to(feet)

    def walk_to(self, pos, radius=1.5):
        self.log.append(("walk", tuple(pos)))
        self.pos = tuple(pos)
        for e in [e for e in self.ents if e.get("item")]:
            if math.dist(e["position"], self.pos) <= PICKUP:
                self.ents.remove(e)
                self.inv[e["item"]] = self.inv.get(e["item"], 0) + e.get("count", 1)
        return True

    # --- block ops -------------------------------------------------------
    def _tier(self, kind: str | None) -> str:
        best, rank = "hand", 0
        for item in self.inventory():
            tool = self.k.items.get(item, {}).get("tool")
            if tool and tool["kind"] == kind:
                tier = tool.get("tier", "")
                r = self.k.drop_tiers.index(tier) if tier in self.k.drop_tiers else 0
                if r > rank:
                    best, rank = tier, r
        return best

    def _drop_item(self, item: str, count: int, at) -> None:
        self.ents.append(
            {
                "uuid": f"drop-{next(self._ids)}",
                "type": "minecraft:item",
                "position": [at[0] + 0.5, at[1], at[2] + 0.5],
                "item": item,
                "count": count,
            }
        )

    def break_block(self, pos, tool=None):
        state = self.w.get(pos)
        ident = bp.parse_state(state)[0] if state else None
        if ident is None or ident in bp.AIR:
            return Result(False, "nothing_to_break")
        info = self.k.blocks.get(ident, {})
        mineable = info.get("mineable") or [None]
        drops = (
            self.k.drops_for(ident, self._tier(mineable[0]))
            if ident in self.k.drops
            else ()
        )
        if info.get("needs_correct_tool") and not drops:
            return Result(False, "wrong_tool")
        self.w[pos] = "minecraft:air"
        for d in drops or (
            (self.k.drops_for(ident, "hand")) if ident in self.k.drops else ()
        ):
            self._drop_item(d.item, max(1, d.min), pos)
        if not self.k.drops.get(ident):
            self._drop_item(info.get("item", ident), 1, pos)
        self._hunger()
        return Result(True, data={"broken": state})

    def place_block(self, pos, state, item):
        if self.inv.get(item, 0) < 1:
            return Result(False, "missing_item")
        if bp.parse_state(self.w.get(pos, "minecraft:air"))[0] not in bp.REPLACEABLE:
            return Result(False, "occupied")
        self.inv[item] -= 1
        self.w[pos] = state
        return Result(True, data={"placed": state})

    def use_block(self, pos, item=None):
        state = self.w.get(pos)
        if state is None:
            return Result(False, "nothing_to_use")
        ident, props = bp.parse_state(state)
        for t in self.k.transforms:
            if t.input == ident and item and item.endswith("_" + t.tool):
                self.w[pos] = t.output
                return Result(True, data={"before": state, "after": t.output})
        if "open" in props:
            after = bp.format_state(
                ident, {**props, "open": "false" if props["open"] == "true" else "true"}
            )
            self.w[pos] = after
            return Result(True, data={"before": state, "after": after})
        return Result(False, "no_effect")

    # --- crafting --------------------------------------------------------
    def _near(self, block: str, pos: Pos | None) -> bool:
        return pos is not None and bp.parse_state(self.w.get(pos, ""))[0] == block

    def _take(self, recipe, times: int) -> bool:
        need: dict[str, int] = {}
        for ing in recipe.ingredients:
            options = [x for o in ing.options for x in self.k.expand(o)]
            item = next(
                (x for x in options if self.inv.get(x, 0) >= ing.count * times), None
            )
            if item is None:
                return False
            need[item] = need.get(item, 0) + ing.count * times
        for item, n in need.items():
            self.inv[item] -= n
        return True

    def craft(self, recipe, times, table=None):
        rec = next((r for r in self.k.recipes if r.id == recipe), None)
        if rec is None:
            return Result(False, "unknown_recipe")
        if rec.station == "crafting_table" and not self._near(
            "minecraft:crafting_table", table
        ):
            return Result(False, "no_table")
        if not self._take(rec, times):
            return Result(False, "missing_item")
        out = rec.result_count * times
        self.inv[rec.result_item] = self.inv.get(rec.result_item, 0) + out
        return Result(True, data={"crafted": out})

    def furnace(self, pos, action, **fields):
        if not self._near("minecraft:furnace", pos):
            return Result(False, "not_a_furnace")
        box = self.furnaces.setdefault(pos, {"input": None, "n": 0, "fuel": 0})
        if action == "load":
            item, n = fields["input"], fields["count"]
            if (
                self.inv.get(item, 0) < n
                or self.inv.get(fields["fuel"], 0) < fields["fuel_count"]
            ):
                return Result(False, "missing_item")
            self.inv[item] -= n
            self.inv[fields["fuel"]] -= fields["fuel_count"]
            box.update(input=item, n=box["n"] + n)
            return Result(True)
        rec = next(
            (
                r
                for r in self.k.recipes
                if r.cook_ticks
                and any(
                    box["input"] in self.k.expand(o)
                    for i in r.ingredients
                    for o in i.options
                )
            ),
            None,
        )
        if rec is None or not box["n"]:
            return Result(False, "nothing_to_collect")
        self.inv[rec.result_item] = (
            self.inv.get(rec.result_item, 0) + box["n"] * rec.result_count
        )
        box.update(input=None, n=0)
        return Result(True, data={"collected": rec.result_item})

    def equip(self, item):
        return Result(
            self.inv.get(item, 0) > 0,
            None if self.inv.get(item, 0) > 0 else "missing_item",
        )

    # --- entities --------------------------------------------------------
    def _entity(self, uuid: str):
        return next((e for e in self.ents if e.get("uuid") == uuid), None)

    def _reachable(self, e) -> bool:
        if (
            self.drift.get(e["uuid"], 0) > 0
            and math.dist(e["position"], self.pos) <= ENTITY_REACH + 0.5
        ):
            self.drift[e["uuid"]] -= 1
            e["position"] = [e["position"][0] + 6, e["position"][1], e["position"][2]]
        return math.dist(e["position"], self.pos) <= ENTITY_REACH + 0.5

    def attack_entity(self, entity, weapon=None):
        e = self._entity(entity)
        if e is None:
            return Result(False, "not_found")
        if not self._reachable(e):
            return Result(False, "out_of_reach")
        self.ents.remove(e)
        for d in self.k.mob_drops(e["type"]):
            self._drop_item(
                d.item, max(1, d.min), tuple(math.floor(v) for v in e["position"])
            )
        self._hunger()
        return Result(True, data={"killed": True})

    def use_entity(self, entity, item=None):
        e = self._entity(entity)
        if e is None:
            return Result(False, "not_found")
        if not self._reachable(e):
            return Result(False, "out_of_reach")
        if (
            e["type"] != "minecraft:sheep"
            or item != "minecraft:shears"
            or entity in self.sheared
        ):
            return Result(False, "no_effect")
        self.sheared.add(entity)
        self._drop_item(
            "minecraft:white_wool", 2, tuple(math.floor(v) for v in e["position"])
        )
        return Result(True)

    def eat(self, item=None):
        info = self.k.foods.get(item or "", None)
        if info is None or self.inv.get(item, 0) < 1:
            return Result(False, "no_food")
        self.inv[item] -= 1
        self._food = min(20, self._food + int(info["nutrition"]))
        return Result(True, data={"item": item})

    def container(self, pos, action, items=None):
        if not self._near("minecraft:chest", pos):
            return Result(False, "not_a_container")
        box = self.boxes.setdefault(pos, {})
        wanted = (
            items
            if items is not None
            else [
                {"item": i, "count": n}
                for i, n in self.inventory().items()
                if "pickaxe" not in i
            ]
        )
        for stack in wanted:
            i, n = stack["item"], stack["count"]
            src, dst = (self.inv, box) if action == "deposit" else (box, self.inv)
            if src.get(i, 0) < n:
                return Result(False, "missing_item")
            src[i] -= n
            dst[i] = dst.get(i, 0) + n
        contents = [
            {"slot": s, "item": i, "count": n}
            for s, (i, n) in enumerate(box.items())
            if n > 0
        ]
        return Result(True, data={"contents": contents})

    def _hunger(self) -> None:
        self._food = max(0, self._food - self.food_per_op)
