package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.Set;

/**
 * Pure combat technique of the fight op (no Minecraft types), checked by CombatLogicTest.
 * Game rules are from the 26.3 bytecode (docs/COMBAT_MECHANICS_26.3.md); the tactic comes
 * from Python's companion_combat (docs/COMPANION_COMBAT_PLAN_2026-11-23.md).
 */
final class CombatLogic {
    private CombatLogic() {}

    /** Player.attack: a hit is strong (crit, sprint knockback, sweep) above this scale. */
    static final double STRONG = 0.9;
    /** Ticks from leaving the ground to the start of the fall (jump 0.42, gravity 0.08, drag 0.98). */
    static final int JUMP_LEAD = 6;
    static final double JAB_MIN = 2.0, JAB_MAX = 4.5;  // spear AttackRange, from the eye
    static final double CREEPER_SAFE = 7.5;  // a started swell runs down only past 7 blocks
    static final double SNIPE_CLOSE = 4.0;  // a target this close is fought with the melee weapon
    static final int FULL_DRAW = 20;  // BowItem.getPowerForTime reaches 1.0
    static final double ARROW_SPEED = 3.0, ARROW_DRAG = .99, ARROW_GRAVITY = .05;
    private static final Set<String> STYLES = Set.of("melee", "poke", "lance", "hit_and_back", "snipe", "hunt");

    enum Kind {
        SWORD, AXE, SPEAR, TRIDENT, BOW, OTHER;
        /** Swung through Player.attack, which can crit whatever is held; a spear jab and an arrow cannot. */
        boolean crits() { return this != SPEAR && this != BOW; }
    }

    static Kind kind(String item) {
        if (item == null) return Kind.OTHER;
        if (item.endsWith("_sword")) return Kind.SWORD;
        if (item.endsWith("_axe") && !item.endsWith("_pickaxe")) return Kind.AXE;
        if (item.endsWith("_spear")) return Kind.SPEAR;
        if (item.equals("minecraft:trident")) return Kind.TRIDENT;
        if (item.equals("minecraft:bow")) return Kind.BOW;
        return Kind.OTHER;
    }

    /** keepMin/keepMax are 0 without a stand-off band. */
    record Tactic(String style, boolean crit, boolean sweep, double keepMin, double keepMax, String melee, boolean noGaze) {}

    static final Tactic DEFAULT = new Tactic("melee", true, true, 0, 0, null, false);

