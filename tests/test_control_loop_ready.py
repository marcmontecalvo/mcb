from mclab.control_loop import READY_TICKS, ready
from mclab.terrain_steps import Inventory, building_supply, hotbar


class Client:
    def __init__(self):
        self.sent = []

    def send(self, op, **fields):
        self.sent.append((op, fields))
        return {"sequence": len(self.sent)}


def obs(tick, rows):
    return {"tick": tick, "body": {"inventory": rows}}


ROWS = [
    {"slot": 0, "item": "minecraft:oak_planks", "count": 64},
    {"slot": 1, "item": "minecraft:dirt", "count": 3},
    {"slot": 20, "item": "minecraft:iron_axe", "count": 1},
    {"slot": 31, "item": "minecraft:cobblestone", "count": 40},
]


def test_an_item_held_only_in_the_main_inventory_is_usable():
    inv = Inventory(dict(r) for r in ROWS)
    assert hotbar(inv, "minecraft:iron_axe") == 20
    assert hotbar(inv, "minecraft:dirt") == 1  # the hotbar copy first
    # supply counts every slot; the first material held is used
    cobble = ("minecraft:cobblestone",)
    assert building_supply(inv, 0, cobble) == 31


def test_a_decision_on_a_main_inventory_slot_brings_the_item_up_once():
    client, readied = Client(), {}
    decision = {"yaw_delta": 3.0, "mine": True, "hotbar_slot": 20}
    out = ready(client, obs(100, ROWS), decision, readied)
    assert client.sent == [("equip", {"item": "minecraft:iron_axe", "slot": 2})]
    assert out == {"yaw_delta": 3.0, "hotbar_slot": 2}  # turns, but does not mine yet
    ready(client, obs(101, ROWS), decision, readied)
    assert len(client.sent) == 1  # waits for the view to show it before asking again
    ready(client, obs(100 + READY_TICKS, ROWS), decision, readied)
    assert len(client.sent) == 2


def test_a_hotbar_decision_passes_through():
    client = Client()
    decision = {"mine": True, "hotbar_slot": 1}
    assert ready(client, obs(5, ROWS), decision, {}) is decision
    assert client.sent == []
