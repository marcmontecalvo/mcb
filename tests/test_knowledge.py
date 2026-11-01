import json
import shutil
from pathlib import Path

import pytest

from mclab.knowledge import KnowledgePackError, load_pack

MINI = Path(__file__).parent / "fixtures" / "knowledge" / "mini"
M = "minecraft:"


def test_tag_options_expand_and_match_member_items():
    k = load_pack(MINI)

    assert k.matches("#minecraft:planks", M + "oak_planks")
    assert not k.matches("#minecraft:planks", M + "stick")
    assert k.matches(M + "stick", M + "stick")
    assert k.expand("#minecraft:oak_logs") == (M + "oak_log", M + "oak_wood")


def test_recipes_are_indexed_by_result_item():
    k = load_pack(MINI)

    ids = {r.id for r in k.recipes_for(M + "iron_ingot")}

    assert M + "iron_ingot_from_smelting_raw_iron" in ids
    assert M + "iron_ingot_from_iron_block" in ids
    assert k.recipes_for(M + "nether_star") == ()


def test_drops_by_tier_fall_through_to_the_next_lower_tier():
    k = load_pack(MINI)

    assert k.drops_for(M + "stone", "hand") == ()
    assert [d.item for d in k.drops_for(M + "stone", "iron")] == [M + "cobblestone"]
    assert [d.item for d in k.drops_for(M + "iron_ore", "stone")] == [M + "raw_iron"]
    assert k.drops_for(M + "iron_ore", "wood") == ()


def test_lookups_are_immutable():
    k = load_pack(MINI)

    with pytest.raises(TypeError):
        k.items[M + "stick"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        k.sources[M + "stone"]["where"] = "x"  # type: ignore[index]


def test_unsupported_schema_fails_clearly(tmp_path):
    bad = tmp_path / "pack"
    shutil.copytree(MINI, bad)
    (bad / "items.json").write_text(
        json.dumps({"schema": 2, "items": {}}), encoding="utf-8"
    )

    with pytest.raises(KnowledgePackError, match="unsupported schema 2"):
        load_pack(bad)


def test_missing_file_fails_clearly(tmp_path):
    bad = tmp_path / "pack"
    shutil.copytree(MINI, bad)
    (bad / "drops.json").unlink()

    with pytest.raises(KnowledgePackError, match="drops.json"):
        load_pack(bad)


def test_vanilla_sources_file_loads_as_override():
    path = Path(__file__).parent.parent / "knowledge" / "sources" / "vanilla-26.3.json"

    k = load_pack(MINI, sources=path)

    assert k.sources[M + "diamond_ore"]["where"] == "underground"
    assert k.sources[M + "oak_log"]["where"] == "tree"
    assert len(k.sources) >= 30
