package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.PriorityQueue;
import java.util.Set;

/**
 * The route planner (ROADMAP item 8): tick costs, Baritone search and diagonals, shapes,
 * swimming, gaps, ladders, water landings, unknown-space exits and, with withEdits, the
 * edit edges. Baritone's costs, heuristic, partial-path rule and tail cutoff. Unknown cells
 * are never air or support. Not thread-safe: one search per instance.
 */
final class RoutePlanner {
    record Node(int x, double y, int z) {
        double distance(Node o) { return Math.sqrt(sq(x - o.x) + sq(y - o.y) + sq(z - o.z)); }
    }
    record Result(String status, List<Node> route, int expanded, double cost, List<Edit> steps) { }
    /** One planned edit edge (terrain_steps.StepPlan): blocks to break and to place, in order. */
    /** scaffold: {x, y, z, support distance} of the places made of scaffolding; the rest are solid. */
    record Edit(Node source, Node target, List<int[]> breaks, List<int[]> places, String kind, List<int[]> scaffold) {
        Edit(Node source, Node target, List<int[]> breaks, List<int[]> places, String kind) {
            this(source, target, breaks, places, kind, List.of());
        }
    }

    // Baritone ActionCosts and Settings, reimplemented (Baritone v1.20.0 values).
    static final double WALK = 20 / 4.317, SPRINT = 20 / 5.612, WALK_OFF = WALK * .8,
            CENTER_AFTER_FALL = WALK - WALK_OFF, JUMP = fallTicks(1.25) - fallTicks(.25),
            COST_HEURISTIC = 3.563, JUMP_PENALTY = 2.0, SQRT2 = Math.sqrt(2);
    static final double[] COEFFICIENTS = {1.5, 2, 2.5, 3, 4, 5, 10};
    static final int MIN_DIST_PATH = 5, CUTOFF_MIN = 30, NEAR_H = 8, NEAR_V = 4;
    // DIG_RISE: an edit step breaking two stone blocks, then the ascend.
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
    private Node start;  // advance() may snap a start inside a climbable cell to its cell
    private final Node goal;
    private final double radius;
    private Double reach;
    private Set<Node> avoidStances = Set.of();
    private double hazardPenalty, mobCoefficient = 1, mobRadius;
    private List<double[]> threats = List.of();
    private boolean ticks = true, baritoneSearch = true, baritoneDiagonal = true, diagonals = true, shapes = true;
    private double heuristicWeight = 1;
    private double breakPenalty = 3, placePenalty = 4, toolWearPenalty;
    private final int maxDrop;
    static final double HURT_TICKS_PER_POINT = 40;
    private final boolean sprint;
    // planner_unknown (D3): plan through unknown space; exits priced at this penalty.
    private final double unknownPenalty;
    private final Map<Long, Boolean> unknownColumns = new HashMap<>();
    // planner_swim + allow_swim, and baritone_moves (ladders, water landings; no edits here).
    private boolean swim, plannerSwim = true, extra, climb = true;

    /** Turns on swimming (planner_swim) and Baritone's extra edit/water-landing moves. */
    RoutePlanner withMoves(boolean swim, boolean extra) { this.swim = swim; this.extra = extra; return this; }
    RoutePlanner withPlannerSwim(boolean plannerSwim) { this.plannerSwim = plannerSwim; return this; }
    RoutePlanner withClimb(boolean climb) { this.climb = climb; return this; }
    RoutePlanner withInteraction(double reach) { this.reach = reach; return this; }
    RoutePlanner withAvoidStances(Set<Node> avoidStances) { this.avoidStances = Set.copyOf(avoidStances); return this; }
    RoutePlanner withSurvival(double hazardPenalty, double mobCoefficient, double mobRadius, List<double[]> threats) {
        this.hazardPenalty = hazardPenalty; this.mobCoefficient = mobCoefficient;
        this.mobRadius = mobRadius; this.threats = threats; return this;
    }
    RoutePlanner withPolicy(boolean ticks, boolean baritoneSearch, boolean baritoneDiagonal,
                            boolean diagonals, boolean shapes, double heuristicWeight) {
        this.ticks = ticks; this.baritoneSearch = baritoneSearch; this.baritoneDiagonal = baritoneDiagonal;
        this.diagonals = diagonals; this.shapes = shapes; this.heuristicWeight = heuristicWeight; return this;
    }
    RoutePlanner withLegacyPricing(double breakPenalty, double placePenalty, double toolWearPenalty) {
        this.breakPenalty = breakPenalty; this.placePenalty = placePenalty; this.toolWearPenalty = toolWearPenalty; return this;
    }
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

