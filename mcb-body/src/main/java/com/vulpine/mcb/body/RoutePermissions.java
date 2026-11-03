package com.vulpine.mcb.body;

import com.google.gson.JsonObject;

/** Immutable permissions captured separately by a plan and an executing objective. */
record RoutePermissions(boolean doors, boolean vines) {
    static RoutePermissions defaults() { return new RoutePermissions(false, true); }
    static RoutePermissions route(JsonObject request) { return from(request, "use_doors", "climb_vines"); }
    static RoutePermissions edits(JsonObject request) { return from(request, "doors", "vines"); }
    private static RoutePermissions from(JsonObject request, String doors, String vines) {
        if (request == null) return defaults();
        return new RoutePermissions(flag(request, doors, false), flag(request, vines, true));
    }
    private static boolean flag(JsonObject request, String name, boolean fallback) {
        if (!request.has(name)) return fallback;
        var entry = request.get(name);
        if (!entry.isJsonPrimitive() || !entry.getAsJsonPrimitive().isBoolean())
            throw new IllegalArgumentException("route_permission_" + name);
        return entry.getAsBoolean();
    }
    RouteGeometry.Reader reader(RouteGeometry.Reader selected) {
        return (x, y, z) -> BuildingMoves.view(selected.cell(x, y, z), doors, vines);
    }
}
