package com.vulpine.mclab;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
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
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.PriorityQueue;
import java.util.Set;
import java.util.function.Supplier;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** Qualification bridge. Handlers never access Minecraft world/player objects. */
public final class BridgeRuntime implements AutoCloseable {
    private static final Gson GSON = new Gson();
    private static volatile BridgeRuntime active;
    private final MinecraftServer server;
    private final Vec3 fixture;
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
    private String referenceName;
    private int referenceEnd;
    private boolean everReady;

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

    public BridgeRuntime(MinecraftServer server, Vec3 fixture) throws IOException {
        this.server = server;
        this.fixture = fixture;
        int port = Integer.parseInt(System.getProperty("mclab.bridge_port", "8765"));
        String token = System.getProperty("mclab.bridge_token", "");
        if (token.length() < 32) throw new IllegalStateException("Missing private bridge token");
        http = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 8);
        workers = new ThreadPoolExecutor(2, 4, 10, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(16), runnable -> {
                    var thread = new Thread(runnable, "mclab-http");
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
                if (path.equals("/state")) {
                    respond(exchange, 200, snapshot);
                    return;
                }
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

    public void preTick() {
        if (body != null && traceEnabled) emit("phase", "fabric_start", Map.of());
        for (var previous : retired) previous.pollReady();
        if (body != null && readyState.equals("spawn_timeout")) body.pollReady();
        int tick = server.getTickCount();
        if (traceEnabled && tick >= traceEnd) traceEnabled = false;
        if (body != null && System.nanoTime() - lastContactNanos > TimeUnit.SECONDS.toNanos(2)) {
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
            emit("released", "fabric_start", Map.of("reason", "expiry", "expires_tick", expiryTick));
            expiryTick = -1;
        }
        if (body != null && body.guardReach()) {
            emit("interaction_rejected", "fabric_start", Map.of("reason", "real_player_reach_guard"));
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
        if (inbound.admin() && op.equals("reference_start")) {
            if (body != null || referenceName != null) throw new IllegalStateException("lab_busy");
            String name = text(request, "player");
            var player = server.getPlayerList().getPlayerByName(name);
            if (player == null || !CarpetBodyAdapter.isHuman(player)) {
                throw new IllegalArgumentException("human_not_present");
            }
            int seconds = request.get("seconds").getAsInt();
            if (seconds < 5 || seconds > 120 || sequence < 1) throw new IllegalArgumentException("reference_bounds");
            String mechanic = request.has("reference_mechanic") ? text(request, "reference_mechanic") : "none";
            if (!Set.of("none", "collision", "step", "ledge", "fall").contains(mechanic)) {
                throw new IllegalArgumentException("unknown_reference_mechanic");
            }
            boolean interactions = request.has("prepare_interactions") && request.get("prepare_interactions").getAsBoolean();
            boolean combat = request.has("prepare_combat") && request.get("prepare_combat").getAsBoolean();
            if ((!mechanic.equals("none") && (interactions || combat)) || (combat && !interactions)) {
                throw new IllegalArgumentException("conflicting_reference_preparation");
            }
            runId = text(request, "run_id"); episodeId = text(request, "episode_id");
            bodyId = text(request, "body_id"); lastRequestSequence = sequence;
            Map<String, Object> setup = Map.of();
            if (!mechanic.equals("none")) {
                setup = HumanReferenceFixture.prepare(server, player, fixture, mechanic);
            } else if (interactions) {
                CarpetBodyAdapter.prepareHumanInteractions(server, player, fixture, combat);
            }
            referenceName = name; referenceEnd = server.getTickCount() + seconds * 20;
            readyState = "reference_recording";
            emit("reference_started", "fabric_start", Map.of("player", name,
                    "request_sequence", sequence, "end_tick", referenceEnd, "setup", setup));
            return;
        }
        if (inbound.admin() && op.equals("spawn")) {
            if (referenceName != null) throw new IllegalStateException("reference_in_progress");
            if (body != null) throw new IllegalStateException("body_already_active");
            if (retired.size() >= 32) throw new IllegalStateException("restart_after_32_qualification_bodies");
            runId = text(request, "run_id");
            episodeId = text(request, "episode_id");
            bodyId = text(request, "body_id");
            if (sequence < 1) throw new IllegalArgumentException("sequence_must_be_positive");
            lastRequestSequence = sequence;
            actionIds.clear();
            body = new CarpetBodyAdapter(server, fixture,
                    "Lab" + java.util.UUID.randomUUID().toString().replace("-", "").substring(0, 10));
            readyState = "preparing";
            everReady = false;
            long started = System.nanoTime();
            int acknowledged = body.spawn(request.get("via_command").getAsBoolean());
            emit("spawn_requested", "fabric_start", Map.of("acknowledged", acknowledged,
                    "request_sequence", sequence, "name", body.name(),
                    "spawn_call_ms", (System.nanoTime() - started) / 1_000_000.0));
            lastContactNanos = inbound.receivedNanos();
            return;
        }
        if (body == null) {
            validateIdentity(request);
            if (sequence <= lastRequestSequence) throw new IllegalArgumentException("duplicate_or_out_of_order_sequence");
            lastRequestSequence = sequence;
            if (inbound.admin() && (op.equals("heartbeat") || op.equals("reference_stop"))) {
                if (op.equals("reference_stop")) {
                    referenceName = null; readyState = "idle";
                    CarpetBodyAdapter.cleanupHumanInteractions(server, fixture);
                }
                emit("received", "fabric_start", Map.of("op", op, "request_sequence", sequence));
                return;
            }
            throw new IllegalStateException("no_body");
        }
        validateIdentity(request);
        if (sequence <= lastRequestSequence) throw new IllegalArgumentException("duplicate_or_out_of_order_sequence");
        lastRequestSequence = sequence;
        lastContactNanos = inbound.receivedNanos();
        emit("received", "fabric_start", Map.of("op", op, "request_sequence", sequence,
                "queue_delay_ms", (System.nanoTime() - inbound.receivedNanos()) / 1_000_000.0));
        if (!inbound.admin()) {
            if (!op.equals("controls")) throw new IllegalArgumentException("controls_endpoint_only");
            var controls = GSON.fromJson(request.getAsJsonObject("controls"), Controls.class);
            validateControls(controls);
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
            case "release" -> {
                scheduled.clear(); body.release(); expiryTick = -1;
                emit("released", "fabric_start", Map.of("reason", "explicit"));
            }
            case "despawn" -> {
                scheduled.clear(); body.restoreFixture(); body.retire(); retired.add(body); body = null;
                expiryTick = -1; readyState = "idle";
                emit("despawned", "fabric_start", Map.of());
            }
            case "fixture" -> {
                if (!readyState.equals("ready")) throw new IllegalStateException("body_not_ready");
                scheduled.clear(); expiryTick = -1;
                int dirt = request.get("dirt").getAsInt();
                if (dirt != 0 && dirt != 16) throw new IllegalArgumentException("fixture_dirt_0_or_16");
                body.setupFixture(request.get("log").getAsBoolean(), dirt,
                        request.has("mechanic") ? text(request, "mechanic") : "flat");
                emit("fixture_setup", "fabric_start", Map.of("dirt", dirt));
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
            case "death_proof" -> { body.deathProof(); }
            case "knockback_proof" -> { body.knockbackProof(); }
            default -> throw new IllegalArgumentException("unknown_admin_operation");
        }
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

    private static void validateTiming(Controls c, int tick) {
        if (tick != c.intended_apply_tick() || tick >= c.expires_tick()
                || c.observation_tick() >= tick || tick - c.observation_tick() > 5) {
            throw new IllegalArgumentException("stale_or_mistimed_action");
        }
    }

    public void postTick() {
        if (referenceName != null) {
            var player = server.getPlayerList().getPlayerByName(referenceName);
            if (player == null) {
                emit("reference_invalid", "fabric_end", Map.of("reason", "human_disconnected"));
                referenceName = null; readyState = "reference_invalid";
            } else if (server.getTickCount() >= referenceEnd) {
                emit("reference_complete", "fabric_end", Map.of());
                referenceName = null; readyState = "reference_complete";
                CarpetBodyAdapter.cleanupHumanInteractions(server, fixture);
            } else emit("reference_sample", "fabric_end", CarpetBodyAdapter.humanReference(player));
        }
        if (body != null && !readyState.equals("spawn_timeout")) {
            String state = body.pollReady();
            if (state.equals("ready")) everReady = true;
            if (!state.equals(readyState)) {
                readyState = state;
                emit("body_state", "fabric_end", Map.of("state", state));
            }
            if (!everReady && (state.equals("resolving_profile") || state.equals("settling")) && body.age() > 600) {
                body.retire(); readyState = "spawn_timeout";
                emit("infrastructure_failure", "fabric_end", Map.of("reason", "spawn_timeout"));
            }
        }
        if (body != null && traceEnabled) emit("phase", "fabric_end_observation", Map.of());
        snapshot = Map.of("schema_version", 1, "launch_id", launchId,
                "run_id", runId, "episode_id", episodeId, "body_id", bodyId,
                "tick", server.getTickCount(), "state", readyState,
                "body", body == null ? Map.of() : body.observe(), "requests_received", received);
    }

    public static void trace(ServerPlayer player, String phase) {
        var bridge = active;
        if (bridge != null && bridge.traceEnabled && bridge.body != null && bridge.body.owns(player)) {
            bridge.emit("phase", phase, Map.of("position", List.of(player.getX(), player.getY(), player.getZ())));
        }
    }

    public static boolean onServerThread() {
        var bridge = active;
        return bridge != null && bridge.server.isSameThread();
    }

    public static void interaction(ServerPlayer player, String type, Supplier<Map<String, Object>> data) {
        var bridge = active;
        if (bridge == null || !bridge.server.isSameThread()) return;
        boolean human = bridge.referenceName != null && bridge.referenceName.equals(player.getName().getString());
        if (human || (bridge.body != null && bridge.body.owns(player))) {
            bridge.emit(type, "server_interaction", data.get());
        }
    }

    @Override public void close() {
        active = null;
        if (body != null) body.retire();
        retired.forEach(CarpetBodyAdapter::retire);
        http.stop(0);
        workers.shutdownNow();
    }
}
