package com.vulpine.mcb.body;

/**
 * Ballistics of a thrown splash or lingering potion (pure, no Minecraft types; checked by ThrowLogicTest).
 *
 * The physics of this game version, read from the 26.3 bytecode:
 * <ul>
 * <li>ThrowablePotionItem.use: Projectile.spawnProjectileFromRotation(.., owner, -20, 0.5, 1): a pitch offset of
 *     -20 degrees, power 0.5, inaccuracy 1 (a triangular noise of up to 0.017 per axis on the unit direction).</li>
 * <li>Projectile.shootFromRotation: the direction is (-sin(yaw) cos(pitch), -sin(pitch + offset),
 *     cos(yaw) cos(pitch)), normalised by shoot(), times the power. The owner's own velocity is added on top.</li>
 * <li>The potion starts at the owner's eye height minus 0.1 (ThrowableItemProjectile constructor).</li>
 * <li>ThrowableProjectile.tick: gravity first (AbstractThrownPotion.getDefaultGravity = 0.05), then drag
 *     (getAirDrag = 0.99; 0.8 in water), then it moves by the velocity, shattering on the first block or entity.</li>
 * </ul>
 * Splash range is 4 blocks (SPLASH_RANGE), so landing within half a block of the target is ample.
 */
final class ThrowLogic {
    private ThrowLogic() {}

    static final double SPEED = 0.5, GRAVITY = 0.05, DRAG = 0.99;
    /** Added to the pitch by shootFromRotation (negative lifts the throw above where the body looks). */
    static final double PITCH_OFFSET = -20;
    /** The potion starts this far below the eye. */
    static final double SPAWN_BELOW_EYE = 0.10000000149011612;
    /** Thrown at the feet: straight down shatters on the ground under the body. */
    static final float SELF_PITCH = 90f;

    static final int MAX_TICKS = 100;
    /** How close to the target counts as a hit; the splash itself reaches four blocks. */
    static final double TOLERANCE = 0.5;
    private static final double SCAN_STEP = 0.25, REFINED = 1e-4;
    private static final int PREDICTION_ROUNDS = 4;

    /** The aim that lands the potion on the target and the ticks it flies (the throw itself is tick 0). */
    record Solution(float yaw, float pitch, int flightTicks, double landX, double landY, double landZ) {}

    /** Where a flight crosses the target height on its way down: x, z and the (fractional) tick. */
    private record Landing(double x, double z, double ticks) {}

    /** One aiming problem: the eye, the heading to the target, its horizontal distance and its height. */
    private record Aim(double ex, double ey, double ez, float yaw, double distance, double ty) {
        /** How far past the target a throw at this pitch lands (negative: short); NaN when it never gets down to it. */
        double error(double pitch) {
            var land = fly(ex, ey, ez, yaw, pitch, ty);
            return land == null ? Double.NaN : Math.hypot(land.x() - ex, land.z() - ez) - distance;
        }

        Solution at(double pitch) {
            float aimed = (float) pitch;  // the game aims with floats
            var land = fly(ex, ey, ez, yaw, aimed, ty);
            return land == null ? null
                    : new Solution(yaw, aimed, (int) Math.ceil(land.ticks() - 1e-9), land.x(), ty, land.z());
        }

        double miss(Solution s) { return Math.abs(Math.hypot(s.landX() - ex, s.landZ() - ez) - distance); }
    }

    /** The yaw that faces horizontal offset (dx, dz): +z is yaw 0, -x is yaw 90 (the body's aim maths). */
    static float yawTo(double dx, double dz) { return (float) Math.toDegrees(Math.atan2(-dx, dz)); }

    /**
     * The aim from eye (ex, ey, ez) that lands a potion on (tx, ty, tz): the lowest arc that gets there, or null
     * when no pitch does (too far, or too high). The potion is judged where it falls through height ty, so a target
     * higher than the top of every arc is out of reach.
     */
    static Solution solve(double ex, double ey, double ez, double tx, double ty, double tz) {
        double dx = tx - ex, dz = tz - ez, distance = Math.hypot(dx, dz);
        var aim = new Aim(ex, ey, ez, distance < 1e-6 ? 0f : yawTo(dx, dz), distance, ty);
        // flattest first: from straight down up to straight up, taking the first pitch that brackets the target
        double lastPitch = Double.NaN, lastError = Double.NaN;
        for (double pitch = 90; pitch >= -90 - 1e-9; pitch -= SCAN_STEP) {
            double error = aim.error(pitch);
            if (Double.isNaN(error)) { lastPitch = Double.NaN; continue; }
            if (Math.abs(error) <= REFINED) return accept(aim, aim.at(pitch));
            if (!Double.isNaN(lastPitch) && Math.signum(lastError) != Math.signum(error)) {
                var refined = accept(aim, refine(aim, lastPitch, lastError, pitch));
                if (refined != null) return refined;
            }
            lastPitch = pitch;
            lastError = error;
        }
        return null;
    }

