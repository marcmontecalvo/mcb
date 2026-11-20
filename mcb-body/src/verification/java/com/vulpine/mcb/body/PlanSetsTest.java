package com.vulpine.mcb.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.zip.Deflater;

/**
 * Build-wide edit sets (protected, costly) are uploaded once, content-addressed, and a
 * plan names them (house-large run 20261119T133618892664Z: 28 KB per plan_route).
 */
public final class PlanSetsTest {
    private record Key(int x, int y, int z) { }
    private static int checks;
    private static void check(boolean yes, String label) { checks++; if (!yes) throw new AssertionError(label); }
    private static void rejects(Runnable r, String reason, String label) {
        try { r.run(); } catch (IllegalArgumentException e) {
            check(reason.equals(e.getMessage()), label + ": " + e.getMessage()); return;
        }
        throw new AssertionError(label + ": accepted");
    }

    /** Uploads one set the way planner_session does; returns its id (the sha256 of its JSON). */
    static String upload(PlanSets sets, String field, JsonArray entries) throws Exception {
        var o = new JsonObject(); o.addProperty("field", field); o.add("entries", entries);
        byte[] raw = o.toString().getBytes(StandardCharsets.UTF_8);
        var deflater = new Deflater(); deflater.setInput(raw); deflater.finish();
        byte[] buffer = new byte[raw.length + 64]; int n = deflater.deflate(buffer); deflater.end();
        String encoded = Base64.getEncoder().encodeToString(java.util.Arrays.copyOf(buffer, n));
        String id = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
        sets.begin(id, encoded.length(), raw.length, id);
        for (int at = 0; at < encoded.length(); at += 10_000)
            sets.append(id, at, encoded.substring(at, Math.min(encoded.length(), at + 10_000)));
        sets.commit(id);
        return id;
    }

    private static JsonArray cells(Iterable<int[]> points) {
        var a = new JsonArray();
        for (var p : points) { var c = new JsonArray(); for (int v : p) c.add(v); a.add(c); }
        return a;
    }

    private static JsonObject edits(String extra) {
        return JsonParser.parseString("""
                {"step":true,"break_costs":{"minecraft:stone":16},"fall_costs":{},
                "allow_place":false,"allow_bridge":false,"allow_pillar":false,
                "max_stack":1,"break_budget":64,"place_budget":64,"place_supply":3,
                "protected_blocks":[],"materials":["minecraft:cobblestone"]""" + extra + "}").getAsJsonObject();
    }

