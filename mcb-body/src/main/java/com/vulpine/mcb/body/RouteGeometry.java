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

    private static boolean waterFluid(Object fluid) {
        return "minecraft:water".equals(fluid) || "minecraft:flowing_water".equals(fluid);
    }

    boolean clear(double x, double z, double bottom, double top) {
        double radius = .3 - 1e-6;
        int feet = (int)Math.floor(bottom + 1e-6);
        for (int cx = (int)Math.floor(x - radius); cx <= Math.floor(x + radius); cx++)
            for (int cz = (int)Math.floor(z - radius); cz <= Math.floor(z + radius); cz++)
                for (int cy = feet; cy < Math.ceil(top - 1e-6); cy++) {
                    var cell = cell(cx, cy, cz);
                    // Wading (RoutePlanner.clearColumn): water in the feet cell only; the head stays dry.
                    if (cy == feet && cell != null && waterFluid(cell.get("fluid"))
                            && List.of().equals(cell.get("collision_boxes")) && !HAZARDS.contains(cell.get("block"))) continue;
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

    // Match RoutePlanner.water: collision-free source fluid includes seagrass.
    // Flowing/falling water, solid waterlogged blocks and hazards remain excluded.
    // Scaffolding too: RoutePlanner plans climbs through it, so the motor must be able to follow them.
    private static final Set<String> CLIMBABLE = Set.of("minecraft:ladder", "minecraft:vine", "minecraft:scaffolding", "minecraft:twisting_vines",
            "minecraft:twisting_vines_plant", "minecraft:weeping_vines", "minecraft:weeping_vines_plant",
            "minecraft:cave_vines", "minecraft:cave_vines_plant");
    private static boolean stillWater(Map<String, Object> cell) {
        if (cell == null || !"minecraft:water".equals(cell.get("fluid"))
                || HAZARDS.contains(cell.get("block")) || !List.of().equals(cell.get("collision_boxes"))) return false;
        Object level = cell.get("properties") instanceof Map<?, ?> properties ? properties.get("level") : null;
        return level == null || "0".equals(level);
    }
    private boolean waterAt(int x, int z, double level) {
        return level == Math.rint(level) && stillWater(cell(x, (int) level - 1, z));
    }

    private static boolean collisionFree(Map<String, Object> cell) {
        return cell != null && !HAZARDS.contains(cell.get("block")) && List.of().equals(cell.get("collision_boxes"));
    }
    /** Known, collision-free, non-hazard air: the head can breathe here. */
    private boolean breathable(int x, int y, int z) {
        var cell = cell(x, y, z);
        return collisionFree(cell) && Set.of("minecraft:empty", "empty", "").contains(cell.getOrDefault("fluid", "minecraft:empty"));
    }
    /** Known, collision-free, non-hazard water or air. Unknown cells are never passable. */
    private boolean swimmable(int x, int y, int z) {
        var cell = cell(x, y, z);
        return breathable(x, y, z) || (collisionFree(cell) && waterFluid(cell.get("fluid")));
    }
    /** The cell above (x,y,z) is known water or air, so a body can rise into it. */
    boolean roomAbove(int x, int y, int z) { return swimmable(x, y + 1, z); }
    @Override public boolean openAbove(RouteMotor.Pose p, RouteMotor.Point target) {
        int head = (int) Math.floor(p.y() + 1.62);
        for (int y = head + 1; y <= (int) target.y(); y++) if (!fits(p.x(), p.z(), y)) return false;
        return true;
    }
    /** Every column the 0.6-wide body at (px,pz) overlaps is swimmable at cell height y. */
    private boolean fits(double px, double pz, int y) {
        double r = .3 - 1e-6;
        for (int cx = (int) Math.floor(px - r); cx <= Math.floor(px + r); cx++)
            for (int cz = (int) Math.floor(pz - r); cz <= Math.floor(pz + r); cz++)
                if (!swimmable(cx, y, cz)) return false;
        return true;
    }
    static final int SURFACE_RADIUS = 8, SURFACE_NODES = 400;
    private static final int[][] SIX = {{0,1,0},{-1,0,0},{1,0,0},{0,0,-1},{0,0,1},{0,-1,0}};
    /** First cell on the shortest swimmable path from the head cell to a breathable cell,
     *  or null when none lies within SURFACE_RADIUS (Manhattan) and SURFACE_NODES.
     *  Horizontal steps also need the cell below free, so the body fits. */
    @Override public int[] surfaceStep(double px, int hy, double pz) {
        int hx = (int) Math.floor(px), hz = (int) Math.floor(pz);
        // Rising needs the whole hitbox clear, not just the centre column (run 20261116T172715478561Z).
        for (int y = hy + 1; y <= hy + SURFACE_RADIUS && fits(px, pz, y); y++)
            if (breathable(hx, y, hz)) return new int[]{hx, hy + 1, hz};  // open column: straight up
        var parent = new HashMap<Key, Key>();
        var start = new Key(hx, hy, hz);
        var queue = new java.util.ArrayDeque<Key>();
        parent.put(start, start); queue.add(start);
        while (!queue.isEmpty() && parent.size() <= SURFACE_NODES) {
            var at = queue.poll();
            for (var d : SIX) {
                var next = new Key(at.x() + d[0], at.y() + d[1], at.z() + d[2]);
                if (parent.containsKey(next) || Math.abs(next.x() - hx) + Math.abs(next.y() - hy) + Math.abs(next.z() - hz) > SURFACE_RADIUS
                        || !swimmable(next.x(), next.y(), next.z())
                        || (d[1] > 0 && at == start && !fits(px, pz, next.y()))
                        || (d[1] == 0 && !swimmable(next.x(), next.y() - 1, next.z()))) continue;
                parent.put(next, at);
                if (breathable(next.x(), next.y(), next.z())) {
                    while (!parent.get(next).equals(start)) next = parent.get(next);
                    return new int[]{next.x(), next.y(), next.z()};
                }
                queue.add(next);
            }
        }
        return null;
    }

    private boolean wet(int x, int y, int z) {
        var cell = cell(x, y, z);
        return cell != null && waterFluid(cell.get("fluid"));
    }
    /** Too deep to stand in with the head out: known water in the feet cell and in the cell above or below it. */
    @Override public boolean deepWater(RouteMotor.Pose p) {
        int x = (int) Math.floor(p.x()), y = (int) Math.floor(p.y()), z = (int) Math.floor(p.z());
        return wet(x, y, z) && (wet(x, y - 1, z) || wet(x, y + 1, z));
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
            boolean floating = low == high;  // every corner over water at the stance level
            for (double ox : new double[]{-.299999, .299999}) for (double oz : new double[]{-.299999, .299999}) {
                int cx = (int) Math.floor(x + ox), cz = (int) Math.floor(z + oz);
                // Support first: a ledge overhanging the landing lake (water under it at the
                // landing height) carries the corner at the ledge, not at the water. Cohort 16
                // R5: every 19-block lake drop off a 2-block ledge was refused here.
                double level = support(x + ox, z + oz, low, high);
                if (Double.isFinite(level)) floating = false;  // stands on it
                else if (waterAt(cx, cz, low)) level = low;
                else if (waterAt(cx, cz, high)) level = high;
                else {
                    // Water-landing drop (Baritone MovementFall walks off and falls): a corner may
                    // overhang a neighbouring column that is known open air down to a safe floor.
                    if (!Double.isFinite(level)) {
                        if (high - low < 2 || !openFall(cx, cz, low, high)) return false;
                        floating = false;
                        level = low;
                    }
                }
                bottom = Math.max(bottom, level);
            }
            // A floating body rides lower under a ceiling and still breathes: a level swim needs
            // only the stance cell (companion/20261112T132940952617Z stood 92 s under leaves).
            if (!clear(x, z, bottom, floating ? bottom + 1 : Math.max(bottom + 1.8 + (jumping ? 1.2 : 0), high + 1.8))) return false;
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
