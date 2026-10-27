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
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.PriorityQueue;
import java.util.Set;
import java.util.function.BiConsumer;
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
    private final JsonObject initialCourseSpec;
    private JsonObject courseSpec;
    private CourseReset courseReset;
    private CourseEpisode episode;
    private HumanCourse human;
    private NaturalRun natural;
    private long pendingBeginSequence;
    private volatile Map<String,Object> policySnapshot = Map.of();
    private boolean compactTerrain;
    private TerrainBatch terrainBatch = new TerrainBatch(List.of(),-100);
    private List<Map<String,Object>> visibleBlocks = List.of();
    private int sensorTick = -100;
    private AwarenessConfig awareness = AwarenessConfig.legacy();
    private AwarenessSensor awarenessSensor = new AwarenessSensor(awareness);
    private boolean resetting;
    private boolean resetSecondVerification;
    private int resetGeneration;
    private int resetWarmUntil = -1;
    private long resetRequestSequence;
    private String resetFirstTerrain = "";
    private String resetFirstBody = "";
    private String resetDigest = "";
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

    public BridgeRuntime(MinecraftServer server, Vec3 fixture, JsonObject courseSpec) throws IOException {
        this.server = server;
        this.fixture = fixture;
        this.courseSpec = courseSpec.deepCopy();
        this.initialCourseSpec = courseSpec.deepCopy();
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
                if (path.equals("/policy")) { respond(exchange,200,policySnapshot); return; }
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
            if (episode != null && episode.running()) episode.finish("failed","controller_watchdog");
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
                if (!readyState.equals("ready") && !readyState.equals("running")) throw new IllegalStateException("body_not_ready");
                validateCourseControls(action.controls());
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
        if (inbound.admin() && op.equals("natural_probe")) {
            if (body != null || human != null || courseReset != null || (natural != null && !natural.terminal()))
                throw new IllegalStateException("lab_busy");
            var cells = NaturalRun.probe(server, request.get("center_x").getAsInt(), request.get("center_z").getAsInt(),
                    request.get("radius").getAsInt(), request.get("stride").getAsInt());
            emit("natural_probe", "fabric_start", Map.of("request_sequence", sequence, "cells", cells));
            return;
        }
        if (inbound.admin() && op.equals("natural_start")) {
            if (resetting || referenceName != null || body != null || human != null || courseReset != null
                    || (episode != null && episode.running()) || (natural != null && !natural.terminal()))
                throw new IllegalStateException("lab_busy");
            var nextAwareness=request.has("awareness")?AwarenessConfig.parse(request.getAsJsonObject("awareness")):AwarenessConfig.legacy();
            String terrainEncoding="cells";
            if(request.has("terrain_encoding")) {
                if(!request.getAsJsonPrimitive("terrain_encoding").isString())
                    throw new IllegalArgumentException("invalid_terrain_encoding");
                terrainEncoding=request.get("terrain_encoding").getAsString();
                if(!Set.of("cells","sections-v1").contains(terrainEncoding)
                        || (terrainEncoding.equals("sections-v1") && nextAwareness.preset().equals("legacy")))
                    throw new IllegalArgumentException("invalid_terrain_encoding");
            }
            if(!text(request,"subject").equals("carpet_body") && !nextAwareness.equals(AwarenessConfig.legacy()))
                throw new IllegalArgumentException("awareness_requires_carpet_body");
            String nextEpisode = text(request, "episode_id"), nextBody = text(request, "body_id");
            if (sequence < 1 || nextEpisode.equals(episodeId) || nextBody.equals(bodyId)) {
                throw new IllegalArgumentException("start_requires_fresh_episode_and_body");
            }
            BiConsumer<String, Map<String, Object>> evaluator = (type, data) -> emit(type, "server_evaluator", data);
            natural = null;
            if (text(request, "subject").equals("carpet_body")) {
                if (retired.size() >= 32) throw new IllegalStateException("restart_after_32_qualification_bodies");
                var spawn = NaturalRun.cell(request.getAsJsonArray("start"));
                var candidate = new CarpetBodyAdapter(server, spawn,
                        "Lab" + java.util.UUID.randomUUID().toString().replace("-", "").substring(0, 10),
                        request.get("yaw").getAsFloat(), 0);
                natural = new NaturalRun(server, request, candidate, null, evaluator);
                body = candidate;
                body.spawn(false);
            } else {
                natural = new NaturalRun(server, request, null,
                        HumanCourse.Subject.named(server, text(request, "player")), evaluator);
            }
            runId = text(request, "run_id"); episodeId = nextEpisode; bodyId = nextBody;
            resetAwareness(nextAwareness);
            compactTerrain=terrainEncoding.equals("sections-v1");
            lastRequestSequence = sequence; lastContactNanos = inbound.receivedNanos(); actionIds.clear();
            everReady = false; readyState = "natural_preparing";
            emit("natural_accepted", "fabric_start", Map.of("request_sequence", sequence));
            return;
        }
        if (inbound.admin() && op.equals("natural_stop") && natural != null) {
            validateIdentity(request);
            if (sequence <= lastRequestSequence) throw new IllegalArgumentException("duplicate_or_out_of_order_sequence");
            lastRequestSequence = sequence;
            natural.finish("failed", "stopped");
            scheduled.clear(); expiryTick = -1;
            if (body != null) { body.release(); body.retire(); retired.add(body); body = null; }
            natural.restoreDevelopmentFixture();
            natural = null; readyState = "idle";
            emit("received", "fabric_start", Map.of("op", op, "request_sequence", sequence));
            return;
        }
        if (inbound.admin() && op.equals("human_course_reset")) {
            if(request.has("awareness")) throw new IllegalArgumentException("qualified_profile_rejects_awareness_override");
            if (resetting || referenceName != null || body != null || courseReset != null
                    || (episode != null && episode.running())) throw new IllegalStateException("lab_busy");
            if (!text(request, "profile_id").equals(NavigationSpec.NAVIGATION) || !request.has("course_spec")
                    || !request.get("course_spec").isJsonObject()) throw new IllegalArgumentException("unqualified_course_profile");
            String nextEpisode = text(request, "episode_id"), nextBody = text(request, "body_id");
            if (sequence < 1 || nextEpisode.equals(episodeId) || nextBody.equals(bodyId)) {
                throw new IllegalArgumentException("reset_requires_fresh_episode_and_body");
            }
            if (human != null) human.park();
            human = null; episode = null;
            human = new HumanCourse(server, text(request, "player"), request.getAsJsonObject("course_spec"),
                    NavigationSpec.NAVIGATION, initialCourseSpec);
            runId = text(request, "run_id"); episodeId = nextEpisode; bodyId = nextBody;
            resetAwareness(AwarenessConfig.legacy());
            lastRequestSequence = sequence; lastContactNanos = inbound.receivedNanos();
            resetRequestSequence = sequence; resetGeneration++; readyState = "resetting_course";
            emit("course_reset_started", "fabric_start", Map.of("generation", resetGeneration,
                    "request_sequence", sequence, "spec_hash", human.specHash(), "subject", "human"));
            return;
        }
        if (inbound.admin() && op.equals("course_reset")) {
            if(request.has("awareness")) throw new IllegalArgumentException("qualified_profile_rejects_awareness_override");
            if (resetting || referenceName != null || human != null) throw new IllegalStateException("lab_busy");
            String profileId = text(request, "profile_id");
            JsonObject nextSpec;
            if (profileId.equals(NavigationSpec.INITIAL)) {
                if (request.has("course_spec")) throw new IllegalArgumentException("initial_profile_rejects_spec_override");
                nextSpec = initialCourseSpec.deepCopy();
            } else if (profileId.equals(NavigationSpec.NAVIGATION) && request.has("course_spec")
                    && request.get("course_spec").isJsonObject()) {
                nextSpec = request.getAsJsonObject("course_spec").deepCopy();
            } else {
                throw new IllegalArgumentException("unqualified_course_profile");
            }
            String nextRun = text(request, "run_id"), nextEpisode = text(request, "episode_id"), nextBody = text(request, "body_id");
            if (sequence < 1 || nextEpisode.equals(episodeId) || nextBody.equals(bodyId)) {
                throw new IllegalArgumentException("reset_requires_fresh_episode_and_body");
            }
            if (retired.size() >= 32) throw new IllegalStateException("restart_after_32_qualification_bodies");
            var prepared = new CourseReset(server, nextSpec, profileId, initialCourseSpec);
            if (episode != null && episode.running()) episode.finish("failed","reset_cancelled");
            scheduled.clear(); expiryTick = -1;
            if (body != null) { body.release(); body.retire(); retired.add(body); body = null; }
            prepared.cleanEntities();
            prepared.resetEnvironment();
            runId = nextRun; episodeId = nextEpisode; bodyId = nextBody;
            resetAwareness(AwarenessConfig.legacy());
            lastRequestSequence = sequence; lastContactNanos = inbound.receivedNanos(); actionIds.clear();
            pendingBeginSequence=0; episode = null; visibleBlocks = List.of(); sensorTick = -100;
            courseReset = prepared; resetting = true; resetSecondVerification = false;
            courseSpec = nextSpec;
            resetWarmUntil = -1; resetRequestSequence = sequence; resetGeneration++;
            resetFirstTerrain = ""; resetFirstBody = ""; resetDigest = "";
            readyState = "resetting_course"; everReady = false;
            emit("course_reset_started", "fabric_start", Map.of("generation", resetGeneration,
                    "request_sequence", sequence, "spec_hash", courseReset.specHash(),
                    "profile_id", courseReset.profileId(), "course_id", courseReset.courseId(),
                    "yaw", courseReset.yaw(), "pitch", courseReset.pitch()));
            return;
        }
        if (courseReset != null && !(inbound.admin() && Set.of("heartbeat", "release", "despawn", "course_begin", "course_end", "course_test").contains(op))
                && !(!inbound.admin() && op.equals("controls") && episode != null && episode.running())) {
            throw new IllegalStateException("course_policy_admission_closed");
        }
        if (inbound.admin() && op.equals("reference_start")) {
            if (body != null || referenceName != null || human != null) throw new IllegalStateException("lab_busy");
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
            if(request.has("awareness")) throw new IllegalArgumentException("qualification_spawn_rejects_awareness_override");
            if (referenceName != null) throw new IllegalStateException("reference_in_progress");
            if (human != null) throw new IllegalStateException("lab_busy");
            if (body != null) throw new IllegalStateException("body_already_active");
            if (retired.size() >= 32) throw new IllegalStateException("restart_after_32_qualification_bodies");
            runId = text(request, "run_id");
            episodeId = text(request, "episode_id");
            bodyId = text(request, "body_id");
            resetAwareness(AwarenessConfig.legacy());
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
            if (inbound.admin() && human != null && op.startsWith("human_course_")) {
                humanOp(op, sequence);
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
            validateCourseControls(controls);
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
            case "course_begin" -> {
                if (courseReset == null || resetting || !readyState.equals("reset_ready") || episode != null)
                    throw new IllegalStateException("course_not_reset_ready");
                courseReset.ensureHumanClear(); courseReset.ensureOnlyBody(body.name());
                if (!CourseReset.digest(body.resetFingerprint()).equals(resetFirstBody))
                    throw new IllegalStateException("body_changed_since_reset");
                if (pendingBeginSequence!=0) throw new IllegalStateException("begin_already_pending");
                pendingBeginSequence=sequence; courseReset.beginVerify(); readyState="begin_verifying";
            }
            case "course_end" -> {
                if (natural!=null && natural.running()) { natural.finish("failed","controller_cancel"); break; }
                if (episode==null || !episode.running()) throw new IllegalStateException("episode_not_running");
                episode.finish("failed","controller_cancel");
            }
            case "course_test" -> {
                if (episode!=null || !readyState.equals("reset_ready")) throw new IllegalStateException("test_requires_reset_ready");
                String kind=text(request,"kind");
                if(kind.equals("sensor_check")) {
                    courseReset.ensureHumanClear();
                    var verification=SensorVerification.check(body.player(),server.getTickCount());
                    readyState="sensor_checked"; resetDigest="";
                    emit("course_sensor_checked","fabric_start",Map.of("request_sequence",sequence,"verification",verification));
                    break;
                }
                if(kind.equals("target_missing")) {
                    var origin=courseSpec.getAsJsonArray("origin"); var target=courseSpec.getAsJsonArray("target");
                    var pos=new net.minecraft.core.BlockPos(origin.get(0).getAsInt()+target.get(0).getAsInt(),
                            origin.get(1).getAsInt()+target.get(1).getAsInt(),origin.get(2).getAsInt()+target.get(2).getAsInt());
                    server.overworld().setBlockAndUpdate(pos,net.minecraft.world.level.block.Blocks.AIR.defaultBlockState());
                    emit("course_test_prepared","fabric_start",Map.of("request_sequence",sequence,"kind",kind));
                    break;
                }
                if (!kind.equals("planted_item")) throw new IllegalArgumentException("unknown_course_test");
                var item=new net.minecraft.world.entity.item.ItemEntity(server.overworld(),
                        body.player().getX()+2,body.player().getY(),body.player().getZ(),
                        new net.minecraft.world.item.ItemStack(net.minecraft.world.item.Items.OAK_LOG));
                item.setNeverPickUp(); server.overworld().addFreshEntity(item);
                emit("course_test_prepared","fabric_start",Map.of("request_sequence",sequence,"kind","planted_item"));
            }
            case "heartbeat"  -> { }
            case "release" -> {
                scheduled.clear(); body.release(); expiryTick = -1;
                emit("released", "fabric_start", Map.of("reason", "explicit"));
            }
            case "despawn" -> {
                if (episode!=null && episode.running()) episode.finish("failed","despawn_cancelled");
                scheduled.clear(); if (courseReset == null) body.restoreFixture(); body.retire(); retired.add(body); body = null;
                expiryTick = -1; readyState = "idle";
                pendingBeginSequence=0; courseReset = null; episode = null; resetting = false; resetDigest = "";
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
            case "awareness_check" -> {
                if(!readyState.equals("ready") || courseReset!=null || natural!=null)
                    throw new IllegalStateException("awareness_check_requires_idle_development_body");
                scheduled.clear(); body.release(); expiryTick=-1;
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
        String liveBodyState = "";
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
            liveBodyState = state;
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
        if (human != null && !human.ready()) tickHuman();
        if (natural != null) {
            natural.tick(liveBodyState.equals("ready"));
            if (natural.running()) readyState = "running";
            else if (natural.terminal()) {
                readyState = "episode_terminal"; scheduled.clear(); expiryTick = -1;
                if (body != null) body.release();
            }
        }
        if (resetting) tickCourseReset(liveBodyState);
        else if (courseReset != null && !resetDigest.isEmpty() && liveBodyState.equals("ready")) readyState = "reset_ready";
        if (pendingBeginSequence!=0) tickCourseBegin();
        if (episode != null) {
            episode.tick();
            readyState=episode.running()?"running":"episode_terminal";
            if (!episode.running()) {scheduled.clear();expiryTick=-1;if(body!=null)body.release();}
        }
        updatePolicySnapshot();
        if (body != null && traceEnabled) emit("phase", "fabric_end_observation", Map.of());
        var status = new LinkedHashMap<String,Object>(Map.of("schema_version", 1, "launch_id", launchId,
                "run_id", runId, "episode_id", episodeId, "body_id", bodyId,
                "tick", server.getTickCount(), "state", readyState,
                "body", body == null ? Map.of() : body.observe(), "requests_received", received,
                "course_reset", courseReset == null ? Map.of() : Map.of("generation", resetGeneration,
                        "progress", courseReset.progress(), "state_digest", resetDigest)));
        status.put("awareness",awarenessState());
        snapshot=Map.copyOf(status);
    }

    private void tickCourseReset(String liveBodyState) {
        try {
            courseReset.ensureHumanClear();
            if (!courseReset.done()) courseReset.step();
            if (courseReset.done() && body == null) {
                resetFirstTerrain = courseReset.terrainDigest();
                body = new CarpetBodyAdapter(server, courseReset.spawn(),
                        "Lab"+java.util.UUID.randomUUID().toString().replace("-", "").substring(0,10),
                        courseReset.yaw(), courseReset.pitch());
                body.spawn(false);
            } else if (body != null && liveBodyState.equals("ready")) {
                if (resetWarmUntil < 0) resetWarmUntil = server.getTickCount()+20;
                if (server.getTickCount() >= resetWarmUntil && !resetSecondVerification) {
                    if (body.player().position().distanceToSqr(courseReset.spawn()) > 1e-8
                            || body.player().getYRot() != courseReset.yaw()
                            || body.player().getXRot() != courseReset.pitch()) {
                        throw new IllegalStateException("reset_spawn_mismatch");
                    }
                    resetFirstBody = CourseReset.digest(body.resetFingerprint());
                    resetSecondVerification = true;
                    courseReset.beginVerify();
                } else if (resetSecondVerification && courseReset.done()) {
                    var physical = body.resetFingerprint();
                    String current = CourseReset.digest(physical);
                    if (!current.equals(resetFirstBody) || !courseReset.terrainDigest().equals(resetFirstTerrain)) {
                        throw new IllegalStateException("reset_state_changed_during_warmed_verification");
                    }
                    courseReset.ensureOnlyBody(body.name());
                    resetDigest = CourseReset.digest(Map.of("terrain", resetFirstTerrain,
                            "body", physical, "spec_hash", courseReset.specHash()));
                    resetting = false; readyState = "reset_ready";
                    emit("course_reset_complete", "fabric_end", Map.ofEntries(
                            Map.entry("generation", resetGeneration), Map.entry("request_sequence", resetRequestSequence),
                            Map.entry("state_digest", resetDigest), Map.entry("terrain_digest", resetFirstTerrain),
                            Map.entry("body_digest", current), Map.entry("spec_hash", courseReset.specHash()),
                            Map.entry("body_state", physical), Map.entry("profile_id", courseReset.profileId()),
                            Map.entry("course_id", courseReset.courseId()), Map.entry("yaw", courseReset.yaw()),
                            Map.entry("pitch", courseReset.pitch())));
                }
            }
            if (resetting) readyState = "resetting_course";
        } catch (Exception error) {
            scheduled.clear(); expiryTick = -1;
            if (body != null) { body.retire(); retired.add(body); body = null; }
            resetting = false; readyState = "reset_failed";
            emit("course_reset_failed", "fabric_end", Map.of("generation", resetGeneration,
                    "request_sequence", resetRequestSequence, "reason", error.toString()));
        }
    }



    private void humanOp(String op, long sequence) {
        emit("received", "fabric_start", Map.of("op", op, "request_sequence", sequence));
        switch (op) {
            case "human_course_begin" -> {
                if (!human.ready() || episode != null) throw new IllegalStateException("course_not_reset_ready");
                human.verifyAtSpawn();
                var subject = human;
                episode = new CourseEpisode(server, subject::player, subject::owns, subject.spec(),
                        (type, data) -> emit(type, "server_evaluator", data));
                readyState = "running";
                emit("course_started", "fabric_end", Map.of("request_sequence", sequence, "subject", "human",
                        "start_tick", episode.publicState().get("start_tick"),
                        "deadline_tick", episode.publicState().get("deadline_tick")));
            }
            case "human_course_end" -> {
                if (episode == null || !episode.running()) throw new IllegalStateException("episode_not_running");
                episode.finish("failed", "controller_cancel");
            }
            case "human_course_stop" -> {
                if (episode != null && episode.running()) episode.finish("failed", "human_course_stopped");
                human.park(); human = null; episode = null; readyState = "idle";
            }
            default -> throw new IllegalArgumentException("unknown_admin_operation");
        }
    }

    private void tickHuman() {
        try {
            var complete = human.tick();
            if (complete == null) return;
            readyState = "reset_ready";
            var data = new LinkedHashMap<String, Object>(complete);
            data.put("generation", resetGeneration); data.put("request_sequence", resetRequestSequence);
            emit("course_reset_complete", "fabric_end", Map.copyOf(data));
        } catch (Exception error) {
            human.park(); human = null; readyState = "reset_failed";
            emit("course_reset_failed", "fabric_end", Map.of("generation", resetGeneration,
                    "request_sequence", resetRequestSequence, "reason", error.toString()));
        }
    }

    private void tickCourseBegin() {
        try {
            courseReset.ensureHumanClear(); courseReset.ensureOnlyBody(body.name());
            courseReset.step(); readyState="begin_verifying";
            if (!courseReset.done()) return;
            if (!courseReset.terrainDigest().equals(resetFirstTerrain)
                    || !CourseReset.digest(body.resetFingerprint()).equals(resetFirstBody))
                throw new IllegalStateException("state_changed_since_reset");
            episode=new CourseEpisode(server,body,courseSpec,(type,data)->emit(type,"server_evaluator",data));
            readyState="running";
            emit("course_started","fabric_end",Map.of("request_sequence",pendingBeginSequence,
                    "start_tick",episode.publicState().get("start_tick"),
                    "deadline_tick",episode.publicState().get("deadline_tick")));
            pendingBeginSequence=0;
        } catch(Exception error) {
            emit("course_begin_failed","fabric_end",Map.of("request_sequence",pendingBeginSequence,"reason",error.toString()));
            pendingBeginSequence=0; readyState="begin_failed";resetDigest="";
            scheduled.clear();expiryTick=-1;if(body!=null)body.release();
        }
    }

    private void validateCourseControls(Controls controls) {
        if (courseReset==null) return;
        if (episode==null || !episode.running()) throw new IllegalStateException("episode_not_running");
        if (controls.attack_pulse() || controls.use_hold() || controls.use_pulse() || controls.hotbar_slot()!=0) {
            episode.attempted("forbidden_control");
            throw new IllegalArgumentException("course_forbidden_control");
        }
    }

    private boolean policyRunning() {
        return (episode != null && episode.running()) || (natural != null && natural.running() && body != null);
    }

    private void resetAwareness(AwarenessConfig next) {
        scheduled.clear(); expiryTick=-1;
        if(body!=null && body.player()!=null) body.release();
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
        if(body!=null && body.player()!=null) {
            var observed=body.observe();
            for(String key:List.of("position","velocity","yaw","pitch","on_ground","health","food",
                    "mode","pose","alive","sprinting","inventory","ray","block_reach","entity_reach","attack_strength"))
                filtered.put(key,observed.get(key));
            // Inventory-only evidence for natural development interactions. Legacy
            // qualified course observations retain their frozen body contract.
            if(natural!=null) filtered.put("building_pickups",natural.buildingPickups());
            if(server.getTickCount()-sensorTick>=awareness.interval() && policyRunning()) {
                visibleBlocks=awareness.preset().equals("legacy")?PolicySensor.visible(body.player(),server.getTickCount())
                        :awarenessSensor.sample(body.player(),server.getTickCount());
                sensorTick=server.getTickCount();
                if(compactTerrain) terrainBatch=new TerrainBatch(visibleBlocks,sensorTick);
            }
        }
        var resources=Map.of("average_mspt",server.getAverageTickTimeNanos()/1_000_000.0,
                "smoothed_mspt",server.getCurrentSmoothedTickTime());
        policySnapshot=Map.ofEntries(Map.entry("schema_version",1),Map.entry("launch_id",launchId),
                Map.entry("run_id",runId),Map.entry("episode_id",episodeId),Map.entry("body_id",bodyId),
                Map.entry("tick",server.getTickCount()),Map.entry("state",readyState),
                Map.entry("body",Map.copyOf(filtered)),Map.entry("visible_blocks",visibleBlocks),
                Map.entry("sensor_profile",awareness.profile()), Map.entry("awareness",awarenessState()),
                Map.entry("visible_entities",body!=null && body.player()!=null && (episode!=null || natural!=null)?PolicySensor.entities(body.player(),awareness):List.of()),
                Map.entry("episode",natural!=null?natural.publicState():episode==null?Map.of():episode.publicState()),
                Map.entry("resources",resources));
        if(compactTerrain) {
            var compact=new LinkedHashMap<String,Object>(policySnapshot);
            compact.remove("visible_blocks"); compact.put("terrain_sections",terrainBatch);
            policySnapshot=Map.copyOf(compact);
        }
        if(policyRunning()) {
            var sample=new LinkedHashMap<String,Object>(policySnapshot);
            // Every simulation tick records body state. Map updates carry a complete local
            // Terrain batches follow the declared cadence; expanded profiles are incremental.
            if(sensorTick!=server.getTickCount()) sample.remove("visible_blocks");
            if(sensorTick!=server.getTickCount()) sample.remove("terrain_sections");
            sample.put("map_update",sensorTick==server.getTickCount());
            emit("policy_sample","fabric_end_observation",Map.copyOf(sample));
        }
    }

    public static boolean allowBreak(ServerPlayer player, net.minecraft.core.BlockPos pos) {
        var b=active;
        return b==null || b.episode==null || !b.episode.owns(player) || b.episode.allowBreak(player,pos);
    }
    public static void beforeCourseBreak(ServerPlayer p,net.minecraft.core.BlockPos pos) {
        var b=active;if(b!=null && b.episode!=null)b.episode.beforeBreak(p,pos);
    }
    public static void afterCourseBreak(ServerPlayer p,net.minecraft.core.BlockPos pos,boolean success) {
        var b=active;if(b!=null && b.episode!=null)b.episode.afterBreak(p,pos,success);
        if(b!=null && b.natural!=null)b.natural.broke(p,success);
    }
    public static boolean permitCourseMutation(net.minecraft.world.level.Level level, net.minecraft.core.BlockPos pos,
                                               net.minecraft.world.level.block.state.BlockState state) {
        var b=active;
        return b==null || !b.server.isSameThread() || level!=b.server.overworld() || b.episode==null
                || b.episode.permitMutation(pos,state);
    }
    public static void courseDrop(net.minecraft.world.entity.Entity entity,boolean added) {
        var b=active;if(b!=null && b.server.isSameThread() && b.episode!=null)b.episode.created(entity,added);
    }
    public static void coursePickup(ServerPlayer p,net.minecraft.world.entity.item.ItemEntity item,int before,int after,String kind) {
        var b=active;if(b!=null && b.server.isSameThread() && b.episode!=null)b.episode.pickup(p,item,before,after);
        if(b!=null && b.server.isSameThread() && b.natural!=null)b.natural.pickup(p,kind,before,after);
    }

    public static void naturalHealthChanged(ServerPlayer p,float before,float after) {
        var b=active;
        if(b!=null && b.server.isSameThread() && b.natural!=null)b.natural.healthChanged(p,before,after);
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
