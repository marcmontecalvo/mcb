package com.vulpine.mclab.client;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;
import baritone.api.Settings;
import baritone.api.pathing.goals.GoalBlock;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.fabricmc.api.ClientModInitializer;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;
import java.util.regex.Pattern;

/**
 * Loopback control surface for the pinned Baritone API. It reports acknowledgments and
 * client-side status only; the server evaluator remains the authority on task outcomes.
 */
public final class ClientBridge implements ClientModInitializer {
    private static final Gson GSON = new Gson();
    private static final int MAX_BODY_BYTES = 4096;
    private static final long CLIENT_THREAD_TIMEOUT_MS = 2000;
    private static final int MAX_COORD = 30_000_000;
    private static final int MAX_MINE_QUANTITY = 64;
    private static final Pattern BLOCK_ID = Pattern.compile("[a-z0-9_.-]+:[a-z0-9_/.-]+");

    private final AtomicLong sequence = new AtomicLong();

    @Override
    public void onInitializeClient() {
        String token = readToken(System.getProperty("mclab.client_bridge_token_file", ""));
        if (token.length() < 32) {
            System.err.println("[mclab-client-bridge] disabled: missing private token");
            return;
        }
        int port = Integer.getInteger("mclab.client_bridge_port", 8767);
        try {
            HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 4);
            http.createContext("/", exchange -> handle(exchange, token));
            http.start();
            System.out.println("[mclab-client-bridge] listening on 127.0.0.1:" + port);
        } catch (IOException e) {
            throw new IllegalStateException("Client bridge could not bind 127.0.0.1:" + port, e);
        }
    }

    private void handle(HttpExchange exchange, String token) throws IOException {
        try (exchange) {
            String authorization = exchange.getRequestHeaders().getFirst("Authorization");
            if (authorization == null || !MessageDigest.isEqual(
                    authorization.getBytes(StandardCharsets.UTF_8),
                    ("Bearer " + token).getBytes(StandardCharsets.UTF_8))) {
                respond(exchange, 401, Map.of("error", "unauthorized"));
                return;
            }
            String route = exchange.getRequestMethod() + " " + exchange.getRequestURI().getPath();
            try {
                Map<String, Object> result = switch (route) {
                    case "GET /status" -> onClient(this::status);
                    case "POST /goto" -> gotoBlock(body(exchange));
                    case "POST /mine" -> mine(body(exchange));
                    case "POST /cancel" -> onClient(() -> ack("cancel",
                            Map.of("cancelled", baritone().getPathingBehavior().cancelEverything())));
                    case "POST /policy" -> policy(body(exchange));
                    default -> null;
                };
                if (result == null) respond(exchange, 404, Map.of("error", "unknown_route"));
                else respond(exchange, 200, result);
            } catch (IllegalArgumentException e) {
                respond(exchange, 400, Map.of("error", e.getMessage()));
            } catch (IllegalStateException e) {
                respond(exchange, 409, Map.of("error", e.getMessage()));
            } catch (Exception e) {
                respond(exchange, 500, Map.of("error", e.getClass().getSimpleName()));
            }
        }
    }

    private Map<String, Object> gotoBlock(JsonObject body) throws Exception {
        int x = coord(body, "x"), y = coord(body, "y"), z = coord(body, "z");
        return onClient(() -> {
            baritone().getCustomGoalProcess().setGoalAndPath(new GoalBlock(x, y, z));
            return ack("goto", Map.of("x", x, "y", y, "z", z));
        });
    }

    private Map<String, Object> mine(JsonObject body) throws Exception {
        String block = body.has("block") ? body.get("block").getAsString() : "";
        if (!BLOCK_ID.matcher(block).matches()) throw new IllegalArgumentException("invalid_block");
        int quantity = body.has("quantity") ? body.get("quantity").getAsInt() : 0;
        if (quantity < 1 || quantity > MAX_MINE_QUANTITY) throw new IllegalArgumentException("invalid_quantity");
        return onClient(() -> {
            baritone().getMineProcess().mineByName(quantity, block);
            return ack("mine", Map.of("block", block, "quantity", quantity));
        });
    }

    private Map<String, Object> policy(JsonObject body) throws Exception {
        Settings settings = BaritoneAPI.getSettings();
        Map<String, Settings.Setting<Boolean>> allowed = Map.of(
                "allowBreak", settings.allowBreak,
                "allowPlace", settings.allowPlace,
                "allowInventory", settings.allowInventory,
                "allowParkour", settings.allowParkour,
                "allowSprint", settings.allowSprint,
                "chatControl", settings.chatControl);
        for (String key : body.keySet()) {
            if (!allowed.containsKey(key) || !body.get(key).isJsonPrimitive()
                    || !body.get(key).getAsJsonPrimitive().isBoolean()) {
                throw new IllegalArgumentException("invalid_policy_field:" + key);
            }
        }
        return onClient(() -> {
            Map<String, Object> applied = new LinkedHashMap<>();
            for (var entry : allowed.entrySet()) {
                if (body.has(entry.getKey())) entry.getValue().value = body.get(entry.getKey()).getAsBoolean();
                applied.put(entry.getKey(), entry.getValue().value);
            }
            return ack("policy", applied);
        });
    }

    private Map<String, Object> status() {
        Minecraft minecraft = Minecraft.getInstance();
        LocalPlayer player = minecraft.player;
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("schema_version", 1);
        value.put("in_world", player != null && minecraft.level != null);
        if (player != null) {
            value.put("player", player.getName().getString());
            value.put("uuid", player.getUUID().toString());
            value.put("position", Map.of("x", player.getX(), "y", player.getY(), "z", player.getZ()));
            IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();
            var pathing = baritone.getPathingBehavior();
            value.put("pathing", pathing.isPathing());
            value.put("goal", String.valueOf(pathing.getGoal()));
            value.put("estimated_ticks_to_goal", pathing.estimatedTicksToGoal().orElse(null));
            value.put("process", baritone.getPathingControlManager().mostRecentInControl()
                    .map(p -> p.displayName()).orElse(null));
        }
        return value;
    }

    private IBaritone baritone() {
        Minecraft minecraft = Minecraft.getInstance();
        if (minecraft.player == null || minecraft.level == null) throw new IllegalStateException("not_in_world");
        return BaritoneAPI.getProvider().getPrimaryBaritone();
    }

    private Map<String, Object> ack(String command, Map<String, Object> fields) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("accepted", true);
        value.put("command", command);
        value.put("sequence", sequence.incrementAndGet());
        value.putAll(fields);
        return value;
    }

    /** Baritone and client state are only touched on the render thread. */
    private static Map<String, Object> onClient(Supplier<Map<String, Object>> action) throws Exception {
        try {
            return Minecraft.getInstance().submit(action).get(CLIENT_THREAD_TIMEOUT_MS, TimeUnit.MILLISECONDS);
        } catch (java.util.concurrent.ExecutionException e) {
            if (e.getCause() instanceof RuntimeException runtime) throw runtime;
            throw e;
        }
    }

    /** The token arrives by file path because launchers log JVM arguments. */
    private static String readToken(String path) {
        if (path.isEmpty()) return "";
        try {
            return java.nio.file.Files.readString(java.nio.file.Path.of(path)).strip();
        } catch (IOException e) {
            return "";
        }
    }

    private static int coord(JsonObject body, String key) {
        if (!body.has(key) || !body.get(key).isJsonPrimitive()) throw new IllegalArgumentException("missing_" + key);
        double raw = body.get(key).getAsDouble();
        if (raw != Math.rint(raw) || Math.abs(raw) > MAX_COORD) throw new IllegalArgumentException("invalid_" + key);
        return (int) raw;
    }

    private static JsonObject body(HttpExchange exchange) throws IOException {
        byte[] bytes = exchange.getRequestBody().readNBytes(MAX_BODY_BYTES + 1);
        if (bytes.length > MAX_BODY_BYTES) throw new IllegalArgumentException("body_too_large");
        if (bytes.length == 0) return new JsonObject();
        try {
            return JsonParser.parseString(new String(bytes, StandardCharsets.UTF_8)).getAsJsonObject();
        } catch (RuntimeException e) {
            throw new IllegalArgumentException("invalid_json");
        }
    }

    private static void respond(HttpExchange exchange, int status, Map<String, ?> value) throws IOException {
        byte[] bytes = GSON.toJson(value).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
    }
}
