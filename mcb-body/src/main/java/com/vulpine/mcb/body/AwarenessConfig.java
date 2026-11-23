package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.Map;
import java.util.Set;

/** Explicit development sensor configuration; the legacy profile is immutable. */
public record AwarenessConfig(String preset, int horizontalRadius, int verticalRadius,
                              int entityRadius, int entityVerticalRadius, String entityVisibility,
                              int interval, int extraCells, int extraMillis) {
    public AwarenessConfig {
        if (!Set.of("legacy", "visible", "chunk", "sections").contains(preset)
                || horizontalRadius < 1 || horizontalRadius > 64
                || verticalRadius < -1 || verticalRadius == 0 || verticalRadius > 64
                || (verticalRadius == -1 && !preset.equals("chunk") && !preset.equals("sections"))
                || entityRadius < 1 || entityRadius > 64
                || entityVerticalRadius < 1 || entityVerticalRadius > 64
                || !Set.of("visible", "unoccluded").contains(entityVisibility)
                || interval < 1 || interval > 20 || extraCells < 1 || extraCells > (preset.equals("sections") ? 65536 : 8192)
                || extraMillis < 1 || extraMillis > 10)
            throw new IllegalArgumentException("invalid_awareness_config");
        if (preset.equals("legacy") && (horizontalRadius != 8 || verticalRadius != 4
                || entityRadius != 8 || entityVerticalRadius != 4 || !entityVisibility.equals("visible")
                || interval != 5 || extraCells != 2048 || extraMillis != 3))
            throw new IllegalArgumentException("legacy_awareness_is_frozen");
    }

    public static AwarenessConfig legacy() {
        return new AwarenessConfig("legacy", 8, 4, 8, 4, "visible", 5, 2048, 3);
    }

    public String profile() { return preset.equals("legacy") ? "local-visibility-v1" : "awareness-v1"; }
    public boolean occluded() { return !preset.equals("chunk") && !preset.equals("sections"); }
    /** How far under an entity box's top the second sight ray aims (inside the box, never past it). */
    static final double SIGHT_TOP_INSET = 0.02;

    /**
     * The heights (at the box's horizontal centre) a "visible" entity is looked for: seen when any ray is clear.
     * Legacy looks at the centre only (frozen). A development preset also looks just under the top, as a player
     * sees any part of it: a drop sunk in a snow layer or grass has its centre at the cover's surface.
     */
    public double[] sightHeights(double minY, double maxY) {
        double centre = (minY + maxY) / 2;
        if (preset.equals("legacy")) return new double[] {centre};
        return new double[] {centre, Math.max(centre, maxY - SIGHT_TOP_INSET)};
    }

    /**
     * Whether the entity sensor reports an entity. A living one at health 0 stays in the world for its 20-tick
     * death animation; a development preset leaves it out of view, since reported it was fought again as a
     * target the body could not find (matrix 20261123T211141Z: two extra fight requests after every kill).
     * Its drops are item entities of their own, still seen for looting. Legacy reports it (frozen).
     */
    public boolean sensed(boolean living, boolean alive) {
        return preset.equals("legacy") || !living || alive;
    }

    /** Whole-section scanner over loaded chunks; requires the sections-v1 terrain encoding. */
    public boolean sections() { return preset.equals("sections"); }

    public Map<String,Object> payload() {
        return Map.ofEntries(Map.entry("schema_version", 1), Map.entry("preset", preset),
                Map.entry("horizontal_radius_blocks", horizontalRadius),
                Map.entry("vertical_radius_blocks", verticalRadius),
                Map.entry("entity_radius_blocks", entityRadius),
                Map.entry("entity_vertical_radius_blocks", entityVerticalRadius),
                Map.entry("entity_visibility", entityVisibility), Map.entry("update_interval_ticks", interval),
                Map.entry("extra_scan_cells_per_update", extraCells), Map.entry("extra_scan_budget_ms", extraMillis));
    }

    private static int integer(JsonObject value, String key) {
        var item = value.getAsJsonPrimitive(key);
        if (!item.isNumber()) throw new IllegalArgumentException("invalid_awareness_" + key);
        try { return item.getAsBigDecimal().intValueExact(); }
        catch (ArithmeticException error) { throw new IllegalArgumentException("invalid_awareness_" + key); }
    }

    private static String string(JsonObject value, String key) {
        var item = value.getAsJsonPrimitive(key);
        if (!item.isString()) throw new IllegalArgumentException("invalid_awareness_" + key);
        return item.getAsString();
    }

    public static AwarenessConfig parse(JsonObject value) {
        if (!value.keySet().equals(legacy().payload().keySet()) || integer(value, "schema_version") != 1)
            throw new IllegalArgumentException("awareness_schema");
        return new AwarenessConfig(string(value, "preset"), integer(value, "horizontal_radius_blocks"),
                integer(value, "vertical_radius_blocks"), integer(value, "entity_radius_blocks"),
                integer(value, "entity_vertical_radius_blocks"), string(value, "entity_visibility"),
                integer(value, "update_interval_ticks"), integer(value, "extra_scan_cells_per_update"),
                integer(value, "extra_scan_budget_ms"));
    }
}
