package com.vulpine.mcb.body;

import com.google.gson.JsonParser;
import java.util.List;

public final class FightLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static void rejects(String json) {
        try { FightLogic.parse(JsonParser.parseString(json).getAsJsonObject()); }
        catch (BodyActions.Fail fail) { check(fail.getMessage().equals("bad_request"), json); return; }
        throw new AssertionError("accepted " + json);
    }

    private static FightLogic.Mob mob(String id, String type, double distance, double dy, boolean los) {
        return new FightLogic.Mob(id, type, distance, dy, los);
    }

    public static void main(String[] args) {
        var d = FightLogic.parse(JsonParser.parseString("{}").getAsJsonObject());
        check(d.radius() == 12 && d.floor() == 6 && d.maxTicks() == 600 && d.leash() == 6, "defaults");
        check(d.entity() == null && d.anchor() == null && d.weapon() == null, "optionals absent");
        var full = FightLogic.parse(JsonParser.parseString(
                "{\"entity\":\"123e4567-e89b-12d3-a456-426614174000\",\"radius\":2,\"floor\":0,\"max_ticks\":2400,"
                + "\"anchor\":[1,64.5,-3],\"leash\":32,\"weapon\":\"minecraft:iron_sword\"}").getAsJsonObject());
        check(full.radius() == 2 && full.floor() == 0 && full.maxTicks() == 2400 && full.leash() == 32, "bounds accepted");
        check(full.anchor()[1] == 64.5 && full.weapon().equals("minecraft:iron_sword"), "anchor and weapon");
        rejects("{\"radius\":1}"); rejects("{\"radius\":33}"); rejects("{\"radius\":5.5}"); rejects("{\"radius\":\"9\"}");
        rejects("{\"floor\":-1}"); rejects("{\"floor\":20}"); rejects("{\"max_ticks\":19}"); rejects("{\"max_ticks\":2401}");
        rejects("{\"leash\":0}"); rejects("{\"leash\":33}"); rejects("{\"anchor\":[1,2]}"); rejects("{\"anchor\":[1,2,\"x\"]}");
        rejects("{\"anchor\":5}"); rejects("{\"entity\":\"nope\"}"); rejects("{\"entity\":7}"); rejects("{\"weapon\":\"\"}");

        check(FightLogic.endReason(6, 6, 1, 600).equals("low_health"), "health at floor ends");
        check(FightLogic.endReason(6.5, 6, 1, 600) == null, "health above floor carries on");
        check(FightLogic.endReason(20, 6, 601, 600).equals("timeout"), "timeout");
        check(FightLogic.endReason(20, 6, 600, 600) == null, "last tick still runs");
        check(FightLogic.endReason(1, 6, 601, 600).equals("low_health"), "health outranks timeout");

        check(FightLogic.avoid(List.of(mob("a", "minecraft:creeper", 3.5, 0, true))), "creeper at 3.5");
        check(FightLogic.avoid(List.of(mob("a", "minecraft:warden", 2, 0, false))), "warden without line of sight");
        check(!FightLogic.avoid(List.of(mob("a", "minecraft:creeper", 3.6, 0, true))), "creeper beyond 3.5");
        check(!FightLogic.avoid(List.of(mob("a", "minecraft:zombie", 1, 0, true))), "zombie is not avoided");
        // hit-and-back is the creeper technique: the creeper is its target, a warden still ends it
        check(!FightLogic.avoid(List.of(mob("a", "minecraft:creeper", 2, 0, true)), true), "creeper fought hit-and-back");
        check(FightLogic.avoid(List.of(mob("a", "minecraft:warden", 2, 0, true)), true), "warden ends hit-and-back");
        check(FightLogic.avoid(List.of(mob("a", "minecraft:creeper", 2, 0, true)), false), "creeper ends other styles");

        check(FightLogic.fightable("minecraft:zombie") && !FightLogic.fightable("minecraft:creeper")
                && !FightLogic.fightable("minecraft:warden") && !FightLogic.fightable("minecraft:cow"), "fightable set");
        var mobs = List.of(mob("far", "minecraft:zombie", 9, 0, true), mob("near", "minecraft:skeleton", 4, 1, true),
                mob("hidden", "minecraft:spider", 2, 0, false), mob("high", "minecraft:zombie", 3, 7, true),
                mob("creeper", "minecraft:creeper", 3, 0, true), mob("cow", "minecraft:cow", 1, 0, true));
        check(FightLogic.pick(null, mobs, 12).uuid().equals("near"), "nearest fightable in view");
        check(FightLogic.pick(null, mobs, 3) == null, "none within radius, in view, level");
        check(FightLogic.pick("far", mobs, 12).uuid().equals("far"), "requested first");
        check(FightLogic.pick("far", mobs, 8).uuid().equals("near"), "requested out of radius falls back");
        check(FightLogic.pick("gone", mobs, 12).uuid().equals("near"), "requested absent falls back");
        check(FightLogic.pick("cow", mobs, 12).uuid().equals("cow"), "requested need not be on the hostile list");
        check(FightLogic.pick(null, List.of(), 12) == null, "empty is clear");

        // a stand-off band past the radius widens it: a bow never backs out of its own fight
        check(FightLogic.reach(12, 0) == 12, "no band: the radius");
        check(FightLogic.reach(12, 15) == 15 + FightLogic.KEEP_MARGIN, "band max plus a margin");
        check(FightLogic.reach(24, 15) == 24, "a wider radius stands");
        check(FightLogic.reach(12, 30.5) == FightLogic.MAX_REACH, "capped at the box the op may scan");

        // a chase path round a ledge the straight step cannot climb (live trial: an axe stood under a
        // skeleton two blocks up for 200 ticks)
        java.util.function.IntBinaryOperator ledge = (x, z) -> x >= 1 && x <= 6 ? (x == 1 && z == 4 ? 67 : 68) : 66;
        FightLogic.Steps walk = (x, y, z, dx, dz) -> {
            int ny = ledge.applyAsInt(x + dx, z + dz);
            return ny - y <= 1 && y - ny <= FightLogic.MAX_DROP ? Double.valueOf(ny) : null;
        };
        var path = FightLogic.chase(new double[] {0, 66, 0}, 4.5, 68, 0.5, 2.5, walk, 4096);
        check(path != null && !path.isEmpty(), "a way round by the ramp");
        check(path.get(0)[0] == 0 && path.get(0)[2] == 1, "first step along the ledge toward the ramp");
        var last = path.get(path.size() - 1);
        check(Math.hypot(last[0] + .5 - 4.5, last[2] + .5 - 0.5) <= 2.5 && Math.abs(last[1] - 68) <= 1.5,
                "ends in reach of the target");
        for (int i = 1; i < path.size(); i++)
            check(Math.abs(path.get(i)[0] - path.get(i - 1)[0]) + Math.abs(path.get(i)[2] - path.get(i - 1)[2]) == 1,
                    "one cardinal step at a time");
        java.util.function.IntBinaryOperator cliff = (x, z) -> x >= 1 ? 68 : 66;
        FightLogic.Steps blocked = (x, y, z, dx, dz) -> {
            int ny = cliff.applyAsInt(x + dx, z + dz);
            return ny - y <= 1 && y - ny <= FightLogic.MAX_DROP ? Double.valueOf(ny) : null;
        };
        double[] foot = {0, 66, 0};
        check(FightLogic.chase(foot, 4.5, 68, 0.5, 2.5, blocked, 4096) == null, "a cliff: no way up");
        check(FightLogic.chase(foot, 1.5, 66, 0.5, 2.5, walk, 4096).isEmpty(), "in reach: no steps");
        check(FightLogic.chase(foot, 4.5, 68, 0.5, 2.5, walk, 3) == null, "a node cap gives up");
        FightLogic.Steps flat = (x, y, z, dx, dz) -> 66.0;
        check(FightLogic.chase(foot, 40.5, 66, 0.5, 2.5, flat, 100000) == null, "never past CHASE_SPAN from the start");
        FightLogic.Steps slab = (x, y, z, dx, dz) -> x + dx == 1 ? 66.5 : x + dx >= 2 ? 67.0 : 66.0;
        var up = FightLogic.chase(foot, 4.5, 67, 0.5, 1.0, slab, 4096);
        check(up != null && up.get(0)[1] == 66.5, "a slab is a half-block stance on the way");

        // damage dealt to the target between two ticks: a drop in health, never a heal, never below zero
        check(FightLogic.dealt(20, 14) == 6, "a hit");
        check(FightLogic.dealt(5, -2) == 5, "a kill counts the health it had");
        check(FightLogic.dealt(10, 12) == 0, "a heal is not negative damage");

        check(FightLogic.direction(3, 1)[0] == 1 && FightLogic.direction(3, 1)[1] == 0, "east");
        check(FightLogic.direction(-3, 1)[0] == -1, "west");
        check(FightLogic.direction(1, 3)[1] == 1 && FightLogic.direction(1, -3)[1] == -1, "south and north");
        check(FightLogic.withinLeash(null, 1, 100, 100, 100), "no anchor, no leash");
        double[] anchor = {0.5, 64, 0.5};
        check(FightLogic.withinLeash(anchor, 6, 6, 64, 0), "on the leash edge");
        check(!FightLogic.withinLeash(anchor, 6, 7, 64, 0), "past the leash");
        check(!FightLogic.withinLeash(anchor, 6, 0, 71, 0), "leash counts height");

        check("minecraft:stone_sword".equals(FightLogic.desiredWeapon("minecraft:stone_sword", true, "minecraft:iron_sword")), "requested weapon carried");
        check("minecraft:iron_sword".equals(FightLogic.desiredWeapon("minecraft:stone_sword", false, "minecraft:iron_sword")), "requested broke, best instead");
        check(FightLogic.desiredWeapon(null, false, null) == null, "empty hand when nothing");
        check(!FightLogic.needsReselect(null, null) && FightLogic.needsReselect(null, "a") && !FightLogic.needsReselect("a", "a"), "reselect");
        System.out.println("FightLogicTest ok");
    }
}
