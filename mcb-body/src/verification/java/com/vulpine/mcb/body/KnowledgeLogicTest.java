package com.vulpine.mcb.body;

import com.google.gson.JsonParser;

import java.util.List;

public final class KnowledgeLogicTest {
    private static int checks;
    private static void check(boolean yes, String label) { checks++; if (!yes) throw new AssertionError(label); }
    private static com.google.gson.JsonObject json(String text) { return JsonParser.parseString(text).getAsJsonObject(); }

    public static void main(String[] args) {
        String id = KnowledgeLogic.packId("26.3", List.of("b@2", "a@1", "vanilla"));
        check(id.matches("26[.]3-[0-9a-f]{12}"), "pack id shape " + id);
        check(id.equals(KnowledgeLogic.packId("26.3", List.of("vanilla", "a@1", "b@2"))), "pack id ignores order");
        check(!id.equals(KnowledgeLogic.packId("26.3", List.of("a@1", "b@3", "vanilla"))), "pack id changes with a mod version");

        check("facing_opposite".equals(KnowledgeLogic.needsSupport(List.of("WallTorchBlock", "BaseTorchBlock", "Block"))), "wall torch");
        check("below".equals(KnowledgeLogic.needsSupport(List.of("TorchBlock", "BaseTorchBlock", "Block"))), "torch");
        check("below".equals(KnowledgeLogic.needsSupport(List.of("WoolCarpetBlock", "CarpetBlock", "Block"))), "carpet");
        check("any_face".equals(KnowledgeLogic.needsSupport(List.of("ButtonBlock", "FaceAttachedHorizontalDirectionalBlock", "Block"))), "button");
        check("below".equals(KnowledgeLogic.needsSupport(List.of("SaplingBlock", "VegetationBlock", "BushBlock", "Block"))), "sapling");
        check(KnowledgeLogic.needsSupport(List.of("FireBlock", "BaseFireBlock", "Block")) == null, "fire needs none");
        check(KnowledgeLogic.needsSupport(List.of("StairBlock", "Block")) == null, "stairs need none");
        check(KnowledgeLogic.needsSupport(List.of("MyModBlock", "Block")) == null, "unknown mod block");

        var pickaxe = KnowledgeLogic.recipe("minecraft:wooden_pickaxe", json("""
            {"type":"minecraft:crafting_shaped","pattern":["XXX"," # "," # "],
             "key":{"X":"#minecraft:planks","#":"minecraft:stick"},"result":{"id":"minecraft:wooden_pickaxe","count":1}}"""));
        check(pickaxe.get("station").getAsString().equals("crafting_table"), "pickaxe station");
        check(pickaxe.get("ingredients").toString().equals(
                "[{\"options\":[\"#minecraft:planks\"],\"count\":3},{\"options\":[\"minecraft:stick\"],\"count\":2}]"), "pickaxe ingredients " + pickaxe.get("ingredients"));
        check(pickaxe.get("width").getAsInt() == 3 && pickaxe.get("height").getAsInt() == 3, "pickaxe size");

        var sticks = KnowledgeLogic.recipe("minecraft:stick", json("""
            {"type":"minecraft:crafting_shaped","pattern":["#","#"],"key":{"#":"#minecraft:planks"},
             "result":{"id":"minecraft:stick","count":4}}"""));
        check(sticks.get("station").getAsString().equals("inventory"), "stick fits 2x2");
        check(sticks.get("result").getAsJsonObject().get("count").getAsInt() == 4, "stick count");

        var shrunk = KnowledgeLogic.shrink(List.of("   ", " X ", "   "));
        check(shrunk.equals(List.of("X")), "shrink to one cell");

        var planks = KnowledgeLogic.recipe("minecraft:oak_planks", json("""
            {"type":"minecraft:crafting_shapeless","ingredients":["#minecraft:oak_logs"],
             "result":{"id":"minecraft:oak_planks","count":4}}"""));
        check(planks.get("station").getAsString().equals("inventory"), "planks station");

        var smelt = KnowledgeLogic.recipe("minecraft:iron_ingot_from_smelting_raw_iron", json("""
            {"type":"minecraft:smelting","ingredient":["minecraft:raw_iron","minecraft:iron_ore"],"experience":0.7,"cookingtime":200,
             "result":{"id":"minecraft:iron_ingot"}}"""));
        check(smelt.get("station").getAsString().equals("furnace") && smelt.get("cook_ticks").getAsInt() == 200, "smelting");
        check(smelt.getAsJsonArray("ingredients").get(0).getAsJsonObject().getAsJsonArray("options").size() == 2, "alternatives kept");

        var mod = KnowledgeLogic.recipe("create:crushing/zinc_ore", json("{\"type\":\"create:crushing\",\"x\":1}"));
        check(mod.get("station").isJsonNull() && mod.has("raw") && !mod.has("ingredients"), "unknown type kept raw");
        var broken = KnowledgeLogic.recipe("x:y", json("{\"type\":\"minecraft:smelting\"}"));
        check(broken.get("station").isJsonNull() && broken.has("raw"), "malformed recipe kept raw");

        var dye = KnowledgeLogic.recipe("minecraft:leather_boots_dyed", json("""
            {"type":"minecraft:crafting_dye","target":"minecraft:leather_boots","dye":"#minecraft:dyes","result":{"id":"minecraft:leather_boots"}}"""));
        check(dye.get("station").getAsString().equals("inventory") && dye.getAsJsonArray("ingredients").size() == 2, "dye recipe parsed");
        var transmute = KnowledgeLogic.recipe("minecraft:black_bundle", json("""
            {"type":"minecraft:crafting_transmute","input":"#minecraft:bundles","material":"minecraft:black_dye","result":{"id":"minecraft:black_bundle"}}"""));
        check(transmute.get("station").getAsString().equals("inventory") && transmute.get("result").getAsJsonObject().get("item").getAsString().equals("minecraft:black_bundle"), "transmute parsed");

        var rolls = List.of(java.util.Map.of("a", 1, "b", 2), java.util.Map.of("a", 3), java.util.Map.of("a", 2, "b", 1), java.util.Map.of("a", 1));
        check(KnowledgeLogic.varies(rolls) && !KnowledgeLogic.varies(List.of(java.util.Map.of("a", 1), java.util.Map.of("a", 1))), "varies");
        var summary = KnowledgeLogic.summarize(rolls);
        check(summary.toString().equals("[{\"item\":\"a\",\"min\":1,\"max\":3},{\"item\":\"b\",\"min\":1,\"max\":2,\"chance\":0.5}]"), "summary " + summary);

        System.out.println("KnowledgeLogic: " + checks + " checks passed");
    }
}
