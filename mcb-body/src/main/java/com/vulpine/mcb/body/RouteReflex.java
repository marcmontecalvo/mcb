package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Local retreat objective from admitted entity observations; never waits for Python. */
final class RouteReflex {
    // Same set as mclab.profiles.HOSTILE (cohort night run: a zombie villager was missing).
    static final Set<String> HOSTILES = Set.of("minecraft:zombie", "minecraft:husk", "minecraft:drowned",
            "minecraft:zombie_villager", "minecraft:skeleton", "minecraft:stray", "minecraft:bogged",
            "minecraft:creeper", "minecraft:spider", "minecraft:cave_spider", "minecraft:witch",
            "minecraft:slime", "minecraft:magma_cube", "minecraft:phantom", "minecraft:pillager",
            "minecraft:vindicator", "minecraft:evoker", "minecraft:ravager", "minecraft:vex",
            "minecraft:silverfish", "minecraft:endermite", "minecraft:blaze", "minecraft:ghast",
            "minecraft:hoglin", "minecraft:zoglin", "minecraft:piglin_brute", "minecraft:wither_skeleton",
            "minecraft:guardian", "minecraft:elder_guardian", "minecraft:breeze", "minecraft:warden");

    /**
     * When to flee and how far. trigger/creeperTrigger: distance that starts a retreat;
     * steps: blocks retreated per reflex (sprinting when more than one). DEFAULT is the
     * original one-block step; the safe profile flees a creeper's fuse range (7 blocks).
     */
    record Flee(double trigger, double creeperTrigger, int steps) {
        static final Flee DEFAULT = new Flee(2.8, 4, 1);
        Flee {
            if (!(trigger > 0 && trigger <= 16 && creeperTrigger > 0 && creeperTrigger <= 16 && steps >= 1 && steps <= 12))
                throw new IllegalArgumentException("flee_bounds");
        }
    }

    private RouteReflex() { }

    static List<RouteMotor.Point> retreat(RouteMotor.Pose pose, List<Map<String, Object>> entities, RouteMotor.Geometry geometry) {
        return retreat(pose, entities, geometry, Flee.DEFAULT);
    }

    static List<RouteMotor.Point> retreat(RouteMotor.Pose pose, List<Map<String, Object>> entities,
                                          RouteMotor.Geometry geometry, Flee flee) {
        if (!pose.grounded()) return List.of();
        var threats = new ArrayList<double[]>();
        for (var entity : entities) {
            if (!HOSTILES.contains(entity.get("type")) || !(entity.get("position") instanceof List<?> point) || point.size() != 3) continue;
            double x = ((Number) point.get(0)).doubleValue(), y = ((Number) point.get(1)).doubleValue(), z = ((Number) point.get(2)).doubleValue();
            double distance = Math.sqrt(Math.pow(x - pose.x(), 2) + Math.pow(y - pose.y(), 2) + Math.pow(z - pose.z(), 2));
            double trigger = "minecraft:creeper".equals(entity.get("type")) ? flee.creeperTrigger() : flee.trigger();
            if (distance < trigger) threats.add(new double[]{x, z});
        }
        if (threats.isEmpty()) return List.of();
        var source = new RouteMotor.Point(Math.floor(pose.x()), Math.rint(pose.y() * 2) / 2, Math.floor(pose.z()));
        if (!geometry.standable(source)) return List.of();
        // Greedy walk away from the nearest close hostile at every step, never closer to any.
        var route = new ArrayList<RouteMotor.Point>(List.of(source));
        var at = source; double px = pose.x(), pz = pose.z();
        for (int step = 0; step < flee.steps(); step++) {
            RouteMotor.Point best = null; double score = clearance(at.x() + .5, at.z() + .5, threats);
            for (int[] d : new int[][]{{1, 0}, {-1, 0}, {0, 1}, {0, -1}}) {
                var target = new RouteMotor.Point(at.x() + d[0], at.y(), at.z() + d[1]);
                double distance = clearance(target.x() + .5, target.z() + .5, threats);
                double yaw = Math.toDegrees(Math.atan2(-(target.x() + .5 - px), target.z() + .5 - pz));
                var projected = new RouteMotor.Pose(px, at.y(), pz, 0, 0, yaw, true, pose.food());
                if (distance > score && !route.contains(target) && geometry.standable(target)
                        && geometry.safe(projected, target, at.y(), .6, false, 0)) { score = distance; best = target; }
            }
            if (best == null) break;
            route.add(best); at = best; px = best.x() + .5; pz = best.z() + .5;
        }
        return route.size() < 2 ? List.of() : List.copyOf(route);
    }

    /** Distance to the nearest close hostile. */
    private static double clearance(double x, double z, List<double[]> threats) {
        double nearest = Double.POSITIVE_INFINITY;
        for (var t : threats) nearest = Math.min(nearest, Math.hypot(x - t[0], z - t[1]));
        return nearest;
    }
}
