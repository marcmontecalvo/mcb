package com.vulpine.mcb.arena;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * The arena ops' requests (pure, checked by ArenaLogicTest). Structured fields only, never a command string; an unknown
 * field is refused. Every request also carries the bridge envelope and may carry a {@code request_id} the answer echoes.
 */
public final class ArenaRequests {
    private ArenaRequests() {}

    static final Set<String> ENVELOPE = Set.of("schema_version", "run_id", "episode_id", "body_id", "sequence", "op", "request_id");
    public static final int MAX_WAVES = 50, MAX_PER_TYPE = 64, MAX_PER_WAVE = 256, MAX_DELAY = 72_000, MAX_PARTICIPANTS = 64;
    /** A dragon takes the world apart; everything else (a wither included) is the arena's to contain. */
    static final Set<String> FORBIDDEN_MOBS = Set.of("minecraft:ender_dragon");
    private static final Pattern ID = Pattern.compile("[a-z0-9_.-]+:[a-z0-9_./-]+");
    private static final Pattern PLAYER = Pattern.compile("[A-Za-z0-9_]{1,16}");

    public record Build(ArenaPlan plan, int[] origin, String name, String dimension) {}

    public record Remove(String name, boolean restore) {}

    /** One wave: mob type to count (in the order given) and the ticks to wait before it spawns. */
    public record Wave(Map<String, Integer> mobs, int delay) {
        public int total() { return mobs.values().stream().mapToInt(Integer::intValue).sum(); }
    }

    /**
     * participants: player names, or null for every player inside the bounds; teams: an explicit team to names map, or
     * null; exit: the cell participants still in the arena are sent to when the match ends (null: they stay).
     */
    public record Start(String name, String mode, List<Wave> waves, List<String> participants, Map<String, List<String>> teams,
                        int[] exit) {}

    /** Give or take away a player's operator status. */
    public record Op(String player, boolean op) {}

    /** The same kit for each player: the trial op's give, clear_inventory, heal and wear (TrialLogic reads it). */
    public record Kit(List<String> players, JsonObject kit) {}

    static final Set<String> KIT_FIELDS = Set.of("give", "clear_inventory", "heal", "wear");

    private static ArenaFail bad(String detail) { return new ArenaFail("bad_request", detail); }

    private static void only(JsonObject request, Set<String> fields) {
        for (var key : request.keySet()) if (!fields.contains(key) && !ENVELOPE.contains(key)) throw bad("unknown field " + key);
    }

