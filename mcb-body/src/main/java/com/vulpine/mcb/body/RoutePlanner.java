package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.PriorityQueue;
import java.util.Set;

/**
 * Compiled planner core, stage A (ROADMAP item 8): the land subset of mclab.route_search
 * under the production flags (tick costs, Baritone search and diagonals, shapes, no swim,
 * gaps, edits or unknown-space exits). Same costs, heuristic, partial-path rule and tail
 * cutoff, so routes match the Python planner on the same cells. Unknown cells are never
 * air or support. Not thread-safe: one search per instance.
 */
final class RoutePlanner {
    record Node(int x, double y, int z) {
        double distance(Node o) { return Math.sqrt(sq(x - o.x) + sq(y - o.y) + sq(z - o.z)); }
    }
    record Result(String status, List<Node> route, int expanded, double cost) { }

    // Baritone ActionCosts and Settings, reimplemented (same values as route_search.py).
    static final double WALK = 20 / 4.317, SPRINT = 20 / 5.612, WALK_OFF = WALK * .8,
            CENTER_AFTER_FALL = WALK - WALK_OFF, JUMP = fallTicks(1.25) - fallTicks(.25),
            COST_HEURISTIC = 3.563, JUMP_PENALTY = 2.0, SQRT2 = Math.sqrt(2);
    static final double[] COEFFICIENTS = {1.5, 2, 2.5, 3, 4, 5, 10};
    static final int MIN_DIST_PATH = 5, CUTOFF_MIN = 30, NEAR_H = 8, NEAR_V = 4;
    // route_search.DIG_RISE: an edit step breaking two stone blocks, then the ascend.
    static final double DIG_RISE = 51.63284688441047;
    static final double CUTOFF_FACTOR = .9, EPS = 1e-7, RADIUS = .3;
    private static final int[][] DIRECTIONS = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
    private static final int[][] DIAGONALS = {{1, 1}, {1, -1}, {-1, 1}, {-1, -1}};
    // mclab.grid_cache.HAZARDS (lava is excluded by its fluid).
    private static final Set<String> HAZARDS = Set.of("minecraft:fire", "minecraft:soul_fire",
            "minecraft:cactus", "minecraft:magma_block", "minecraft:campfire", "minecraft:soul_campfire",
            "minecraft:powder_snow", "minecraft:sweet_berry_bush", "minecraft:wither_rose", "minecraft:bubble_column");

    private final RouteGeometry.Reader reader;
    private final Map<Long, Map<String, Object>> cells = new HashMap<>();
    private final Map<Long, double[]> heights = new HashMap<>();
    private final Node start, goal;
    private final double radius;
    private final int maxDrop;
    private final boolean sprint;
    // planner_unknown (D3): plan through unknown space; exits priced at this penalty.
    private final double unknownPenalty;
    private final Map<Long, Boolean> unknownColumns = new HashMap<>();

    RoutePlanner(RouteGeometry.Reader reader, Node start, Node goal, double radius, int maxDrop, boolean sprint) {
        this(reader, start, goal, radius, maxDrop, sprint, 0);
    }
    /** unknownPenalty 0: known-only planning; else route_search planner_unknown with that penalty. */
    RoutePlanner(RouteGeometry.Reader reader, Node start, Node goal, double radius, int maxDrop, boolean sprint,
                 double unknownPenalty) {
        this.reader = reader; this.start = start; this.goal = goal;
        this.radius = radius; this.maxDrop = maxDrop; this.sprint = sprint; this.unknownPenalty = unknownPenalty;
    }

    static double fallTicks(double distance) {
        if (distance == 0) return 0;
        double remaining = distance;
        for (int tick = 0; ; tick++) {
            double speed = (Math.pow(.98, tick) - 1) * -3.92;
            if (remaining <= speed) return tick + remaining / speed;
            remaining -= speed;
        }
    }

