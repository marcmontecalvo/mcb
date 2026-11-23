package com.vulpine.mcb.body;

import com.vulpine.mcb.body.LanceLogic.Move;
import com.vulpine.mcb.body.LanceLogic.Use;
import com.vulpine.mcb.body.LanceLogic.View;

/** Spear charge decisions; game numbers from docs/COMBAT_MECHANICS_26.3.md section 4 (iron: delay 12, window 225). */
public final class LanceLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    static final double ZOMBIE = 0.1165, SKELETON = -0.0688;  // closing per tick
    static final double SPRINTING = 0.1532;  // stored velocity after a steady sprint tick
    static final int DELAY = 12, WINDOW = 225, READY = DELAY;

    /** A ground view of a mob, the spear held in use for `ticks` (-1: not in use). */
    private static View view(double eye, double bodyV, double closing, int ticks, boolean melee) {
        return new View(eye, eye + 0.3, bodyV, closing, true, ticks >= 0, Math.max(0, ticks), DELAY, WINDOW,
                melee, false, false, false, false, false, false);
    }

    private static View creeper(double eye, double feet, boolean lit, boolean swollen, boolean fleeing, boolean hit) {
        return new View(eye, feet, 0, 0.1377, true, true, READY, DELAY, WINDOW, true, true, lit, swollen, fleeing, false, hit);
    }

    public static void main(String[] args) {
        // --- the charge's reach and speed are the spear's own
        check(LanceLogic.NEAR == CombatLogic.JAB_MIN && LanceLogic.FAR == CombatLogic.JAB_MAX, "AttackRange 2.0..4.5");
        check(Math.abs(LanceLogic.MIN_REL - 0.23) < 1e-9, "4.6 b/s relative is 0.23 b/tick");
        check(LanceLogic.LOW > LanceLogic.NEAR && LanceLogic.HIGH < LanceLogic.FAR, "the band sits inside the reach");

        // --- ready: a standing sprint-jump at a zombie walking in lands 3.16 off at 8.9 b/s relative
        var a = LanceLogic.decide(view(3.6, 0, ZOMBIE, READY, false));
        check(a.move() == Move.BURST && a.use() == Use.KEEP, "zombie at 3.6, ready: burst " + a);
        // one tick short of the delay is ready: the damage reads this tick's move next tick
        check(LanceLogic.decide(view(3.6, 0, ZOMBIE, DELAY - 1, false)).move() == Move.BURST, "delay-1 is ready");
        // a skeleton backing off: a ground sprint is 4.2 b/s relative (no damage), the burst lands
        a = LanceLogic.decide(view(3.6, SPRINTING, SKELETON, READY, false));
        check(a.move() == Move.BURST, "skeleton at 3.6: burst " + a);
        check(LanceLogic.decide(view(5.0, SPRINTING, SKELETON, READY, false)).move() == Move.CHARGE,
                "skeleton at 5.0: run in");
        // the burst would overshoot inside the band, the plain sprint lands in it
        check(LanceLogic.decide(view(2.85, SPRINTING, ZOMBIE, READY, false)).move() == Move.CHARGE,
                "zombie at 2.85 sprinting: the ground tick lands");
        // too near to land a charge tick, nothing to swap to: open the gap holding the charge
        a = LanceLogic.decide(view(2.2, 0, ZOMBIE, READY, false));
        check(a.move() == Move.BACK && !a.inMelee(), "zombie at 2.2, spear alone: back " + a);
        // in the air the run is kept on (the burst was the jump)
        var air = new View(3.0, 3.3, 0.3, ZOMBIE, false, true, READY, DELAY, WINDOW, false, false, false, false, false, false, false);
        check(LanceLogic.decide(air).move() == Move.CHARGE, "airborne: keep running");

        // --- the use: started when not held, restarted before the damage window closes
        a = LanceLogic.decide(view(10, 0, ZOMBIE, -1, false));
        check(a.use() == Use.START, "not in use: start " + a);
        check(LanceLogic.decide(view(10, 0, ZOMBIE, DELAY + WINDOW - 1, false)).use() == Use.RESTART, "window ending: restart");
        check(LanceLogic.decide(view(10, 0, ZOMBIE, DELAY + 100, false)).use() == Use.KEEP, "inside the window: keep");

        // --- not ready: arrive at the band as the delay ends, not before
        a = LanceLogic.decide(view(7.6, 0, ZOMBIE, 0, false));
        check(a.move() == Move.HOLD, "zombie at 7.6, delay to run: hold " + a);
        check(LanceLogic.decide(view(10, 0, ZOMBIE, 0, false)).move() == Move.CHARGE, "zombie at 10: run in during the delay");
        check(LanceLogic.decide(view(3.0, 0, ZOMBIE, 0, false)).move() == Move.BACK, "it would walk inside the band first: back");

        // --- a melee weapon takes over inside the charge's reach and hands back only far out
        a = LanceLogic.decide(view(2.2, 0, ZOMBIE, READY, true));
        check(a.move() == Move.MELEE && a.use() == Use.STOP && a.inMelee(), "zombie at 2.2: swap to the sword " + a);
        var held = new View(4.5, 4.8, 0, ZOMBIE, true, false, 0, DELAY, WINDOW, true, false, false, false, false, true, false);
        check(LanceLogic.decide(held).move() == Move.MELEE, "knocked back to 4.5: the sword stays");
        var far = new View(6.5, 6.8, 0, ZOMBIE, true, false, 0, DELAY, WINDOW, true, false, false, false, false, true, false);
        a = LanceLogic.decide(far);
        check(!a.inMelee() && a.use() == Use.START && a.move() == Move.HOLD, "out at 6.5: the spear again " + a);
        a = LanceLogic.decide(view(3.0, 0, ZOMBIE, -1, true));
        check(a.move() == Move.MELEE, "too near to wait out the delay: the sword " + a);

        // --- a creeper: never met in melee; after a hit, lit, or near its fuse, outrun past 7.5
        a = LanceLogic.decide(creeper(3.5, 3.8, false, false, false, true));
        check(a.move() == Move.FLEE && a.fleeing(), "a hit creeper is fled " + a);
        check(LanceLogic.decide(creeper(5, 5.3, false, true, true, false)).move() == Move.FLEE, "fled until past 7.5");
        check(LanceLogic.decide(creeper(8, 8.3, false, true, true, false)).move() == Move.FLEE, "and its swell gone");
        a = LanceLogic.decide(creeper(8, 8.3, false, false, true, false));
        check(!a.fleeing() && a.move() == Move.CHARGE, "past 7.5, swell gone: charge again " + a);
        check(LanceLogic.decide(creeper(4, 4.3, true, true, false, false)).move() == Move.FLEE, "lit: flee");
        a = LanceLogic.decide(creeper(2.2, 3.2, false, false, false, false));
        check(a.move() == Move.FLEE && !a.inMelee(), "inside its fuse margin: flee, never the sword " + a);
        // walking in, a ground sprint is fast enough: no jump that would carry the body onto its fuse
        a = LanceLogic.decide(creeper(3.6, 4.2, false, false, false, false));
        check(a.move() == Move.CHARGE, "creeper at 3.6, ready: a ground charge " + a);
        // a still creeper close by: the run lands with its feet past the fuse margin
        var still = new View(3.0, 3.6, 0.1532, 0, true, true, READY, DELAY, WINDOW, false, true, false, false, false, false, false);
        check(LanceLogic.decide(still).move() == Move.CHARGE, "still creeper at 3.0: charge");
        var tight = new View(3.0, 3.4, 0.1532, 0, true, true, READY, DELAY, WINDOW, false, true, false, false, false, false, false);
        check(LanceLogic.decide(tight).move() != Move.CHARGE, "a charge tick that would put its feet inside 3.3: not taken");
        System.out.println("LanceLogicTest ok");
    }
}
