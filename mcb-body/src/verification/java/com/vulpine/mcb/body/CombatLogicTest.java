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

        // --- spear poke: the band, and a creeper's fuse outrun first
        check(CombatLogic.poke(4.0, 0, 4.3, 3.6, 4.4, false, false) == CombatLogic.Move.HOLD, "poke: in the band");
        check(CombatLogic.poke(3.4, 0, 3.7, 3.6, 4.4, false, false) == CombatLogic.Move.RETREAT, "poke: too close");
        check(CombatLogic.poke(5.0, 0, 5.3, 3.6, 4.4, false, false) == CombatLogic.Move.APPROACH, "poke: too far");
        check(CombatLogic.poke(4.0, 0, 4.3, 3.6, 4.4, true, false) == CombatLogic.Move.FLEE, "poke: a swelling creeper is outrun");
        check(CombatLogic.poke(6.0, 0, 6.3, 3.6, 4.4, false, true) == CombatLogic.Move.FLEE, "poke: fled until past the swell's reach");
        check(CombatLogic.poke(7.4, 0, 7.7, 3.6, 4.4, false, true) == CombatLogic.Move.APPROACH, "poke: swell gone, out there: go again");
        check(CombatLogic.poke(7.4, 0, 7.7, 3.6, 4.4, true, true) == CombatLogic.Move.FLEE, "poke: still swelling out there");
        // the band is judged where the gap will be a few ticks on: walking in at 0.2 a tick while a
        // creeper walks in at 0.13, the body needs about five ticks to turn round (live 20261123T205225Z:
        // a 3.3 near edge let the creeper inside its 3-block fuse after every jab)
        check(CombatLogic.poke(4.6, .33, 4.9, 3.6, 4.4, false, false) == CombatLogic.Move.RETREAT, "poke: closing fast: back off early");
        check(CombatLogic.poke(4.5, .13, 4.8, 3.6, 4.4, false, false) == CombatLogic.Move.HOLD, "poke: a creeper walking in: wait in the band");
        check(CombatLogic.poke(4.0, -.2, 4.3, 3.6, 4.4, false, false) == CombatLogic.Move.HOLD, "poke: a gap opening is not led");
        check(CombatLogic.poke(5.2, .1, 5.5, 3.6, 4.4, false, false) == CombatLogic.Move.APPROACH, "poke: still far: close in");
        check(CombatLogic.POKE_LEAD >= 5, "poke: led by at least the body's turn-round time");

        // --- a creeper is lit when its swell climbs, or a draining swell is far on; a flicker is not
        // (live 20261123T210731Z: every jab left swell 1 of 28 draining, swell_dir -1, and the poke fled)
        check(CombatLogic.lit(1, 0), "lit: the swell climbs");
        check(!CombatLogic.lit(-1, .04), "a draining flicker is not lit");
        check(!CombatLogic.lit(-1, 0), "no swell");
        check(CombatLogic.lit(-1, .6), "a draining swell far on: still kept off");

        // --- which tactics may fight a creeper: hit-and-back, or a poke whose band stays past its fuse
        check(CombatLogic.fightsCreepers(new CombatLogic.Tactic("hit_and_back", false, true, 0, 0, null, false)), "hit and back fights creepers");
        check(CombatLogic.fightsCreepers(new CombatLogic.Tactic("poke", false, true, 3.6, 4.4, null, false)), "a fuse-clear poke fights creepers");
        check(!CombatLogic.fightsCreepers(new CombatLogic.Tactic("poke", false, true, 2.5, 4.0, null, false)), "a zombie's poke band is inside the fuse");
        check(!CombatLogic.fightsCreepers(CombatLogic.DEFAULT), "crit melee leaves creepers");
        // the spear charge: hits from 2.0 to 4.5 and outruns a creeper after each (LanceLogic)
        var lance = tactic("{\"tactic\":{\"style\":\"lance\",\"crit\":true,\"melee\":\"minecraft:iron_sword\"}}");
        check(lance.style().equals("lance") && "minecraft:iron_sword".equals(lance.melee()) && lance.crit(), "lance parses");
        check(CombatLogic.fightsCreepers(lance), "a lance fights creepers (never in melee)");

        // --- creeper hit and back
        check(CombatLogic.hitAndBack(false, 2.8, false, true, 1).equals("strike"), "full and in reach: sprint hit");
        check(CombatLogic.hitAndBack(false, 5, false, false, 1).equals("approach"), "close in");
        check(CombatLogic.hitAndBack(true, 5, false, false, 1).equals("retreat"), "after a hit: back off");
        check(CombatLogic.hitAndBack(true, 7.6, true, false, 1).equals("retreat"), "still swelling: stay off");
        check(CombatLogic.hitAndBack(true, 7.6, false, false, 1).equals("approach"), "swell gone: go again");
        check(CombatLogic.hitAndBack(false, 3, true, true, 1).equals("retreat"), "a swelling creeper is never hit");

        // --- bow: full draw, aim with drop and lead
        check(CombatLogic.FULL_DRAW == 20, "full draw");
        // the draw is an item use the fight runs itself (a client with no mob in reach sends use-item):
        // through the click helper's entity-reach guard, a mob 3 to 4.5 blocks along the aim cancelled
        // every draw tick after tick (host run 20261123T211058112992Z: 9 rejections in a row)
        check(CombatLogic.draw(false, 0, 20, true, true) == CombatLogic.Draw.START, "draw: start in sight");
        check(CombatLogic.draw(false, 0, 20, false, false) == CombatLogic.Draw.NONE, "draw: nothing to aim at");
        check(CombatLogic.draw(true, 12, 20, true, true) == CombatLogic.Draw.HOLD, "draw: not full yet");
        check(CombatLogic.draw(true, 20, 20, true, true) == CombatLogic.Draw.RELEASE, "draw: full, in sight, aimed");
        check(CombatLogic.draw(true, 25, 20, false, true) == CombatLogic.Draw.HOLD, "draw: hold while sight is lost");
        check(CombatLogic.draw(true, 25, 20, true, false) == CombatLogic.Draw.HOLD, "draw: hold while out of range");
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
