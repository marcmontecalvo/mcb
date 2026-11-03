package com.vulpine.mcb.body;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.reflect.TypeToken;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.phys.Vec3;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.PriorityQueue;
import java.util.Set;
import java.util.function.Supplier;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * Body bridge: spawn a Carpet body, apply controls, sense, report body events.
 * Knows nothing about courses, episodes or scoring (those live in mcb-lab).
 * Handlers never access Minecraft world/player objects.
 */
public final class BodyRuntime implements AutoCloseable {
    private static final Gson GSON = new Gson();
    private static final int MAX_RETIRED_BODIES = 32;
    private static volatile BodyRuntime active;
    private final MinecraftServer server;
    private volatile Map<String,Object> policySnapshot = Map.of();
    private volatile Object servedTerrain; // last terrain batch /policy actually served
    private TerrainBatch loggedBatch; // this update's newly scanned sections, for the event log
    private boolean compactTerrain;
    private TerrainBatch terrainBatch = new TerrainBatch(List.of(),-100);
    private List<Map<String,Object>> visibleBlocks = List.of();
    private int sensorTick = -100;
    private AwarenessConfig awareness = AwarenessConfig.legacy();
    private AwarenessSensor awarenessSensor = new AwarenessSensor(awareness);
    private boolean stream;
    private boolean extendedObservation;
    private boolean courseLimits;
    private boolean legacyMoving;
    private final String launchId = System.getProperty("mclab.launch_id", "unmanaged");
    private final ArrayBlockingQueue<Inbound> inbox = new ArrayBlockingQueue<>(64);
    private final ArrayDeque<Event> events = new ArrayDeque<>();
    private final PriorityQueue<Scheduled> scheduled = new PriorityQueue<>(
            Comparator.comparingInt(Scheduled::tick));
    private final List<CarpetBodyAdapter> retired = new ArrayList<>();
    private final Set<String> actionIds = new HashSet<>();
    private final HttpServer http;
    private final ThreadPoolExecutor workers;
    private volatile Map<String, Object> snapshot = Map.of("state", "idle");
    private long eventSequence;
    private long lastRequestSequence;
    private long lastContactNanos = System.nanoTime();
    private String runId = "none";
    private String episodeId = "none";
    private String bodyId = "none";
    private CarpetBodyAdapter body;
    private String readyState = "idle";
    private int expiryTick = -1;
    private boolean traceEnabled;
    private int traceEnd;
    private long received;
    private boolean everReady;
    private RouteMotor routeMotor;
    private StepMotor stepMotor;
    private GapMotor gapMotor;
    private RouteMotor reflexMotor;
    private int reflexSequence;
    private boolean nativeMode;
    private BreakTask breakTask;
    private BodyEntity.Task actionTask;

    public record Controls(String action_id, int observation_tick, int intended_apply_tick,
                           int expires_tick, int duration, double forward, double strafe,
                           double yaw_delta, double pitch_delta, boolean jump, boolean sneak,
                           boolean sprint, boolean mine, boolean attack_pulse,
                           boolean use_hold, boolean use_pulse, int hotbar_slot) {}
    public record Inbound(JsonObject json, boolean admin, long receivedNanos) {}
    public record Scheduled(int tick, JsonObject request, Controls controls) {}
    public record Event(int schema_version, String launch_id, String run_id, String episode_id,
                        String body_id, long sequence, int tick, String phase, String type,
                        Map<String, Object> data) {}

