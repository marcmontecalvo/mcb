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
 * Replays one planning request: runs RoutePlanner on a map exported by
 * scripts/analysis/planner_parity.py and prints
 * {status, route, expanded, cost, ms} as JSON. Args: export.json nodeLimit [repeats].
 */
public final class RoutePlannerParity {
    public static void main(String[] args) throws Exception {
        for (String file : args[0].split(",")) run(file, args);  // several exports in one JVM
    }

    private static void run(String file, String[] args) throws Exception {
        var root = JsonParser.parseString(Files.readString(Path.of(file))).getAsJsonObject();
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
        var edits = root.has("request") ? (root.getAsJsonObject("request").has("edits")
                ? RoutePlanner.EditPolicy.fromJson(root.getAsJsonObject("request").getAsJsonObject("edits")) : null)
                : root.has("edits") ? RoutePlanner.EditPolicy.fromJson(root.getAsJsonObject("edits")) : null;
        RoutePlanner.Result result = null;
        double best = Double.POSITIVE_INFINITY;
        for (int i = 0; i < repeats; i++) {
            long began = System.nanoTime();
            RoutePlanner planner;
            if (root.has("request")) planner = live(root.getAsJsonObject("request"), cells, start, goal, edits);
            else {
                planner = new RoutePlanner((x, y, z) -> cells.get(key(x, y, z)), start, goal,
                    root.get("radius").getAsDouble(), root.get("max_drop").getAsInt(), true,
                    root.has("unknown_penalty") ? root.get("unknown_penalty").getAsDouble() : 0)
                    .withMoves(root.has("swim") && root.get("swim").getAsBoolean(),
                            root.has("swim") && root.get("swim").getAsBoolean())
                    .withGaps(root.has("max_gap") ? root.get("max_gap").getAsInt() : 0);
                if (edits != null) planner.withEdits(edits);
            }
            double h0 = planner.estimate(start);
            long fieldEnd = System.nanoTime();
            result = planner.search(limit);
            if (i == 0) { System.out.println("{\"h_start\": " + h0 + ", \"field_ms\": " + (fieldEnd - began) / 1e6 + "}"); }
            best = Math.min(best, (System.nanoTime() - began) / 1e6);
        }
        var out = new LinkedHashMap<String, Object>();
        out.put("status", result.status());
        out.put("route", result.route().stream().map(n -> List.of(n.x(), n.y(), n.z())).toList());
        out.put("expanded", result.expanded());
        out.put("cost", result.cost());
        out.put("steps", result.steps().stream().map(e -> Map.of("source", List.of(e.source().x(), e.source().y(), e.source().z()),
                "target", List.of(e.target().x(), e.target().y(), e.target().z()), "kind", e.kind(),
                "breaks", e.breaks().stream().map(p -> List.of(p[0], p[1], p[2])).toList(),
                "places", e.places().stream().map(p -> List.of(p[0], p[1], p[2])).toList())).toList());
        out.put("ms", best);
        out.put("file", file);
        System.out.println(gson.toJson(out));
    }

    /** The planner as BodyRuntime.startPlan builds it from a live plan request stored under "request". */
    private static RoutePlanner live(com.google.gson.JsonObject rq, Map<Long, Map<String, Object>> cells,
                                     RoutePlanner.Node start, RoutePlanner.Node goal, RoutePlanner.EditPolicy edits) {
        RouteGeometry.Reader base = (x, y, z) -> cells.get(key(x, y, z));
        var reader = rq.has("edits") ? RoutePermissions.edits(rq.getAsJsonObject("edits")).reader(base) : base;
        var planner = new RoutePlanner(reader, start, goal, rq.get("radius").getAsDouble(), rq.get("max_drop").getAsInt(),
                rq.get("sprint").getAsBoolean(), rq.get("unknown_penalty").getAsDouble())
                .withMoves(rq.get("swim").getAsBoolean(), rq.get("extra").getAsBoolean())
                .withPlannerSwim(!rq.has("planner_swim") || rq.get("planner_swim").getAsBoolean())
                .withClimb(rq.has("allow_climb") && rq.get("allow_climb").getAsBoolean())
                .withGaps(rq.get("max_gap").getAsInt());
        double weight = System.getenv("HW") != null ? Double.parseDouble(System.getenv("HW")) : rq.get("heuristic_weight").getAsDouble();
        if (rq.has("tick_costs")) {
            planner.withPolicy(rq.get("tick_costs").getAsBoolean(), rq.get("baritone_search").getAsBoolean(),
                    rq.get("baritone_diagonals").getAsBoolean(), rq.get("allow_diagonals").getAsBoolean(),
                    rq.get("allow_shapes").getAsBoolean(), weight);
            planner.withLegacyPricing(rq.get("break_penalty").getAsDouble(), rq.get("placement_penalty").getAsDouble(),
                    rq.get("tool_wear_penalty").getAsDouble());
        }
        if (rq.has("interact_reach")) planner.withInteraction(rq.get("interact_reach").getAsDouble());
        if (edits != null) {
            planner.withEdits(edits);
            var e = rq.getAsJsonObject("edits");
            if (e.has("falling_neighbours")) planner.withFallingNeighbours(e.get("falling_neighbours").getAsBoolean());
        }
        return planner;
    }

    private static long key(int x, int y, int z) {
        return (((long) x & 0x3FFFFF) << 42) | (((long) y & 0xFFFFF) << 22) | ((long) z & 0x3FFFFF);
    }
}
