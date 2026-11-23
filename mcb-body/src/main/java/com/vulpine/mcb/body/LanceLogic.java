package com.vulpine.mcb.body;

/**
 * Spear charge (pure, checked by LanceLogicTest). Held in use, a spear hits whatever its look crosses
 * 2.0 to 4.5 from the eye on each tick its speed along the look beats the target's by 4.6 b/s, for
 * 1 + floor(relative b/s x the tier's multiplier) (docs/COMBAT_MECHANICS_26.3.md section 4). The
 * speed is the body's last move (ServerPlayer known movement), so a tick's move hits on the next.
 * A ground sprint at a mob walking in is fast enough (iron: 8); one backing off (a skeleton) is not,
 * and only the sprint-jump's first tick (+0.2 b/tick) beats it. Inside the near end nothing hits:
 * a melee weapon, when carried, takes over and hands back only once the target is far out again
 * (the charge waits out its delay first); a creeper is never met in melee and is outrun after a hit.
 */
final class LanceLogic {
    private LanceLogic() {}

    /** The spear's AttackRange, eye to box. */
    static final double NEAR = CombatLogic.JAB_MIN, FAR = CombatLogic.JAB_MAX;
    /** The eye gap a charge tick aims to land in: margins for the aim and the target's own move. */
    static final double LOW = 2.3, HIGH = 4.2;
    /** Inside this the charge cannot land: the melee weapon takes over (or the body backs off). */
    static final double CLOSE = 2.4;
    /** The melee weapon hands back to the spear only past this: the charge's delay is waited out first. */
    static final double RETURN = 6.0;
    /** The damage condition's relative speed (4.6 b/s every tier), per tick, and a margin over it. */
    static final double MIN_REL = 4.6 / 20, REL_MARGIN = 0.02;
    /** A sprinting ground tick's thrust, and the steady sprint per tick. */
    static final double RUN = CritForecast.SPRINT * CritForecast.INPUT;
    static final double STEADY = RUN / (1 - CritForecast.GROUND_DRAG);
    /** A creeper lights with the body's feet inside 3 blocks: a charge tick keeps them this far. */
    static final double CREEPER_KEEP = CombatLogic.CREEPER_FUSE + 0.3;

    /** BURST: sprint-jump at it; CHARGE: sprint at it; MELEE: the melee weapon's own technique. */
    enum Move { HOLD, CHARGE, BURST, BACK, FLEE, MELEE }
    /** KEEP: leave the use as it is. */
    enum Use { KEEP, START, RESTART, STOP }

    /**
     * eye: eye to the target's box; feet: centre to centre; bodyV: the body's stored velocity toward it;
     * closing: the target's last move toward the body; ticksUsing: of the spear (using); delay and window:
     * the spear's KineticWeapon delay and damage duration; inMelee, fleeing: the state so far; hit: the
     * charge took health off the target since the last tick.
     */
    record View(double eye, double feet, double bodyV, double closing, boolean onGround, boolean using, int ticksUsing,
                int delay, int window, boolean hasMelee, boolean creeper, boolean lit, boolean swollen,
                boolean fleeing, boolean inMelee, boolean hit) {}

    record Act(Move move, Use use, boolean inMelee, boolean fleeing) {}

    static Act decide(View v) {
        Use use = !v.using() ? Use.START : v.ticksUsing() >= v.delay() + v.window() - 2 ? Use.RESTART : Use.KEEP;
        if (v.creeper()) {
            boolean flee = v.lit() || v.hit() || v.feet() < CREEPER_KEEP
                    || v.fleeing() && (v.feet() <= CombatLogic.CREEPER_SAFE || v.swollen());
            if (flee) return new Act(Move.FLEE, use, false, true);
        }
        // ticks until a move made now would be read past the delay (the damage reads it next tick)
        int wait = v.using() ? Math.max(0, v.delay() - 1 - v.ticksUsing()) : v.delay() - 1;
        if (v.hasMelee() && !v.creeper()) {
            boolean melee = v.inMelee() ? v.eye() < RETURN
                    : v.eye() < CLOSE || v.eye() - Math.max(0, v.closing()) * wait < CLOSE;
            if (melee) return new Act(Move.MELEE, v.using() ? Use.STOP : Use.KEEP, true, false);
        }
        return new Act(move(v, wait), use, false, false);
    }

    private static Move move(View v, int wait) {
        if (!v.onGround()) return Move.CHARGE;  // the burst was the jump: keep the run on
        double run = v.bodyV() + RUN, burst = run + CritForecast.SPRINT_JUMP;
        if (wait == 0) {
            if (lands(v, run)) return Move.CHARGE;  // a ground tick: the body can still stop after it
            if (lands(v, burst)) return Move.BURST;
            return v.eye() - burst - v.closing() > HIGH ? Move.CHARGE : Move.BACK;
        }
        // not ready: be at the band's far edge as the delay ends, not inside it before
        if (v.eye() - Math.max(0, v.closing()) * wait < LOW) return Move.BACK;
        double ticksIn = (v.eye() - HIGH) / Math.max(0.05, STEADY + v.closing());
        return ticksIn > wait ? Move.CHARGE : Move.HOLD;
    }

    /** This tick's move puts the target in the band, fast enough, and a creeper's fuse out of it. */
    private static boolean lands(View v, double move) {
        double at = v.eye() - move - v.closing();
        if (at < LOW || at > HIGH || move + v.closing() < MIN_REL + REL_MARGIN) return false;
        return !v.creeper() || v.feet() - move - v.closing() >= CREEPER_KEEP;
    }
}
