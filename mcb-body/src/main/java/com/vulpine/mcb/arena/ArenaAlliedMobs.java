package com.vulpine.mcb.arena;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Pattern;

/**
 * Pure rules for counted allied mob groups in arena {@code teams} mode (checked by ArenaLogicTest): curated verified
 * mob allowlist, wire parsing and validation for {@code team_mobs} and {@code team_mob_kits}, kit/name override
 * precedence, capacity and active-team checks (mixed and mob-only teams), targeting/friendly-fire rules, and active
 * count merging for elimination and draws.
 */
public final class ArenaAlliedMobs {
    private ArenaAlliedMobs() {}

    /** Curated allowlist of allied mob types with verified targeting, friendly-fire, kill credit, and spawn behavior. */
    public static final Set<String> SUPPORTED_TYPES = Set.of(
            "minecraft:zombie",
            "minecraft:husk",
            "minecraft:drowned",
            "minecraft:skeleton",
            "minecraft:stray",
            "minecraft:wither_skeleton",
            "minecraft:spider",
            "minecraft:cave_spider",
            "minecraft:pillager",
            "minecraft:vindicator",
            "minecraft:witch",
            "minecraft:iron_golem",
            "minecraft:wolf",
            "minecraft:piglin_brute",
            "minecraft:blaze",
            "minecraft:breeze");

    /** Undead allied mob types that burn in open daylight unless wearing headgear. */
    private static final Set<String> SUNLIGHT_UNDEAD = Set.of(
            "minecraft:zombie",
            "minecraft:drowned",
            "minecraft:skeleton",
            "minecraft:stray");

    public static final int MAX_GROUP_COUNT = 32;
    public static final int MAX_GROUPS_PER_TEAM = 32;
    public static final int MAX_NAME_LENGTH = 32;
    public static final int MAX_GIVE = 36;
    public static final int MAX_GIVE_COUNT = 256;
    public static final int MAX_ENCHANTMENTS = 4;
    public static final int MAX_ENCHANTMENT_LEVEL = 10;

    static final Set<String> GROUP_FIELDS = Set.of("type", "count", "name", "kit", "overrides");
    static final Set<String> OVERRIDE_FIELDS = Set.of("index", "name", "kit");
    static final Set<String> KIT_FIELDS = Set.of("give", "clear_inventory", "heal", "wear");
    static final Set<String> GIVE_FIELDS = Set.of("item", "count", "enchantments", "potion");
    private static final Pattern ID = Pattern.compile("[a-z0-9_.-]+:[a-z0-9_./-]+");

    /** One item entry in a structured mob kit (mirrors TrialLogic.Give). */
    public record Give(String item, int count, Map<String, Integer> enchantments, String potion) {}

    /** A structured mob kit ({@code give}, {@code clear_inventory}, {@code heal}, {@code wear}). */
    public record MobKit(List<Give> give, boolean clearInventory, boolean heal, boolean wear) {}

    /** Per-index name and/or kit override inside an allied mob group. */
    public record MemberOverride(int index, String name, MobKit kit) {}

    /** A counted group of allied mobs on one team. */
    public record Group(String type, int count, String name, MobKit kit, List<MemberOverride> overrides) {}

    /** One expanded allied mob to spawn, with its final resolved name and kit. */
    public record MemberSpec(String team, int groupIndex, int memberIndex, String type, String customName,
                             MobKit effectiveKit) {}

    /** Entity role inside a running arena match. */
    public enum Affiliation { PLAYER, ALLIED_MOB, WAVE_MOB, OUTSIDER }

    /** A live combatant considered during mob retargeting. */
    public record Combatant(int index, Affiliation kind, String team, double x, double y, double z, boolean live) {}

    public static boolean needsDaylightHelmet(String type) {
        return type != null && SUNLIGHT_UNDEAD.contains(type);
    }

    // ---- parsing

    private static ArenaFail bad(String detail) { return new ArenaFail("bad_request", detail); }

