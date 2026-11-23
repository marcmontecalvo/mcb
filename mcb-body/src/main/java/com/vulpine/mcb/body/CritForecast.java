package com.vulpine.mcb.body;

/**
 * Where a jump-crit lands (pure, checked by CritForecastTest). A crit is struck on the way down
 * of a hop, ticks 7 to 11 after the jump; by then the target has walked on and the body has moved
 * with its own input. This simulates both along the line between them, tick by tick, for each way
 * to hop (in place, backward, walking or sprinting at it), and picks the hop whose strike comes
 * first with the target in reach and the body still outside the target's own reach. With no hop
 * that lands now, it picks the ground step that lets one land once the cooldown is back.
 * The physics and the mob speeds are from docs/MOB_MOVEMENT_26.3.md.
 */
final class CritForecast {
    private CritForecast() {}

    // the body (Player): speed attribute, sprint, accelerations and drags per tick
    static final double WALK = 0.1, SPRINT = 0.13, INPUT = 0.98;
    static final double AIR_WALK = 0.02, AIR_SPRINT = 0.026, SPRINT_JUMP = 0.2;
    static final double GROUND_DRAG = 0.546, AIR_DRAG = 0.91;
    static final double JUMP = 0.42, GRAVITY = 0.08, FALL_DRAG = 0.98, EYE = 1.62;
    static final int HOP = 12;
    /** Struck on hop ticks FIRST..LAST with the target in reach on that tick and the next. */
    static final int FIRST = 7, LAST = 10;
    /** Mob.DEFAULT_ATTACK_REACH (sqrt 2.04 - 0.6) and the body's half width. */
    static final double MOB_REACH = Math.sqrt(2.04) - 0.6, BODY_HALF = 0.3;
    /** Margins against the forecast's error: kept outside a mob's reach, inside the body's. */
    static final double SAFE = 0.2, REACH_MARGIN = 0.2;
    /** A skeleton inside 7.5 blocks strafes backward: 0.0688 b/tick along its facing. */
    static final double ARCHER_BACK = 0.0688, ARCHER_CLOSE = 7.5;
    /** Past this gap beyond reach nothing is forecast: the body walks in. */
    static final double FAR = 6;

    /** Hop input: forward along the line (1 toward the target, -1 away), and sprinting. */
    enum Hop {
        STAND(0, false), BACK(-1, false), WALK(1, false), SPRINT(1, true);
        final int forward; final boolean sprint;
        Hop(int forward, boolean sprint) { this.forward = forward; this.sprint = sprint; }
    }

    /** A ground tick: hold, back off, walk in; STRIKE: nothing lands safely and the target is upon the body. */
    enum Step { HOLD, RETREAT, APPROACH, STRIKE }

    /**
     * The line from the body to the target, now. gap: centre to centre, horizontal; half: gap minus
     * the eye's horizontal distance to the target's box (its half extent along the line); boxLow and
     * boxHigh: the target's box above the body's feet; bodyV: the body's stored velocity toward it;
     * targetV: its last displacement per tick away from the body; intent: the displacement it heads
     * for (intent()); mobReach: the gap it hits the body within (0: it does not melee); ticksToFull:
     * the weapon's cooldown left; reach: the body's entity interaction range.
     */
    record Scene(double gap, double half, double boxLow, double boxHigh, double bodyV, double targetV, double intent,
                 boolean targetGround, double mobReach, double ticksToFull, double reach) {}

    /** hop: jump now with it; else step this tick. strikeIn: ticks to the planned strike (-1: none). */
    record Plan(Hop hop, Step step, int strikeIn) {}

    /** The body's displacement toward the target after each tick: wait ground ticks of `during`, then the hop. */
    static double[] body(double v0, Step during, int wait, Hop hop) { return body(v0, during, true, wait, hop); }

