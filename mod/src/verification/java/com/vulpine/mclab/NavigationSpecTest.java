package com.vulpine.mclab;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Admission tests require no Minecraft server, filesystem mutation or provider. */
public final class NavigationSpecTest {
    private static Map<String, Object> box(List<Integer> low, List<Integer> high, String block) {
        return new LinkedHashMap<>(Map.of("from", low, "to", high, "block", block));
    }

    private static Map<String, Object> spec(String course) {
        var value = new LinkedHashMap<String, Object>();
        value.put("schema_version", 1); value.put("course_id", course);
        value.put("tick_limit", switch(course) { case "C1" -> 600; case "C2" -> 900; case "C5" -> 1800; default -> 1200; });
        value.put("origin", List.of(1000, 80, 1000));
        value.put("spawn", List.of(0.5, 0, 0.5)); value.put("yaw", course.equals("C1") ? 0 : -135);
        value.put("pitch", 0); value.put("pit_plane", -5);
        value.put("clear_region", Map.of("min", List.of(-24,-20,-24), "max", List.of(24,20,24)));
        value.put("allowed_region", Map.of("min", List.of(-16,-4,-16), "max", List.of(16,8,16)));
        value.put("target", List.of(0,0,12));
        value.put("break_whitelist", List.of(List.of(0,0,12)));
        value.put("place_whitelist", List.of()); value.put("initial_inventory", List.of());
        value.put("success", "causal_target_break_drop_pickup"); value.put("track", "perception_goal");
        value.put("spec_hash", "a".repeat(64));
        value.put("geometry", new ArrayList<>(List.of(
                box(List.of(-16,-4,-16), List.of(16,-1,16), "minecraft:stone"),
                box(List.of(0,0,12), List.of(0,0,12), "minecraft:oak_log"))));
        return value;
    }

    private static void reject(Map<String, Object> value) {
        reject(NavigationSpec.NAVIGATION, value);
    }

    private static void reject(String profile, Map<String, Object> value) {
        try {
            NavigationSpec.validate(profile, value, spec("C1"));
            throw new AssertionError("invalid navigation specification accepted");
        } catch (IllegalArgumentException expected) {
            if (expected.getMessage() == null) throw new AssertionError("missing rejection reason");
        }
    }

    public static void main(String[] args) {
        NavigationSpec.validate(NavigationSpec.INITIAL, spec("C1"), spec("C1"));
        for (String course : List.of("C2", "C3", "C4", "C5")) {
            NavigationSpec.validate(NavigationSpec.NAVIGATION, spec(course), spec("C1"));
        }
        reject(NavigationSpec.INITIAL, spec("C2"));
        reject(NavigationSpec.NAVIGATION, spec("C1"));
        reject("unknown-profile", spec("C2"));
        for (double yaw : new double[]{Double.NaN, Double.POSITIVE_INFINITY, -181, 180, 179.9999999}) {
            var value = spec("C2"); value.put("yaw", yaw); reject(value);
        }
        for (int yaw : new int[]{-180, 0, 179}) {
            var value = spec("C2"); value.put("yaw", yaw);
            NavigationSpec.validate(NavigationSpec.NAVIGATION, value, spec("C1"));
        }
        for (var change : Map.<String,Object>ofEntries(
                Map.entry("tick_limit", 600), Map.entry("pitch", 1),
                Map.entry("origin", List.of(1001,80,1000)), Map.entry("spawn", List.of(0.5,0.1,0.5)),
                Map.entry("target", List.of(16,0,12)), Map.entry("break_whitelist", List.of(List.of(0,0,11))),
                Map.entry("place_whitelist", List.of(List.of(0,0,0))), Map.entry("initial_inventory", List.of("dirt")),
                Map.entry("spec_hash", "not-a-hash"), Map.entry("success", "inventory_count"),
                Map.entry("track", "coordinate_goal"), Map.entry("schema_version", 2),
                Map.entry("clear_region", Map.of("min", List.of(-25,-20,-24), "max", List.of(24,20,24)))
        ).entrySet()) {
            var value = spec("C2"); value.put(change.getKey(), change.getValue()); reject(value);
        }
        for (var extra : List.of(
                box(List.of(-17,0,1), List.of(-17,0,1), "minecraft:stone"),
                box(List.of(1,0,1), List.of(1,0,1), "minecraft:water"),
                box(List.of(0,1,0), List.of(0,1,0), "minecraft:stone"),
                box(List.of(0,0,12), List.of(0,0,12), "minecraft:air"),
                box(List.of(2,0,12), List.of(2,0,12), "minecraft:oak_log"))) {
            var value = spec("C2");
            var geometry = new ArrayList<>(List.of(
                    box(List.of(-16,-4,-16), List.of(16,-1,16), "minecraft:stone"),
                    box(List.of(0,0,12), List.of(0,0,12), "minecraft:oak_log"), extra));
            value.put("geometry", geometry); reject(value);
        }
        var invalidCoordinate = spec("C2");
        invalidCoordinate.put("target", List.of("0",0,12)); reject(invalidCoordinate);
        var emptyGeometry = spec("C2"); emptyGeometry.put("geometry", List.of()); reject(emptyGeometry);
        var unknownCourse = spec("C6"); reject(unknownCourse);
        var missingField = spec("C2"); missingField.remove("spec_hash"); reject(missingField);
        System.out.println("Navigation admission: legacy/profile separation, C2-C5, headings, bounds, rules and geometry verified");
    }
}