    Result search(int nodeLimit) {
        if (!standable(start)) return new Result("unreachable", List.of(), 0, 0);
        var costs = new HashMap<Node, Double>();
        var previous = new HashMap<Node, Node>();
        var nodes = new ArrayList<Node>();
        // Same order as Python's heap of (f, g, exit flag, node) tuples, so ties expand alike.
        var frontier = new PriorityQueue<double[]>((a, b) -> {
            int c = Double.compare(a[0], b[0]);
            if (c == 0) c = Double.compare(a[1], b[1]);
            if (c == 0) c = Double.compare(a[3], b[3]);
            if (c != 0) return c;
            Node p = nodes.get((int) a[2]), q = nodes.get((int) b[2]);
            c = Integer.compare(p.x, q.x);
            if (c == 0) c = Double.compare(p.y, q.y);
            return c != 0 ? c : Integer.compare(p.z, q.z);
        });
        Node[] coefBest = new Node[COEFFICIENTS.length];
        double[] coefValue = new double[COEFFICIENTS.length];
        java.util.Arrays.fill(coefValue, Double.POSITIVE_INFINITY);
        costs.put(start, 0.0); nodes.add(start);
        frontier.add(new double[]{remaining(start), 0, 0, 0});
        Node best = start, exit = null;
        double exitValue = Double.POSITIVE_INFINITY;
        int expanded = 0;
        while (!frontier.isEmpty() && expanded < nodeLimit) {
            var entry = frontier.poll();
            int index = (int) entry[2];
            Node node = nodes.get(index);
            double cost = entry[1];
            if (cost != costs.get(node)) continue;
            if (entry[3] == 1)  // the cheapest way on runs through unknown space
                return trim(result("segment", node, previous, expanded, cost), costs);
            expanded++;
            double estimate = closeness(node);
            if (estimate < closeness(best)) best = node;
            for (int i = 0; i < COEFFICIENTS.length; i++) {
                double value = estimate + cost / COEFFICIENTS[i];
                if (value < coefValue[i]) { coefValue[i] = value; coefBest[i] = node; }
            }
            if (distance(node) == 0) return result("reached", node, previous, expanded, cost);
            if (unknownPenalty > 0 && !node.equals(start) && frontierNode(node)) {
                double total = cost + exitRemainder(node);
                frontier.add(new double[]{total, cost, index, 1});
                if (total < exitValue) { exitValue = total; exit = node; }
            }
            for (var move : moves(node)) {
                double total = cost + move.cost;
                if (total < costs.getOrDefault(move.target, Double.POSITIVE_INFINITY)) {
                    costs.put(move.target, total); previous.put(move.target, node);
                    nodes.add(move.target);
                    frontier.add(new double[]{total + remaining(move.target), total, nodes.size() - 1, 0});
                }
            }
        }
        Node end = exit;
        if (end == null) {
            end = best;
            for (var node : coefBest)
                if (node != null && node.distance(start) > MIN_DIST_PATH) { end = node; break; }
        }
        return trim(result(frontier.isEmpty() ? "unreachable" : "incomplete", end, previous, expanded, costs.get(end)), costs);
    }

    /** Settings.pathCutoff*: drop the last 10% of long non-goal routes. */
    private static Result trim(Result r, Map<Node, Double> costs) {
        int n = r.route.size();
        if (n < CUTOFF_MIN) return r;
        var route = r.route.subList(0, Math.max(2, (int) (n * CUTOFF_FACTOR)));
        return new Result(r.status, route, r.expanded, costs.get(route.get(route.size() - 1)));
    }

    // Partial-route estimate (route_search._remaining(node, 1)): walk rate when planning
    // through unknown space, else the Baritone heuristic.
    private double closeness(Node node) {
        return unknownPenalty > 0 ? horizontal(node) * WALK + vertical(node, false, false) : remaining(node);
    }
    private double horizontal(Node node) {
        double ax = Math.abs(node.x - goal.x), az = Math.abs(node.z - goal.z);
        return Math.max(0, Math.abs(ax - az) + Math.min(ax, az) * SQRT2 - radius);
    }
    private double vertical(Node node, boolean exitEstimate, boolean buried) {
        double rise = goal.y - node.y;
        if (buried && rise > 0) return rise * DIG_RISE;
        double v = rise > 0 ? rise * JUMP : -rise * fallTicks(2) / 2;
        double h = horizontal(node);
        if (exitEstimate && rise > h) v += (rise - h) * WALK;
        return v;
    }
    /** The unknown remainder from a frontier node, at the unknown penalty (D3). */
    private double exitRemainder(Node node) {
        return unknownPenalty * (WALK * horizontal(node) + vertical(node, true, buried(node)));
    }
    /** Known solid cover over the node with no known air above it, below goal height. */
    private boolean buried(Node node) {
        boolean covered = false;
        for (int y = (int) Math.floor(node.y) + 2; y < Math.floor(goal.y); y++) {
            var cell = cell(node.x, y, node.z);
            if (cell == null) continue;
            if (!boxes(cell).isEmpty()) covered = true;
            else if (covered) return false;
        }
        return covered;
    }
    /** A known node whose moves read unknown cells, outside the always-refreshed start box. */
    private boolean frontierNode(Node node) {
        int fy = (int) Math.floor(node.y), low = fy - maxDrop - 1, high = fy + 3;
        for (var d : DIRECTIONS) {
            int cx = node.x + d[0], cz = node.z + d[1];
            long key = (((long) cx & 0xFFFFF) << 44) | (((long) cz & 0xFFFFF) << 24)
                    | (((long) low & 0xFFF) << 12) | ((long) high & 0xFFF);
            Boolean unknown = unknownColumns.get(key);
            if (unknown == null) { unknown = columnUnknown(cx, cz, low, high); unknownColumns.put(key, unknown); }
            if (unknown) return true;
        }
        return false;
    }
    private boolean columnUnknown(int cx, int cz, int low, int high) {
        boolean near = Math.abs(cx - start.x) <= NEAR_H && Math.abs(cz - start.z) <= NEAR_H;
        int sy = (int) Math.floor(start.y);
        for (int cy = low; cy < high; cy++) {
            if (near && Math.abs(cy - sy) <= NEAR_V) continue;
            if (cell(cx, cy, cz) == null) return true;
        }
        return false;
    }

