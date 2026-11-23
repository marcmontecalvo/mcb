package com.vulpine.mcb.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * The trial op's request (pure, checked by TrialLogicTest): set the scene for a combat
 * trial on the play server. It clears the area and the inventory, gives items, heals,
 * sets the time and summons mobs at offsets from the body. It takes structured fields
 * only, never a command string. Docs: COMPANION_COMBAT_PLAN_2026-11-23.md section 5.
 */
final class TrialLogic {
    private TrialLogic() {}

    static final int MAX_CLEAR = 48, MAX_COUNT = 256, MAX_GIVE = 36, MAX_SUMMON = 8, MAX_OFFSET = 32;
    static final Set<String> FIELDS = Set.of("clear", "clear_inventory", "heal", "time", "give", "summon");
    /** Every request carries these (bridge.py envelope); anything else unknown is refused. */
    static final Set<String> ENVELOPE = Set.of("schema_version", "run_id", "episode_id", "body_id", "sequence", "op");
    /** Bosses turn the world over: a trial never summons one. */
    static final Set<String> BOSSES = Set.of("minecraft:ender_dragon", "minecraft:wither");
    private static final Pattern ID = Pattern.compile("[a-z0-9_.-]+:[a-z0-9_./-]+");

    record Give(String item, int count) {}
    record Summon(String type, int dx, int dy, int dz, boolean helmet) {}
    record Trial(int clear, boolean clearInventory, boolean heal, Integer time, List<Give> give, List<Summon> summon) {}

    private static BodyActions.Fail bad() { return new BodyActions.Fail("bad_request"); }

    private static int integer(JsonElement e, int min, int max) {
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) throw bad();
        double v = e.getAsDouble();
        if (v != Math.rint(v) || v < min || v > max) throw bad();
        return (int) v;
    }

    private static boolean flag(JsonObject o, String key) {
        if (!o.has(key)) return false;
        var e = o.get(key);
        if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isBoolean()) throw bad();
        return e.getAsBoolean();
    }

    private static String id(JsonElement e) {
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) throw bad();
        String s = e.getAsString();
        if (!ID.matcher(s).matches()) throw bad();
        return s;
    }

    private static JsonArray array(JsonObject o, String key, int max) {
        if (!o.has(key)) return new JsonArray();
        var e = o.get(key);
        if (!e.isJsonArray() || e.getAsJsonArray().size() > max) throw bad();
        return e.getAsJsonArray();
    }

    private static JsonObject entry(JsonElement e, Set<String> fields) {
        if (!e.isJsonObject()) throw bad();
        var o = e.getAsJsonObject();
        for (var key : o.keySet()) if (!fields.contains(key)) throw bad();
        return o;
    }

    static Trial parse(JsonObject request) {
        for (var key : request.keySet())
            if (!FIELDS.contains(key) && !ENVELOPE.contains(key)) throw bad();
        int clear = request.has("clear") ? integer(request.get("clear"), 0, MAX_CLEAR) : 0;
        Integer time = request.has("time") ? integer(request.get("time"), 0, 23999) : null;
        var give = new ArrayList<Give>();
        for (var e : array(request, "give", MAX_GIVE)) {
            var o = entry(e, Set.of("item", "count"));
            give.add(new Give(id(o.get("item")), integer(o.get("count"), 1, MAX_COUNT)));
        }
        var summon = new ArrayList<Summon>();
        for (var e : array(request, "summon", MAX_SUMMON)) {
            var o = entry(e, Set.of("type", "offset", "helmet"));
            String type = id(o.get("type"));
            var offset = o.get("offset");
            if (offset == null || !offset.isJsonArray() || offset.getAsJsonArray().size() != 3) throw bad();
            var xyz = offset.getAsJsonArray();
            int dx = integer(xyz.get(0), -MAX_OFFSET, MAX_OFFSET), dy = integer(xyz.get(1), -MAX_OFFSET, MAX_OFFSET),
                    dz = integer(xyz.get(2), -MAX_OFFSET, MAX_OFFSET);
            if (BOSSES.contains(type)) throw new BodyActions.Fail("not_allowed");
            summon.add(new Summon(type, dx, dy, dz, flag(o, "helmet")));
        }
        return new Trial(clear, flag(request, "clear_inventory"), flag(request, "heal"), time, List.copyOf(give),
                List.copyOf(summon));
    }
}
