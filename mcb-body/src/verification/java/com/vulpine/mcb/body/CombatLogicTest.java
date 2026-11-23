package com.vulpine.mcb.body;

import com.google.gson.JsonParser;

public final class CombatLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static CombatLogic.Tactic tactic(String json) {
        return CombatLogic.parseTactic(JsonParser.parseString(json).getAsJsonObject());
    }

    private static void rejects(String json) {
        try { tactic(json); }
        catch (BodyActions.Fail fail) { check(fail.getMessage().equals("bad_request"), json); return; }
        throw new AssertionError("accepted " + json);
    }

    /**
     * Independent arrow flight (AbstractArrow.tick: move, then drag, then gravity). The game
     * tests the whole segment an arrow sweeps each tick, so the miss is measured along it.
     */
    private static double missBy(double[] aim, double dx, double dy, double dz, double vx, double vy, double vz) {
        double yaw = Math.toRadians(aim[0]), pitch = Math.toRadians(aim[1]);
        double ax = -Math.sin(yaw) * Math.cos(pitch) * 3, ay = -Math.sin(pitch) * 3, az = Math.cos(yaw) * Math.cos(pitch) * 3;
        double x = 0, y = 0, z = 0, best = Double.POSITIVE_INFINITY;
        for (int t = 0; t < 200; t++) {
            for (int s = 1; s <= 30; s++) {
                double f = s / 30.0, tt = t + f;
                best = Math.min(best, Math.sqrt(Math.pow(x + ax * f - (dx + vx * tt), 2)
                        + Math.pow(y + ay * f - (dy + vy * tt), 2) + Math.pow(z + az * f - (dz + vz * tt), 2)));
            }
            x += ax; y += ay; z += az;
            ax *= .99; ay *= .99; az *= .99; ay -= .05;
        }
        return best;
    }

    public static void main(String[] args) {
        // --- tactic parsing: absent = crit melee (the old fight, now with crits)
        var d = CombatLogic.parseTactic(JsonParser.parseString("{}").getAsJsonObject());
        check(d.style().equals("melee") && d.crit() && d.sweep() && d.melee() == null && !d.noGaze(), "default tactic");
        var s = tactic("{\"tactic\":{\"style\":\"snipe\",\"crit\":false,\"sweep\":true,\"keep\":[8,15],"
                + "\"melee\":\"minecraft:iron_sword\"}}");
        check(s.style().equals("snipe") && s.keepMin() == 8 && s.keepMax() == 15 && "minecraft:iron_sword".equals(s.melee()), "snipe");
        check(tactic("{\"tactic\":{\"style\":\"melee\",\"crit\":true,\"sweep\":true,\"no_gaze\":true}}").noGaze(), "no gaze");
        rejects("{\"tactic\":5}"); rejects("{\"tactic\":{\"style\":\"dance\"}}"); rejects("{\"tactic\":{\"style\":\"avoid\"}}");
        rejects("{\"tactic\":{\"style\":\"poke\",\"keep\":[4,2]}}"); rejects("{\"tactic\":{\"style\":\"poke\",\"keep\":[1]}}");
        rejects("{\"tactic\":{\"style\":\"melee\",\"crit\":\"yes\"}}"); rejects("{\"tactic\":{\"style\":\"snipe\",\"melee\":\"\"}}");

        // --- weapon kinds
        check(CombatLogic.kind("minecraft:iron_sword") == CombatLogic.Kind.SWORD, "sword");
        check(CombatLogic.kind("minecraft:netherite_axe") == CombatLogic.Kind.AXE, "axe");
        check(CombatLogic.kind("minecraft:wooden_spear") == CombatLogic.Kind.SPEAR, "spear");
        check(CombatLogic.kind("minecraft:trident") == CombatLogic.Kind.TRIDENT, "trident");
        check(CombatLogic.kind("minecraft:bow") == CombatLogic.Kind.BOW, "bow");
        check(CombatLogic.kind("minecraft:pickaxe") == CombatLogic.Kind.OTHER && CombatLogic.kind(null) == CombatLogic.Kind.OTHER, "other");
        check(CombatLogic.Kind.SWORD.crits() && CombatLogic.Kind.AXE.crits() && CombatLogic.Kind.TRIDENT.crits(), "swing weapons crit");
        // Player.attack crits whatever is held: a fist (1 -> 1.5) or a pickaxe too
        check(CombatLogic.Kind.OTHER.crits(), "a fist or a tool crits");
        check(!CombatLogic.Kind.SPEAR.crits() && !CombatLogic.Kind.BOW.crits(), "jab and bow do not");

        // --- melee crit cycle: jump so the cooldown is full on the way down, strike falling
        var A = CombatLogic.Act.class;
        check(CombatLogic.melee(false, 1, 0, true, false, true, true, true, false, true) == CombatLogic.Act.NONE, "out of reach: move");
        check(CombatLogic.melee(true, .5, 7, true, false, true, true, true, false, true) == CombatLogic.Act.NONE, "too early to jump");
        check(CombatLogic.melee(true, .6, CombatLogic.JUMP_LEAD, true, false, true, true, true, false, true) == CombatLogic.Act.JUMP, "jump on lead");
        check(CombatLogic.melee(true, 1, 0, false, false, true, true, true, false, true) == CombatLogic.Act.NONE, "rising: wait for the fall");
        check(CombatLogic.melee(true, 1, 0, false, true, true, true, true, false, true) == CombatLogic.Act.STRIKE, "falling and full: crit");
        check(CombatLogic.melee(true, .8, 2, false, true, true, true, true, false, true) == CombatLogic.Act.NONE, "falling, not full: no weak hit");
        // no crit possible here (water, ladder, low ceiling): a full-strength grounded hit
        check(CombatLogic.melee(true, 1, 0, true, false, false, true, true, false, true) == CombatLogic.Act.STRIKE, "no crit here");
        check(CombatLogic.melee(true, .85, 1, true, false, false, true, true, false, true) == CombatLogic.Act.NONE, "never before full");
        // a weapon that does not crit (spear handled elsewhere) or crit not wanted
        check(CombatLogic.melee(true, 1, 0, true, false, true, false, true, false, true) == CombatLogic.Act.STRIKE, "crit off");
        check(CombatLogic.melee(true, 1, 0, true, false, true, true, false, false, true) == CombatLogic.Act.STRIKE, "no crit kind");
        // a grounded sword hit would sweep the animals near the target: sprint hit instead
        check(CombatLogic.melee(true, 1, 0, true, false, false, true, true, true, false) == CombatLogic.Act.SPRINT_STRIKE, "no sweep");
        check(CombatLogic.melee(true, 1, 0, true, false, false, true, true, true, true) == CombatLogic.Act.STRIKE, "sweep allowed");

        // --- stand-off (spear poke, bow snipe)
        check(CombatLogic.keep(2.0, 2.5, 4) == CombatLogic.Move.RETREAT, "too close");
        check(CombatLogic.keep(5.0, 2.5, 4) == CombatLogic.Move.APPROACH, "too far");
        check(CombatLogic.keep(3.0, 2.5, 4) == CombatLogic.Move.HOLD, "in the band");
        check(CombatLogic.jabReach(3.0) && CombatLogic.jabReach(4.4) && !CombatLogic.jabReach(1.9) && !CombatLogic.jabReach(4.6), "jab reach");

        // --- creeper hit and back
        check(CombatLogic.hitAndBack(false, 2.8, false, true, 1).equals("strike"), "full and in reach: sprint hit");
        check(CombatLogic.hitAndBack(false, 5, false, false, 1).equals("approach"), "close in");
        check(CombatLogic.hitAndBack(true, 5, false, false, 1).equals("retreat"), "after a hit: back off");
        check(CombatLogic.hitAndBack(true, 7.6, true, false, 1).equals("retreat"), "still swelling: stay off");
        check(CombatLogic.hitAndBack(true, 7.6, false, false, 1).equals("approach"), "swell gone: go again");
        check(CombatLogic.hitAndBack(false, 3, true, true, 1).equals("retreat"), "a swelling creeper is never hit");

        // --- bow: full draw, aim with drop and lead
        check(CombatLogic.FULL_DRAW == 20, "full draw");
        double[] still = CombatLogic.aim(0, 0, 20, 0, 0, 0);
        check(still != null && still[1] < 0 && still[1] > -10, "level shot aims a little up");
        check(missBy(still, 0, 0, 20, 0, 0, 0) < .3, "level shot hits");
        double[] up = CombatLogic.aim(6, 8, 10, 0, 0, 0);
        check(up != null && missBy(up, 6, 8, 10, 0, 0, 0) < .3, "uphill shot hits");
        double[] lead = CombatLogic.aim(0, 0, 20, .15, 0, 0);
        check(lead != null && missBy(lead, 0, 0, 20, .15, 0, 0) < .4, "a walking target is led");
        check(missBy(still, 0, 0, 20, .15, 0, 0) > .8, "unled would miss it");
        check(CombatLogic.aim(0, 0, 400, 0, 0, 0) == null, "out of range");
        check(CombatLogic.snipeClose(3.5, true) && !CombatLogic.snipeClose(3.5, false) && !CombatLogic.snipeClose(6, true),
                "switch to melee when it closes in and a melee weapon is held");
        System.out.println("CombatLogicTest ok");
    }
}