    private static Result result(String status, Node end, Map<Node, Node> previous, int expanded, double cost) {
        var route = new ArrayList<Node>();
        for (Node node = end; node != null; node = previous.get(node)) route.add(node);
        Collections.reverse(route);
        return new Result(status, route, expanded, cost);
    }

    // Goal: horizontal disk of the radius at exact standing height (route_search._distance).
    double distance(Node node) {
        return Math.max(Math.max(0, Math.hypot(node.x - goal.x, node.z - goal.z) - radius), Math.abs(node.y - goal.y));
    }

    // Baritone GoalXZ octile heuristic x COST_HEURISTIC plus GoalYLevel (route_search._remaining).
    double remaining(Node node) {
        double ax = Math.abs(node.x - goal.x), az = Math.abs(node.z - goal.z);
        double horizontal = Math.max(0, Math.abs(ax - az) + Math.min(ax, az) * SQRT2 - radius);
        double rise = goal.y - node.y;
        double vertical = rise > 0 ? rise * JUMP : -rise * fallTicks(2) / 2;
        return COST_HEURISTIC * horizontal + vertical;
    }

    private record Move(Node target, double cost) { }

    private List<Move> moves(Node node) {
        var out = new ArrayList<Move>(8);
        int x = node.x, z = node.z; double y = node.y;
        for (var d : DIRECTIONS)
            for (double ty : surfaces(x + d[0], z + d[1], y - maxDrop, y + 1)) {
                var target = new Node(x + d[0], ty, z + d[1]);
                if (stepClear(node, target)) out.add(new Move(target, heightCost(ty - y)));
            }
        for (var d : DIAGONALS) {
            var target = new Node(x + d[0], y, z + d[1]);
            if (!standable(target)) continue;
            int a = side(x + d[0], y, z), b = side(x, y, z + d[1]);
            if (a < 0 || b < 0 || (a == 0 && b == 0)) continue;
            // Edging past one blocked side: walk pace, no sprint (MovementDiagonal).
            out.add(new Move(target, a == 0 || b == 0 ? WALK * SQRT2 * (SQRT2 - .001) : flat() * SQRT2));
        }
        return out;
    }

    private double flat() { return sprint ? SPRINT : WALK; }

    private double heightCost(double dy) {
        if (dy > .5) return Math.max(JUMP, WALK) + JUMP_PENALTY;  // MovementAscend
        if (dy < -.5) return WALK_OFF + Math.max(fallTicks(-dy), CENTER_AFTER_FALL);  // Descend/Fall
        return flat();
    }

    /** -1 unknown or hazard below, 0 blocked, 1 passable (route_search._side). */
    private int side(int x, double y, int z) {
        int fy = (int) Math.floor(y);
        if (cell(x, fy, z) == null || cell(x, fy + 1, z) == null) return -1;
        if (!clearColumn(x, z, y, y + 1.8)) return 0;
        for (int cy = fy - 1; cy > fy - 2 - maxDrop; cy--) {
            var below = cell(x, cy, z);
            if (below == null) continue;
            if (HAZARDS.contains(below.get("block")) || String.valueOf(below.get("fluid")).contains("lava")) return -1;
            if (!boxes(below).isEmpty()) break;
        }
        return 1;
    }

