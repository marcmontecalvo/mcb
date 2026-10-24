package com.vulpine.mclab;

import com.google.gson.*;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.world.clock.WorldClocks;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Incremental C1 reset foundation. No scoring or policy admission is implemented here. */
public final class CourseReset {
    private static final Gson GSON = new Gson();
    private static final int CELLS = 49*41*49;
    private final MinecraftServer server;
    private final JsonObject spec;
    private final int x, y, z;
    private final AABB bounds;
    private int cursor;
    private String phase = "build";
    private MessageDigest hasher = sha();
    private String terrainDigest = "";

    public CourseReset(MinecraftServer server, JsonObject specification) {
        this.server = server;
        this.spec = specification.deepCopy();
        if (!spec.get("course_id").getAsString().equals("C1")
                || !spec.getAsJsonArray("initial_inventory").isEmpty()
                || spec.get("yaw").getAsFloat() != 0 || spec.get("pitch").getAsFloat() != 0) {
            throw new IllegalArgumentException("initial_profile_requires_standard_C1");
        }
        var origin = spec.getAsJsonArray("origin");
        x = origin.get(0).getAsInt(); y = origin.get(1).getAsInt(); z = origin.get(2).getAsInt();
        var clear = spec.getAsJsonObject("clear_region");
        if (!clear.getAsJsonArray("min").equals(JsonParser.parseString("[-24,-20,-24]"))
                || !clear.getAsJsonArray("max").equals(JsonParser.parseString("[24,20,24]"))) {
            throw new IllegalArgumentException("unsupported_reset_envelope");
        }
        for (var entry : spec.getAsJsonArray("geometry")) state(entry.getAsJsonObject().get("block").getAsString());
        bounds = new AABB(x-24, y-20, z-24, x+25, y+21, z+25);
        ensureHumanClear();
        for (var entity : server.overworld().getEntitiesOfClass(LivingEntity.class, bounds)) {
            if (!(entity instanceof Player)) throw new IllegalStateException("unexpected_living_entity_in_arena");
        }
    }

    private static BlockState state(String id) {
        return switch (id) {
            case "minecraft:air" -> Blocks.AIR.defaultBlockState();
            case "minecraft:stone" -> Blocks.STONE.defaultBlockState();
            case "minecraft:oak_log" -> Blocks.OAK_LOG.defaultBlockState();
            default -> throw new IllegalArgumentException("unsupported_reset_block");
        };
    }

    public void ensureHumanClear() {
        for (var player : server.getPlayerList().getPlayers()) {
            if (CarpetBodyAdapter.isHuman(player) && player.level() == server.overworld()
                    && player.getBoundingBox().intersects(bounds)) {
                throw new IllegalStateException("human_in_course_reset_arena");
            }
        }
    }

    public void cleanEntities() {
        server.overworld().getEntitiesOfClass(ItemEntity.class, bounds).forEach(e -> e.discard());
        server.overworld().getEntitiesOfClass(Projectile.class, bounds).forEach(e -> e.discard());
    }

    public void resetEnvironment() {
        var clock = server.registryAccess().lookupOrThrow(Registries.WORLD_CLOCK).getOrThrow(WorldClocks.OVERWORLD);
        var manager = server.overworld().clockManager();
        manager.setTotalTicks(clock, 6000);
        manager.setRate(clock, 1);
        manager.setPaused(clock, true);
        var weather = server.overworld().getWeatherData();
        weather.setClearWeatherTime(12000); weather.setRainTime(12000); weather.setThunderTime(12000);
        weather.setRaining(false); weather.setThundering(false);
        server.overworld().setRainLevel(0); server.overworld().setThunderLevel(0);
    }

    public static Map<String, Object> environmentFingerprint(MinecraftServer server) {
        var clock = server.registryAccess().lookupOrThrow(Registries.WORLD_CLOCK).getOrThrow(WorldClocks.OVERWORLD);
        var instance = server.overworld().clockManager().getInstance(clock);
        var weather = server.overworld().getWeatherData();
        return Map.of("clock_ticks", instance.totalTicks(), "clock_rate", instance.rate(),
                "clock_paused", instance.isPaused(), "raining", weather.isRaining(),
                "thundering", weather.isThundering(), "clear_weather_time", weather.getClearWeatherTime(),
                "rain_time", weather.getRainTime(), "thunder_time", weather.getThunderTime());
    }

