"""The --supply stock of scripts/run-build.py: the whole house, ready to build."""

import json
import runpy
from collections import Counter
from pathlib import Path

import pytest

from mclab.companion_jobs import load_vanilla_pack, materials_of

ROOT = Path(__file__).parents[1]
HOUSE = ROOT / "tests/fixtures/schematics/house-large.build.json"
CHEST_SLOTS = 27


@pytest.fixture(scope="module")
def program():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return runpy.run_path(str(ROOT / "scripts/run-build.py"))
    finally:
        sys.path.remove(str(ROOT / "scripts"))


def test_the_supply_holds_every_block_of_the_house(program):
    knowledge = load_vanilla_pack()
    build = json.loads(HOUSE.read_text(encoding="utf-8"))
    give = program["supply_contents"](build, knowledge, "item")
    for item, n in materials_of(build, knowledge).items():
        if item in program["COAL_CONSUMERS"]:
            continue  # crafted from the coal form, on purpose
        assert give.get(item, 0) >= n, item


def test_the_supply_holds_two_stacks_of_scaffolding_in_the_first_chest(program):
    knowledge = load_vanilla_pack()
    build = json.loads(HOUSE.read_text(encoding="utf-8"))
    tools = Counter()
    for stack in program["BIN_TOOLS"]:
        tools[stack["item"]] += stack["count"]
    assert tools["minecraft:scaffolding"] == 128

    give = program["supply_contents"](build, knowledge, "item")
    bins = program["supply_bins"](give, knowledge)
    assert len(program["BIN_TOOLS"]) + len(bins[0]) <= CHEST_SLOTS
    assert all(len(b) <= CHEST_SLOTS for b in bins)
