package com.vulpine.mcb.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Pure logic of the knowledge pack (docs/KNOWLEDGE_PACK_FORMAT.md): no Minecraft types, so it is
 * checked by KnowledgeLogicTest. KnowledgeDump feeds it registry data and codec-encoded recipes.
 */
final class KnowledgeLogic {
    private KnowledgeLogic() {}

    /** "<game version>-<first 12 hex of sha256 over the sorted lines>". */
    static String packId(String gameVersion, List<String> lines) {
        var sorted = new ArrayList<>(lines);
        sorted.sort(null);
        try {
            var digest = MessageDigest.getInstance("SHA-256").digest(String.join("\n", sorted).getBytes(StandardCharsets.UTF_8));
            var hex = new StringBuilder();
            for (byte b : digest) hex.append(String.format("%02x", b));
            return gameVersion + "-" + hex.substring(0, 12);
        } catch (java.security.NoSuchAlgorithmException error) { throw new IllegalStateException(error); }
    }

    // ---- needs_support: first class in the block's superclass chain (most derived first) that is listed wins.
    private static final Map<String, String> SUPPORT = new HashMap<>();
    private static void rule(String support, String... classes) { for (var c : classes) SUPPORT.put(c, support); }
    static {
        rule("none", "FireBlock", "BaseFireBlock", "SoulFireBlock", "FlowerPotBlock", "LanternBlock", "ChainBlock",
                "ScaffoldingBlock", "PointedDripstoneBlock");
        rule("facing_opposite", "WallTorchBlock", "RedstoneWallTorchBlock", "LadderBlock", "WallSignBlock",
                "WallBannerBlock", "WallSkullBlock", "PlayerWallHeadBlock", "WitherWallSkullBlock", "PiglinWallSkullBlock",
                "TripWireHookBlock", "BaseCoralWallFanBlock", "CoralWallFanBlock", "AmethystClusterBlock");
        rule("any_face", "FaceAttachedHorizontalDirectionalBlock", "ButtonBlock", "LeverBlock");
        rule("above", "HangingRootsBlock", "SporeBlossomBlock", "HangingMossBlock", "CaveVinesBlock", "CaveVinesPlantBlock",
                "WeepingVinesBlock", "WeepingVinesPlantBlock", "CeilingHangingSignBlock");
        rule("below", "TorchBlock", "RedstoneTorchBlock", "CarpetBlock", "WoolCarpetBlock", "MossyCarpetBlock", "DoorBlock",
                "VegetationBlock", "BushBlock", "DoublePlantBlock", "StandingSignBlock", "BannerBlock", "DiodeBlock",
                "BaseRailBlock", "BasePressurePlateBlock", "RedStoneWireBlock", "RedstoneWireBlock", "SnowLayerBlock",
                "SugarCaneBlock", "CactusBlock", "KelpBlock", "KelpPlantBlock", "SeagrassBlock", "TallSeagrassBlock",
                "TwistingVinesBlock", "TwistingVinesPlantBlock", "AbstractCandleBlock", "CakeBlock", "LilyPadBlock",
                "BaseCoralPlantTypeBlock", "CoralFanBlock", "SeaPickleBlock", "TurtleEggBlock", "FlowerBedBlock",
                "StemBlock", "AttachedStemBlock", "CropBlock", "SaplingBlock", "FlowerBlock", "NetherWartBlock");
    }

    /** null when no rule applies (also for the explicit "none" rules). */
    static String needsSupport(List<String> classChain) {
        for (var name : classChain) {
            var value = SUPPORT.get(name);
            if (value != null) return value.equals("none") ? null : value;
        }
        return null;
    }

    // ---- recipes: input is Recipe.DIRECT_CODEC output (JSON), output is the recipes.json entry.
    private static final Map<String, String> COOKING_STATIONS = Map.of(
            "minecraft:smelting", "furnace", "minecraft:blasting", "blast_furnace", "minecraft:smoking", "smoker",
            "minecraft:campfire_cooking", "campfire", "minecraft:stonecutting", "stonecutter");

    static JsonObject recipe(String id, JsonObject encoded) {
        try {
            var parsed = parse(id, encoded);
            if (parsed != null) return parsed;
        } catch (RuntimeException ignored) { /* kept raw below */ }
        var out = new JsonObject();
        out.addProperty("id", id);
        out.addProperty("type", encoded.has("type") ? encoded.get("type").getAsString() : "unknown");
        out.add("station", com.google.gson.JsonNull.INSTANCE);
        out.add("raw", encoded);
        return out;
    }