    private static String text(JsonObject o, String key, String where) {
        var e = o.get(key);
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString())
            throw bad(where + " must be text");
        return e.getAsString();
    }

    private static int whole(JsonElement e, String where, int lo, int hi) {
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber())
            throw bad(where + " must be a whole number");
        double v = e.getAsDouble();
        if (v != Math.rint(v) || v < lo || v > hi) throw bad(where + " must be " + lo + ".." + hi);
        return (int) v;
    }

    private static boolean flag(JsonObject o, String key, String where) {
        if (!o.has(key)) return false;
        var e = o.get(key);
        if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isBoolean())
            throw bad(where + "." + key + " must be true or false");
        return e.getAsBoolean();
    }

    private static String mobName(JsonObject o, String key, String where) {
        String s = text(o, key, where);
        if (s.isEmpty() || s.length() > MAX_NAME_LENGTH || s.isBlank())
            throw bad(where + " must be 1.." + MAX_NAME_LENGTH + " non-blank characters");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c < 0x20 || c == 0x7f) throw bad(where + " holds a control character");
        }
        return s;
    }

    public static Map<String, List<Group>> parseTeamMobs(JsonElement element) {
        if (element == null || !element.isJsonObject()) throw bad("team_mobs must map a team to a list of mob groups");
        var out = new LinkedHashMap<String, List<Group>>();
        for (var entry : element.getAsJsonObject().entrySet()) {
            String team = entry.getKey();
            if (!ArenaPlan.NAME.matcher(team).matches()) throw bad("team name " + team);
            String teamWhere = "team_mobs." + team;
            if (!entry.getValue().isJsonArray()) throw bad(teamWhere + " must be a list of mob groups");
            var arr = entry.getValue().getAsJsonArray();
            if (arr.size() > MAX_GROUPS_PER_TEAM)
                throw bad(teamWhere + " holds more than " + MAX_GROUPS_PER_TEAM + " groups");
            var groups = new ArrayList<Group>(arr.size());
            for (int i = 0; i < arr.size(); i++) groups.add(parseGroup(arr.get(i), teamWhere + "[" + i + "]"));
            out.put(team, List.copyOf(groups));
        }
        return Collections.unmodifiableMap(out);
    }

    private static Group parseGroup(JsonElement element, String where) {
        if (element == null || !element.isJsonObject()) throw bad(where + " must be an object");
        var o = element.getAsJsonObject();
        for (var key : o.keySet()) if (!GROUP_FIELDS.contains(key)) throw bad(where + " has unknown field " + key);
        String type = text(o, "type", where + ".type");
        if (!ID.matcher(type).matches()) throw bad(where + ".type " + type + " is not a namespaced id");
        if (!SUPPORTED_TYPES.contains(type)) throw new ArenaFail("unsupported_mob", type + " is not a supported allied mob");
        int count = whole(o.get("count"), where + ".count", 1, MAX_GROUP_COUNT);
        String name = o.has("name") ? mobName(o, "name", where + ".name") : null;
        MobKit kit = o.has("kit") ? parseKit(o.get("kit"), where + ".kit") : null;
        var overrides = new ArrayList<MemberOverride>();
        if (o.has("overrides")) {
            var ov = o.get("overrides");
            if (!ov.isJsonArray()) throw bad(where + ".overrides must be a list");
            var seenIndices = new LinkedHashSet<Integer>();
            var list = ov.getAsJsonArray();
            for (int i = 0; i < list.size(); i++) {
                String ovWhere = where + ".overrides[" + i + "]";
                if (!list.get(i).isJsonObject()) throw bad(ovWhere + " must be an object");
                var oo = list.get(i).getAsJsonObject();
                for (var key : oo.keySet())
                    if (!OVERRIDE_FIELDS.contains(key)) throw bad(ovWhere + " has unknown field " + key);
                int idx = whole(oo.get("index"), ovWhere + ".index", 0, count - 1);
                if (!seenIndices.add(idx)) throw bad(ovWhere + ".index " + idx + " is duplicated");
                String oName = oo.has("name") ? mobName(oo, "name", ovWhere + ".name") : null;
                MobKit oKit = oo.has("kit") ? parseKit(oo.get("kit"), ovWhere + ".kit") : null;
                overrides.add(new MemberOverride(idx, oName, oKit));
            }
        }
        return new Group(type, count, name, kit, List.copyOf(overrides));
    }

    public static Map<String, MobKit> parseTeamMobKits(JsonElement element) {
        if (element == null || !element.isJsonObject()) throw bad("team_mob_kits must map a team to a kit object");
        var out = new LinkedHashMap<String, MobKit>();
        for (var entry : element.getAsJsonObject().entrySet()) {
            String team = entry.getKey();
            if (!ArenaPlan.NAME.matcher(team).matches()) throw bad("team name " + team);
            out.put(team, parseKit(entry.getValue(), "team_mob_kits." + team));
        }
        return Collections.unmodifiableMap(out);
    }

    public static MobKit parseKit(JsonElement element, String where) {
        if (element == null || !element.isJsonObject()) throw bad(where + " must be an object");
        var o = element.getAsJsonObject();
        for (var key : o.keySet())
            if (!KIT_FIELDS.contains(key)) throw bad(where + " has no " + key + " (only " + KIT_FIELDS + ")");
        var gives = new ArrayList<Give>();
        if (o.has("give")) {
            var giveEl = o.get("give");
            if (!giveEl.isJsonArray() || giveEl.getAsJsonArray().size() > MAX_GIVE)
                throw bad(where + ".give must be a list of at most " + MAX_GIVE + " items");
            var arr = giveEl.getAsJsonArray();
            for (int i = 0; i < arr.size(); i++) {
                String gWhere = where + ".give[" + i + "]";
                if (!arr.get(i).isJsonObject()) throw bad(gWhere + " must be an object");
                var go = arr.get(i).getAsJsonObject();
                for (var key : go.keySet())
                    if (!GIVE_FIELDS.contains(key)) throw bad(gWhere + " has unknown field " + key);
                String item = text(go, "item", gWhere + ".item");
                if (!ID.matcher(item).matches()) throw bad(gWhere + ".item " + item + " is not a namespaced id");
                int count = go.has("count") ? whole(go.get("count"), gWhere + ".count", 1, MAX_GIVE_COUNT) : 1;
                var ench = new TreeMap<String, Integer>();
                if (go.has("enchantments")) {
                    var map = go.get("enchantments");
                    if (!map.isJsonObject() || map.getAsJsonObject().size() > MAX_ENCHANTMENTS)
                        throw bad(gWhere + ".enchantments must be an object of at most " + MAX_ENCHANTMENTS + " entries");
                    for (var lv : map.getAsJsonObject().entrySet()) {
                        if (!ID.matcher(lv.getKey()).matches())
                            throw bad(gWhere + ".enchantments " + lv.getKey() + " is not a namespaced id");
                        ench.put(lv.getKey(), whole(lv.getValue(), gWhere + ".enchantments." + lv.getKey(), 1, MAX_ENCHANTMENT_LEVEL));
                    }
                }
                String potion = null;
                if (go.has("potion")) {
                    potion = text(go, "potion", gWhere + ".potion");
                    if (!ID.matcher(potion).matches())
                        throw bad(gWhere + ".potion " + potion + " is not a namespaced id");
                }
                gives.add(new Give(item, count, Collections.unmodifiableMap(ench), potion));
            }
        }
        return new MobKit(List.copyOf(gives), flag(o, "clear_inventory", where), flag(o, "heal", where),
                flag(o, "wear", where));
    }

    // ---- resolution and capacity checks

    public static int totalCount(Map<String, List<Group>> teamMobs) {
        if (teamMobs == null || teamMobs.isEmpty()) return 0;
        int total = 0;
        for (var groups : teamMobs.values()) {
            for (var g : groups) total += g.count();
        }
        return total;
    }

    public static Map<String, Integer> countByTeam(Map<String, List<Group>> teamMobs) {
        var out = new LinkedHashMap<String, Integer>();
        if (teamMobs == null) return out;
        for (var entry : teamMobs.entrySet()) {
            int sum = 0;
            for (var g : entry.getValue()) sum += g.count();
            out.put(entry.getKey(), sum);
        }
        return out;
    }

    /** Expands every group into individual spawn specs with precedence {@code override.kit > group.kit > team_mob_kits[team]}. */
    public static List<MemberSpec> resolveMembers(Map<String, List<Group>> teamMobs, Map<String, MobKit> teamMobKits) {
        if (teamMobs == null || teamMobs.isEmpty()) return List.of();
        var kits = teamMobKits == null ? Map.<String, MobKit>of() : teamMobKits;
        var out = new ArrayList<MemberSpec>();
        for (var entry : teamMobs.entrySet()) {
            String team = entry.getKey();
            MobKit teamDefault = kits.get(team);
            var groups = entry.getValue();
            for (int gi = 0; gi < groups.size(); gi++) {
                var group = groups.get(gi);
                var byIndex = new HashMap<Integer, MemberOverride>();
                for (var ov : group.overrides()) byIndex.put(ov.index(), ov);
                for (int mi = 0; mi < group.count(); mi++) {
                    var ov = byIndex.get(mi);
                    String name = (ov != null && ov.name() != null) ? ov.name() : group.name();
                    MobKit kit = (ov != null && ov.kit() != null) ? ov.kit()
                            : (group.kit() != null ? group.kit() : teamDefault);
                    out.add(new MemberSpec(team, gi, mi, group.type(), name, kit));
                }
            }
        }
        return List.copyOf(out);
    }

    /**
     * Checks team existence, total allied mob capacity ({@code over_capacity} naming {@code mobs}), and that at least
     * two teams are active (each having {@code >= 1} player OR {@code >= 1} allied mob).
     */
    public static void checkConfig(Set<String> arenaTeams, Map<String, List<String>> playerAssignment,
                                   Map<String, List<Group>> teamMobs, Map<String, MobKit> teamMobKits,
                                   int mobCapacity) {
        if (teamMobs != null) {
            for (var team : teamMobs.keySet()) {
                if (!arenaTeams.contains(team)) throw new ArenaFail("unknown_team", team);
            }
        }
        if (teamMobKits != null) {
            for (var team : teamMobKits.keySet()) {
                if (!arenaTeams.contains(team)) throw new ArenaFail("unknown_team", team);
            }
        }
        int total = totalCount(teamMobs);
        if (total > mobCapacity) {
            throw new ArenaFail("over_capacity",
                    "mobs: " + total + " allied mobs exceed arena capacity " + mobCapacity);
        }
        var mobCounts = countByTeam(teamMobs);
        var active = new LinkedHashSet<String>();
        if (playerAssignment != null) {
            for (var entry : playerAssignment.entrySet()) {
                if (!entry.getValue().isEmpty()) active.add(entry.getKey());
            }
        }
        for (var entry : mobCounts.entrySet()) {
            if (entry.getValue() > 0) active.add(entry.getKey());
        }
        if (active.size() < 2) {
            throw new ArenaFail("not_enough_teams",
                    "a match needs at least two active teams (players or allied mobs)");
        }
    }

    // ---- targeting, friendly fire, retargeting and active counts

    /** Whether {@code mobKind} (on {@code mobTeam}) may target {@code targetKind} (on {@code targetTeam}). */
    public static boolean canMobTarget(Affiliation mobKind, String mobTeam, Affiliation targetKind, String targetTeam) {
        if (mobKind == Affiliation.OUTSIDER) return true;
        if (targetKind == Affiliation.OUTSIDER) return false;
        if (mobKind == Affiliation.ALLIED_MOB) {
            if (targetKind == Affiliation.WAVE_MOB) return true;
            return targetTeam != null && !targetTeam.isEmpty() && !targetTeam.equals(mobTeam);
        }
        // WAVE_MOB: hostile to all players and allied mobs, never other wave mobs or outsiders
        return targetKind == Affiliation.PLAYER || targetKind == Affiliation.ALLIED_MOB;
    }

    /** Whether damage from {@code attackerKind} to {@code targetKind} may land in {@code mode}. */
    public static boolean allowsDamage(String mode, Affiliation attackerKind, String attackerTeam,
                                       Affiliation targetKind, String targetTeam, boolean sameEntity) {
        if (sameEntity) return true;
        if (ArenaLogic.pvp(mode)) {
            if (attackerTeam != null && !attackerTeam.isEmpty() && attackerTeam.equals(targetTeam)) {
                return false;
            }
            return true;
        }
        return !(attackerKind == Affiliation.PLAYER && targetKind == Affiliation.PLAYER);
    }

    /** Picks nearest live enemy team combatant (player or allied mob), falling back to nearest live wave mob. */
    public static int pickAlliedTarget(String mobTeam, double x, double y, double z, List<Combatant> candidates) {
        int bestEnemy = -1;
        double bestEnemySq = Double.MAX_VALUE;
        int bestWave = -1;
        double bestWaveSq = Double.MAX_VALUE;
        for (var c : candidates) {
            if (!c.live()) continue;
            double dx = c.x() - x, dy = c.y() - y, dz = c.z() - z;
            double sq = dx * dx + dy * dy + dz * dz;
            if (c.kind() == Affiliation.PLAYER || c.kind() == Affiliation.ALLIED_MOB) {
                if (c.team() != null && !c.team().isEmpty() && !c.team().equals(mobTeam) && sq < bestEnemySq) {
                    bestEnemySq = sq;
                    bestEnemy = c.index();
                }
            } else if (c.kind() == Affiliation.WAVE_MOB && sq < bestWaveSq) {
                bestWaveSq = sq;
                bestWave = c.index();
            }
        }
        return bestEnemy >= 0 ? bestEnemy : bestWave;
    }

    /** Picks nearest live player or allied mob for a neutral wave hazard. */
    public static int pickWaveTarget(double x, double y, double z, List<Combatant> candidates) {
        int best = -1;
        double bestSq = Double.MAX_VALUE;
        for (var c : candidates) {
            if (!c.live()) continue;
            if (c.kind() != Affiliation.PLAYER && c.kind() != Affiliation.ALLIED_MOB) continue;
            double dx = c.x() - x, dy = c.y() - y, dz = c.z() - z;
            double sq = dx * dx + dy * dy + dz * dz;
            if (sq < bestSq) {
                bestSq = sq;
                best = c.index();
            }
        }
        return best;
    }

    /** Adds live allied mob counts per team to active player counts (alive + returning) per team. */
    public static Map<String, Integer> mergeActive(Map<String, Integer> playerActive, Map<String, Integer> alliedAlive) {
        var out = new LinkedHashMap<String, Integer>();
        if (playerActive != null) out.putAll(playerActive);
        if (alliedAlive != null) {
            for (var entry : alliedAlive.entrySet()) {
                out.merge(entry.getKey(), Math.max(0, entry.getValue()), Integer::sum);
            }
        }
        return out;
    }

    /** Search offsets around a team pad so larger allied mob groups (up to 32) find clean standing space. */
    public static List<int[]> mobSearchOffsets() {
        var out = new ArrayList<int[]>(ArenaLogic.searchOffsets());
        for (int dy = 0; dy <= 1; dy++) {
            for (int r = 3; r <= 5; r++) {
                for (int dx = -r; dx <= r; dx++) {
                    for (int dz = -r; dz <= r; dz++) {
                        if (Math.max(Math.abs(dx), Math.abs(dz)) == r) out.add(new int[] {dx, dy, dz});
                    }
                }
            }
        }
        return out;
    }
}
