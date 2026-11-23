package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.List;
import java.util.Objects;

/** Pure decisions of the per-tick fight op (no Minecraft types), checked by FightLogicTest. */
final class FightLogic {
    private FightLogic() {}

    static final int DEFAULT_RADIUS = 12, DEFAULT_FLOOR = 6, DEFAULT_MAX_TICKS = 600, DEFAULT_LEASH = 6;
    static final double AVOID_DISTANCE = 3.5;
    static final double MAX_DY = 6;
    /** Candidate feet offsets for the next cell: level, one-block step up (jump), then drops up to 3. */
    static final int[] STEP_OFFSETS = {0, 1, -1, -2, -3};
    static final int MAX_DROP = 3;

    /** Validated request. anchor is null when absent (no leash). */
    record Params(String entity, int radius, int floor, int maxTicks, double[] anchor, int leash, String weapon) {}

    /** A candidate living entity. distance is 3D, dy is target minus body. */
    record Mob(String uuid, String type, double distance, double dy, boolean lineOfSight) {}

    private static int integer(JsonObject request, String key, int fallback, int min, int max) {
        if (!request.has(key)) return fallback;
        var element = request.get(key);
        if (!element.isJsonPrimitive() || !element.getAsJsonPrimitive().isNumber()) throw new BodyActions.Fail("bad_request");
        double value = element.getAsDouble();
        if (value != Math.rint(value) || value < min || value > max) throw new BodyActions.Fail("bad_request");
        return (int) value;
    }

