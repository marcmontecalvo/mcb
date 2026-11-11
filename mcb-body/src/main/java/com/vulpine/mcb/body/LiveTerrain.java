package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.Map;

/**
 * The planner's view for a {@code "terrain":"live"} plan: the live world inside a box
 * around the body, read through the sensor's per-BlockState facts, so planner and
 * motor see the same blocks there. Outside the box, or where the chunk is not loaded,
 * the uploaded planner map answers; below both a cell is unknown, never air.
 * Read on the server thread only (plans start and advance there).
 */
final class LiveTerrain {
    /** Facts of a loaded cell, or null when its chunk is not loaded. */
    interface Source { Map<String, Object> facts(int x, int y, int z); }

    static final int MAX_RADIUS = 512;

    private LiveTerrain() { }

    static RouteGeometry.Reader reader(Source live, int cx, int cy, int cz, int horizontal, int vertical,
                                       RouteGeometry.Reader fallback) {
        return (x, y, z) -> {
            if (Math.abs((long) x - cx) <= horizontal && Math.abs((long) z - cz) <= horizontal
                    && (vertical == -1 || Math.abs((long) y - cy) <= vertical)) {
                var facts = live.facts(x, y, z);
                if (facts != null) return facts;
            }
            return fallback.cell(x, y, z);
        };
    }

    /** [horizontal, vertical] for a live plan, null for a flagless (uploaded-map) plan. */
    static int[] radius(JsonObject request) {
        if (!request.has("terrain")) return null;
        var mode = request.get("terrain");
        if (!mode.isJsonPrimitive() || !mode.getAsJsonPrimitive().isString() || !mode.getAsString().equals("live"))
            throw new IllegalArgumentException("terrain_mode");
        var value = request.get("terrain_radius");
        if (value == null || !value.isJsonArray() || value.getAsJsonArray().size() != 2)
            throw new IllegalArgumentException("terrain_radius");
        int[] radius = new int[2];
        for (int i = 0; i < 2; i++) {
            var entry = value.getAsJsonArray().get(i);
            if (!entry.isJsonPrimitive() || !entry.getAsJsonPrimitive().isNumber())
                throw new IllegalArgumentException("terrain_radius");
            double raw = entry.getAsDouble();
            if (raw != Math.rint(raw) || raw < (i == 0 ? 0 : -1) || raw > MAX_RADIUS)
                throw new IllegalArgumentException("terrain_radius");
            radius[i] = (int) raw;
        }
        return radius;
    }
}
