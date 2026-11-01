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
    /** One planned edit edge (terrain_steps.StepPlan): kinds step, downward, descend, pillar. */
    record Plan(Node source, Node target, List<Node> breaks, List<Node> places, String kind) { }
    record Result(String status, List<Node> route, int expanded, double cost, List<Plan> steps) {
        Result(String status, List<Node> route, int expanded, double cost) { this(status, route, expanded, cost, List.of()); }
    }

    // Baritone ActionCosts and Settings, reimplemented (same values as route_search.py).
    static final double WALK = 20 / 4.317, SPRINT = 20 / 5.612, WALK_OFF = WALK * .8,
            CENTER_AFTER_FALL = WALK - WALK_OFF, JUMP = fallTicks(1.25) - fallTicks(.25),
            COST_HEURISTIC = 3.563, JUMP_PENALTY = 2.0, SQRT2 = Math.sqrt(2);
    static final double[] COEFFICIENTS = {1.5, 2, 2.5, 3, 4, 5, 10};
    static final int MIN_DIST_PATH = 5, CUTOFF_MIN = 30, NEAR_H = 8, NEAR_V = 4;
    // route_search.DIG_RISE: an edit step breaking two stone blocks, then the ascend.
    static final double DIG_RISE = 51.63284688441047;
    static final double SWIM = 20 / 2.2, FLOWING = 1.5, LADDER_UP = 20 / 2.35, LADDER_DOWN = 20 / 3.0;
    static final int LANDING_MAX = 20, SURFACE_SCAN = 40;
    private static final Set<String> CLIMBABLE = Set.of("minecraft:ladder", "minecraft:vine", "minecraft:scaffolding",
            "minecraft:twisting_vines", "minecraft:twisting_vines_plant", "minecraft:weeping_vines",
            "minecraft:weeping_vines_plant", "minecraft:cave_vines", "minecraft:cave_vines_plant");
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
    // planner_swim + allow_swim, and baritone_moves (ladders, water landings; no edits here).
    private boolean swim, extra;

    /** Turns on swimming (planner_swim) and Baritone's extra moves (ladders, water landings). */
    RoutePlanner withMoves(boolean swim, boolean extra) { this.swim = swim; this.extra = extra; return this; }
    private int maxGap;
    /** Level gap jumps of 1..maxGap (max 2; width 2 needs sprint), Baritone MovementParkour. */
    RoutePlanner withGaps(int maxGap) { this.maxGap = Math.min(maxGap, 2); return this; }
    private Set<Node> favored = Set.of();
    private Set<List<Node>> excluded = Set.of();
    /** Previous-route favouring (Settings.backtrackCostFavoringCoefficient 0.5) and failed edges. */
    RoutePlanner withHistory(Set<Node> favored, Set<List<Node>> excluded) {
        this.favored = favored; this.excluded = excluded; return this;
    }
    static final double FAVOR = .5;
    // MCB-measured edit overheads (terrain_steps / route_search), ticks.
    static final double EDIT_STEP = 15, PLACE = 20, BREAK_EXTRA = 2, BREAK_AIM = 6, STACK_PLACE = 20,
            BRIDGE_PLACE = 15, EDIT_CHAIN = 10, FALL_SETTLE = 17;
    private static final Set<String> FALLING = Set.of("minecraft:gravel", "minecraft:red_sand", "minecraft:sand",
            "minecraft:suspicious_gravel", "minecraft:suspicious_sand");
    /** Edit permissions and supplies, computed by Python from the policy and inventory. */
    record Edits(boolean allowBreak, boolean allowPlace, boolean allowBridge, boolean allowPillar, int maxSupportStack,
                 int breakBudget, int placeBudget, int placeSupply, Map<String, Double> breakTicks, Set<String> breakable,
                 Set<Node> protectedCells, Set<String> protectedBlocks, String material) { }
    private Edits edits;
    /** Turns on edit edges (route_search planner_edits): cut steps, support stacks, dig down, pillar. */
    RoutePlanner withEdits(Edits edits) { this.edits = edits; return this; }
    private final Map<Node, Plan> plans = new HashMap<>();
    private final Map<Node, Integer> editDepth = new HashMap<>();
    // Cells changed by the plan of the edge that reached the node being expanded.
    private Map<Long, Map<String, Object>> overlay = Map.of();
    private Set<Long> overlayColumns = Set.of();

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

    // Resumable search state (advance() runs in slices on the server thread).
    private final Map<Node, Double> costs = new HashMap<>();
    private final Map<Node, Node> previous = new HashMap<>();
    private final List<Node> nodes = new ArrayList<>();
    // Same order as Python's heap of (f, g, exit flag, node) tuples, so ties expand alike.
    private final PriorityQueue<double[]> frontier = new PriorityQueue<>((a, b) -> {
        int c = Double.compare(a[0], b[0]);
        if (c == 0) c = Double.compare(a[1], b[1]);
        if (c == 0) c = Double.compare(a[3], b[3]);
        if (c != 0) return c;
        Node p = nodes.get((int) a[2]), q = nodes.get((int) b[2]);
        c = Integer.compare(p.x, q.x);
        if (c == 0) c = Double.compare(p.y, q.y);
        return c != 0 ? c : Integer.compare(p.z, q.z);
    });
    private final Node[] coefBest = new Node[COEFFICIENTS.length];
    private final double[] coefValue = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY,
            Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY};
    private Node best, exit;
    private double exitValue = Double.POSITIVE_INFINITY;
    private int expanded;
    private Result terminal;

    /** One-shot search to nodeLimit expansions (tests and the parity tool). */
    Result search(int nodeLimit) {
        Result r;
        while ((r = advance(nodeLimit, Long.MAX_VALUE)).status().equals("searching")) { }
        return r;
    }

    int expanded() { return expanded; }

    /**
     * Expands until the goal, an unknown-space exit, nodeLimit total expansions or the
     * System.nanoTime() deadline. "searching" means call again; otherwise the result is final.
     */
    Result advance(int nodeLimit, long deadlineNanos) {
        if (terminal != null) return terminal;
        if (best == null) {
            if (!(swim ? supported(start) : standable(start)))
                return terminal = new Result("unreachable", List.of(), 0, 0);
            best = start;
            costs.put(start, 0.0); nodes.add(start);
            frontier.add(new double[]{remaining(start), 0, 0, 0});
        }
        int slice = 0;
        while (!frontier.isEmpty() && expanded < nodeLimit) {
            if ((++slice & 63) == 0 && System.nanoTime() > deadlineNanos) return new Result("searching", List.of(), expanded, 0);
            var entry = frontier.poll();
            int index = (int) entry[2];
            Node node = nodes.get(index);
            double cost = entry[1];
            if (cost != costs.get(node)) continue;
            if (entry[3] == 1)  // the cheapest way on runs through unknown space
                return terminal = trim(result("segment", node, previous, expanded, cost), costs);
            expanded++;
            double estimate = closeness(node);
            if (estimate < closeness(best)) best = node;
            for (int i = 0; i < COEFFICIENTS.length; i++) {
                double value = estimate + cost / COEFFICIENTS[i];
                if (value < coefValue[i]) { coefValue[i] = value; coefBest[i] = node; }
            }
            if (distance(node) == 0) return terminal = result("reached", node, previous, expanded, cost);
            setOverlay(plans.get(node));
            if (unknownPenalty > 0 && !node.equals(start) && frontierNode(node)) {
                double total = cost + exitRemainder(node);
                frontier.add(new double[]{total, cost, index, 1});
                if (total < exitValue) { exitValue = total; exit = node; }
            }
            for (var move : moves(node)) {
                if (!excluded.isEmpty() && excluded.contains(List.of(node, move.target))) continue;
                double total = cost + move.cost * (favored.contains(move.target) ? FAVOR : 1);
                if (total < costs.getOrDefault(move.target, Double.POSITIVE_INFINITY)) {
                    costs.put(move.target, total); previous.put(move.target, node);
                    if (move.plan == null) { plans.remove(move.target); editDepth.put(move.target, editDepth.getOrDefault(node, 0)); }
                    else { plans.put(move.target, move.plan); editDepth.put(move.target, editDepth.getOrDefault(node, 0) + 1); }
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
        return terminal = trim(result(frontier.isEmpty() ? "unreachable" : "incomplete", end, previous, expanded,
                costs.get(end)), costs);
    }

    /** Settings.pathCutoff*: drop the last 10% of long non-goal routes. */
    private static Result trim(Result r, Map<Node, Double> costs) {
        int n = r.route.size();
        if (n < CUTOFF_MIN) return r;
        var route = r.route.subList(0, Math.max(2, (int) (n * CUTOFF_FACTOR)));
        var targets = new java.util.HashSet<>(route.subList(1, route.size()));
        var steps = r.steps.stream().filter(p -> targets.contains(p.target)).toList();
        return new Result(r.status, route, r.expanded, costs.get(route.get(route.size() - 1)), steps);
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
        int fy = (int) Math.floor(node.y), low = fy - Math.max(maxDrop, extra ? 1 : 0) - 1, high = fy + (extra ? 4 : 3);
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

    private Result result(String status, Node end, Map<Node, Node> previous, int expanded, double cost) {
        var route = new ArrayList<Node>();
        for (Node node = end; node != null; node = previous.get(node)) route.add(node);
        Collections.reverse(route);
        var steps = new ArrayList<Plan>();
        for (int i = 1; i < route.size(); i++) {
            var plan = plans.get(route.get(i));
            if (plan != null && plan.source.equals(route.get(i - 1))) steps.add(plan);
        }
        return new Result(status, route, expanded, cost, steps);
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

    private record Move(Node target, double cost, Plan plan) {
        Move(Node target, double cost) { this(target, cost, null); }
    }

    // Same order as route_search._moves: equal-cost ties keep the first move found.
    private List<Move> moves(Node node) {
        var out = new ArrayList<Move>(8);
        int x = node.x, z = node.z; double y = node.y;
        if (extra && climbOnly(node)) { extraMoves(node, out); return out; }
        boolean onWater = swim && (surfaceNode(node) || flowingSurface(node));
        boolean editReady = edits != null && integral(y) && blockStandable(node);
        var below = at(x, y - 1, z);
        boolean gapRoom = maxGap > 0 && integral(y) && below != null && dry(below) && fullCube(below);
        for (var d : DIRECTIONS) {
            if (swim) {
                var target = new Node(x + d[0], y, z + d[1]);
                double cost = swimCost(node, target);
                if (cost > 0) out.add(new Move(target, cost));
                // No +1 exit from a surface stance (it is +2 for the body: cohort 19 R5).
                if (onWater) continue;
            }
            // A gap needs a hole under the adjacent cell and a full-cube support.
            if (gapRoom && empty(at(x + d[0], y - 1, z + d[1])))
                for (int width = 1; width <= maxGap; width++)
                    if ((width < 2 || sprint) && gap(node, d, width + 1))
                        out.add(new Move(new Node(x + d[0] * (width + 1), y, z + d[1] * (width + 1)),
                                WALK * (width + 1) + JUMP_PENALTY));
            var reached = new java.util.HashSet<Node>();
            for (double ty : surfaces(x + d[0], z + d[1], y - maxDrop, y + 1)) {
                var target = new Node(x + d[0], ty, z + d[1]);
                if (allowed(node, target) && stepClear(node, target)) { reached.add(target); out.add(new Move(target, heightCost(ty - y))); }
            }
            if (editReady) editEdges(node, d, reached, out);
        }
        for (var d : DIAGONALS) {
            var target = new Node(x + d[0], y, z + d[1]);
            if (!(swim ? supported(target) : standable(target))) continue;
            int a = side(x + d[0], y, z), b = side(x, y, z + d[1]);
            if (a < 0 || b < 0 || (a == 0 && b == 0)) continue;
            if (swim) {  // MovementDiagonal: water at either end swims, no sprint
                var wet = surfaceWater(target);
                if (wet == null) wet = surfaceWater(node);
                if (wet != null) { out.add(new Move(target, SWIM * multiplier(wet) * SQRT2)); continue; }
            }
            // Edging past one blocked side: walk pace, no sprint (MovementDiagonal).
            out.add(new Move(target, a == 0 || b == 0 ? WALK * SQRT2 * (SQRT2 - .001) : flat() * SQRT2));
        }
        if (extra) extraMoves(node, out);
        return out;
    }

    // --- swimming (water_movement, route_moves) ---

    private static boolean dry(Map<String, Object> c) { return "minecraft:empty".equals(c.getOrDefault("fluid", "minecraft:empty")); }
    private static boolean empty(Map<String, Object> c) {
        return c != null && dry(c) && ("minecraft:air".equals(c.get("block")) || List.of().equals(c.get("collision_boxes")));
    }
    private static boolean climbable(Map<String, Object> c) { return c != null && dry(c) && CLIMBABLE.contains(c.get("block")); }
    private static boolean water(Map<String, Object> c, boolean still) {
        if (c == null || !"minecraft:water".equals(c.get("fluid")) || !List.of().equals(c.get("collision_boxes"))
                || HAZARDS.contains(c.get("block"))) return false;
        Object level = c.get("properties") instanceof Map<?, ?> m ? m.get("level") : null;
        return still == "0".equals(level == null ? "0" : level);
    }
    private static boolean integral(double y) { return y == Math.floor(y); }
    private Map<String, Object> at(int x, double y, int z) { return integral(y) ? cell(x, (int) y, z) : null; }
    boolean surfaceNode(Node n) {
        return integral(n.y) && water(at(n.x, n.y - 1, n.z), true) && clearColumn(n.x, n.z, n.y, n.y + 2.5);
    }
    private boolean flowingSurface(Node n) {
        return integral(n.y) && water(at(n.x, n.y - 1, n.z), false) && clearColumn(n.x, n.z, n.y, n.y + 2.5);
    }
    private Map<String, Object> surfaceWater(Node n) {
        var below = at(n.x, n.y - 1, n.z);
        if (below == null || !"minecraft:water".equals(below.get("fluid"))) return null;
        return surfaceNode(n) || flowingSurface(n) ? below : null;
    }
    private static double multiplier(Map<String, Object> wet) { return water(wet, true) ? 1 : FLOWING; }
    /** water_movement.supported_node with shapes: a surface stance, or standable over a safe cell. */
    boolean supported(Node n) {
        return surfaceNode(n) || (standable(n) && safe(cell(n.x, (int) Math.ceil(n.y) - 1, n.z)));
    }
    /** route_moves.swim_cost under planner_swim; 0 when there is no swim edge. */
    private double swimCost(Node source, Node target) {
        var wetSource = surfaceWater(source); var wetTarget = surfaceWater(target);
        if (wetSource == null && wetTarget == null) return 0;
        for (var n : List.of(source, target)) if (!supported(n) && !flowingSurface(n)) return 0;
        return SWIM * multiplier(wetTarget != null ? wetTarget : wetSource);
    }

    /** gap_jump.gap_plan: full-cube take-off and landing, 2-deep hole, clear runway and arc. */
    private boolean gap(Node source, int[] d, int span) {
        int sx = source.x, sz = source.z; double sy = source.y;
        if (!fullDry(at(sx, sy - 1, sz)) || !fullDry(at(sx + d[0] * span, sy - 1, sz + d[1] * span))) return false;
        for (int offset = 1; offset < span; offset++)
            if (!empty(at(sx + d[0] * offset, sy - 1, sz + d[1] * offset))) return false;
        for (int offset = -1; offset < span + 3; offset++) {
            int x = sx + d[0] * offset, z = sz + d[1] * offset;
            if (0 < offset && offset < span) {
                if (!empty(at(x, sy - 1, z)) || !empty(at(x, sy - 2, z))) return false;
            } else {
                var under = at(x, sy - 1, z);
                if (!fullDry(under) || HAZARDS.contains(under.get("block"))) return false;
            }
            if (!clearColumn(x, z, sy, sy + 3.2)) return false;
            if (!(0 < offset && offset < span) && !blockStandable(new Node(x, sy, z))) return false;
        }
        return true;
    }
    private static boolean fullDry(Map<String, Object> c) { return c != null && dry(c) && fullCube(c); }

    // --- Baritone extra moves without edits: ladders, water landings (route_moves.extra_moves) ---

    /** navigation.standable: full-cube floor, empty feet and head (integral heights only). */
    private boolean blockStandable(Node n) {
        if (!integral(n.y)) return false;
        var floor = at(n.x, n.y - 1, n.z);
        return floor != null && dry(floor) && fullCube(floor) && empty(at(n.x, n.y, n.z)) && empty(at(n.x, n.y + 1, n.z));
    }
    private boolean climbOnly(Node n) { return climbable(at(n.x, n.y, n.z)) && !blockStandable(n); }
    private boolean openOrClimb(Map<String, Object> c) { return empty(c) || climbable(c); }

    private void extraMoves(Node node, List<Move> out) {
        ladderMoves(node, out);
        if (!integral(node.y) || !blockStandable(node)) return;
        if (edits != null) {
            var down = downward(node);
            if (down != null && allowed(node, down.target)) out.add(new Move(down.target, priced(down, -1, node, fallTicks(1)), down));
            var pillar = pillar(node);
            if (pillar != null && allowed(node, pillar.target))
                out.add(new Move(pillar.target, priced(pillar, 1, node, JUMP + JUMP_PENALTY), pillar));
        }
        for (var d : DIRECTIONS) {
            if (edits != null) {
                var target = new Node(node.x + d[0], node.y - 1, node.z + d[1]);
                if (allowed(node, target)) {
                    var plan = descend(node, target);
                    if (plan != null) out.add(new Move(target, priced(plan, -1, node, Double.NaN), plan));
                }
            }
            if (swim) landings(node, d, out);
        }
    }

    // --- edit edges (terrain_steps.plan_edit, route_moves.prepare_*) ---

    private boolean allowed(Node a, Node b) { return excluded.isEmpty() || !excluded.contains(List.of(a, b)); }
    private boolean flagEmpty(int x, double y, int z) { return empty(at(x, y, z)); }
    private boolean flagFull(int x, double y, int z) { return fullDry(at(x, y, z)); }

    private void setOverlay(Plan plan) {
        overlay = Map.of(); overlayColumns = Set.of();  // falling stacks are read from the base map
        if (plan == null) return;
        var changed = new HashMap<Long, Map<String, Object>>();
        var columns = new java.util.HashSet<Long>();
        Map<String, Object> air = Map.of("block", "minecraft:air", "fluid", "minecraft:empty", "collision_boxes", List.of());
        for (var p : plan.breaks) {
            changed.put(key(p.x, (int) p.y, p.z), air); columns.add(column(p.x, p.z));
            if (extra) for (var q : fallingStack(p)) changed.put(key(q.x, (int) q.y, q.z), air);
        }
        for (var p : plan.places) {
            changed.put(key(p.x, (int) p.y, p.z), Map.of("block", edits.material(), "fluid", "minecraft:empty",
                    "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1))));
            columns.add(column(p.x, p.z));
        }
        overlay = changed; overlayColumns = columns;
    }

    private List<Node> fallingStack(Node p) {
        var stack = new ArrayList<Node>();
        while (true) {
            var c = cell(p.x, (int) p.y + stack.size() + 1, p.z);
            if (c == null || !dry(c) || !FALLING.contains(c.get("block"))) return stack;
            stack.add(new Node(p.x, p.y + stack.size() + 1, p.z));
        }
    }

    /** terrain_steps.cut_allowed: known dry neighbours; falling ones only with a known support. */
    private boolean cutAllowed(Node p) {
        var c = at(p.x, p.y, p.z);
        if (!edits.allowBreak() || edits.protectedCells().contains(p) || c == null
                || edits.protectedBlocks().contains(c.get("block")) || !edits.breakable().contains(c.get("block"))
                || !fullDry(c)) return false;
        for (int[] d : new int[][]{{1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, 0, 1}, {0, 0, -1}}) {
            var n = at(p.x + d[0], p.y + d[1], p.z + d[2]);
            if (n == null || !dry(n)) return false;
            if (FALLING.contains(n.get("block"))) {
                if (!extra) return false;
                if (d[1] == 0 && !flagFull(p.x + d[0], p.y - 1, p.z + d[2])) return false;
            }
        }
        return true;
    }

    /** Points that need breaking, or null when any of them may not be broken (or too many). */
    private List<Node> cuts(List<Node> points, int limit) {
        var out = new ArrayList<Node>();
        for (var p : new java.util.LinkedHashSet<>(points)) {
            if (flagEmpty(p.x, p.y, p.z)) continue;
            if (!cutAllowed(p)) return null;
            out.add(p);
        }
        return out.size() <= limit ? out : null;
    }

    private boolean anchored(Node source, Node p) {
        if (flagFull(p.x, p.y - 1, p.z)) return true;
        int dx = p.x - source.x, dz = p.z - source.z;
        return edits.allowBridge() && p.y == source.y - 1 && Math.abs(dx) + Math.abs(dz) == 1
                && flagEmpty(p.x, p.y - 1, p.z) && flagFull(source.x, source.y - 1, source.z);
    }

    /** terrain_steps.plan_edit for a standable full-block source and an adjacent target (dy 0 or 1). */
    private Plan planEdit(Node source, Node target) {
        int x = source.x, z = source.z, tx = target.x, tz = target.z; double y = source.y, ty = target.y;
        List<Node> places = List.of();
        var support = new Node(tx, ty - 1, tz);
        if (!flagFull(tx, ty - 1, tz)) {
            if (!edits.allowPlace()) return null;
            var stack = new ArrayList<Node>(List.of(support));
            while (!anchored(source, stack.get(stack.size() - 1))) {
                if (stack.size() >= edits.maxSupportStack()) return null;
                var top = stack.get(stack.size() - 1);
                stack.add(new Node(top.x, top.y - 1, top.z));
            }
            Collections.reverse(stack);
            if (stack.size() > edits.placeBudget()) return null;
            for (var p : stack) {
                var c = at(p.x, p.y, p.z);
                if (edits.protectedCells().contains(p) || (c != null && edits.protectedBlocks().contains(c.get("block")))
                        || !empty(c)) return null;
            }
            if (edits.placeSupply() < stack.size()) return null;
            places = List.copyOf(stack);
        }
        var clearance = ty == y
                ? List.of(new Node(tx, ty, tz), new Node(tx, ty + 1, tz))
                : List.of(new Node(tx, ty, tz), new Node(tx, ty + 1, tz), new Node(x, y + 2, z), new Node(x, y + 3, z),
                new Node(tx, ty + 2, tz));
        var cuts = new ArrayList<Node>();
        for (var p : clearance) {
            if (flagEmpty(p.x, p.y, p.z)) continue;
            if (!cutAllowed(p)) return null;
            cuts.add(p);
        }
        if (cuts.size() > Math.min(4, edits.breakBudget()) || (cuts.isEmpty() && places.isEmpty())) return null;
        return new Plan(source, target, List.copyOf(cuts), places, "step");
    }

    private void editEdges(Node node, int[] d, Set<Node> reached, List<Move> out) {
        for (int dy = 0; dy <= 1; dy++) {
            var target = new Node(node.x + d[0], node.y + dy, node.z + d[1]);
            if (reached.contains(target) || !allowed(node, target)) continue;
            var plan = planEdit(node, target);
            if (plan != null) out.add(new Move(target, priced(plan, dy, node, Double.NaN), plan));
        }
    }

    /** MovementDownward: break the floor, drop 1 onto a known solid, safe y-2. */
    private Plan downward(Node s) {
        var support = at(s.x, s.y - 2, s.z);
        if (!fullDry(support) || HAZARDS.contains(support.get("block"))) return null;
        var c = cuts(List.of(new Node(s.x, s.y - 1, s.z)), edits.breakBudget());
        return c == null || c.isEmpty() ? null : new Plan(s, new Node(s.x, s.y - 1, s.z), c, List.of(), "downward");
    }
    /** MovementDescend with breaks: clear target feet, head and the cell above. */
    private Plan descend(Node s, Node t) {
        var floor = at(t.x, t.y - 1, t.z);
        if (!fullDry(floor) || HAZARDS.contains(floor.get("block"))) return null;
        var c = cuts(List.of(t, new Node(t.x, s.y, t.z), new Node(t.x, s.y + 1, t.z)), edits.breakBudget());
        return c == null || c.isEmpty() ? null : new Plan(s, t, c, List.of(), "descend");
    }
    /** MovementPillar: place under the feet while jumping (no head cut). */
    private Plan pillar(Node s) {
        if (!edits.allowPillar() || !edits.allowPlace() || edits.placeBudget() < 1 || edits.protectedCells().contains(s)
                || !flagEmpty(s.x, s.y + 2, s.z) || !flagEmpty(s.x, s.y + 3, s.z) || edits.placeSupply() < 1) return null;
        return new Plan(s, new Node(s.x, s.y + 1, s.z), List.of(), List.of(s), "pillar");
    }

    /** route_search._edit_cost + _chain: movement, MCB-measured edit overheads, then one more edit costs more. */
    private double priced(Plan plan, int dy, Node node, double base) {
        double breaking = 0;
        for (var p : plan.breaks) {
            var block = String.valueOf(at(p.x, p.y, p.z).get("block"));
            breaking += edits.breakTicks().get(block) + BREAK_EXTRA + BREAK_AIM;
            if (extra) for (var q : fallingStack(p))
                breaking += edits.breakTicks().getOrDefault(String.valueOf(at(q.x, q.y, q.z).get("block")), 0.0) + BREAK_EXTRA + FALL_SETTLE;
        }
        int bridged = 0;
        for (var p : plan.places) { var under = at(p.x, p.y - 1, p.z); if (under == null || boxes(under).isEmpty()) bridged++; }
        return (Double.isNaN(base) ? heightCost(dy) : base) + EDIT_STEP + breaking + PLACE * plan.places.size()
                + STACK_PLACE * Math.max(0, plan.places.size() - 1) + BRIDGE_PLACE * bridged
                + EDIT_CHAIN * editDepth.getOrDefault(node, 0);
    }
    private void ladderMoves(Node node, List<Move> out) {
        int x = node.x, z = node.z; double y = node.y;
        if (!integral(y)) return;
        if (climbable(at(x, y, z))) {
            var up = new Node(x, y + 1, z); var down = new Node(x, y - 1, z);
            if (climbable(at(x, y + 1, z)) && openOrClimb(at(x, y + 2, z))) out.add(new Move(up, LADDER_UP));
            if (climbable(at(x, y - 1, z))) out.add(new Move(down, LADDER_DOWN));
            for (var d : DIRECTIONS) for (int dy = 0; dy <= 1; dy++) {
                var target = new Node(x + d[0], y + dy, z + d[1]);
                if (blockStandable(target)) out.add(new Move(target, heightCost(dy)));
            }
        }
        if (!blockStandable(node)) return;
        for (var d : DIRECTIONS) for (int dy = 0; dy >= -1; dy--) {
            var target = new Node(x + d[0], y + dy, z + d[1]);
            if (!climbable(at(target.x, target.y, target.z)) || !openOrClimb(at(target.x, target.y + 1, target.z))
                    || !openOrClimb(at(target.x, y, target.z))) continue;
            out.add(new Move(target, heightCost(dy)));
        }
    }
    /** MovementDescend.dynamicFallCost water branch: walk off into still water at any height. */
    private void landings(Node node, int[] d, List<Move> out) {
        for (int h = 1; h <= LANDING_MAX; h++) {
            var target = new Node(node.x + d[0], node.y - h, node.z + d[1]);
            var c = at(target.x, target.y, target.z);
            if (c == null || !boxes(c).isEmpty()) return;
            if (waterLanding(node, target)) out.add(new Move(target, WALK_OFF + fallTicks(h)));
            if (!dry(c)) return;
        }
    }
    private boolean waterLanding(Node source, Node target) {
        double drop = source.y - target.y;
        if (drop < 1 || drop > LANDING_MAX || !blockStandable(source) || !surfaceNode(target)
                || !clearColumn(target.x, target.z, target.y, source.y + 1.8)) return false;
        int y = (int) target.y - 1;
        while (water(cell(target.x, y, target.z), true)) {
            y--;
            if (y < target.y - SURFACE_SCAN) return false;
        }
        var floor = cell(target.x, y, target.z);
        return y < target.y - 1 && safe(floor) && !boxes(floor).isEmpty();
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

    private static long key(int x, int y, int z) {
        return (((long) x & 0x3FFFFFF) << 38) | (((long) y & 0xFFF) << 26) | ((long) z & 0x3FFFFFF);
    }
    private static long column(int x, int z) { return (((long) x & 0xFFFFFFFFL) << 32) | ((long) z & 0xFFFFFFFFL); }
    private Map<String, Object> cell(int x, int y, int z) {
        long key = key(x, y, z);
        if (!overlay.isEmpty()) { var edited = overlay.get(key); if (edited != null) return edited; }
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
        long key = key(x, y, z);
        boolean edited = !overlayColumns.isEmpty() && overlayColumns.contains(column(x, z));
        var cached = edited ? null : heights.get(key);
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
        if (!edited) heights.put(key, result);
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