    private static JsonObject parse(String id, JsonObject enc) {
        String type = enc.get("type").getAsString();
        var out = new JsonObject();
        out.addProperty("id", id);
        out.addProperty("type", type);
        var slots = new ArrayList<List<String>>();
        switch (type) {
            case "minecraft:crafting_shaped" -> {
                var rows = new ArrayList<String>();
                for (var row : enc.getAsJsonArray("pattern")) rows.add(row.getAsString());
                var pattern = shrink(rows);
                var key = enc.getAsJsonObject("key");
                var usedKey = new JsonObject();
                for (var row : pattern) for (char c : row.toCharArray()) if (c != ' ') {
                    var options = options(key.get(String.valueOf(c)));
                    slots.add(options);
                    usedKey.add(String.valueOf(c), strings(options));
                }
                int width = pattern.isEmpty() ? 0 : pattern.get(0).length(), height = pattern.size();
                out.addProperty("station", width <= 2 && height <= 2 ? "inventory" : "crafting_table");
                out.addProperty("width", width);
                out.addProperty("height", height);
                var rowsJson = new JsonArray();
                pattern.forEach(rowsJson::add);
                out.add("pattern", rowsJson);
                out.add("key", usedKey);
            }
            case "minecraft:crafting_shapeless" -> {
                for (var e : enc.getAsJsonArray("ingredients")) slots.add(options(e));
                out.addProperty("station", slots.size() <= 4 ? "inventory" : "crafting_table");
            }
            case "minecraft:crafting_transmute" -> { // input + material -> result (recolouring, shulker boxes, bundles)
                slots.add(options(enc.get("input")));
                slots.add(options(enc.get("material")));
                out.addProperty("station", "inventory");
            }
            case "minecraft:crafting_dye" -> { // target + dye -> dyed target
                slots.add(options(enc.get("target")));
                slots.add(options(enc.get("dye")));
                out.addProperty("station", "inventory");
            }
            case "minecraft:smithing_transform" -> {
                for (var part : List.of("template", "base", "addition")) if (enc.has(part)) slots.add(options(enc.get(part)));
                out.addProperty("station", "smithing_table");
            }
            default -> {
                var station = COOKING_STATIONS.get(type);
                if (station == null) return null;
                slots.add(options(enc.get("ingredient")));
                out.addProperty("station", station);
                if (enc.has("cookingtime")) out.add("cook_ticks", enc.get("cookingtime"));
            }
        }
        out.add("ingredients", aggregate(slots));
        out.add("result", result(enc.get("result")));
        return out;
    }

    /** Drop empty rows and columns, as ShapedRecipePattern.shrink does. */
    static List<String> shrink(List<String> rows) {
        int top = 0, bottom = rows.size();
        while (top < bottom && rows.get(top).isBlank()) top++;
        while (bottom > top && rows.get(bottom - 1).isBlank()) bottom--;
        int left = Integer.MAX_VALUE, right = -1;
        for (int i = top; i < bottom; i++) {
            var row = rows.get(i);
            for (int c = 0; c < row.length(); c++) if (row.charAt(c) != ' ') { left = Math.min(left, c); right = Math.max(right, c); }
        }
        var out = new ArrayList<String>();
        for (int i = top; i < bottom; i++) out.add(rows.get(i).substring(left, right + 1));
        return out;
    }

    /** An ingredient is a string ("minecraft:x" or "#tag") or a list of them. */
    static List<String> options(JsonElement ingredient) {
        var out = new ArrayList<String>();
        if (ingredient.isJsonPrimitive()) out.add(ingredient.getAsString());
        else for (var e : ingredient.getAsJsonArray()) out.add(e.getAsString());
        if (out.isEmpty()) throw new IllegalArgumentException("empty_ingredient");
        return out;
    }

    private static JsonArray strings(List<String> values) {
        var array = new JsonArray();
        values.forEach(array::add);
        return array;
    }

    /** Each distinct slot ingredient with how many slots use it, in order of first appearance. */
    static JsonArray aggregate(List<List<String>> slots) {
        var counts = new LinkedHashMap<List<String>, Integer>();
        for (var slot : slots) counts.merge(slot, 1, Integer::sum);
        var out = new JsonArray();
        counts.forEach((options, count) -> {
            var entry = new JsonObject();
            entry.add("options", strings(options));
            entry.addProperty("count", count);
            out.add(entry);
        });
        return out;
    }

    private static JsonObject result(JsonElement result) {
        var out = new JsonObject();
        if (result.isJsonPrimitive()) { out.addProperty("item", result.getAsString()); out.addProperty("count", 1); return out; }
        var obj = result.getAsJsonObject();
        out.addProperty("item", obj.get("id").getAsString());
        out.addProperty("count", obj.has("count") ? obj.get("count").getAsInt() : 1);
        return out;
    }

    // ---- loot roll statistics (blocks and mobs)

    /** True when the rolls are not all identical (so more rolls are worth it). */
    static boolean varies(List<Map<String, Integer>> rolls) {
        for (var roll : rolls) if (!roll.equals(rolls.get(0))) return true;
        return false;
    }

    /** Per item: min/max count over the rolls that dropped it, and chance (if under 1) as the share of rolls. */
    static JsonArray summarize(List<Map<String, Integer>> rolls) {
        var stats = new java.util.TreeMap<String, int[]>(); // min, max, rolls that dropped it
        for (var roll : rolls) roll.forEach((item, count) -> stats.merge(item, new int[]{count, count, 1},
                (a, b) -> new int[]{Math.min(a[0], b[0]), Math.max(a[1], b[1]), a[2] + b[2]}));
        var out = new JsonArray();
        stats.forEach((item, v) -> {
            var drop = new JsonObject();
            drop.addProperty("item", item);
            drop.addProperty("min", v[0]);
            drop.addProperty("max", v[1]);
            if (v[2] < rolls.size()) drop.addProperty("chance", Math.round(1000.0 * v[2] / rolls.size()) / 1000.0);
            out.add(drop);
        });
        return out;
    }
}
