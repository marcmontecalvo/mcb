package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.List;

/**
 * Pure decisions of the per-tick escape op (no Minecraft types), checked by EscapeLogicTest.
 * Each tick: steer the heading whose next cell is safe and leads furthest from every
 * hostile in range (a repulsion field, so new spawns turn the run), sprint, and jump
 * on landing to sprint-jump. Docs: COMPANION_COMBAT_PLAN_2026-11-23.md section 3.
 */
final class EscapeLogic {
    private EscapeLogic() {}

    static final int DEFAULT_RADIUS = 24, DEFAULT_CLEAR = 16, DEFAULT_MAX_TICKS = 200;
    static final int CLEAR_TICKS = 20, CORNERED_TICKS = 20;
    static final double LOOKAHEAD = 2.0;  // blocks of travel each heading is scored at
    static final double STAY = 0.9;  // the current heading's field is scaled by this: no jitter
    static final int SPRINT_FOOD = 6;  // vanilla: sprinting needs food above 6
    /** Eight headings, counter-clockwise from east (+x); 2 is south (+z) in Minecraft. */
    static final int[] DX = {1, 1, 0, -1, -1, -1, 0, 1};
    static final int[] DZ = {0, 1, 1, 1, 0, -1, -1, -1};

    record Params(int radius, int clear, int maxTicks) {}

    private static int integer(JsonObject request, String key, int fallback, int min, int max) {
        if (!request.has(key)) return fallback;
        var element = request.get(key);
        if (!element.isJsonPrimitive() || !element.getAsJsonPrimitive().isNumber()) throw new BodyActions.Fail("bad_request");
        double value = element.getAsDouble();
        if (value != Math.rint(value) || value < min || value > max) throw new BodyActions.Fail("bad_request");
        return (int) value;
    }

    static Params parse(JsonObject request) {
        int radius = integer(request, "radius", DEFAULT_RADIUS, 4, 32);
        int clear = integer(request, "clear", DEFAULT_CLEAR, 4, 32);
        if (clear > radius) throw new BodyActions.Fail("bad_request");
        return new Params(radius, clear, integer(request, "max_ticks", DEFAULT_MAX_TICKS, 20, 1200));
    }

    /**
     * The safe heading (0..7) minimising the hostiles' repulsion field Σ 1/d² at LOOKAHEAD
     * blocks along it; -1 when none is safe. With no hostile, the current heading if safe.
     * mobs hold {x, z}; current is -1 before the first pick.
     */
    static int heading(double x, double z, List<double[]> mobs, boolean[] safe, int current) {
        if (mobs.isEmpty() && current >= 0 && safe[current]) return current;
        int best = -1;
        double bestField = Double.POSITIVE_INFINITY;
        for (int h = 0; h < 8; h++) {
            if (!safe[h]) continue;
            double norm = LOOKAHEAD / Math.hypot(DX[h], DZ[h]);
            double px = x + DX[h] * norm, pz = z + DZ[h] * norm, field = 0;
            for (var m : mobs) {
                double d = Math.max(.5, Math.hypot(m[0] - px, m[1] - pz));
                field += 1 / (d * d);
            }
            if (h == current) field *= STAY;
            if (field < bestField) { bestField = field; best = h; }
        }
        return best;
    }

    static boolean sprint(int food) { return food > SPRINT_FOOD; }

    /** Sprint-jump: on the ground, sprinting, headroom here and ahead, the next cell not a drop. */
    static boolean jump(boolean onGround, boolean sprinting, boolean headroom, boolean levelAhead) {
        return onGround && sprinting && headroom && levelAhead;
    }

    /** End reason before moving this tick, or null to carry on. */
    static String end(int ticks, int maxTicks, int clearTicks, int corneredTicks) {
        if (clearTicks >= CLEAR_TICKS) return "clear";
        if (corneredTicks >= CORNERED_TICKS) return "cornered";
        if (ticks > maxTicks) return "timeout";
        return null;
    }
}
