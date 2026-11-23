package com.vulpine.mcb.body;

import com.google.gson.JsonParser;

public final class TrialLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static TrialLogic.Trial parse(String json) {
        return TrialLogic.parse(JsonParser.parseString(json).getAsJsonObject());
    }

    private static void rejects(String json, String reason) {
        try { parse(json); }
        catch (BodyActions.Fail fail) { check(fail.getMessage().equals(reason), json + " -> " + fail.getMessage()); return; }
        throw new AssertionError("accepted " + json);
    }

    public static void main(String[] args) {
        var none = parse("{}");
        check(none.clear() == 0 && !none.clearInventory() && !none.heal() && none.time() == null, "defaults do nothing");
        check(none.give().isEmpty() && none.summon().isEmpty(), "nothing given or summoned");

        var full = parse("{\"clear\":32,\"clear_inventory\":true,\"heal\":true,\"time\":18000,"
                + "\"give\":[{\"item\":\"minecraft:iron_sword\",\"count\":1},{\"item\":\"minecraft:arrow\",\"count\":64}],"
                + "\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[8,0,-2],\"helmet\":true},"
                + "{\"type\":\"minecraft:pig\",\"offset\":[-3,0,0]}]}");
        check(full.clear() == 32 && full.clearInventory() && full.heal() && full.time() == 18000, "flags and time");
        check(full.give().size() == 2 && full.give().get(1).item().equals("minecraft:arrow")
                && full.give().get(1).count() == 64, "items in order");
        var zombie = full.summon().get(0);
        check(zombie.type().equals("minecraft:zombie") && zombie.dx() == 8 && zombie.dy() == 0 && zombie.dz() == -2
                && zombie.helmet(), "summon with offset and a helmet (undead do not burn by day)");
        check(!full.summon().get(1).helmet(), "helmet off by default");
        check(!full.surface() && !none.surface(), "the body stays where it is by default");
        check(parse("{\"surface\":true}").surface(), "surface: the scene starts on open ground");
        rejects("{\"surface\":\"yes\"}", "bad_request");

        // where a summon stands: on its column's ground with surface, else level with the body
        check(TrialLogic.summonY(true, 71, 0, 77) == 77, "surface: the summon column's ground");
        check(TrialLogic.summonY(true, 71, 2, 77) == 79, "surface: dy above that ground");
        check(TrialLogic.summonY(false, 71, 0, 77) == 71, "no surface: the body's feet level");
        check(TrialLogic.summonY(false, 71, -1, 77) == 70, "no surface: dy from the feet");

        // distance instead of an offset: anywhere about that far, in sight, on ground level with the body
        var seen = parse("{\"summon\":[{\"type\":\"minecraft:skeleton\",\"distance\":8}]}").summon().get(0);
        check(seen.distance() == 8 && seen.dx() == 0 && seen.dz() == 0, "a distance summon has no offset");
        check(full.summon().get(0).distance() == null, "an offset summon has no distance");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"distance\":8,\"offset\":[8,0,0]}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"distance\":1}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"distance\":33}]}", "bad_request");
        var ring = TrialLogic.ring(8);
        check(ring.get(0)[0] == 8 && ring.get(0)[1] == 0, "east first, where an offset summon stood");
        var keys = new java.util.HashSet<Long>();
        for (int[] c : ring) {
            check(keys.add(((long) c[0] << 32) ^ (c[1] & 0xffffffffL)), "no cell twice");
            double r = Math.hypot(c[0], c[1]);
            check(r >= 5.5 && r <= 10.5, "every cell within two blocks of the distance: " + r);
        }
        check(ring.size() >= 48, "several rings of headings: " + ring.size());
        check(Math.abs(Math.hypot(ring.get(15)[0], ring.get(15)[1]) - 8) <= 0.75, "the exact distance all round first");
        check(TrialLogic.ring(2).stream().allMatch(c -> Math.hypot(c[0], c[1]) >= 1.5), "never on the body");
        check(TrialLogic.level(71, 71) && TrialLogic.level(71, 72) && TrialLogic.level(71, 70), "a step is level");
        check(!TrialLogic.level(71, 73) && !TrialLogic.level(71, 69), "a rise or a drop is not");

        // structured fields only: never a command string
        rejects("{\"clear\":-1}", "bad_request"); rejects("{\"clear\":49}", "bad_request");
        rejects("{\"time\":24000}", "bad_request"); rejects("{\"time\":-1}", "bad_request");
        rejects("{\"heal\":1}", "bad_request"); rejects("{\"give\":{}}", "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:stone\",\"count\":0}]}", "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:stone\",\"count\":257}]}", "bad_request");
        rejects("{\"give\":[{\"item\":\"stone; op me\",\"count\":1}]}", "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:stone\"}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[33,0,0]}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[1,0]}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[1.5,0,0]}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\"}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[1,0,0],\"nbt\":\"{}\"}]}", "bad_request");
        rejects("{\"command\":\"/op Marc\"}", "bad_request");
        // a boss turns the world over: never summoned by a trial
        rejects("{\"summon\":[{\"type\":\"minecraft:ender_dragon\",\"offset\":[0,10,0]}]}", "not_allowed");
        rejects("{\"summon\":[{\"type\":\"minecraft:wither\",\"offset\":[0,0,5]}]}", "not_allowed");
        var many = new StringBuilder("{\"summon\":[");
        for (int i = 0; i < 9; i++) many.append(i == 0 ? "" : ",").append("{\"type\":\"minecraft:zombie\",\"offset\":[1,0,0]}");
        rejects(many.append("]}").toString(), "bad_request");
        System.out.println("TrialLogicTest ok");
    }
}