    public static void main(String[] args) throws Exception {
        var sets = new PlanSets();
        // A house-large shell: thousands of protected cells, uploaded once.
        var house = new java.util.ArrayList<int[]>();
        for (int x = 0; x < 25; x++) for (int y = 0; y < 12; y++) for (int z = 0; z < 19; z++)
            if (x == 0 || x == 24 || y == 0 || y == 11 || z == 0 || z == 18) house.add(new int[]{1184 + x, 71 + y, -823 + z});
        check(house.size() > 1500, "house-sized set");
        String protectedId = upload(sets, "protected", cells(house));
        var costly = new JsonArray();
        var c0 = new JsonArray(); c0.add(1184); c0.add(79); c0.add(-823); c0.add(300.0); costly.add(c0);
        String costlyId = upload(sets, "costly", costly);

        // Reference plus inline delta resolves to their union; the reference keys are gone.
        var resolved = sets.resolve(edits(",\"protected\":[[3,1,0]],\"protected_set\":\"" + protectedId
                + "\",\"costly_set\":\"" + costlyId + "\""));
        check(!resolved.has("protected_set") && !resolved.has("costly_set"), "references resolved away");
        var policy = RoutePlanner.EditPolicy.fromJson(resolved);
        check(policy.protectedCells().size() == house.size() + 1, "base plus delta: " + policy.protectedCells().size());
        check(policy.protectedCells().contains(RoutePlanner.key(1184, 71, -823)), "base cell protected");
        check(policy.protectedCells().contains(RoutePlanner.key(3, 1, 0)), "inline foothold protected");
        check(policy.extraCosts().get(RoutePlanner.key(1184, 79, -823)) == 300.0, "costly cell priced");
        // Without a costly reference and inline field the request is unchanged.
        var plain = edits(",\"protected\":[[3,1,0]]");
        check(sets.resolve(plain).equals(plain), "requests without references pass through");

        // An unknown reference never plans without its protection.
        rejects(() -> sets.resolve(edits(",\"protected\":[],\"protected_set\":\"" + "0".repeat(64) + "\"")),
                "plan_set_unknown", "unknown set");
        // A set is only good for the field it was uploaded for.
        rejects(() -> sets.resolve(edits(",\"protected\":[],\"protected_set\":\"" + costlyId + "\"")),
                "plan_set_field", "costly set used as protected");
        // The id is the content digest: a mislabelled upload is refused.
        rejects(() -> sets.begin("a".repeat(64), 10, 10, "b".repeat(64)), "plan_set_id", "id is the digest");
        // A corrupt upload never lands in the store.
        sets.begin("c".repeat(64), 4, 3, "c".repeat(64)); sets.append("c".repeat(64), 0, "AAAA");
        try { sets.commit("c".repeat(64)); throw new AssertionError("corrupt set accepted"); }
        catch (IllegalArgumentException expected) { checks++; }
        rejects(() -> sets.resolve(edits(",\"protected\":[],\"protected_set\":\"" + "c".repeat(64) + "\"")),
                "plan_set_unknown", "corrupt set not stored");

        // Bounded store: the oldest unused set goes first; one in use stays.
        var bounded = new PlanSets();
        String keep = upload(bounded, "protected", cells(List.of(new int[]{0, 0, 0})));
        String first = upload(bounded, "protected", cells(List.of(new int[]{1, 0, 0})));
        for (int i = 2; i < PlanSets.MAX_SETS + 1; i++) {
            bounded.resolve(edits(",\"protected_set\":\"" + keep + "\""));
            upload(bounded, "protected", cells(List.of(new int[]{i, 0, 0})));
        }
        check(bounded.size() == PlanSets.MAX_SETS, "store is bounded: " + bounded.size());
        bounded.resolve(edits(",\"protected_set\":\"" + keep + "\""));
        rejects(() -> bounded.resolve(edits(",\"protected_set\":\"" + first + "\"")), "plan_set_unknown", "evicted set");

        // Protection is honoured end to end: a sealed room whose walls are protected only
        // through the uploaded set is not broken out of.
        var room = new HashMap<Key, Map<String, Object>>();
        var wall = new java.util.ArrayList<int[]>();
        var stone = Map.<String, Object>of("block", "minecraft:stone", "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1)), "fluid", "minecraft:empty");
        var air = Map.<String, Object>of("block", "minecraft:air", "collision_boxes", List.of(), "fluid", "minecraft:empty");
        for (int x = -4; x <= 6; x++) for (int z = -4; z <= 4; z++) for (int y = -1; y <= 4; y++) {
            boolean shell = y <= 0 || y >= 3 || Math.abs(x) == 1 && Math.abs(z) <= 1 || Math.abs(z) == 1 && Math.abs(x) <= 1;
            room.put(new Key(x, y, z), shell ? stone : air);
            if (shell) wall.add(new int[]{x, y, z});
        }
        String roomId = upload(sets, "protected", cells(wall));
        var sealed = RoutePlanner.EditPolicy.fromJson(sets.resolve(edits(",\"protected\":[],\"protected_set\":\"" + roomId + "\"")));
        var res = new RoutePlanner((x, y, z) -> room.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(3, 1, 0), 0, 3, true).withMoves(false, true).withEdits(sealed).search(50_000);
        check(!res.status().equals("reached") && res.steps().isEmpty(), "protected set keeps the walls whole: " + res.status());
        var open = RoutePlanner.EditPolicy.fromJson(edits(",\"protected\":[]"));
        var broke = new RoutePlanner((x, y, z) -> room.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(3, 1, 0), 0, 3, true).withMoves(false, true).withEdits(open).search(50_000);
        check(broke.status().equals("reached"), "the same room without protection is broken out of: " + broke.status());
        terrain(sets);
        System.out.println("PlanSetsTest ok (" + checks + " checks)");
    }

    /** Terrain plans: policy.protected_positions (the build) and top-level protected by reference. */
    private static void terrain(PlanSets sets) throws Exception {
        // TerrainPlannerTest's cliff: six placed supports reach the top; (0,1,2) is one of them.
        var cliff = new HashMap<Key, Map<String, Object>>();
        for (int x = -2; x < 3; x++) for (int y = -1; y < 8; y++) for (int z = -1; z < 9; z++) {
            boolean solid = y < 0 || Math.abs(x) == 1 || (x == 0 && z >= 4 && y < 4);
            cliff.put(new Key(x, y, z), Map.of("block", solid ? "minecraft:bedrock" : "minecraft:air",
                    "collision_boxes", solid ? List.of(List.of(0, 0, 0, 1, 1, 1)) : List.of()));
        }
        var build = new java.util.ArrayList<int[]>();
        build.add(new int[]{0, 1, 2});
        for (int i = 0; i < 2000; i++) build.add(new int[]{1000 + i, 70, -800});
        String buildId = upload(sets, "protected_positions", cells(build));
        String footId = upload(sets, "protected", cells(List.of(new int[]{50, 1, 50})));
        var policyJson = JsonParser.parseString("{\"protected_positions\":[[60,1,60]],\"protected_positions_set\":\""
                + buildId + "\"}").getAsJsonObject();
        var resolved = sets.resolve(policyJson);
        var positions = BodyRuntime.nodes(resolved, "protected_positions");
        check(positions.size() == 2002 && positions.contains(new RoutePlanner.Node(0, 1, 2))
                && positions.contains(new RoutePlanner.Node(60, 1, 60)), "terrain policy set resolves: " + positions.size());
        var request = sets.resolve(JsonParser.parseString("{\"mode\":\"terrain\",\"protected_set\":\"" + footId + "\"}").getAsJsonObject());
        check(BodyRuntime.nodes(request, "protected").contains(new RoutePlanner.Node(50, 1, 50)), "top-level protected resolves");
        rejects(() -> sets.resolve(JsonParser.parseString("{\"protected_positions_set\":\"" + footId + "\"}").getAsJsonObject()),
                "plan_set_field", "a protected set is not a policy set");
        for (boolean guarded : new boolean[]{false, true}) {
            var policy = new TerrainPlanner.Policy(false, true, 8, 8, 0, List.of("minecraft:cobblestone", "minecraft:dirt"),
                    Set.of(), guarded ? positions : Set.of(), 3, 4, 0, false, 3, false, 3, false, false, true);
            var planner = new TerrainPlanner((x, y, z) -> cliff.get(new Key(x, y, z)), new RoutePlanner.Node(0, 0, 0),
                    new RoutePlanner.Node(0, 4, 4), 0, policy, List.of(new TerrainPlanner.Stock(3, "minecraft:cobblestone", 6)),
                    Set.of(), Set.of(), 1, 4, 24);
            TerrainPlanner.Result r; int calls = 0;
            do { r = planner.advance(1000, Long.MAX_VALUE); if (++calls > 10_000) throw new AssertionError("terrain search"); }
            while (r.status().equals("searching"));
            if (guarded) check(!r.status().equals("reached"), "set-protected support is never placed: " + r.status());
            else check(r.status().equals("reached"), "unprotected cliff is climbed: " + r.status());
        }
    }
}
