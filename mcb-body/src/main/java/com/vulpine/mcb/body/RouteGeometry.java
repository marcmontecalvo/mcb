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

    // Baritone MovementHelper.isWater/isFlowing: only a KNOWN source block counts. Flowing,
    // falling, waterlogged and bubble-column water all fail (level != 0, other block id).
    private static final Set<String> CLIMBABLE = Set.of("minecraft:ladder", "minecraft:vine", "minecraft:twisting_vines",
            "minecraft:twisting_vines_plant", "minecraft:weeping_vines", "minecraft:weeping_vines_plant",
            "minecraft:cave_vines", "minecraft:cave_vines_plant");
    private static boolean stillWater(Map<String, Object> cell) {
        return cell != null && "minecraft:water".equals(cell.get("block")) && "minecraft:water".equals(cell.get("fluid"))
                && cell.get("properties") instanceof Map<?, ?> properties && "0".equals(properties.get("level"))
                && boxes(cell).isEmpty();
    }
    private boolean waterAt(int x, int z, double level) {
        return level == Math.rint(level) && stillWater(cell(x, (int) level - 1, z));
    }

    /** Surface stance: known still water below, clear body cell and headroom above it. */
    @Override public boolean swimSurface(RouteMotor.Point p) {
        return p.y() == Math.rint(p.y()) && waterAt((int) p.x(), (int) p.z(), p.y())
                && clear(p.x() + .5, p.z() + .5, p.y(), p.y() + 1.8);
    }

    /** Baritone MovementDescend.dynamicFallCost water branch: still water, then a known solid floor. */
    @Override public boolean waterLanding(RouteMotor.Point p) {
        if (!swimSurface(p)) return false;
        int x = (int) p.x(), z = (int) p.z();
        for (int y = (int) p.y() - 2; y >= (int) p.y() - 18; y--) {
            var cell = cell(x, y, z);
            if (stillWater(cell)) continue;
            return cell != null && admitted(cell) && !boxes(cell).isEmpty();
        }
        return false;
    }

    /** Body cell is a known ladder/vine and the cell above is known and not a hazard. */
    @Override public boolean climbable(RouteMotor.Point p) {
        var body = cell((int) p.x(), (int) p.y(), (int) p.z());
        var head = cell((int) p.x(), (int) p.y() + 1, (int) p.z());
        return p.y() == Math.rint(p.y()) && body != null && CLIMBABLE.contains(body.get("block")) && admitted(body)
                && head != null && admitted(head) && (boxes(head).isEmpty() || CLIMBABLE.contains(head.get("block")));
    }

    /** Sweep for an edge that touches a water stance: each corner needs known water or support. */
    @Override public boolean swimSafe(RouteMotor.Pose pose, RouteMotor.Point target, double originY,
                                      double distance, boolean jumping) {
        double yaw = Math.toRadians(pose.yaw());
        double low = Math.min(originY, target.y()), high = Math.max(originY, target.y());
        for (int sample = 0; sample <= 4; sample++) {
            double x = pose.x() - Math.sin(yaw) * distance * sample / 4;
            double z = pose.z() + Math.cos(yaw) * distance * sample / 4;
            double bottom = Double.NEGATIVE_INFINITY;
            for (double ox : new double[]{-.299999, .299999}) for (double oz : new double[]{-.299999, .299999}) {
                int cx = (int) Math.floor(x + ox), cz = (int) Math.floor(z + oz);
                double level;
                if (waterAt(cx, cz, low)) level = low;
                else if (waterAt(cx, cz, high)) level = high;
                else {
                    level = support(x + ox, z + oz, low, high);
                    // Water-landing drop (Baritone MovementFall walks off and falls): a corner may
                    // overhang a neighbouring column that is known open air down to a safe floor.
                    if (!Double.isFinite(level)) {
                        if (high - low < 2 || !openFall(cx, cz, low, high)) return false;
                        level = low;
                    }
                }
                bottom = Math.max(bottom, level);
            }
            if (!clear(x, z, bottom, Math.max(bottom + 1.8 + (jumping ? 1.2 : 0), high + 1.8))) return false;
        }
        return true;
    }

    /** Known air/still water from the stance up to the source, then known solid or still water, no hazard. */
    private boolean openFall(int x, int z, double low, double high) {
        for (int y = (int) Math.floor(low); y <= (int) Math.floor(high); y++) {
            var cell = cell(x, y, z);
            if (cell == null || HAZARDS.contains(cell.get("block")) || !boxes(cell).isEmpty()
                    || !(admitted(cell) || stillWater(cell))) return false;
        }
        for (int y = (int) Math.floor(low) - 1; y >= (int) Math.floor(low) - 1 - MAX_FALL_SCAN; y--) {
            var cell = cell(x, y, z);
            if (cell == null || HAZARDS.contains(cell.get("block"))) return false;
            if (stillWater(cell) || (admitted(cell) && !boxes(cell).isEmpty())) return true;
            if (!admitted(cell)) return false;
        }
        return false;
    }
    private static final int MAX_FALL_SCAN = 24;

    @Override public boolean standable(RouteMotor.Point p) {
        double support = support(p.x() + .5, p.z() + .5, p.y(), p.y());
        return Double.isFinite(support) && clear(p.x() + .5, p.z() + .5, p.y(), p.y() + 1.8);
    }

    @Override public boolean safe(RouteMotor.Pose pose, RouteMotor.Point target, double originY,
                                  double distance, boolean jumping, int maxDrop) {
        return sweep(pose, target, originY, distance, jumping, maxDrop, false);
    }

    /**
     * Level diagonal sweep (Baritone MovementDiagonal): the standable source and target
     * columns carry the body, so two supported corners suffice at every sample and the
     * side columns may be holes of any depth. Unsupported corners must not hang over a hazard.
     */
    @Override public boolean safeDiagonal(RouteMotor.Pose pose, RouteMotor.Point target, double originY,
                                          double distance, boolean jumping, int maxDrop) {
        return sweep(pose, target, originY, distance, jumping, maxDrop, true);
    }

    /** True when both side columns of a level diagonal have known support within maxDrop (walk-only otherwise). */
    @Override public boolean cornerSprintOk(RouteMotor.Point source, RouteMotor.Point target, int maxDrop) {
        return supportedWithin((int) target.x(), (int) source.y(), (int) source.z(), maxDrop)
                && supportedWithin((int) source.x(), (int) source.y(), (int) target.z(), maxDrop);
    }

    private boolean supportedWithin(int x, int y, int z, int maxDrop) {
        for (int k = 0; k <= maxDrop; k++) {
            var cell = cell(x, y - 1 - k, z);
            if (cell != null && admitted(cell) && !boxes(cell).isEmpty()) return true;
        }
        return false;
    }

    private boolean hazardBelow(double worldX, double worldZ, double originY, int maxDrop) {
        int x = (int) Math.floor(worldX), z = (int) Math.floor(worldZ);
        for (int y = (int) Math.floor(originY) - 1; y >= (int) Math.floor(originY) - 1 - maxDrop; y--) {
            var cell = cell(x, y, z);
            if (cell == null) continue;
            if (HAZARDS.contains(cell.get("block")) || String.valueOf(cell.getOrDefault("fluid", "")).contains("lava")) return true;
            if (!boxes(cell).isEmpty()) return false;
        }
        return false;
    }

    private boolean sweep(RouteMotor.Pose pose, RouteMotor.Point target, double originY,
                          double distance, boolean jumping, int maxDrop, boolean cut) {
        double yaw = Math.toRadians(pose.yaw());
        // Every hitbox corner must be over support, except that a body already
        // overhanging an edge may move as long as the overhang never grows.
        // Requiring all four there froze R5 of run 20261029T134955375284Z at a
        // cliff edge: no move, not even away from the void, could pass.
        // Diagonal cuts (cut) ask for two supported corners instead of four.
        int required = 4;
        for (int sample = 0; sample <= 4; sample++) {
            double x = pose.x() - Math.sin(yaw) * distance * sample / 4;
            double z = pose.z() + Math.cos(yaw) * distance * sample / 4;
            double topSupport = Double.NEGATIVE_INFINITY;
            int supported = 0;
            for (double ox : new double[]{-.299999, .299999}) for (double oz : new double[]{-.299999, .299999}) {
                double h = support(x + ox, z + oz,
                        originY - maxDrop, Math.max(target.y(), originY + .6));
                if (!Double.isFinite(h)) {
                    if (cut && hazardBelow(x + ox, z + oz, originY, maxDrop)) return false;
                    continue;
                }
                supported++;
                topSupport = Math.max(topSupport, h);
            }
            if (sample == 0) required = cut ? Math.min(2, Math.max(1, supported)) : Math.max(1, supported);
            if (supported == 0 || supported < required) return false;
            double bottom = Math.max(pose.y(), topSupport);
            if (!clear(x, z, bottom, bottom + 1.8 + (jumping ? 1.2 : 0))) return false;
        }
        return true;
    }
}
