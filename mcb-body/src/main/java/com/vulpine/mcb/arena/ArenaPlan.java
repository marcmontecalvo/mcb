package com.vulpine.mcb.arena;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * An arena plan (pure; the schema docs/ARENA_RUNTIME.md describes): a size, ordered block fills in local
 * coordinates from the min corner (0,0,0), markers (team pads, mob spawns, spectator) and capacity. Parsing checks the
 * shape and that every fill and marker lies inside the size; whether a block id and its states exist is the
 * runtime's vanilla block-state parser's to say, before anything is placed.
 */
public record ArenaPlan(String name, int sx, int sy, int sz, List<Fill> fills, Map<String, Team> teams,
                        List<int[]> mobSpawns, int[] spectator, Capacity capacity) {
    public static final int MAX_AXIS = 256, MAX_FILLS = 5000, MAX_PADS = 64, MAX_SPAWNS = 64, MAX_TEAMS = 8;
    /** Cells a plan may place in all (two full 256-cube volumes); the build is sliced over ticks, never one hitch. */
    public static final long MAX_CELLS = 33_554_432L;
    public static final Pattern NAME = Pattern.compile("[a-z0-9][a-z0-9_-]{0,31}");
    /** A block id with optional states; never NBT, never anything a command parser could read as more. */
    static final Pattern BLOCK = Pattern.compile("[a-z0-9_.-]+:[a-z0-9_./-]+(\\[[a-z0-9_=,]*])?");

    /** A box of cells to set to one block; min and max are normalised from the plan's from and to. */
    public record Fill(int[] min, int[] max, String block) {
        public long volume() {
            return (long) (max[0] - min[0] + 1) * (max[1] - min[1] + 1) * (max[2] - min[2] + 1);
        }
    }

    public record Team(List<int[]> pads, int[] entry) {}

    public record Capacity(int teams, int perTeam, int mobs) {}

    static ArenaFail bad(String detail) { return new ArenaFail("bad_plan", detail); }

    public static ArenaPlan parse(JsonObject plan) {
        if (!plan.has("schema") || number(plan.get("schema"), "schema") != 1) throw bad("schema must be 1");
        String name = text(plan, "name");
        if (!NAME.matcher(name).matches()) throw bad("name " + name + " must match " + NAME.pattern());
        int[] size = triple(plan.get("size"), "size");
        for (int axis : size) if (axis < 1 || axis > MAX_AXIS) throw bad("size must be 1.." + MAX_AXIS + " on every axis");
        if (!plan.has("fills") || !plan.get("fills").isJsonArray()) throw bad("fills must be an array");
        var list = plan.getAsJsonArray("fills");
        if (list.size() > MAX_FILLS) throw bad("at most " + MAX_FILLS + " fills");
        var fills = new ArrayList<Fill>();
        long cells = 0;
        for (int i = 0; i < list.size(); i++) {
            var where = "fills[" + i + "]";
            if (!list.get(i).isJsonObject()) throw bad(where + " must be an object");
            var o = list.get(i).getAsJsonObject();
            int[] from = triple(o.get("from"), where + ".from"), to = triple(o.get("to"), where + ".to");
            String block = text(o, "block");
            if (!BLOCK.matcher(block).matches()) throw bad(where + ".block " + block + " is not a block id with optional states");
            var min = new int[3];
            var max = new int[3];
            for (int a = 0; a < 3; a++) {
                min[a] = Math.min(from[a], to[a]);
                max[a] = Math.max(from[a], to[a]);
                if (min[a] < 0 || max[a] >= size[a]) throw bad(where + " lies outside the size");
            }
            var fill = new Fill(min, max, block);
            cells += fill.volume();
            if (cells > MAX_CELLS) throw bad("more than " + MAX_CELLS + " cells to place");
            fills.add(fill);
        }
        var teams = new LinkedHashMap<String, Team>();
        var spawns = new ArrayList<int[]>();
        int[] spectator = null;
        if (plan.has("markers")) {
            if (!plan.get("markers").isJsonObject()) throw bad("markers must be an object");
            var markers = plan.getAsJsonObject("markers");
            if (markers.has("teams")) {
                if (!markers.get("teams").isJsonObject()) throw bad("markers.teams must be an object");
                var all = markers.getAsJsonObject("teams");
                if (all.size() > MAX_TEAMS) throw bad("at most " + MAX_TEAMS + " teams");
                for (var entry : all.entrySet()) {
                    String team = entry.getKey(), where = "markers.teams." + team;
                    if (!NAME.matcher(team).matches()) throw bad("team name " + team);
                    if (!entry.getValue().isJsonObject()) throw bad(where + " must be an object");
                    var o = entry.getValue().getAsJsonObject();
                    var pads = positions(o.get("pads"), where + ".pads", size, MAX_PADS);
                    if (pads.isEmpty()) throw bad(where + " needs at least one pad");
                    int[] entryCell = o.has("entry") ? inside(triple(o.get("entry"), where + ".entry"), size, where + ".entry") : null;
                    teams.put(team, new Team(List.copyOf(pads), entryCell));
                }
            }
            if (markers.has("mob_spawns")) spawns.addAll(positions(markers.get("mob_spawns"), "markers.mob_spawns", size, MAX_SPAWNS));
            if (markers.has("spectator")) spectator = inside(triple(markers.get("spectator"), "markers.spectator"), size, "markers.spectator");
        }
        int maxPads = teams.values().stream().mapToInt(t -> t.pads().size()).max().orElse(0);
        int capTeams = teams.size(), capPer = maxPads, capMobs = 64;
        if (plan.has("capacity")) {
            if (!plan.get("capacity").isJsonObject()) throw bad("capacity must be an object");
            var c = plan.getAsJsonObject("capacity");
            if (c.has("teams")) capTeams = ranged(c.get("teams"), "capacity.teams", 0, MAX_TEAMS);
            if (c.has("per_team")) capPer = ranged(c.get("per_team"), "capacity.per_team", 0, MAX_PADS);
            if (c.has("mobs")) capMobs = ranged(c.get("mobs"), "capacity.mobs", 0, 512);
        }
        return new ArenaPlan(name, size[0], size[1], size[2], List.copyOf(fills),
                java.util.Collections.unmodifiableMap(teams), List.copyOf(spawns), spectator, new Capacity(capTeams, capPer, capMobs));
    }

    public long totalCells() {
        long n = 0;
        for (var fill : fills) n += fill.volume();
        return n;
    }

    public ArenaBox box(int[] origin) { return ArenaBox.of(origin, new int[] {sx, sy, sz}); }

    // ---- JSON helpers (shared with the registry and the request parsers)

    static String text(JsonObject o, String key) {
        var e = o.get(key);
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) throw bad(key + " must be text");
        return e.getAsString();
    }

    static int number(JsonElement e, String where) {
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) throw bad(where + " must be a whole number");
        double v = e.getAsDouble();
        if (v != Math.rint(v) || Math.abs(v) > Integer.MAX_VALUE) throw bad(where + " must be a whole number");
        return (int) v;
    }

    static int ranged(JsonElement e, String where, int lo, int hi) {
        int v = number(e, where);
        if (v < lo || v > hi) throw bad(where + " must be " + lo + ".." + hi);
        return v;
    }

    static int[] triple(JsonElement e, String where) {
        if (e == null || !e.isJsonArray() || e.getAsJsonArray().size() != 3) throw bad(where + " must be [x,y,z]");
        JsonArray a = e.getAsJsonArray();
        return new int[] {number(a.get(0), where), number(a.get(1), where), number(a.get(2), where)};
    }

    private static int[] inside(int[] cell, int[] size, String where) {
        for (int a = 0; a < 3; a++) if (cell[a] < 0 || cell[a] >= size[a]) throw bad(where + " lies outside the size");
        return cell;
    }

    private static List<int[]> positions(JsonElement e, String where, int[] size, int max) {
        if (e == null || !e.isJsonArray() || e.getAsJsonArray().size() > max) throw bad(where + " must be a list of at most " + max + " [x,y,z]");
        var out = new ArrayList<int[]>();
        var a = e.getAsJsonArray();
        for (int i = 0; i < a.size(); i++) out.add(inside(triple(a.get(i), where + "[" + i + "]"), size, where + "[" + i + "]"));
        return out;
    }
}
