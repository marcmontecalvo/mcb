package com.vulpine.mcb.body;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Checks only cells returned by the configured sensor. Missing facts never mean air. */
final class RouteGeometry implements RouteMotor.Geometry {
    interface Reader { Map<String, Object> cell(int x, int y, int z); }
    private record Key(int x, int y, int z) { }
    private static final Set<String> HAZARDS = Set.of("minecraft:lava", "minecraft:fire", "minecraft:soul_fire",
            "minecraft:powder_snow", "minecraft:cactus", "minecraft:sweet_berry_bush", "minecraft:magma_block",
            "minecraft:campfire", "minecraft:soul_campfire", "minecraft:bubble_column", "minecraft:wither_rose");
    private final Reader reader;
    private final Map<Key, Map<String, Object>> cache = new HashMap<>();
    RouteGeometry(Reader reader) { this.reader = reader; }
    private Map<String, Object> cell(int x, int y, int z) {
        var key = new Key(x, y, z);
        if (!cache.containsKey(key)) cache.put(key, reader.cell(x, y, z));
        return cache.get(key);
    }
    private boolean admitted(Map<String, Object> cell) {
        return cell != null && !HAZARDS.contains(cell.get("block"))
                && Set.of("minecraft:empty", "empty", "").contains(cell.getOrDefault("fluid", "minecraft:empty"));
    }
    private static List<?> boxes(Map<String, Object> cell) {
        return cell.get("collision_boxes") instanceof List<?> list ? list : List.of();
    }
    private static double coordinate(List<?> box, int index) { return ((Number) box.get(index)).doubleValue(); }

    boolean clear(double x, double z, double bottom, double top) {
        double radius = .3 - 1e-6;
        for (int cx = (int)Math.floor(x - radius); cx <= Math.floor(x + radius); cx++)
            for (int cz = (int)Math.floor(z - radius); cz <= Math.floor(z + radius); cz++)
                for (int cy = (int)Math.floor(bottom + 1e-6); cy < Math.ceil(top - 1e-6); cy++) {
                    var cell = cell(cx, cy, cz);
                    if (!admitted(cell)) return false;
                    for (var entry : boxes(cell)) {
                        var box = (List<?>) entry;
                        if (cx + coordinate(box, 0) < x + radius && cx + coordinate(box, 3) > x - radius
                                && cz + coordinate(box, 2) < z + radius && cz + coordinate(box, 5) > z - radius
                                && cy + coordinate(box, 1) < top - 1e-6 && cy + coordinate(box, 4) > bottom + 1e-6) return false;
                    }
                }
        return true;
    }

    private double support(double worldX, double worldZ, double low, double high) {
        int x = (int)Math.floor(worldX), z = (int)Math.floor(worldZ);
        double found = Double.NaN;
        for (int y = (int)Math.floor(low) - 1; y <= Math.floor(high); y++) {
            var cell = cell(x, y, z);
            if (!admitted(cell)) continue;
            for (var entry : boxes(cell)) {
                var box = (List<?>) entry;
                double height = y + coordinate(box, 4);
                if (height < low - 1e-6 || height > high + 1e-6) continue;
                if (worldX < x + coordinate(box, 0) - 1e-6 || worldX > x + coordinate(box, 3) + 1e-6
                        || worldZ < z + coordinate(box, 2) - 1e-6 || worldZ > z + coordinate(box, 5) + 1e-6) continue;
                if (Double.isNaN(found) || height > found) found = height;
            }
        }
        return found;
    }

    @Override public boolean standable(RouteMotor.Point p) {
        double support = support(p.x() + .5, p.z() + .5, p.y(), p.y());
        return Double.isFinite(support) && clear(p.x() + .5, p.z() + .5, p.y(), p.y() + 1.8);
    }

    @Override public boolean safe(RouteMotor.Pose pose, RouteMotor.Point target, double originY,
                                  double distance, boolean jumping, int maxDrop) {
        double yaw = Math.toRadians(pose.yaw());
        // Every hitbox corner must be over support, except that a body already
        // overhanging an edge may move as long as the overhang never grows.
        // Requiring all four there froze R5 of run 20261029T134955375284Z at a
        // cliff edge: no move, not even away from the void, could pass.
        int required = 4;
        for (int sample = 0; sample <= 4; sample++) {
            double x = pose.x() - Math.sin(yaw) * distance * sample / 4;
            double z = pose.z() + Math.cos(yaw) * distance * sample / 4;
            double topSupport = Double.NEGATIVE_INFINITY;
            int supported = 0;
            for (double ox : new double[]{-.299999, .299999}) for (double oz : new double[]{-.299999, .299999}) {
                double h = support(x + ox, z + oz,
                        originY - maxDrop, Math.max(target.y(), originY + .6));
                if (!Double.isFinite(h)) continue;
                supported++;
                topSupport = Math.max(topSupport, h);
            }
            if (sample == 0) required = Math.max(1, supported);
            if (supported == 0 || supported < required) return false;
            double bottom = Math.max(pose.y(), topSupport);
            if (!clear(x, z, bottom, bottom + 1.8 + (jumping ? 1.2 : 0))) return false;
        }
        return true;
    }
}