    public void ensureOnlyBody(String name) {
        if (!server.overworld().getEntitiesOfClass(ItemEntity.class, bounds).isEmpty()
                || !server.overworld().getEntitiesOfClass(Projectile.class, bounds).isEmpty()) {
            throw new IllegalStateException("residual_arena_drops_or_projectiles");
        }
        for (var entity : server.overworld().getEntitiesOfClass(LivingEntity.class, bounds)) {
            if (!(entity instanceof Player p) || !p.getName().getString().equals(name)) {
                throw new IllegalStateException("unexpected_arena_living_entity");
            }
        }
    }

    private BlockState expected(int dx, int dy, int dz) {
        var result = Blocks.AIR.defaultBlockState();
        for (var entry : spec.getAsJsonArray("geometry")) {
            var box = entry.getAsJsonObject();
            var a = box.getAsJsonArray("from"); var b = box.getAsJsonArray("to");
            if (dx >= a.get(0).getAsInt() && dx <= b.get(0).getAsInt()
                    && dy >= a.get(1).getAsInt() && dy <= b.get(1).getAsInt()
                    && dz >= a.get(2).getAsInt() && dz <= b.get(2).getAsInt()) {
                result = state(box.get("block").getAsString());
            }
        }
        return result;
    }

    public void step() {
        ensureHumanClear();
        if (done()) return;
        for (int budget = 0; budget < 2048 && cursor < CELLS; budget++, cursor++) {
            int dx = cursor%49-24, dz = (cursor/49)%49-24, dy = cursor/(49*49)-20;
            var pos = new BlockPos(x+dx, y+dy, z+dz);
            if (!server.overworld().getChunkSource().hasChunk(pos.getX() >> 4, pos.getZ() >> 4)) {
                throw new IllegalStateException("reset_chunks_not_loaded");
            }
            var wanted = expected(dx, dy, dz);
            var actual = server.overworld().getBlockState(pos);
            if (phase.equals("build")) {
                if (actual.hasBlockEntity() || !actual.getFluidState().isEmpty()) {
                    throw new IllegalStateException("reset_requires_fluid_and_block_entity_free_arena");
                }
                server.overworld().setBlockAndUpdate(pos, wanted);
            } else {
                if (!actual.equals(wanted)) throw new IllegalStateException("reset_geometry_mismatch_at_"+pos);
                hasher.update((cursor+":"+actual+"\n").getBytes(StandardCharsets.UTF_8));
            }
        }
        if (cursor == CELLS) {
            if (phase.equals("build")) beginVerify();
            else { terrainDigest = HexFormat.of().formatHex(hasher.digest()); phase = "verified"; }
        }
    }

    public void beginVerify() { phase = "verify"; cursor = 0; hasher = sha(); }
    public boolean done() { return phase.equals("verified"); }
    public String terrainDigest() { return terrainDigest; }
    public String specHash() { return spec.get("spec_hash").getAsString(); }
    public Vec3 spawn() {
        var s = spec.getAsJsonArray("spawn");
        return new Vec3(x+s.get(0).getAsDouble(), y+s.get(1).getAsDouble(), z+s.get(2).getAsDouble());
    }
    public Map<String, Object> progress() {
        return Map.of("phase", phase, "cursor", cursor, "cells", CELLS,
                "spec_hash", specHash(), "terrain_digest", terrainDigest);
    }

    private static MessageDigest sha() {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (java.security.NoSuchAlgorithmException error) { throw new IllegalStateException(error); }
    }
    private static JsonElement canonical(JsonElement value) {
        if (value.isJsonObject()) {
            var result = new JsonObject();
            value.getAsJsonObject().keySet().stream().sorted().forEach(k -> result.add(k, canonical(value.getAsJsonObject().get(k))));
            return result;
        }
        if (value.isJsonArray()) {
            var result = new JsonArray();
            value.getAsJsonArray().forEach(e -> result.add(canonical(e)));
            return result;
        }
        return value;
    }
    public static String digest(Object value) {
        return HexFormat.of().formatHex(sha().digest(canonical(GSON.toJsonTree(value)).toString().getBytes(StandardCharsets.UTF_8)));
    }
}