    /**
     * As solve for a target moving at (vx, vz) blocks a tick: led by the flight time, found by solving again at
     * where the target will be. A target the lead carries out of reach is out of reach.
     */
    static Solution solveMoving(double ex, double ey, double ez, double tx, double ty, double tz, double vx, double vz) {
        var current = solve(ex, ey, ez, tx, ty, tz);
        if (current == null || vx == 0 && vz == 0) return current;
        for (int round = 0; round < PREDICTION_ROUNDS; round++) {
            var led = solve(ex, ey, ez, tx + vx * current.flightTicks(), ty, tz + vz * current.flightTicks());
            if (led == null) return null;
            boolean settled = led.flightTicks() == current.flightTicks();
            current = led;
            if (settled) break;
        }
        return current;
    }

    /** Bisects pitches a and b (the error changes sign between them) to the aim that lands on the target. */
    private static Solution refine(Aim aim, double a, double errorA, double b) {
        for (int i = 0; i < 60 && Math.abs(a - b) > REFINED; i++) {
            double mid = (a + b) / 2, errorMid = aim.error(mid);
            if (Double.isNaN(errorMid)) break;
            if (Math.signum(errorMid) == Math.signum(errorA)) { a = mid; errorA = errorMid; } else b = mid;
        }
        var fromA = aim.at(a);
        var fromB = aim.at(b);
        if (fromA == null || fromB == null) return fromA == null ? fromB : fromA;
        return aim.miss(fromA) <= aim.miss(fromB) ? fromA : fromB;
    }

    private static Solution accept(Aim aim, Solution s) { return s != null && aim.miss(s) <= TOLERANCE ? s : null; }

    /** A potion that breaks nearer the thrower than this (a 4-block splash and a margin) splashes it too. */
    static final double BREAKS_NEAR = 4.75;

    /**
     * Where a potion thrown from eye (ex, ey, ez) with this yaw and pitch is at the start and after each of
     * {@code ticks} ticks, {x, y, z}: the throw op clips each step against the blocks.
     */
    static double[][] path(double ex, double ey, double ez, double yaw, double pitch, int ticks) {
        double rad = Math.PI / 180;
        double dx = -Math.sin(yaw * rad) * Math.cos(pitch * rad);
        double dy = -Math.sin((pitch + PITCH_OFFSET) * rad);
        double dz = Math.cos(yaw * rad) * Math.cos(pitch * rad);
        double length = Math.sqrt(dx * dx + dy * dy + dz * dz);
        double vx = dx / length * SPEED, vy = dy / length * SPEED, vz = dz / length * SPEED;
        double x = ex, y = ey - SPAWN_BELOW_EYE, z = ez;
        var out = new double[Math.max(0, ticks) + 1][];
        out[0] = new double[] {x, y, z};
        for (int tick = 1; tick <= ticks; tick++) {
            vy -= GRAVITY;
            vx *= DRAG; vy *= DRAG; vz *= DRAG;
            x += vx; y += vy; z += vz;
            out[tick] = new double[] {x, y, z};
        }
        return out;
    }

    /** The flight from the eye with this aim, to where it crosses height ty descending; null when it never does. */
    private static Landing fly(double ex, double ey, double ez, double yaw, double pitch, double ty) {
        double rad = Math.PI / 180;
        double dx = -Math.sin(yaw * rad) * Math.cos(pitch * rad);
        double dy = -Math.sin((pitch + PITCH_OFFSET) * rad);
        double dz = Math.cos(yaw * rad) * Math.cos(pitch * rad);
        double length = Math.sqrt(dx * dx + dy * dy + dz * dz);
        double vx = dx / length * SPEED, vy = dy / length * SPEED, vz = dz / length * SPEED;
        double x = ex, y = ey - SPAWN_BELOW_EYE, z = ez;
        for (int tick = 1; tick <= MAX_TICKS; tick++) {
            vy -= GRAVITY;
            vx *= DRAG; vy *= DRAG; vz *= DRAG;
            double nx = x + vx, ny = y + vy, nz = z + vz;
            if (vy < 0 && y > ty && ny <= ty) {
                double f = (y - ty) / (y - ny);
                return new Landing(x + (nx - x) * f, z + (nz - z) * f, tick - 1 + f);
            }
            if (y <= ty && vy < 0) return null;  // already below the target and falling: it never rises to it
            x = nx; y = ny; z = nz;
        }
        return null;
    }
}
