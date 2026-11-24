package com.vulpine.mcb.body;

/**
 * The off-hand shield in a melee fight (pure; the fight reads the world). A shield blocks only once it has been up
 * its block delay (5 ticks), and a raised one slows the body to a fifth, so it is raised when a melee mob will be in
 * reach before a late raise would be up, kept up while that holds, and dropped the tick a strike is ready in reach
 * (the strike comes first) or when nothing is close. A shield an axe disabled counts as none until its cooldown ends.
 * Docs: COMPANION_COMBAT_PLAN_2026-11-23.md section 15.
 */
final class ShieldLogic {
    private ShieldLogic() {}

    enum Act { RAISE, LOWER, HOLD, NONE }

    /** Ticks from the raise to a shield that blocks, with one of slack: the block delay is 5. */
    static final int RAISE_TICKS = 6;
    /**
     * The gap (the mob's box to the body's) a melee mob hits across: a mob's attack box is its own grown by about
     * 0.83 blocks; a little more for the tick the hit is judged.
     */
    static final double MOB_REACH = 1.3;

    /** Whether a melee mob `gap` blocks off, closing at `closing` blocks a tick, is in reach before a raise blocks. */
    static boolean threatened(double gap, double closing) {
        return gap - Math.max(0, closing) * RAISE_TICKS <= MOB_REACH;
    }

    /**
     * This tick's shield move: `shield` (one in the off hand, not disabled), `blocking` (raised now), `strike` (a
     * swing is ready and in reach this tick), `threatened` (some melee mob is, as threatened() judges it).
     */
    static Act act(boolean shield, boolean blocking, boolean strike, boolean threatened) {
        if (!shield || strike || !threatened) return blocking ? Act.LOWER : Act.NONE;
        return blocking ? Act.HOLD : Act.RAISE;
    }
}
