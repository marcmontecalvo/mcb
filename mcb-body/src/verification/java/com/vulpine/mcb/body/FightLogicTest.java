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
