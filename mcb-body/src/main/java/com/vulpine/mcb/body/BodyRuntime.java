package com.vulpine.mcb.body;

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
    private boolean compactTerrain;
    private TerrainBatch terrainBatch = new TerrainBatch(List.of(),-100);
    private List<Map<String,Object>> visibleBlocks = List.of();
    private int sensorTick = -100;
    private AwarenessConfig awareness = AwarenessConfig.legacy();
    private AwarenessSensor awarenessSensor = new AwarenessSensor(awareness);
    private boolean stream;
    private boolean extendedObservation;
    private boolean courseLimits;
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
                if (path.equals("/policy")) { respond(exchange,200,policySnapshot); return; }
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

    public void preTick() {
        if (body != null && traceEnabled) emit("phase", "fabric_start", Map.of());
        for (var previous : retired) previous.pollReady();
        if (body != null && readyState.equals("spawn_timeout")) body.pollReady();
        int tick = server.getTickCount();
        if (traceEnabled && tick >= traceEnd) traceEnabled = false;
        if (body != null && System.nanoTime() - lastContactNanos > TimeUnit.SECONDS.toNanos(2)) {
            cancelRoute("watchdog");
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
        if (!tickReflex()) { tickRoute(); tickStep(); tickGap(); }
    }

    private Map<String,Object> routeStatus() {
        return routeMotor == null ? Map.of("state", "idle") : routeMotor.status();
    }

    private void cancelRoute(String reason) {
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
        return (x,y,z) -> {
            var pos = new net.minecraft.core.BlockPos(x,y,z);
            if (!p.level().isInsideBuildHeight(pos) || !AwarenessSensor.inRange(awareness,p.blockPosition(),pos)) return null;
            return PolicySensor.cell(p,server.getTickCount(),pos,awareness.occluded());
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
            var retreat=RouteReflex.retreat(pose,PolicySensor.entities(p,awareness),geometry);
            if(!retreat.isEmpty()) {
                cancelRoute("threat_reflex"); scheduled.clear(); body.release(); expiryTick=-1;
                reflexMotor=new RouteMotor("retreat-"+(++reflexSequence),retreat,new RouteMotor.Policy(false,false,true,0));
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
            var geometry = new RouteGeometry(localReader(p));
            var velocity = p.getDeltaMovement();
            var input = routeMotor.tick(new RouteMotor.Pose(p.getX(),p.getY(),p.getZ(),velocity.x,velocity.z,
                    p.getYRot(),p.onGround(),p.getFoodData().getFoodLevel()),geometry);
            if (!routeMotor.active()) {
                body.release(); expiryTick = -1;
            } else {
                int tick = server.getTickCount();
                body.controls(new Controls("native-route",tick,tick,tick+2,2,input.forward(),0,input.yaw(),0,
                        input.jump(),false,input.sprint(),false,false,false,false,0));
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
                    || (gapMotor != null && gapMotor.active()) || (reflexMotor != null && reflexMotor.active()))
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
            case "follow_route" -> {
                if (!readyState.equals("ready") || courseLimits || !extendedObservation)
                    throw new IllegalStateException("native_route_requires_development_body");
                var candidate = RouteMotor.parse(request);
                cancelRoute("replaced");
                scheduled.clear(); body.release(); expiryTick = -1;
                routeMotor = candidate;
                nativeMode=true;
                emit("route_accepted", "fabric_start", Map.of("request_sequence",sequence,"route",routeStatus()));
            }
            case "follow_step" -> {
                if (!readyState.equals("ready") || courseLimits || !extendedObservation)
                    throw new IllegalStateException("native_step_requires_development_body");
                var candidate = new StepMotor(request);
                cancelRoute("replaced");
                scheduled.clear(); body.release(); expiryTick=-1;
                stepMotor=candidate;
                nativeMode=true;
                emit("step_accepted","fabric_start",Map.of("request_sequence",sequence,"step",stepMotor.status()));
            }
            case "follow_gap" -> {
                if(!readyState.equals("ready") || courseLimits || !extendedObservation)
                    throw new IllegalStateException("native_gap_requires_development_body");
                var candidate=new GapMotor(request);
                cancelRoute("replaced"); scheduled.clear(); body.release(); expiryTick=-1;
                gapMotor=candidate;
                nativeMode=true;
                emit("gap_accepted","fabric_start",Map.of("request_sequence",sequence,"gap",gapMotor.status()));
            }
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
                || (terrainEncoding.equals("sections-v1") && nextAwareness.preset().equals("legacy")))
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
                        "in_water",player.isInWater(),"on_climbable",player.onClimbable()));
            }
            if(streaming && server.getTickCount()-sensorTick>=awareness.interval()) {
                visibleBlocks=awareness.preset().equals("legacy")?PolicySensor.visible(body.player(),server.getTickCount())
                        :awarenessSensor.sample(body.player(),server.getTickCount());
                sensorTick=server.getTickCount();
                if(compactTerrain) terrainBatch=new TerrainBatch(visibleBlocks,sensorTick);
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
            observation=Map.copyOf(routed);
        }
        policySnapshot=observation;
        if(streaming) {
            var sample=new LinkedHashMap<String,Object>(observation);
            // Every simulation tick records body state; terrain follows the declared cadence.
            if(sensorTick!=server.getTickCount()) sample.remove("visible_blocks");
            if(sensorTick!=server.getTickCount()) sample.remove("terrain_sections");
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
