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
    /** inWater is isInWater(); the 8-argument form (land only) keeps older callers valid. */
    record Pose(double x, double y, double z, double vx, double vz, double yaw,
                boolean grounded, int food, boolean inWater) {
        Pose(double x, double y, double z, double vx, double vz, double yaw, boolean grounded, int food) {
            this(x, y, z, vx, vz, yaw, grounded, food, false);
        }
    }
    /** swim: water-surface stances and water-landing drops; climb: ladder/vine edges. */
    record Policy(boolean diagonals, boolean sprint, boolean shapes, int maxDrop, boolean swim, boolean climb) {
        Policy(boolean diagonals, boolean sprint, boolean shapes, int maxDrop) {
            this(diagonals, sprint, shapes, maxDrop, false, false);
        }
    }
    record Input(double forward, double yaw, boolean jump, boolean sprint) {
        static final Input STOP = new Input(0, 0, false, false);
    }
    interface Geometry {
        boolean standable(Point point);
        boolean safe(Pose pose, Point target, double originY, double distance, boolean jumping, int maxDrop);
        // Optional capabilities; the default is "unknown, so no".
        /** Level diagonal sweep (two supported corners suffice); default is the strict rule. */
        default boolean safeDiagonal(Pose pose, Point target, double originY, double distance, boolean jumping, int maxDrop) {
            return safe(pose, target, originY, distance, jumping, maxDrop);
        }
        /** Both side columns of a diagonal have known support within maxDrop. */
        default boolean cornerSprintOk(Point source, Point target, int maxDrop) { return false; }
        default boolean swimSurface(Point point) { return false; }
        default boolean waterLanding(Point point) { return false; }
        default boolean climbable(Point point) { return false; }
        default boolean swimSafe(Pose pose, Point target, double originY, double distance, boolean jumping) { return false; }
    }
    /** Still water breaks any fall; the cap only bounds a malformed route. */
    static final int MAX_WATER_DROP = 24;

    private final String id;
    private final List<Point> route;
    private final Policy policy;
    private int index = 1, ticks, stepTicks, stalled, recentredAt = -1;
    private boolean recentring;
    private Point bend;      // edging waypoint (centre = x+.5), null when not bending
    private int bendIndex = -1;
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
            boolean vertical = dx + dz == 0 && policy.climb && Math.abs(dy) == 1;
            if (dx > 1 || dz > 1 || (dx + dz == 0 && !vertical) || (dx + dz > 1 && !policy.diagonals)
                    || dy > 1 || (!vertical && -dy > (policy.swim ? MAX_WATER_DROP : policy.maxDrop))) throw new IllegalArgumentException("route_edge_permission");
        }
        this.id = id; this.route = List.copyOf(route); this.policy = policy;
    }

    static RouteMotor parse(JsonObject request) {
        var fields = request.getAsJsonObject("movement_policy");
        var base = Set.of("allow_diagonals", "allow_sprint", "allow_shapes", "max_drop", "max_gap", "allow_swim");
        boolean hasClimb = fields.keySet().equals(Set.of("allow_diagonals", "allow_sprint", "allow_shapes", "max_drop",
                "max_gap", "allow_swim", "allow_climb"));
        if (!hasClimb && !fields.keySet().equals(base))
            throw new IllegalArgumentException("route_movement_policy_fields");
        int maxDrop = integer(fields, "max_drop");
        // Gaps and edits have separate executors; ordinary routes cannot imply them.
        int maxGap=integer(fields, "max_gap");
        if(maxGap<0 || maxGap>2)
            throw new IllegalArgumentException("native_route_unsupported_movement_policy");
        var policy = new Policy(bool(fields, "allow_diagonals"), bool(fields, "allow_sprint"), bool(fields, "allow_shapes"), maxDrop,
                bool(fields, "allow_swim"), hasClimb && bool(fields, "allow_climb"));
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
            // Baritone PathExecutor re-syncs to a later path position; a stale start
            // (the body walked or dropped during planning) is not a failure.
            int later = resyncIndex(pose);
            if (later < 0) { stop("route_start_displaced"); return Input.STOP; }
            index = later; stepTicks = stalled = 0;
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
        while (arrived(pose, geometry, route.get(index - 1), target)) {
            index++; stepTicks = stalled = 0;
            if (index == route.size()) { state = "done"; return Input.STOP; }
            target = route.get(index);
        }
        if (stepTicks > 200) { stop("route_step_deadline"); return Input.STOP; }
        double moved = Math.sqrt(Math.pow(pose.x - lastX, 2) + Math.pow(pose.z - lastZ, 2) + Math.pow(pose.y - lastY, 2));
        lastX = pose.x; lastZ = pose.z; lastY = pose.y;
        double distance = target.distance(pose);
        var source = route.get(index - 1);
        if (pose.grounded && !pose.inWater && pose.y < source.y - policy.maxDrop - .25) { stop("route_unexpected_fall"); return Input.STOP; }
        if (!(geometry.standable(target) || (policy.swim && geometry.swimSurface(target))
                || (policy.climb && geometry.climbable(target)))) { stop("route_target_unknown_or_changed"); return Input.STOP; }
        boolean climbEdge = policy.climb && (geometry.climbable(target) || geometry.climbable(source));
        boolean swimEdge = !climbEdge && policy.swim && (geometry.swimSurface(target) || geometry.swimSurface(source));
        // Drops beyond maxDrop are legal only into known still water with a known floor.
        if (!climbEdge && source.y - target.y > policy.maxDrop && !(swimEdge && geometry.waterLanding(target))) {
            stop("route_drop_requires_water"); return Input.STOP;
        }
        if (recentring) return recentre(pose, geometry, source);
        if (climbEdge) return climb(pose, source, target, moved);
        if (swimEdge) return swim(pose, geometry, source, target, moved);
        // Only brake onto a point the route turns, climbs or ends at. Carrying speed
        // straight through a step-down lands on the next (standable) point.
        var after = index + 1 < route.size() ? route.get(index + 1) : null;
        boolean throughPoint = after != null && (after.y <= target.y || sprintAscentAt(index + 1))
                && after.x - target.x == target.x - source.x && after.z - target.z == target.z - source.z
                && geometry.standable(after);
        // Level diagonal (Baritone MovementDiagonal): the standable endpoints carry the body.
        boolean cut = target.y == source.y && Math.abs(target.x - source.x) == 1 && Math.abs(target.z - source.z) == 1;
        if (bend != null && bendIndex != index) bend = null;
        if (bend != null) {
            // Leave the bend as soon as the straight sweep to the target clears the blocked column.
            double reachNow = Math.min(target.distance(pose) + .03, Math.hypot(pose.vx, pose.vz) * 2 + WALK_BLOCKS_PER_TICK * 2);
            var facing = new Pose(pose.x, pose.y, pose.z, pose.vx, pose.vz, yawTo(pose, target), pose.grounded, pose.food, pose.inWater);
            if (!fits(geometry, true, facing, target, source.y, reachNow, false)) {
                if (bend.distance(pose) < .02) { bend = null; stop("route_swept_geometry_unknown_or_unsafe"); return Input.STOP; }
                return followBend(pose, geometry, source);
            }
            bend = null;
        }
        // Preserve momentum on a straight, level segment, but never cut a corner.
        int targetIndex = index;
        if (target.y == source.y) {
            double dx = target.x - source.x, dz = target.z - source.z;
            for (int n = index + 1; n < Math.min(index + 3, route.size()); n++) {
                var farther = route.get(n); var prior = route.get(n - 1);
                if (farther.y != source.y || farther.x - prior.x != dx || farther.z - prior.z != dz) break;
                double heading = yawTo(pose, farther);
                double travel = Math.min(farther.distance(pose), Math.hypot(pose.vx, pose.vz) * 2 + SPRINT_BLOCKS_PER_TICK * 2);
                if (Math.abs(wrap(heading - pose.yaw)) > 8 || !geometry.standable(farther)
                        || !fits(geometry, cut, pose, farther, source.y, travel, false)) break;
                target = farther; targetIndex = n;
            }
        }
        // Instant heading (Baritone LookBehavior sets the exact rotation every tick; native
        // inputs are not bound by the legacy 45 degree rule). Never stop to turn: steer and
        // move in the same tick whenever the swept body on the new heading is safe.
        double turn = target.distance(pose) < .08 ? 0 : wrap(yawTo(pose, target) - pose.yaw);
        double yaw = turn;
        if (Math.abs(turn) > 8) {
            var turned = new Pose(pose.x, pose.y, pose.z, pose.vx, pose.vz, pose.yaw + yaw, pose.grounded, pose.food, pose.inWater);
            double reach = Math.min(target.distance(pose) + .03, Math.hypot(pose.vx, pose.vz) * 2 + WALK_BLOCKS_PER_TICK * 2);
            if (!fits(geometry, cut, turned, target, source.y, reach, false))
                return new Input(0, yaw, false, false);
            pose = turned;
        }
        // Baritone MovementAscend waits until close to the rise before jumping.
        // Sprint ascents (Baritone PathExecutor.sprintableAscend) jump a stride early so
        // the speed carries onto the step.
        boolean sprintAscent = sprintAscentAt(index);
        double jumpRange = sprintAscent && Math.hypot(pose.vx, pose.vz) > .12 ? 1.7 : 1.2;
        boolean jumping = target.y > pose.y + .6 && pose.grounded && distance <= jumpRange;
        // Brake only for the route's end or a landing the route turns at; elsewhere arrival
        // (column entry) retargets, as Baritone's PathExecutor does.
        boolean brake = targetIndex == route.size() - 1 || (target.y < source.y && !throughPoint);
        double forward = brake ? Math.min(1, Math.max(.12, target.distance(pose) / .3)) : 1;
        double travel = Math.min(target.distance(pose) + .03,
                Math.hypot(pose.vx, pose.vz) * 2 + WALK_BLOCKS_PER_TICK * 2 * forward);
        if (!fits(geometry, cut, pose, target, source.y, travel, jumping)) {
            // Baritone MovementDiagonal "edging": steer round a blocked side column.
            if (cut && !jumping && startBend(pose, geometry, source, target)) return followBend(pose, geometry, source);
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
        double reach = Math.hypot(pose.vx, pose.vz) * 2 + SPRINT_BLOCKS_PER_TICK * 2;
        boolean sprint = sprintAscent
                ? policy.sprint && pose.food > 6 && forward == 1 && geometry.safe(pose, target, source.y, reach, true, policy.maxDrop)
                : policy.sprint && pose.food > 6 && !jumping && forward == 1
                && target.y <= pose.y + .1
                && (!cut || geometry.cornerSprintOk(source, route.get(index), policy.maxDrop))
                && fits(geometry, cut, pose, target, source.y, reach, false)
                && cornerClear(geometry, pose, target, targetIndex, reach);
        if (moved < .01 && pose.grounded && !jumping) stalled++; else stalled = 0;
        if (stalled > 30) { stop("route_no_progress"); return Input.STOP; }
        return new Input(forward, yaw, jumping, sprint);
    }

    /**
     * Sprint through a route point only if the body, carried over it, is still safe on the next
     * segment's heading (corner overshoot). Applies once the point is within one sprint reach.
     */
    private boolean cornerClear(Geometry geometry, Pose pose, Point target, int targetIndex, double reach) {
        if (targetIndex + 1 >= route.size() || target.distance(pose) >= reach) return true;
        var after = route.get(targetIndex + 1);
        var centre = new Pose(target.x + .5, target.y, target.z + .5, 0, 0, 0, true, pose.food, false);
        var onward = new Pose(centre.x, centre.y, centre.z, 0, 0, yawTo(centre, after), true, pose.food, false);
        boolean diagonal = after.y == target.y && Math.abs(after.x - target.x) == 1 && Math.abs(after.z - target.z) == 1;
        return (!diagonal || geometry.cornerSprintOk(target, after, policy.maxDrop))
                && fits(geometry, diagonal, onward, after, target.y, SPRINT_BLOCKS_PER_TICK * 2, false);
    }

    private boolean fits(Geometry geometry, boolean cut, Pose pose, Point target, double originY, double distance, boolean jumping) {
        return cut ? geometry.safeDiagonal(pose, target, originY, distance, jumping, policy.maxDrop)
                : geometry.safe(pose, target, originY, distance, jumping, policy.maxDrop);
    }

    // Reference: Baritone MovementDiagonal edging past one blocked side. Waypoint = the shared
    // corner point shifted 0.31 off the blocked column along its own axis and 0.31 into the
    // clear side, so both legs keep the hitbox (0.3 radius) clear of the blocked column.
    private static final double BEND_OFFSET = .31;

    /** Picks the first bend whose both legs pass the diagonal sweep; false if neither does. */
    private boolean startBend(Pose pose, Geometry geometry, Point source, Point target) {
        double dx = target.x - source.x, dz = target.z - source.z;
        double cx = source.x + (dx > 0 ? 1 : 0), cz = source.z + (dz > 0 ? 1 : 0);
        double[][] options = {{cx - dx * BEND_OFFSET, cz + dz * BEND_OFFSET},   // x-neighbour blocked
                              {cx + dx * BEND_OFFSET, cz - dz * BEND_OFFSET}};  // z-neighbour blocked
        for (var option : options) {
            var waypoint = new Point(option[0] - .5, source.y, option[1] - .5);
            double first = waypoint.distance(pose);
            var toWaypoint = new Pose(pose.x, pose.y, pose.z, pose.vx, pose.vz, Math.toDegrees(yawRad(pose, waypoint)),
                    pose.grounded, pose.food, pose.inWater);
            var atWaypoint = new Pose(option[0], pose.y, option[1], 0, 0, Math.toDegrees(yawRad(new Pose(option[0], pose.y, option[1], 0, 0, 0, true, 20), target)),
                    true, pose.food, false);
            if (geometry.safeDiagonal(toWaypoint, waypoint, source.y, first, false, policy.maxDrop)
                    && geometry.safeDiagonal(atWaypoint, target, source.y, target.distance(atWaypoint), false, policy.maxDrop)) {
                bend = waypoint; bendIndex = index; return true;
            }
        }
        return false;
    }

    private Input followBend(Pose pose, Geometry geometry, Point source) {
        double distance = bend.distance(pose);
        double turn = wrap(yawTo(pose, bend) - pose.yaw);
        double yaw = Math.max(-45, Math.min(45, turn));
        if (Math.abs(turn) > 8) return new Input(0, yaw, false, false);
        if (!geometry.safeDiagonal(pose, bend, source.y, distance + .03, false, policy.maxDrop)) {
            bend = null; stop("route_swept_geometry_unknown_or_unsafe"); return Input.STOP;
        }
        // Walk pace only: no sprint round a bend.
        return new Input(Math.min(1, Math.max(.12, distance / .3)), yaw, false, false);
    }

    /** Later route point (1 or 2) the body already stands on or beside, or -1. Baritone PathExecutor re-sync. */
    private int resyncIndex(Pose pose) {
        for (int n = Math.min(2, route.size() - 1); n >= 1; n--) {
            var p = route.get(n);
            boolean inCell = Math.floor(pose.x) == p.x && Math.floor(pose.z) == p.z;
            boolean level = Math.abs(pose.y - p.y) <= .2;
            boolean wet = pose.inWater && inCell && pose.y >= p.y - 1.0 && pose.y <= p.y + .2;
            if ((pose.grounded && inCell && level) || wet || (p.distance(pose) <= .6 && level)) return n;
        }
        return -1;
    }

    /** Arrival: the expected column at a supported, swimming or climbing height. */
    private boolean arrived(Pose pose, Geometry geometry, Point source, Point target) {
        if (Math.floor(pose.x) != target.x || Math.floor(pose.z) != target.z) return false;
        boolean last = index == route.size() - 1;
        if (pose.grounded && Math.abs(pose.y - target.y) <= .2 && (!last || target.distance(pose) <= .2)) return true;
        // Swimming bobs the feet up to a block under the surface stance.
        if (policy.swim && pose.inWater && pose.y >= target.y - 1.0 && pose.y <= target.y + .2
                && (!last || target.distance(pose) <= .35) && geometry.swimSurface(target)) return true;
        return policy.climb && pose.y >= target.y - .5 && pose.y <= target.y + (target.y > source.y ? .9 : .3)
                && (!last || target.distance(pose) <= .45) && geometry.climbable(target);
    }

    /**
     * Edge i-1 to i is a 1-block ascent entered straight from a level traverse, another ascent
     * (staircase) or a 1-block descent, and continued in the same direction
     * (Baritone PathExecutor.sprintableAscend; the descend case follows its straight-ascend skip).
     */
    private boolean sprintAscentAt(int i) {
        if (!policy.sprint || i < 2 || i + 1 >= route.size()) return false;
        var before = route.get(i - 2); var source = route.get(i - 1); var target = route.get(i); var after = route.get(i + 1);
        double dx = target.x - source.x, dz = target.z - source.z;
        return target.y == source.y + 1 && Math.abs(before.y - source.y) <= 1 && dx + dz != 0
                && source.x - before.x == dx && source.z - before.z == dz
                && after.x - target.x == dx && after.z - target.z == dz;
    }

    // Derived from Baritone MovementTraverse/Ascend water handling: hold jump to stay at
    // the surface and swim toward the next stance (Settings.sprintInWater).
    private Input swim(Pose pose, Geometry geometry, Point source, Point target, double moved) {
        double turn = wrap(yawTo(pose, target) - pose.yaw);
        double yaw = turn;
        // The sweep runs on the new heading: heading changes are instant (Baritone LookBehavior).
        pose = new Pose(pose.x, pose.y, pose.z, pose.vx, pose.vz, pose.yaw + turn, pose.grounded, pose.food, pose.inWater);
        boolean exiting = !geometry.swimSurface(target);
        double distance = target.distance(pose);
        // Already over the target column but below the stance: do not steer, just rise.
        if (distance < .3) return new Input(0, 0, true, false);
        boolean last = index == route.size() - 1;
        double forward = last && !exiting ? Math.min(1, Math.max(.12, distance / .3)) : 1;
        boolean jump = pose.inWater || (target.y > pose.y + .6 && pose.grounded && distance <= 1.2);
        double travel = Math.min(distance + .03, Math.hypot(pose.vx, pose.vz) * 2 + WALK_BLOCKS_PER_TICK * 2 * forward);
        if (!geometry.swimSafe(pose, target, source.y, travel, jump)) {
            stop("route_swept_geometry_unknown_or_unsafe"); return Input.STOP;
        }
        if (moved < .01 && (pose.grounded || pose.inWater)) stalled++; else stalled = 0;
        if (stalled > 30) { stop("route_no_progress"); return Input.STOP; }
        boolean sprint = policy.sprint && pose.food > 6 && pose.inWater && forward == 1 && !exiting;
        return new Input(forward, yaw, jump, sprint);
    }

    // Derived from Baritone MovementPillar/MovementDescend ladder handling: jump climbs a
    // ladder or vine, releasing jump descends. Side entries and exits just walk, jumping
    // when they rise; the climbable cells themselves are checked by the geometry.
    private Input climb(Pose pose, Point source, Point target, double moved) {
        boolean vertical = target.x == source.x && target.z == source.z;
        double turn = wrap(yawTo(pose, target) - pose.yaw);
        double yaw = Math.max(-45, Math.min(45, turn));
        boolean up = target.y > source.y;
        if (moved < .01) stalled++; else stalled = 0;
        if (stalled > 30) { stop("route_no_progress"); return Input.STOP; }
        if (vertical) {
            boolean off = target.distance(pose) > .35;
            return new Input(off && Math.abs(turn) <= 45 ? .3 : 0, off ? yaw : 0, up, false);
        }
        return new Input(Math.abs(turn) > 45 ? 0 : 1, yaw, up, false);
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
    private static double yawRad(Pose pose, Point target) { return Math.toRadians(yawTo(pose, target)); }
    private static double wrap(double yaw) { return ((yaw + 180) % 360 + 360) % 360 - 180; }
}
