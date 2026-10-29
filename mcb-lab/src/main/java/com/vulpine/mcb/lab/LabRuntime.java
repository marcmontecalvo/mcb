package com.vulpine.mcb.lab;

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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.BiConsumer;
import java.util.function.Supplier;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * Lab bridge: courses, resets, natural runs, fixtures, reference recording and scoring.
 * Every subject is a named player; the lab never spawns or drives a body, so it scores a
 * Carpet body, a human or a Baritone client the same way. Handlers never touch world objects.
 */
public final class LabRuntime implements AutoCloseable {
    /** A subject named in a start request may join later (e.g. a body spawned in parallel). */
    static final int SUBJECT_JOIN_TICKS = 600;
    /** Consecutive ticks a joining subject must be present before the lab takes it over. */
    static final int SUBJECT_PRESENT_TICKS = 5;
    private static final Gson GSON = new Gson();
    private static final Set<String> OPS = Set.of("natural_probe", "natural_start", "course_reset",
            "reference_start", "fixture", "death_proof", "knockback_proof", "stop", "heartbeat",
            "course_begin", "course_end", "course_test", "reference_stop", "natural_path");
    private static volatile LabRuntime active;

    private final MinecraftServer server;
    private final Vec3 fixture;
    private final JsonObject initialCourseSpec;
    private final String launchId = System.getProperty("mclab.launch_id", "unmanaged");
    private final ArrayBlockingQueue<Inbound> inbox = new ArrayBlockingQueue<>(64);
    private final ArrayDeque<Event> events = new ArrayDeque<>();
    private final HttpServer http;
    private final ThreadPoolExecutor workers;
    private volatile Map<String, Object> snapshot = Map.of("state", "idle");
    private long eventSequence;
    private long lastRequestSequence;
    private String runId = "none";
    private String episodeId = "none";
    private String bodyId = "none";
    private String readyState = "idle";

    private PendingCourse pending;
    private SubjectCourse course;
    private CourseEpisode episode;
    private long pendingBeginSequence;
    private int resetGeneration;
    private long resetRequestSequence;
    private String resetDigest = "";
    private NaturalRun natural;
    private String referenceName;
    private int referenceEnd;
    private String fixtureSubject;
    private Map<String, Object> referenceSetup = Map.of();
    /** Opt-in per course: a silent controller fails the episode (bodies driven over the bridge). */
    private boolean watchdog;
    private long lastContactNanos = System.nanoTime();

    public record Inbound(JsonObject json, long receivedNanos) {}
    public record Event(int schema_version, String launch_id, String run_id, String episode_id,
                        String body_id, long sequence, int tick, String phase, String type,
                        Map<String, Object> data) {}
    private static final class PendingCourse {
        final String player; final JsonObject spec; final String profileId; final int deadline;
        int presentTicks;
        PendingCourse(String player, JsonObject spec, String profileId, int deadline) {
            this.player = player; this.spec = spec; this.profileId = profileId; this.deadline = deadline;
        }
    }