    private static String text(JsonObject o, String key) {
        var e = o.get(key);
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) throw bad(key + " must be text");
        return e.getAsString();
    }

    private static String name(JsonObject o) {
        String name = text(o, "name");
        if (!ArenaPlan.NAME.matcher(name).matches()) throw bad("name must match " + ArenaPlan.NAME.pattern());
        return name;
    }

    private static int whole(JsonElement e, String where, int lo, int hi) {
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) throw bad(where + " must be a whole number");
        double v = e.getAsDouble();
        if (v != Math.rint(v) || v < lo || v > hi) throw bad(where + " must be " + lo + ".." + hi);
        return (int) v;
    }

    private static boolean flag(JsonObject o, String key) {
        if (!o.has(key)) return false;
        var e = o.get(key);
        if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isBoolean()) throw bad(key + " must be true or false");
        return e.getAsBoolean();
    }

    private static int[] cell(JsonElement e, String where) {
        if (e == null || !e.isJsonArray() || e.getAsJsonArray().size() != 3) throw bad(where + " must be [x,y,z]");
        var cell = new int[3];
        for (int i = 0; i < 3; i++) cell[i] = whole(e.getAsJsonArray().get(i), where, -30_000_000, 30_000_000);
        return cell;
    }

    public static Build parseBuild(JsonObject request) {
        only(request, Set.of("plan", "origin", "name", "dimension"));
        if (!request.has("plan") || !request.get("plan").isJsonObject()) throw bad("plan must be the plan object");
        var plan = ArenaPlan.parse(request.getAsJsonObject("plan"));
        var cell = cell(request.get("origin"), "origin");
        String name = request.has("name") ? name(request) : plan.name();
        String dimension = "minecraft:overworld";
        if (request.has("dimension")) {
            dimension = text(request, "dimension");
            if (!ID.matcher(dimension).matches()) throw bad("dimension must be a namespaced id");
        }
        return new Build(plan, cell, name, dimension);
    }

    /** A request that names an arena and nothing else (status, stop). */
    public static String parseName(JsonObject request) {
        only(request, Set.of("name"));
        return name(request);
    }

    public static Remove parseRemove(JsonObject request) {
        only(request, Set.of("name", "restore"));
        return new Remove(name(request), flag(request, "restore"));
    }

    /**
     * A match: waves (co-op: waves required, no teams), teams (team against team, waves optional) or race (teams race to
     * kill the waves: waves required).
     */
    public static Start parseStart(JsonObject request) {
        only(request, Set.of("name", "mode", "waves", "participants", "teams", "exit"));
        String name = name(request), mode = text(request, "mode");
        if (!ArenaLogic.MODES.contains(mode)) throw bad("mode must be waves, teams or race");
        if (!ArenaLogic.sided(mode) && request.has("teams")) throw bad("teams belong to the teams and race modes");
        var waves = new ArrayList<Wave>();
        boolean optional = mode.equals("teams");
        if (request.has("waves") || !optional) {
            if (!request.has("waves") || !request.get("waves").isJsonArray()) throw bad("waves must be a list of waves");
            var list = request.getAsJsonArray("waves");
            if ((list.isEmpty() && !optional) || list.size() > MAX_WAVES) throw bad("waves must hold 1.." + MAX_WAVES + " waves");
            for (int i = 0; i < list.size(); i++) waves.add(wave(list.get(i), "waves[" + i + "]"));
        }
        int[] exit = request.has("exit") ? cell(request.get("exit"), "exit") : null;
        List<String> participants = null;
        Map<String, List<String>> teams = null;
        if (request.has("participants")) participants = players(request.get("participants"), "participants");
        if (request.has("teams")) {
            if (participants != null) throw bad("give participants or teams, not both");
            if (!request.get("teams").isJsonObject()) throw bad("teams must map a team to its players");
            teams = new LinkedHashMap<>();
            var flat = new ArrayList<String>();
            for (var entry : request.getAsJsonObject("teams").entrySet()) {
                if (!ArenaPlan.NAME.matcher(entry.getKey()).matches()) throw bad("team name " + entry.getKey());
                var names = entry.getValue().isJsonArray() && entry.getValue().getAsJsonArray().isEmpty() ? List.<String>of()
                        : players(entry.getValue(), "teams." + entry.getKey());
                teams.put(entry.getKey(), names);
                flat.addAll(names);
            }
            participants = flat;
        }
        return new Start(name, mode, List.copyOf(waves), participants == null ? null : List.copyOf(participants), teams, exit);
    }

    /** The world spawn: where new players and everyone without a bed appear (the arena server's lobby). */
    public static int[] parseSpawn(JsonObject request) {
        only(request, Set.of("position"));
        return cell(request.get("position"), "position");
    }

    /** {@code operator}: true gives operator status, false takes it away (the envelope's own {@code op} names the op). */
    public static Op parseOp(JsonObject request) {
        only(request, Set.of("player", "operator"));
        var player = request.get("player");
        if (player == null || !player.isJsonPrimitive() || !PLAYER.matcher(player.getAsString()).matches()) throw bad("player must be a player name");
        if (!request.has("operator")) throw bad("operator must be true or false");
        return new Op(player.getAsString(), flag(request, "operator"));
    }

    public static Kit parseKit(JsonObject request) {
        only(request, Set.of("players", "kit"));
        var players = players(request.get("players"), "players");
        if (!request.has("kit") || !request.get("kit").isJsonObject()) throw bad("kit must be an object");
        var kit = request.getAsJsonObject("kit");
        for (var key : kit.keySet()) if (!KIT_FIELDS.contains(key)) throw bad("a kit has no " + key + " (only " + KIT_FIELDS + ")");
        return new Kit(List.copyOf(players), kit.deepCopy());
    }

    private static Wave wave(JsonElement e, String where) {
        if (!e.isJsonObject()) throw bad(where + " must be an object");
        var o = e.getAsJsonObject();
        for (var key : o.keySet()) if (!key.equals("mobs") && !key.equals("delay_ticks")) throw bad(where + " has unknown field " + key);
        if (!o.has("mobs") || !o.get("mobs").isJsonObject() || o.getAsJsonObject("mobs").isEmpty()) throw bad(where + ".mobs must name mob types and counts");
        var mobs = new LinkedHashMap<String, Integer>();
        int total = 0;
        for (var mob : o.getAsJsonObject("mobs").entrySet()) {
            if (!ID.matcher(mob.getKey()).matches()) throw bad(where + ".mobs " + mob.getKey() + " is not a namespaced id");
            if (FORBIDDEN_MOBS.contains(mob.getKey())) throw new ArenaFail("not_allowed", mob.getKey());
            int count = whole(mob.getValue(), where + ".mobs." + mob.getKey(), 1, MAX_PER_TYPE);
            total += count;
            mobs.put(mob.getKey(), count);
        }
        if (total > MAX_PER_WAVE) throw bad(where + " holds more than " + MAX_PER_WAVE + " mobs");
        int delay = o.has("delay_ticks") ? whole(o.get("delay_ticks"), where + ".delay_ticks", 0, MAX_DELAY) : 0;
        return new Wave(java.util.Collections.unmodifiableMap(mobs), delay);
    }

    private static List<String> players(JsonElement e, String where) {
        if (e == null || !e.isJsonArray() || e.getAsJsonArray().isEmpty() || e.getAsJsonArray().size() > MAX_PARTICIPANTS)
            throw bad(where + " must be a list of 1.." + MAX_PARTICIPANTS + " player names");
        var out = new ArrayList<String>();
        for (var n : e.getAsJsonArray()) {
            if (!n.isJsonPrimitive() || !n.getAsJsonPrimitive().isString() || !PLAYER.matcher(n.getAsString()).matches())
                throw bad(where + " holds a bad player name");
            out.add(n.getAsString());
        }
        return out;
    }
}
