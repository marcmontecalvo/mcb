package com.vulpine.mcb.body;

import java.util.Map;
import java.util.function.Supplier;

/** Which terrain batch /policy last handed out. A served batch counts as delivered. */
final class ServedTerrain {
    private Object served;

    /** Reads the snapshot and marks its terrain batch served in one step. */
    synchronized Map<String,Object> serve(Supplier<Map<String,Object>> snapshot) {
        var result=snapshot.get();
        served=result.get("terrain_sections");
        return result;
    }

    /** True when the current batch was never served, so its sections must ride along. */
    synchronized boolean unread(Object current) { return served!=current; }
}
