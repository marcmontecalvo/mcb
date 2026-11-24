package com.vulpine.mcb.body;

/**
 * The thrown-potion solver against an independent copy of the game's physics. Constants confirmed in the 26.3
 * bytecode (ThrowablePotionItem.use: spawnProjectileFromRotation(.., -20, 0.5, 1); Projectile.shootFromRotation;
 * AbstractThrownPotion.getDefaultGravity 0.05; ThrowableProjectile.tick: gravity, then drag 0.99, then move).
 */
public final class ThrowLogicTest {
    private static final double EYE = 65.62;  // a standing body on feet y 64

    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    /** Where a potion thrown with this yaw and pitch crosses height ty on its way down: {x, y, z, ticks}, or null. */
    private static double[] fly(double ex, double ey, double ez, float yaw, float pitch, double ty) {
        double rad = Math.PI / 180;
        double dx = -Math.sin(yaw * rad) * Math.cos(pitch * rad);
        double dy = -Math.sin((pitch - 20) * rad);
        double dz = Math.cos(yaw * rad) * Math.cos(pitch * rad);
        double len = Math.sqrt(dx * dx + dy * dy + dz * dz);
        double vx = dx / len * 0.5, vy = dy / len * 0.5, vz = dz / len * 0.5;
        double x = ex, y = ey - 0.10000000149011612, z = ez;
        for (int t = 1; t <= 200; t++) {
            vy -= 0.05;
            vx *= 0.99; vy *= 0.99; vz *= 0.99;
            double nx = x + vx, ny = y + vy, nz = z + vz;
            if (vy < 0 && y > ty && ny <= ty) {
                double f = (y - ty) / (y - ny);
                return new double[] {x + (nx - x) * f, ty, z + (nz - z) * f, t};
            }
            x = nx; y = ny; z = nz;
        }
        return null;
    }

    private static double miss(double[] land, double tx, double tz) {
        return land == null ? Double.POSITIVE_INFINITY : Math.hypot(land[0] - tx, land[2] - tz);
    }

    /** The farthest flat distance (to a target at ty) any pitch reaches. */
    private static double farthest(double ty) {
        double best = 0;
        for (float p = -90; p <= 90; p += 0.1f) {
            var land = fly(0, EYE, 0, 0f, p, ty);
            if (land != null) best = Math.max(best, Math.hypot(land[0], land[2]));
        }
        return best;
    }

    private static void reaches(double dx, double dy, double dz, String label) {
        var s = ThrowLogic.solve(0, EYE, 0, dx, EYE - 1.62 + dy, dz);
        check(s != null, label + ": reachable");
        var land = fly(0, EYE, 0, s.yaw(), s.pitch(), EYE - 1.62 + dy);
        check(miss(land, dx, dz) <= 0.5, label + ": lands " + miss(land, dx, dz) + " from the target");
        check(s.flightTicks() == (int) land[3], label + ": predicted flight " + s.flightTicks() + " vs " + land[3]);
        check(s.flightTicks() > 0 && s.flightTicks() < 60, label + ": flight ticks " + s.flightTicks());
    }

