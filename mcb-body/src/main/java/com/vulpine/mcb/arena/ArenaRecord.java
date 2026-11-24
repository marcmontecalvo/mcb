package com.vulpine.mcb.arena;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A registered arena (pure; persisted by {@link ArenaRegistry}): where it stands, its markers in local coordinates, and
 * its state. States: building (the placing job runs), built, incomplete (the server stopped mid-build; the arena stays
 * protected until it is removed), clearing (a restoring removal is in progress).
 */
public final class ArenaRecord {
    private final String name, dimension;
    private final int[] origin, size;
    private final Map<String, ArenaPlan.Team> teams;
    private final List<int[]> mobSpawns;
    private final int[] spectator;
    private final ArenaPlan.Capacity capacity;
    private String state;
    /** Chunks this arena holds force-loaded (while building or in a match); released at the next start after a crash. */
    private List<Long> forced = List.of();

    ArenaRecord(String name, String dimension, int[] origin, int[] size, Map<String, ArenaPlan.Team> teams,
                List<int[]> mobSpawns, int[] spectator, ArenaPlan.Capacity capacity, String state) {
        this.name = name; this.dimension = dimension; this.origin = origin; this.size = size; this.teams = teams;
        this.mobSpawns = mobSpawns; this.spectator = spectator; this.capacity = capacity; this.state = state;
    }

    public static ArenaRecord of(ArenaPlan plan, String name, String dimension, int[] origin) {
        return new ArenaRecord(name, dimension, origin.clone(), new int[] {plan.sx(), plan.sy(), plan.sz()}, plan.teams(),
                plan.mobSpawns(), plan.spectator(), plan.capacity(), "building");
    }

    public String name() { return name; }

    public String dimension() { return dimension; }

    public int[] origin() { return origin; }

    public int[] size() { return size; }

    public Map<String, ArenaPlan.Team> teams() { return teams; }

    public List<int[]> mobSpawns() { return mobSpawns; }

    /** Null when the plan has no spectator marker. */
    public int[] spectator() { return spectator; }

    public ArenaPlan.Capacity capacity() { return capacity; }

    public String state() { return state; }

    public void setState(String state) { this.state = state; }

    public List<Long> forced() { return forced; }

    public void setForced(List<Long> forced) { this.forced = List.copyOf(forced); }

    public ArenaBox box() { return ArenaBox.of(origin, size); }

    /** A local marker cell in world coordinates. */
    public int[] world(int[] local) {
        return new int[] {origin[0] + local[0], origin[1] + local[1], origin[2] + local[2]};
    }

    // ---- JSON

    private static JsonArray cell(int[] c) {
        var a = new JsonArray();
        for (int v : c) a.add(v);
        return a;
    }

    JsonObject toJson() {
        var o = new JsonObject();
        o.addProperty("name", name);
        o.addProperty("dimension", dimension);
        o.add("origin", cell(origin));
        o.add("size", cell(size));
        o.addProperty("state", state);
        var teamsJson = new JsonObject();
        teams.forEach((team, t) -> {
            var to = new JsonObject();
            var pads = new JsonArray();
            t.pads().forEach(p -> pads.add(cell(p)));
            to.add("pads", pads);
            if (t.entry() != null) to.add("entry", cell(t.entry()));
            teamsJson.add(team, to);
        });
        var markers = new JsonObject();
        markers.add("teams", teamsJson);
        var spawns = new JsonArray();
        mobSpawns.forEach(s -> spawns.add(cell(s)));
        markers.add("mob_spawns", spawns);
        if (spectator != null) markers.add("spectator", cell(spectator));
        o.add("markers", markers);
        var cap = new JsonObject();
        cap.addProperty("teams", capacity.teams());
        cap.addProperty("per_team", capacity.perTeam());
        cap.addProperty("mobs", capacity.mobs());
        o.add("capacity", cap);
        var held = new JsonArray();
        forced.forEach(held::add);
        o.add("forced", held);
        return o;
    }

    static ArenaRecord fromJson(JsonObject o) {
        var markers = o.getAsJsonObject("markers");
        var teams = new LinkedHashMap<String, ArenaPlan.Team>();
        for (var entry : markers.getAsJsonObject("teams").entrySet()) {
            var t = entry.getValue().getAsJsonObject();
            var pads = new ArrayList<int[]>();
            t.getAsJsonArray("pads").forEach(p -> pads.add(ints(p.getAsJsonArray())));
            teams.put(entry.getKey(), new ArenaPlan.Team(List.copyOf(pads), t.has("entry") ? ints(t.getAsJsonArray("entry")) : null));
        }
        var spawns = new ArrayList<int[]>();
        markers.getAsJsonArray("mob_spawns").forEach(s -> spawns.add(ints(s.getAsJsonArray())));
        var cap = o.getAsJsonObject("capacity");
        var record = new ArenaRecord(o.get("name").getAsString(), o.get("dimension").getAsString(),
                ints(o.getAsJsonArray("origin")), ints(o.getAsJsonArray("size")), java.util.Collections.unmodifiableMap(teams),
                List.copyOf(spawns), markers.has("spectator") ? ints(markers.getAsJsonArray("spectator")) : null,
                new ArenaPlan.Capacity(cap.get("teams").getAsInt(), cap.get("per_team").getAsInt(), cap.get("mobs").getAsInt()),
                o.get("state").getAsString());
        var held = new ArrayList<Long>();
        if (o.has("forced")) o.getAsJsonArray("forced").forEach(c -> held.add(c.getAsLong()));
        record.forced = List.copyOf(held);
        return record;
    }

    private static int[] ints(JsonArray a) {
        return new int[] {a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt()};
    }
}