    static Params parse(JsonObject request) {
        String entity = null, weapon = null;
        if (request.has("entity")) {
            var e = request.get("entity");
            if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) throw new BodyActions.Fail("bad_request");
            try { entity = java.util.UUID.fromString(e.getAsString()).toString(); }
            catch (IllegalArgumentException bad) { throw new BodyActions.Fail("bad_request"); }
        }
        if (request.has("weapon")) {
            var w = request.get("weapon");
            if (!w.isJsonPrimitive() || !w.getAsJsonPrimitive().isString() || w.getAsString().isBlank()) throw new BodyActions.Fail("bad_request");
            weapon = w.getAsString();
        }
        double[] anchor = null;
        if (request.has("anchor")) {
            var a = request.get("anchor");
            if (!a.isJsonArray() || a.getAsJsonArray().size() != 3) throw new BodyActions.Fail("bad_request");
            anchor = new double[3];
            for (int i = 0; i < 3; i++) {
                var n = a.getAsJsonArray().get(i);
                if (!n.isJsonPrimitive() || !n.getAsJsonPrimitive().isNumber() || !Double.isFinite(n.getAsDouble()))
                    throw new BodyActions.Fail("bad_request");
                anchor[i] = n.getAsDouble();
            }
        }
        return new Params(entity, integer(request, "radius", DEFAULT_RADIUS, 2, 32), integer(request, "floor", DEFAULT_FLOOR, 0, 19),
                integer(request, "max_ticks", DEFAULT_MAX_TICKS, 20, 2400), anchor, integer(request, "leash", DEFAULT_LEASH, 1, 32), weapon);
    }

    /** Types the fight engages on its own: the hostile set minus the two it must not trade blows with. */
    static boolean fightable(String type) {
        return RouteReflex.HOSTILES.contains(type) && !avoidType(type);
    }

    static boolean avoidType(String type) {
        return "minecraft:creeper".equals(type) || "minecraft:warden".equals(type);
    }

    /** Creeper or warden within AVOID_DISTANCE. */
    static boolean avoid(List<Mob> mobs) {
        return avoid(mobs, false);
    }

    /** As {@link #avoid(List)}; a hit-and-back fight targets creepers, so only a warden ends it. */
    static boolean avoid(List<Mob> mobs, boolean creepersFought) {
        for (var mob : mobs) {
            if (!avoidType(mob.type()) || mob.distance() > AVOID_DISTANCE) continue;
            if (creepersFought && "minecraft:creeper".equals(mob.type())) continue;
            return true;
        }
        return false;
    }

    /** End reason before acting this tick, or null to carry on. ticks counts this tick. */
    static String endReason(double health, int floor, int ticks, int maxTicks) {
        if (health <= floor) return "low_health";
        if (ticks > maxTicks) return "timeout";
        return null;
    }

    /** The requested entity while present and within radius, else the nearest fightable mob in view; null when none. */
    static Mob pick(String requested, List<Mob> mobs, int radius) {
        if (requested != null)
            for (var mob : mobs) if (mob.uuid().equals(requested) && mob.distance() <= radius) return mob;
        Mob best = null;
        for (var mob : mobs) {
            if (!fightable(mob.type()) || mob.distance() > radius || Math.abs(mob.dy()) > MAX_DY || !mob.lineOfSight()) continue;
            if (best == null || mob.distance() < best.distance()) best = mob;
        }
        return best;
    }

    /** Blocks past a stand-off band's far edge the fight still holds its target (a strafe, a step back). */
    static final int KEEP_MARGIN = 3;
    /** The radius bound of the request: the op never scans a wider box. */
    static final int MAX_REACH = 32;

    /**
     * The radius the fight looks in: the requested one, widened to the stand-off band's far edge plus KEEP_MARGIN.
     * A bow keeping 8 to 15 blocks off would otherwise back out of a radius of 12 and end the fight with the target
     * alive.
     */
    static int reach(int radius, double keepMax) {
        return Math.min(MAX_REACH, Math.max(radius, (int) Math.ceil(keepMax) + KEEP_MARGIN));
    }

    /**
     * The feet y of the stance one cardinal step (dx, dz) from the stance at column (x, z), feet y, or null when it
     * cannot be walked. y is in half blocks (a slab is a stance), as the fight motor's own step reads it.
     */
    @FunctionalInterface
    interface Steps { Double next(int x, double y, int z, int dx, int dz); }

    /**
     * The feet y of a standable, safe stance in column (cx, cz) one step from {@code pose} at feet y (level, a step
     * up, a drop of up to MAX_DROP), inside the leash; null when there is none.
     */
    static Double stance(RouteGeometry geometry, RouteMotor.Pose pose, int cx, double y, int cz,
                         double[] anchor, int leash) {
        for (int offset : STEP_OFFSETS) {
            var cell = new RouteMotor.Point(cx, y + offset, cz);
            if (!withinLeash(anchor, leash, cx, cell.y(), cz)) continue;
            if (geometry.standable(cell) && geometry.safe(pose, cell, y, .6, offset > 0, MAX_DROP)) return y + offset;
        }
        return null;
    }

    /**
     * The chase's steps over real terrain: a body standing at each cell's centre, facing the step (the safety
     * sweep samples along the pose's facing; a fixed facing checked every step as if it went south).
     */
    static Steps steps(RouteGeometry geometry, int food, double[] anchor, int leash) {
        return (x, y, z, dx, dz) -> stance(geometry,
                new RouteMotor.Pose(x + .5, y, z + .5, 0, 0, yaw(dx, dz), true, food), x + dx, y, z + dz, anchor, leash);
    }

    /** Minecraft yaw (degrees) facing the step (dx, dz): 0 is +z (south), 90 is -x (west). */
    static float yaw(double dx, double dz) {
        return (float) Math.toDegrees(Math.atan2(-dx, dz));
    }

    /** A chase path never leaves this square round its start (blocks each way). */
    static final int CHASE_SPAN = 16;
    /** Cells a chase search may expand. */
    static final int CHASE_NODES = 4096;
    private static final int[][] CARDINALS = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};

    /**
     * The shortest walk (breadth first, one cardinal step at a time, as the fight motor walks) from the feet cell
     * {@code start} to a cell whose centre is within {@code range} blocks across and 1.5 up or down of the target
     * (tx, ty, tz): the cells after the start, in order. Empty when the start is in range already; null when no such
     * cell is within CHASE_SPAN and maxNodes (a cliff: the caller says unreachable).
     */
    static List<double[]> chase(double[] start, double tx, double ty, double tz, double range, Steps steps,
                                int maxNodes) {
        return search(start, tx, ty, tz, range, steps, maxNodes).path();
    }

    /** A chase search: the path (as {@link #chase}) and how many cells it reached, the start included. */
    record Search(List<double[]> path, int expanded) { }

    static Search search(double[] start, double tx, double ty, double tz, double range, Steps steps, int maxNodes) {
        java.util.function.Predicate<double[]> goal = c -> Math.hypot(c[0] + .5 - tx, c[2] + .5 - tz) <= range
                && Math.abs(c[1] - ty) <= 1.5;
        if (goal.test(start)) return new Search(List.of(), 1);
        var parent = new java.util.HashMap<Long, double[]>();
        var queue = new java.util.ArrayDeque<double[]>();
        parent.put(cellKey(start), null);
        queue.add(start);
        while (!queue.isEmpty() && parent.size() <= maxNodes) {
            double[] at = queue.poll();
            for (int[] d : CARDINALS) {
                int x = (int) at[0], z = (int) at[2], nx = x + d[0], nz = z + d[1];
                if (Math.abs(nx - start[0]) > CHASE_SPAN || Math.abs(nz - start[2]) > CHASE_SPAN) continue;
                Double ny = steps.next(x, at[1], z, d[0], d[1]);
                if (ny == null) continue;
                double[] next = {nx, ny, nz};
                long key = cellKey(next);
                if (parent.containsKey(key)) continue;
                parent.put(key, at);
                if (goal.test(next)) {
                    var path = new java.util.ArrayList<double[]>();
                    for (double[] c = next; c != start; c = parent.get(cellKey(c))) path.add(c);
                    java.util.Collections.reverse(path);
                    return new Search(path, parent.size());
                }
                queue.add(next);
            }
        }
        return new Search(null, parent.size());
    }

    /** Ticks with no chase path before a target that is not coming closer is left "unreachable" (2 s). */
    static final int CHASE_GIVE_UP = 40;
    /** Blocks nearer than when the path was lost: the target is coming to the body (a zombie walking up). */
    static final double CLOSING = 1.0;

    /**
     * The fight's call on a target no chase path reaches, lost at tick {@code since} from {@code lostAt} blocks:
     * "closing" when it has come CLOSING nearer (wait on, from here), "unreachable" when CHASE_GIVE_UP ticks went by
     * without that, else null (hold still and let it come).
     */
    static String noPath(int since, double lostAt, int now, double distance) {
        if (distance <= lostAt - CLOSING) return "closing";
        return now - since >= CHASE_GIVE_UP ? "unreachable" : null;
    }

    /** A stance's key: column x and z, feet y in half blocks. */
    private static long cellKey(double[] c) {
        long x = (long) c[0] & 0x3ffffff, z = (long) c[2] & 0x3ffffff, y = Math.round(c[1] * 2) & 0xfff;
        return x << 38 | y << 26 | z;
    }

    /** Damage the target took between two ticks: the health it lost, never negative, a kill counting what it had. */
    static double dealt(double before, double after) {
        return Math.max(0, before - Math.max(0, after));
    }

    /** Cardinal unit step (dominant axis) from the body toward the target: {sx, sz}. */
    static int[] direction(double dx, double dz) {
        return Math.abs(dx) >= Math.abs(dz) ? new int[]{dx >= 0 ? 1 : -1, 0} : new int[]{0, dz >= 0 ? 1 : -1};
    }

    /** True when the cell centre is within leash of the anchor (always true without an anchor). */
    static boolean withinLeash(double[] anchor, int leash, int cellX, double cellY, int cellZ) {
        if (anchor == null) return true;
        double dx = cellX + .5 - anchor[0], dy = cellY - anchor[1], dz = cellZ + .5 - anchor[2];
        return Math.sqrt(dx * dx + dy * dy + dz * dz) <= leash;
    }

    /** The weapon to hold: the requested item while carried, else the best one; null means an empty hand. */
    static String desiredWeapon(String requested, boolean requestedCarried, String best) {
        return requested != null && requestedCarried ? requested : best;
    }

    static boolean needsReselect(String held, String desired) { return !Objects.equals(held, desired); }
}
