package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Per-tick route execution. No planner, world edits, networking or lab dependency. */
final class RouteMotor {
    // Reference: Baritone v1.20.0 ActionCosts WALK_ONE_BLOCK_COST / SPRINT_ONE_BLOCK_COST.
    // Values are vanilla movement rates, independently used here for motion prediction.
    static final double WALK_BLOCKS_PER_TICK = 4.317 / 20;
    static final double SPRINT_BLOCKS_PER_TICK = 5.612 / 20;
    record Point(double x, double y, double z) {
        double distance(Pose p) { return Math.hypot(x + .5 - p.x, z + .5 - p.z); }
    }
    record Pose(double x, double y, double z, double vx, double vz, double yaw,
                boolean grounded, int food) { }
    record Policy(boolean diagonals, boolean sprint, boolean shapes, int maxDrop) { }
    record Input(double forward, double yaw, boolean jump, boolean sprint) {
        static final Input STOP = new Input(0, 0, false, false);
    }
    interface Geometry {
        boolean standable(Point point);
        boolean safe(Pose pose, Point target, double originY, double distance, boolean jumping, int maxDrop);
    }

    private final String id;
    private final List<Point> route;
    private final Policy policy;
    private int index = 1, ticks, stepTicks, stalled, recentredAt = -1;
    private boolean recentring;
    private double lastX = Double.NaN, lastZ, lastY;
    private String state = "following", reason = "";

    RouteMotor(String id, List<Point> route, Policy policy) {
        if (id == null || !id.matches("[A-Za-z0-9_.:-]{1,80}")) throw new IllegalArgumentException("route_id");
        if (route == null || route.size() < 2 || route.size() > 32) throw new IllegalArgumentException("route_length");
        if (policy.maxDrop < 0 || policy.maxDrop > 3) throw new IllegalArgumentException("route_drop_policy");
        for (int i = 0; i < route.size(); i++) {
            var p = route.get(i);
            if (!Double.isFinite(p.x) || !Double.isFinite(p.y) || !Double.isFinite(p.z)
                    || Math.abs(p.x) > 30_000_000 || Math.abs(p.z) > 30_000_000
                    || p.x != Math.rint(p.x) || p.z != Math.rint(p.z)
                    || p.y < -2048 || p.y > 2048 || p.y * 2 != Math.rint(p.y * 2)
                    || (!policy.shapes && p.y != Math.rint(p.y))) throw new IllegalArgumentException("route_point");
            if (i == 0) continue;
            var previous = route.get(i - 1);
            double dx = Math.abs(p.x - previous.x), dz = Math.abs(p.z - previous.z), dy = p.y - previous.y;
            if (dx > 1 || dz > 1 || dx + dz == 0 || (dx + dz > 1 && !policy.diagonals)
                    || dy > 1 || -dy > policy.maxDrop) throw new IllegalArgumentException("route_edge_permission");
        }
        this.id = id; this.route = List.copyOf(route); this.policy = policy;
    }

    static RouteMotor parse(JsonObject request) {
        var fields = request.getAsJsonObject("movement_policy");
        if (!fields.keySet().equals(Set.of("allow_diagonals", "allow_sprint", "allow_shapes", "max_drop", "max_gap", "allow_swim")))
            throw new IllegalArgumentException("route_movement_policy_fields");
        int maxDrop = integer(fields, "max_drop");
        // Gaps, swimming and edits have separate executors; ordinary routes cannot imply them.
        int maxGap=integer(fields, "max_gap");
        if(maxGap<0 || maxGap>2 || bool(fields, "allow_swim"))
            throw new IllegalArgumentException("native_route_unsupported_movement_policy");
        var policy = new Policy(bool(fields, "allow_diagonals"), bool(fields, "allow_sprint"), bool(fields, "allow_shapes"), maxDrop);
        var points = new ArrayList<Point>();
        var values = request.getAsJsonArray("route");
        if (values.size() > 32) throw new IllegalArgumentException("route_length");
        for (var value : values) {
            var xyz = value.getAsJsonArray();
            if (xyz.size() != 3) throw new IllegalArgumentException("route_point");
            for (var coordinate : xyz) if (!coordinate.isJsonPrimitive() || !coordinate.getAsJsonPrimitive().isNumber())
                throw new IllegalArgumentException("route_coordinate_type");
            points.add(new Point(xyz.get(0).getAsDouble(), xyz.get(1).getAsDouble(), xyz.get(2).getAsDouble()));
        }
        if(!request.get("route_id").isJsonPrimitive() || !request.getAsJsonPrimitive("route_id").isString())
            throw new IllegalArgumentException("route_id_type");
        return new RouteMotor(request.get("route_id").getAsString(), points, policy);
    }

