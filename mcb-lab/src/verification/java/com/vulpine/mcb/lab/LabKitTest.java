package com.vulpine.mcb.lab;

import com.google.gson.JsonParser;

/** Kit origin, stack lists and the natural session's goal height (scaffold challenges). */
public final class LabKitTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static void rejects(Runnable run, String reason) {
        try { run.run(); } catch (IllegalArgumentException e) { check(e.getMessage().equals(reason), reason + " got " + e.getMessage()); return; }
        throw new AssertionError("accepted, expected " + reason);
    }

    public static void main(String[] args) {
        var plain = JsonParser.parseString("{}").getAsJsonObject();
        check(java.util.Arrays.equals(LabKit.origin(plain, 1, 2, 3), new int[]{1, 2, 3}), "no at: the body's feet");
        var at = JsonParser.parseString("{\"at\":[990,80,988]}").getAsJsonObject();
        check(java.util.Arrays.equals(LabKit.origin(at, 1, 2, 3), new int[]{990, 80, 988}), "at: a fixed origin");
        rejects(() -> LabKit.origin(JsonParser.parseString("{\"at\":[1,2]}").getAsJsonObject(), 0, 0, 0), "invalid_at");

        var stacks = LabKit.stackSpecs(JsonParser.parseString(
                "[{\"item\":\"minecraft:scaffolding\",\"count\":32},{\"item\":\"minecraft:iron_pickaxe\",\"count\":1}]").getAsJsonArray());
        check(stacks.size() == 2 && stacks.get(0).getKey().equals("minecraft:scaffolding") && stacks.get(0).getValue() == 32, "stacks in order");
        rejects(() -> LabKit.stackSpecs(JsonParser.parseString("[{\"item\":\"minecraft:dirt\",\"count\":0}]").getAsJsonArray()), "invalid_count");
        rejects(() -> LabKit.stackSpecs(JsonParser.parseString("[{\"item\":\"\",\"count\":1}]").getAsJsonArray()), "unknown_item");
        var many = new StringBuilder("[");
        for (int i = 0; i < 65; i++) many.append(i == 0 ? "" : ",").append("{\"item\":\"minecraft:dirt\",\"count\":1}");
        rejects(() -> LabKit.stackSpecs(JsonParser.parseString(many + "]").getAsJsonArray()), "too_many_stacks");

        check(NaturalRun.goalDy(plain) == null, "no goal_dy: the default height and no ground rule");
        check(NaturalRun.goalDy(JsonParser.parseString("{\"goal_dy\":0.5}").getAsJsonObject()) == 0.5, "goal_dy kept");
        rejects(() -> NaturalRun.goalDy(JsonParser.parseString("{\"goal_dy\":0.1}").getAsJsonObject()), "natural_goal_dy");
        rejects(() -> NaturalRun.goalDy(JsonParser.parseString("{\"goal_dy\":5}").getAsJsonObject()), "natural_goal_dy");
        System.out.println("LabKitTest ok");
    }
}