    private static double[] body(double v0, Step during, boolean sprintOk, int wait, Hop hop) {
        double[] out = new double[wait + HOP];
        double v = v0, at = 0;
        int f = during == Step.RETREAT ? -1 : during == Step.APPROACH ? 1 : 0;
        boolean run = f > 0 && sprintOk;
        for (int t = 0; t < wait; t++) {  // ground: accelerate, move, ground drag
            v += f * (run ? SPRINT : WALK) * INPUT;
            at += v; v *= GROUND_DRAG; out[t] = at;
        }
        // the jump tick still moves with ground physics (its flag is read before the move)
        v += hop.forward * (hop.sprint ? SPRINT : WALK) * INPUT + (hop.sprint ? SPRINT_JUMP : 0);
        at += v; v *= GROUND_DRAG; out[wait] = at;
        for (int t = wait + 1; t < wait + HOP; t++) {
            v += hop.forward * (hop.sprint ? AIR_SPRINT : AIR_WALK) * INPUT;
            at += v; v *= AIR_DRAG; out[t] = at;
        }
        return out;
    }

    /** The body's feet height after each tick: flat for `wait` ticks, then a hop (apex 1.25 at tick 6, down at 12). */
    static double[] feet(int wait) {
        double[] out = new double[wait + HOP];
        double y = 0, vy = JUMP;
        for (int t = wait; t < wait + HOP; t++) {
            y += vy; vy = (vy - GRAVITY) * FALL_DRAG;
            out[t] = Math.max(0, y);
        }
        return out;
    }

    /**
     * The target's displacement away from the body after each of n ticks. On the ground its walk
     * relaxes from the last one seen to the one it heads for at the ground drag; in the air (knocked
     * back) it coasts at the air drag.
     */
    static double[] target(double seen, double intent, boolean ground, int n) {
        double[] out = new double[n];
        double d = seen, at = 0;
        for (int t = 0; t < n; t++) {
            d = ground ? intent + GROUND_DRAG * (d - intent) : d * AIR_DRAG;
            at += d; out[t] = at;
        }
        return out;
    }

    /**
     * The walk a target heads for, away from the body per tick. A melee mob hunting the body walks in
     * at its speed (MoveControl thrust f * f on the ground: speed^2 / 0.454); a skeleton holding its bow
     * backs off inside 7.5 blocks; anything else (an animal, a mob after something else) keeps on as seen.
     */
    static double intent(boolean chasing, boolean archer, double distance, double seen, double speed) {
        if (!chasing) return seen;
        if (archer) return distance < ARCHER_CLOSE ? ARCHER_BACK : seen;
        return -(speed * speed / (1 - GROUND_DRAG));
    }

    /** The centre gap a mob of this half width hits within along (ux, uz): its box inflated MOB_REACH on x and z. */
    static double mobReach(double half, double ux, double uz) {
        return (BODY_HALF + MOB_REACH + half) / Math.max(Math.abs(ux), Math.abs(uz));
    }

    private static double eyeGap(Scene s, double gap, double feet) {
        double h = Math.max(0, gap - s.half()), eye = EYE + feet;
        double v = Math.max(0, Math.max(s.boxLow() - eye, eye - s.boxHigh()));
        return Math.hypot(h, v);
    }

    private static boolean reaches(Scene s, double gap, double feet) { return eyeGap(s, gap, feet) < s.reach() - REACH_MARGIN; }

    private static boolean threat(Scene s) { return s.mobReach() > 0; }