    private static boolean bool(JsonObject value, String key) {
        var entry = value.get(key);
        if (entry == null || !entry.isJsonPrimitive() || !entry.getAsJsonPrimitive().isBoolean())
            throw new IllegalArgumentException("route_boolean_" + key);
        return entry.getAsBoolean();
    }
    private static int integer(JsonObject value, String key) {
        var entry = value.get(key);
        if (entry == null || !entry.isJsonPrimitive() || !entry.getAsJsonPrimitive().isNumber()
                || !Double.isFinite(entry.getAsDouble()) || entry.getAsDouble() != entry.getAsInt())
            throw new IllegalArgumentException("route_integer_" + key);
        return entry.getAsInt();
    }

    String id() { return id; }
    boolean active() { return state.equals("following"); }
    void stop(String why) { state = "needs_replan"; reason = why; }
    Map<String, Object> status() {
        return Map.of("route_id", id, "state", state, "reason", reason, "next_index", index,
                "length", route.size(), "motor_ticks", ticks);
    }

    Input tick(Pose pose, Geometry geometry) {
        if (!active()) return Input.STOP;
        ticks++; stepTicks++;
        if (ticks == 1 && (route.get(0).distance(pose) > 1.5 || Math.abs(pose.y - route.get(0).y) > 1.5)) {
            stop("route_start_displaced"); return Input.STOP;
        }
        // Momentum can carry the body past a drop's landing column onto the next
        // route point. Accept the point it stands on (Baritone PathExecutor
        // re-syncs to a later path position) instead of walking back.
        // Run 20261029T125842184277Z R4: 12 of 47 step-downs backed up.
        for (int n = Math.min(index + 2, route.size() - 1); n > index; n--) {
            var later = route.get(n);
            if (pose.grounded && Math.abs(pose.y - later.y) <= .2
                    && Math.floor(pose.x) == later.x && Math.floor(pose.z) == later.z) {
                index = n; stepTicks = stalled = 0; break;
            }
        }
        var target = route.get(index);
        // Baritone MovementTraverse/Ascend/Descend advance on destination arrival.
        // Independent rule: entered destination column at its expected supported height.
        while (pose.grounded && Math.abs(pose.y - target.y) <= .2
                && Math.floor(pose.x) == target.x && Math.floor(pose.z) == target.z
                && (index < route.size() - 1 || target.distance(pose) <= .2)) {
            index++; stepTicks = stalled = 0;
            if (index == route.size()) { state = "done"; return Input.STOP; }
            target = route.get(index);
        }
        if (stepTicks > 200) { stop("route_step_deadline"); return Input.STOP; }
        double moved = Math.sqrt(Math.pow(pose.x - lastX, 2) + Math.pow(pose.z - lastZ, 2) + Math.pow(pose.y - lastY, 2));
        lastX = pose.x; lastZ = pose.z; lastY = pose.y;
        double distance = target.distance(pose);
        var source = route.get(index - 1);
        if (pose.grounded && pose.y < source.y - policy.maxDrop - .25) { stop("route_unexpected_fall"); return Input.STOP; }
        if (!geometry.standable(target)) { stop("route_target_unknown_or_changed"); return Input.STOP; }
        if (recentring) return recentre(pose, geometry, source);
        // Only brake onto a point the route turns, climbs or ends at. Carrying speed
        // straight through a step-down lands on the next (standable) point.
        var after = index + 1 < route.size() ? route.get(index + 1) : null;
        boolean throughPoint = after != null && after.y <= target.y
                && after.x - target.x == target.x - source.x && after.z - target.z == target.z - source.z
                && geometry.standable(after);
        // Preserve momentum on a straight, level segment, but never cut a corner.
        if (target.y == source.y) {
            double dx = target.x - source.x, dz = target.z - source.z;
            for (int n = index + 1; n < Math.min(index + 3, route.size()); n++) {
                var farther = route.get(n); var prior = route.get(n - 1);
                if (farther.y != source.y || farther.x - prior.x != dx || farther.z - prior.z != dz) break;
                double heading = yawTo(pose, farther);
                double travel = Math.min(farther.distance(pose), Math.hypot(pose.vx, pose.vz) * 2 + SPRINT_BLOCKS_PER_TICK * 2);
                if (Math.abs(wrap(heading - pose.yaw)) > 8 || !geometry.standable(farther)
                        || !geometry.safe(pose, farther, source.y, travel, false, policy.maxDrop)) break;
                target = farther;
            }
        }
        double turn = wrap(yawTo(pose, target) - pose.yaw);
        double yaw = Math.max(-45, Math.min(45, turn));
        if (Math.abs(turn) > 8) {
            // Steer and move in the same tick, as Baritone does, when one tick's
            // rotation covers the turn and the swept body is safe on the new
            // heading. Turning in place at every corner cost ~1/3 of corner speed
            // in run 20261029T190159944470Z. Larger turns still stop first.
            var turned = new Pose(pose.x, pose.y, pose.z, pose.vx, pose.vz, pose.yaw + yaw, pose.grounded, pose.food);
            double reach = Math.min(target.distance(pose) + .03, Math.hypot(pose.vx, pose.vz) * 2 + WALK_BLOCKS_PER_TICK * 2);
            if (Math.abs(turn) > 45 || !geometry.safe(turned, target, source.y, reach, false, policy.maxDrop))
                return new Input(0, yaw, false, false);
            pose = turned;
        }
        // Baritone MovementAscend waits until close to the rise before jumping.
        boolean jumping = target.y > pose.y + .6 && pose.grounded && distance <= 1.2;
        double forward = throughPoint ? 1 : Math.min(1, Math.max(.12, target.distance(pose) / .3));
        double travel = Math.min(target.distance(pose) + .03,
                Math.hypot(pose.vx, pose.vz) * 2 + WALK_BLOCKS_PER_TICK * 2 * forward);
        if (!geometry.safe(pose, target, source.y, travel, jumping, policy.maxDrop)) {
            // The planner's step is clear from the cell centre; an off-centre body can
            // clip a wall or overhang an edge on the direct line (run
            // 20261029T134955375284Z R4/R5 froze this way). Centre once, then retry.
            if (recentredAt != index && pose.grounded && Math.abs(pose.y - source.y) <= .2
                    && source.distance(pose) > .2) {
                recentredAt = index; recentring = true;
                return recentre(pose, geometry, source);
            }
            stop("route_swept_geometry_unknown_or_unsafe"); return Input.STOP;
        }
        // Keep sprinting into a point the route runs straight through (including a
        // step-down) and while airborne; Carpet cancels sprint at once on false.
        boolean sprint = policy.sprint && pose.food > 6 && !jumping && forward == 1
                && target.y <= pose.y + .1 && (throughPoint || target.distance(pose) > 1.5)
                && geometry.safe(pose, target, source.y,
                        Math.hypot(pose.vx, pose.vz) * 2 + SPRINT_BLOCKS_PER_TICK * 2, false, policy.maxDrop);
        if (moved < .01 && pose.grounded && !jumping) stalled++; else stalled = 0;
        if (stalled > 30) { stop("route_no_progress"); return Input.STOP; }
        return new Input(forward, yaw, jumping, sprint);
    }

    private Input recentre(Pose pose, Geometry geometry, Point source) {
        double distance = source.distance(pose);
        if (distance <= .2) { recentring = false; return Input.STOP; }
        double turn = wrap(yawTo(pose, source) - pose.yaw);
        double yaw = Math.max(-45, Math.min(45, turn));
        if (Math.abs(turn) > 8) return new Input(0, yaw, false, false);
        if (!geometry.safe(pose, source, source.y, distance + .03, false, policy.maxDrop)) {
            recentring = false; stop("route_swept_geometry_unknown_or_unsafe"); return Input.STOP;
        }
        return new Input(Math.min(1, Math.max(.12, distance / .3)), yaw, false, false);
    }

    private static double yawTo(Pose pose, Point target) {
        return Math.toDegrees(Math.atan2(-(target.x + .5 - pose.x), target.z + .5 - pose.z));
    }
    private static double wrap(double yaw) { return ((yaw + 180) % 360 + 360) % 360 - 180; }
}
