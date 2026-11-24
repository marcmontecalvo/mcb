package com.vulpine.mcb.body;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * The body bridge: one HTTP endpoint, one request inbox, one event log and one set of bodies. Each body is a
 * {@link BodyRuntime} (a body's own motors, plans, identity and state); this class routes to them by body id and
 * tags what they emit. Spawning an id the hub does not hold adds a body, despawning removes one; every op, snapshot
 * and event belongs to exactly one body (or to none: arena and chat events are shared). A client written for one body
 * keeps working: it sends no body selector, so it reaches the first body and reads every event. Rules for which body a
 * spawn adds, replaces or refuses: {@link BodyRegistry}; for who reads which events: {@link EventLog}.
 *
 * <p>Handlers never access Minecraft world/player objects; the server thread does the work.
 */
public final class BodyHub implements AutoCloseable {
    private static final Gson GSON = new Gson();
    /** An /admin or /controls body: 256 KB, room for an arena plan with thousands of fills. */
    private static final int MAX_REQUEST_BYTES = 262_144;
    /** Events kept per body (and for the shared ring): about 100 s of one body's policy stream. */
    static final int RING = 2048;
    /** A removed body's events stay readable this long: its last acknowledgement may not have been read yet. */
    static final int RING_LINGER_TICKS = 6000;
    /** Requests admitted per body per tick. */
    static final int ADMIT_PER_BODY = 8;
    private static final int INBOX = 256;
    private static final int SWEEP_EVERY = 100;
    private static volatile BodyHub active;

    /** One event on the wire. {@code body_id} names the body it concerns (an event nobody owns carries the first body's). */
    public record Event(int schema_version, String launch_id, String run_id, String episode_id,
                        String body_id, long sequence, int tick, String phase, String type,
                        Map<String, Object> data) {}

    private final MinecraftServer server;
    private final String launchId = System.getProperty("mclab.launch_id", "unmanaged");
    private final ArrayBlockingQueue<BodyRuntime.Inbound> inbox = new ArrayBlockingQueue<>(INBOX);
    private final EventLog<Event> events = new EventLog<>(RING);
    private final BodyRegistry<BodyRuntime> bodies = new BodyRegistry<>(BodyRuntime::name, BodyRuntime::live);
    /** No body: what /state and /policy answer for an id nobody holds, so a reader waiting for its body sees idle. */
    private final BodyRuntime blank;
    private final HttpServer http;
    private final ThreadPoolExecutor workers;
    /** The players online, published each tick for GET /players (read on the HTTP thread). */
    private volatile List<Map<String, Object>> roster = List.of();
    /** The body id whose arena op the server thread is handling right now: its acknowledgement is that body's alone. */
    private String arenaRequester;

    public BodyHub(MinecraftServer server) throws IOException {
        this.server = server;
        this.blank = new BodyRuntime(server, this);
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
        com.vulpine.mcb.arena.ArenaRuntime.setEmitter(this::emitArena);
    }

    String launchId() { return launchId; }

    MinecraftServer server() { return server; }

    // ---- the HTTP thread

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
            var query = query(exchange.getRequestURI().getRawQuery());
            if (exchange.getRequestMethod().equals("GET")) {
                if (path.equals("/policy")) {
                    // Mark before the slow encode+send: a scan during it must not carry this batch again.
                    respond(exchange, 200, engineFor(query.get("body")).servePolicy());
                    return;
                }
                if (path.equals("/state")) { respond(exchange, 200, engineFor(query.get("body")).snapshot()); return; }
                if (path.equals("/players")) { respond(exchange, 200, Map.of("schema_version", 1, "launch_id", launchId, "players", roster)); return; }
                if (path.equals("/events")) {
                    long after = query.containsKey("after") ? Long.parseLong(query.get("after")) : 0;
                    respond(exchange, 200, eventBatch(after, query.get("body")));
                    return;
                }
            }
            if (exchange.getRequestMethod().equals("POST")
                    && (path.equals("/admin") || path.equals("/controls"))) {
                var bytes = exchange.getRequestBody().readNBytes(MAX_REQUEST_BYTES + 1);
                if (bytes.length > MAX_REQUEST_BYTES) {
                    respond(exchange, 413, Map.of("error", "request_too_large"));
                    return;
                }
                var request = JsonParser.parseString(new String(bytes, StandardCharsets.UTF_8)).getAsJsonObject();
                if (!inbox.offer(new BodyRuntime.Inbound(request, path.equals("/admin"), System.nanoTime(), server.getTickCount()))) {
                    respond(exchange, 429, Map.of("error", "queue_full"));
                    return;
                }
                respond(exchange, 202, Map.of("status", "queued", "launch_id", launchId));
                return;
            }
            if (exchange.getRequestMethod().equals("POST") && path.equals("/tell")) {
                var request = JsonParser.parseString(new String(
                        exchange.getRequestBody().readNBytes(2048), StandardCharsets.UTF_8)).getAsJsonObject();
                var id = java.util.UUID.fromString(request.get("uuid").getAsString());
                var message = request.get("text").getAsString();
                if (message.length() > 256) throw new IllegalArgumentException("text_too_long");
                server.execute(() -> {
                    var player = server.getPlayerList().getPlayer(id);
                    if (player != null) player.sendSystemMessage(net.minecraft.network.chat.Component.literal(message));
                });
                respond(exchange, 202, Map.of("status", "queued", "launch_id", launchId));
                return;
            }
            if (exchange.getRequestMethod().equals("POST") && path.equals("/say")) {
                var request = JsonParser.parseString(new String(
                        exchange.getRequestBody().readNBytes(4096), StandardCharsets.UTF_8)).getAsJsonObject();
                var text = CompanionChat.requireText(request.get("text").getAsString());
                var target = request.has("to") && !request.get("to").isJsonNull()
                        ? CompanionChat.requireTarget(request.get("to").getAsString()) : null;
                var speaker = request.has("speaker") && !request.get("speaker").isJsonNull()
                        ? CompanionChat.requireSpeaker(request.get("speaker").getAsString()) : null;
                say(text, target, speaker);
                respond(exchange, 202, Map.of("status", "queued", "launch_id", launchId));
                return;
            }
            respond(exchange, 404, Map.of("error", "not_found"));
        } catch (RuntimeException error) {
            respond(exchange, 400, Map.of("error", "invalid_request"));
        } finally { exchange.close(); }
    }

    /** The query's parameters, decoded; a repeated key keeps its last value. */
    static Map<String, String> query(String raw) {
        var out = new HashMap<String, String>();
        if (raw == null || raw.isEmpty()) return out;
        for (var pair : raw.split("&")) {
            int eq = pair.indexOf('=');
            String key = eq < 0 ? pair : pair.substring(0, eq);
            String value = eq < 0 ? "" : pair.substring(eq + 1);
            out.put(URLDecoder.decode(key, StandardCharsets.UTF_8), URLDecoder.decode(value, StandardCharsets.UTF_8));
        }
        return out;
    }

    /** The body a reader names; with none named, the first. An id nobody holds reads as no body. */
    private BodyRuntime engineFor(String bodyId) {
        var engine = bodyId == null ? bodies.first() : bodies.get(bodyId);
        return engine == null ? blank : engine;
    }

    private static void respond(HttpExchange exchange, int code, Object payload) throws IOException {
        byte[] data = GSON.toJson(payload).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(code, data.length);
        exchange.getResponseBody().write(data);
    }

    private Map<String, Object> eventBatch(long after, String body) {
        var batch = events.batch(after, body);
        return Map.of("schema_version", 1, "launch_id", launchId, "gap", batch.gap(),
                "latest_sequence", batch.latest(), "events", batch.events());
    }

    // ---- events

    /** One event of {@code engine}, readable by that body's reader (and by readers of everything). */
    synchronized void emit(BodyRuntime engine, String type, String phase, Map<String, Object> data) {
        record(engine.bodyId(), engine.runId(), engine.episodeId(), engine.bodyId(), type, phase, data);
    }

    /**
     * An event no body owns (a player's command or chat, arena progress): every reader sees it. It carries the first
     * body's identity, which is what a client of one body has always seen.
     */
    synchronized void emitShared(String type, String phase, Map<String, Object> data) {
        var first = bodies.first();
        record(null, first == null ? "none" : first.runId(), first == null ? "none" : first.episodeId(),
                first == null ? "none" : first.bodyId(), type, phase, data);
    }

    /**
     * The arena runtime's events. An answer to an op (emitted while that op is handled) goes to the body that asked, so
     * two bodies that number their requests alike never read each other's answers; the rest (progress, match results)
     * is shared.
     */
    private synchronized void emitArena(String type, Map<String, Object> data) {
        var asker = arenaRequester == null ? null : bodies.get(arenaRequester);
        if (asker != null) emit(asker, type, "fabric_start", data);
        else emitShared(type, "fabric_start", data);
    }

    private void record(String scope, String run, String episode, String body, String type, String phase,
                        Map<String, Object> data) {
        long sequence = events.reserve();
        int tick = server.getTickCount();
        events.add(scope, sequence, tick,
                new Event(1, launchId, run, episode, body, sequence, tick, phase, type, BodyRuntime.eventData(data)));
    }

    // ---- the server thread

    public void preTick() {
        int tick = server.getTickCount();
        var held = bodies.all();
        held.forEach(BodyRuntime::preTickHead);
        for (int budget = ADMIT_PER_BODY * Math.max(1, held.size()); budget > 0; budget--) {
            var request = inbox.poll();
            if (request == null) break;
            try {
                if (BodyRuntime.staleQueued(request.receivedTick(), tick)) {
                    throw new IllegalArgumentException("stale_queued_request");
                }
                admit(request);
            } catch (Exception error) {
                reject(request.json(), error);
            }
        }
        // After the drain, so a request queued during a long tick counts as contact.
        bodies.all().forEach(BodyRuntime::preTickTail);
        if (tick % SWEEP_EVERY == 0) sweep(tick);
    }

    /** Refuses a request: the reason goes to the sender's body when it has one here, else to everyone. */
    private void reject(JsonObject request, Exception error) {
        var data = Map.<String, Object>of("request_sequence",
                request.has("sequence") ? request.get("sequence").toString() : "missing",
                "reason", error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage());
        String bodyId = request.has("body_id") && request.get("body_id").isJsonPrimitive()
                ? request.get("body_id").getAsString() : null;
        var engine = bodyId == null ? null : bodies.get(bodyId);
        if (engine != null) emit(engine, "rejected", "fabric_start", data);
        else if (bodyId != null && bodyId.matches("[A-Za-z0-9_.:-]{1,80}")) {
            // The body is not here (yet): still the sender's rejection, so its reader sees it.
            record(bodyId, text(request, "run_id", "none"), text(request, "episode_id", "none"), bodyId,
                    "rejected", "fabric_start", data);
        } else emitShared("rejected", "fabric_start", data);
    }

    private void admit(BodyRuntime.Inbound inbound) throws Exception {
        var request = inbound.json();
        if (request.get("schema_version").getAsInt() != 1) throw new IllegalArgumentException("schema_version");
        long sequence = request.get("sequence").getAsLong();
        String op = BodyRuntime.text(request, "op");
        if (inbound.admin() && com.vulpine.mcb.arena.ArenaRuntime.handles(op)) {
            // The arena runtime owns these ops: they need no body, and answer by request_id and sequence.
            var arena = com.vulpine.mcb.arena.ArenaRuntime.active();
            if (arena == null) {
                emitShared(op + "_failed", "fabric_start", Map.of("request_sequence", sequence, "reason", "arena_runtime_unavailable"));
                return;
            }
            arenaRequester = request.has("body_id") && request.get("body_id").isJsonPrimitive()
                    ? request.get("body_id").getAsString() : null;
            try { arena.handle(op, request, sequence); }
            finally { arenaRequester = null; }
            return;
        }
        if (inbound.admin() && op.equals("spawn")) {
            spawn(request, sequence, inbound.receivedNanos());
            return;
        }
        var engine = bodies.get(BodyRuntime.text(request, "body_id"));
        if (engine == null) throw new IllegalArgumentException("wrong_identity");
        engine.admit(inbound, sequence, op);
    }

    /**
     * Adds a body, or replaces one: {@link BodyRegistry} decides which engine takes the spawn. A spawn is
     * {@code coexist: true} when it shares the server with other bodies (the companion host's); without it the body is the
     * single legacy one every older client assumes, replaced by {@code replace}.
     */
    private void spawn(JsonObject request, long sequence, long receivedNanos) throws Exception {
        String bodyId = BodyRuntime.text(request, "body_id");
        String name = request.has("name") ? request.get("name").getAsString()
                : "Lab" + java.util.UUID.randomUUID().toString().replace("-", "").substring(0, 10);
        if (!name.matches("[A-Za-z0-9_]{3,16}")) throw new IllegalArgumentException("invalid_name");
        request.addProperty("name", name);
        boolean coexist = request.has("coexist") && BodyRuntime.strictBoolean(request, "coexist");
        boolean replace = request.has("replace") && request.get("replace").getAsBoolean();
        var decision = bodies.admitSpawn(bodyId, name, coexist, replace);
        var engine = decision.target() != null ? decision.target() : new BodyRuntime(server, this);
        boolean held = bodies.idOf(engine) != null;
        engine.received();
        try {
            engine.spawn(request, sequence, receivedNanos);
        } finally {
            // An engine takes its new identity part-way through a spawn, so the registry follows whatever identity it
            // ended with, however the spawn ended; a new engine that never got a body is not kept.
            if (held || engine.live()) bodies.bind(engine.bodyId(), engine, coexist);
        }
    }

    /** A body that is gone (despawned, every retired adapter finished) stops being held; its events linger a while. */
    private void sweep(int tick) {
        var live = new HashSet<String>();
        for (var engine : bodies.all()) {
            String id = bodies.idOf(engine);
            if (bodies.coexists(id) && engine.removable()) bodies.remove(id);
            else live.add(id);
        }
        events.sweep(tick, live, RING_LINGER_TICKS);
    }

    public void postTick() {
        bodies.all().forEach(BodyRuntime::postTick);
        blank.postTick();
        updateRoster();
    }

    /**
     * Every player online (the bodies' own fake players too, flagged fake), for GET /players: where they are, whether a
     * commanded attack can hurt them, and whether they are still there (a logout drops them). Built each tick on the
     * server thread; the HTTP thread reads the published copy.
     */
    private void updateRoster() {
        var out = new ArrayList<Map<String, Object>>();
        for (var player : server.getPlayerList().getPlayers()) {
            var entry = new LinkedHashMap<String, Object>();
            entry.put("name", player.getName().getString());
            entry.put("uuid", player.getUUID().toString());
            entry.put("position", List.of(player.getX(), player.getY(), player.getZ()));
            entry.put("dimension", player.level().dimension().identifier().toString());
            entry.put("mode", player.isSpectator() ? "spectator" : player.isCreative() ? "creative" : "survival");
            entry.put("invulnerable", player.isInvulnerable());
            entry.put("fake", CarpetBodyAdapter.isFakePlayer(player));
            entry.put("alive", !player.isDeadOrDying());  // false on the death screen: a match waits for the respawn
            out.add(Map.copyOf(entry));
        }
        roster = List.copyOf(out);
    }

    // ---- the world's callbacks (server thread)

    /**
     * A player's /mcb command. Always recorded for the Python service, which owns authority (owner and allowlist).
     * stop also cancels every body here, so it needs neither Python nor a model.
     */
    static void playerCommand(ServerPlayer player, String sub, Map<String, Object> args) {
        var hub = active;
        if (hub == null) return;
        var data = new HashMap<String, Object>(args);
        data.put("player_uuid", player.getUUID().toString());
        data.put("player_name", player.getName().getString());
        data.put("command", sub);
        addSender(data, player);
        hub.emitShared("player_command", "fabric_start", data);
        if (sub.equals("stop")) hub.bodies.all().forEach(body -> body.cancelAll("player_stop")); // none spawned: a no-op
    }

    /**
     * A body's player died (Fabric AFTER_DEATH, server thread). Carpet then removes the fake player, so the state
     * afterwards reads disconnected_or_dead; the body that owns the player reports the cause, once per spawned body.
     */
    static void bodyDied(net.minecraft.world.entity.LivingEntity entity,
                         net.minecraft.world.damagesource.DamageSource source) {
        var hub = active;
        if (hub == null || !(entity instanceof ServerPlayer player)) return;
        for (var body : hub.bodies.all()) if (body.owns(player)) { body.died(player, source); return; }
    }

    /** The sender's position and dimension, shared by player_command and player_chat. */
    static void addSender(Map<String, Object> data, ServerPlayer player) {
        data.put("position", List.of(player.getX(), player.getY(), player.getZ()));
        data.put("dimension", player.level().dimension().identifier().toString());
    }

    /**
     * A chat line from a real player, on the server thread. Carpet fake players are skipped so no body hears itself or
     * another. The text is the plain signed content, cut to 256 characters.
     */
    static void playerChat(ServerPlayer player, String text) {
        var hub = active;
        if (hub == null || !CompanionChat.heard(CarpetBodyAdapter.isFakePlayer(player))) return;
        var data = new HashMap<String, Object>();
        data.put("player_uuid", player.getUUID().toString());
        data.put("player_name", player.getName().getString());
        data.put("text", CompanionChat.truncate(text));
        addSender(data, player);
        hub.emitShared("player_chat", "fabric_start", data);
    }

    /** /say: speaks as a body (the one named, else the first; MCB with none), to one online player or to everyone. */
    private void say(String text, String to, String speaker) {
        server.execute(() -> {
            String who = CompanionChat.speaker(speaker, bodies.all().stream().map(BodyRuntime::name)
                    .filter(java.util.Objects::nonNull).toList());
            if (to == null) {
                server.getPlayerList().broadcastSystemMessage(
                        net.minecraft.network.chat.Component.literal(CompanionChat.broadcast(who, text)), false);
                return;
            }
            var player = server.getPlayerList().getPlayerByName(to);
            if (player == null) return; // offline or unknown: accepted, nothing emitted
            player.sendSystemMessage(net.minecraft.network.chat.Component
                    .literal(CompanionChat.whisper(who, text))
                    .withStyle(net.minecraft.ChatFormatting.GRAY, net.minecraft.ChatFormatting.ITALIC));
        });
    }

    private static String text(JsonObject request, String key, String fallback) {
        try { return BodyRuntime.text(request, key); } catch (RuntimeException error) { return fallback; }
    }

    public static void trace(ServerPlayer player, String phase) {
        var hub = active;
        if (hub == null) return;
        for (var body : hub.bodies.all()) if (body.owns(player)) { body.trace(player, phase); return; }
    }

    /** Interaction packets and results for this mod's own bodies only. */
    public static void interaction(ServerPlayer player, String type, Supplier<Map<String, Object>> data) {
        var hub = active;
        if (hub == null || !hub.server.isSameThread()) return;
        for (var body : hub.bodies.all()) if (body.owns(player)) { body.interaction(type, data); return; }
    }

    @Override public void close() {
        active = null;
        bodies.all().forEach(BodyRuntime::close);
        blank.close();
        http.stop(0);
        workers.shutdownNow();
    }
}