    private static boolean flag(JsonObject t, String key, boolean fallback) {
        if (!t.has(key)) return fallback;
        var e = t.get(key);
        if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isBoolean()) throw new BodyActions.Fail("bad_request");
        return e.getAsBoolean();
    }

    /** The request's optional "tactic" object; absent means crit melee. */
    static Tactic parseTactic(JsonObject request) {
        if (!request.has("tactic")) return DEFAULT;
        var raw = request.get("tactic");
        if (!raw.isJsonObject()) throw new BodyActions.Fail("bad_request");
        var t = raw.getAsJsonObject();
        if (!t.has("style") || !t.get("style").isJsonPrimitive() || !STYLES.contains(t.get("style").getAsString()))
            throw new BodyActions.Fail("bad_request");
        double min = 0, max = 0;
        if (t.has("keep")) {
            var k = t.get("keep");
            if (!k.isJsonArray() || k.getAsJsonArray().size() != 2) throw new BodyActions.Fail("bad_request");
            for (var n : k.getAsJsonArray())
                if (!n.isJsonPrimitive() || !n.getAsJsonPrimitive().isNumber()) throw new BodyActions.Fail("bad_request");
            min = k.getAsJsonArray().get(0).getAsDouble();
            max = k.getAsJsonArray().get(1).getAsDouble();
            if (!(min >= 0 && min < max && max <= 32)) throw new BodyActions.Fail("bad_request");
        }
        String melee = null;
        if (t.has("melee")) {
            var m = t.get("melee");
            if (!m.isJsonPrimitive() || !m.getAsJsonPrimitive().isString() || m.getAsString().isBlank())
                throw new BodyActions.Fail("bad_request");
            melee = m.getAsString();
        }
        return new Tactic(t.get("style").getAsString(), flag(t, "crit", false), flag(t, "sweep", true), min, max, melee,
                flag(t, "no_gaze", false));
    }

    enum Act { NONE, JUMP, STRIKE, SPRINT_STRIKE }

    /**
     * One tick of a swung weapon. With a crit wanted and possible here (not in water, on a
     * ladder or under a low ceiling), jump when the cooldown will be full as the fall starts
     * and strike on the way down. Otherwise strike only at full strength; when a grounded
     * sword hit would sweep bystanders the hunt must spare, strike sprinting (no sweep).
     */
    static Act melee(boolean inReach, double scale, double ticksToFull, boolean onGround, boolean falling,
                     boolean critHere, boolean critWanted, boolean kindCrits, boolean sweepRisk, boolean sweepOk) {
        if (!inReach) return Act.NONE;
        boolean strong = scale > STRONG;
        if (critWanted && kindCrits && critHere) {
            if (!onGround) return falling && strong ? Act.STRIKE : Act.NONE;
            return ticksToFull <= JUMP_LEAD ? Act.JUMP : Act.NONE;
        }
        if (!strong) return Act.NONE;
        return sweepRisk && !sweepOk ? Act.SPRINT_STRIKE : Act.STRIKE;
    }

    /** FLEE: sprint away, facing away (outrunning a creeper's swell). */
    enum Move { HOLD, APPROACH, RETREAT, FLEE }

    /** Stay inside the stand-off band [min, max]. */
    static Move keep(double distance, double min, double max) {
        if (distance < min) return Move.RETREAT;
        if (distance > max) return Move.APPROACH;
        return Move.HOLD;
    }

    /** A draining swell at least this far on (of the fuse) is still kept off: re-lit, it would blow too soon. */
    static final double LIT_RESIDUAL = .5;

    /**
     * Creeper.tick explodes when the swell reaches its maximum, and it climbs only while the swell
     * direction is up (SwellGoal: the target's feet within 3 blocks). A draining swell (direction -1)
     * is not lit: a jab leaves a one-tick flicker that drains at once.
     */
    static boolean lit(int swellDir, double swelling) {
        return swellDir > 0 || swelling >= LIT_RESIDUAL;
    }

    /** Ticks a poke leads the gap by: the body takes about five to turn from walking in to backing off. */
    static final int POKE_LEAD = 6;

    /**
     * Spear: hold the band (eye to box) judged where the gap will be POKE_LEAD ticks on at
     * {@code closing} (blocks a tick it shrank by; an opening gap is not led), so the body
     * backs off before a target walking in is inside the band's near edge. A swelling
     * creeper, or one still being fled from, is outrun past CREEPER_SAFE (feet to feet)
     * before the band is taken again.
     */
    static Move poke(double eye, double closing, double distance, double min, double max, boolean swelling,
                     boolean fleeing) {
        if (swelling || fleeing && distance <= CREEPER_SAFE) return Move.FLEE;
        return keep(eye - Math.max(0, closing) * POKE_LEAD, min, max);
    }

    /**
     * Creeper.SwellGoal lights with the target's feet inside 3 blocks of its own; a box edge is
     * a half-width nearer than the feet, so a poke whose whole band (eye to box) keeps the feet
     * past the fuse may fight one, as hit-and-back does.
     */
    static final double CREEPER_FUSE = 3.0, CREEPER_HALF_WIDTH = 0.3;

    static boolean fightsCreepers(Tactic t) {
        if (t.style().equals("hit_and_back") || t.style().equals("lance")) return true;  // lance: LanceLogic flees after a hit
        return t.style().equals("poke") && t.keepMin() + CREEPER_HALF_WIDTH > CREEPER_FUSE;
    }

    static boolean jabReach(double eyeDistance) { return eyeDistance >= JAB_MIN && eyeDistance <= JAB_MAX; }

    /**
     * Creeper: a sprint hit knocks it back; then keep off past CREEPER_SAFE until its swell is
     * gone (it only runs down out there) and go again. A swelling creeper is never hit.
     */
    static String hitAndBack(boolean retreating, double distance, boolean swelling, boolean inReach, double scale) {
        if (retreating || swelling) return distance > CREEPER_SAFE && !swelling ? "approach" : "retreat";
        if (inReach && scale > STRONG) return "strike";
        return "approach";
    }

    static boolean snipeClose(double distance, boolean hasMelee) { return hasMelee && distance < SNIPE_CLOSE; }

    enum Draw { NONE, START, HOLD, RELEASE }

    /**
     * One tick of a bow (or trident) draw the fight runs itself: start with the target in sight,
     * release at full draw with it in sight and a shot solved, else hold the draw.
     */
    static Draw draw(boolean using, int ticksUsing, int fullDraw, boolean sight, boolean aimed) {
        if (!using) return sight ? Draw.START : Draw.NONE;
        return ticksUsing >= fullDraw && sight && aimed ? Draw.RELEASE : Draw.HOLD;
    }

    /**
     * {yaw, pitch} in degrees (Minecraft: yaw 0 = +z, pitch up negative) for a full-draw arrow
     * from (0,0,0) to hit a target at (dx,dy,dz) moving (vx,vy,vz) per tick; null out of range.
     * Low arc, three lead passes; each pass bisects the pitch on the simulated flight.
     */
    static double[] aim(double dx, double dy, double dz, double vx, double vy, double vz) {
        double ax = dx, ay = dy, az = dz;
        double[] shot = null;
        for (int pass = 0; pass < 3; pass++) {
            shot = solve(Math.hypot(ax, az), ay);
            if (shot == null) return null;
            double t = shot[1];
            ax = dx + vx * t; ay = dy + vy * t; az = dz + vz * t;
        }
        shot = solve(Math.hypot(ax, az), ay);
        if (shot == null) return null;
        return new double[]{Math.toDegrees(Math.atan2(-ax, az)), -shot[0]};
    }

    /** {elevation degrees (up positive), flight ticks} on the low arc, or null. */
    private static double[] solve(double horizontal, double height) {
        double lo = -80, hi = 42;  // ~42 degrees is the drag-limited range peak
        double[] top = fly(hi, horizontal);
        if (top == null || top[0] < height) return null;
        double[] at = null;
        for (int i = 0; i < 50; i++) {
            double mid = (lo + hi) / 2;
            at = fly(mid, horizontal);
            if (at != null && at[0] >= height) hi = mid; else lo = mid;
        }
        at = fly(hi, horizontal);
        return at == null ? null : new double[]{hi, at[1]};
    }

    /** {height at the horizontal distance, ticks}, or null when it falls short within 200 ticks. */
    private static double[] fly(double elevation, double horizontal) {
        double r = Math.toRadians(elevation);
        double vh = Math.cos(r) * ARROW_SPEED, vy = Math.sin(r) * ARROW_SPEED, h = 0, y = 0;
        for (int t = 1; t <= 200; t++) {
            double nh = h + vh, ny = y + vy;
            if (nh >= horizontal) {
                double f = vh == 0 ? 0 : (horizontal - h) / vh;
                return new double[]{y + vy * f, t - 1 + f};
            }
            h = nh; y = ny;
            vh *= ARROW_DRAG; vy = vy * ARROW_DRAG - ARROW_GRAVITY;
            if (y < -64) return null;
        }
        return null;
    }
}
