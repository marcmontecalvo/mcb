package com.vulpine.mcb.body;

import com.vulpine.mcb.body.CritForecast.Hop;
import com.vulpine.mcb.body.CritForecast.Scene;
import com.vulpine.mcb.body.CritForecast.Step;

/** Numbers from docs/MOB_MOVEMENT_26.3.md (sections 1, 2 and 4). */
public final class CritForecastTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }
    private static boolean near(double a, double b, double eps) { return Math.abs(a - b) <= eps; }

    static final double ZOMBIE = -0.1165;  // b/tick closing (speed 0.23)
    static final double SPRINTING = 0.1532, WALKING = 0.1179;  // stored velocity after a steady ground tick

    /** A 0.6-wide, 1.95-tall mob level with the body, `gap` centre to centre. */
    private static Scene scene(double gap, double bodyV, double targetV, double intent, double mobReach, double toFull) {
        return new Scene(gap, 0.3, 0, 1.95, bodyV, targetV, intent, true, mobReach, toFull, 3.0);
    }

    public static void main(String[] args) {
        // --- the body's hop, against the worked jump of MOB_MOVEMENT section 1
        double[] sprint = CritForecast.body(SPRINTING, Step.HOLD, 0, Hop.SPRINT);
        check(near(sprint[0], 0.4806, 1e-3), "sprint-jump tick 1: " + sprint[0]);
        check(near(sprint[11], 3.63, 0.03), "sprint-jump travels 3.63: " + sprint[11]);
        double[] back = CritForecast.body(-WALKING, Step.HOLD, 0, Hop.BACK);
        check(near(back[11], -2.04, 0.03), "walk-back jump travels 2.04 back: " + back[11]);
        check(CritForecast.body(0, Step.HOLD, 0, Hop.STAND)[11] == 0, "a standing jump stays put");
        double[] feet = CritForecast.feet(0);
        check(near(feet[5], 1.2522, 1e-3) && feet[6] < feet[5], "apex at tick 6, falling from tick 7");
        check(feet[11] == 0 && feet[0] == 0.42, "lands on tick 12");
        double[] waited = CritForecast.feet(3);
        check(waited[2] == 0 && waited[3] == 0.42, "a hop after a wait starts after it");

        // --- the target relaxes to the walk it heads for (a zombie from rest: ~90% in 4 ticks)
        double[] zombie = CritForecast.target(0, ZOMBIE, true, 8);
        check(zombie[3] - zombie[2] < 0.9 * ZOMBIE, "zombie at speed by tick 4");
        double[] knocked = CritForecast.target(0.4, ZOMBIE, false, 3);
        // in the air it only coasts (drag 0.91, no thrust worth counting)
        check(near(knocked[0], 0.364, 1e-3) && knocked[2] - knocked[1] < knocked[0], "a knocked-back mob slows");

        // --- intent: a chaser walks in at its speed; an archer backs off inside 7.5; else as seen
        check(near(CritForecast.intent(true, false, 5, 0, 0.23), ZOMBIE, 1e-3), "zombie closes 0.1165");
        check(near(CritForecast.intent(true, true, 6, 0, 0.25), CritForecast.ARCHER_BACK, 1e-9), "skeleton backs off");
        check(CritForecast.intent(true, true, 10, 0.02, 0.25) == 0.02, "skeleton past 7.5 as seen");
        check(CritForecast.intent(false, false, 4, 0.05, 0.25) == 0.05, "a pig as seen");

        // --- a mob's own reach: its box inflated 0.828 on x and z must touch the body's
        check(near(CritForecast.mobReach(0.3, 1, 0), 1.428, 1e-3), "axis reach 1.43");
        check(near(CritForecast.mobReach(0.3, Math.sqrt(.5), Math.sqrt(.5)), 2.02, 1e-2), "diagonal reach 2.02");

        // --- a zombie walking in from 3.6: jump in place; walking in would meet its arms
        var stand = CritForecast.plan(scene(3.6, 0, ZOMBIE, ZOMBIE, 1.43, 0), true, true, true);
        check(stand.hop() == Hop.STAND && stand.strikeIn() >= 7 && stand.strikeIn() <= 10, "zombie: stand jump " + stand);

        // --- a zombie already at 2.4: jump backwards, so the strike lands before it reaches
        var away = CritForecast.plan(scene(2.4, 0, ZOMBIE, ZOMBIE, 1.43, 0), true, true, true);
        check(away.hop() == Hop.BACK, "zombie close: back jump " + away);
        var penned = CritForecast.plan(scene(2.4, 0, ZOMBIE, ZOMBIE, 1.43, 0), true, false, true);
        check(penned.hop() != Hop.BACK && penned.step() != Step.RETREAT, "no back step: never backward " + penned);
        // nothing lands safely and it is about to hit: a grounded strike now, its knockback buys room
        check(penned.hop() == null && penned.step() == Step.STRIKE, "penned: strike now " + penned);

        // --- a skeleton backing off at 4.6: sprint-jump at it; in place it stays out of reach
        var chase = CritForecast.plan(scene(4.6, SPRINTING, CritForecast.ARCHER_BACK, CritForecast.ARCHER_BACK, 0, 0),
                true, true, true);
        check(chase.hop() == Hop.SPRINT, "skeleton: sprint jump " + chase);
        var walkOnly = CritForecast.plan(scene(4.6, SPRINTING, CritForecast.ARCHER_BACK, CritForecast.ARCHER_BACK, 0, 0),
                true, true, false);
        check(walkOnly.hop() != Hop.SPRINT, "no sprint (hungry): no sprint jump " + walkOnly);
        var blocked = CritForecast.plan(scene(4.6, 0, CritForecast.ARCHER_BACK, CritForecast.ARCHER_BACK, 0, 0),
                false, true, true);
        check(blocked.hop() == null || blocked.hop() == Hop.STAND || blocked.hop() == Hop.BACK, "no forward step " + blocked);

        // --- far off: walk in
        var far = CritForecast.plan(scene(9, 0, 0, 0, 1.43, 0), true, true, true);
        check(far.hop() == null && far.step() == Step.APPROACH, "far: approach " + far);

        // --- the cooldown not back yet: hold while the zombie walks into the window
        var wait = CritForecast.plan(scene(4.5, 0, ZOMBIE, ZOMBIE, 1.43, 12), true, true, true);
        check(wait.hop() == null && wait.step() == Step.HOLD, "cooldown: hold " + wait);
        check(wait.strikeIn() >= 12, "the planned strike waits for the cooldown " + wait);

        // --- a zombie at its arms' length with the cooldown down: back off
        var hurt = CritForecast.plan(scene(1.6, 0, ZOMBIE, ZOMBIE, 1.43, 12), true, true, true);
        check(hurt.hop() == null && hurt.step() == Step.RETREAT, "too close: retreat " + hurt);

        // --- a still pig in reach: jump in place (no threat, the least travel)
        var pig = CritForecast.plan(scene(2.6, 0, 0, 0, 0, 0), true, true, true);
        check(pig.hop() == Hop.STAND, "still pig: stand jump " + pig);

        // --- a mob above the body: the eye's height counts (a pig on a 2-block ledge, 2.2 out)
        var ledge = CritForecast.plan(new Scene(2.6, 0.45, 2, 2.9, 0, 0, 0, true, 0, 0, 3.0), true, true, true);
        check(ledge.hop() != null, "a mob above is reached from the jump " + ledge);

        System.out.println("CritForecastTest ok");
    }
}