    RoutePlanner(RouteGeometry.Reader reader, Node start, Node goal, double radius, int maxDrop, boolean sprint) {
        this(reader, start, goal, radius, maxDrop, sprint, 0);
    }
    /** unknownPenalty 0: known-only planning; else planner_unknown: unknown cells cost that penalty. */
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
    // (f, then deeper g first, exit flag, node). Deeper-first ties: with a near-exact dig field every parallel
    // shaft has equal f, and shallow-first expanded them all (Python planner, whose order this copied, is gone).
    private final PriorityQueue<double[]> frontier = new PriorityQueue<>((a, b) -> {
        int c = Double.compare(a[0], b[0]);
        if (c == 0) c = Double.compare(b[1], a[1]);
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
    /** The unweighted search estimate from node (parity tool and tests). */
    double estimate(Node node) { return Math.max(remaining(node), digAware() ? field(node) : 0); }

    private boolean canRecoverStart() {
        return edits != null && edits.stepEdits && !edits.breakCosts.isEmpty()
                && integral(start.y) && fullDry(at(start.x, start.y - 1, start.z));
    }

    private static final int FLOOD_LIMIT = 4096;

    /**
     * A near goal must not burn the node limit. When no edit can create support (no place, pillar or bridge),
     * flood backward from the goal over potential stances; if the flood closes without the start the goal is
     * unreachable, and the A* is skipped. Sound because breaking never creates support: every node the search
     * can stand on is a cell that has support now (a collision top below it, a climbable cell) or an unknown
     * cell. The relaxed model is a superset of the real moves: horizontal reach Chebyshev maxGap+1 (walks,
     * diagonals, gap jumps); forward rise at most 1 and drop at most maxDrop, on the exact heights;
     * climbables link vertically (the window covers the column); headroom must be exact without a way to
     * break, else only breakable unprotected full cubes may stand in it; null (unknown) cells are stances at
     * their own height and the one above; in the goal's own column only a drop from above or a climbable
     * links stances. Anything else (fluid in reach, goal reach mode, flood past FLOOD_LIMIT, start reached)
     * gives up and the normal search runs. Not excluded, so still a superset: protected cells as stances.
     */
    private boolean provablyUnreachable() {
        if (reach != null || (edits != null && (edits.allowPlace || edits.allowPillar || edits.allowBridge))) return false;
        var startCell = cell(start.x, (int) Math.floor(start.y), start.z);
        if (startCell == null || !dry(startCell)) return false;
        int reachXZ = Math.max(maxGap, 0) + 1, r = (int) Math.ceil(radius);
        boolean noBreak = unknownPenalty == 0 && (edits == null || edits.breakCosts.isEmpty());
        var seen = new HashSet<Node>();
        var queue = new ArrayDeque<Node>();
        for (int dx = -r; dx <= r; dx++) for (int dz = -r; dz <= r; dz++)
            if (Math.hypot(dx, dz) <= radius) {
                var seed = new Node(goal.x + dx, goal.y, goal.z + dz);
                if (isStart(seed)) return false;
                if (seen.add(seed)) queue.add(seed);
            }
        while (!queue.isEmpty()) {
            Node p = queue.poll();
            for (int dx = -reachXZ; dx <= reachXZ; dx++) for (int dz = -reachXZ; dz <= reachXZ; dz++) {
                var hs = potentialStances(p.x + dx, p.z + dz, p.y - 1 - EPS, p.y + maxDrop + EPS);
                if (hs == null) return false;
                for (double h : hs) {
                    // Same column: only a drop from above, or a climbable, links two stances (no place, no pillar).
                    if (dx == 0 && dz == 0 && (Math.abs(h - p.y) < EPS || (h < p.y && !climbable(cell(p.x, (int) Math.floor(h + EPS), p.z))
                            && !climbable(cell(p.x, (int) Math.floor(p.y + EPS), p.z))))) continue;
                    var q = new Node(p.x + dx, h, p.z + dz);
                    // No way to break and no unknown planning: a walking rise is exactly stepClear (jump room, swept
                    // box); ladders and a drop in the same column are exempt.
                    if (noBreak && p.y > h + EPS && !(dx == 0 && dz == 0) && !climbable(cell(q.x, (int) Math.floor(h + EPS), q.z))
                            && !climbable(cell(p.x, (int) Math.floor(p.y + EPS), p.z)) && !stepClear(q, p)) continue;
                    if (isStart(q)) return false;
                    if (!seen.add(q)) continue;
                    if (seen.size() > FLOOD_LIMIT) return false;
                    queue.add(q);
                }
            }
        }
        return true;
    }
    /**
     * A goal disk without a cell that has, or could be given, support at goal height is unreachable at any
     * distance; searching it expands the whole node limit (run 20261117T015107803188Z: 64 capped plans at a
     * pond edge over protected air). Possible: unknown cells, fluid or a climbable at goal height, a support
     * top at goal height now, or with place/pillar/bridge an unprotected support cell that is empty or
     * breakable. Water under the goal is no support: a swimmer floats a block lower. Headroom as headroomOk.
     */
    private boolean noGoalStance() {
        if (reach != null || !integral(goal.y)) return false;
        int r = (int) Math.ceil(radius), gy = (int) goal.y;
        boolean canPlace = edits != null && (edits.allowPlace || edits.allowPillar || edits.allowBridge);
        for (int dx = -r; dx <= r; dx++) for (int dz = -r; dz <= r; dz++) {
            if (Math.hypot(dx, dz) > radius) continue;
            int x = goal.x + dx, z = goal.z + dz;
            if (isStart(new Node(x, goal.y, z))) return false;
            for (int cy = gy - 2; cy <= gy + 1; cy++) if (cell(x, cy, z) == null) return false;
            var below = cell(x, gy - 1, z);
            // Swimming or wading at goal height, or a climbable to hang in: possible.
            if (!dry(cell(x, gy, z)) || !dry(cell(x, gy + 1, z)) || climbable(cell(x, gy, z))) return false;
            for (int cy = gy - 2; cy <= gy - 1; cy++)
                for (var box : boxes(cell(x, cy, z)))
                    if (Math.abs(cy + at(box, 4) - goal.y) < EPS && at(box, 0) < .8 - EPS && at(box, 3) > .2 + EPS
                            && at(box, 2) < .8 - EPS && at(box, 5) > .2 + EPS && headroomOk(x, z, cy, goal.y)) return false;
            if (canPlace && !edits.protectedCells.contains(key(x, gy - 1, z))
                    && (below == null || boxes(below).isEmpty() || edits.breakCosts.containsKey(below.get("block")))
                    && headroomOk(x, z, gy - 1, goal.y)) return false;
        }
        return true;
    }
    /** The start's column and height, or the cell it hangs in. */
    private boolean isStart(Node n) {
        return n.x == start.x && n.z == start.z && n.y >= Math.floor(start.y) - EPS && n.y <= start.y + EPS;
    }

    /**
     * Headroom for a stance at height h on the support in cell sy. Without a way to break it is the exact
     * clearColumn test; with one, a cell above only needs to be empty or a breakable, unprotected full cube.
     */
    private boolean headroomOk(int x, int z, int sy, double h) {
        if (edits == null || edits.breakCosts.isEmpty()) return clearColumn(x, z, h, h + 1.8);
        for (int cy = (int) Math.floor(h + EPS); cy < Math.ceil(h + 1.8 - EPS); cy++) {
            var c = cell(x, cy, z);
            if (c == null || cy == sy || boxes(c).isEmpty()) continue;
            if (!fullDry(c) || edits.protectedCells.contains(key(x, cy, z)) || !edits.breakCosts.containsKey(c.get("block")))
                return false;
        }
        return true;
    }

    /** Heights in [lo, hi] where the column may hold a stance (see provablyUnreachable); null when fluid is in range. */
    private List<Double> potentialStances(int x, int z, double lo, double hi) {
        var found = new java.util.TreeSet<Double>();
        for (int cy = (int) Math.floor(lo) - 1; cy <= Math.floor(hi); cy++) {
            var c = cell(x, cy, z);
            if (c == null) { found.add((double) cy); found.add(cy + 1.0); continue; }
            if (!dry(c)) return null;
            if (climbable(c)) found.add((double) cy);
            for (var box : boxes(c))
                if (at(box, 0) < .8 - EPS && at(box, 3) > .2 + EPS && at(box, 2) < .8 - EPS && at(box, 5) > .2 + EPS
                        && headroomOk(x, z, cy, cy + at(box, 4)))
                    found.add(cy + at(box, 4));
        }
        found.removeIf(h -> h < lo || h > hi);
        return new ArrayList<>(found);
    }

    /**
     * Expands until the goal, an unknown-space exit, nodeLimit total expansions or the
     * System.nanoTime() deadline. "searching" means call again; otherwise the result is final.
     */
    Result advance(int nodeLimit, long deadlineNanos) {
        if (terminal != null) return terminal;
        if (best == null) {
            // A body hanging in a ladder/scaffold column is at a valid start whatever its fractional y.
            if (climb && !integral(start.y) && climbable(cell(start.x, (int) Math.floor(start.y), start.z)))
                start = new Node(start.x, Math.floor(start.y), start.z);
            if (!stance(start) && !(climb && climbOnly(start)) && !canRecoverStart() && !submerged(start))
                return terminal = new Result("unreachable", List.of(), 0, 0, List.of());
            if (provablyUnreachable()) return terminal = new Result("unreachable", List.of(), 0, 0, List.of());
            if (noGoalStance()) return terminal = new Result("unreachable", List.of(), 0, 0, List.of());
            best = start;
            costs.put(start, 0.0); nodes.add(start);
            frontier.add(new double[]{heuristic(start), 0, 0, 0});
        }
        int slice = 0;
        while (!frontier.isEmpty() && expanded < nodeLimit) {
            if ((++slice & 63) == 0 && System.nanoTime() > deadlineNanos) return new Result("searching", List.of(), expanded, 0, List.of());
            var entry = frontier.poll();
            int index = (int) entry[2];
            Node node = nodes.get(index);
            double cost = entry[1];
            if (cost != costs.get(node)) continue;
            if (entry[3] == 1)  // the cheapest way on runs through unknown space
                return terminal = trim(result("segment", node, cost));
            expanded++;
            // Later edges from a node see the edits of the edge that reached it.
            overlay = null;
            if (edits != null) overlay = overlayOf(plans.get(node));
            double estimate = closeness(node);
            if (estimate < closeness(best)) best = node;
            for (int i = 0; baritoneSearch && i < COEFFICIENTS.length; i++) {
                double value = estimate + cost / COEFFICIENTS[i];
                if (value < coefValue[i]) { coefValue[i] = value; coefBest[i] = node; }
            }
            if (distance(node) == 0) return terminal = result("reached", node, cost);
            if (unknownPenalty > 0 && !node.equals(start) && frontierNode(node)) {
                double total = cost + exitRemainder(node);
                frontier.add(new double[]{total, cost, index, 1});
                if (total < exitValue) { exitValue = total; exit = node; }
            }
            for (var move : moves(node)) {
                if (!excluded.isEmpty() && excluded.contains(List.of(node, move.target))) continue;
                double total = cost + survival(move.target, move.cost) * (baritoneSearch && favored.contains(move.target) ? FAVOR : 1);
                if (total < costs.getOrDefault(move.target, Double.POSITIVE_INFINITY)) {
                    costs.put(move.target, total); previous.put(move.target, node);
                    if (edits != null) {
                        int d = depth.getOrDefault(node, 0);
                        if (move.edit == null) plans.remove(move.target); else { plans.put(move.target, move.edit); d++; }
                        depth.put(move.target, d);
                    }
                    nodes.add(move.target);
                    frontier.add(new double[]{total + heuristic(move.target), total, nodes.size() - 1, 0});
                }
            }
        }
        Node end = exit;
        if (end == null) {
            end = best;
            for (var node : baritoneSearch ? coefBest : new Node[0])
                if (node != null && node.distance(start) > MIN_DIST_PATH) { end = node; break; }
        }
        overlay = null;
        return terminal = trim(result(frontier.isEmpty() ? "unreachable" : "incomplete", end, costs.get(end)));
    }

    /** Settings.pathCutoff*: drop the last 10% of long non-goal routes. */
    private Result trim(Result r) {
        int n = r.route.size();
        if (!baritoneSearch || n < CUTOFF_MIN) return r;
        var route = r.route.subList(0, Math.max(2, (int) (n * CUTOFF_FACTOR)));
        var targets = new HashSet<>(route.subList(1, route.size()));
        var steps = r.steps.stream().filter(e -> targets.contains(e.target)).toList();
        return new Result(r.status, route, r.expanded, costs.get(route.get(route.size() - 1)), steps);
    }

    // Partial-route estimate (remaining(node, 1)): walk rate when planning
    // through unknown space, else the Baritone heuristic.
    private double closeness(Node node) {
        return ticks ? (unknownPenalty > 0 ? horizontal(node) * WALK + vertical(node, false, false) : remaining(node)) : distance(node);
    }
    private double horizontal(Node node) {
        double ax = Math.abs(node.x - goal.x), az = Math.abs(node.z - goal.z);
        double h = Math.max(0, (baritoneSearch ? Math.abs(ax - az) + Math.min(ax, az) * SQRT2 : Math.hypot(ax, az)) - radius);
        return reach == null ? h : Math.max(0, h + radius - reach);
    }
    private double vertical(Node node, boolean exitEstimate, boolean buried) {
        double rise = goal.y - node.y;
        if (reach != null) {
            rise -= 1.12;
            rise = Math.copySign(Math.max(0, Math.abs(rise) - reach), rise);
        }
        if (buried && rise > 0) return rise * DIG_RISE;
        double v = rise > 0 ? rise * JUMP : -rise * fallTicks(2) / 2;
        // The terrain field (digAware) prices digging down without solidBelow's column overshoot.
        if (rise < 0 && edits != null && edits.stepEdits && !digAware()) v += solidBelow(node, (int) Math.ceil(-rise));
        double h = horizontal(node);
        if (exitEstimate && rise > h) v += (rise - h) * WALK;
        return v;
    }
    /** Dig price (edit, break, chain) of n known breakable blocks straight below the node, else 0. Without it a buried
     *  goal is priced at the fall rate and the search floods the surface (run 20261112T033141102600Z).
     *  ponytail: column only; a cave beside it is reachable more cheaply, so the estimate may overshoot. */
    private double solidBelow(Node node, int n) {
        double cheapest = edits.breakCosts.values().stream().mapToDouble(Double::doubleValue).min().orElse(0);
        int top = (int) Math.floor(node.y) - 1;
        for (int y = top; y > top - n; y--) {
            var c = cell(node.x, y, node.z);
            if (!fullDry(c) || !edits.breakCosts.containsKey(c.get("block"))) return 0;
        }
        int d = depth.getOrDefault(node, 0);  // editCost adds EDIT_CHAIN per edit already on the route
        return n * (EDIT_STEP + cheapest) + EDIT_CHAIN * (n * d + n * (n - 1) / 2.0);
    }
    /** The unknown remainder from a frontier node, at the unknown penalty (D3). */
    private double exitRemainder(Node node) {
        double walk = ticks ? unknownPenalty * (WALK * horizontal(node) + vertical(node, true, buried(node))) : unknownPenalty * distance(node);
        // Under rock the unknown is dug, not walked: never cheaper than the relaxed dig estimate (run 20261113T174328610209Z).
        if (digAware() && coveredAbove(node.x, (int) Math.floor(node.y) + 1, node.z))
            return Math.max(walk, field(node) + EDIT_STEP);
        return walk;
    }
    /** Known solid non-foliage cover over the node with no known air above it, below goal height. */
    private boolean buried(Node node) {
        boolean covered = false;
        for (int y = (int) Math.floor(node.y) + 2; y < Math.floor(goal.y); y++) {
            var cell = cell(node.x, y, node.z);
            if (cell == null) continue;
            if (!boxes(cell).isEmpty() && !foliage(cell)) covered = true;
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

    private Result result(String status, Node end, double cost) {
        var route = new ArrayList<Node>();
        for (Node node = end; node != null; node = previous.get(node)) route.add(node);
        Collections.reverse(route);
        var steps = new ArrayList<Edit>();
        for (int i = 1; i < route.size(); i++) {
            var edit = plans.get(route.get(i));
            if (edit != null && edit.source.equals(route.get(i - 1))) steps.add(edit);
        }
        return new Result(status, route, expanded, cost, steps);
    }

    // Goal: horizontal disk of the radius at exact standing height.
    double distance(Node node) {
        if (reach != null) {
            if (!avoidStances.contains(node) && interacts(node)) return 0;
            return Math.max(1e-9, Math.sqrt(sq(node.x - goal.x) + sq(node.y + 1.12 - goal.y)
                    + sq(node.z - goal.z)) - reach);
        }
        return Math.max(Math.max(0, Math.hypot(node.x - goal.x, node.z - goal.z) - radius), Math.abs(node.y - goal.y));
    }

    private boolean interacts(Node node) {
        double ex = node.x + .5, ey = node.y + 1.62, ez = node.z + .5;
        double dx = goal.x + .5 - ex, dy = goal.y + .5 - ey, dz = goal.z + .5 - ez;
        double distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (distance > reach) return false;
        int steps = Math.max(1, (int)Math.ceil(distance / .1));
        for (int i = 0; i < steps; i++) {
            int x = (int)Math.floor(ex + dx * i / steps), y = (int)Math.floor(ey + dy * i / steps);
            int z = (int)Math.floor(ez + dz * i / steps);
            if (x == goal.x && y == goal.y && z == goal.z) return true;
            var c = cell(x, y, z);
            if (c == null || !boxes(c).isEmpty()) return false;
        }
        return true;
    }

    private double survival(Node node, double edge) {
        if (hazardPenalty != 0 && integral(node.y)) {
            boolean hazard = false;
            for (var d : DIRECTIONS) for (int y = (int)node.y - 1; y <= (int)node.y; y++) {
                var c = baseCell(node.x + d[0], y, node.z + d[1]);
                if (c != null && (HAZARDS.contains(c.get("block")) || String.valueOf(c.get("fluid")).contains("lava"))) hazard = true;
            }
            if (hazard) edge += hazardPenalty;
        }
        if (mobCoefficient != 1) for (var t : threats) {
            if (sq(node.x - t[0]) + sq(node.y - t[1]) + sq(node.z - t[2]) <= sq(mobRadius)) {
                edge *= mobCoefficient; break;
            }
        }
        return edge;
    }

    // Baritone GoalXZ octile heuristic x COST_HEURISTIC plus GoalYLevel.
    double remaining(Node node) {
        if (!ticks) return distance(node);
        return (baritoneSearch ? COST_HEURISTIC : SPRINT) * horizontal(node) + vertical(node, false, false);
    }
    private double heuristic(Node node) {
        if (unknownPenalty > 0 && heuristicWeight == 0)
            return ticks ? unknownPenalty * (WALK * horizontal(node) + vertical(node, false, false)) : unknownPenalty * distance(node);
        double h = remaining(node);
        if (digAware()) h = Math.max(h, field(node));
        return (heuristicWeight == 0 ? 1 : heuristicWeight) * h;
    }

    // --- dig-aware heuristic: a relaxed cell graph searched backward from the goal ---
    // Without it a buried goal is priced at the walk rate (~4 ticks a block) while breaking stone costs ~150
    // (runs 20261113T174328610209Z, 20261113T171138236559Z: floods of 10000 nodes). The relaxed graph has one
    // node per cell (10 neighbours: 8 horizontal, up, down), no geometry, and each cell costs what entering it
    // would at least cost: walk rate if passable, EDIT_STEP + break cost if breakable, unknown space at the unknown
    // penalty (or at stone price when known solid lies above it); unbreakable solid and lava are walls.
    // Cheaper than any real route, so h stays a lower bound up to the vertical/diagonal simplifications.
    static final int FIELD_BUDGET = 150_000, FIELD_MARGIN_H = 16, FIELD_MARGIN_V = 8, COVER_SCAN = 64;
    private final Map<Long, Integer> fieldEditCount = new HashMap<>();  // dig moves on each stance's field path
    static final double INF = Double.POSITIVE_INFINITY;
    private PriorityQueue<double[]> fieldQueue;  // {distance, x, y, z}
    private final Map<Long, Double> fieldDistance = new HashMap<>();  // settled cells
    private final Map<Long, Double> fieldTentative = new HashMap<>();
    private final Map<Long, Double> enterCosts = new HashMap<>();
    private int fieldX0, fieldX1, fieldY0, fieldY1, fieldZ0, fieldZ1;
    private double stoneStep = -1, goalBreak = -1;
    private static final int[][] FIELD_STEPS = {{1, 0, 0}, {-1, 0, 0}, {0, 0, 1}, {0, 0, -1}, {1, 0, 1}, {1, 0, -1},
            {-1, 0, 1}, {-1, 0, -1}, {0, 1, 0}, {0, -1, 0}};

    private boolean digAware() {
        return edits != null && ticks && !edits.breakCosts.isEmpty();
    }
    /** Lower bound on the cost from the node to the goal through the relaxed graph (0 outside it). */
    private double field(Node node) {
        if (fieldQueue == null) fieldInit();
        int ny = (int) Math.floor(node.y);
        long k = key(node.x, ny, node.z);
        Double d = fieldDistance.get(k);
        while (d == null && !fieldQueue.isEmpty() && fieldDistance.size() < FIELD_BUDGET) {
            fieldSettle();
            d = fieldDistance.get(k);
        }
        if (d != null) {
            // editCost adds EDIT_CHAIN per edit already on the route: k more edits from depth dn add k*dn more.
            return d + EDIT_CHAIN * fieldEditCount.getOrDefault(k, 0) * (double) depth.getOrDefault(node, 0);
        }
        boolean inside = node.x >= fieldX0 && node.x <= fieldX1 && node.z >= fieldZ0 && node.z <= fieldZ1
                && ny >= fieldY0 && ny <= fieldY1;
        // Unsettled inside the box (budget spent): at least the queue minimum. Outside it: the straight line.
        // ponytail: a cell the relaxed box cannot reach also gets the line (its way may leave the box).
        if (inside && !fieldQueue.isEmpty()) return fieldQueue.peek()[0];
        if (inside) return lineEstimate(node);
        // Outside: no cheaper than the nearest box cell's value less a walk to it (triangle inequality), else
        // exits beyond the box looked free and segments ran away from the goal (plan-330: ended 40 blocks off).
        var clamp = new Node(Math.max(fieldX0, Math.min(fieldX1, node.x)),
                Math.max(fieldY0, Math.min(fieldY1, ny)), Math.max(fieldZ0, Math.min(fieldZ1, node.z)));
        int dx = Math.abs(clamp.x - node.x), dz = Math.abs(clamp.z - node.z);
        double walk = COST_HEURISTIC * (Math.max(dx, dz) + (SQRT2 - 1) * Math.min(dx, dz));
        return Math.max(lineEstimate(node), field(clamp) - walk);
    }
    private double extraCost(int x, int y, int z) {
        return edits.extraCosts.getOrDefault(key(x, y, z), 0.0);
    }
    private double goalBreakCost() {
        if (goalBreak < 0) {
            goalBreak = 0;
            for (int dy = 0; dy <= 1; dy++) {
                var c = baseCell(goal.x, (int) goal.y + dy, goal.z);
                if (fullDry(c) && edits.breakCosts.containsKey(c.get("block"))) goalBreak += edits.breakCosts.get(c.get("block")) + extraCost(goal.x, (int) goal.y + dy, goal.z);
            }
        }
        return goalBreak;
    }
    private void fieldInit() {
        fieldQueue = new PriorityQueue<>((a, b) -> Double.compare(a[0], b[0]));
        int mh = FIELD_MARGIN_H, mv = FIELD_MARGIN_V;
        fieldX0 = Math.min(start.x, goal.x) - mh; fieldX1 = Math.max(start.x, goal.x) + mh;
        fieldZ0 = Math.min(start.z, goal.z) - mh; fieldZ1 = Math.max(start.z, goal.z) + mh;
        int sy = (int) Math.floor(start.y), gy = (int) Math.floor(goal.y);
        fieldY0 = Math.min(sy, gy) - mv; fieldY1 = Math.max(sy, gy) + mv;
        // Goal stances: the radius disk at goal height, or (interaction goals) stances with unobstructed sight in reach.
        int r = (int) Math.ceil(reach != null ? reach + 1 : radius), ry = reach != null ? r + 2 : 0;
        for (int dx = -r; dx <= r; dx++) for (int dz = -r; dz <= r; dz++) for (int dy = -ry; dy <= ry; dy++) {
            if (reach != null ? Math.sqrt(dx * dx + sq(dy + 1.62 - .5) + dz * dz) > reach + 1 : Math.hypot(dx, dz) > radius) continue;
            int x = goal.x + dx, y = gy + dy, z = goal.z + dz;
            if (x < fieldX0 || x > fieldX1 || z < fieldZ0 || z > fieldZ1 || y < fieldY0 || y > fieldY1) continue;
            if (reach != null && !losClearForField(x, y, z)) continue;
            fieldTentative.put(key(x, y, z), 0.0);
            fieldQueue.add(new double[]{0, x, y, z});
        }
    }
    private boolean losClearForField(int sx, int sy, int sz) {
        double ex = sx + .5, ey = sy + 1.62, ez = sz + .5;
        double dx = goal.x + .5 - ex, dy = goal.y + .5 - ey, dz = goal.z + .5 - ez;
        double dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
        int steps = Math.max(1, (int) Math.ceil(dist / .1));
        for (int i = 0; i < steps; i++) {
            int x = (int) Math.floor(ex + dx * i / steps), y = (int) Math.floor(ey + dy * i / steps);
            int z = (int) Math.floor(ez + dz * i / steps);
            if (x == goal.x && y == goal.y && z == goal.z) return true;
            if (x == sx && z == sz && (y == sy || y == sy + 1)) continue;
            var c = baseCell(x, y, z);
            if (c != null) {
                if (!boxes(c).isEmpty()) return false;
            } else if (unknownPenalty <= 0 || coveredAboveEnter(x, y, z)) {
                return false;
            }
        }
        return true;
    }
    /**
     * Settles stance s (feet cell) and relaxes every stance c with a move c -> s. The move pays the walk or
     * vertical rate plus, for the cells of s's body (feet, head) that c's body does not already occupy, their
     * break costs and one EDIT_STEP. Digging is cardinal or vertical only, as in planEdit/downward; diagonals
     * walk. Support is ignored (relaxed), so the field stays below the real price.
     */
    private void fieldSettle() {
        var e = fieldQueue.poll();
        int x = (int) e[1], y = (int) e[2], z = (int) e[3];
        long k = key(x, y, z);
        if (fieldDistance.putIfAbsent(k, e[0]) != null) return;
        int edits = fieldEditCount.getOrDefault(k, 0);
        for (var d : FIELD_STEPS) {
            int cx = x + d[0], cy = y + d[1], cz = z + d[2];
            if (cx < fieldX0 || cx > fieldX1 || cz < fieldZ0 || cz > fieldZ1 || cy < fieldY0 || cy > fieldY1) continue;
            long ck = key(cx, cy, cz);
            if (fieldDistance.containsKey(ck)) continue;
            // Body cells of s that c's body does not cover: rising one, only s's head is new; dropping one, only s's feet.
            // c = s + d: d[1] == 1 means c is above s (the move drops), so c's feet already open s's head.
            double feet = d[1] == -1 ? 0 : enterCost(x, y, z), head = d[1] == 1 ? 0 : enterCost(x, y + 1, z);
            if (feet == INF || head == INF) continue;
            boolean dig = feet > 0 || head > 0;
            boolean diagonal = d[0] != 0 && d[2] != 0;
            if (dig && diagonal) continue;
            // EDIT_CHAIN: the j-th edit counted back from the goal pays j chains (k edits sum to k(k-1)/2 either way),
            // so the field prefers few edits as editCost does.
            double step = dig ? EDIT_STEP + feet + head + EDIT_CHAIN * edits : 0;
            step += d[1] == -1 ? JUMP : d[1] == 1 ? (dig ? fallTicks(1) : fallTicks(2) / 2) : COST_HEURISTIC * (diagonal ? SQRT2 : 1);
            boolean enteredUnknown = (d[1] != -1 && baseCell(x, y, z) == null) || (d[1] != 1 && baseCell(x, y + 1, z) == null);
            if (!dig && unknownPenalty > 1 && enteredUnknown) step += (unknownPenalty - 1) * COST_HEURISTIC;
            double total = e[0] + step;
            if (total < fieldTentative.getOrDefault(ck, INF)) {
                fieldTentative.put(ck, total);
                fieldEditCount.put(ck, edits + (dig ? 1 : 0));
                fieldQueue.add(new double[]{total, cx, cy, cz});
            }
        }
    }
    /** Price of opening one body cell: 0 passable, its break cost if breakable, INF for walls and lava. */
    private double enterCost(int x, int y, int z) {
        long k = key(x, y, z);
        Double known = enterCosts.get(k);
        if (known == null) { known = computeEnter(x, y, z); enterCosts.put(k, known); }
        return known;
    }
    private double computeEnter(int x, int y, int z) {
        var c = baseCell(x, y, z);
        if (c == null) {
            if (unknownPenalty <= 0) return INF;
            return coveredAboveEnter(x, y, z) ? stoneStep() - EDIT_STEP : 0;
        }
        if (!dry(c)) return String.valueOf(c.get("fluid")).contains("lava") ? INF : 0;
        if (boxes(c).isEmpty() || !fullCube(c)) return 0;
        if (edits.protectedCells.contains(key(x, y, z))) return INF;
        Double b = edits.breakCosts.get(c.get("block"));
        return b == null ? INF : b + extraCost(x, y, z);
    }
    /** Outside the field: straight-line walk plus the cheapest break for each known breakable solid on the line. */
    private double lineEstimate(Node node) {
        double cheapest = edits.breakCosts.values().stream().mapToDouble(Double::doubleValue).min().orElse(0);
        double dx = goal.x - node.x, dy = goal.y - node.y, dz = goal.z - node.z;
        int n = (int) Math.ceil(Math.max(Math.abs(dx), Math.max(Math.abs(dy), Math.abs(dz))));
        int solid = 0;
        for (int i = 1; i <= n; i++) {
            var c = baseCell((int) Math.floor(node.x + .5 + dx * i / n), (int) Math.floor(node.y + dy * i / n),
                    (int) Math.floor(node.z + .5 + dz * i / n));
            if (fullDry(c) && edits.breakCosts.containsKey(c.get("block"))) solid++;
        }
        return WALK * horizontal(node) + solid * cheapest;
    }
    private static boolean foliage(Map<String, Object> c) {
        String b = String.valueOf(c.getOrDefault("block", ""));
        return b.endsWith("_leaves") || b.endsWith("_log") || b.endsWith("_wood")
                || b.endsWith("_stem") || b.endsWith("_hyphae");
    }
    private int coverTop(int x, int z) {
        return coverTops.computeIfAbsent(((long) x << 32) ^ (z & 0xFFFFFFFFL), k -> {
            int top = Math.max(fieldY1, (int) Math.floor(Math.max(start.y, goal.y))) + COVER_SCAN;
            int bottom = Math.min(fieldY0, (int) Math.floor(Math.min(start.y, goal.y))) - COVER_SCAN;
            for (int cy = top; cy >= bottom; cy--) {
                var c = baseCell(x, cy, z);
                if (c != null && !boxes(c).isEmpty() && !foliage(c)) return cy;
            }
            return Integer.MIN_VALUE;
        });
    }
    /** Known non-foliage terrain higher up the column: unknown space or underground frontiers there are under ground/rock. */
    private boolean coveredAbove(int x, int y, int z) {
        return y < coverTop(x, z);
    }
    /** Unknown cell (x, y, z) sits directly below a solid non-foliage ceiling with no known open air between them. */
    private boolean coveredAboveEnter(int x, int y, int z) {
        int top = coverTop(x, z);
        if (y >= top) return false;
        for (int cy = y + 1; cy <= top; cy++) {
            var c = baseCell(x, cy, z);
            if (c != null && !foliage(c)) return !boxes(c).isEmpty();
        }
        return false;
    }
    private final Map<Long, Integer> coverTops = new HashMap<>();  // highest known non-foliage solid per column
    /** What digging through plain stone costs per block (mean break cost when the profile has no stone). */
    private double stoneStep() {
        if (stoneStep < 0) {
            Double stone = edits.breakCosts.get("minecraft:stone");
            stoneStep = EDIT_STEP + (stone != null ? stone
                    : edits.breakCosts.values().stream().mapToDouble(Double::doubleValue).average().orElse(0));
        }
        return stoneStep;
    }

    private record Move(Node target, double cost, Edit edit) {
        Move(Node target, double cost) { this(target, cost, null); }
    }

    // Fixed move order: equal-cost ties keep the first move found.
    private List<Move> moves(Node node) {
        var out = new ArrayList<Move>(8);
        int x = node.x, z = node.z; double y = node.y;
        if (submerged(node) && !stance(node)) { swimOut(node, out); return out; }
        if (node.equals(start) && !stance(start) && !(climb && climbOnly(node))) {
            if (canRecoverStart()) for (var d : DIRECTIONS) for (int dy = 0; dy <= 1; dy++)
                addEdit(planEdit(node, new Node(x + d[0], y + dy, z + d[1])), heightCost(dy), node, out);
            return out;
        }
        if (climb && climbOnly(node)) { ladderMoves(node, out); return out; }
        boolean onWater = swim && (surfaceNode(node) || (plannerSwim && flowingSurface(node)));
        var below = at(x, y - 1, z);
        boolean gapRoom = maxGap > 0 && integral(y) && below != null && dry(below) && fullCube(below);
        boolean editReady = edits != null && edits.stepEdits && integral(y) && editStance(node);
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
                                ticks ? WALK * (width + 1) + (baritoneSearch ? JUMP_PENALTY : 0) : width + 3.5));
            var reached = editReady ? new HashSet<Node>() : null;
            var possible = shapes ? surfaces(x + d[0], z + d[1], y - maxDrop, y + 1) : blockSurfaces(x + d[0], z + d[1], y);
            for (double ty : possible) {
                var target = new Node(x + d[0], ty, z + d[1]);
                if (shapes ? stepClear(node, target) : blockStepClear(node, target)) {
                    out.add(new Move(target, heightCost(ty - y)));
                    if (reached != null) reached.add(target);
                }
            }
            if (editReady) for (int dy = 0; dy <= 1; dy++) {
                var target = new Node(x + d[0], y + dy, z + d[1]);
                if (!reached.contains(target)) addEdit(planEdit(node, target), heightCost(dy), node, out);
            }
        }
        if (diagonals) for (var d : DIAGONALS) {
            var target = new Node(x + d[0], y, z + d[1]);
            if (!stance(target)) continue;
            if (!baritoneDiagonal) {
                if (stance(new Node(x + d[0], y, z)) && stance(new Node(x, y, z + d[1])))
                    out.add(new Move(target, flat() * SQRT2));
                continue;
            }
            int a = side(x + d[0], y, z), b = side(x, y, z + d[1]);
            if (a < 0 || b < 0 || (a == 0 && b == 0)) continue;
            if (plannerSwim) {  // MovementDiagonal: water at either end swims, no sprint
                var wet = surfaceWater(target);
                if (wet == null) wet = surfaceWater(node);
                if (wet != null) { out.add(new Move(target, (ticks ? SWIM : 1.8) * multiplier(wet) * SQRT2)); continue; }
            }
            if (!swept(node, target)) continue;  // thin panel on a column edge: the motor would refuse the cut
            // Edging past one blocked side: walk pace, no sprint (MovementDiagonal).
            out.add(new Move(target, a == 0 || b == 0 ? WALK * SQRT2 * (SQRT2 - .001) : flat() * SQRT2));
        }
        if (climb) ladderMoves(node, out);
        if (extra) extraMoves(node, out);
        return out;
    }
    private boolean stance(Node n) { return swim ? supported(n) : shapes ? standable(n) : blockStandable(n); }
    private List<Double> blockSurfaces(int x, int z, double y) {
        if (!integral(y)) return List.of();
        var result = new ArrayList<Double>();
        for (int dy = 0; dy <= 1; dy++) if (blockStandable(new Node(x, y + dy, z))) result.add(y + dy);
        for (int dy = -1; dy >= -maxDrop; dy--) if (blockStandable(new Node(x, y + dy, z))) result.add(y + dy);
        return result;
    }
    private boolean blockStepClear(Node from, Node to) {
        if (!blockStandable(to) || !integral(from.y) || !integral(to.y)) return false;
        int y = (int)from.y, ty = (int)to.y;
        if (ty == y + 1) return empty(at(from.x, y + 2, from.z)) && empty(at(from.x, y + 3, from.z)) && empty(at(to.x, y + 3, to.z));
        if (ty < y) for (int cy = ty; cy < y + 2; cy++) if (!empty(at(to.x, cy, to.z))) return false;
        return true;
    }

    // --- swimming ---

    private static boolean dry(Map<String, Object> c) { return "minecraft:empty".equals(c.getOrDefault("fluid", "minecraft:empty")); }
    private static boolean empty(Map<String, Object> c) {
        return c != null && dry(c) && ("minecraft:air".equals(c.get("block")) || List.of().equals(c.get("collision_boxes")));
    }
    private static boolean climbable(Map<String, Object> c) { return c != null && dry(c) && CLIMBABLE.contains(c.get("block")); }
    private static boolean waterFluid(Object fluid) {
        return "minecraft:water".equals(fluid) || "minecraft:flowing_water".equals(fluid);
    }
    private static boolean water(Map<String, Object> c, boolean still) {
        if (c == null || !List.of().equals(c.get("collision_boxes")) || HAZARDS.contains(c.get("block"))) return false;
        Object fluid = c.get("fluid");
        if ("minecraft:flowing_water".equals(fluid)) return !still;
        if (!"minecraft:water".equals(fluid)) return false;
        Object level = c.get("properties") instanceof Map<?, ?> m ? m.get("level") : null;
        return still == "0".equals(level == null ? "0" : level);
    }
    private static boolean integral(double y) { return y == Math.floor(y); }
    private Map<String, Object> at(int x, double y, int z) { return integral(y) ? cell(x, (int) y, z) : null; }
    boolean surfaceNode(Node n) {
        return integral(n.y) && water(at(n.x, n.y - 1, n.z), true) && safe(at(n.x, n.y, n.z))
                && clearColumn(n.x, n.z, n.y, n.y + 2.5);
    }
    private boolean flowingSurface(Node n) {
        return integral(n.y) && water(at(n.x, n.y - 1, n.z), false) && safe(at(n.x, n.y, n.z))
                && clearColumn(n.x, n.z, n.y, n.y + 2.5);
    }
    private Map<String, Object> surfaceWater(Node n) {
        var below = at(n.x, n.y - 1, n.z);
        if (below == null || !waterFluid(below.get("fluid"))) return null;
        return surfaceNode(n) || flowingSurface(n) ? below : null;
    }
    private static double multiplier(Map<String, Object> wet) { return water(wet, true) ? 1 : FLOWING; }
    /** water_movement.supported_node with shapes: a surface stance, or standable over a safe cell. */
    boolean supported(Node n) {
        return surfaceNode(n) || ((shapes ? standable(n) : blockStandable(n)) && safe(cell(n.x, (int) Math.ceil(n.y) - 1, n.z)));
    }
    /** Feet inside still or flowing water: no stance, but a body already there must swim out. */
    private boolean submerged(Node n) { return integral(n.y) && wadeable(at(n.x, n.y, n.z)); }
    /** From a submerged cell: up, or sideways into more water, until the feet clear the surface. */
    private void swimOut(Node node, List<Move> out) {
        double cost = ticks ? SWIM : 1.8;
        var up = at(node.x, node.y + 1, node.z);
        if (wadeable(up) || (empty(up) && surfaceNode(new Node(node.x, node.y + 1, node.z)))) {
            out.add(new Move(new Node(node.x, node.y + 1, node.z), cost));
        }
        for (var d : DIRECTIONS) {
            var target = new Node(node.x + d[0], node.y, node.z + d[1]);
            if (submerged(target)) out.add(new Move(target, cost));
        }
    }
    /** Swim cost under planner_swim; 0 when there is no swim edge. */
    private double swimCost(Node source, Node target) {
        if (!plannerSwim) {
            return (surfaceNode(source) || surfaceNode(target)) && supported(source) && supported(target)
                    ? (ticks ? SWIM : 1.8) : 0;
        }
        var wetSource = surfaceWater(source); var wetTarget = surfaceWater(target);
        if (wetSource == null && wetTarget == null) return 0;
        for (var n : List.of(source, target)) if (!supported(n) && !flowingSurface(n)) return 0;
        return (ticks ? SWIM : 1.8) * multiplier(wetTarget != null ? wetTarget : wetSource);
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
    private static boolean scaffolding(Map<String, Object> c) { return c != null && "minecraft:scaffolding".equals(c.get("block")); }
    /** A floor to stand or click on: a full cube, or scaffolding (it senses with no collision boxes, yet a body above it,
     *  not sneaking, stands on its top). */
    private static boolean sturdy(Map<String, Object> c) { return fullDry(c) || (c != null && dry(c) && scaffolding(c)); }

    /** ScaffoldingBlock.getDistance for a block placed at (x,y,z): the stored distance of scaffolding below, 0 on a sturdy
     *  top, else the least neighbour distance + 1 (7 = it falls). own: earlier places of this edit (-1 solid, else distance). */
    private int scaffoldDistance(int x, int y, int z, Map<Long, Integer> own, int budget) {
        var below = own.get(key(x, y - 1, z));
        if (below != null) return below >= 0 ? below : 0;
        var cellBelow = cell(x, y - 1, z);
        if (scaffolding(cellBelow)) return existingDistance(x, y - 1, z, own, budget);
        if (fullDry(cellBelow)) return 0;
        int best = 7;
        for (var d : DIRECTIONS) {
            var n = own.get(key(x + d[0], y, z + d[1]));
            if (n != null) { if (n >= 0) best = Math.min(best, n + 1); }
            else if (budget > 0 && scaffolding(cell(x + d[0], y, z + d[1])))
                best = Math.min(best, existingDistance(x + d[0], y, z + d[1], own, budget - 1) + 1);
        }
        return Math.min(best, 7);
    }
    private int existingDistance(int x, int y, int z, Map<Long, Integer> own, int budget) {
        if (cell(x, y, z).get("scaffold_distance") instanceof Integer d) return d;
        return scaffoldDistance(x, y, z, own, budget);  // the map's own scaffolding stands, so this is at most 6
    }
    /** Which places (lowest first) are scaffolding: held, supported within 6, and not clicked on scaffolding (the item
     *  redirects such a click sideways or up, away from the planned cell). The rest stay solid. */
    private List<int[]> scaffoldCells(List<int[]> places) {
        if (edits.scaffoldSupply <= 0) return List.of();
        var own = new HashMap<Long, Integer>();
        var out = new ArrayList<int[]>();
        int left = edits.scaffoldSupply;
        for (var p : places) {
            var ob = own.get(key(p[0], p[1] - 1, p[2]));
            boolean onScaffolding = ob != null ? ob >= 0 : scaffolding(cell(p[0], p[1] - 1, p[2]));
            int d = scaffoldDistance(p[0], p[1], p[2], own, 6);
            if (left > 0 && !onScaffolding && d <= 6) { out.add(new int[]{p[0], p[1], p[2], d}); own.put(key(p), d); left--; }
            else own.put(key(p), -1);
        }
        return out;
    }

    // --- Baritone extra moves without edits: ladders, water landings ---

    /** navigation.standable: full-cube floor, empty feet and head (integral heights only). */
    private boolean blockStandable(Node n) {
        if (!integral(n.y)) return false;
        var floor = at(n.x, n.y - 1, n.z);
        return floor != null && sturdy(floor) && empty(at(n.x, n.y, n.z)) && empty(at(n.x, n.y + 1, n.z));
    }
    /** collision_geometry.edit_stance: a dry block stance, or one wading in one-deep
     * water on a full block with the head in air. From wet feet the native planner
     * offered no edit edge and the body stood in a dug pocket (iron run
     * 20261112T205117541135Z). */
    private boolean editStance(Node n) {
        if (blockStandable(n)) return true;
        var floor = at(n.x, n.y - 1, n.z);
        // A top slab or a stair's high half is a flat floor too: a body on an eave
        // slab had no edit move and every plan was unreachable after one node
        // (large house run 20261117T075353967859Z).
        if (integral(n.y) && flatFloor(floor) && empty(at(n.x, n.y, n.z)) && empty(at(n.x, n.y + 1, n.z))) return true;
        return floor != null && fullCube(floor) && wadeable(at(n.x, n.y, n.z)) && empty(at(n.x, n.y + 1, n.z));
    }
    private boolean climbOnly(Node n) { return climbable(at(n.x, n.y, n.z)) && !blockStandable(n); }
    private boolean openOrClimb(Map<String, Object> c) { return empty(c) || climbable(c); }

    private void extraMoves(Node node, List<Move> out) {
        if (!integral(node.y) || !blockStandable(node)) return;
        if (edits != null) {
            var down = downward(node);  // MovementDownward: fall straight down, no walk-off
            addEdit(down, down == null ? 0 : fallTicks(node.y - down.target.y), node, out);
            addEdit(pillar(node), JUMP + JUMP_PENALTY, node, out);
        }
        for (var d : DIRECTIONS) {
            if (edits != null) addEdit(descend(node, d), heightCost(-1), node, out);
            if (swim) landings(node, d, out);
        }
    }
    private void ladderMoves(Node node, List<Move> out) {
        int x = node.x, z = node.z; double y = node.y;
        if (!integral(y)) return;
        if (climbable(at(x, y, z))) {
            var up = new Node(x, y + 1, z); var down = new Node(x, y - 1, z);
            boolean platform = "minecraft:scaffolding".equals(at(x, y, z).get("block")) && standable(up);
            if ((climbable(at(x, y + 1, z)) || platform) && openOrClimb(at(x, y + 2, z))) out.add(new Move(up, ticks ? LADDER_UP : 1.4));
            if (climbable(at(x, y - 1, z))) out.add(new Move(down, ticks ? LADDER_DOWN : 1.4));
            else letGo(node, out);
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
    /** Off the bottom of a climbable: let go and drop to the first stance or water below.
     *  Run 20261117T013535177027Z hung on a vine over a pond: with no way down the
     *  climb, every plan from there was unreachable in 3 nodes. */
    private void letGo(Node node, List<Move> out) {
        for (int h = 1; h <= LANDING_MAX; h++) {
            var target = new Node(node.x, node.y - h, node.z);
            var c = at(target.x, target.y, target.z);
            if (c == null || !boxes(c).isEmpty()) return;
            double cost = (ticks ? fallTicks(h) : 1 + .4 * h) + hurt(h);
            if (swim && submerged(target)) { out.add(new Move(target, cost)); return; }
            if (!dry(c)) return;
            if (stance(target)) { if (h <= maxDrop) out.add(new Move(target, cost)); return; }
        }
    }
    /** MovementDescend.dynamicFallCost water branch: walk off into still water at any height. */
    private void landings(Node node, int[] d, List<Move> out) {
        for (int h = 1; h <= LANDING_MAX; h++) {
            var target = new Node(node.x + d[0], node.y - h, node.z + d[1]);
            var c = at(target.x, target.y, target.z);
            if (c == null || !boxes(c).isEmpty()) return;
            if (waterLanding(node, target)) out.add(new Move(target, ticks ? WALK_OFF + fallTicks(h) : 1 + .4 * h));
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

    private double flat() { return ticks ? (sprint ? SPRINT : WALK) : 1; }

    private double heightCost(double dy) {
        if (!ticks) return 1 + .4 * Math.abs(dy) + hurt(-dy);
        if (dy > .5) return Math.max(JUMP, WALK) + (baritoneSearch ? JUMP_PENALTY : 0);  // MovementAscend
        if (dy < -.5) return WALK_OFF + Math.max(fallTicks(-dy), CENTER_AFTER_FALL) + hurt(-dy);  // Descend/Fall
        return flat();
    }

    /** Health a fall of h costs (one point per block past three), priced as a walk of
     *  HURT_TICKS_PER_POINT each: a body only drops farther than three blocks when
     *  allowed to (max_drop past 3, a last resort) and then still takes the stairs or
     *  a short detour over the drop. A 6-block drop (3 points) is a ~26-block walk. */
    static double hurt(double h) {
        double points = Math.ceil(h - 3 - EPS);
        return points > 0 ? points * HURT_TICKS_PER_POINT : 0;
    }

    /** -1 unknown or hazard below, 0 blocked, 1 passable. */
    private int side(int x, double y, int z) {
        int fy = (int) Math.floor(y);
        if (cell(x, fy, z) == null || cell(x, fy + 1, z) == null) return -1;
        if (shapes ? !clearColumn(x, z, y, y + 1.8) : !(empty(at(x, y, z)) && empty(at(x, y + 1, z)))) return 0;
        for (int cy = fy - 1; cy > fy - 2 - maxDrop; cy--) {
            var below = cell(x, cy, z);
            if (below == null) continue;
            if (HAZARDS.contains(below.get("block")) || String.valueOf(below.get("fluid")).contains("lava")) return -1;
            if (!boxes(below).isEmpty()) break;
        }
        return 1;
    }

    // --- collision geometry (mclab.collision_geometry) ---

    static long key(int x, int y, int z) {
        return (((long) x & 0x3FFFFFF) << 38) | (((long) y & 0xFFF) << 26) | ((long) z & 0x3FFFFFF);
    }
    private static long key(int[] p) { return key(p[0], p[1], p[2]); }
    private Map<String, Object> cell(int x, int y, int z) {
        long key = key(x, y, z);
        if (overlay != null) {
            var edited = overlay.get(key);
            if (edited != null) return edited;
        }
        return baseCell(x, y, z);
    }
    private Map<String, Object> baseCell(int x, int y, int z) {
        long key = key(x, y, z);
        if (!cells.containsKey(key)) cells.put(key, reader.cell(x, y, z));
        return cells.get(key);
    }
    private static boolean safe(Map<String, Object> cell) {
        return cell != null && "minecraft:empty".equals(cell.getOrDefault("fluid", "minecraft:empty"))
                && !HAZARDS.contains(cell.get("block"));
    }
    private static boolean wadeable(Map<String, Object> cell) {
        return cell != null && waterFluid(cell.get("fluid")) && List.of().equals(cell.get("collision_boxes"))
                && !HAZARDS.contains(cell.get("block"));
    }
    private static List<?> boxes(Map<String, Object> cell) {
        return cell.get("collision_boxes") instanceof List<?> list ? list : List.of();
    }
    private static double at(Object box, int i) { return ((Number) ((List<?>) box).get(i)).doubleValue(); }

    boolean clearColumn(int x, int z, double bottom, double top) {
        double cx0 = x + .5, cz0 = z + .5;
        double left = cx0 - RADIUS, right = cx0 + RADIUS, near = cz0 - RADIUS, far = cz0 + RADIUS;
        int feet = (int) Math.floor(bottom + EPS);
        for (int cx = (int) Math.floor(left + EPS); cx < Math.ceil(right - EPS); cx++)
            for (int cz = (int) Math.floor(near + EPS); cz < Math.ceil(far - EPS); cz++)
                for (int cy = feet; cy < Math.ceil(top - EPS); cy++) {
                    var cell = cell(cx, cy, cz);
                    if (cy == feet && wadeable(cell)) continue;  // collision_geometry.clear_body: feet may be wet
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
        var cached = overlay == null ? heights.get(key) : null;  // edited views are not cached
        if (cached != null) return cached;
        var cell = cell(x, y, z);
        var found = new ArrayList<Double>();
        if (safe(cell)) {
            String block = String.valueOf(cell.getOrDefault("block", ""));
            if (fullCube(cell) || block.endsWith("_slab") || block.endsWith("_stairs") || block.equals("minecraft:scaffolding"))
                for (var box : scaffolding(cell) && boxes(cell).isEmpty() ? List.of(List.of(0, 0, 0, 1, 1, 1)) : boxes(cell))
                    if (at(box, 0) < .8 - EPS && at(box, 3) > .2 + EPS && at(box, 2) < .8 - EPS && at(box, 5) > .2 + EPS) {
                        double h = y + at(box, 4);
                        if (clearColumn(x, z, h, h + 1.8)) found.add(h);
                    }
        }
        double[] result = found.stream().mapToDouble(Double::doubleValue).toArray();
        if (overlay == null) heights.put(key, result);
        return result;
    }
    /** collision_geometry.flat_floor: dry, with a box topped at the block top under the centre. */
    private static boolean flatFloor(Map<String, Object> c) {
        if (c == null || !dry(c)) return false;
        for (var box : boxes(c))
            if (Math.abs(at(box, 4) - 1) < EPS && at(box, 0) < .8 - EPS && at(box, 3) > .2 + EPS
                    && at(box, 2) < .8 - EPS && at(box, 5) > .2 + EPS) return true;
        return false;
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
        boolean clear;
        if (target.y > source.y) {
            // Jump room is above the take-off (feet peak 1.25 up): a block step into a
            // 2-high doorway under a roof is open, as in Baritone's MovementAscend
            // (small house run 20261116T234547601261Z searched 10000 nodes past one).
            double top = Math.max(target.y + 1.8, source.y + 1.8 + (target.y - source.y > .6 ? 1.2 : 0));
            clear = clearColumn(source.x, source.z, source.y, top) && clearColumn(target.x, target.z, target.y, top);
        } else clear = clearColumn(target.x, target.z, target.y, source.y + 1.8);
        if (!clear) return false;
        // A clear stance centre can hide a thin entry-face obstacle (an open
        // trapdoor on a house wall). Admit the same swept hitbox as the motor.
        return swept(source, target);
    }
    /** The motor's swept hitbox (RouteMotor.fits) when either column holds partial geometry. */
    private boolean swept(Node source, Node target) {
        if (!sideGeometry(source) && !sideGeometry(target)) return true;
        var geometry = new RouteGeometry(this::cell);
        double yaw = Math.toDegrees(Math.atan2(-(target.x - source.x), target.z - source.z));
        var pose = new RouteMotor.Pose(source.x + .5, source.y, source.z + .5, 0, 0, yaw, true, 20);
        var point = new RouteMotor.Point(target.x, target.y, target.z);
        double distance = Math.hypot(target.x - source.x, target.z - source.z);
        if (target.x != source.x && target.z != source.z)
            return geometry.safeDiagonal(pose, point, source.y, distance, false, maxDrop);
        return geometry.safe(pose, point, source.y, distance, target.y - source.y > .6, maxDrop);
    }
    private boolean sideGeometry(Node node) {
        for (int y = (int) Math.floor(node.y); y < Math.ceil(node.y + 1.8 - EPS); y++)
            if (!boxes(cell(node.x, y, node.z)).isEmpty()) return true;
        return false;
    }

    // --- edit edges (stage D) ---

    /**
     * Edit permissions and prices from the Python side (body_search.edit_payload). breakCosts
     * lists only blocks that may be broken: the hotbar holds their tool and they are not
     * protected; it is empty when breaking is off. placeSupply: blocks placeable in a row.
     */
    record EditPolicy(boolean stepEdits, Map<String, Double> breakCosts, Map<String, Double> fallCosts,
                      boolean allowPlace, boolean allowBridge, boolean allowPillar, int maxStack, int breakBudget,
                      int placeBudget, int placeSupply, Set<Long> protectedCells, Set<String> protectedBlocks,
                      String material, List<String> materials, Map<Long, Double> extraCosts, int scaffoldSupply) {
        EditPolicy(boolean stepEdits, Map<String, Double> breakCosts, Map<String, Double> fallCosts,
                   boolean allowPlace, boolean allowBridge, boolean allowPillar, int maxStack, int breakBudget,
                   int placeBudget, int placeSupply, Set<Long> protectedCells, Set<String> protectedBlocks,
                   String material, List<String> materials, Map<Long, Double> extraCosts) {
            this(stepEdits, breakCosts, fallCosts, allowPlace, allowBridge, allowPillar, maxStack, breakBudget,
                    placeBudget, placeSupply, protectedCells, protectedBlocks, material, materials, extraCosts, 0);
        }
        EditPolicy(boolean stepEdits, Map<String, Double> breakCosts, Map<String, Double> fallCosts,
                   boolean allowPlace, boolean allowBridge, boolean allowPillar, int maxStack, int breakBudget,
                   int placeBudget, int placeSupply, Set<Long> protectedCells, Set<String> protectedBlocks,
                   String material, List<String> materials) {
            this(stepEdits, breakCosts, fallCosts, allowPlace, allowBridge, allowPillar, maxStack, breakBudget,
                    placeBudget, placeSupply, protectedCells, protectedBlocks, material, materials, Map.of(), 0);
        }
        EditPolicy(boolean stepEdits, Map<String, Double> breakCosts, Map<String, Double> fallCosts,
                   boolean allowPlace, boolean allowBridge, boolean allowPillar, int maxStack, int breakBudget,
                   int placeBudget, int placeSupply, Set<Long> protectedCells, Set<String> protectedBlocks,
                   String material) {
            this(stepEdits, breakCosts, fallCosts, allowPlace, allowBridge, allowPillar, maxStack, breakBudget,
                    placeBudget, placeSupply, protectedCells, protectedBlocks, material, List.of(material), Map.of(), 0);
        }
        static EditPolicy fromJson(JsonObject o) {
            var breakCosts = costs(o.getAsJsonObject("break_costs"));
            var fallCosts = costs(o.getAsJsonObject("fall_costs"));
            int maxStack = integer(o.get("max_stack")), breakBudget = integer(o.get("break_budget")),
                    placeBudget = integer(o.get("place_budget")), placeSupply = integer(o.get("place_supply"));
            var cells = new HashSet<Long>();
            for (var e : o.getAsJsonArray("protected")) {
                var a = e.getAsJsonArray();
                if (a.size() != 3) throw new IllegalArgumentException("edit_protected");
                int x = integer(a.get(0)), y = integer(a.get(1)), z = integer(a.get(2));
                if (Math.abs((long)x) > 30_000_000 || Math.abs((long)z) > 30_000_000 || y < -2048 || y > 2047)
                    throw new IllegalArgumentException("edit_protected");
                cells.add(key(x, y, z));
            }
            // "costly": [[x,y,z,cost]]: breakable like any cell whose block is in break_costs, plus cost per cell.
            var extra = new HashMap<Long, Double>();
            if (o.has("costly")) for (var e : o.getAsJsonArray("costly")) {
                var a = e.getAsJsonArray();
                if (a.size() != 4) throw new IllegalArgumentException("edit_costly");
                int x = integer(a.get(0)), y = integer(a.get(1)), z = integer(a.get(2));
                double c = a.get(3).getAsDouble();
                if (Math.abs((long)x) > 30_000_000 || Math.abs((long)z) > 30_000_000 || y < -2048 || y > 2047
                        || !Double.isFinite(c) || c < 0 || extra.size() >= 4_000_000)
                    throw new IllegalArgumentException("edit_costly");
                extra.put(key(x, y, z), c);
            }
            var blocks = new HashSet<String>();
            for (var e : o.getAsJsonArray("protected_blocks")) blocks.add(e.getAsString());
            var materials = new ArrayList<String>();
            if (o.has("materials")) {
                for (var e : o.getAsJsonArray("materials")) {
                    String m = e.getAsString();
                    if (!m.startsWith("minecraft:") || materials.size() >= 64)
                        throw new IllegalArgumentException("edit_bounds");
                    materials.add(m);
                }
            }
            String material = o.has("material") ? o.get("material").getAsString()
                    : (materials.isEmpty() ? "minecraft:cobblestone" : materials.get(0));
            if (!o.has("materials")) materials.add(material);
            if (maxStack < 1 || maxStack > 3 || breakBudget < 0 || breakBudget > 64 || placeBudget < 0 || placeBudget > 64
                    || placeSupply < 0 || placeSupply > 4096 || cells.size() > 4_000_000 || blocks.size() > 4_000_000
                    || !material.startsWith("minecraft:"))
                throw new IllegalArgumentException("edit_bounds");
            return new EditPolicy(bool(o, "step"), breakCosts, fallCosts, bool(o, "allow_place"),
                    bool(o, "allow_bridge"), bool(o, "allow_pillar"), maxStack, breakBudget,
                    placeBudget, placeSupply, cells, blocks, material, List.copyOf(materials), extra,
                    o.has("scaffold_supply") ? Math.max(0, Math.min(4096, integer(o.get("scaffold_supply")))) : 0);
        }
        private static int integer(com.google.gson.JsonElement e) {
            if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) throw new IllegalArgumentException("edit_integer");
            double n = e.getAsDouble();
            if (!Double.isFinite(n) || n != Math.rint(n) || n < Integer.MIN_VALUE || n > Integer.MAX_VALUE)
                throw new IllegalArgumentException("edit_integer");
            return (int)n;
        }
        private static boolean bool(JsonObject o, String name) {
            var e = o.get(name);
            if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isBoolean()) throw new IllegalArgumentException("edit_boolean");
            return e.getAsBoolean();
        }
        private static Map<String, Double> costs(JsonObject o) {
            var out = new HashMap<String, Double>();
            for (var e : o.entrySet()) {
                double v = e.getValue().getAsDouble();
                if (!(v > 0 && v < 10_000) || out.size() >= 512) throw new IllegalArgumentException("edit_costs");
                out.put(e.getKey(), v);
            }
            return out;
        }
    }

    // MCB-measured edit overheads (terrain_steps constants of the same names).
    static final double EDIT_STEP = 15, PLACE = 20, STACK_PLACE = 20, BRIDGE_PLACE = 15, EDIT_CHAIN = 10;
    private static final int[][] CUT_SIDES = {{1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}};
    private static final Set<String> FALLING = Set.of("minecraft:sand", "minecraft:red_sand", "minecraft:gravel",
            "minecraft:suspicious_sand", "minecraft:suspicious_gravel");
    private static final Map<String, Object> AIR = Map.of("block", "minecraft:air", "fluid", "minecraft:empty",
            "collision_boxes", List.of());

    private EditPolicy edits;
    private Map<String, Object> placed;
    private final Map<Node, Edit> plans = new HashMap<>();  // the edit edge that reached each node
    private final Map<Node, Integer> depth = new HashMap<>();  // edit edges on the best path to each node
    private Map<Long, Map<String, Object>> overlay;  // the expanding node's planned edits, or null

    /** Turns on edit edges: dig/place steps (planner_edits) and, with extra moves, dig down, descend, pillar. */
    /** Accepted for old requests and ignored: sand or gravel beside a cut is no longer a refusal (owner ruling). */
    RoutePlanner withFallingNeighbours(boolean ignored) { return this; }

    RoutePlanner withEdits(EditPolicy edits) {
        this.edits = edits;
        String block = !edits.materials.isEmpty() ? edits.materials.get(0) : edits.material;
        this.placed = Map.of("block", block, "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1)));
        return this;
    }

    /** Planned changes: broken cells (and the falling stack over them) become air, placed ones cubes. */
    private Map<Long, Map<String, Object>> overlayOf(Edit edit) {
        if (edit == null) return null;
        var out = new HashMap<Long, Map<String, Object>>();
        for (var p : edit.breaks) {
            out.put(key(p), AIR);
            if (extra) for (var q : fallingStack(p)) out.put(key(q), AIR);
        }
        var scaffolds = new HashMap<Long, Integer>();
        for (var s : edit.scaffold) scaffolds.put(key(s[0], s[1], s[2]), s[3]);
        for (var p : edit.places) {
            var distance = scaffolds.get(key(p));
            out.put(key(p), distance == null ? placed : Map.of("block", "minecraft:scaffolding", "fluid", "minecraft:empty",
                    "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1)), "scaffold_distance", distance));
        }
        // A shaft dug by consecutive downward edits stays open: with only the last edit seen, the
        // cell above the head reverted to stone and no plan dug deeper than 2 (run 20261112T033141102600Z).
        // ponytail: plans keep one edit per node, so a later cheaper arrival at a source can rewrite the chain.
        for (var e = edit; e.kind.equals("downward") && (e = plans.get(e.source)) != null && e.kind.equals("downward"); )
            for (var p : e.breaks) out.putIfAbsent(key(p), AIR);
        return out;
    }

    private void addEdit(Edit edit, double base, Node from, List<Move> out) {
        if (edit == null) return;
        double cost = editCost(edit, base, from);
        if (cost < Double.POSITIVE_INFINITY) out.add(new Move(edit.target, cost, edit));
    }

    /** Edit cost, summed in a fixed order so equal-cost ties are deterministic. */
    private double editCost(Edit edit, double base, Node from) {
        if (!ticks) return (edit.target.y > edit.source.y ? 1.4 : 1)
                + (breakPenalty + toolWearPenalty) * edit.breaks.size() + placePenalty * edit.places.size()
                + edit.breaks.stream().mapToDouble(p -> extraCost(p[0], p[1], p[2])).sum();
        double breaking = 0;
        for (var p : edit.breaks) breaking += edits.breakCosts.get(block(p)) + extraCost(p[0], p[1], p[2]);
        if (extra && !edit.breaks.isEmpty()) {  // getMiningDurationTicks includeFalling
            double falling = 0;
            for (var p : edit.breaks) for (var q : fallingStack(p)) {
                Double f = edits.fallCosts.get(block(q));
                if (f == null) return Double.POSITIVE_INFINITY;  // no break time known for it
                falling += f;
            }
            breaking += falling;
        }
        int n = edit.places.size(), bridged = 0;
        for (var p : edit.places) {
            var below = cell(p[0], p[1] - 1, p[2]);
            if (below == null || boxes(below).isEmpty()) bridged++;
        }
        return base + EDIT_STEP + breaking + PLACE * n + STACK_PLACE * Math.max(0, n - 1) + BRIDGE_PLACE * bridged
                + EDIT_CHAIN * depth.getOrDefault(from, 0);
    }

    private Object block(int[] p) { return cell(p[0], p[1], p[2]).get("block"); }

    /** terrain_steps.falling_stack: gravity blocks stacked directly above p. */
    private List<int[]> fallingStack(int[] p) {
        var out = new ArrayList<int[]>();
        for (int y = p[1] + 1; ; y++) {
            var c = cell(p[0], y, p[2]);
            if (c == null || !FALLING.contains(c.get("block"))) return out;
            out.add(new int[]{p[0], y, p[2]});
        }
    }

    /** terrain_steps.cut_allowed: a known dry full cube with a tool, no fluid or unknown beside or above it. */
    private boolean cutAllowed(int[] p) {
        var c = cell(p[0], p[1], p[2]);
        if (!fullDry(c) || edits.protectedCells.contains(key(p)) || !edits.breakCosts.containsKey(c.get("block")))
            return false;
        for (var d : CUT_SIDES) {
            var n = cell(p[0] + d[0], p[1] + d[1], p[2] + d[2]);
            if (n == null || !dry(n)) return false;
            // Sand and gravel beside or above are no hazard (owner ruling): the stack above is priced and dug
            // (fallingStack); one beside stays put. Only a stack over the bot's own head is refused (overHead).
        }
        return true;
    }

    /** Owner rule: never cut a cell in the source column above the head with sand/gravel resting on it. */
    private boolean overHead(Node source, List<int[]> cuts) {
        for (var p : cuts)
            if (p[0] == source.x && p[2] == source.z && p[1] >= (int) source.y + 2 && !fallingStack(p).isEmpty()) return true;
        return false;
    }

    /** Blocks to break among points (empty ones skipped); null when one of them may not be broken. */
    private List<int[]> cuts(int[]... points) {
        var out = new ArrayList<int[]>();
        for (var p : points) {
            if (empty(cell(p[0], p[1], p[2]))) continue;
            if (!cutAllowed(p)) return null;
            out.add(p);
        }
        return out;
    }

    /** An edit step: support stack under the target, then headroom cuts. */
    private Edit planEdit(Node source, Node target) {
        int x = source.x, y = (int) source.y, z = source.z, tx = target.x, ty = (int) target.y, tz = target.z;
        List<int[]> places = List.of();
        if (!sturdy(cell(tx, ty - 1, tz))) {
            if (!edits.allowPlace) return null;
            var stack = new ArrayList<int[]>();
            stack.add(new int[]{tx, ty - 1, tz});
            while (!anchored(x, y, z, stack.get(stack.size() - 1))) {
                if (stack.size() >= edits.maxStack) return null;
                var p = stack.get(stack.size() - 1);
                stack.add(new int[]{p[0], p[1] - 1, p[2]});
            }
            Collections.reverse(stack);
            if (stack.size() > edits.placeBudget || stack.size() > edits.placeSupply) return null;
            for (var p : stack) {
                var c = cell(p[0], p[1], p[2]);
                if (edits.protectedCells.contains(key(p)) || !empty(c) || edits.protectedBlocks.contains(c.get("block")))
                    return null;
            }
            places = stack;
        }
        boolean recover = source.equals(start) && !stance(source);
        var cuts = recover
                ? (ty == y
                        ? cuts(new int[]{x, y + 1, z}, new int[]{x, y, z}, new int[]{tx, ty, tz}, new int[]{tx, ty + 1, tz})
                        : cuts(new int[]{x, y + 1, z}, new int[]{x, y, z}, new int[]{tx, ty, tz}, new int[]{tx, ty + 1, tz},
                                new int[]{x, y + 2, z}, new int[]{x, y + 3, z}, new int[]{tx, ty + 2, tz}))
                : (ty == y
                        ? cuts(new int[]{tx, ty, tz}, new int[]{tx, ty + 1, tz})
                        : cuts(new int[]{tx, ty, tz}, new int[]{tx, ty + 1, tz}, new int[]{x, y + 2, z}, new int[]{x, y + 3, z},
                                new int[]{tx, ty + 2, tz}));
        // 7 cuts: the ceiling StepMotor has always executed (the recover form proved
        // it). At 4, a +1 rise through solid rock listed 5 and was never offered, so
        // the body could dig down but not back up (iron run 20261114T201928310227Z:
        // 430 s of node-limit-incomplete plans below a 41-block shaft).
        if (cuts == null || cuts.size() > Math.min(7, edits.breakBudget) || (cuts.isEmpty() && places.isEmpty())
                || overHead(source, cuts))
            return null;
        return new Edit(source, target, cuts, places, "step", scaffoldCells(places));
    }

    /** Support below, or a permitted side anchor one below the source. */
    private boolean anchored(int x, int y, int z, int[] p) {
        if (sturdy(cell(p[0], p[1] - 1, p[2]))) return true;
        return edits.allowBridge && p[1] == y - 1 && Math.abs(p[0] - x) + Math.abs(p[2] - z) == 1
                && empty(cell(p[0], p[1] - 1, p[2])) && sturdy(cell(x, y - 1, z));
    }

    /** Cells to cut, plus the callers' "nothing to break" rule. */
    private List<int[]> budgeted(List<int[]> cuts) {
        return cuts == null || cuts.isEmpty() || cuts.size() > edits.breakBudget ? null : cuts;
    }

    /** Downward: break the floor, drop 1 onto a known safe cube. */
    private Edit downward(Node node) {
        int x = node.x, y = (int) node.y, z = node.z, land = y - 1;
        // Open dry cells under the floor are fallen through (a cave below, owner: a short fall is fine), to max_drop.
        while (land - 1 >= y - 1 - maxDrop && empty(cell(x, land - 1, z))) land--;
        var support = cell(x, land - 1, z);
        if (!fullDry(support) || HAZARDS.contains(support.get("block"))) return null;
        var cuts = budgeted(cuts(new int[]{x, y - 1, z}));
        return cuts == null ? null : new Edit(node, new Node(x, land, z), cuts, List.of(), "downward");
    }

    /** Descend: clear the target's feet, head and the cell above, onto a safe cube. */
    private Edit descend(Node node, int[] d) {
        int y = (int) node.y, tx = node.x + d[0], tz = node.z + d[1];
        var floor = cell(tx, y - 2, tz);
        if (!fullDry(floor) || HAZARDS.contains(floor.get("block"))) return null;
        // The fourth cut (target feet+3) is what makes the step reversible: the +1 walk
        // back needs the launch column clear to feet+3. 3-high stairs were walked down and
        // never back up (gather_dig.py 8b20de6); the planner must not offer a one-way hole.
        var cuts = budgeted(cuts(new int[]{tx, y - 1, tz}, new int[]{tx, y, tz}, new int[]{tx, y + 1, tz},
                new int[]{tx, y + 2, tz}));
        return cuts == null ? null : new Edit(node, new Node(tx, node.y - 1, tz), cuts, List.of(), "descend");
    }

    /** Pillar edge: place under the feet while jumping (no head cut). */
    private Edit pillar(Node node) {
        int x = node.x, y = (int) node.y, z = node.z;
        if (!edits.allowPillar || !edits.allowPlace || edits.placeBudget < 1 || edits.placeSupply < 1
                || edits.protectedCells.contains(key(x, y, z)) || !empty(cell(x, y + 2, z)) || !empty(cell(x, y + 3, z)))
            return null;
        var places = List.of(new int[]{x, y, z});
        return new Edit(node, new Node(x, node.y + 1, z), List.of(), places, "pillar", scaffoldCells(places));
    }

    private static double sq(double v) { return v * v; }
}