    /** {strike tick, safety margin}, or null when this course strikes nothing safely. */
    private static double[] course(Scene s, Step during, boolean sprintOk, int wait, Hop hop) {
        double[] me = body(s.bodyV(), during, sprintOk, wait, hop), up = feet(wait);
        double[] it = target(s.targetV(), s.intent(), s.targetGround(), wait + HOP);
        double margin = Double.POSITIVE_INFINITY;
        for (int j = 1; j <= wait + LAST + 1; j++) {
            double gap = s.gap() + it[j - 1] - me[j - 1];
            if (threat(s)) {
                margin = Math.min(margin, gap - s.mobReach());
                if (gap <= s.mobReach() + SAFE) return null;  // it reaches the body first
            }
            int i = j - wait - 1;  // the strike tick j-1 as a hop tick
            if (i >= FIRST && i <= LAST && j - 1 >= s.ticksToFull()) {
                double before = s.gap() + it[j - 2] - me[j - 2];
                if (reaches(s, before, up[j - 2]) && reaches(s, gap, up[j - 1])) {
                    // the next tick is checked safe already (it is j); the strike is j - 1
                    return new double[] {j - 1, margin};
                }
            }
        }
        return null;
    }

    /** Earlier strike; then, against a threat, the wider margin; then the earlier option. */
    private static boolean better(double[] a, int aOrder, double[] best, int bestOrder, boolean threat) {
        if (best == null) return true;
        if (a[0] != best[0]) return a[0] < best[0];
        if (threat && a[1] != best[1]) return a[1] > best[1];
        return aOrder < bestOrder;
    }

    static Plan plan(Scene s, boolean forwardOk, boolean backOk, boolean sprintOk) {
        if (s.gap() - s.half() > s.reach() + FAR) return new Plan(null, Step.APPROACH, -1);
        java.util.List<Hop> hops = new java.util.ArrayList<>();
        for (var h : Hop.values())
            if (h.forward == 0 || h.forward < 0 && backOk || h.forward > 0 && forwardOk && (!h.sprint || sprintOk)) hops.add(h);
        double[] best = null; Hop bestHop = null; int bestOrder = 0;
        for (var h : hops) {
            double[] c = course(s, Step.HOLD, sprintOk, 0, h);
            if (c != null && better(c, h.ordinal(), best, bestOrder, threat(s))) { best = c; bestHop = h; bestOrder = h.ordinal(); }
        }
        if (bestHop != null) return new Plan(bestHop, null, (int) best[0]);
        // no hop lands now: the ground step after which one lands once the cooldown is back
        int wait = Math.max(1, (int) Math.ceil(s.ticksToFull()) - (FIRST + 1));
        // a tie on the strike goes to the step with the least motion (the forecast errs more the more it moves)
        Step bestStep = null;
        for (var step : Step.values()) {
            if (step == Step.STRIKE || step == Step.RETREAT && !backOk || step == Step.APPROACH && !forwardOk) continue;
            for (var h : hops) {
                double[] c = course(s, step, sprintOk, wait, h);
                if (c == null) continue;
                boolean first = bestStep == null || c[0] < best[0]
                        || c[0] == best[0] && step == bestStep && better(c, h.ordinal(), best, bestOrder, threat(s));
                if (first) { best = c; bestStep = step; bestOrder = h.ordinal(); }
            }
        }
        if (bestStep != null) return new Plan(null, bestStep, (int) best[0]);
        return new Plan(null, fallback(s, wait, backOk), -1);
    }

    /** Nothing lands: back off from a threat about to reach the body (or strike it now, penned); walk in to one out of reach. */
    private static Step fallback(Scene s, int wait, boolean backOk) {
        double[] it = target(s.targetV(), s.intent(), s.targetGround(), wait + LAST + 1);
        double[] me = body(s.bodyV(), Step.HOLD, wait, Hop.STAND), up = feet(wait);
        for (int j = 0; j < wait + LAST + 1; j++) {
            double gap = s.gap() + it[j] - me[j];
            if (threat(s) && gap <= s.mobReach() + SAFE) {
                boolean ready = s.ticksToFull() <= 0 && reaches(s, s.gap(), 0);
                return ready ? Step.STRIKE : backOk ? Step.RETREAT : Step.HOLD;
            }
        }
        int mid = wait + (FIRST + LAST) / 2;
        return reaches(s, s.gap() + it[mid] - me[mid], up[mid]) ? Step.HOLD : Step.APPROACH;
    }
}