    public BodyRuntime(MinecraftServer server) throws IOException {
        this.server = server;
        int port = Integer.parseInt(System.getProperty("mclab.bridge_port", "8765"));
        String token = System.getProperty("mclab.bridge_token", "");
        if (token.length() < 32) throw new IllegalStateException("Missing private bridge token");
        http = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 8);
        workers = new ThreadPoolExecutor(2, 4, 10, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(16), runnable -> {
                    var thread = new Thread(runnable, "mcb-body-http");
                    thread.setDaemon(true);
                    return thread;
                }, new ThreadPoolExecutor.AbortPolicy());
        http.setExecutor(workers);
        http.createContext("/", exchange -> handle(exchange, token));
        http.start();
        active = this;
    }

    private void handle(HttpExchange exchange, String token) throws IOException {
        try {
            String authorization = exchange.getRequestHeaders().getFirst("Authorization");
            if (authorization == null || !MessageDigest.isEqual(
                    authorization.getBytes(StandardCharsets.UTF_8),
                    ("Bearer " + token).getBytes(StandardCharsets.UTF_8))) {
                respond(exchange, 401, Map.of("error", "unauthorized"));
                return;
            }
            String path = exchange.getRequestURI().getPath();
            if (exchange.getRequestMethod().equals("GET")) {
                if (path.equals("/policy")) {
                    var served=policySnapshot;
                    respond(exchange,200,served);
                    servedTerrain=served.get("terrain_sections"); // delivered: see requeue()
                    return;
                }
                if (path.equals("/state")) { respond(exchange, 200, snapshot); return; }
                if (path.equals("/events")) {
                    String query = exchange.getRequestURI().getRawQuery();
                    long after = query == null ? 0 : Long.parseLong(query.replace("after=", ""));
                    respond(exchange, 200, eventBatch(after));
                    return;
                }
            }
            if (exchange.getRequestMethod().equals("POST")
                    && (path.equals("/admin") || path.equals("/controls"))) {
                var bytes = exchange.getRequestBody().readNBytes(16385);
                if (bytes.length > 16384) {
                    respond(exchange, 413, Map.of("error", "request_too_large"));
                    return;
                }
                var request = JsonParser.parseString(new String(bytes, StandardCharsets.UTF_8)).getAsJsonObject();
                if (!inbox.offer(new Inbound(request, path.equals("/admin"), System.nanoTime()))) {
                    respond(exchange, 429, Map.of("error", "queue_full"));
                    return;
                }
                respond(exchange, 202, Map.of("status", "queued", "launch_id", launchId));
                return;
            }
            respond(exchange, 404, Map.of("error", "not_found"));
        } catch (RuntimeException error) {
            respond(exchange, 400, Map.of("error", "invalid_request"));
        } finally { exchange.close(); }
    }

    private static void respond(HttpExchange exchange, int code, Object payload) throws IOException {
        byte[] data = GSON.toJson(payload).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(code, data.length);
        exchange.getResponseBody().write(data);
    }

    private synchronized Map<String, Object> eventBatch(long after) {
        long first = events.isEmpty() ? eventSequence + 1 : events.getFirst().sequence();
        return Map.of("schema_version", 1, "launch_id", launchId,
                "gap", after < first - 1, "latest_sequence", eventSequence,
                "events", events.stream().filter(event -> event.sequence() > after).toList());
    }

    private synchronized void emit(String type, String phase, Map<String, Object> data) {
        if (events.size() == 2048) events.removeFirst();
        events.addLast(new Event(1, launchId, runId, episodeId, bodyId, ++eventSequence,
                server.getTickCount(), phase, type, Map.copyOf(data)));
    }

    private static String text(JsonObject request, String key) {
        String value = request.get(key).getAsString();
        if (!value.matches("[A-Za-z0-9_.:-]{1,80}")) throw new IllegalArgumentException("invalid_" + key);
        return value;
    }

    private static int exactInt(com.google.gson.JsonElement value, String field) {
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber())
            throw new IllegalArgumentException(field);
        double number = value.getAsDouble();
        if (!Double.isFinite(number) || number != Math.rint(number) || number < Integer.MIN_VALUE || number > Integer.MAX_VALUE)
            throw new IllegalArgumentException(field);
        return (int)number;
    }

    private static long exactLong(com.google.gson.JsonElement value, String field) {
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber())
            throw new IllegalArgumentException(field);
        try {
            return value.getAsBigDecimal().longValueExact();
        } catch (ArithmeticException | NumberFormatException error) {
            throw new IllegalArgumentException(field, error);
        }
    }

    private static boolean strictBoolean(JsonObject value, String field) {
        var entry = value.get(field);
        if (entry == null || !entry.isJsonPrimitive() || !entry.getAsJsonPrimitive().isBoolean())
            throw new IllegalArgumentException(field);
        return entry.getAsBoolean();
    }

    public void preTick() {
        if (body != null && traceEnabled) emit("phase", "fabric_start", Map.of());
        for (var previous : retired) previous.pollReady();
        if (body != null && readyState.equals("spawn_timeout")) body.pollReady();
        int tick = server.getTickCount();
        if (traceEnabled && tick >= traceEnd) traceEnabled = false;
        if (body != null && System.nanoTime() - lastContactNanos > TimeUnit.SECONDS.toNanos(2)) {
            cancelRoute("watchdog");
            legacyMoving = false;
            body.release();
            if (expiryTick >= 0) emit("released", "fabric_start", Map.of("reason", "watchdog"));
            expiryTick = -1;
        }
        for (int budget = 0; budget < 8; budget++) {
            var request = inbox.poll();
            if (request == null) break;
            received++;
            try {
                if (System.nanoTime() - request.receivedNanos() > TimeUnit.SECONDS.toNanos(2)) {
                    throw new IllegalArgumentException("stale_queued_request");
                }
                admit(request);
            }
            catch (Exception error) {
                emit("rejected", "fabric_start", Map.of("request_sequence",
                        request.json().has("sequence") ? request.json().get("sequence").toString() : "missing",
                        "reason", error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage()));
            }
        }
        while (!scheduled.isEmpty() && scheduled.peek().tick() <= tick) {
            var action = scheduled.remove();
            try {
                validateIdentity(action.request());
                validateTiming(action.controls(), tick);
                if (!readyState.equals("ready")) throw new IllegalStateException("body_not_ready");
                body.controls(action.controls());
                legacyMoving = action.controls().forward() != 0 || action.controls().strafe() != 0;
                expiryTick = action.controls().expires_tick();
                emit("applied", "fabric_start", Map.of("action_id", action.controls().action_id(),
                        "actual_apply_tick", tick, "request_sequence", action.request().get("sequence").getAsLong()));
            } catch (Exception error) {
                emit("rejected_at_execution", "fabric_start", Map.of("action_id", action.controls().action_id(),
                        "reason", error.getMessage(), "request_sequence", action.request().get("sequence").getAsLong()));
            }
        }
        // A valid replacement at this same boundary renews a held button atomically.
        // Otherwise expiry releases before Carpet, including after a rejected replacement.
        if (body != null && expiryTick >= 0 && tick >= expiryTick) {
            body.release();
            legacyMoving = false;
            emit("released", "fabric_start", Map.of("reason", "expiry", "expires_tick", expiryTick));
            expiryTick = -1;
        }
        if (body != null && body.guardReach()) {
            emit("interaction_rejected", "fabric_start", Map.of("reason", "real_player_reach_guard"));
        }
        if (body != null && body.player() != null && readyState.equals("ready") && !courseLimits
                && extendedObservation && !nativeMode && legacyMoving && routePermissions.doors())
            BuildingMoves.openNear(body.player());
        if (!tickReflex()) { tickRoute(); tickStep(); tickGap(); }
        tickBreak();
        tickAction();
        tickPlans();
    }

    // Compiled planner core (ROADMAP item 8, stage E): searches run on the server thread in a
    // bounded slice per tick, reading the same awareness-limited cells as the motors.
    private static final long PLAN_SLICE_NANOS = 10_000_000L;
    // Threat reflex setting from the cost profile (follow_route "flee"); default one block.
    private RouteReflex.Flee flee = RouteReflex.Flee.DEFAULT;
    private static final int MAX_PLANS = 4;
    private RoutePermissions routePermissions = RoutePermissions.defaults();
    private final Map<String,RoutePlanner> plans = new LinkedHashMap<>();
    private final Map<String,TerrainPlanner> terrainPlans = new LinkedHashMap<>();
    private PlannerMap plannerMap = new PlannerMap();
    private PlanData planData = new PlanData();
    private final Map<String,Integer> planLimits = new HashMap<>();
    private final Map<String,Map<String,Object>> planResults = new LinkedHashMap<>();

    private void startPlan(JsonObject request) {
        String id = request.get("plan_id").getAsString();
        if (!id.matches("[A-Za-z0-9_.:-]{1,64}")) throw new IllegalArgumentException("plan_id");
        if (request.has("mode") && request.get("mode").getAsString().equals("terrain")) {
            startTerrainPlan(id, request);
            return;
        }
        if (request.has("mode")) throw new IllegalArgumentException("plan_mode");
        var s = request.getAsJsonArray("start"); var g = request.getAsJsonArray("goal");
        var start = node(s); var goal = node(g);
        double radius = request.get("radius").getAsDouble(), penalty = request.get("unknown_penalty").getAsDouble();
        int maxDrop = exactInt(request.get("max_drop"), "max_drop"), maxGap = exactInt(request.get("max_gap"), "max_gap");
        int limit = exactInt(request.get("node_limit"), "node_limit");
        if (!Double.isFinite(radius) || radius < 0 || maxDrop < 0 || maxDrop > 3 || maxGap < 0 || maxGap > 2
                || limit < 1 || limit > 100_000 || !(penalty == 0 || (penalty >= 1 && penalty <= 10)))
            throw new IllegalArgumentException("plan_bounds");
        var requestedEdits = request.has("edits") ? request.getAsJsonObject("edits") : null;
        var planPermissions = RoutePermissions.edits(requestedEdits);
        if (!request.has("map_generation")) throw new IllegalArgumentException("map_generation");
        var selected = plannerMap.reader(exactLong(request.get("map_generation"), "map_generation"));
        RouteGeometry.Reader reader = planPermissions.reader(selected);
        var planner = new RoutePlanner(reader, start, goal, radius, maxDrop,
                strictBoolean(request, "sprint"), penalty)
                .withMoves(strictBoolean(request, "swim"), strictBoolean(request, "extra"))
                .withPlannerSwim(!request.has("planner_swim") || strictBoolean(request, "planner_swim"))
                .withClimb(request.has("allow_climb") && strictBoolean(request, "allow_climb"))
                .withGaps(maxGap)
                .withHistory(nodes(request, "favored"), edges(request))
                .withAvoidStances(nodes(request, "avoid_stances"));
        if (request.has("tick_costs")) {
            double weight = request.get("heuristic_weight").getAsDouble();
            if (!Double.isFinite(weight) || weight < 0 || weight > 10) throw new IllegalArgumentException("heuristic_weight");
            planner.withPolicy(strictBoolean(request, "tick_costs"), strictBoolean(request, "baritone_search"),
                    strictBoolean(request, "baritone_diagonals"), strictBoolean(request, "allow_diagonals"),
                    strictBoolean(request, "allow_shapes"), weight);
            double breakPenalty = request.get("break_penalty").getAsDouble();
            double placePenalty = request.get("placement_penalty").getAsDouble();
            double toolWearPenalty = request.get("tool_wear_penalty").getAsDouble();
            if (!Double.isFinite(breakPenalty) || !Double.isFinite(placePenalty) || !Double.isFinite(toolWearPenalty)
                    || breakPenalty < 0 || placePenalty < 0 || toolWearPenalty < 0)
                throw new IllegalArgumentException("legacy_pricing");
            planner.withLegacyPricing(breakPenalty, placePenalty, toolWearPenalty);
        }
        if (request.has("interact_reach")) {
            double reach = request.get("interact_reach").getAsDouble();
            if (!Double.isFinite(reach) || reach <= 0) throw new IllegalArgumentException("interact_reach");
            planner.withInteraction(reach);
        }
        if (request.has("hazard_penalty")) {
            double hazard = request.get("hazard_penalty").getAsDouble();
            double coefficient = request.get("mob_coefficient").getAsDouble();
            double mobRadius = request.get("mob_radius").getAsDouble();
            if (!Double.isFinite(hazard) || hazard < 0 || !Double.isFinite(coefficient)
                    || coefficient < .1 || coefficient > 10 || !Double.isFinite(mobRadius) || mobRadius < 1 || mobRadius > 32)
                throw new IllegalArgumentException("survival_bounds");
            var threats = new ArrayList<double[]>();
            for (var e : request.getAsJsonArray("threats")) {
                var a = e.getAsJsonArray();
                if (a.size() != 3 || threats.size() >= 4096) throw new IllegalArgumentException("threats");
                double[] p = {a.get(0).getAsDouble(), a.get(1).getAsDouble(), a.get(2).getAsDouble()};
                for (double v : p) if (!Double.isFinite(v)) throw new IllegalArgumentException("threats");
                threats.add(p);
            }
            planner.withSurvival(hazard, coefficient, mobRadius, threats);
        }
        if (request.has("edits")) {
            var edits = request.getAsJsonObject("edits");
            planner.withEdits(RoutePlanner.EditPolicy.fromJson(edits));
            if (edits.has("falling_neighbours")) planner.withFallingNeighbours(strictBoolean(edits, "falling_neighbours"));
        }
        if (plans.size() + terrainPlans.size() >= MAX_PLANS) throw new IllegalStateException("planner_capacity");
        plans.put(id, planner); planLimits.put(id, limit);
        planResults.put(id, Map.of("plan_id", id, "state", "searching"));
        while (planResults.size() > MAX_PLANS) planResults.remove(planResults.keySet().iterator().next());
    }

    private void startTerrainPlan(String id, JsonObject request) {
        if (!request.has("map_generation")) throw new IllegalArgumentException("map_generation");
        var selected = plannerMap.reader(exactLong(request.get("map_generation"), "map_generation"));
        var start = node(request.get("start")); var goal = node(request.get("goal"));
        double radius = request.get("radius").getAsDouble();
        int maxDrop = exactInt(request.get("max_drop"), "max_drop"), horizon = exactInt(request.get("horizon"), "horizon");
        int width = exactInt(request.get("beam_width"), "beam_width"), limit = exactInt(request.get("node_limit"), "node_limit");
        if (!Double.isFinite(radius) || radius < 0 || maxDrop < 0 || maxDrop > 3 || limit < 1 || limit > 100_000)
            throw new IllegalArgumentException("terrain_bounds");
        var p = request.getAsJsonObject("policy");
        RouteGeometry.Reader reader = RoutePermissions.route(p).reader(selected);
        int breaks = exactInt(p.get("break_budget"), "break_budget"), places = exactInt(p.get("place_budget"), "place_budget");
        int reserve = exactInt(p.get("reserve_blocks"), "reserve_blocks"), stack = exactInt(p.get("max_support_stack"), "max_support_stack");
        int pillar = exactInt(p.get("max_pillar_height"), "max_pillar_height");
        double breakCost = p.get("break_penalty").getAsDouble(), placeCost = p.get("placement_penalty").getAsDouble();
        double wear = p.get("tool_wear_penalty").getAsDouble();
        if (breaks < 0 || breaks > 64 || places < 0 || places > 64 || reserve < 0 || reserve > 64
                || stack < 1 || stack > 3 || pillar < 1 || pillar > 3 || !Double.isFinite(breakCost)
                || !Double.isFinite(placeCost) || !Double.isFinite(wear) || breakCost < 0 || breakCost > 1000
                || placeCost < 0 || placeCost > 1000 || wear < 0 || wear > 1000)
            throw new IllegalArgumentException("terrain_policy_bounds");
        var materials = new ArrayList<String>();
        for (var e : p.getAsJsonArray("material_allowlist")) {
            String material = e.getAsString();
            if (!Set.of("minecraft:cobblestone", "minecraft:dirt").contains(material) || materials.size() >= 64)
                throw new IllegalArgumentException("terrain_material");
            materials.add(material);
        }
        var protectedBlocks = new HashSet<String>();
        for (var e : p.getAsJsonArray("protected_blocks")) {
            String block = e.getAsString();
            if (!block.startsWith("minecraft:") || protectedBlocks.size() >= 4096) throw new IllegalArgumentException("protected_blocks");
            protectedBlocks.add(block);
        }
        var policy = new TerrainPlanner.Policy(strictBoolean(p, "allow_break"), strictBoolean(p, "allow_place"),
                breaks, places, reserve, materials, protectedBlocks, nodes(p, "protected_positions"), breakCost, placeCost, wear,
                strictBoolean(p, "allow_bridge"), stack, strictBoolean(p, "allow_pillar"), pillar,
                strictBoolean(p, "planner_tick_costs"), strictBoolean(p, "baritone_moves"),
                strictBoolean(p, "allow_falling_neighbours"));
        var inventory = new ArrayList<TerrainPlanner.Stock>();
        for (var e : request.getAsJsonArray("inventory")) {
            var row = e.getAsJsonObject();
            int slot = exactInt(row.get("slot"), "terrain_inventory"), count = exactInt(row.get("count"), "terrain_inventory");
            String item = row.get("item").getAsString();
            if (slot < 0 || slot > 255 || count < 0 || count > 1_000_000 || inventory.size() >= 256 || !item.startsWith("minecraft:"))
                throw new IllegalArgumentException("terrain_inventory");
            inventory.add(new TerrainPlanner.Stock(slot, item, count));
        }
        var excluded = new HashSet<TerrainPlanner.Edge>();
        for (var edge : edges(request)) excluded.add(new TerrainPlanner.Edge(edge.get(0), edge.get(1)));
        if (plans.size() + terrainPlans.size() >= MAX_PLANS) throw new IllegalStateException("planner_capacity");
        terrainPlans.put(id, new TerrainPlanner(reader, start, goal, radius, policy, inventory,
                nodes(request, "protected"), excluded, maxDrop, horizon, width));
        planLimits.put(id, limit);
        planResults.put(id, Map.of("plan_id", id, "state", "searching"));
        while (planResults.size() > MAX_PLANS) planResults.remove(planResults.keySet().iterator().next());
    }

    private void plannerMapFrame(JsonObject request) {
        String kind = request.get("kind").getAsString();
        switch (kind) {
            case "begin" -> plannerMap.begin(exactLong(request.get("base"), "map_base"),
                    exactLong(request.get("generation"), "map_generation"));
            case "palette" -> {
                List<Map<String, Object>> facts = GSON.fromJson(request.get("facts"),
                        new TypeToken<List<Map<String, Object>>>() { }.getType());
                plannerMap.palette(exactInt(request.get("first"), "map_palette_first"), facts);
            }
            case "section", "remove" -> {
                var section = request.getAsJsonArray("section");
                if (section == null || section.size() != 3) throw new IllegalArgumentException("map_section");
                int x = exactInt(section.get(0), "map_section"), y = exactInt(section.get(1), "map_section"), z = exactInt(section.get(2), "map_section");
                if (kind.equals("remove")) plannerMap.remove(x, y, z);
                else plannerMap.section(x, y, z, request.get("states").getAsString());
            }
            case "commit" -> plannerMap.commit(exactLong(request.get("generation"), "map_generation"));
            case "abort" -> plannerMap.abort();
            case "status" -> { }
            case "reset" -> {
                if (!plans.isEmpty() || !terrainPlans.isEmpty()) throw new IllegalStateException("map_plans_active");
                plannerMap = new PlannerMap();
            }
            case "batch" -> {
                var frames = request.getAsJsonArray("frames");
                if (frames == null || frames.isEmpty() || frames.size() > 256) throw new IllegalArgumentException("map_batch");
                for (var item : frames) {
                    var frame = item.getAsJsonObject();
                    if (!Set.of("palette", "section", "remove").contains(frame.get("kind").getAsString()))
                        throw new IllegalArgumentException("map_batch_kind");
                    plannerMapFrame(frame);
                }
            }
            default -> throw new IllegalArgumentException("map_frame_kind");
        }
    }

    private static RoutePlanner.Node node(com.google.gson.JsonElement e) {
        var a = e.getAsJsonArray();
        if (a.size() != 3 || !a.get(1).isJsonPrimitive() || !a.get(1).getAsJsonPrimitive().isNumber())
            throw new IllegalArgumentException("plan_node");
        int x = exactInt(a.get(0), "plan_node"), z = exactInt(a.get(2), "plan_node");
        double y = a.get(1).getAsDouble();
        if (Math.abs((long)x) > 30_000_000 || Math.abs((long)z) > 30_000_000 || !Double.isFinite(y)
                || y < -2048 || y > 2048) throw new IllegalArgumentException("plan_node");
        return new RoutePlanner.Node(x, y, z);
    }
    private static Set<RoutePlanner.Node> nodes(JsonObject request, String key) {
        var out = new HashSet<RoutePlanner.Node>();
        if (request.has(key)) for (var e : request.getAsJsonArray(key)) out.add(node(e));
        if (out.size() > 4_000_000) throw new IllegalArgumentException("plan_history_bounds");
        return out;
    }
    private static Set<List<RoutePlanner.Node>> edges(JsonObject request) {
        var out = new HashSet<List<RoutePlanner.Node>>();
        if (request.has("excluded")) for (var e : request.getAsJsonArray("excluded")) {
            var pair = e.getAsJsonArray(); out.add(List.of(node(pair.get(0)), node(pair.get(1))));
        }
        if (out.size() > 4_000_000) throw new IllegalArgumentException("plan_history_bounds");
        return out;
    }

    private void tickPlans() {
        if ((plans.isEmpty() && terrainPlans.isEmpty()) || body == null || body.player() == null) return;
        long deadline = System.nanoTime() + PLAN_SLICE_NANOS;
        var done = new ArrayList<String>();
        for (var entry : plans.entrySet()) {
            if (System.nanoTime() > deadline) break;
            var result = entry.getValue().advance(planLimits.get(entry.getKey()), deadline);
            if (result.status().equals("searching")) continue;
            var route = result.route().stream().map(n -> List.<Object>of(n.x(), n.y(), n.z())).toList();
            var steps = result.steps().stream().map(e -> Map.<String,Object>of(
                    "source", List.<Object>of(e.source().x(), e.source().y(), e.source().z()),
                    "target", List.<Object>of(e.target().x(), e.target().y(), e.target().z()),
                    "breaks", e.breaks().stream().map(p -> List.of(p[0], p[1], p[2])).toList(),
                    "places", e.places().stream().map(p -> List.of(p[0], p[1], p[2])).toList(),
                    "kind", e.kind())).toList();
            var report = Map.<String,Object>of("plan_id", entry.getKey(), "state", "done", "status", result.status(),
                    "route", route, "steps", steps, "expanded", result.expanded(), "tick", server.getTickCount());
            planResults.put(entry.getKey(), report);
            emit("plan_finished", "fabric_end", Map.of("plan_id", entry.getKey(), "status", result.status(),
                    "expanded", result.expanded(), "length", route.size()));
            done.add(entry.getKey());
        }
        done.forEach(id -> { plans.remove(id); planLimits.remove(id); });
        done.clear();
        for (var entry : terrainPlans.entrySet()) {
            if (System.nanoTime() > deadline) break;
            var result = entry.getValue().advance(planLimits.get(entry.getKey()), deadline);
            if (result.status().equals("searching")) continue;
            var route = result.route().stream().map(n -> List.<Object>of(n.x(), n.y(), n.z())).toList();
            var steps = result.steps().stream().map(step -> {
                var value = new HashMap<String, Object>();
                value.put("source", List.of(step.source().x(), step.source().y(), step.source().z()));
                value.put("target", List.of(step.target().x(), step.target().y(), step.target().z()));
                value.put("breaks", step.breaks().stream().map(n -> List.of(n.x(), n.y(), n.z())).toList());
                value.put("places", step.places().stream().map(n -> List.of(n.x(), n.y(), n.z())).toList());
                value.put("kind", step.kind());
                if (step.exit() != null) value.put("exit", List.of(step.exit().x(), step.exit().y(), step.exit().z()));
                return value;
            }).toList();
            planResults.put(entry.getKey(), Map.of("plan_id", entry.getKey(), "state", "done", "status", result.status(),
                    "route", route, "steps", steps, "expanded", result.expanded(), "reason", result.reason(), "tick", server.getTickCount()));
            emit("plan_finished", "fabric_end", Map.of("plan_id", entry.getKey(), "status", result.status(),
                    "expanded", result.expanded(), "length", route.size()));
            done.add(entry.getKey());
        }
        done.forEach(id -> { terrainPlans.remove(id); planLimits.remove(id); });
    }

    private Map<String,Object> routeStatus() {
        return routeMotor == null ? Map.of("state", "idle") : routeMotor.status();
    }

    private void cancelRoute(String reason) {
        legacyMoving = false;
        if (actionTask != null) {
            var task = actionTask;
            actionTask = null;
            if (body != null && body.player() != null) task.stop(body.player());
            emit(task.name() + "_failed", "fabric_start", Map.of("request_sequence", task.requestSequence(), "reason", "cancelled:" + reason));
        }
        if (breakTask != null) {
            emit("break_block_failed", "fabric_start", Map.of("request_sequence", breakTask.requestSequence,
                    "reason", "cancelled:" + reason));
            breakTask = null;
        }
        if (!reason.equals("threat_reflex")) nativeMode=false;
        if (!reason.equals("threat_reflex") && reflexMotor != null && reflexMotor.active()) reflexMotor.stop(reason);
        if (gapMotor != null && gapMotor.active()) {
            gapMotor.stop(reason); emit("gap_stopped","fabric_start",gapMotor.status());
        }
        if (stepMotor != null && stepMotor.active()) {
            stepMotor.stop(reason);
            emit("step_stopped", "fabric_start", stepMotor.status());
        }
        if (routeMotor != null && routeMotor.active()) {
            routeMotor.stop(reason);
            emit("route_stopped", "fabric_start", routeMotor.status());
        }
    }

    private RouteGeometry.Reader localReader(ServerPlayer p) {
        return localReader(p, routePermissions);
    }
    private RouteGeometry.Reader localReader(ServerPlayer p, RoutePermissions permissions) {
        return (x,y,z) -> {
            var pos = new net.minecraft.core.BlockPos(x,y,z);
            if (!p.level().isInsideBuildHeight(pos) || !AwarenessSensor.inRange(awareness,p.blockPosition(),pos)) return null;
            return BuildingMoves.view(PolicySensor.cell(p,server.getTickCount(),pos,awareness.occluded()),
                    permissions.doors(), permissions.vines());
        };
    }

    private boolean tickReflex() {
        if (body==null || body.player()==null || !body.player().isAlive() || !readyState.equals("ready")) return false;
        if (!nativeMode && (reflexMotor==null || !reflexMotor.active())) return false;
        boolean operating=nativeMode;
        var p=body.player(); var velocity=p.getDeltaMovement();
        var pose=new RouteMotor.Pose(p.getX(),p.getY(),p.getZ(),velocity.x,velocity.z,p.getYRot(),p.onGround(),p.getFoodData().getFoodLevel());
        var geometry=new RouteGeometry(localReader(p));
        if ((reflexMotor==null || !reflexMotor.active()) && operating && p.onGround()) {
            var retreat=RouteReflex.retreat(pose,PolicySensor.entities(p,awareness),geometry,flee);
            if(!retreat.isEmpty()) {
                cancelRoute("threat_reflex"); scheduled.clear(); body.release(); expiryTick=-1;
                // Multi-block flights sprint (a lit creeper is escaped beyond 7 blocks).
                reflexMotor=new RouteMotor("retreat-"+(++reflexSequence),retreat,new RouteMotor.Policy(false,flee.steps()>1,true,0));
                emit("reflex_started","fabric_start",reflexMotor.status());
            }
        }
        if(reflexMotor==null || !reflexMotor.active()) return false;
        try {
            var input=reflexMotor.tick(pose,geometry);
            if(reflexMotor.active()) {
                int tick=server.getTickCount();
                body.controls(new Controls("native-reflex",tick,tick,tick+2,2,input.forward(),0,input.yaw(),0,false,false,false,false,false,false,false,0));
                expiryTick=tick+2;
            } else { body.release(); expiryTick=-1; emit("reflex_finished","fabric_start",reflexMotor.status()); }
        } catch(RuntimeException error) {
            reflexMotor.stop("reflex_error:"+error.getClass().getSimpleName()); body.release(); expiryTick=-1;
            emit("reflex_finished","fabric_start",reflexMotor.status());
        }
        return true;
    }

    private void tickStep() {
        if (stepMotor == null || !stepMotor.active()) return;
        if (body == null || !readyState.equals("ready") || body.player() == null || !body.player().isAlive()) {
            cancelRoute("body_not_ready"); if (body != null) body.release(); return;
        }
        var previous = stepMotor.status();
        try {
            if (routePermissions.doors()) BuildingMoves.openNear(body.player());
            var controls = stepMotor.tick(body.player(),server.getTickCount(),localReader(body.player()));
            if (stepMotor.active()) { body.controls(controls); expiryTick=server.getTickCount()+2; }
            else { body.release(); expiryTick=-1; }
            if (!previous.get("state").equals(stepMotor.status().get("state"))
                    || !previous.get("operation").equals(stepMotor.status().get("operation")))
                emit("step_progress","fabric_start",stepMotor.status());
        } catch (RuntimeException error) {
            cancelRoute("step_motor_error:"+error.getClass().getSimpleName()); body.release(); expiryTick=-1;
        }
    }

    private void tickGap() {
        if (gapMotor == null || !gapMotor.active()) return;
        if (body == null || !readyState.equals("ready") || body.player() == null || !body.player().isAlive()) {
            cancelRoute("body_not_ready"); if(body!=null) body.release(); return;
        }
        var previous=gapMotor.status();
        try {
            if (routePermissions.doors()) BuildingMoves.openNear(body.player());
            var controls=gapMotor.tick(body.player(),server.getTickCount(),localReader(body.player()));
            if(gapMotor.active()) { body.controls(controls); expiryTick=server.getTickCount()+2; }
            else { body.release(); expiryTick=-1; }
            if(!previous.get("state").equals(gapMotor.status().get("state"))
                    || !previous.get("phase").equals(gapMotor.status().get("phase")))
                emit("gap_progress","fabric_start",gapMotor.status());
        } catch(RuntimeException error) {
            cancelRoute("gap_motor_error:"+error.getClass().getSimpleName()); body.release(); expiryTick=-1;
        }
    }

    private void tickRoute() {
        if (routeMotor == null || !routeMotor.active()) return;
        if (body == null || !readyState.equals("ready") || body.player() == null || !body.player().isAlive()) {
            cancelRoute("body_not_ready");
            if (body != null) body.release();
            return;
        }
        var p = body.player();
        var prior = routeMotor.status();
        try {
            if (routePermissions.doors()) BuildingMoves.openNear(p);
            var geometry = new RouteGeometry(localReader(p));
            var velocity = p.getDeltaMovement();
            var input = routeMotor.tick(new RouteMotor.Pose(p.getX(),p.getY(),p.getZ(),velocity.x,velocity.z,
                    p.getYRot(),p.onGround(),p.getFoodData().getFoodLevel(),p.isInWater()),geometry);
            if (!routeMotor.active()) {
                body.release(); expiryTick = -1;
            } else {
                int tick = server.getTickCount();
                body.controls(new Controls("native-route",tick,tick,tick+2,2,input.forward(),0,input.yaw(),
                        RouteMotor.levelPitch(p.getXRot()),input.jump(),false,input.sprint(),false,false,false,false,0));
                expiryTick = tick + 2;
            }
            if (!prior.get("state").equals(routeMotor.status().get("state"))
                    || !prior.get("next_index").equals(routeMotor.status().get("next_index")))
                emit("route_progress", "fabric_start", routeMotor.status());
        } catch (RuntimeException error) {
            cancelRoute("motor_error:" + error.getClass().getSimpleName());
            body.release(); expiryTick = -1;
        }
    }

    private void validateIdentity(JsonObject request) {
        if (!text(request, "run_id").equals(runId) || !text(request, "episode_id").equals(episodeId)
                || !text(request, "body_id").equals(bodyId)) throw new IllegalArgumentException("wrong_identity");
    }

    private void admit(Inbound inbound) throws Exception {
        var request = inbound.json();
        if (request.get("schema_version").getAsInt() != 1) throw new IllegalArgumentException("schema_version");
        long sequence = request.get("sequence").getAsLong();
        String op = text(request, "op");
        if (inbound.admin() && op.equals("spawn")) {
            spawn(request, sequence, inbound.receivedNanos());
            return;
        }
        validateIdentity(request);
        if (sequence <= lastRequestSequence) throw new IllegalArgumentException("duplicate_or_out_of_order_sequence");
        lastRequestSequence = sequence;
        if (body == null) {
            // Without a body only liveness and an idempotent despawn are meaningful.
            if (inbound.admin() && op.equals("dump_knowledge")) { dumpKnowledge(request, sequence); return; }
            if (inbound.admin() && (op.equals("heartbeat") || op.equals("despawn"))) {
                emit("received", "fabric_start", Map.of("op", op, "request_sequence", sequence));
                if (op.equals("despawn")) emit("despawned", "fabric_start", Map.of("request_sequence", sequence));
                return;
            }
            throw new IllegalStateException("no_body");
        }
        lastContactNanos = inbound.receivedNanos();
        emit("received", "fabric_start", Map.of("op", op, "request_sequence", sequence,
                "queue_delay_ms", (System.nanoTime() - inbound.receivedNanos()) / 1_000_000.0));
        if (!inbound.admin()) {
            if (!op.equals("controls")) throw new IllegalArgumentException("controls_endpoint_only");
            if (nativeMode || (routeMotor != null && routeMotor.active()) || (stepMotor != null && stepMotor.active())
                    || (gapMotor != null && gapMotor.active()) || (reflexMotor != null && reflexMotor.active())
                    || breakTask != null || actionTask != null)
                throw new IllegalArgumentException("route_active");
            var controls = GSON.fromJson(request.getAsJsonObject("controls"), Controls.class);
            validateControls(controls);
            validateLimits(controls);
            if (!actionIds.add(controls.action_id())) throw new IllegalArgumentException("duplicate_action_id");
            if (actionIds.size() > 10000) throw new IllegalStateException("qualification_action_budget");
            if (scheduled.size() >= 16) throw new IllegalStateException("scheduled_queue_full");
            if (scheduled.stream().anyMatch(s -> s.tick() == controls.intended_apply_tick())) {
                throw new IllegalArgumentException("one_action_per_apply_tick");
            }
            // Admission and execution are both before Carpet at this boundary.
            // An action received before START(t+1) may apply at that same START.
            if (controls.intended_apply_tick() < server.getTickCount()
                    || controls.intended_apply_tick() > server.getTickCount() + 5) {
                throw new IllegalArgumentException("late_or_distant_action");
            }
            scheduled.add(new Scheduled(controls.intended_apply_tick(), request, controls));
            return;
        }
        switch (op) {
            case "heartbeat" -> { }
            case "route_policy" -> {
                if (!readyState.equals("ready") || courseLimits || !extendedObservation || nativeMode)
                    throw new IllegalStateException("route_policy_requires_development_body");
                routePermissions = RoutePermissions.route(request);
                emit("route_policy_accepted", "fabric_start", Map.of("request_sequence", sequence));
            }
            case "follow_route" -> {
                if (!readyState.equals("ready") || courseLimits || !extendedObservation)
                    throw new IllegalStateException("native_route_requires_development_body");
                var candidate = RouteMotor.parse(request);
                var permissions = RoutePermissions.route(request);
                var nextFlee = flee;
                if (request.has("flee")) {
                    var f = request.getAsJsonObject("flee");
                    nextFlee = new RouteReflex.Flee(f.get("trigger").getAsDouble(), f.get("creeper_trigger").getAsDouble(),
                            f.get("steps").getAsInt());
                }
                cancelRoute("replaced");
                scheduled.clear(); body.release(); expiryTick = -1;
                routePermissions = permissions; flee = nextFlee;
                routeMotor = candidate;
                nativeMode=true;
                emit("route_accepted", "fabric_start", Map.of("request_sequence",sequence,"route",routeStatus()));
            }
            case "follow_step" -> {
                if (!readyState.equals("ready") || courseLimits || !extendedObservation)
                    throw new IllegalStateException("native_step_requires_development_body");
                var candidate = new StepMotor(request);
                var permissions = RoutePermissions.route(request.getAsJsonObject("terrain_policy"));
                cancelRoute("replaced");
                scheduled.clear(); body.release(); expiryTick=-1;
                routePermissions = permissions;
                stepMotor=candidate;
                nativeMode=true;
                emit("step_accepted","fabric_start",Map.of("request_sequence",sequence,"step",stepMotor.status()));
            }
            case "follow_gap" -> {
                if(!readyState.equals("ready") || courseLimits || !extendedObservation)
                    throw new IllegalStateException("native_gap_requires_development_body");
                var candidate=new GapMotor(request);
                var permissions = RoutePermissions.route(request);
                cancelRoute("replaced"); scheduled.clear(); body.release(); expiryTick=-1;
                routePermissions = permissions;
                gapMotor=candidate;
                nativeMode=true;
                emit("gap_accepted","fabric_start",Map.of("request_sequence",sequence,"gap",gapMotor.status()));
            }
            case "plan_route" -> {
                if (!readyState.equals("ready") || courseLimits || !extendedObservation)
                    throw new IllegalStateException("native_plan_requires_development_body");
                JsonObject planRequest = request;
                if (request.has("plan_ref")) {
                    planRequest = planData.take(request.get("plan_ref").getAsString());
                    if (!request.get("plan_id").getAsString().equals(planRequest.get("plan_id").getAsString()))
                        throw new IllegalArgumentException("plan_data_id");
                }
                startPlan(planRequest);
                emit("plan_accepted", "fabric_start", Map.of("request_sequence", sequence, "plan_id",
                        request.get("plan_id").getAsString()));
            }
            case "planner_request" -> {
                if (!readyState.equals("ready") || courseLimits || !extendedObservation)
                    throw new IllegalStateException("planner_request_requires_development_body");
                String kind = request.get("kind").getAsString();
                if (kind.equals("begin")) planData.begin(request.get("plan_id").getAsString(),
                        exactInt(request.get("encoded_length"), "encoded_length"), exactInt(request.get("raw_length"), "raw_length"),
                        request.get("sha256").getAsString());
                else if (kind.equals("part")) planData.append(request.get("plan_id").getAsString(),
                        exactInt(request.get("offset"), "offset"), request.get("data").getAsString());
                else if (kind.equals("abort")) planData.abort();
                else throw new IllegalArgumentException("plan_data_kind");
                emit("planner_request_accepted", "fabric_start", Map.of("request_sequence", sequence));
            }
            case "planner_map" -> {
                if (!readyState.equals("ready") || courseLimits || !extendedObservation)
                    throw new IllegalStateException("planner_map_requires_development_body");
                plannerMapFrame(request);
                emit("planner_map_accepted", "fabric_start", Map.of("request_sequence", sequence,
                        "generation", plannerMap.generation()));
            }
            case "cancel_plan" -> {
                String id = request.get("plan_id").getAsString();
                plans.remove(id); terrainPlans.remove(id); planLimits.remove(id);
                planResults.remove(id);
                emit("plan_cancelled", "fabric_start", Map.of("request_sequence", sequence, "plan_id", id));
            }
            case "dump_knowledge" -> dumpKnowledge(request, sequence);
            case "craft" -> bodyAction("craft", sequence, player -> BodyActions.craft(server, player, request));
            case "furnace" -> bodyAction("furnace", sequence, player -> BodyActions.furnace(server, player, request));
            case "equip" -> bodyAction("equip", sequence, player -> BodyActions.equip(player, request));
            case "place_block" -> bodyAction("place_block", sequence, player -> { requireFree(); return BodyBuild.place(player, request); });
            case "use_block" -> bodyAction("use_block", sequence, player -> { requireFree(); return BodyBuild.use(player, request); });
            case "break_block" -> startBreak(request, sequence);
            case "attack_entity" -> startAction("attack_entity", sequence, player -> BodyEntity.AttackTask.start(player, request, sequence));
            case "eat" -> startAction("eat", sequence, player -> BodyEntity.EatTask.start(player, request, sequence));
            case "use_entity" -> bodyAction("use_entity", sequence, player -> { requireFree(); return BodyEntity.use(player, request); });
            case "container" -> bodyAction("container", sequence, player -> { requireFree(); return BodyContainer.run(player, request); });
            case "cancel_route" -> {
                cancelRoute("explicit");
                body.release(); expiryTick = -1;
                emit("route_cancelled", "fabric_start", Map.of("request_sequence",sequence,"route",routeStatus()));
            }
            case "release" -> {
                cancelRoute("explicit_release");
                scheduled.clear(); body.release(); expiryTick = -1;
                emit("released", "fabric_start", Map.of("reason", "explicit", "request_sequence", sequence));
            }
            case "despawn" -> {
                retireBody();
                readyState = "idle";
                emit("despawned", "fabric_start", Map.of("request_sequence", sequence));
            }
            case "sensor_check" -> {
                requireIdleReady();
                emit("course_sensor_checked","fabric_start",Map.of("request_sequence",sequence,
                        "verification",SensorVerification.check(body.player(),server.getTickCount())));
            }
            case "awareness_check" -> {
                requireIdleReady();
                emit("awareness_checked","fabric_start",Map.of("request_sequence",sequence,
                        "verification",SensorVerification.awarenessCheck(body.player(),server.getTickCount())));
            }
            case "command_proof" -> {
                if (!readyState.equals("ready")) throw new IllegalStateException("body_not_ready");
                int duration = request.get("duration").getAsInt();
                if (duration < 1 || duration > 200) throw new IllegalArgumentException("proof_duration_1_to_200");
                int result = body.commandProof(text(request, "command"));
                expiryTick = server.getTickCount() + duration;
                emit("command_acknowledged", "fabric_start", Map.of("result", result,
                        "command", text(request, "command"), "request_sequence", sequence));
            }
            case "trace" -> { traceEnabled = true; traceEnd = server.getTickCount() + 200; }
            default -> throw new IllegalArgumentException("unknown_admin_operation");
        }
    }

    /** Writes knowledge/&lt;pack_id&gt;/ (one server tick); needs no body. */
    private void dumpKnowledge(JsonObject request, long sequence) {
        try {
            var root = KnowledgeDump.resolveRoot(request.get("dir").getAsString());
            var result = new LinkedHashMap<String, Object>(new KnowledgeDump(server, body == null ? null : body.player()).run(root));
            result.put("request_sequence", sequence);
            emit("knowledge_dumped", "fabric_start", result);
        } catch (Exception error) {
            emit("knowledge_failed", "fabric_start", Map.of("request_sequence", sequence, "reason",
                    String.valueOf(error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage())));
        }
    }

    /** Legal actions of the body (craft, furnace, equip, place, use); emits &lt;name&gt;_done or &lt;name&gt;_failed. */
    private void bodyAction(String name, long sequence, java.util.function.Function<ServerPlayer, Map<String, Object>> work) {
        try {
            if (!readyState.equals("ready") || body.player() == null) throw new BodyActions.Fail("body_not_ready");
            var data = new LinkedHashMap<String, Object>(work.apply(body.player()));
            data.put("request_sequence", sequence);
            data.put("inventory", body.observe().get("inventory"));
            emit(name + "_done", "fabric_start", data);
        } catch (RuntimeException error) {
            emitFailed(name, sequence, error);
        }
    }

    private void emitFailed(String name, long sequence, RuntimeException error) {
        var data = new LinkedHashMap<String, Object>();
        data.put("request_sequence", sequence);
        data.put("reason", error instanceof BodyActions.Fail || error instanceof BodyBuild.PlacedWrong
                ? error.getMessage() : "invalid_request:" + error.getClass().getSimpleName());
        if (error instanceof BodyBuild.PlacedWrong wrong) data.put("placed", wrong.placed);
        if (error instanceof BodyBuild.Unplaceable why) data.put("diagnosis", why.diagnosis);
        if (body != null && body.player() != null) data.put("inventory", body.observe().get("inventory"));
        emit(name + "_failed", "fabric_start", data);
    }

    /** nativeMode alone (a finished route) does not block body actions; only a running motor does. */
    private boolean routeActive() {
        return (routeMotor != null && routeMotor.active()) || (stepMotor != null && stepMotor.active())
                || (gapMotor != null && gapMotor.active()) || (reflexMotor != null && reflexMotor.active());
    }

    private void requireFree() {
        if (routeActive()) throw new BodyActions.Fail("route_active");
        if (breakTask != null || actionTask != null) throw new BodyActions.Fail("busy");
    }

    private void startAction(String name, long sequence, java.util.function.Function<ServerPlayer, BodyEntity.Task> starter) {
        try {
            if (!readyState.equals("ready") || body.player() == null) throw new BodyActions.Fail("body_not_ready");
            requireFree();
            actionTask = starter.apply(body.player());
        } catch (RuntimeException error) {
            emitFailed(name, sequence, error);
        }
    }

    /** Ticks attack_entity / eat: the task acts on the player each tick and reports done or a reason. */
    private void tickAction() {
        if (actionTask == null) return;
        var task = actionTask;
        var player = body == null ? null : body.player();
        if (player == null || !player.isAlive() || !readyState.equals("ready")) {
            actionTask = null;
            emit(task.name() + "_failed", "fabric_start", Map.of("request_sequence", task.requestSequence(), "reason", "body_not_ready"));
            return;
        }
        String outcome = task.step(player);
        if (outcome == null) return;
        actionTask = null;
        if (!outcome.equals("done")) task.stop(player);
        var data = new LinkedHashMap<String, Object>();
        data.put("request_sequence", task.requestSequence());
        data.put("inventory", body.observe().get("inventory"));
        if (outcome.equals("done")) {
            data.putAll(task.result(player));
            emit(task.name() + "_done", "fabric_start", data);
        } else {
            data.put("reason", outcome);
            emit(task.name() + "_failed", "fabric_start", data);
        }
    }

    private void startBreak(JsonObject request, long sequence) {
        try {
            if (!readyState.equals("ready") || body.player() == null) throw new BodyActions.Fail("body_not_ready");
            requireFree();
            breakTask = BreakTask.start(body.player(), request, sequence);
        } catch (RuntimeException error) {
            emitFailed("break_block", sequence, error);
        }
    }

    private void tickBreak() {
        if (breakTask == null) return;
        var task = breakTask;
        var player = body == null ? null : body.player();
        if (player == null || !player.isAlive() || !readyState.equals("ready")) {
            breakTask = null;
            if (body != null) body.release();
            expiryTick = -1;
            emit("break_block_failed", "fabric_start", Map.of("request_sequence", task.requestSequence, "reason", "body_not_ready"));
            return;
        }
        String outcome = task.step(player);
        int tick = server.getTickCount();
        if (outcome == null) {
            body.controls(new Controls("native-break", tick, tick, tick + 2, 2, 0, 0, 0, 0, false, false, false,
                    true, false, false, false, player.getInventory().getSelectedSlot()));
            expiryTick = tick + 2;
            return;
        }
        breakTask = null;
        body.release();
        expiryTick = -1;
        var data = new LinkedHashMap<String, Object>();
        data.put("request_sequence", task.requestSequence);
        data.put("pos", task.posList());
        data.put("inventory", body.observe().get("inventory"));
        if (outcome.equals("done")) {
            data.put("broken", BodyBuild.render(task.original));
            data.put("ticks", task.ticks);
            emit("break_block_done", "fabric_start", data);
        } else {
            data.put("reason", outcome);
            emit("break_block_failed", "fabric_start", data);
        }
    }

    /** Sensor fixtures edit and restore nearby terrain; the controller must not be driving. */
    private void requireIdleReady() {
        if (!readyState.equals("ready") || !scheduled.isEmpty() || expiryTick >= 0)
            throw new IllegalStateException("sensor_check_requires_ready_released_body");
        scheduled.clear(); body.release(); expiryTick = -1;
    }

    /**
     * Spawn a fresh body with a new identity. replace=true retires the current body first
     * (the controller has usually already moved to the next episode's identity).
     */
    private void spawn(JsonObject request, long sequence, long receivedNanos) throws Exception {
        // A spawn opens a new identity, so its sequence may restart (a new controller process).
        if (sequence < 1) throw new IllegalArgumentException("sequence_must_be_positive");
        boolean replace = request.has("replace") && request.get("replace").getAsBoolean();
        if (body != null && !replace) throw new IllegalStateException("body_already_active");
        if (retired.size() + (body == null ? 0 : 1) >= MAX_RETIRED_BODIES)
            throw new IllegalStateException("restart_after_32_qualification_bodies");
        var nextAwareness = request.has("awareness")
                ? AwarenessConfig.parse(request.getAsJsonObject("awareness")) : AwarenessConfig.legacy();
        String terrainEncoding = request.has("terrain_encoding") ? request.get("terrain_encoding").getAsString() : "cells";
        if (!Set.of("cells","sections-v1").contains(terrainEncoding)
                || (terrainEncoding.equals("sections-v1") && nextAwareness.preset().equals("legacy"))
                || (nextAwareness.sections() && !terrainEncoding.equals("sections-v1")))
            throw new IllegalArgumentException("invalid_terrain_encoding");
        String limits = request.has("limits") ? text(request, "limits") : "none";
        if (!Set.of("none", "course").contains(limits)) throw new IllegalArgumentException("unknown_limits");
        var position = request.getAsJsonArray("position");
        var at = new Vec3(position.get(0).getAsDouble(), position.get(1).getAsDouble(), position.get(2).getAsDouble());
        if (!Double.isFinite(at.x) || !Double.isFinite(at.y) || !Double.isFinite(at.z))
            throw new IllegalArgumentException("invalid_position");
        float yaw = request.has("yaw") ? request.get("yaw").getAsFloat() : 0;
        float pitch = request.has("pitch") ? request.get("pitch").getAsFloat() : 0;
        // The controller may choose the name so it can tell the lab which player to score.
        String name = request.has("name") ? request.get("name").getAsString()
                : "Lab" + java.util.UUID.randomUUID().toString().replace("-", "").substring(0, 10);
        if (!name.matches("[A-Za-z0-9_]{3,16}")) throw new IllegalArgumentException("invalid_name");
        var candidate = new CarpetBodyAdapter(server, at, name, yaw, pitch);
        retireBody();
        plannerMap = new PlannerMap();
        planData = new PlanData();
        routePermissions = RoutePermissions.defaults(); flee = RouteReflex.Flee.DEFAULT;
        legacyMoving = false;
        plans.clear(); terrainPlans.clear(); planLimits.clear(); planResults.clear();
        runId = text(request, "run_id"); episodeId = text(request, "episode_id"); bodyId = text(request, "body_id");
        lastRequestSequence = sequence; lastContactNanos = receivedNanos; actionIds.clear();
        resetAwareness(nextAwareness);
        compactTerrain = terrainEncoding.equals("sections-v1");
        stream = request.has("stream") && request.get("stream").getAsBoolean();
        extendedObservation = request.has("extended_observation") && request.get("extended_observation").getAsBoolean();
        courseLimits = limits.equals("course");
        body = candidate;
        readyState = "preparing";
        everReady = false;
        long started = System.nanoTime();
        boolean viaCommand = request.has("via_command") && request.get("via_command").getAsBoolean();
        int acknowledged = body.spawn(viaCommand);
        emit("spawn_requested", "fabric_start", Map.of("acknowledged", acknowledged,
                "request_sequence", sequence, "name", body.name(),
                "spawn_call_ms", (System.nanoTime() - started) / 1_000_000.0));
    }

    private void retireBody() {
        cancelRoute("retired"); routeMotor = null; stepMotor = null; gapMotor = null; reflexMotor = null;
        scheduled.clear(); expiryTick = -1;
        if (body != null) { body.retire(); retired.add(body); body = null; }
    }

    static void validateControls(Controls c) {
        if (c == null || c.action_id() == null || !c.action_id().matches("[A-Za-z0-9_.:-]{1,80}")) {
            throw new IllegalArgumentException("action_id");
        }
        if (!Double.isFinite(c.forward()) || Math.abs(c.forward()) > 1
                || !Double.isFinite(c.strafe()) || Math.abs(c.strafe()) > 1
                || !Double.isFinite(c.yaw_delta()) || Math.abs(c.yaw_delta()) > 45
                || !Double.isFinite(c.pitch_delta()) || Math.abs(c.pitch_delta()) > 45
                || c.hotbar_slot() < 0 || c.hotbar_slot() > 8 || c.duration() < 1 || c.duration() > 5
                || c.expires_tick() != c.intended_apply_tick() + c.duration()
                || (c.mine() && c.attack_pulse()) || (c.use_hold() && c.use_pulse())
                || (c.sneak() && c.sprint())) throw new IllegalArgumentException("invalid_controls");
    }

    /** Qualified course bodies may only move, look, jump and mine with slot 0. */
    private void validateLimits(Controls controls) {
        if (courseLimits && (controls.attack_pulse() || controls.use_hold() || controls.use_pulse()
                || controls.hotbar_slot() != 0)) throw new IllegalArgumentException("course_forbidden_control");
    }

    private static void validateTiming(Controls c, int tick) {
        if (tick != c.intended_apply_tick() || tick >= c.expires_tick()
                || c.observation_tick() >= tick || tick - c.observation_tick() > 5) {
            throw new IllegalArgumentException("stale_or_mistimed_action");
        }
    }

    public void postTick() {
        if (body != null && !readyState.equals("spawn_timeout")) {
            String state = body.pollReady();
            if (state.equals("ready")) everReady = true;
            if (!state.equals(readyState)) {
                readyState = state;
                emit("body_state", "fabric_end", Map.of("state", state));
                if (!state.equals("ready")) { scheduled.clear(); body.release(); expiryTick = -1; }
            }
            if (!everReady && (state.equals("resolving_profile") || state.equals("settling")) && body.age() > 600) {
                body.retire(); readyState = "spawn_timeout";
                emit("infrastructure_failure", "fabric_end", Map.of("reason", "spawn_timeout"));
            }
        }
        updatePolicySnapshot();
        if (body != null && traceEnabled) emit("phase", "fabric_end_observation", Map.of());
        var status = new LinkedHashMap<String,Object>(Map.of("schema_version", 1, "launch_id", launchId,
                "run_id", runId, "episode_id", episodeId, "body_id", bodyId,
                "tick", server.getTickCount(), "state", readyState,
                "body", body == null ? Map.of() : body.observe(), "requests_received", received));
        status.put("awareness",awarenessState());
        status.put("route_follower",routeStatus());
        status.put("step_follower",stepMotor==null?Map.of("state","idle"):stepMotor.status());
        status.put("gap_follower",gapMotor==null?Map.of("state","idle"):gapMotor.status());
        status.put("reflex_follower",reflexMotor==null?Map.of("state","idle"):reflexMotor.status());
        snapshot=Map.copyOf(status);
    }

    private boolean streaming() {
        return stream && body != null && body.player() != null && readyState.equals("ready");
    }

    private void resetAwareness(AwarenessConfig next) {
        awareness=next; awarenessSensor=new AwarenessSensor(next);
        compactTerrain=false; terrainBatch=new TerrainBatch(List.of(),-100);
        visibleBlocks=List.of(); sensorTick=-100; policySnapshot=Map.of();
    }

    private Map<String,Object> awarenessState() {
        return Map.of("config",awareness.payload(),"revision",episodeId,
                "sensor_profile",awareness.profile(),"coverage",awarenessSensor.coverage(),
                "scope","body_radius_available_chunks_only","client_equivalent",false,
                "changes_require_new_episode",true);
    }

    private void updatePolicySnapshot() {
        var filtered=new LinkedHashMap<String,Object>();
        boolean streaming=streaming();
        if(body!=null && body.player()!=null) {
            var observed=body.observe();
            for(String key:List.of("position","velocity","yaw","pitch","on_ground","health","food",
                    "mode","pose","alive","sprinting","inventory","ray","block_reach","entity_reach","attack_strength"))
                filtered.put(key,observed.get(key));
            // Development bodies also report survival state. Qualified course bodies keep
            // their frozen observation contract.
            if(extendedObservation) {
                var player=body.player();
                filtered.put("survival",Map.of("eye_height",player.getEyeHeight(),
                        "air",player.getAirSupply(),"max_air",player.getMaxAirSupply(),
                        "in_water",player.isInWater(),"eye_in_water",player.isUnderWater(),
                        "on_climbable",player.onClimbable()));
            }
            if(streaming && server.getTickCount()-sensorTick>=awareness.interval()) {
                // A section batch nobody fetched is carried into the next one.
                if(compactTerrain && servedTerrain!=terrainBatch) awarenessSensor.carry(awarenessSensor.sections());
                visibleBlocks=awareness.preset().equals("legacy")?PolicySensor.visible(body.player(),server.getTickCount())
                        :awarenessSensor.sample(body.player(),server.getTickCount());
                sensorTick=server.getTickCount();
                if(compactTerrain) {
                    terrainBatch=new TerrainBatch(visibleBlocks,sensorTick,awarenessSensor.sections());
                    // The event log records each section once, when scanned; carried ones are
                    // already logged (repeating them made 3 MB events and stalled /events).
                    loggedBatch=new TerrainBatch(visibleBlocks,sensorTick,awarenessSensor.scanned());
                }
            }
        }
        var resources=Map.of("average_mspt",server.getAverageTickTimeNanos()/1_000_000.0,
                "smoothed_mspt",server.getCurrentSmoothedTickTime());
        // Build locally and publish once: the HTTP thread reads policySnapshot at any
        // moment, and a partly built one (no *_follower fields) aborted natural run
        // 20261029T135915643474Z R2 and R5 with "lost its accepted identity".
        Map<String,Object> observation=Map.ofEntries(Map.entry("schema_version",1),Map.entry("launch_id",launchId),
                Map.entry("run_id",runId),Map.entry("episode_id",episodeId),Map.entry("body_id",bodyId),
                Map.entry("tick",server.getTickCount()),Map.entry("state",readyState),
                Map.entry("body",Map.copyOf(filtered)),Map.entry("visible_blocks",visibleBlocks),
                Map.entry("sensor_profile",awareness.profile()), Map.entry("awareness",awarenessState()),
                Map.entry("visible_entities",streaming?PolicySensor.entities(body.player(),awareness):List.of()),
                Map.entry("resources",resources));
        if(compactTerrain) {
            var compact=new LinkedHashMap<String,Object>(observation);
            compact.remove("visible_blocks"); compact.put("terrain_sections",terrainBatch);
            observation=Map.copyOf(compact);
        }
        if (extendedObservation) {
            var routed = new LinkedHashMap<String,Object>(observation);
            routed.put("route_follower",routeStatus());
            routed.put("step_follower",stepMotor==null?Map.of("state","idle"):stepMotor.status());
            routed.put("gap_follower",gapMotor==null?Map.of("state","idle"):gapMotor.status());
            routed.put("reflex_follower",reflexMotor==null?Map.of("state","idle"):reflexMotor.status());
            routed.put("route_planner",List.copyOf(planResults.values()));
            observation=Map.copyOf(routed);
        }
        policySnapshot=observation;
        if(streaming) {
            var sample=new LinkedHashMap<String,Object>(observation);
            // Every simulation tick records body state; terrain follows the declared cadence.
            if(sensorTick!=server.getTickCount()) sample.remove("visible_blocks");
            if(sensorTick!=server.getTickCount()) sample.remove("terrain_sections");
            else if(compactTerrain) sample.put("terrain_sections",loggedBatch);
            sample.put("map_update",sensorTick==server.getTickCount());
            emit("policy_sample","fabric_end_observation",Map.copyOf(sample));
        }
    }

    public static void trace(ServerPlayer player, String phase) {
        var bridge = active;
        if (bridge != null && bridge.traceEnabled && bridge.body != null && bridge.body.owns(player)) {
            bridge.emit("phase", phase, Map.of("position", List.of(player.getX(), player.getY(), player.getZ())));
        }
    }

    /** Interaction packets and results for this mod's own bodies only. */
    public static void interaction(ServerPlayer player, String type, Supplier<Map<String, Object>> data) {
        var bridge = active;
        if (bridge == null || !bridge.server.isSameThread()) return;
        if (bridge.body != null && bridge.body.owns(player)) bridge.emit(type, "server_interaction", data.get());
    }

    @Override public void close() {
        active = null;
        if (body != null) body.retire();
        retired.forEach(CarpetBodyAdapter::retire);
        http.stop(0);
        workers.shutdownNow();
    }
}