    public static void main(String[] args) {
        // the game's constants
        check(ThrowLogic.SPEED == 0.5 && ThrowLogic.PITCH_OFFSET == -20 && ThrowLogic.GRAVITY == 0.05
                && ThrowLogic.DRAG == 0.99, "projectile constants of this version");
        check(ThrowLogic.SELF_PITCH == 90f, "self: straight down");

        // flat targets at several distances and headings, aimed at the target's feet
        reaches(0, 0, 3, "3 ahead");
        reaches(0, 0, 5, "5 ahead");
        reaches(-4, 0, -3, "5 behind-left");
        reaches(3, 0, 0, "3 east");
        reaches(2, 0, 1, "2 and 1");
        reaches(0, 0, 0.8, "almost at the feet");
        // above and below
        reaches(0, 1, 3, "1 up at 3");
        reaches(0, 2, 2, "2 up at 2");
        reaches(0, -4, 5, "4 down at 5");
        reaches(0, -8, 2, "8 down at 2");
        reaches(0, -1, 0, "straight below");

        // range: the real reach of a 0.5-speed lob is short; beyond it there is no angle
        double reach = farthest(EYE - 1.62);
        check(reach > 4 && reach < 12, "flat reach " + reach);
        check(ThrowLogic.solve(0, EYE, 0, 0, EYE - 1.62, reach - 0.3) != null, "just inside the reach");
        check(ThrowLogic.solve(0, EYE, 0, 0, EYE - 1.62, reach + 1.0) == null, "just outside the reach");
        check(ThrowLogic.solve(0, EYE, 0, 0, EYE - 1.62, 10) == null, "10 blocks is out of range");
        check(ThrowLogic.solve(0, EYE, 0, 0, EYE - 1.62, 40) == null, "40 blocks is out of range");
        check(ThrowLogic.solve(0, EYE, 0, 0, EYE + 20, 2) == null, "20 blocks up is out of range");

        // the lower arc: no slower solution exists that lands as close
        var flat = ThrowLogic.solve(0, EYE, 0, 0, EYE - 1.62, 4);
        check(flat != null, "4 ahead");
        for (float p = -90; p <= 90; p += 0.05f) {
            var land = fly(0, EYE, 0, 0f, p, EYE - 1.62);
            if (land != null && miss(land, 0, 4) < 0.15) check(land[3] >= flat.flightTicks() - 1, "a quicker arc at pitch " + p);
        }
        var up = ThrowLogic.solve(0, EYE, 0, 0, EYE - 1.62, 2);
        check(up.pitch() > flat.pitch() && flat.pitch() > ThrowLogic.solve(0, EYE, 0, 0, EYE - 1.62, 5).pitch(),
                "nearer targets take a flatter throw");

        // yaw: Minecraft's south is +z (yaw 0), west +x (yaw 90): the same maths as the body's aim
        check(Math.abs(ThrowLogic.solve(0, EYE, 0, 0, EYE - 1.62, 3).yaw()) < 0.5, "+z is yaw 0");
        check(Math.abs(ThrowLogic.solve(0, EYE, 0, -3, EYE - 1.62, 0).yaw() - 90) < 0.5, "-x is yaw 90");
        check(Math.abs(Math.abs(ThrowLogic.solve(0, EYE, 0, 0, EYE - 1.62, -3).yaw()) - 180) < 0.5, "-z is yaw 180");
        check(Math.abs(ThrowLogic.solve(0, EYE, 0, 3, EYE - 1.62, 0).yaw() + 90) < 0.5, "+x is yaw -90");

        // a moving target: lead it by the flight time
        var walker = ThrowLogic.solveMoving(0, EYE, 0, 2, EYE - 1.62, 3, 0.1, 0);
        check(walker != null, "a walking target in reach");
        double lx = 2 + 0.1 * walker.flightTicks();
        var land = fly(0, EYE, 0, walker.yaw(), walker.pitch(), EYE - 1.62);
        check(miss(land, lx, 3) <= 0.6, "lands where the walker will be: " + miss(land, lx, 3));
        check(walker.yaw() < -34.5, "aimed ahead of it, toward +x (a still target at (2, 3) is yaw -33.7)");
        var still = ThrowLogic.solveMoving(0, EYE, 0, 2, EYE - 1.62, 3, 0, 0);
        var plain = ThrowLogic.solve(0, EYE, 0, 2, EYE - 1.62, 3);
        check(still.yaw() == plain.yaw() && still.pitch() == plain.pitch(), "no velocity is the plain solve");
        // a target running out of reach is unreachable even though it is in reach now
        check(ThrowLogic.solveMoving(0, EYE, 0, 0, EYE - 1.62, 4, 0, 1.0) == null, "a runner outpaces the lob");

        // self: pitch 90 drops it at the feet
        var drop = fly(0, EYE, 0, 0f, ThrowLogic.SELF_PITCH, EYE - 1.62);
        check(drop != null && miss(drop, 0, 0) < 0.5, "self: lands at the feet");

        System.out.println("ThrowLogicTest ok");
    }
}
