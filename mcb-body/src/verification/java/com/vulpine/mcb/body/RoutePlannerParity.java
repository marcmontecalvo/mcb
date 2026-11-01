package com.vulpine.mcb.body;

import com.google.gson.Gson;
import com.google.gson.JsonParser;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Runs RoutePlanner on a map exported by scripts/analysis/planner_parity.py and prints
 * {status, route, expanded, cost, ms} as JSON. Args: export.json nodeLimit [repeats].
 */
public final class RoutePlannerParity {
    public static void main(String[] args) throws Exception {
        var root = JsonParser.parseString(Files.readString(Path.of(args[0]))).getAsJsonObject();
        var gson = new Gson();
        List<Map<String, Object>> palette = gson.fromJson(root.get("palette"), List.class);
        var cells = new HashMap<Long, Map<String, Object>>();
        for (var row : root.getAsJsonArray("cells")) {
            var r = row.getAsJsonArray();
            cells.put(key(r.get(0).getAsInt(), r.get(1).getAsInt(), r.get(2).getAsInt()), palette.get(r.get(3).getAsInt()));
        }
        var s = root.getAsJsonArray("start"); var g = root.getAsJsonArray("goal");
        var start = new RoutePlanner.Node(s.get(0).getAsInt(), s.get(1).getAsDouble(), s.get(2).getAsInt());
        var goal = new RoutePlanner.Node(g.get(0).getAsInt(), g.get(1).getAsDouble(), g.get(2).getAsInt());
        int limit = Integer.parseInt(args[1]), repeats = args.length > 2 ? Integer.parseInt(args[2]) : 1;
        RoutePlanner.Result result = null;
        double best = Double.POSITIVE_INFINITY;
        for (int i = 0; i < repeats; i++) {
            long began = System.nanoTime();
            result = new RoutePlanner((x, y, z) -> cells.get(key(x, y, z)), start, goal,
                    root.get("radius").getAsDouble(), root.get("max_drop").getAsInt(), true,
                    root.has("unknown_penalty") ? root.get("unknown_penalty").getAsDouble() : 0)
                    .withMoves(root.has("swim") && root.get("swim").getAsBoolean(),
                            root.has("swim") && root.get("swim").getAsBoolean())
                    .withGaps(root.has("max_gap") ? root.get("max_gap").getAsInt() : 0)
                    .withEdits(root.has("edits") ? edits(root.getAsJsonObject("edits")) : null)
                    .search(limit);
            best = Math.min(best, (System.nanoTime() - began) / 1e6);
        }
        var out = new LinkedHashMap<String, Object>();
        out.put("status", result.status());
        out.put("route", result.route().stream().map(n -> List.of(n.x(), n.y(), n.z())).toList());
        out.put("expanded", result.expanded());
        out.put("cost", result.cost());
        out.put("ms", best);
        out.put("steps", result.steps().stream().map(p -> Map.of("source", node(p.source()), "target", node(p.target()),
                "kind", p.kind(), "breaks", p.breaks().stream().map(RoutePlannerParity::node).toList(),
                "places", p.places().stream().map(RoutePlannerParity::node).toList())).toList());
        System.out.println(gson.toJson(out));
    }

    private static List<Object> node(RoutePlanner.Node n) { return List.of(n.x(), n.y(), n.z()); }

    static RoutePlanner.Edits edits(com.google.gson.JsonObject e) {
        var ticks = new HashMap<String, Double>();
        e.getAsJsonObject("break_ticks").entrySet().forEach(t -> ticks.put(t.getKey(), t.getValue().getAsDouble()));
        var breakable = new java.util.HashSet<String>();
        e.getAsJsonArray("breakable").forEach(b -> breakable.add(b.getAsString()));
        var cells = new java.util.HashSet<RoutePlanner.Node>();
        e.getAsJsonArray("protected").forEach(p -> { var a = p.getAsJsonArray();
            cells.add(new RoutePlanner.Node(a.get(0).getAsInt(), a.get(1).getAsDouble(), a.get(2).getAsInt())); });
        var blocks = new java.util.HashSet<String>();
        e.getAsJsonArray("protected_blocks").forEach(b -> blocks.add(b.getAsString()));
        return new RoutePlanner.Edits(e.get("allow_break").getAsBoolean(), e.get("allow_place").getAsBoolean(),
                e.get("allow_bridge").getAsBoolean(), e.get("allow_pillar").getAsBoolean(), e.get("max_support_stack").getAsInt(),
                e.get("break_budget").getAsInt(), e.get("place_budget").getAsInt(), e.get("place_supply").getAsInt(),
                ticks, breakable, cells, blocks, e.get("material").getAsString());
    }

    private static long key(int x, int y, int z) {
        return (((long) x & 0x3FFFFF) << 42) | (((long) y & 0xFFFFF) << 22) | ((long) z & 0x3FFFFF);
    }
}