    // --- collision geometry (mclab.collision_geometry) ---

    private Map<String, Object> cell(int x, int y, int z) {
        long key = (((long) x & 0x3FFFFFF) << 38) | (((long) y & 0xFFF) << 26) | ((long) z & 0x3FFFFFF);
        if (!cells.containsKey(key)) cells.put(key, reader.cell(x, y, z));
        return cells.get(key);
    }
    private static boolean safe(Map<String, Object> cell) {
        return cell != null && "minecraft:empty".equals(cell.getOrDefault("fluid", "minecraft:empty"))
                && !HAZARDS.contains(cell.get("block"));
    }
    private static List<?> boxes(Map<String, Object> cell) {
        return cell.get("collision_boxes") instanceof List<?> list ? list : List.of();
    }
    private static double at(Object box, int i) { return ((Number) ((List<?>) box).get(i)).doubleValue(); }

    boolean clearColumn(int x, int z, double bottom, double top) {
        double cx0 = x + .5, cz0 = z + .5;
        double left = cx0 - RADIUS, right = cx0 + RADIUS, near = cz0 - RADIUS, far = cz0 + RADIUS;
        for (int cx = (int) Math.floor(left + EPS); cx < Math.ceil(right - EPS); cx++)
            for (int cz = (int) Math.floor(near + EPS); cz < Math.ceil(far - EPS); cz++)
                for (int cy = (int) Math.floor(bottom + EPS); cy < Math.ceil(top - EPS); cy++) {
                    var cell = cell(cx, cy, cz);
                    if (!safe(cell) || !cell.containsKey("collision_boxes")) return false;
                    for (var box : boxes(cell))
                        if (cx + at(box, 0) < right - EPS && cx + at(box, 3) > left + EPS
                                && cz + at(box, 2) < far - EPS && cz + at(box, 5) > near + EPS
                                && cy + at(box, 1) < top - EPS && cy + at(box, 4) > bottom + EPS) return false;
                }
        return true;
    }

    private double[] cellHeights(int x, int y, int z) {
        long key = (((long) x & 0x3FFFFFF) << 38) | (((long) y & 0xFFF) << 26) | ((long) z & 0x3FFFFFF);
        var cached = heights.get(key);
        if (cached != null) return cached;
        var cell = cell(x, y, z);
        var found = new ArrayList<Double>();
        if (safe(cell)) {
            String block = String.valueOf(cell.getOrDefault("block", ""));
            if (fullCube(cell) || block.endsWith("_slab") || block.endsWith("_stairs"))
                for (var box : boxes(cell))
                    if (at(box, 0) < .8 - EPS && at(box, 3) > .2 + EPS && at(box, 2) < .8 - EPS && at(box, 5) > .2 + EPS) {
                        double h = y + at(box, 4);
                        if (clearColumn(x, z, h, h + 1.8)) found.add(h);
                    }
        }
        double[] result = found.stream().mapToDouble(Double::doubleValue).toArray();
        heights.put(key, result);
        return result;
    }
    // mclab.navigation.full_cube: any unit box (fluid is already checked by safe()).
    private static boolean fullCube(Map<String, Object> cell) {
        for (var box : boxes(cell))
            if (at(box, 0) == 0 && at(box, 1) == 0 && at(box, 2) == 0 && at(box, 3) == 1 && at(box, 4) == 1 && at(box, 5) == 1)
                return true;
        return false;
    }

    List<Double> surfaces(int x, int z, double low, double high) {
        var found = new java.util.TreeSet<Double>();
        for (int y = (int) Math.floor(low) - 1; y <= Math.floor(high); y++)
            for (double h : cellHeights(x, y, z)) if (low - EPS <= h && h <= high + EPS) found.add(h);
        return new ArrayList<>(found);
    }
    boolean standable(Node node) {
        for (double h : surfaces(node.x, node.z, node.y, node.y)) if (Math.abs(h - node.y) < EPS) return true;
        return false;
    }
    private boolean stepClear(Node source, Node target) {
        if (!standable(source) || !standable(target)) return false;
        if (target.y > source.y) {
            double extra = target.y - source.y > .6 ? 1.2 : 0;
            return clearColumn(source.x, source.z, source.y, Math.max(source.y + 1.8, target.y + 1.8 + extra))
                    && clearColumn(target.x, target.z, target.y, target.y + 1.8 + extra);
        }
        return clearColumn(target.x, target.z, target.y, source.y + 1.8);
    }

    private static double sq(double v) { return v * v; }
}
