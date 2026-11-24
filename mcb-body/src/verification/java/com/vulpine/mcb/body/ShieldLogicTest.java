package com.vulpine.mcb.body;

import com.vulpine.mcb.body.ShieldLogic.Act;

public final class ShieldLogicTest {
    private static void check(boolean ok, String what) { if (!ok) throw new AssertionError(what); }

    public static void main(String[] args) {
        // a melee mob is a threat once the gap to its reach closes within the shield's raise time
        check(ShieldLogic.threatened(1.0, 0), "inside its reach");
        check(!ShieldLogic.threatened(3.0, 0), "standing off, still");
        check(ShieldLogic.threatened(3.0, 0.3), "walking in at 0.3 a tick: there before a late shield is up");
        check(!ShieldLogic.threatened(3.0, -0.3), "backing away");
        check(!ShieldLogic.threatened(6.0, 0.3), "far, even closing");

        // no shield (or one an axe disabled): a raised one comes down, nothing else
        check(ShieldLogic.act(false, true, false, true) == Act.LOWER, "no shield, blocking: lower");
        check(ShieldLogic.act(false, false, false, true) == Act.NONE, "no shield: nothing");
        // a strike ready in reach comes first: the shield drops for it
        check(ShieldLogic.act(true, true, true, true) == Act.LOWER, "strike: lower");
        check(ShieldLogic.act(true, false, true, true) == Act.NONE, "strike, not blocking: nothing");
        // threatened and not striking: raise, then keep it up
        check(ShieldLogic.act(true, false, false, true) == Act.RAISE, "threat: raise");
        check(ShieldLogic.act(true, true, false, true) == Act.HOLD, "threat: hold");
        // no threat: the shield comes down (blocking walks at a fifth of the speed)
        check(ShieldLogic.act(true, true, false, false) == Act.LOWER, "clear: lower");
        check(ShieldLogic.act(true, false, false, false) == Act.NONE, "clear: nothing");
        System.out.println("ShieldLogicTest ok");
    }
}