    public LabRuntime(MinecraftServer server, Vec3 fixture, JsonObject courseSpec) throws IOException {
        this.server = server;
        this.fixture = fixture;
        this.initialCourseSpec = courseSpec.deepCopy();
        int port = Integer.parseInt(System.getProperty("mclab.lab_port", "8768"));
        String token = System.getProperty("mclab.bridge_token", "");
        if (token.length() < 32) throw new IllegalStateException("Missing private bridge token");
        http = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 8);
        workers = new ThreadPoolExecutor(2, 4, 10, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(16), runnable -> {
                    var thread = new Thread(runnable, "mcb-lab-http");
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
                if (path.equals("/state")) { respond(exchange, 200, snapshot); return; }
                if (path.equals("/events")) {
                    String query = exchange.getRequestURI().getRawQuery();
                    long after = query == null ? 0 : Long.parseLong(query.replace("after=", ""));
                    respond(exchange, 200, eventBatch(after));
                    return;
                }
            }
            if (exchange.getRequestMethod().equals("POST") && path.equals("/admin")) {
                var bytes = exchange.getRequestBody().readNBytes(16385);
                if (bytes.length > 16384) {
                    respond(exchange, 413, Map.of("error", "request_too_large"));
                    return;
                }
                var request = JsonParser.parseString(new String(bytes, StandardCharsets.UTF_8)).getAsJsonObject();
                if (!inbox.offer(new Inbound(request, System.nanoTime()))) {
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

    private BiConsumer<String, Map<String, Object>> evaluator() {
        return (type, data) -> emit(type, "server_evaluator", data);
    }

    private static String text(JsonObject request, String key) {
        String value = request.get(key).getAsString();
        if (!value.matches("[A-Za-z0-9_.:-]{1,80}")) throw new IllegalArgumentException("invalid_" + key);
        return value;
    }

    public void preTick() {
        if (watchdog && episode != null && episode.running()
                && System.nanoTime() - lastContactNanos > TimeUnit.SECONDS.toNanos(2)) {
            episode.finish("failed", "controller_watchdog");
        }
        for (int budget = 0; budget < 8; budget++) {
            var request = inbox.poll();
            if (request == null) break;
            try {
                if (System.nanoTime() - request.receivedNanos() > TimeUnit.SECONDS.toNanos(2)) {
                    throw new IllegalArgumentException("stale_queued_request");
                }
                admit(request);
            } catch (Exception error) {
                emit("rejected", "fabric_start", Map.of("request_sequence",
                        request.json().has("sequence") ? request.json().get("sequence").toString() : "missing",
                        "reason", error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage()));
            }
        }
    }

    private boolean busy() {
        return pending != null || course != null || episode != null || natural != null || referenceName != null;
    }

    /** Start-type requests open a new run identity; the sequence restarts with it. */
    private void adopt(JsonObject request, long sequence) {
        if (sequence < 1) throw new IllegalArgumentException("sequence_must_be_positive");
        runId = text(request, "run_id"); episodeId = text(request, "episode_id"); bodyId = text(request, "body_id");
        lastRequestSequence = sequence;
        lastContactNanos = System.nanoTime();
    }

    private void requireFreshIdentity(JsonObject request, long sequence, String reason) {
        if (sequence < 1 || text(request, "episode_id").equals(episodeId) || text(request, "body_id").equals(bodyId))
            throw new IllegalArgumentException(reason);
    }

    private void validateIdentity(JsonObject request, long sequence) {
        if (!text(request, "run_id").equals(runId) || !text(request, "episode_id").equals(episodeId)
                || !text(request, "body_id").equals(bodyId)) throw new IllegalArgumentException("wrong_identity");
        if (sequence <= lastRequestSequence) throw new IllegalArgumentException("duplicate_or_out_of_order_sequence");
        lastRequestSequence = sequence;
        lastContactNanos = System.nanoTime();
    }

    private void admit(Inbound inbound) throws Exception {
        var request = inbound.json();
        if (request.get("schema_version").getAsInt() != 1) throw new IllegalArgumentException("schema_version");
        long sequence = request.get("sequence").getAsLong();
        String op = text(request, "op");
        // The allowlist precedes identity: unknown operations are never interpreted.
        if (!OPS.contains(op)) throw new IllegalArgumentException("unknown_admin_operation");
        switch (op) {
            case "natural_probe" -> {
                if (busy()) throw new IllegalStateException("lab_busy");
                var cells = NaturalRun.probe(server, request.get("center_x").getAsInt(), request.get("center_z").getAsInt(),
                        request.get("radius").getAsInt(), request.get("stride").getAsInt());
                emit("natural_probe", "fabric_start", Map.of("request_sequence", sequence, "cells", cells));
                return;
            }
            case "natural_start" -> { naturalStart(request, sequence); return; }
            case "course_reset" -> { courseReset(request, sequence); return; }
            case "reference_start" -> { referenceStart(request, sequence); return; }
            case "fixture", "death_proof", "knockback_proof" -> { proof(op, request, sequence); return; }
            case "stop" -> {
                if (!busy()) {
                    // Idle stop needs no identity: it only restores a dirty mechanics fixture.
                    stopAll("stopped");
                    emit("stopped", "fabric_start", Map.of("request_sequence", sequence));
                    return;
                }
            }
            default -> { }
        }
        validateIdentity(request, sequence);
        emit("received", "fabric_start", Map.of("op", op, "request_sequence", sequence));
        switch (op) {
            case "heartbeat" -> { }
            case "natural_path" -> {
                // A viewing aid: a path arriving after the episode ends is simply dropped.
                if (natural != null && natural.running()) natural.showPath(request.getAsJsonArray("points"));
            }
            case "stop" -> {
                stopAll(request.has("reason") ? text(request, "reason") : "stopped");
                emit("stopped", "fabric_start", Map.of("request_sequence", sequence));
            }
            case "course_begin" -> {
                if (course == null || !readyState.equals("reset_ready") || episode != null || pendingBeginSequence != 0)
                    throw new IllegalStateException("course_not_reset_ready");
                course.begin();
                pendingBeginSequence = sequence; readyState = "begin_verifying";
            }
            case "course_end" -> {
                if (natural != null && natural.running()) { natural.finish("failed", "controller_cancel"); break; }
                if (episode == null || !episode.running()) throw new IllegalStateException("episode_not_running");
                episode.finish("failed", "controller_cancel");
            }
            case "course_test" -> courseTest(request, sequence);
            case "reference_stop" -> {
                referenceName = null; readyState = "idle";
                LabFixture.cleanupInteractions(server, fixture);
            }
            default -> throw new IllegalArgumentException("unknown_admin_operation");
        }
    }

    private void naturalStart(JsonObject request, long sequence) {
        if (natural != null && natural.terminal()) { natural.restoreDevelopmentFixture(); natural = null; }
        if (busy()) throw new IllegalStateException("lab_busy");
        requireFreshIdentity(request, sequence, "start_requires_fresh_episode_and_body");
        String kind = request.has("subject") ? text(request, "subject") : "human";
        natural = new NaturalRun(server, request, text(request, "player"), kind, evaluator());
        adopt(request, sequence);
        readyState = "natural_preparing";
        emit("natural_accepted", "fabric_start", Map.of("request_sequence", sequence));
    }

    private void courseReset(JsonObject request, long sequence) {
        if (request.has("awareness")) throw new IllegalArgumentException("qualified_profile_rejects_awareness_override");
        if (natural != null || referenceName != null) throw new IllegalStateException("lab_busy");
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
        requireFreshIdentity(request, sequence, "reset_requires_fresh_episode_and_body");
        String player = text(request, "player");
        if (episode != null && episode.running()) episode.finish("failed", "reset_cancelled");
        episode = null; course = null; pendingBeginSequence = 0; resetDigest = "";
        pending = new PendingCourse(player, nextSpec, profileId, server.getTickCount() + SUBJECT_JOIN_TICKS);
        watchdog = request.has("watchdog") && request.get("watchdog").getAsBoolean();
        adopt(request, sequence);
        resetRequestSequence = sequence; resetGeneration++; readyState = "awaiting_subject";
        emit("course_reset_started", "fabric_start", Map.of("generation", resetGeneration,
                "request_sequence", sequence, "spec_hash", nextSpec.get("spec_hash").getAsString(),
                "profile_id", profileId, "course_id", nextSpec.get("course_id").getAsString(),
                "yaw", nextSpec.get("yaw").getAsFloat(), "pitch", nextSpec.get("pitch").getAsFloat(),
                "subject", player));
    }

    private void referenceStart(JsonObject request, long sequence) throws Exception {
        if (busy()) throw new IllegalStateException("lab_busy");
        String name = text(request, "player");
        var player = LabFixture.named(server, name);
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
        adopt(request, sequence);
        Map<String, Object> setup = Map.of();
        if (!mechanic.equals("none")) {
            setup = HumanReferenceFixture.prepare(server, player, fixture, mechanic);
        } else if (interactions) {
            LabFixture.prepareInteractions(server, player, fixture, combat);
        }
        referenceName = name; referenceEnd = server.getTickCount() + seconds * 20;
        readyState = "reference_recording";
        emit("reference_started", "fabric_start", Map.of("player", name,
                "request_sequence", sequence, "end_tick", referenceEnd, "setup", setup));
    }

    /**
     * Development fixtures and proofs on a named player. Fixtures never run during a run;
     * death/knockback diagnostics may target the running subject under the run's identity.
     */
    private void proof(String op, JsonObject request, long sequence) throws Exception {
        if (busy() && op.equals("fixture")) throw new IllegalStateException("course_policy_admission_closed");
        String name = text(request, "player");
        var player = LabFixture.named(server, name);
        if (busy()) validateIdentity(request, sequence);
        else adopt(request, sequence);
        switch (op) {
            case "fixture" -> {
                int dirt = request.get("dirt").getAsInt();
                if (dirt != 0 && dirt != 16) throw new IllegalArgumentException("fixture_dirt_0_or_16");
                fixtureSubject = name;
                referenceSetup = LabFixture.setup(server, player, fixture, request.get("log").getAsBoolean(), dirt,
                        request.has("mechanic") ? text(request, "mechanic") : "flat");
                emit("fixture_setup", "fabric_start", Map.of("dirt", dirt, "request_sequence", sequence));
            }
            case "death_proof" -> LabFixture.deathProof(server, player);
            case "knockback_proof" -> LabFixture.knockbackProof(player);
            default -> throw new IllegalArgumentException("unknown_admin_operation");
        }
        emit("received", "fabric_start", Map.of("op", op, "request_sequence", sequence));
    }

    private void courseTest(JsonObject request, long sequence) {
        if (episode != null || !readyState.equals("reset_ready")) throw new IllegalStateException("test_requires_reset_ready");
        String kind = text(request, "kind");
        var spec = course.spec();
        if (kind.equals("target_missing")) {
            var origin = spec.getAsJsonArray("origin"); var target = spec.getAsJsonArray("target");
            var pos = new net.minecraft.core.BlockPos(origin.get(0).getAsInt()+target.get(0).getAsInt(),
                    origin.get(1).getAsInt()+target.get(1).getAsInt(), origin.get(2).getAsInt()+target.get(2).getAsInt());
            server.overworld().setBlockAndUpdate(pos, net.minecraft.world.level.block.Blocks.AIR.defaultBlockState());
        } else if (kind.equals("planted_item")) {
            var p = course.player();
            if (p == null) throw new IllegalStateException("subject_disconnected");
            var item = new net.minecraft.world.entity.item.ItemEntity(server.overworld(), p.getX()+2, p.getY(), p.getZ(),
                    new net.minecraft.world.item.ItemStack(net.minecraft.world.item.Items.OAK_LOG));
            item.setNeverPickUp(); server.overworld().addFreshEntity(item);
        } else throw new IllegalArgumentException("unknown_course_test");
        emit("course_test_prepared", "fabric_start", Map.of("request_sequence", sequence, "kind", kind));
    }

    /** Ends whatever is running, restores lab fixtures, and returns to idle. */
    private void stopAll(String reason) {
        if (episode != null && episode.running()) episode.finish("failed", reason);
        if (natural != null) { natural.finish("failed", reason); natural.restoreDevelopmentFixture(); }
        if (course != null) course.park();
        if (referenceName != null) LabFixture.cleanupInteractions(server, fixture);
        if (fixtureSubject != null) { LabFixture.restoreFixture(server, fixture, fixtureSubject); fixtureSubject = null; }
        pending = null; course = null; episode = null; natural = null; referenceName = null;
        pendingBeginSequence = 0; resetDigest = ""; referenceSetup = Map.of();
        watchdog = false;
        readyState = "idle";
    }

    public void postTick() {
        tickReference();
        if (pending != null) tickPending();
        if (course != null && !course.ready() && pendingBeginSequence == 0 && episode == null) tickCourseReset();
        if (pendingBeginSequence != 0) tickCourseBegin();
        if (natural != null) {
            natural.tick();
            if (natural.running()) readyState = "running";
            else if (natural.terminal()) readyState = "episode_terminal";
        }
        if (episode != null) {
            episode.tick();
            readyState = episode.running() ? "running" : "episode_terminal";
        }
        publishState();
    }

    private void tickReference() {
        if (referenceName == null) return;
        var player = server.getPlayerList().getPlayerByName(referenceName);
        if (player == null) {
            emit("reference_invalid", "fabric_end", Map.of("reason", "human_disconnected"));
            referenceName = null; readyState = "reference_invalid";
        } else if (server.getTickCount() >= referenceEnd) {
            emit("reference_complete", "fabric_end", Map.of());
            referenceName = null; readyState = "reference_complete";
            LabFixture.cleanupInteractions(server, fixture);
        } else emit("reference_sample", "fabric_end", LabFixture.reference(player));
    }

    private void tickPending() {
        var joined = server.getPlayerList().getPlayerByName(pending.player);
        pending.presentTicks = joined == null ? 0 : pending.presentTicks + 1;
        try {
            if (pending.presentTicks < SUBJECT_PRESENT_TICKS) {
                if (server.getTickCount() >= pending.deadline) throw new IllegalStateException("subject_not_present");
                return;
            }
            course = new SubjectCourse(server, pending.player, pending.spec, pending.profileId, initialCourseSpec);
            pending = null; readyState = "resetting_course";
        } catch (Exception error) {
            pending = null; readyState = "reset_failed";
            emit("course_reset_failed", "fabric_end", Map.of("generation", resetGeneration,
                    "request_sequence", resetRequestSequence, "reason", error.toString()));
        }
    }

    private void tickCourseReset() {
        try {
            var complete = course.tick();
            if (complete == null) return;
            resetDigest = (String) complete.get("state_digest");
            readyState = "reset_ready";
            var data = new LinkedHashMap<String, Object>(complete);
            data.put("generation", resetGeneration); data.put("request_sequence", resetRequestSequence);
            emit("course_reset_complete", "fabric_end", Map.copyOf(data));
        } catch (Exception error) {
            course.park(); course = null; readyState = "reset_failed";
            emit("course_reset_failed", "fabric_end", Map.of("generation", resetGeneration,
                    "request_sequence", resetRequestSequence, "reason", error.toString()));
        }
    }

    private void tickCourseBegin() {
        try {
            if (!course.tickBegin()) { readyState = "begin_verifying"; return; }
            var subject = course;
            episode = new CourseEpisode(server, subject::player, subject::owns, subject.spec(), evaluator());
            readyState = "running";
            emit("course_started", "fabric_end", Map.of("request_sequence", pendingBeginSequence,
                    "start_tick", episode.publicState().get("start_tick"),
                    "deadline_tick", episode.publicState().get("deadline_tick")));
            pendingBeginSequence = 0;
        } catch (Exception error) {
            emit("course_begin_failed", "fabric_end", Map.of("request_sequence", pendingBeginSequence,
                    "reason", error.toString()));
            pendingBeginSequence = 0; readyState = "begin_failed"; resetDigest = "";
        }
    }

    private void publishState() {
        var status = new LinkedHashMap<String, Object>();
        status.put("schema_version", 1); status.put("launch_id", launchId);
        status.put("run_id", runId); status.put("episode_id", episodeId); status.put("body_id", bodyId);
        status.put("tick", server.getTickCount()); status.put("state", readyState);
        status.put("fixture", List.of(fixture.x, fixture.y, fixture.z));
        status.put("fixture_state", LabFixture.fixtureState(server, fixture));
        status.put("reference_setup", referenceSetup);
        status.put("course_reset", course == null && pending == null ? Map.of() : Map.of("generation", resetGeneration,
                "progress", course == null ? Map.of("phase", "awaiting_subject") : course.progress(),
                "state_digest", resetDigest));
        status.put("episode", natural != null ? natural.publicState() : episode == null ? Map.of() : episode.publicState());
        // Inventory-only pickup ledger, present only for natural development runs.
        if (natural != null) status.put("building_pickups", natural.buildingPickups());
        snapshot = Map.copyOf(status);
    }

    // ---- Hooks called from lab mixins (server thread). ----

    public static boolean onServerThread() {
        var lab = active;
        return lab != null && lab.server.isSameThread();
    }
    public static boolean allowBreak(ServerPlayer player, net.minecraft.core.BlockPos pos) {
        var b = active;
        return b == null || b.episode == null || !b.episode.owns(player) || b.episode.allowBreak(player, pos);
    }
    public static void beforeCourseBreak(ServerPlayer p, net.minecraft.core.BlockPos pos) {
        var b = active; if (b != null && b.episode != null) b.episode.beforeBreak(p, pos);
    }
    public static void afterCourseBreak(ServerPlayer p, net.minecraft.core.BlockPos pos, boolean success) {
        var b = active; if (b == null || !b.server.isSameThread()) return;
        if (b.episode != null) b.episode.afterBreak(p, pos, success);
        if (b.natural != null) b.natural.broke(p, success);
    }
    public static boolean permitCourseMutation(net.minecraft.world.level.Level level, net.minecraft.core.BlockPos pos,
                                               net.minecraft.world.level.block.state.BlockState state) {
        var b = active;
        return b == null || !b.server.isSameThread() || level != b.server.overworld() || b.episode == null
                || b.episode.permitMutation(pos, state);
    }
    public static void courseDrop(net.minecraft.world.entity.Entity entity, boolean added) {
        var b = active; if (b != null && b.server.isSameThread() && b.episode != null) b.episode.created(entity, added);
    }
    public static void coursePickup(ServerPlayer p, net.minecraft.world.entity.item.ItemEntity item, int before, int after, String kind) {
        var b = active; if (b == null || !b.server.isSameThread()) return;
        if (b.episode != null) b.episode.pickup(p, item, before, after);
        if (b.natural != null) b.natural.pickup(p, kind, before, after);
    }
    public static void naturalHealthChanged(ServerPlayer p, float before, float after) {
        var b = active;
        if (b != null && b.server.isSameThread() && b.natural != null) b.natural.healthChanged(p, before, after);
    }
    /** Interaction packets and results of the player being recorded as a reference. */
    public static void interaction(ServerPlayer player, String type, Supplier<Map<String, Object>> data) {
        var b = active;
        if (b == null || !b.server.isSameThread()) return;
        if (b.referenceName != null && b.referenceName.equals(player.getName().getString())) {
            b.emit(type, "server_interaction", data.get());
        }
    }

    @Override public void close() {
        active = null;
        http.stop(0);
        workers.shutdownNow();
    }
}
