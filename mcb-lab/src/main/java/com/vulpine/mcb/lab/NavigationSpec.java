package com.vulpine.mcb.lab;

import java.util.List;
import java.util.Map;
import java.util.Set;

/** Pure admission checks for the separately scoped, development navigation profile. */
public final class NavigationSpec {
    public static final String INITIAL = "initial-c1-survival-v1";
    public static final String NAVIGATION = "navigation-c2-c5-survival-v1";
    private static final Map<String, Integer> LIMITS = Map.of("C1", 600, "C2", 900,
            "C3", 1200, "C4", 1200, "C5", 1800);
    private static final Set<String> BLOCKS = Set.of("minecraft:air", "minecraft:stone", "minecraft:oak_log");

    private NavigationSpec() {}

    public static void validate(String profile, Map<?, ?> spec, Map<?, ?> startup) {
        String course = text(spec.get("course_id"));
        boolean initial = INITIAL.equals(profile);
        if ((!initial && !NAVIGATION.equals(profile)) || !LIMITS.containsKey(course)
                || initial != course.equals("C1")) reject("unqualified_course_profile");
        if (integer(spec.get("schema_version")) != 1 || integer(spec.get("tick_limit")) != LIMITS.get(course)) {
            reject("unsupported_navigation_schema_or_deadline");
        }
        var origin = vector(spec.get("origin"));
        var original = vector(startup.get("origin"));
        for (int i = 0; i < 3; i++) {
            if (origin[i] != original[i]) reject("navigation_origin_changed");
        }
        if (origin[1] < 64 || origin[1] > 250 || Math.abs((long) origin[0]) > 29_000_000
                || Math.abs((long) origin[2]) > 29_000_000) reject("unsupported_navigation_origin");
        double yaw = number(spec.get("yaw")), pitch = number(spec.get("pitch"));
        if (yaw < -180 || yaw >= 180 || (float) yaw >= 180 || pitch != 0 || (initial && yaw != 0)) {
            reject("unsupported_navigation_heading");
        }
        var spawn = list(spec.get("spawn"));
        if (spawn.size() != 3 || number(spawn.get(0)) != 0.5 || number(spawn.get(1)) != 0
                || number(spawn.get(2)) != 0.5) reject("unsupported_navigation_spawn");
        region(spec.get("clear_region"), new int[]{-24,-20,-24}, new int[]{24,20,24});
        region(spec.get("allowed_region"), new int[]{-16,-4,-16}, new int[]{16,8,16});
        if (integer(spec.get("pit_plane")) != -5 || !list(spec.get("initial_inventory")).isEmpty()
                || !list(spec.get("place_whitelist")).isEmpty()
                || !text(spec.get("success")).equals("causal_target_break_drop_pickup")
                || !text(spec.get("track")).equals("perception_goal")) reject("unsupported_navigation_rules");
        var target = vector(spec.get("target"));
        if (target[1] != 0 || Math.abs((long) target[0]) > 15 || Math.abs((long) target[2]) > 15
                || (Math.abs(target[0]) <= 1 && Math.abs(target[2]) <= 1)) reject("unsupported_navigation_target");
        var breaks = list(spec.get("break_whitelist"));
        if (breaks.size() != 1 || !same(vector(breaks.get(0)), target)) reject("unsupported_navigation_break_whitelist");
        if (!text(spec.get("spec_hash")).matches("[a-f0-9]{64}")) reject("invalid_navigation_spec_hash");
        var geometry = list(spec.get("geometry"));
        if (geometry.isEmpty() || geometry.size() > 64) reject("unsupported_navigation_geometry_count");
        int logs = 0;
        for (Object entry : geometry) {
            var box = map(entry);
            var low = vector(box.get("from")); var high = vector(box.get("to"));
            for (int i = 0; i < 3; i++) {
                int minimum = i == 1 ? -4 : -16, maximum = i == 1 ? 8 : 16;
                if (low[i] < minimum || high[i] > maximum || low[i] > high[i]) reject("navigation_geometry_outside_arena");
            }
            String block = text(box.get("block"));
            if (!BLOCKS.contains(block)) reject("unsupported_navigation_block");
            if (block.equals("minecraft:oak_log")) {
                logs++;
                if (!same(low, target) || !same(high, target)) reject("navigation_log_does_not_match_target");
            }
        }
        if (logs != 1 || !blockAt(geometry, target[0], target[1], target[2]).equals("minecraft:oak_log")) {
            reject("navigation_requires_one_target_log");
        }
        if (!blockAt(geometry, 0, -1, 0).equals("minecraft:stone")
                || !blockAt(geometry, 0, 0, 0).equals("minecraft:air")
                || !blockAt(geometry, 0, 1, 0).equals("minecraft:air")) reject("navigation_spawn_obstructed");
    }

    private static String blockAt(List<?> geometry, int x, int y, int z) {
        String result = "minecraft:air";
        for (var entry : geometry) {
            var box = map(entry); var low = vector(box.get("from")); var high = vector(box.get("to"));
            if (x >= low[0] && x <= high[0] && y >= low[1] && y <= high[1] && z >= low[2] && z <= high[2]) {
                result = text(box.get("block"));
            }
        }
        return result;
    }

    private static void region(Object value, int[] low, int[] high) {
        var region = map(value);
        if (!same(vector(region.get("min")), low) || !same(vector(region.get("max")), high)) {
            reject("unsupported_navigation_envelope");
        }
    }

    private static boolean same(int[] left, int[] right) { return java.util.Arrays.equals(left, right); }
    private static Map<?, ?> map(Object value) {
        if (!(value instanceof Map<?, ?> result)) throw new IllegalArgumentException("navigation_expected_object");
        return result;
    }
    private static List<?> list(Object value) {
        if (!(value instanceof List<?> result)) throw new IllegalArgumentException("navigation_expected_array");
        return result;
    }
    private static String text(Object value) {
        if (!(value instanceof String result)) throw new IllegalArgumentException("navigation_expected_string");
        return result;
    }
    private static double number(Object value) {
        if (!(value instanceof Number result) || !Double.isFinite(result.doubleValue())) {
            throw new IllegalArgumentException("navigation_expected_finite_number");
        }
        return result.doubleValue();
    }
    private static int integer(Object value) {
        double result = number(value);
        if (result < Integer.MIN_VALUE || result > Integer.MAX_VALUE || result != Math.rint(result)) {
            throw new IllegalArgumentException("navigation_expected_integer");
        }
        return (int) result;
    }
    private static int[] vector(Object value) {
        var values = list(value);
        if (values.size() != 3) throw new IllegalArgumentException("navigation_expected_three_coordinates");
        return new int[]{integer(values.get(0)), integer(values.get(1)), integer(values.get(2))};
    }
    private static void reject(String reason) { throw new IllegalArgumentException(reason); }
}
