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
    static final Set<String> FIELDS = Set.of("clear", "clear_inventory", "heal", "time", "give", "summon", "surface");
    /** Every request carries these (bridge.py envelope); anything else unknown is refused. */
    static final Set<String> ENVELOPE = Set.of("schema_version", "run_id", "episode_id", "body_id", "sequence", "op");
    /** Bosses turn the world over: a trial never summons one. */
    static final Set<String> BOSSES = Set.of("minecraft:ender_dragon", "minecraft:wither");
    private static final Pattern ID = Pattern.compile("[a-z0-9_.-]+:[a-z0-9_./-]+");

    static final int MAX_ENCHANTMENTS = 4, MAX_LEVEL = 10;
    /** An item, how many, and its enchantments (id to level; a trident's Loyalty). */
    record Give(String item, int count, java.util.Map<String, Integer> enchantments) {}
    /** An offset summon stands at (dx, dy, dz); a distance summon (offset 0) at a cell BodyTrial picks from ring(). */
    record Summon(String type, int dx, int dy, int dz, boolean helmet, Integer distance) {}
    /** surface: the body moves to the top of its column first and summons stand on their columns' ground. */
    record Trial(int clear, boolean clearInventory, boolean heal, Integer time, List<Give> give, List<Summon> summon,
                 boolean surface) {}

    /**
     * The feet y of a summon: dy above its column's ground (the first free cell over the top block) with
     * surface, else dy from the body's feet. The caller still checks the mob has room there.
     */
    static int summonY(boolean surface, int feetY, int dy, int groundY) {
        return (surface ? groundY : feetY) + dy;
    }

    static final int MIN_DISTANCE = 2, HEADINGS = 16;

    /**
     * The cells a distance summon may stand on, best first: the exact distance all round before one block nearer or
     * farther, then two; on each ring east first (where an offset summon stood), then turning both ways. Rounded to
     * whole cells, no cell twice, none on the body.
     */
    static List<int[]> ring(int distance) {
        var cells = new ArrayList<int[]>();
        var taken = new java.util.HashSet<Long>();
        for (int step : new int[] {0, -1, 1, -2, 2}) {
            int r = distance + step;
            if (r < MIN_DISTANCE) continue;
            for (int i = 0; i <= HEADINGS / 2; i++) {
                for (int k : i == 0 || i == HEADINGS / 2 ? new int[] {i} : new int[] {i, -i}) {
                    double a = k * 2 * Math.PI / HEADINGS;
                    int dx = (int) Math.round(r * Math.cos(a)), dz = (int) Math.round(r * Math.sin(a));
                    if (Math.hypot(dx, dz) < 1.5 || !taken.add(((long) dx << 32) ^ (dz & 0xffffffffL))) continue;
                    cells.add(new int[] {dx, dz});
                }
            }
        }
        return cells;
    }

    /** A summon's ground is level with the body's feet within a step: no rise to hide behind, no drop to fall off. */
    static boolean level(int feetY, int groundY) {
        return Math.abs(groundY - feetY) <= 1;
    }

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
            var o = entry(e, Set.of("item", "count", "enchantments"));
            var enchantments = new java.util.TreeMap<String, Integer>();
            if (o.has("enchantments")) {
                var map = o.get("enchantments");
                if (!map.isJsonObject() || map.getAsJsonObject().size() > MAX_ENCHANTMENTS) throw bad();
                for (var level : map.getAsJsonObject().entrySet()) {
                    if (!ID.matcher(level.getKey()).matches()) throw bad();
                    enchantments.put(level.getKey(), integer(level.getValue(), 1, MAX_LEVEL));
                }
            }
            give.add(new Give(id(o.get("item")), integer(o.get("count"), 1, MAX_COUNT),
                    java.util.Map.copyOf(enchantments)));
        }
        var summon = new ArrayList<Summon>();
        for (var e : array(request, "summon", MAX_SUMMON)) {
            var o = entry(e, Set.of("type", "offset", "distance", "helmet"));
            String type = id(o.get("type"));
            if (o.has("distance") == o.has("offset")) throw bad();  // exactly one way to place it
            if (BOSSES.contains(type)) throw new BodyActions.Fail("not_allowed");
            if (o.has("distance")) {
                int distance = integer(o.get("distance"), MIN_DISTANCE, MAX_OFFSET);
                summon.add(new Summon(type, 0, 0, 0, flag(o, "helmet"), distance));
                continue;
            }
            var offset = o.get("offset");
            if (!offset.isJsonArray() || offset.getAsJsonArray().size() != 3) throw bad();
            var xyz = offset.getAsJsonArray();
            int dx = integer(xyz.get(0), -MAX_OFFSET, MAX_OFFSET), dy = integer(xyz.get(1), -MAX_OFFSET, MAX_OFFSET),
                    dz = integer(xyz.get(2), -MAX_OFFSET, MAX_OFFSET);
            summon.add(new Summon(type, dx, dy, dz, flag(o, "helmet"), null));
        }
        return new Trial(clear, flag(request, "clear_inventory"), flag(request, "heal"), time, List.copyOf(give),
                List.copyOf(summon), flag(request, "surface"));
    }
}
