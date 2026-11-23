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
